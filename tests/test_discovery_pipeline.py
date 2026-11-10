import concurrent.futures
import csv
import json
import math
import multiprocessing
import pickle
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from tests.discovery_pipeline_fixtures import (
    _compiled_run_spec,
    _FakeRawHandle,
    _OwnerBoundFakeRawHandle,
    _read_csv,
    _run_discovery,
    _scan_event,
    _settings,
    _spec_bound_opener,
)
from xic_extractor.acquisition_contract import (
    _mint_raw_acquisition_session_receipt,
)
from xic_extractor.discovery import pipeline as _pipeline
from xic_extractor.discovery.batch_contract import DISCOVERY_BATCH_INDEX_COLUMNS
from xic_extractor.discovery.csv_reader import (
    read_discovery_batch_index,
    read_owner_bound_discovery_peaks_csv,
)
from xic_extractor.discovery.evidence_config import (
    DEFAULT_EVIDENCE_PROFILE,
    DiscoveryEvidenceProfile,
)
from xic_extractor.discovery.evidence_score import score_discovery_evidence
from xic_extractor.discovery.models import (
    DiscoveryBatchOutputs,
)
from xic_extractor.discovery.ms1_capture import _DiscoveryMs1Measurements
from xic_extractor.discovery.output_publication import (
    write_batch_index,
)
from xic_extractor.discovery.pipeline import (
    _acquire_discovery_sample,
    discover_sample_for_process_job,
)
from xic_extractor.discovery.runtime_measurements import (
    DISCOVERY_MS1_MEASUREMENT_NAMES,
    reject_caller_authored_discovery_ms1_measurement,
    validate_discovery_ms1_observations,
)
from xic_extractor.discovery.sample_process_execution import (
    DiscoveryProcessExecutionError,
    DiscoverySampleProcessError,
    DiscoverySampleProcessJob,
    DiscoverySampleProcessResult,
    run_discovery_sample_process_job,
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
    from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv

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

    # Only instrument I/O is substituted: CLI binding, search, physical processing,
    # evidence composition and both writers all execute their production paths.
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
        (candidate,) = read_discovery_peaks_csv(output_dir / "discovery_peaks.csv")
        assert candidate.sample_stem == sample
        assert candidate.matched_tag_names == tuple(name for name, _ in tags)
        assert candidate.neutral_loss_tag == tags[0][0]
        events = _read_csv(output_dir / "discovery_events.csv")
        (best,) = [
            row
            for row in events
            if row["ms2_scan_number"] == str(candidate.best_ms2_scan_id)
            and row["neutral_loss_tag"] == candidate.neutral_loss_tag
        ]
        assert float(best["product_mz"]) == pytest.approx(500.0 - tags[0][1])
        assert {
            row["ms2_scan_number"]
            for row in events
            if row["peak_id"] == candidate.peak_id
        } == {"100", "101"}
        assert candidate.ms1_apex_rt == pytest.approx(apex)
        (work,) = json.loads(
            (output_dir / "discovery_physical_facts.json").read_text(encoding="utf-8")
        )["work_manifest"]
        assert float.fromhex(work["xic_rt_min"]) == pytest.approx(apex - 0.7)
        assert float.fromhex(work["xic_rt_max"]) == pytest.approx(apex + 0.71)
        assert candidate.ms1_area == pytest.approx(
            height
            * 0.1
            * np.sqrt(2.0 * np.pi)
            * 60.0
            * math.erf(math.sqrt(math.log(10))),
            rel=0.01,
        )
        assert candidate.status == "admitted"
        events = _read_csv(output_dir / "discovery_events.csv")
        assert {row["peak_id"] for row in events} == {candidate.peak_id}
        assert raw.entered and raw.closed


@pytest.mark.parametrize("parallel", [False, True])
def test_cancelled_batch_retains_completed_samples_without_publishing_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parallel: bool
) -> None:
    raw_paths = tuple(tmp_path / f"sample-{index}.raw" for index in range(4))
    for path in raw_paths:
        path.write_bytes(path.name.encode())
    handles = {path: _FakeRawHandle(events=[]) for path in raw_paths}

    def opener(path, dll_dir):
        return handles[path]

    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline._owner_bound_raw_opener",
        lambda source, _mass_recalibration: (
            lambda path, dll_dir: _with_source(opener(path, dll_dir), source)
        ),
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
            max_workers=2 if parallel else 1,
            executor_factory=Executor if parallel else None,
            progress_callback=lambda done, total, sample: progress.append(sample),
            should_cancel=lambda: bool(progress),
        )

    completed = 2 if parallel else 1
    assert progress == [path.stem for path in raw_paths[:completed]]
    for path in raw_paths[:completed]:
        assert handles[path].closed
        assert (output_dir / path.stem / "discovery_peaks.csv").is_file()
    assert all(not handles[path].entered for path in raw_paths[completed:])
    assert not (output_dir / "discovery_batch_index.csv").exists()


def test_single_discovery_cancelled_after_acquisition_closes_raw_without_output(
    tmp_path: Path,
) -> None:
    raw = _FakeRawHandle(events=[])
    output_dir = tmp_path / "out"
    with pytest.raises(InterruptedError, match="Discovery cancelled"):
        _run_discovery(
            tmp_path / "sample.raw",
            output_dir=output_dir,
            settings=_settings(),
            dll_dir=tmp_path / "dll",
            raw_opener=lambda *_args: raw,
            should_cancel=lambda: raw.closed,
        )
    assert raw.entered and raw.closed
    assert not output_dir.exists()


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


def test_single_raw_pipeline_groups_strict_ms2_seeds_and_writes_dual_csvs(
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

    assert outputs.peaks_csv == output_dir / "discovery_peaks.csv"
    assert outputs.events_csv == output_dir / "discovery_events.csv"
    assert outputs.physical_facts_json == output_dir / "discovery_physical_facts.json"
    rows = _read_csv(outputs.peaks_csv)
    assert len(rows) == 1
    assert rows[0]["best_ms2_scan_id"] == "202"
    assert rows[0]["attached_event_count"] == "2"
    assert rows[0]["status"] == "blank"
    event_rows = _read_csv(outputs.events_csv)
    assert [row["ms2_scan_number"] for row in event_rows] == ["101", "202"]
    assert {row["peak_id"] for row in event_rows} == {rows[0]["peak_id"]}


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

    rows = _read_csv(outputs.peaks_csv)
    assert len(rows) == 2
    assert len({row["peak_id"] for row in rows}) == 2
    assert {row["best_ms2_scan_id"] for row in rows} == {"19561"}
    product_by_peak = {
        row["peak_id"]: row["product_mz"] for row in _read_csv(outputs.events_csv)
    }
    rows_by_product = {product_by_peak[row["peak_id"]]: row for row in rows}
    inferred = rows_by_product["184.113235"]
    direct = rows_by_product["185.115845"]
    assert inferred["precursor_mz_basis"] == "product_plus_neutral_loss"
    assert direct["precursor_mz_basis"] == "scan_precursor"


@pytest.mark.parametrize("reverse_products", [False, True])
def test_pipeline_retains_distinct_inferred_features_from_one_ms2_in_both_csvs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reverse_products: bool,
) -> None:
    # Both inferred ions fit one 1.6 m/z isolation around 258.3. Repeating a
    # product is duplicate evidence; the second MS1 feature is not a duplicate.
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

    rows = _read_csv(outputs.peaks_csv)
    assert len(rows) == 2
    precursor_by_peak = {
        row["peak_id"]: row["precursor_mz"] for row in _read_csv(outputs.events_csv)
    }
    areas = {
        round(float(precursor_by_peak[row["peak_id"]]), 4): float(row["ms1_area"])
        for row in rows
    }
    assert set(areas) == set(amplitudes)
    assert areas[258.6085] > areas[258.1085] > 0.0
    assert len({row["peak_id"] for row in rows}) == 2
    assert {row["best_ms2_scan_id"] for row in rows} == {"19561"}
    # Neither inferred ion has a parent MS1 reading, so no event attaches.
    assert {row["status"] for row in rows} == {"no_nl_event"}
    events = _read_csv(outputs.events_csv)
    # The repeated product of one scan is one event, not two.
    assert len(events) == 2
    assert {row["unattached_reason"] for row in events} == {"parent_ms1_unavailable"}


def test_pipeline_writes_header_only_csv_when_no_strict_seeds(tmp_path: Path) -> None:
    raw = _FakeRawHandle(events=[])

    outputs = _run_discovery(
        tmp_path / "Blank.raw",
        output_dir=tmp_path / "out",
        settings=_settings(),
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: raw,
    )

    with outputs.peaks_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    with outputs.events_csv.open(newline="", encoding="utf-8") as handle:
        review_rows = list(csv.reader(handle))

    assert len(rows) == 1
    assert "peak_id" in rows[0]
    assert len(review_rows) == 1
    assert "unattached_reason" in review_rows[0]


def test_batch_pipeline_writes_per_sample_csvs_and_index(
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
    assert outputs.batch_index_csv == tmp_path / "out" / "discovery_batch_index.csv"
    assert len(outputs.per_sample) == 2
    index_rows = _read_csv(outputs.batch_index_csv)
    assert [row["sample_stem"] for row in index_rows] == [
        "TumorBC2312_DNA",
        "NormalBC2257_DNA",
    ]
    assert [row["peak_count"] for row in index_rows] == ["1", "1"]
    assert [row["admitted_peak_count"] for row in index_rows] == ["0", "1"]
    assert {row["schema_version"] for row in index_rows} == {
        "discovery_batch_index_peaks"
    }
    assert "discovery_receipt_id" not in index_rows[0]
    assert "acquisition_source_id" not in index_rows[0]
    assert [row["peak_csv"] for row in index_rows] == [
        "TumorBC2312_DNA/discovery_peaks.csv",
        "NormalBC2257_DNA/discovery_peaks.csv",
    ]
    assert [row["event_csv"] for row in index_rows] == [
        "TumorBC2312_DNA/discovery_events.csv",
        "NormalBC2257_DNA/discovery_events.csv",
    ]

    first_rows = _read_csv(tmp_path / "out" / "TumorBC2312_DNA" / "discovery_peaks.csv")
    first_review_rows = _read_csv(
        tmp_path / "out" / "TumorBC2312_DNA" / "discovery_events.csv"
    )
    second_rows = _read_csv(
        tmp_path / "out" / "NormalBC2257_DNA" / "discovery_peaks.csv"
    )
    assert first_rows[0]["status"] == "blank"
    assert second_rows[0]["status"] == "admitted"
    assert [row["peak_id"] for row in first_review_rows] == [first_rows[0]["peak_id"]]
    assert [row["sample_stem"] for row in second_rows] == ["NormalBC2257_DNA"]
    assert not (tmp_path / "out" / "discovery_peaks.csv").exists()
    assert all(raw.entered and raw.closed for raw in raw_by_path.values())
    batch = read_discovery_batch_index(
        outputs.batch_index_csv,
    )
    receipt = batch.peak_receipts["TumorBC2312_DNA"]
    rebound = read_owner_bound_discovery_peaks_csv(
        batch.peak_csvs["TumorBC2312_DNA"],
        receipt=receipt,
    )
    assert tuple(candidate.peak_id for candidate in rebound) == (
        first_rows[0]["peak_id"],
    )

    published = tmp_path / "published"
    (tmp_path / "out").replace(published)
    relocated = read_discovery_batch_index(
        published / "discovery_batch_index.csv",
    )
    relocated_receipt = relocated.peak_receipts["TumorBC2312_DNA"]
    relocated_candidates = read_owner_bound_discovery_peaks_csv(
        relocated.peak_csvs["TumorBC2312_DNA"],
        receipt=relocated_receipt,
    )
    assert tuple(candidate.peak_id for candidate in relocated_candidates) == (
        first_rows[0]["peak_id"],
    )


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
        "xic_extractor.discovery.pipeline._owner_bound_raw_opener",
        lambda acquisition_source, _mass_recalibration: (
            lambda path, dll_dir: _with_source(
                parallel_handles[path], acquisition_source
            )
        ),
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
        max_workers=2,
        executor_factory=_SynchronousExecutor,
        progress_callback=lambda completed, total, sample: progress.append(
            (completed, total, sample)
        ),
    )

    assert [output.peaks_csv.parent.name for output in parallel.per_sample] == [
        "sample-b",
        "sample-a",
    ]
    assert _read_csv(serial.batch_index_csv) == _read_csv(parallel.batch_index_csv)
    for serial_output, parallel_output in zip(
        serial.per_sample, parallel.per_sample, strict=True
    ):
        assert serial_output.peaks_csv.read_bytes() == (
            parallel_output.peaks_csv.read_bytes()
        )
        assert serial_output.events_csv.read_bytes() == (
            parallel_output.events_csv.read_bytes()
        )
    assert all(raw.entered and raw.closed for raw in parallel_handles.values())
    assert progress == [(1, 2, "sample-b"), (2, 2, "sample-a")]


def test_parallel_batch_rejects_parent_raw_opener(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="RAW handles inside spawned workers"):
        _run_discovery_batch(
            (tmp_path / "sample.raw",),
            output_dir=tmp_path / "out",
            settings=_settings(),
            dll_dir=tmp_path / "dll",
            raw_opener=lambda path, dll_dir: _FakeRawHandle(events=[]),
            max_workers=2,
        )


def test_parallel_batch_preserves_first_failure_and_completed_census(
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
                future.set_result(
                    DiscoverySampleProcessError(
                        sample_index=job.sample_index,
                        sample_stem=job.raw_path.stem,
                        raw_name=job.raw_path.name,
                        **_job_identity(job),
                        message="RuntimeError: owner boom",
                        timing_records=(
                            TimingRecord(
                                stage="discover.failed_worker_partial",
                                elapsed_sec=0.25,
                                sample_stem=job.raw_path.stem,
                                metrics={},
                            ),
                        ),
                        measurement_observations=(
                            {
                                "metric": "worker_retry_count",
                                "scope": job.raw_path.stem,
                                "unit": "retries",
                                "aggregation": "sum",
                                "source": "discovery.worker",
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
            max_workers=2,
            executor_factory=_FailFirstExecutor,
        )

    error = caught.value
    assert error.first_failure.raw_name == "sample-0.raw"
    assert error.failed_sample_stems == ("sample-0",)
    assert error.failed_job_count == 1
    assert error.completed_sample_stems == ()
    assert error.cancelled_job_count == 1
    assert error.in_flight_job_count == 0
    assert error.unsubmitted_job_count == 2
    assert not (tmp_path / "out" / "discovery_batch_index.csv").exists()
    request_ipc = [
        row
        for row in recorder.measurement_observations
        if row["source"] == "discovery.process_job.request"
    ]
    assert {row["scope"] for row in request_ipc} == {"sample-0", "sample-1"}
    assert all(row["availability"] == "partial" for row in request_ipc)
    assert any(
        record.stage == "discover.failed_worker_partial" for record in recorder.records
    )
    assert any(
        row["metric"] == "worker_retry_count" and row["scope"] == "sample-0"
        for row in recorder.measurement_observations
    )


def test_parallel_batch_preserves_every_same_batch_failure(
    tmp_path: Path,
) -> None:
    raw_paths = tuple(tmp_path / f"sample-{index}.raw" for index in range(4))
    for raw_path in raw_paths:
        raw_path.write_bytes(raw_path.stem.encode())

    class _MultipleFailureExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, worker, job):
            future = Future()
            future.set_result(
                DiscoverySampleProcessError(
                    sample_index=job.sample_index,
                    sample_stem=job.raw_path.stem,
                    raw_name=job.raw_path.name,
                    **_job_identity(job),
                    message=f"RuntimeError: owner boom {job.sample_index}",
                )
            )
            return future

        def shutdown(self, *, wait, cancel_futures=False) -> None:
            pass

    with pytest.raises(DiscoveryProcessExecutionError) as caught:
        _run_discovery_batch(
            raw_paths,
            output_dir=tmp_path / "out",
            settings=_settings(),
            dll_dir=tmp_path / "dll",
            max_workers=2,
            executor_factory=_MultipleFailureExecutor,
        )

    error = caught.value
    assert error.first_failure.raw_name == "sample-0.raw"
    assert error.failed_sample_stems == ("sample-0", "sample-1")
    assert error.failed_job_count == 2
    assert error.completed_sample_stems == ()
    assert error.cancelled_job_count == 0
    assert error.in_flight_job_count == 0
    assert error.unsubmitted_job_count == 2
    assert not (tmp_path / "out" / "discovery_batch_index.csv").exists()


def test_parallel_batch_rejects_error_caller_authored_owner_counts(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"raw")

    class _ForgedErrorExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, worker, job):
            future = Future()
            future.set_result(
                DiscoverySampleProcessError(
                    sample_index=job.sample_index,
                    sample_stem=job.raw_path.stem,
                    raw_name=job.raw_path.name,
                    **_job_identity(job),
                    message="RuntimeError: owner boom",
                    measurement_observations=(
                        {
                            "metric": "raw_open_count",
                            "scope": job.raw_path.stem,
                            "unit": "opens",
                            "aggregation": "sum",
                            "source": "worker",
                            "availability": "measured",
                            "value": 999,
                            "reason": "",
                        },
                    ),
                )
            )
            return future

        def shutdown(self, *, wait, cancel_futures=False) -> None:
            pass

    with pytest.raises(ValueError, match="cannot author"):
        _run_discovery_batch(
            (raw_path,),
            output_dir=tmp_path / "out",
            settings=_settings(),
            dll_dir=tmp_path / "dll",
            timing_recorder=TimingRecorder("discovery"),
            max_workers=2,
            executor_factory=_ForgedErrorExecutor,
        )


def test_parallel_batch_projects_typed_error_measurements_and_reduces_raw_open(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"raw")
    measurements = _DiscoveryMs1Measurements(
        input_ion_count=0,
        input_event_count=0,
        exact_scan_window_count=0,
        predicted_chunked_call_count=0,
        batch_max_requests=256,
        acquisition_batches=(),
    )

    class _TypedErrorExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, worker, job):
            future = Future()
            raw_open_receipt = _mint_raw_acquisition_session_receipt(
                job.acquisition_source
            )
            future.set_result(
                DiscoverySampleProcessError(
                    sample_index=job.sample_index,
                    sample_stem=job.raw_path.stem,
                    raw_name=job.raw_path.name,
                    **_job_identity(job),
                    message="RuntimeError: owner boom",
                    ms1_measurements=measurements,
                    raw_open_receipt=raw_open_receipt,
                )
            )
            return future

        def shutdown(self, *, wait, cancel_futures=False) -> None:
            pass

    recorder = TimingRecorder("discovery")
    with pytest.raises(DiscoveryProcessExecutionError, match="owner boom"):
        _run_discovery_batch(
            (raw_path,),
            output_dir=tmp_path / "out",
            settings=_settings(),
            dll_dir=tmp_path / "dll",
            timing_recorder=recorder,
            max_workers=2,
            executor_factory=_TypedErrorExecutor,
        )

    rows = {
        str(row["metric"]): row
        for row in recorder.measurement_observations
        if row["scope"] == "sample"
    }
    assert rows["logical_xic_request_count"]["value"] == 0
    assert rows["logical_xic_request_count"]["availability"] == "measured"
    assert rows["raw_open_count"]["value"] == 1
    assert rows["raw_open_count"]["availability"] == "measured"
    assert rows["raw_open_count"]["reason"] == ""


def test_parallel_batch_disabled_timing_skips_ipc_serialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_paths = (tmp_path / "sample-0.raw", tmp_path / "sample-1.raw")
    for raw_path in raw_paths:
        raw_path.write_bytes(raw_path.stem.encode())

    class _FailureExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, worker, job):
            future = Future()
            future.set_result(
                DiscoverySampleProcessError(
                    sample_index=job.sample_index,
                    sample_stem=job.raw_path.stem,
                    raw_name=job.raw_path.name,
                    **_job_identity(job),
                    message="RuntimeError: owner boom",
                )
            )
            return future

        def shutdown(self, *, wait, cancel_futures=False) -> None:
            pass

    monkeypatch.setattr(
        "xic_extractor.discovery.sample_process_execution.pickle.dumps",
        lambda _value: (_ for _ in ()).throw(AssertionError("must not serialize")),
    )

    with pytest.raises(DiscoveryProcessExecutionError, match="owner boom"):
        _run_discovery_batch(
            raw_paths,
            output_dir=tmp_path / "out",
            settings=_settings(),
            dll_dir=tmp_path / "dll",
            max_workers=2,
            executor_factory=_FailureExecutor,
        )

    recorder = TimingRecorder("discovery")
    with pytest.raises(DiscoveryProcessExecutionError, match="owner boom"):
        _run_discovery_batch(
            raw_paths,
            output_dir=tmp_path / "instrumented-out",
            settings=_settings(),
            dll_dir=tmp_path / "dll",
            timing_recorder=recorder,
            max_workers=2,
            executor_factory=_FailureExecutor,
        )
    ipc_rows = [
        row for row in recorder.measurement_observations if row["metric"] == "ipc_bytes"
    ]
    assert len(ipc_rows) == 4
    assert all(row["availability"] == "failed" for row in ipc_rows)


def test_discovery_worker_disabled_mode_omits_diagnostic_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"raw")

    def _fail_with_diagnostics(*args, timing_recorder, **kwargs):
        timing_recorder.record("discover.hidden", elapsed_sec=1.0)
        timing_recorder.observe("raw_open_count", value=1)
        raise RuntimeError("owner boom")

    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline._discover_completed_sample",
        _fail_with_diagnostics,
    )
    job = _bound_process_job(raw_path, tmp_path, instrument_diagnostics=False)

    result = run_discovery_sample_process_job(
        job, discover_sample=discover_sample_for_process_job
    )

    assert isinstance(result, DiscoverySampleProcessError)
    assert result.timing_records == ()
    assert result.measurement_observations == ()


def test_discovery_worker_failure_carries_own_partial_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"raw")

    measurements = _DiscoveryMs1Measurements(
        input_ion_count=0,
        input_event_count=0,
        exact_scan_window_count=0,
        predicted_chunked_call_count=0,
        batch_max_requests=256,
        acquisition_batches=(),
    )

    def _fail_with_diagnostics(*args, timing_recorder, measurement_observer, **kwargs):
        timing_recorder.record("discover.partial", elapsed_sec=0.5)
        timing_recorder.observe("raw_open_count", value=1, unit="opens")
        timing_recorder.observe("worker_retry_count", value=1, unit="retries")
        measurement_observer(measurements)
        raise RuntimeError("owner boom")

    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline._discover_completed_sample",
        _fail_with_diagnostics,
    )
    job = _bound_process_job(raw_path, tmp_path, instrument_diagnostics=True)

    result = run_discovery_sample_process_job(
        job, discover_sample=discover_sample_for_process_job
    )

    assert isinstance(result, DiscoverySampleProcessError)
    assert [record.stage for record in result.timing_records] == ["discover.partial"]
    assert [row["metric"] for row in result.measurement_observations] == [
        "worker_retry_count"
    ]
    assert result.ms1_measurements == measurements


def test_discovery_process_error_payload_survives_spawn_pickle_roundtrip(
    tmp_path: Path,
) -> None:
    measurements = _DiscoveryMs1Measurements(
        input_ion_count=0,
        input_event_count=0,
        exact_scan_window_count=0,
        predicted_chunked_call_count=0,
        batch_max_requests=256,
        acquisition_batches=(),
    )
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"spawn-receipt")
    source = build_local_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    capability = source
    error = DiscoverySampleProcessError(
        sample_index=0,
        sample_stem="sample",
        raw_name="sample.raw",
        message="RuntimeError: owner boom",
        ms1_measurements=measurements,
        raw_open_receipt=_mint_raw_acquisition_session_receipt(capability),
    )

    context = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=1,
        mp_context=context,
    ) as executor:
        restored = executor.submit(pickle.loads, pickle.dumps(error)).result(timeout=30)

    assert restored == error


def test_discovery_raw_failure_preserves_open_and_adapter_measurements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"failure-receipt")
    source = build_local_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    receipt = _mint_raw_acquisition_session_receipt(source)
    raw = _FakeRawHandle(events=[])
    raw.raw_open_receipt = receipt
    spec = _compiled_run_spec((raw_path,), _settings())
    raw.acquisition_source = spec.acquisition_inputs[0].acquisition_source
    recorder = TimingRecorder("discovery")
    observed_receipts = []
    monkeypatch.setattr(
        "xic_extractor.discovery.cid_nl_search.collect_strict_nl_events",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("scan failed")),
    )

    with pytest.raises(RuntimeError, match="scan failed"):
        _acquire_discovery_sample(
            raw_path,
            dll_dir=tmp_path / "dll",
            raw_opener=lambda path, dll_dir: raw,
            timing_recorder=recorder,
            raw_open_receipt_observer=observed_receipts.append,
            run_spec=spec,
            acquisition_input=spec.acquisition_inputs[0],
        )

    rows = recorder.measurement_observations
    assert observed_receipts == [receipt]
    assert not any(row["metric"] == "raw_open_count" for row in rows)
    assert any(
        row["metric"] == "logical_xic_request_count"
        and row["availability"] == "unavailable"
        for row in rows
    )


def test_discovery_capture_failure_preserves_owner_acquisition_measurements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-failure")
    source = build_local_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    raw = _OwnerBoundFakeRawHandle(
        source,
        [_scan_event(scan_number=101, rt=7.80, product_intensity=3000.0)],
        **_peak_kwargs(7.8, 100.0),
    )
    recorder = TimingRecorder("discovery")
    spec = _compiled_run_spec((raw_path,), _settings())
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_capture._measured_peak",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("peak failed")),
    )

    with pytest.raises(RuntimeError, match="peak failed"):
        _acquire_discovery_sample(
            raw_path,
            dll_dir=tmp_path / "dll",
            raw_opener=lambda path, dll_dir: raw,
            timing_recorder=recorder,
            run_spec=spec,
            acquisition_input=spec.acquisition_inputs[0],
        )

    rows = {
        str(row["metric"]): row
        for row in recorder.measurement_observations
        if row["scope"] == "sample"
    }
    assert rows["logical_xic_request_count"]["value"] == 1
    assert rows["xic_batch_count"]["value"] == 1
    assert rows["adapter_chromatogram_call_count"]["value"] == 1
    assert rows["trace_point_count"]["value"] == len(_peak_trace(7.8)[0])
    assert raw.closed


def test_adapter_counter_failure_does_not_mask_discovery_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CounterFailureRaw(_FakeRawHandle):
        @property
        def raw_chromatogram_call_count(self) -> int:
            raise RuntimeError("counter failed")

    raw = _CounterFailureRaw(events=[])
    raw_path = tmp_path / "sample.raw"
    spec = _compiled_run_spec((raw_path,), _settings())
    raw.acquisition_source = spec.acquisition_inputs[0].acquisition_source
    recorder = TimingRecorder("discovery")
    monkeypatch.setattr(
        "xic_extractor.discovery.cid_nl_search.collect_strict_nl_events",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("scan failed")),
    )

    with pytest.raises(RuntimeError, match="scan failed"):
        _acquire_discovery_sample(
            raw_path,
            dll_dir=tmp_path / "dll",
            raw_opener=lambda path, dll_dir: raw,
            timing_recorder=recorder,
            run_spec=spec,
            acquisition_input=spec.acquisition_inputs[0],
        )

    adapter_row = next(
        row
        for row in recorder.measurement_observations
        if row["metric"] == "adapter_chromatogram_call_count"
    )
    assert adapter_row["availability"] == "unavailable"
    assert adapter_row["reason"] == (
        "raw handle call counter read failed: RuntimeError: counter failed"
    )


def test_parallel_batch_failure_preserves_completed_worker_diagnostics(
    tmp_path: Path,
) -> None:
    raw_paths = (tmp_path / "sample-0.raw", tmp_path / "sample-1.raw")
    for raw_path in raw_paths:
        raw_path.write_bytes(raw_path.stem.encode())

    class _MixedExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, worker, job):
            future = Future()
            if job.sample_index == 0:
                result: object = DiscoverySampleProcessError(
                    sample_index=0,
                    sample_stem="sample-0",
                    raw_name="sample-0.raw",
                    message="RuntimeError: owner boom",
                    **_job_identity(job),
                )
            else:
                result = DiscoverySampleProcessResult(
                    sample_index=1,
                    sample_stem="sample-1",
                    publication=cast(Any, None),
                    **_job_identity(job),
                    timing_records=(
                        TimingRecord(
                            stage="discover.worker",
                            elapsed_sec=2.0,
                            sample_stem="sample-1",
                        ),
                    ),
                    measurement_observations=(
                        {
                            "metric": "worker_retry_count",
                            "scope": "sample-1",
                            "unit": "retries",
                            "aggregation": "sum",
                            "source": "worker",
                            "availability": "measured",
                            "value": 1,
                            "reason": "",
                        },
                    ),
                )
            future.set_result(result)
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
            max_workers=2,
            executor_factory=_MixedExecutor,
        )

    assert caught.value.completed_sample_stems == ("sample-1",)
    assert [record.stage for record in recorder.records] == ["discover.worker"]
    observations = recorder.measurement_observations
    assert any(row["metric"] == "worker_retry_count" for row in observations)
    ipc_rows = [row for row in observations if row["metric"] == "ipc_bytes"]
    assert len(ipc_rows) == 4
    assert all(row["availability"] == "partial" for row in ipc_rows)
    assert {row["scope"] for row in ipc_rows} == {"sample-0", "sample-1"}


def test_batch_pipeline_escapes_excel_formula_strings_in_index(
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

    row = _read_csv(outputs.batch_index_csv)[0]
    assert row["sample_stem"] == "'=cmd"
    assert Path(row["raw_file"]).name == "=cmd.raw"
    assert row["peak_csv"].endswith("discovery_peaks.csv")
    assert row["event_csv"].endswith("discovery_events.csv")


def test_batch_index_writer_preserves_csv_and_timing_contract(tmp_path: Path) -> None:
    timing = TimingRecorder("discovery", timer=iter([1.0, 1.25]).__next__)
    output_path = tmp_path / "nested" / "discovery_batch_index.csv"
    row = dict.fromkeys(DISCOVERY_BATCH_INDEX_COLUMNS, "")
    row.update(
        {
            "sample_stem": "Sample_A",
            "raw_file": "Sample_A.raw",
            "peak_csv": "Sample_A/discovery_peaks.csv",
            "event_csv": "Sample_A/discovery_events.csv",
            "peak_count": "2",
        }
    )

    returned = write_batch_index(output_path, [row], timing_recorder=timing)

    assert returned == output_path
    raw = output_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw
    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(DISCOVERY_BATCH_INDEX_COLUMNS)
        assert list(reader) == [row]
    assert [(record.stage, record.metrics) for record in timing.records] == [
        (
            "discover.write_batch_index",
            {"raw_count": 1, "row_count": 1},
        )
    ]


def test_batch_index_writer_rejects_unexpected_columns(tmp_path: Path) -> None:
    row = dict.fromkeys(DISCOVERY_BATCH_INDEX_COLUMNS, "")
    row["unexpected"] = "value"

    with pytest.raises(ValueError, match="dict contains fields not in fieldnames"):
        write_batch_index(
            tmp_path / "discovery_batch_index.csv",
            [row],
            timing_recorder=TimingRecorder.disabled("discovery"),
        )


def test_run_discovery_keeps_stale_pair_when_event_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    stale_full = output_dir / "discovery_peaks.csv"
    stale_review = output_dir / "discovery_events.csv"
    stale_full.write_text("stale full\n", encoding="utf-8")
    stale_review.write_text("stale review\n", encoding="utf-8")
    raw = _FakeRawHandle(events=[])

    def _fail_review(path: Path, candidates: object) -> Path:
        raise OSError("simulated review writer failure")

    monkeypatch.setattr(
        "xic_extractor.discovery.output_publication.write_discovery_events_csv",
        _fail_review,
    )

    with pytest.raises(OSError, match="simulated review writer failure"):
        _run_discovery(
            tmp_path / "Blank.raw",
            output_dir=output_dir,
            settings=_settings(),
            dll_dir=tmp_path / "dll",
            raw_opener=lambda path, dll_dir: raw,
        )

    assert stale_full.read_text(encoding="utf-8") == "stale full\n"
    assert stale_review.read_text(encoding="utf-8") == "stale review\n"
    assert not (output_dir / "discovery_peaks.csv.tmp").exists()
    assert not (output_dir / "discovery_events.csv.tmp").exists()


def test_pipeline_threads_evidence_profile_into_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _FakeRawHandle(
        [_scan_event(scan_number=101, rt=7.80, product_intensity=3000.0)],
        **_peak_kwargs(7.8, 600.0),
    )
    settings = _settings(
        evidence_profile=DiscoveryEvidenceProfile(
            name="default",
            weights=replace(
                DEFAULT_EVIDENCE_PROFILE.weights,
                ms1_peak_present=30,
            ),
            thresholds=replace(
                DEFAULT_EVIDENCE_PROFILE.thresholds,
                scan_support_target=3,
            ),
        )
    )

    outputs = _run_discovery(
        tmp_path / "Sample.raw",
        output_dir=tmp_path / "out",
        settings=settings,
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: raw,
    )

    rows = _read_csv(outputs.peaks_csv)
    assert rows[0]["evidence_score"] == "58"


def test_pipeline_scores_ms1_scan_support_against_the_profile_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _FakeRawHandle(
        [_scan_event(scan_number=101, rt=7.80, product_intensity=3000.0)],
        **_peak_kwargs(7.8, 600.0),
    )
    settings = _settings(
        evidence_profile=DiscoveryEvidenceProfile(
            name="default",
            weights=DEFAULT_EVIDENCE_PROFILE.weights,
            thresholds=replace(
                DEFAULT_EVIDENCE_PROFILE.thresholds,
                scan_support_target=150,
            ),
        )
    )

    outputs = _run_discovery(
        tmp_path / "Sample.raw",
        output_dir=tmp_path / "out",
        settings=settings,
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: raw,
    )

    rows = _read_csv(outputs.peaks_csv)
    start, end = (
        float(rows[0][name]) for name in ("ms1_peak_rt_start", "ms1_peak_rt_end")
    )
    count = np.count_nonzero((raw._rt >= start) & (raw._rt <= end))
    assert 0 < count < 150
    assert int(rows[0]["evidence_score"]) == score_discovery_evidence(
        attached_event_count=int(rows[0]["attached_event_count"]),
        ms1_event_delta_min=float(rows[0]["ms1_apex_rt"]) - 7.80,
        ms2_product_max_intensity=3000.0,
        ms1_area=float(rows[0]["ms1_area"]),
        ms1_scan_support_score=count / 150,
        settings=settings,
    )


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

    assert _read_csv(outputs.peaks_csv) == []
    (event,) = _read_csv(outputs.events_csv)
    assert event["peak_id"] == ""
    assert event["unattached_reason"]


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
        "discover.blank_qualification",
        "discover.write_peaks_csv",
        "discover.write_events_csv",
        "discover.write_physical_facts_sidecar",
        "discover.publish_outputs",
        "discover.write_batch_index",
    }
    assert records_by_stage["discover.ms2_events"].sample_stem == "Sample"
    assert records_by_stage["discover.ms2_events"].metrics["event_count"] == 1
    assert records_by_stage["discover.ion_grouping"].metrics["ion_count"] == 1
    assert records_by_stage["discover.ms1_capture"].metrics["peak_count"] == 1
    assert records_by_stage["discover.ms1_capture"].metrics["event_count"] == 1
    assert (
        records_by_stage["discover.blank_qualification"].metrics[
            "excluded_background_count"
        ]
        == 1
    )
    assert records_by_stage["discover.write_peaks_csv"].metrics["row_count"] == 1
    assert records_by_stage["discover.write_events_csv"].metrics["row_count"] == 1
    assert records_by_stage["discover.publish_outputs"].metrics == {
        "artifact_count": 3,
        "replaced_existing_count": 0,
    }


def test_batch_pipeline_records_batch_index_timing(tmp_path: Path) -> None:
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
    assert records_by_stage["discover.write_batch_index"].metrics == {
        "raw_count": 1,
        "row_count": 1,
    }


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


def test_batch_pipeline_binds_production_open_to_receipted_raw_content(
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
    ) -> _FakeRawHandle:
        calls.append(
            (
                path,
                dll_dir,
                expected_acquisition_source,
            )
        )
        raw.acquisition_source = expected_acquisition_source
        raw.raw_open_receipt = _mint_raw_acquisition_session_receipt(
            expected_acquisition_source
        )
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
    raw_open_rows = [
        row
        for row in recorder.measurement_observations
        if row["metric"] == "raw_open_count" and row["scope"] == "Sample"
    ]
    assert len(raw_open_rows) == 1
    assert raw_open_rows[0]["value"] == 1
    assert raw_open_rows[0]["availability"] == "measured"
    assert raw.closed is True


def test_owner_bound_discovery_projects_complete_ms1_measurements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "Sample.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_local_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    raw = _OwnerBoundFakeRawHandle(
        source,
        [_scan_event(scan_number=101, rt=7.80, product_intensity=3000.0)],
        **_peak_kwargs(7.8, 100.0),
    )
    recorder = TimingRecorder("discovery")

    _run_discovery(
        raw_path,
        output_dir=tmp_path / "out",
        settings=_settings(),
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, dll_dir: raw,
        timing_recorder=recorder,
    )

    rows = {
        str(row["metric"]): row
        for row in recorder.measurement_observations
        if row["scope"] == "Sample"
    }
    assert rows["raw_open_count"]["value"] is None
    assert rows["raw_open_count"]["availability"] == "unavailable"
    assert rows["raw_open_count"]["reason"]
    assert rows["discovery_ms1_input_channel_count"]["value"] == 1
    assert rows["discovery_ms1_input_event_count"]["value"] == 1
    assert rows["logical_xic_request_count"]["value"] == 1
    assert rows["xic_batch_count"]["value"] == 1
    assert rows["adapter_chromatogram_call_count"]["value"] == 1
    assert rows["trace_point_count"]["value"] == len(_peak_trace(7.8)[0])
    assert rows["discovery_ms1_exact_scan_window_count"]["value"] == 1
    assert rows["discovery_ms1_predicted_chunked_call_count"]["value"] == 1
    assert rows["discovery_ms1_batch_max_requests"]["value"] == 256
    assert rows["discovery_ms1_predicted_call_reduction_pct"]["value"] == 0.0
    for metric in ("worker_peak_rss_bytes", "process_tree_peak_rss_bytes"):
        assert rows[metric]["availability"] == "unavailable"
        assert str(rows[metric]["reason"]).startswith("unsupported:")


@pytest.mark.parametrize(
    "metric",
    (
        "raw_open_count",
        "logical_xic_request_count",
        "trace_point_count",
        "discovery_ms1_input_channel_count",
    ),
)
def test_worker_measurement_projection_rejects_caller_authored_owner_counts(
    metric: str,
) -> None:
    with pytest.raises(ValueError, match="cannot author"):
        reject_caller_authored_discovery_ms1_measurement(
            {"metric": metric, "value": 999}
        )


def test_discovery_ms1_measurement_validator_rejects_duplicate_fields() -> None:
    recorder = TimingRecorder("discovery")
    for metric in DISCOVERY_MS1_MEASUREMENT_NAMES:
        recorder.observe(metric, value=1, scope="Sample")
    recorder.observe(
        "discovery_ms1_input_channel_count",
        value=1,
        scope="Sample",
    )

    with pytest.raises(ValueError, match="duplicate"):
        validate_discovery_ms1_observations(recorder, "Sample")


def test_discovery_ms1_measurement_validator_rejects_unknown_fields() -> None:
    recorder = TimingRecorder("discovery")
    for metric in DISCOVERY_MS1_MEASUREMENT_NAMES:
        recorder.observe(metric, value=1, scope="Sample")
    recorder.observe("discovery_ms1_unowned_count", value=1, scope="Sample")

    with pytest.raises(ValueError, match="unexpected=.*unowned"):
        validate_discovery_ms1_observations(recorder, "Sample")


def _peak_trace(
    apex_rt: float, amplitude: float = 1000.0
) -> tuple[np.ndarray, np.ndarray]:
    """A resolved Gaussian peak with enough points for the canonical profile."""
    rt = np.linspace(apex_rt - 1.0, apex_rt + 1.0, 201)
    return rt, 5.0 + amplitude * np.exp(-0.5 * ((rt - apex_rt) / 0.1) ** 2)


def _peak_kwargs(apex_rt: float, amplitude: float) -> dict[str, np.ndarray]:
    rt, intensity = _peak_trace(apex_rt, amplitude)
    return {"rt": rt, "intensity": intensity}


def _with_source(handle: Any, source: Any) -> Any:
    handle.acquisition_source = source
    return handle


def _job_identity(job: Any) -> dict[str, str]:
    return {
        "signal_processing_profile": job.run_spec.signal_processing_profile,
        "acquisition_source_id": job.acquisition_source.acquisition_source_id,
    }


def _bound_process_job(raw_path: Path, tmp_path: Path, **overrides: Any) -> Any:
    settings = _settings()
    spec = _compiled_run_spec((raw_path,), settings)
    source = spec.acquisition_inputs[0].acquisition_source
    fields: dict[str, Any] = {
        "sample_index": 0,
        "raw_path": raw_path,
        "output_dir": tmp_path / "out",
        "settings": settings,
        "dll_dir": tmp_path / "dll",
        "acquisition_source": source,
        "run_spec": spec,
        "acquisition_input": spec.acquisition_inputs[0],
    }
    fields.update(overrides)
    return DiscoverySampleProcessJob(**fields)


def _run_discovery_batch(
    raw_paths: tuple[Path, ...], **kwargs: Any
) -> DiscoveryBatchOutputs:
    spec = kwargs.pop("run_spec", None) or _compiled_run_spec(
        raw_paths, kwargs["settings"]
    )
    opener = kwargs.pop("raw_opener", None)
    if opener is not None:
        opener = _spec_bound_opener(spec, opener)
    return _pipeline.run_discovery_batch(
        raw_paths, run_spec=spec, raw_opener=opener, **kwargs
    )
