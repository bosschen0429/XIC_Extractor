import ast
import csv
from collections.abc import Iterator
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from xic_extractor.alignment.csv_io import (
    read_discovery_batch_index,
    read_owner_bound_discovery_candidates_csv,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.discovery.evidence_config import (
    DEFAULT_EVIDENCE_PROFILE,
    DiscoveryEvidenceProfile,
)
from xic_extractor.discovery.models import (
    DiscoveryBatchOutputs,
    DiscoveryRunOutputs,
    DiscoverySettings,
    NeutralLossProfile,
)
from xic_extractor.discovery.pipeline import (
    _BATCH_INDEX_COLUMNS,
    DiscoveryProcessExecutionError,
    DiscoverySampleProcessError,
    _write_batch_index,
    run_discovery,
    run_discovery_batch,
)
from xic_extractor.processing_identity import build_acquisition_source_identity
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    Ms2Scan,
    Ms2ScanEvent,
)
from xic_extractor.signal_processing import PeakDetectionResult, PeakResult

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
    assert (
        "TumorBC2312_DNA#19561@mz300.160635_p184.113235"
        in candidate_ids
    )
    assert (
        "TumorBC2312_DNA#19561@mz301.164978_p185.115845"
        in candidate_ids
    )
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
    assert {row["schema_version"] for row in index_rows} == {
        "discovery_batch_index_v2"
    }
    assert all(
        row["discovery_receipt_id"].startswith(
            "xic.discovery-candidate-universe.v1:"
        )
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
        lambda acquisition_source: (
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
                    )
                )
            self.submitted += 1
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
    calls: list[tuple[Path, Path, object]] = []

    def _open(
        path: Path,
        dll_dir: Path,
        *,
        expected_acquisition_source: object = None,
    ) -> _FakeRawHandle:
        calls.append((path, dll_dir, expected_acquisition_source))
        return raw

    monkeypatch.setattr("xic_extractor.raw_reader.open_raw", _open)

    run_discovery_batch(
        (raw_path,),
        output_dir=tmp_path / "out",
        settings=_settings(),
        peak_config=peak_config,
    )

    assert calls == [
        (
            raw_path,
            peak_config.dll_dir,
            build_acquisition_source_identity(
                raw_path,
                acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
                observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
            ),
        )
    ]
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
