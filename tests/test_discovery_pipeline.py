import sqlite3
from concurrent.futures import Future
from contextlib import closing
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.discovery_peak_fixtures import representative_event
from tests.discovery_pipeline_fixtures import (
    _compiled_run_spec,
    _FakeRawHandle,
    _run_discovery,
    _scan_event,
    _settings,
    vendor_fake_open_raw,
)
from tests.discovery_store_fixtures import stored_events, stored_peaks
from tests.execution_fixtures import in_process_execution
from xic_extractor.configuration.execution_profile import (
    ExecutionProfile,
)
from xic_extractor.discovery import pipeline as _pipeline
from xic_extractor.discovery.cid_nl_search import DiscoverySampleJob
from xic_extractor.discovery.models import (
    DiscoveryBatchOutputs,
)
from xic_extractor.discovery.sample_process_execution import (
    DiscoveryProcessExecutionError,
    DiscoverySampleProcessError,
    DiscoverySampleProcessResult,
    run_discovery_sample_process_job,
)
from xic_extractor.discovery.store import (
    part_path,
    read_discovery_batch,
    read_peak_counts,
)
from xic_extractor.execution.timing import TimingRecord, TimingRecorder
from xic_extractor.processing_identity import (
    build_local_acquisition_source_identity,
)
from xic_extractor.raw_source_binding import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.xic_models import XICTrace


@pytest.mark.parametrize(
    "selection", ["single_dr", "single_r", "union", "intersection"]
)
def test_method_toml_runs_sample_local_cli_to_compatible_csvs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    from scripts.run_discovery import main

    available_tags = (("custom_dR", 116.0473), ("custom_R", 132.0423))
    tags = (
        available_tags[:1]
        if selection == "single_dr"
        else available_tags[1:]
        if selection == "single_r"
        else available_tags
    )
    combine_mode = "single" if selection.startswith("single") else selection
    preset_path = tmp_path / "method.toml"
    preset_path.write_text(
        'name = "User method"\ndescription = "Selected custom losses"\n'
        f'combine_mode = "{combine_mode}"\n'
        + "".join(
            f'[[tag]]\nstrategy = "neutral_loss"\nname = "{name}"\nvalue = {loss}\n'
            for name, loss in tags
        )
        + "[discovery]\nprecursor_mz_tolerance_ppm = 12.0\n"
        "ms1_search_padding_min = 0.7\n",
        encoding="utf-8",
    )
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    raws = {}

    def open_raw(path, _dll_dir, *, expected_acquisition_source, **_kwargs):
        raw = raws[path.stem]
        raw.acquisition_source = expected_acquisition_source
        return raw

    monkeypatch.setattr("xic_extractor.raw_reader.open_raw", open_raw)
    for sample, apex, height in (("a", 8.0, 1000.0), ("b", 9.0, 2300.0)):
        raw_path = tmp_path / f"{sample}.raw"
        raw_path.write_bytes(f"sample:{sample}".encode())
        raw = _FakeRawHandle(
            [
                _scan_event(
                    scan_number=scan,
                    rt=apex + offset,
                    precursor_mz=500.0,
                    masses=[500.0 - loss for _, loss in available_tags],
                    intensities=[9000.0, 8000.0],
                )
                for scan, offset in ((100, 0.0), (101, 0.01))
            ],
            **_peak_kwargs(apex, height),
        )
        raws[sample] = raw
        output_dir = tmp_path / sample
        assert (
            main(
                [
                    "--raw",
                    str(raw_path),
                    "--dll-dir",
                    str(dll_dir),
                    "--output-dir",
                    str(output_dir),
                    "--preset",
                    str(preset_path),
                    "--no-blank-qualification",
                ]
            )
            == 0
        )
        store = output_dir / "discovery.sqlite"
        (candidate,) = stored_peaks(store)
        assert candidate.sample_stem == sample
        assert candidate.matched_tag_names == tuple(name for name, _ in tags)
        assert candidate.neutral_loss_tag == tags[0][0]
        events = stored_events(store)
        (best,) = [
            item.event
            for item in events
            if item.event.scan.scan_number == candidate.best_ms2_scan_id
            and item.event.neutral_loss_tag == candidate.neutral_loss_tag
        ]
        assert best.scan.product_mz == pytest.approx(500.0 - tags[0][1])
        assert {
            item.event.scan.scan_number
            for item in events
            if item.peak_id == candidate.peak_id
        } == {100, 101}
        assert candidate.ms1_apex_rt == pytest.approx(apex)
        assert sorted(path.name for path in output_dir.iterdir()) == [
            "discovery.sqlite"
        ]
        assert candidate.status == "admitted"
        assert {item.peak_id for item in events} == {candidate.peak_id}
        assert raw.entered and raw.closed


@pytest.mark.parametrize("parallel", [False, True])
def test_cancelled_batch_retains_completed_samples_without_publishing_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parallel: bool
) -> None:
    raw_paths = tuple(tmp_path / f"sample-{index}.raw" for index in range(4))
    for path in raw_paths:
        path.write_bytes(path.name.encode())
    handles = {path: _FakeRawHandle(events=[]) for path in raw_paths}

    def opener(path, dll_dir):
        return handles[path]

    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw",
        lambda path, dll_dir, **_kwargs: opener(path, dll_dir),
    )

    class Executor:
        def __init__(self, **kwargs):
            pass

        def submit(self, worker, job):
            future = Future()
            future.set_result(worker(job))
            return future

        def shutdown(self, **kwargs):
            pass

    progress = []
    output_dir = tmp_path / "out"
    with pytest.raises(InterruptedError, match="Discovery cancelled"):
        _run_discovery_batch(
            raw_paths,
            output_dir=output_dir,
            settings=_settings(),
            dll_dir=tmp_path / "dll",
            raw_opener=None if parallel else opener,
            execution=ExecutionProfile(parallel_workers=2 if parallel else 1),
            executor_factory=Executor if parallel else None,
            progress_callback=lambda done, total, sample: progress.append(sample),
            should_cancel=lambda: bool(progress),
        )

    completed = 2 if parallel else 1
    assert progress == [path.stem for path in raw_paths[:completed]]
    for path in raw_paths[:completed]:
        assert handles[path].closed
        assert part_path(output_dir, path.stem).is_file()
    assert all(not handles[path].entered for path in raw_paths[completed:])
    assert not (output_dir / "discovery.sqlite").exists()


def _write_sample_background_library(directory: Path) -> None:
    from xic_extractor.discovery.blank_qualification import (
        BlankBackgroundIon,
        BlankBackgroundLibrary,
    )

    BlankBackgroundLibrary(
        ions=(BlankBackgroundIon(258.1085, 300.0, 3, "blank"),),
        min_intensity=0.0,
        source_blanks=("blank",),
    ).save_json(directory / "blank_library.json")


def test_single_raw_pipeline_groups_strict_ms2_seeds_into_one_peak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_sample_background_library(tmp_path)
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    output_dir = tmp_path / "out"
    raw = _FakeRawHandle(
        [
            _scan_event(scan_number=101, rt=7.80, product_intensity=3000.0),
            _scan_event(scan_number=202, rt=7.86, product_intensity=9000.0),
        ],
        **_peak_kwargs(7.86, 619.0),
    )

    outputs = _run_discovery(
        raw_path,
        output_dir=output_dir,
        settings=_settings(enable_blank_qualification=True),
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: raw,
    )

    assert outputs.store == output_dir / "discovery.sqlite"
    assert sorted(path.name for path in output_dir.iterdir()) == ["discovery.sqlite"]
    (peak,) = stored_peaks(outputs.store)
    assert peak.best_ms2_scan_id == 202
    assert peak.attached_event_count == 2
    assert peak.status == "blank"
    events = stored_events(outputs.store)
    assert [item.event.scan.scan_number for item in events] == [101, 202]
    assert {item.peak_id for item in events} == {peak.peak_id}


def test_single_raw_pipeline_writes_inferred_and_direct_same_scan_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    output_dir = tmp_path / "out"
    raw = _FakeRawHandle(
        [
            _scan_event(
                scan_number=19561,
                rt=23.4669,
                precursor_mz=301.164978,
                masses=[184.113235, 185.115845],
                intensities=[278825.7, 367660.1],
            ),
        ],
        **_peak_kwargs(23.45, 1000.0),
    )

    outputs = _run_discovery(
        raw_path,
        output_dir=output_dir,
        settings=_settings(ms2_precursor_tol_da=1.6),
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: raw,
    )

    peaks = stored_peaks(outputs.store)
    assert len(peaks) == 2
    assert len({peak.peak_id for peak in peaks}) == 2
    assert {peak.best_ms2_scan_id for peak in peaks} == {19561}
    basis_by_product = {
        representative_event(peak).scan.product_mz: representative_event(
            peak
        ).scan.precursor_mz_basis
        for peak in peaks
    }
    assert basis_by_product == {
        184.113235: "product_plus_neutral_loss",
        185.115845: "scan_precursor",
    }


@pytest.mark.parametrize("reverse_products", [False, True])
def test_pipeline_retains_distinct_inferred_features_from_one_ms2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reverse_products: bool,
) -> None:
    products = [(142.0611, 5_000.0), (142.0611, 5_000.0), (142.5611, 150_000.0)]
    if reverse_products:
        products.reverse()
    raw = _FakeRawHandle(
        [
            _scan_event(
                scan_number=19561,
                rt=7.8,
                precursor_mz=258.3,
                masses=[mz for mz, _ in products],
                intensities=[intensity for _, intensity in products],
            )
        ]
    )
    amplitudes = {258.1085: 250.0, 258.6085: 1_000.0}
    monkeypatch.setattr(
        raw,
        "extract_xic_many",
        lambda requests: tuple(
            XICTrace.from_arrays(*_peak_trace(7.8, amplitudes[round(request.mz, 4)]))
            for request in requests
        ),
    )

    outputs = _run_discovery(
        tmp_path / "Sample.raw",
        output_dir=tmp_path / "out",
        settings=_settings(),
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: raw,
    )

    peaks = stored_peaks(outputs.store)
    assert len(peaks) == 2
    assert {
        round(representative_event(peak).scan.precursor_mz, 4) for peak in peaks
    } == set(amplitudes)
    assert len({peak.peak_id for peak in peaks}) == 2
    assert {peak.best_ms2_scan_id for peak in peaks} == {19561}
    assert {peak.status for peak in peaks} == {"no_nl_event"}
    events = stored_events(outputs.store)
    assert len(events) == 2
    assert {item.unattached_reason for item in events} == {"parent_ms1_unavailable"}


def test_pipeline_publishes_an_empty_sample_when_no_strict_seeds(
    tmp_path: Path,
) -> None:
    raw = _FakeRawHandle(events=[])

    outputs = _run_discovery(
        tmp_path / "Blank.raw",
        output_dir=tmp_path / "out",
        settings=_settings(),
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: raw,
    )

    assert read_discovery_batch(outputs.store).sample_order == ("Blank",)
    assert stored_peaks(outputs.store) == []
    assert stored_events(outputs.store) == []


def test_batch_pipeline_publishes_samples_in_run_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_sample_background_library(tmp_path)
    raw_paths = (
        tmp_path / "TumorBC2312_DNA.raw",
        tmp_path / "NormalBC2257_DNA.raw",
    )
    for raw_path in raw_paths:
        raw_path.write_bytes(f"discovery:{raw_path.stem}".encode())
    raw_by_path = {
        raw_paths[0]: _FakeRawHandle(
            [_scan_event(scan_number=101, rt=7.80, product_intensity=3000.0)],
            **_peak_kwargs(7.8, 619.0),
        ),
        raw_paths[1]: _FakeRawHandle(
            [_scan_event(scan_number=202, rt=9.10, product_intensity=7000.0)],
            **_peak_kwargs(9.1, 940.0),
        ),
    }

    outputs = _run_discovery_batch(
        raw_paths,
        output_dir=tmp_path / "out",
        settings=_settings(enable_blank_qualification=True),
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: raw_by_path[path],
    )

    assert isinstance(outputs, DiscoveryBatchOutputs)
    assert outputs.store == tmp_path / "out" / "discovery.sqlite"
    assert outputs.sample_stems == ("TumorBC2312_DNA", "NormalBC2257_DNA")
    assert read_peak_counts(outputs.store) == ({"total": 2, "admitted": 1}, 2)
    first, second = stored_peaks(outputs.store)
    assert (first.sample_stem, first.status) == ("TumorBC2312_DNA", "blank")
    assert (second.sample_stem, second.status) == ("NormalBC2257_DNA", "admitted")
    assert [item.peak_id for item in stored_events(outputs.store)] == [
        first.peak_id,
        second.peak_id,
    ]
    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == [
        "discovery.sqlite"
    ]
    assert all(raw.entered and raw.closed for raw in raw_by_path.values())

    published = tmp_path / "published"
    (tmp_path / "out").replace(published)
    relocated = read_discovery_batch(published / "discovery.sqlite")
    assert relocated.sample_order == outputs.sample_stems
    assert [peak.peak_id for peak in stored_peaks(relocated.path)] == [
        first.peak_id,
        second.peak_id,
    ]


def test_parallel_batch_preserves_serial_artifacts_and_sample_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_paths = (tmp_path / "sample-b.raw", tmp_path / "sample-a.raw")
    for raw_path in raw_paths:
        raw_path.write_bytes(raw_path.stem.encode())

    def _handles():
        return {
            path: _FakeRawHandle(
                [_scan_event(scan_number=index + 1, rt=8.0 + index)],
                rt=np.array([7.9 + index, 8.0 + index, 8.1 + index]),
                intensity=np.array([1.0, 100.0, 1.0]),
            )
            for index, path in enumerate(raw_paths)
        }

    serial_handles = _handles()
    serial = _run_discovery_batch(
        raw_paths,
        output_dir=tmp_path / "serial",
        settings=_settings(),
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: serial_handles[path],
    )

    parallel_handles = _handles()
    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw",
        lambda path, dll_dir, **_kwargs: parallel_handles[path],
    )

    class _SynchronousExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, worker, job):
            future = Future()
            future.set_result(worker(job))
            return future

        def shutdown(self, *, wait, cancel_futures=False) -> None:
            pass

    progress = []
    parallel = _run_discovery_batch(
        raw_paths,
        output_dir=tmp_path / "parallel",
        settings=_settings(),
        dll_dir=tmp_path / "dll",
        execution=ExecutionProfile(parallel_workers=2),
        executor_factory=_SynchronousExecutor,
        progress_callback=lambda completed, total, sample: progress.append(
            (completed, total, sample)
        ),
    )

    assert parallel.sample_stems == ("sample-b", "sample-a")
    assert _dump(serial.store) == _dump(parallel.store)
    assert all(raw.entered and raw.closed for raw in parallel_handles.values())
    assert progress == [(1, 2, "sample-b"), (2, 2, "sample-a")]


def test_parallel_batch_rejects_parent_raw_opener(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="RAW handles inside spawned workers"):
        _run_discovery_batch(
            (tmp_path / "sample.raw", tmp_path / "second.raw"),
            output_dir=tmp_path / "out",
            settings=_settings(),
            dll_dir=tmp_path / "dll",
            raw_opener=lambda path, dll_dir: _FakeRawHandle(events=[]),
            execution=ExecutionProfile(parallel_workers=2),
        )


def test_parallel_batch_reports_the_first_failure_without_publishing(
    tmp_path: Path,
) -> None:
    raw_paths = tuple(tmp_path / f"sample-{index}.raw" for index in range(4))
    for raw_path in raw_paths:
        raw_path.write_bytes(raw_path.stem.encode())

    class _FailFirstExecutor:
        def __init__(self, *args, **kwargs) -> None:
            self.submitted = 0

        def submit(self, worker, job):
            future = Future()
            if self.submitted == 0:
                stem = job.acquisition_input.raw_path.stem
                future.set_result(
                    DiscoverySampleProcessError(
                        raw_name=job.acquisition_input.raw_path.name,
                        message="RuntimeError: owner boom",
                        timing_records=(
                            TimingRecord(
                                stage="discover.failed_worker_partial",
                                elapsed_sec=0.25,
                                sample_stem=stem,
                                metrics={},
                            ),
                        ),
                        measurement_observations=(
                            {
                                "metric": "raw_open_count",
                                "scope": stem,
                                "unit": "opens",
                                "aggregation": "sum",
                                "source": "xic_extractor.raw_reader.open_raw",
                                "availability": "measured",
                                "value": 1,
                                "reason": "",
                            },
                        ),
                    )
                )
            self.submitted += 1
            return future

        def shutdown(self, *, wait, cancel_futures=False) -> None:
            pass

    recorder = TimingRecorder("discovery")
    with pytest.raises(DiscoveryProcessExecutionError) as caught:
        _run_discovery_batch(
            raw_paths,
            output_dir=tmp_path / "out",
            settings=_settings(),
            dll_dir=tmp_path / "dll",
            timing_recorder=recorder,
            execution=ExecutionProfile(parallel_workers=2),
            executor_factory=_FailFirstExecutor,
        )

    assert str(caught.value) == "sample-0.raw: RuntimeError: owner boom"
    assert not (tmp_path / "out" / "discovery.sqlite").exists()
    assert [record.stage for record in recorder.records] == [
        "discover.failed_worker_partial"
    ]
    assert [
        (row["metric"], row["scope"], row["value"])
        for row in recorder.measurement_observations
    ] == [("raw_open_count", "sample-0", 1)]


def test_batch_pipeline_keeps_formula_like_sample_names_as_text(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "=cmd.raw"
    raw_path.write_bytes(b"formula-path")
    raw = _FakeRawHandle(events=[])

    outputs = _run_discovery_batch(
        (raw_path,),
        output_dir=tmp_path / "out",
        settings=_settings(),
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: raw,
    )

    batch = read_discovery_batch(outputs.store)
    assert batch.sample_order == ("=cmd",)
    assert batch.samples["=cmd"].raw_file.name == "=cmd.raw"


def test_pipeline_records_an_event_without_ms1_signal_without_a_peak(
    tmp_path: Path,
) -> None:
    raw = _FakeRawHandle(
        [_scan_event(scan_number=101, rt=7.80, product_intensity=3000.0)],
        rt=np.asarray([], dtype=float),
        intensity=np.asarray([], dtype=float),
    )

    outputs = _run_discovery(
        tmp_path / "Sample.raw",
        output_dir=tmp_path / "out",
        settings=_settings(),
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: raw,
    )

    assert stored_peaks(outputs.store) == []
    (event,) = stored_events(outputs.store)
    assert event.peak_id == ""
    assert event.unattached_reason


def test_single_raw_pipeline_records_discovery_timing_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_sample_background_library(tmp_path)
    raw = _FakeRawHandle(
        [_scan_event(scan_number=101, rt=7.80, product_intensity=3000.0)],
        **_peak_kwargs(7.8, 600.0),
    )
    recorder = TimingRecorder("discovery", run_id="test-discovery")

    _run_discovery(
        tmp_path / "Sample.raw",
        output_dir=tmp_path / "out",
        settings=_settings(enable_blank_qualification=True),
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: raw,
        timing_recorder=recorder,
    )

    records_by_stage = {record.stage: record for record in recorder.records}
    assert set(records_by_stage) == {
        "discover.ms2_events",
        "discover.ion_grouping",
        "discover.ms1_capture",
        "discover.write_sample",
        "discover.publish_batch",
    }
    assert records_by_stage["discover.ms2_events"].sample_stem == "Sample"
    assert records_by_stage["discover.ms2_events"].metrics["event_count"] == 1
    assert records_by_stage["discover.ion_grouping"].metrics["ion_count"] == 1
    assert records_by_stage["discover.ms1_capture"].metrics == {
        "peak_count": 1,
        "event_count": 1,
        "logical_xic_request_count": 1,
        "xic_batch_count": 1,
        "adapter_chromatogram_call_count": 1,
        "trace_point_count": len(_peak_trace(7.8)[0]),
    }
    assert records_by_stage["discover.write_sample"].metrics == {
        "peak_count": 1,
        "event_count": 1,
    }
    assert records_by_stage["discover.publish_batch"].metrics == {"sample_count": 1}


def test_batch_pipeline_records_batch_publication_timing(tmp_path: Path) -> None:
    raw_path = tmp_path / "Blank.raw"
    raw_path.write_bytes(b"blank")
    raw = _FakeRawHandle(events=[])
    recorder = TimingRecorder("discovery", run_id="test-batch")

    _run_discovery_batch(
        (raw_path,),
        output_dir=tmp_path / "out",
        settings=_settings(),
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: raw,
        timing_recorder=recorder,
    )

    records_by_stage = {record.stage: record for record in recorder.records}
    assert records_by_stage["discover.publish_batch"].metrics == {"sample_count": 1}


def test_pipeline_uses_injected_raw_opener_with_dll_dir_and_closes_context(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "Sample.raw"
    raw = _FakeRawHandle(events=[])
    calls: list[tuple[Path, Path]] = []

    def _open(path: Path, dll_dir: Path) -> _FakeRawHandle:
        calls.append((path, dll_dir))
        return raw

    _run_discovery(
        raw_path,
        output_dir=tmp_path / "out",
        settings=_settings(),
        dll_dir=tmp_path / "dll",
        raw_opener=_open,
    )

    assert calls == [(raw_path, tmp_path / "dll")]
    assert raw.entered is True
    assert raw.closed is True


def test_batch_pipeline_binds_production_open_to_raw_content_and_recorder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "Sample.raw"
    raw_path.write_bytes(b"owner-bound-raw")
    raw = _FakeRawHandle(events=[])
    calls: list[tuple[Path, Path, object]] = []

    def _open(
        path: Path,
        dll_dir: Path,
        *,
        expected_acquisition_source: object = None,
        mass_recalibration: object = None,
        timing_recorder: object = None,
    ) -> _FakeRawHandle:
        calls.append(
            (
                path,
                dll_dir,
                expected_acquisition_source,
            )
        )
        assert timing_recorder is recorder
        raw.acquisition_source = expected_acquisition_source
        return raw

    monkeypatch.setattr("xic_extractor.raw_reader.open_raw", _open)

    recorder = TimingRecorder("discovery")
    _run_discovery_batch(
        (raw_path,),
        output_dir=tmp_path / "out",
        settings=_settings(),
        dll_dir=tmp_path / "dll",
        timing_recorder=recorder,
    )

    assert len(calls) == 1
    path, dll_dir, source = calls[0]
    assert path == raw_path
    assert dll_dir == tmp_path / "dll"
    assert source == build_local_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    assert raw.closed is True


def _peak_trace(
    apex_rt: float, amplitude: float = 1000.0
) -> tuple[np.ndarray, np.ndarray]:
    """A resolved Gaussian peak with enough points for the canonical profile."""
    rt = np.linspace(apex_rt - 1.0, apex_rt + 1.0, 201)
    return rt, 5.0 + amplitude * np.exp(-0.5 * ((rt - apex_rt) / 0.1) ** 2)


def _peak_kwargs(apex_rt: float, amplitude: float) -> dict[str, np.ndarray]:
    rt, intensity = _peak_trace(apex_rt, amplitude)
    return {"rt": rt, "intensity": intensity}


def _run_discovery_batch(
    raw_paths: tuple[Path, ...], **kwargs: Any
) -> DiscoveryBatchOutputs:
    settings = kwargs.pop("settings")
    spec = kwargs.pop("run_spec", None) or _compiled_run_spec(raw_paths, settings)
    kwargs.setdefault("execution", in_process_execution())
    return _pipeline.run_discovery_batch(spec, **kwargs)


@pytest.mark.parametrize(
    "mode", ("read_failure", "open_failure", "cancel", "publication_failure")
)
def test_failed_sample_keeps_completed_parts_and_closes_its_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from xic_extractor.configuration.mass_recalibration import MassRecalibration
    from xic_extractor.raw_reader import EXPECTED_THERMO_DLLS
    from xic_extractor.raw_reader_errors import RawReaderOpenError

    paths = (tmp_path / "observed.raw", tmp_path / "case.raw")
    for path, content in zip(paths, ("success", mode), strict=True):
        path.write_text(content)
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    for name in EXPECTED_THERMO_DLLS:
        (dll_dir / name).write_bytes(b"fake dll")
    settings = _settings(
        mass_recalibration=MassRecalibration.NONE, rt_min=7.5, rt_max=8.6
    )
    spec = _compiled_run_spec(paths, settings)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    if mode == "publication_failure":
        part_path(output_dir, "case").with_suffix(".sqlite.tmp").mkdir(parents=True)
    openers = {
        item.raw_path: vendor_fake_open_raw(
            DiscoverySampleJob(
                index,
                output_dir,
                settings,
                spec.signal_processing_profile,
                dll_dir,
                item,
            )
        )
        for index, item in enumerate(spec.acquisition_inputs)
    }
    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw",
        lambda path, dll, **kwargs: openers[path](path, dll, **kwargs),
    )

    recorder = TimingRecorder("discovery")
    with pytest.raises((RuntimeError, OSError, RawReaderOpenError)) as caught:
        _run_discovery_batch(
            paths,
            run_spec=spec,
            settings=settings,
            output_dir=output_dir,
            dll_dir=dll_dir,
            timing_recorder=recorder,
        )

    expected_text = {"publication_failure": "case", "cancel": "cancelled"}.get(
        mode, "vendor"
    )
    assert expected_text in str(caught.value)
    assert not (output_dir / "discovery.sqlite").exists()
    assert part_path(output_dir, "observed").is_file()
    for stem in ("observed", "case"):
        assert (output_dir / f"{stem}.closed").read_text() == "disposed"

    timing = {
        row["metric"]: row
        for row in recorder.measurement_observations
        if row["scope"] == "case"
    }
    if mode == "open_failure":
        assert timing["raw_open_count"]["availability"] == "unavailable"
        assert timing["raw_open_count"]["reason"]
    else:
        assert timing["raw_open_count"]["value"] == 1
    if mode in {"read_failure", "cancel"}:
        calls = timing["adapter_chromatogram_call_count"]
        assert calls["availability"] == "measured"
        assert isinstance(calls["value"], int)
        for metric in (
            "logical_xic_request_count",
            "xic_batch_count",
            "trace_point_count",
        ):
            assert timing[metric]["availability"] == "unavailable"
            assert timing[metric]["reason"]


@pytest.mark.parametrize("mode", ("success", "read_failure"))
def test_process_worker_records_raw_open_through_its_production_opener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from xic_extractor.configuration.mass_recalibration import MassRecalibration
    from xic_extractor.raw_reader import EXPECTED_THERMO_DLLS

    raw_path = tmp_path / "sample.raw"
    raw_path.write_text(mode)
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    for name in EXPECTED_THERMO_DLLS:
        (dll_dir / name).write_bytes(b"fake dll")
    settings = _settings(
        mass_recalibration=MassRecalibration.NONE, rt_min=7.5, rt_max=8.6
    )
    spec = _compiled_run_spec((raw_path,), settings)
    item = spec.acquisition_inputs[0]
    job = DiscoverySampleJob(
        0,
        tmp_path / "out",
        settings,
        spec.signal_processing_profile,
        dll_dir,
        item,
    )
    monkeypatch.setattr("xic_extractor.raw_reader.open_raw", vendor_fake_open_raw(job))

    result = run_discovery_sample_process_job(job, record_timing=True)

    expected_type = (
        DiscoverySampleProcessResult
        if mode == "success"
        else DiscoverySampleProcessError
    )
    assert isinstance(result, expected_type)
    rows = [
        (row["scope"], row["value"], row["availability"])
        for row in result.measurement_observations
        if row["metric"] == "raw_open_count"
    ]
    assert rows == [("sample", 1, "measured")]


def _dump(store: Path) -> list[str]:
    """The file's full content, statement by statement."""
    with closing(sqlite3.connect(store)) as db:
        return list(db.iterdump())
