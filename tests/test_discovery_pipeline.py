import ast
import concurrent.futures
import csv
import multiprocessing
import pickle
from collections.abc import Iterator, Sequence
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from xic_extractor.acquisition_contract import (
    _mint_raw_acquisition_session_receipt,
    mint_raw_acquisition_session_capability,
)
from xic_extractor.alignment.csv_io import (
    read_discovery_batch_index,
    read_owner_bound_discovery_candidates_csv,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.diagnostics.timing import TimingRecord, TimingRecorder
from xic_extractor.discovery.evidence_config import (
    DEFAULT_EVIDENCE_PROFILE,
    DiscoveryEvidenceProfile,
)
from xic_extractor.discovery.models import (
    DISCOVERY_BRIEF_COLUMNS,
    DISCOVERY_CANDIDATE_COLUMNS,
    DiscoveryBatchOutputs,
    DiscoveryPhysicalWorkItem,
    DiscoveryRunOutputs,
    DiscoverySettings,
    NeutralLossProfile,
)
from xic_extractor.discovery.ms1_backfill import _DiscoveryMs1Measurements
from xic_extractor.discovery.physical_fact_persistence import (
    read_discovery_physical_fact_sidecar,
    write_discovery_physical_fact_sidecar,
)
from xic_extractor.discovery.pipeline import (
    _BATCH_INDEX_COLUMNS,
    _DISCOVERY_MS1_MEASUREMENT_NAMES,
    DiscoveryProcessExecutionError,
    DiscoverySampleProcessError,
    DiscoverySampleProcessJob,
    DiscoverySampleProcessResult,
    _discover_raw_file,
    _reject_caller_authored_discovery_ms1_measurement,
    _run_discovery_sample_process_job,
    _validate_discovery_ms1_observations,
    _write_batch_index,
    _write_dual_csvs,
    run_discovery,
    run_discovery_batch,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    build_acquisition_source_identity,
)
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    Ms2Scan,
    Ms2ScanEvent,
)
from xic_extractor.signal_processing import PeakDetectionResult, PeakResult
from xic_extractor.xic_models import XICRequest, XICTrace

NEUTRAL_LOSS_DA = 116.0474


def test_single_raw_pipeline_groups_strict_ms2_seeds_and_writes_dual_csvs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    output_dir = tmp_path / "out"
    raw = _FakeRawHandle(
        [
            _scan_event(scan_number=101, rt=7.80, product_intensity=3000.0),
            _scan_event(scan_number=202, rt=7.86, product_intensity=9000.0),
        ],
        rt=np.array([7.70, 7.86, 8.00]),
        intensity=np.array([10.0, 600.0, 20.0]),
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: _ok_peak(
            rt=7.86,
            intensity=600.0,
            area=42.0,
            start=7.70,
            end=8.00,
        ),
    )

    outputs = run_discovery(
        raw_path,
        output_dir=output_dir,
        settings=_settings(seed_rt_gap_min=0.20),
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda path, dll_dir: raw,
    )

    assert outputs == DiscoveryRunOutputs(
        candidates_csv=output_dir / "discovery_candidates.csv",
        review_csv=output_dir / "discovery_review.csv",
    )
    rows = _read_csv(outputs.candidates_csv)
    assert len(rows) == 1
    assert rows[0]["candidate_id"] == "TumorBC2312_DNA#202@mz258.1085_p142.0611"
    assert rows[0]["best_ms2_scan_id"] == "202"
    assert rows[0]["seed_scan_ids"] == "101;202"
    assert rows[0]["seed_event_count"] == "2"
    assert rows[0]["ms1_peak_found"] == "TRUE"
    review_rows = _read_csv(outputs.review_csv)
    assert len(review_rows) == 1
    assert review_rows[0]["candidate_id"] == (
        "TumorBC2312_DNA#202@mz258.1085_p142.0611"
    )
    assert "review_note" in review_rows[0]


def test_spec_bound_single_run_uses_compiled_acquisition_and_writes_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "Sample.raw"
    raw_path.write_bytes(b"compiled acquisition")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    handle_source = spec.acquisition_inputs[0].acquisition_source
    raw = _OwnerBoundFakeRawHandle(
        handle_source,
        [_scan_event(scan_number=101, rt=7.80)],
        rt=np.asarray([7.70, 7.80, 7.90]),
        intensity=np.asarray([10.0, 600.0, 20.0]),
    )
    from xic_extractor.peak_detection import evidence_facts as physical_owner

    physical_calls = {"prepare": 0, "evaluate": 0}
    real_prepare = physical_owner.prepare_physical_signal
    real_evaluate = physical_owner.evaluate_physical_peak

    def counted_prepare(*args, **kwargs):
        physical_calls["prepare"] += 1
        return real_prepare(*args, **kwargs)

    def counted_evaluate(*args, **kwargs):
        physical_calls["evaluate"] += 1
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.prepare_physical_signal",
        counted_prepare,
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.evaluate_physical_peak",
        counted_evaluate,
    )
    monkeypatch.setattr(physical_owner, "prepare_physical_signal", counted_prepare)
    monkeypatch.setattr(physical_owner, "evaluate_physical_peak", counted_evaluate)
    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline._acquisition_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("spec-bound run fingerprinted the RAW twice")
        ),
    )
    captured_manifest: tuple[DiscoveryPhysicalWorkItem, ...] = ()

    def capture_sidecar(path, **kwargs):
        nonlocal captured_manifest
        captured_manifest = tuple(kwargs["work_manifest"])
        return write_discovery_physical_fact_sidecar(path, **kwargs)

    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline.write_discovery_physical_fact_sidecar",
        capture_sidecar,
    )

    outputs = run_discovery(
        raw_path,
        output_dir=tmp_path / "out",
        settings=settings,
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda path, dll_dir: raw,
        run_spec=spec,
    )

    assert outputs.physical_facts_json == (
        tmp_path / "out" / "discovery_physical_facts.v1.json"
    )
    assert outputs.physical_facts_json is not None
    assert physical_calls == {"prepare": 1, "evaluate": 1}
    expected_work_manifest = captured_manifest
    assert len(expected_work_manifest) == 1
    records = read_discovery_physical_fact_sidecar(
        outputs.physical_facts_json,
        expected_run_spec=spec,
        expected_acquisition_input=spec.acquisition_inputs[0],
        expected_work_manifest=expected_work_manifest,
    )
    assert len(records) == 1
    assert (
        records[0].candidate_id == _read_csv(outputs.candidates_csv)[0]["candidate_id"]
    )
    assert records[0].evaluation.prepared.profile_id == spec.profile_id
    assert physical_calls == {"prepare": 2, "evaluate": 2}
    assert raw.raw_chromatogram_call_count == 1


def test_run_spec_conflicts_fail_before_raw_opener(tmp_path: Path) -> None:
    raw_path = tmp_path / "Sample.raw"
    raw_path.write_bytes(b"compiled acquisition")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    opens: list[Path] = []

    def _open(path: Path, _dll_dir: Path):
        opens.append(path)
        return _FakeRawHandle(events=[])

    with pytest.raises(ValueError, match="settings conflict"):
        run_discovery(
            raw_path,
            output_dir=tmp_path / "settings-out",
            settings=replace(settings, seed_rt_gap_min=0.33),
            peak_config=_peak_config(tmp_path),
            raw_opener=_open,
            run_spec=spec,
        )
    with pytest.raises(ValueError, match="peak config conflicts"):
        run_discovery(
            raw_path,
            output_dir=tmp_path / "profile-out",
            settings=settings,
            peak_config=replace(
                _peak_config(tmp_path),
                signal_processing_window_points=21,
            ),
            raw_opener=_open,
            run_spec=spec,
        )
    with pytest.raises(TypeError, match="ProcessingRunSpec"):
        run_discovery(
            raw_path,
            output_dir=tmp_path / "invalid-out",
            settings=settings,
            peak_config=_peak_config(tmp_path),
            raw_opener=_open,
            run_spec=object(),  # type: ignore[arg-type]
        )
    assert opens == []


@pytest.mark.parametrize("binding", ["missing", "crossed"])
def test_spec_bound_handle_closes_source_before_ms2_or_xic(
    tmp_path: Path,
    binding: str,
) -> None:
    raw_path = tmp_path / "Sample.raw"
    raw_path.write_bytes(b"compiled acquisition")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    if binding == "missing":
        source = None
    else:
        other = tmp_path / "Other.raw"
        other.write_bytes(b"different acquisition")
        source = build_acquisition_source_identity(
            other,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )

    class CountingHandle:
        acquisition_source = source
        raw_chromatogram_call_count = 0

        def __init__(self) -> None:
            self.ms2_calls = 0
            self.xic_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_ms2_scans(self, *_args):
            self.ms2_calls += 1
            return iter(())

        def extract_xic_many(self, requests):
            self.xic_calls += 1
            return tuple(XICTrace.empty() for _request in requests)

        def scan_window_for_request(self, _request):
            return (0, 0)

    handle = CountingHandle()

    with pytest.raises(ValueError, match="lacks|crosses"):
        run_discovery(
            raw_path,
            output_dir=tmp_path / "out",
            settings=settings,
            peak_config=_peak_config(tmp_path),
            raw_opener=lambda *_args: handle,
            run_spec=spec,
        )

    assert handle.ms2_calls == 0
    assert handle.xic_calls == 0


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
        rt=np.array([23.10, 23.34, 23.50]),
        intensity=np.array([10.0, 1000.0, 20.0]),
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: _ok_peak(
            rt=23.34,
            intensity=1000.0,
            area=123456.0,
            start=23.10,
            end=23.50,
        ),
    )

    outputs = run_discovery(
        raw_path,
        output_dir=output_dir,
        settings=_settings(seed_rt_gap_min=0.20, ms2_precursor_tol_da=1.6),
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda path, dll_dir: raw,
    )

    rows = _read_csv(outputs.candidates_csv)
    candidate_ids = {row["candidate_id"] for row in rows}
    assert len(rows) == 2
    assert len(candidate_ids) == 2
    assert "TumorBC2312_DNA#19561@mz300.160635_p184.113235" in candidate_ids
    assert "TumorBC2312_DNA#19561@mz301.164978_p185.115845" in candidate_ids
    rows_by_product = {row["product_mz"]: row for row in rows}
    inferred = rows_by_product["184.113"]
    direct = rows_by_product["185.116"]
    assert inferred["precursor_mz_basis"] == "product_plus_neutral_loss"
    assert inferred["neutral_loss_error_basis"] == (
        "configured_loss_inferred_precursor"
    )
    assert direct["precursor_mz_basis"] == "scan_precursor"
    assert direct["neutral_loss_error_basis"] == "measured_scan_precursor_product"


def test_pipeline_writes_header_only_csv_when_no_strict_seeds(tmp_path: Path) -> None:
    raw = _FakeRawHandle(events=[])

    outputs = run_discovery(
        tmp_path / "Blank.raw",
        output_dir=tmp_path / "out",
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda path, dll_dir: raw,
    )

    with outputs.candidates_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    with outputs.review_csv.open(newline="", encoding="utf-8") as handle:
        review_rows = list(csv.reader(handle))

    assert len(rows) == 1
    assert "candidate_id" in rows[0]
    assert len(review_rows) == 1
    assert "review_note" in review_rows[0]


def test_batch_pipeline_writes_per_sample_csvs_and_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_paths = (
        tmp_path / "TumorBC2312_DNA.raw",
        tmp_path / "NormalBC2257_DNA.raw",
    )
    for raw_path in raw_paths:
        raw_path.write_bytes(f"discovery:{raw_path.stem}".encode())
    raw_by_path = {
        raw_paths[0]: _FakeRawHandle(
            [_scan_event(scan_number=101, rt=7.80, product_intensity=3000.0)],
            rt=np.array([7.70, 7.80, 8.00]),
            intensity=np.array([10.0, 600.0, 20.0]),
        ),
        raw_paths[1]: _FakeRawHandle(
            [_scan_event(scan_number=202, rt=9.10, product_intensity=7000.0)],
            rt=np.array([9.00, 9.10, 9.30]),
            intensity=np.array([15.0, 900.0, 25.0]),
        ),
    }
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda rt, intensity, peak_config, **kwargs: _ok_peak(
            rt=float(rt[1]),
            intensity=float(intensity[1]),
            area=float(intensity[1] * 0.1),
            start=float(rt[0]),
            end=float(rt[-1]),
        ),
    )

    outputs = run_discovery_batch(
        raw_paths,
        output_dir=tmp_path / "out",
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
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
    assert [row["candidate_count"] for row in index_rows] == ["1", "1"]
    assert {row["schema_version"] for row in index_rows} == {"discovery_batch_index_v2"}
    assert all(
        row["discovery_receipt_id"].startswith("xic.discovery-candidate-universe.v1:")
        for row in index_rows
    )
    assert [row["medium_count"] for row in index_rows] == ["1", "1"]
    assert [row["candidate_csv"] for row in index_rows] == [
        "TumorBC2312_DNA/discovery_candidates.csv",
        "NormalBC2257_DNA/discovery_candidates.csv",
    ]
    assert [row["review_csv"] for row in index_rows] == [
        "TumorBC2312_DNA/discovery_review.csv",
        "NormalBC2257_DNA/discovery_review.csv",
    ]

    first_rows = _read_csv(
        tmp_path / "out" / "TumorBC2312_DNA" / "discovery_candidates.csv"
    )
    first_review_rows = _read_csv(
        tmp_path / "out" / "TumorBC2312_DNA" / "discovery_review.csv"
    )
    second_rows = _read_csv(
        tmp_path / "out" / "NormalBC2257_DNA" / "discovery_candidates.csv"
    )
    assert [row["candidate_id"] for row in first_rows] == [
        "TumorBC2312_DNA#101@mz258.1085_p142.0611"
    ]
    assert [row["candidate_id"] for row in first_review_rows] == [
        "TumorBC2312_DNA#101@mz258.1085_p142.0611"
    ]
    assert [row["candidate_id"] for row in second_rows] == [
        "NormalBC2257_DNA#202@mz258.1085_p142.0611"
    ]
    assert not (tmp_path / "out" / "discovery_candidates.csv").exists()
    assert all(raw.entered and raw.closed for raw in raw_by_path.values())
    batch = read_discovery_batch_index(
        outputs.batch_index_csv,
        require_owner_closure=True,
    )
    receipt = batch.candidate_receipts["TumorBC2312_DNA"]
    assert receipt is not None
    rebound = read_owner_bound_discovery_candidates_csv(
        batch.candidate_csvs["TumorBC2312_DNA"],
        receipt=receipt,
        require_successor_columns=True,
    )
    assert tuple(candidate.candidate_id for candidate in rebound) == (
        "TumorBC2312_DNA#101@mz258.1085_p142.0611",
    )

    published = tmp_path / "published"
    (tmp_path / "out").replace(published)
    relocated = read_discovery_batch_index(
        published / "discovery_batch_index.csv",
        require_owner_closure=True,
    )
    relocated_receipt = relocated.candidate_receipts["TumorBC2312_DNA"]
    assert relocated_receipt is not None
    relocated_candidates = read_owner_bound_discovery_candidates_csv(
        relocated.candidate_csvs["TumorBC2312_DNA"],
        receipt=relocated_receipt,
        require_successor_columns=True,
    )
    assert tuple(candidate.candidate_id for candidate in relocated_candidates) == (
        "TumorBC2312_DNA#101@mz258.1085_p142.0611",
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

    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda rt, intensity, peak_config, **kwargs: _ok_peak(
            rt=float(rt[1]),
            intensity=float(intensity[1]),
            area=10.0,
            start=float(rt[0]),
            end=float(rt[-1]),
        ),
    )
    serial_handles = _handles()
    serial = run_discovery_batch(
        raw_paths,
        output_dir=tmp_path / "serial",
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda path, dll_dir: serial_handles[path],
    )

    parallel_handles = _handles()
    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline._owner_bound_raw_opener",
        lambda acquisition_source, raw_session_capability: (
            lambda path, dll_dir: parallel_handles[path]
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
    parallel = run_discovery_batch(
        raw_paths,
        output_dir=tmp_path / "parallel",
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        max_workers=2,
        executor_factory=_SynchronousExecutor,
        progress_callback=lambda completed, total, sample: progress.append(
            (completed, total, sample)
        ),
    )

    assert [output.candidates_csv.parent.name for output in parallel.per_sample] == [
        "sample-b",
        "sample-a",
    ]
    assert _read_csv(serial.batch_index_csv) == _read_csv(parallel.batch_index_csv)
    for serial_output, parallel_output in zip(
        serial.per_sample, parallel.per_sample, strict=True
    ):
        assert serial_output.candidates_csv.read_bytes() == (
            parallel_output.candidates_csv.read_bytes()
        )
        assert serial_output.review_csv.read_bytes() == (
            parallel_output.review_csv.read_bytes()
        )
    assert all(raw.entered and raw.closed for raw in parallel_handles.values())
    assert progress == [(1, 2, "sample-b"), (2, 2, "sample-a")]


def test_batch_serial_and_process_share_one_run_spec_and_sample_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_paths = (tmp_path / "sample-b.raw", tmp_path / "sample-a.raw")
    for raw_path in raw_paths:
        raw_path.write_bytes(raw_path.stem.encode())
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        raw_paths, discovery_settings=settings
    )

    serial_handles = {
        path: _OwnerBoundFakeRawHandle(
            spec.acquisition_inputs[index].acquisition_source,
            [],
            rt=np.asarray([], dtype=float),
            intensity=np.asarray([], dtype=float),
        )
        for index, path in enumerate(raw_paths)
    }
    serial = run_discovery_batch(
        raw_paths,
        output_dir=tmp_path / "serial-spec",
        settings=settings,
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda path, dll_dir: serial_handles[path],
        run_spec=spec,
    )

    process_handles = {
        path: _OwnerBoundFakeRawHandle(
            spec.acquisition_inputs[index].acquisition_source,
            [],
            rt=np.asarray([], dtype=float),
            intensity=np.asarray([], dtype=float),
        )
        for index, path in enumerate(raw_paths)
    }
    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline._owner_bound_raw_opener",
        lambda acquisition_source, raw_session_capability: (
            lambda path, dll_dir: process_handles[path]
        ),
    )

    submitted_jobs: list[DiscoverySampleProcessJob] = []

    class _SynchronousExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, worker, job):
            submitted_jobs.append(job)
            future = Future()
            future.set_result(worker(job))
            return future

        def shutdown(self, *, wait, cancel_futures=False) -> None:
            pass

    process = run_discovery_batch(
        raw_paths,
        output_dir=tmp_path / "process-spec",
        settings=settings,
        peak_config=_peak_config(tmp_path),
        max_workers=2,
        executor_factory=_SynchronousExecutor,
        run_spec=spec,
    )

    assert tuple(job.run_spec for job in submitted_jobs) == (spec, spec)
    assert tuple(job.acquisition_input for job in submitted_jobs) == (
        spec.acquisition_inputs
    )
    assert all(pickle.loads(pickle.dumps(job)) == job for job in submitted_jobs)
    assert _read_csv(serial.batch_index_csv) == _read_csv(process.batch_index_csv)
    for serial_output, process_output in zip(
        serial.per_sample, process.per_sample, strict=True
    ):
        assert serial_output.physical_facts_json is not None
        assert process_output.physical_facts_json is not None
        assert serial_output.physical_facts_json.read_bytes() == (
            process_output.physical_facts_json.read_bytes()
        )


def test_parallel_batch_rejects_parent_raw_opener(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="RAW handles inside spawned workers"):
        run_discovery_batch(
            (tmp_path / "sample.raw",),
            output_dir=tmp_path / "out",
            settings=_settings(),
            peak_config=_peak_config(tmp_path),
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
        run_discovery_batch(
            raw_paths,
            output_dir=tmp_path / "out",
            settings=_settings(),
            peak_config=_peak_config(tmp_path),
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
                    message=f"RuntimeError: owner boom {job.sample_index}",
                )
            )
            return future

        def shutdown(self, *, wait, cancel_futures=False) -> None:
            pass

    with pytest.raises(DiscoveryProcessExecutionError) as caught:
        run_discovery_batch(
            raw_paths,
            output_dir=tmp_path / "out",
            settings=_settings(),
            peak_config=_peak_config(tmp_path),
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
        run_discovery_batch(
            (raw_path,),
            output_dir=tmp_path / "out",
            settings=_settings(),
            peak_config=_peak_config(tmp_path),
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
        input_group_count=0,
        empty_group_count=0,
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
                job.raw_session_capability
            )
            future.set_result(
                DiscoverySampleProcessError(
                    sample_index=job.sample_index,
                    sample_stem=job.raw_path.stem,
                    raw_name=job.raw_path.name,
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
        run_discovery_batch(
            (raw_path,),
            output_dir=tmp_path / "out",
            settings=_settings(),
            peak_config=_peak_config(tmp_path),
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
                    message="RuntimeError: owner boom",
                )
            )
            return future

        def shutdown(self, *, wait, cancel_futures=False) -> None:
            pass

    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline.pickle.dumps",
        lambda _value: (_ for _ in ()).throw(AssertionError("must not serialize")),
    )

    with pytest.raises(DiscoveryProcessExecutionError, match="owner boom"):
        run_discovery_batch(
            raw_paths,
            output_dir=tmp_path / "out",
            settings=_settings(),
            peak_config=_peak_config(tmp_path),
            max_workers=2,
            executor_factory=_FailureExecutor,
        )

    recorder = TimingRecorder("discovery")
    with pytest.raises(DiscoveryProcessExecutionError, match="owner boom"):
        run_discovery_batch(
            raw_paths,
            output_dir=tmp_path / "instrumented-out",
            settings=_settings(),
            peak_config=_peak_config(tmp_path),
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
        "xic_extractor.discovery.pipeline._discover_raw_file",
        _fail_with_diagnostics,
    )
    job = DiscoverySampleProcessJob(
        sample_index=0,
        raw_path=raw_path,
        output_dir=tmp_path / "out",
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        acquisition_source=build_acquisition_source_identity(
            raw_path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        ),
        raw_session_capability=mint_raw_acquisition_session_capability(
            build_acquisition_source_identity(
                raw_path,
                acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
                observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
            )
        ),
        instrument_diagnostics=False,
    )

    result = _run_discovery_sample_process_job(job)

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
        input_group_count=0,
        empty_group_count=0,
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
        "xic_extractor.discovery.pipeline._discover_raw_file",
        _fail_with_diagnostics,
    )
    job = DiscoverySampleProcessJob(
        sample_index=0,
        raw_path=raw_path,
        output_dir=tmp_path / "out",
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        acquisition_source=build_acquisition_source_identity(
            raw_path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        ),
        raw_session_capability=mint_raw_acquisition_session_capability(
            build_acquisition_source_identity(
                raw_path,
                acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
                observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
            )
        ),
        instrument_diagnostics=True,
    )

    result = _run_discovery_sample_process_job(job)

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
        input_group_count=0,
        empty_group_count=0,
        exact_scan_window_count=0,
        predicted_chunked_call_count=0,
        batch_max_requests=256,
        acquisition_batches=(),
    )
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"spawn-receipt")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    capability = mint_raw_acquisition_session_capability(source)
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


def test_real_spawn_rejects_full_mismatched_process_job_with_exact_identity(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"real-spawn-mismatched-job")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    acquisition_input = spec.acquisition_inputs[0]
    source = acquisition_input.acquisition_source
    job = DiscoverySampleProcessJob(
        sample_index=0,
        raw_path=raw_path,
        output_dir=tmp_path / "out",
        settings=replace(settings, seed_rt_gap_min=0.33),
        peak_config=_peak_config(tmp_path),
        acquisition_source=source,
        raw_session_capability=mint_raw_acquisition_session_capability(source),
        run_spec=spec,
        acquisition_input=acquisition_input,
    )

    context = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=1,
        mp_context=context,
    ) as executor:
        result = executor.submit(_run_discovery_sample_process_job, job).result(
            timeout=30
        )

    assert isinstance(result, DiscoverySampleProcessError)
    assert result.message == "ValueError: Discovery process job crosses method settings"
    assert result.run_spec_id == spec.run_spec_id
    assert result.profile_id == spec.profile_id
    assert result.acquisition_source_id == source.acquisition_source_id
    assert all(
        value.strip()
        for value in (
            result.run_spec_id,
            result.profile_id,
            result.acquisition_source_id,
        )
    )


@pytest.mark.parametrize(
    ("spec_bound", "replacement_position"),
    [(False, 1), (False, 2), (True, 1), (True, 2), (True, 3)],
)
@pytest.mark.parametrize("prior_outputs", [False, True])
def test_discovery_output_publication_rolls_back_every_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spec_bound: bool,
    replacement_position: int,
    prior_outputs: bool,
) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    final_paths = [
        output_dir / "discovery_candidates.csv",
        output_dir / "discovery_review.csv",
        output_dir / "discovery_physical_facts.v1.json",
    ]
    run_spec = None
    acquisition_input = None
    if spec_bound:
        raw_path = tmp_path / "sample.raw"
        raw_path.write_bytes(b"atomic-publication")
        run_spec = compile_discovery_processing_run_spec(
            (raw_path,), discovery_settings=_settings()
        )
        acquisition_input = run_spec.acquisition_inputs[0]
    prior = {
        path: f"prior-{index}".encode("utf-8")
        for index, path in enumerate(final_paths)
    }
    if prior_outputs:
        for path, payload in prior.items():
            path.write_bytes(payload)

    real_replace = Path.replace
    published = 0
    final_names = {path.name for path in final_paths}

    def fail_selected_publication(source: Path, target: Path) -> Path:
        nonlocal published
        target = Path(target)
        if (
            source.name.endswith(".tmp")
            and target.name in final_names
            and not target.name.endswith(".tmp")
        ):
            published += 1
            if published == replacement_position:
                raise OSError(f"publication failure {replacement_position}")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_selected_publication)

    with pytest.raises(OSError, match=f"publication failure {replacement_position}"):
        _write_dual_csvs(
            output_dir,
            (),
            timing_recorder=TimingRecorder.disabled("discovery"),
            sample_stem="sample",
            run_spec=run_spec,
            acquisition_input=acquisition_input,
        )

    for path in final_paths:
        if prior_outputs:
            assert path.read_bytes() == prior[path]
        else:
            assert not path.exists()
    assert not [
        path for path in output_dir.iterdir() if path.name.endswith((".tmp", ".bak"))
    ]


def test_legacy_publication_removes_stale_spec_bound_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "out"
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"spec-to-legacy-success")
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=_settings()
    )
    spec_outputs = _write_dual_csvs(
        output_dir,
        (),
        timing_recorder=TimingRecorder.disabled("discovery"),
        sample_stem="sample",
        run_spec=spec,
        acquisition_input=spec.acquisition_inputs[0],
    )
    assert spec_outputs.physical_facts_json is not None
    assert spec_outputs.physical_facts_json.exists()
    expected_candidates = (",".join(DISCOVERY_CANDIDATE_COLUMNS) + "\n").encode()
    expected_review = (",".join(DISCOVERY_BRIEF_COLUMNS) + "\n").encode()

    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline.write_discovery_candidates_csv",
        lambda path, _candidates: path.write_bytes(expected_candidates),
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline.write_discovery_review_csv",
        lambda path, _candidates: path.write_bytes(expected_review),
    )

    outputs = _write_dual_csvs(
        output_dir,
        (),
        timing_recorder=TimingRecorder.disabled("discovery"),
        sample_stem="sample",
    )

    assert outputs.candidates_csv.read_bytes() == expected_candidates
    assert outputs.review_csv.read_bytes() == expected_review
    assert outputs.physical_facts_json is None
    assert not (output_dir / "discovery_physical_facts.v1.json").exists()
    assert not [
        path for path in output_dir.iterdir() if path.name.endswith((".tmp", ".bak"))
    ]


@pytest.mark.parametrize("failure_point", ["backup", "publish-1", "publish-2"])
def test_spec_to_legacy_failure_restores_complete_prior_member_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    output_dir = tmp_path / "out"
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"spec-to-legacy-rollback")
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=_settings()
    )
    spec_outputs = _write_dual_csvs(
        output_dir,
        (),
        timing_recorder=TimingRecorder.disabled("discovery"),
        sample_stem="sample",
        run_spec=spec,
        acquisition_input=spec.acquisition_inputs[0],
    )
    assert spec_outputs.physical_facts_json is not None
    final_paths = (
        spec_outputs.candidates_csv,
        spec_outputs.review_csv,
        spec_outputs.physical_facts_json,
    )
    prior = {path: path.read_bytes() for path in final_paths}
    physical_path = spec_outputs.physical_facts_json
    real_replace = Path.replace
    published = 0

    def fail_transition(source: Path, target: Path) -> Path:
        nonlocal published
        target = Path(target)
        if (
            failure_point == "backup"
            and source == physical_path
            and target.name == f"{physical_path.name}.bak"
        ):
            raise OSError("legacy transition backup failure")
        if source.name.endswith(".tmp") and target in final_paths[:2]:
            published += 1
            if failure_point == f"publish-{published}":
                raise OSError(f"legacy transition {failure_point} failure")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_transition)

    with pytest.raises(OSError, match="legacy transition"):
        _write_dual_csvs(
            output_dir,
            (),
            timing_recorder=TimingRecorder.disabled("discovery"),
            sample_stem="sample",
        )

    assert {path: path.read_bytes() for path in final_paths} == prior
    assert not [
        path for path in output_dir.iterdir() if path.name.endswith((".tmp", ".bak"))
    ]


@pytest.mark.parametrize("prior_outputs", [False, True])
def test_discovery_output_publication_rolls_back_sidecar_staging_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prior_outputs: bool,
) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"sidecar-stage-failure")
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=_settings()
    )
    final_paths = (
        output_dir / "discovery_candidates.csv",
        output_dir / "discovery_review.csv",
        output_dir / "discovery_physical_facts.v1.json",
    )
    prior = {path: f"prior-{index}".encode() for index, path in enumerate(final_paths)}
    if prior_outputs:
        for path, payload in prior.items():
            path.write_bytes(payload)
    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline.write_discovery_physical_fact_sidecar",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("sidecar staging failed")
        ),
    )

    with pytest.raises(OSError, match="sidecar staging failed"):
        _write_dual_csvs(
            output_dir,
            (),
            timing_recorder=TimingRecorder.disabled("discovery"),
            sample_stem="sample",
            run_spec=spec,
            acquisition_input=spec.acquisition_inputs[0],
        )

    for path in final_paths:
        if prior_outputs:
            assert path.read_bytes() == prior[path]
        else:
            assert not path.exists()
    assert not [
        path for path in output_dir.iterdir() if path.name.endswith((".tmp", ".bak"))
    ]


def test_process_job_validates_binding_and_preserves_failure_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"process identity")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    acquisition_input = spec.acquisition_inputs[0]
    source = acquisition_input.acquisition_source
    capability = mint_raw_acquisition_session_capability(source)
    opener_requests: list[Path] = []

    def _rejecting_owner_opener(_source, _capability):
        def _open(path: Path, _dll_dir: Path):
            opener_requests.append(path)
            raise RuntimeError("owner boom")

        return _open

    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline._owner_bound_raw_opener",
        _rejecting_owner_opener,
    )
    valid_job = DiscoverySampleProcessJob(
        sample_index=0,
        raw_path=raw_path,
        output_dir=tmp_path / "out",
        settings=settings,
        peak_config=_peak_config(tmp_path),
        acquisition_source=source,
        raw_session_capability=capability,
        run_spec=spec,
        acquisition_input=acquisition_input,
    )

    failure = _run_discovery_sample_process_job(valid_job)

    assert isinstance(failure, DiscoverySampleProcessError)
    assert failure.run_spec_id == spec.run_spec_id
    assert failure.profile_id == spec.profile_id
    assert failure.acquisition_source_id == source.acquisition_source_id
    assert failure.message == "RuntimeError: owner boom"
    assert opener_requests == [raw_path]

    crossed_job = replace(
        valid_job,
        settings=replace(settings, seed_rt_gap_min=0.33),
    )
    crossed = _run_discovery_sample_process_job(crossed_job)
    assert isinstance(crossed, DiscoverySampleProcessError)
    assert (
        crossed.message == "ValueError: Discovery process job crosses method settings"
    )
    assert crossed.run_spec_id == spec.run_spec_id
    assert crossed.profile_id == spec.profile_id
    assert crossed.acquisition_source_id == source.acquisition_source_id
    assert opener_requests == [raw_path]


def test_discovery_raw_failure_preserves_open_and_adapter_measurements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"failure-receipt")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    receipt = _mint_raw_acquisition_session_receipt(
        mint_raw_acquisition_session_capability(source)
    )
    raw = _FakeRawHandle(events=[])
    raw.raw_open_receipt = receipt
    recorder = TimingRecorder("discovery")
    observed_receipts = []
    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline.collect_strict_nl_seeds",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("scan failed")),
    )

    with pytest.raises(RuntimeError, match="scan failed"):
        _discover_raw_file(
            raw_path,
            settings=_settings(),
            peak_config=_peak_config(tmp_path),
            raw_opener=lambda path, dll_dir: raw,
            timing_recorder=recorder,
            raw_open_receipt_observer=observed_receipts.append,
        )

    rows = recorder.measurement_observations
    assert observed_receipts == [receipt]
    assert not any(row["metric"] == "raw_open_count" for row in rows)
    assert any(
        row["metric"] == "logical_xic_request_count"
        and row["availability"] == "unavailable"
        for row in rows
    )


def test_discovery_peak_failure_preserves_owner_acquisition_measurements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-failure")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    raw = _OwnerBoundFakeRawHandle(
        source,
        [_scan_event(scan_number=101, rt=7.80, product_intensity=3000.0)],
        rt=np.asarray([7.7, 7.8, 7.9]),
        intensity=np.asarray([10.0, 100.0, 10.0]),
    )
    recorder = TimingRecorder("discovery")
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("peak failed")),
    )

    with pytest.raises(RuntimeError, match="peak failed"):
        _discover_raw_file(
            raw_path,
            settings=_settings(),
            peak_config=_peak_config(tmp_path),
            raw_opener=lambda path, dll_dir: raw,
            timing_recorder=recorder,
        )

    rows = {
        str(row["metric"]): row
        for row in recorder.measurement_observations
        if row["scope"] == "sample"
    }
    assert rows["logical_xic_request_count"]["value"] == 1
    assert rows["xic_batch_count"]["value"] == 1
    assert rows["adapter_chromatogram_call_count"]["value"] == 1
    assert rows["trace_point_count"]["value"] == 3
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
    recorder = TimingRecorder("discovery")
    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline.collect_strict_nl_seeds",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("scan failed")),
    )

    with pytest.raises(RuntimeError, match="scan failed"):
        _discover_raw_file(
            tmp_path / "sample.raw",
            settings=_settings(),
            peak_config=_peak_config(tmp_path),
            raw_opener=lambda path, dll_dir: raw,
            timing_recorder=recorder,
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
                )
            else:
                result = DiscoverySampleProcessResult(
                    sample_index=1,
                    sample_stem="sample-1",
                    outputs=cast(Any, None),
                    receipt=cast(Any, None),
                    index_row={},
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
        run_discovery_batch(
            raw_paths,
            output_dir=tmp_path / "out",
            settings=_settings(),
            peak_config=_peak_config(tmp_path),
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

    outputs = run_discovery_batch(
        (raw_path,),
        output_dir=tmp_path / "out",
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda path, dll_dir: raw,
    )

    row = _read_csv(outputs.batch_index_csv)[0]
    assert row["sample_stem"] == "'=cmd"
    assert Path(row["raw_file"]).name == "=cmd.raw"
    assert row["candidate_csv"].endswith("discovery_candidates.csv")
    assert row["review_csv"].endswith("discovery_review.csv")


def test_batch_index_writer_preserves_csv_and_timing_contract(tmp_path: Path) -> None:
    timing = TimingRecorder("discovery", timer=iter([1.0, 1.25]).__next__)
    output_path = tmp_path / "nested" / "discovery_batch_index.csv"
    row = dict.fromkeys(_BATCH_INDEX_COLUMNS, "")
    row.update(
        {
            "sample_stem": "Sample_A",
            "raw_file": "Sample_A.raw",
            "candidate_csv": "Sample_A/discovery_candidates.csv",
            "review_csv": "Sample_A/discovery_review.csv",
            "candidate_count": "2",
            "high_count": "1",
            "medium_count": "1",
            "low_count": "0",
        }
    )

    returned = _write_batch_index(output_path, [row], timing_recorder=timing)

    assert returned == output_path
    raw = output_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw
    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(_BATCH_INDEX_COLUMNS)
        assert list(reader) == [row]
    assert [(record.stage, record.metrics) for record in timing.records] == [
        (
            "discover.write_batch_index",
            {"raw_count": 1, "row_count": 1},
        )
    ]


def test_batch_index_writer_rejects_unexpected_columns(tmp_path: Path) -> None:
    row = dict.fromkeys(_BATCH_INDEX_COLUMNS, "")
    row["unexpected"] = "value"

    with pytest.raises(ValueError, match="dict contains fields not in fieldnames"):
        _write_batch_index(
            tmp_path / "discovery_batch_index.csv",
            [row],
            timing_recorder=TimingRecorder.disabled("discovery"),
        )


def test_run_discovery_keeps_stale_pair_when_review_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    stale_full = output_dir / "discovery_candidates.csv"
    stale_review = output_dir / "discovery_review.csv"
    stale_full.write_text("stale full\n", encoding="utf-8")
    stale_review.write_text("stale review\n", encoding="utf-8")
    raw = _FakeRawHandle(events=[])

    def _fail_review(path: Path, candidates: object) -> Path:
        raise OSError("simulated review writer failure")

    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline.write_discovery_review_csv",
        _fail_review,
    )

    with pytest.raises(OSError, match="simulated review writer failure"):
        run_discovery(
            tmp_path / "Blank.raw",
            output_dir=output_dir,
            settings=_settings(),
            peak_config=_peak_config(tmp_path),
            raw_opener=lambda path, dll_dir: raw,
        )

    assert stale_full.read_text(encoding="utf-8") == "stale full\n"
    assert stale_review.read_text(encoding="utf-8") == "stale review\n"
    assert not (output_dir / "discovery_candidates.csv.tmp").exists()
    assert not (output_dir / "discovery_review.csv.tmp").exists()


def test_pipeline_threads_evidence_profile_into_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _FakeRawHandle(
        [_scan_event(scan_number=101, rt=7.80, product_intensity=3000.0)],
        rt=np.array([7.70, 7.80, 8.00]),
        intensity=np.array([10.0, 600.0, 20.0]),
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: _ok_peak(
            rt=7.80,
            intensity=600.0,
            area=42.0,
            start=7.70,
            end=8.00,
        ),
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

    outputs = run_discovery(
        tmp_path / "Sample.raw",
        output_dir=tmp_path / "out",
        settings=settings,
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda path, dll_dir: raw,
    )

    rows = _read_csv(outputs.candidates_csv)
    assert rows[0]["evidence_score"] == "58"


def test_pipeline_populates_ms1_scan_support_score_from_profile_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _FakeRawHandle(
        [_scan_event(scan_number=101, rt=7.80, product_intensity=3000.0)],
        rt=np.array([7.60, 7.70, 7.80, 7.90, 8.00]),
        intensity=np.array([10.0, 100.0, 600.0, 300.0, 20.0]),
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: _ok_peak(
            rt=7.80,
            intensity=600.0,
            area=42.0,
            start=7.70,
            end=8.00,
        ),
    )
    settings = _settings(
        evidence_profile=DiscoveryEvidenceProfile(
            name="default",
            weights=DEFAULT_EVIDENCE_PROFILE.weights,
            thresholds=replace(
                DEFAULT_EVIDENCE_PROFILE.thresholds,
                scan_support_target=8,
            ),
        )
    )

    outputs = run_discovery(
        tmp_path / "Sample.raw",
        output_dir=tmp_path / "out",
        settings=settings,
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda path, dll_dir: raw,
    )

    rows = _read_csv(outputs.candidates_csv)
    assert rows[0]["ms1_scan_support_score"] == "0.5"


def test_pipeline_leaves_ms1_scan_support_blank_for_missing_ms1_peak(
    tmp_path: Path,
) -> None:
    raw = _FakeRawHandle(
        [_scan_event(scan_number=101, rt=7.80, product_intensity=3000.0)],
        rt=np.asarray([], dtype=float),
        intensity=np.asarray([], dtype=float),
    )

    outputs = run_discovery(
        tmp_path / "Sample.raw",
        output_dir=tmp_path / "out",
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda path, dll_dir: raw,
    )

    rows = _read_csv(outputs.candidates_csv)
    assert rows[0]["ms1_peak_found"] == "FALSE"
    assert rows[0]["ms1_scan_support_score"] == ""


def test_single_raw_pipeline_records_discovery_timing_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _FakeRawHandle(
        [_scan_event(scan_number=101, rt=7.80, product_intensity=3000.0)],
        rt=np.array([7.70, 7.80, 8.00]),
        intensity=np.array([10.0, 600.0, 20.0]),
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: _ok_peak(
            rt=7.80,
            intensity=600.0,
            area=42.0,
            start=7.70,
            end=8.00,
        ),
    )
    recorder = TimingRecorder("discovery", run_id="test-discovery")

    run_discovery(
        tmp_path / "Sample.raw",
        output_dir=tmp_path / "out",
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda path, dll_dir: raw,
        timing_recorder=recorder,
    )

    records_by_stage = {record.stage: record for record in recorder.records}
    assert set(records_by_stage) == {
        "discover.ms2_seeds",
        "discover.group_seeds",
        "discover.ms1_backfill",
        "discover.peak_anchor",
        "discover.write_candidates_csv",
        "discover.write_review_csv",
    }
    assert records_by_stage["discover.ms2_seeds"].sample_stem == "Sample"
    assert records_by_stage["discover.ms2_seeds"].metrics["seed_count"] == 1
    assert records_by_stage["discover.group_seeds"].metrics["group_count"] == 1
    assert records_by_stage["discover.ms1_backfill"].metrics["candidate_count"] == 1
    assert records_by_stage["discover.peak_anchor"].metrics["candidate_count"] == 1
    assert records_by_stage["discover.write_candidates_csv"].metrics["row_count"] == 1
    assert records_by_stage["discover.write_review_csv"].metrics["row_count"] == 1


def test_batch_pipeline_records_batch_index_timing(tmp_path: Path) -> None:
    raw_path = tmp_path / "Blank.raw"
    raw_path.write_bytes(b"blank")
    raw = _FakeRawHandle(events=[])
    recorder = TimingRecorder("discovery", run_id="test-batch")

    run_discovery_batch(
        (raw_path,),
        output_dir=tmp_path / "out",
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
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
    peak_config = _peak_config(tmp_path)
    raw = _FakeRawHandle(events=[])
    calls: list[tuple[Path, Path]] = []

    def _open(path: Path, dll_dir: Path) -> _FakeRawHandle:
        calls.append((path, dll_dir))
        return raw

    run_discovery(
        raw_path,
        output_dir=tmp_path / "out",
        settings=_settings(),
        peak_config=peak_config,
        raw_opener=_open,
    )

    assert calls == [(raw_path, peak_config.dll_dir)]
    assert raw.entered is True
    assert raw.closed is True


def test_batch_pipeline_binds_production_open_to_receipted_raw_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "Sample.raw"
    raw_path.write_bytes(b"owner-bound-raw")
    peak_config = _peak_config(tmp_path)
    raw = _FakeRawHandle(events=[])
    calls: list[tuple[Path, Path, object, object]] = []

    def _open(
        path: Path,
        dll_dir: Path,
        *,
        expected_acquisition_source: object = None,
        _raw_session_capability: object = None,
    ) -> _FakeRawHandle:
        calls.append(
            (
                path,
                dll_dir,
                expected_acquisition_source,
                _raw_session_capability,
            )
        )
        raw.raw_open_receipt = _mint_raw_acquisition_session_receipt(
            _raw_session_capability
        )
        return raw

    monkeypatch.setattr("xic_extractor.raw_reader.open_raw", _open)

    recorder = TimingRecorder("discovery")
    run_discovery_batch(
        (raw_path,),
        output_dir=tmp_path / "out",
        settings=_settings(),
        peak_config=peak_config,
        timing_recorder=recorder,
    )

    assert len(calls) == 1
    path, dll_dir, source, capability = calls[0]
    assert path == raw_path
    assert dll_dir == peak_config.dll_dir
    assert source == build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    assert capability.acquisition_source == source
    raw_open_rows = [
        row
        for row in recorder.measurement_observations
        if row["metric"] == "raw_open_count" and row["scope"] == "Sample"
    ]
    assert len(raw_open_rows) == 1
    assert raw_open_rows[0]["value"] == 1
    assert raw_open_rows[0]["availability"] == "measured"
    assert raw.closed is True


def test_pipeline_does_not_import_targeted_extractor_module() -> None:
    pipeline_path = (
        Path(__file__).resolve().parents[1]
        / "xic_extractor"
        / "discovery"
        / "pipeline.py"
    )
    tree = ast.parse(pipeline_path.read_text(encoding="utf-8"))

    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_from_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "xic_extractor.extractor" not in imported_modules | imported_from_modules


def test_owner_bound_discovery_projects_complete_ms1_measurements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "Sample.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    raw = _OwnerBoundFakeRawHandle(
        source,
        [_scan_event(scan_number=101, rt=7.80, product_intensity=3000.0)],
        rt=np.asarray([7.7, 7.8, 7.9]),
        intensity=np.asarray([10.0, 100.0, 10.0]),
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: _ok_peak(
            rt=7.8,
            intensity=100.0,
            area=10.0,
            start=7.7,
            end=7.9,
        ),
    )
    recorder = TimingRecorder("discovery")

    run_discovery(
        raw_path,
        output_dir=tmp_path / "out",
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
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
    assert rows["discovery_ms1_input_group_count"]["value"] == 1
    assert rows["discovery_ms1_empty_group_count"]["value"] == 0
    assert rows["logical_xic_request_count"]["value"] == 1
    assert rows["xic_batch_count"]["value"] == 1
    assert rows["adapter_chromatogram_call_count"]["value"] == 1
    assert rows["trace_point_count"]["value"] == 3
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
        "discovery_ms1_input_group_count",
    ),
)
def test_worker_measurement_projection_rejects_caller_authored_owner_counts(
    metric: str,
) -> None:
    with pytest.raises(ValueError, match="cannot author"):
        _reject_caller_authored_discovery_ms1_measurement(
            {"metric": metric, "value": 999}
        )


def test_discovery_ms1_measurement_validator_rejects_duplicate_fields() -> None:
    recorder = TimingRecorder("discovery")
    for metric in _DISCOVERY_MS1_MEASUREMENT_NAMES:
        recorder.observe(metric, value=1, scope="Sample")
    recorder.observe(
        "discovery_ms1_input_group_count",
        value=1,
        scope="Sample",
    )

    with pytest.raises(ValueError, match="duplicate"):
        _validate_discovery_ms1_observations(recorder, "Sample")


def test_discovery_ms1_measurement_validator_rejects_unknown_fields() -> None:
    recorder = TimingRecorder("discovery")
    for metric in _DISCOVERY_MS1_MEASUREMENT_NAMES:
        recorder.observe(metric, value=1, scope="Sample")
    recorder.observe("discovery_ms1_unowned_count", value=1, scope="Sample")

    with pytest.raises(ValueError, match="unexpected=.*unowned"):
        _validate_discovery_ms1_observations(recorder, "Sample")


def _settings(**overrides: object) -> DiscoverySettings:
    values = {
        "neutral_loss_profile": NeutralLossProfile("DNA_dR", NEUTRAL_LOSS_DA),
        **overrides,
    }
    return DiscoverySettings(**cast(Any, values))


def _peak_config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path / "dll",
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        resolver_mode="local_minimum",
    )


def _scan_event(
    *,
    scan_number: int,
    rt: float,
    product_intensity: float = 10000.0,
    precursor_mz: float = 258.1085,
    masses: list[float] | None = None,
    intensities: list[float] | None = None,
) -> Ms2ScanEvent:
    if masses is None:
        masses = [precursor_mz - NEUTRAL_LOSS_DA]
    if intensities is None:
        intensities = [product_intensity]
    return Ms2ScanEvent(
        scan=Ms2Scan(
            scan_number=scan_number,
            rt=rt,
            precursor_mz=precursor_mz,
            masses=np.asarray(masses, dtype=float),
            intensities=np.asarray(intensities, dtype=float),
            base_peak=max(intensities),
        ),
        parse_error=None,
        scan_number=scan_number,
    )


def _ok_peak(
    *,
    rt: float,
    intensity: float,
    area: float,
    start: float,
    end: float,
) -> PeakDetectionResult:
    return PeakDetectionResult(
        status="OK",
        peak=PeakResult(
            rt=rt,
            intensity=intensity,
            intensity_smoothed=intensity,
            area=area,
            peak_start=start,
            peak_end=end,
        ),
        n_points=3,
        max_smoothed=intensity,
        n_prominent_peaks=1,
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class _OwnerBoundFakeRawHandle:
    def __init__(
        self,
        source: AcquisitionSourceIdentity,
        events: list[Ms2ScanEvent],
        *,
        rt: np.ndarray,
        intensity: np.ndarray,
    ) -> None:
        self.acquisition_source = source
        self._events = events
        self._rt = rt
        self._intensity = intensity
        self.raw_chromatogram_call_count = 0
        self.entered = False
        self.closed = False

    def __enter__(self) -> "_OwnerBoundFakeRawHandle":
        self.entered = True
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def iter_ms2_scans(
        self,
        rt_min: float,
        rt_max: float,
    ) -> Iterator[Ms2ScanEvent]:
        yield from self._events

    def extract_xic_many(
        self,
        requests: Sequence[XICRequest],
    ) -> tuple[XICTrace, ...]:
        self.raw_chromatogram_call_count += len(
            {self.scan_window_for_request(request) for request in requests}
        )
        return tuple(
            XICTrace.from_arrays(self._rt, self._intensity) for _request in requests
        )

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        return int(request.rt_min * 100), int(request.rt_max * 100)


class _FakeRawHandle:
    def __init__(
        self,
        events: list[Ms2ScanEvent],
        *,
        rt: np.ndarray | None = None,
        intensity: np.ndarray | None = None,
    ) -> None:
        self._events = events
        self._rt = np.asarray([] if rt is None else rt, dtype=float)
        self._intensity = np.asarray(
            [] if intensity is None else intensity, dtype=float
        )
        self.entered = False
        self.closed = False

    def __enter__(self) -> "_FakeRawHandle":
        self.entered = True
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def iter_ms2_scans(self, rt_min: float, rt_max: float) -> Iterator[Ms2ScanEvent]:
        yield from self._events

    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        return self._rt, self._intensity
