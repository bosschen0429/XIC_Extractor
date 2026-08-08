from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import xic_extractor.alignment.pipeline as pipeline_module
from tests.alignment_pipeline_helpers import FakeRawOpener
from tests.alignment_pipeline_helpers import (
    patch_owner_pipeline_to_matrix as _patch_owner_pipeline_to_matrix,
)
from tests.alignment_pipeline_helpers import peak_config as _peak_config
from tests.alignment_pipeline_helpers import write_batch as _write_batch
from tests.alignment_pipeline_helpers import (
    write_partial_batch as _write_partial_batch,
)
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.diagnostics.timing import TimingRecorder


def test_pipeline_records_alignment_timing_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    _patch_owner_pipeline_to_matrix(monkeypatch)
    recorder = TimingRecorder("alignment", run_id="test-alignment")

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
        timing_recorder=recorder,
    )

    stages = [record.stage for record in recorder.records]
    assert stages == [
        "alignment.run_config",
        "alignment.read_batch_index",
        "alignment.read_candidates",
        "alignment.audit_evidence_mode",
        "alignment.open_raw_sources",
        "alignment.build_owners",
        "alignment.cluster_owners",
        "alignment.backfill_scope",
        "alignment.owner_backfill",
        "alignment.build_matrix",
        "alignment.claim_registry",
        "alignment.primary_consolidation",
        "alignment.pre_backfill_recenter",
        "alignment.write_outputs",
    ]
    records_by_stage = {record.stage: record for record in recorder.records}
    assert records_by_stage["alignment.run_config"].elapsed_sec == 0.0
    assert records_by_stage["alignment.run_config"].metrics == {
        "raw_workers": 1,
        "raw_xic_batch_size": 1,
        "owner_build_xic_backend": "raw",
        "owner_backfill_xic_backend": "raw",
        "owner_backfill_window_strategy": "exact",
        "owner_backfill_superwindow_span_factor": 2,
        "preconsolidate_owner_families": False,
        "output_level": "machine",
        "backfill_scope": "full-audit",
        "requested_audit_evidence_mode": "auto",
        "selected_family_count": 0,
        "require_discovery_owner_closure": False,
        "emit_provisional_matrix": False,
        "emit_early_loser_shadow": False,
        "drift_prior_source": "none",
    }
    assert records_by_stage["alignment.audit_evidence_mode"].metrics == {
        "requested_audit_evidence_mode": "auto",
        "audit_evidence_mode": "none",
        "audit_evidence_mode_reason": "no_audit_destination",
        "heavy_audit_enabled": False,
    }
    assert records_by_stage["alignment.read_candidates"].metrics["candidate_count"] == 1
    assert records_by_stage["alignment.open_raw_sources"].metrics["raw_count"] == 1


def test_pipeline_records_alignment_extract_xic_inner_timing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_partial_batch(
        tmp_path,
        sample_order=("Sample_A", "Sample_B"),
        candidate_samples=("Sample_A",),
    )
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    (raw_dir / "Sample_B.raw").write_text("raw", encoding="utf-8")
    monkeypatch.setattr(
        "xic_extractor.alignment.ownership.find_peak_and_area",
        _ok_alignment_peak,
    )
    monkeypatch.setattr(
        "xic_extractor.alignment.owner_backfill.find_peak_and_area",
        _ok_alignment_peak,
    )
    monkeypatch.setattr(
        pipeline_module,
        "_write_outputs_atomic",
        lambda *args, **kwargs: None,
    )
    recorder = TimingRecorder("alignment", run_id="test-inner-timing")

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=TraceRawOpener(),
        timing_recorder=recorder,
    )

    records_by_stage_sample = {
        (record.stage, record.sample_stem): record for record in recorder.records
    }
    build_record = records_by_stage_sample[
        ("alignment.build_owners.extract_xic", "Sample_A")
    ]
    backfill_record = records_by_stage_sample[
        ("alignment.owner_backfill.extract_xic", "Sample_B")
    ]
    expected_metrics = {
        "extract_xic_count": 1,
        "extract_xic_batch_count": 1,
        "raw_chromatogram_call_count": 0,
        "point_count": 3,
    }
    assert build_record.metrics == expected_metrics
    assert backfill_record.metrics == expected_metrics

    owner_backfill_record = records_by_stage_sample[("alignment.owner_backfill", "")]
    assert owner_backfill_record.metrics == {
        "extract_xic_count": 1,
        "extract_xic_batch_count": 1,
        "raw_chromatogram_call_count": 0,
        "point_count": 3,
        "mean_xic_per_raw_chromatogram_call": None,
        "mean_xic_per_extract_batch": 1.0,
    }


def test_timed_raw_source_records_batch_calls() -> None:
    from xic_extractor.xic_models import XICRequest, XICTrace

    class BatchSource:
        def __init__(self) -> None:
            self.raw_chromatogram_call_count = 0

        def extract_xic_many(self, requests):
            self.raw_chromatogram_call_count += 1
            return tuple(
                XICTrace.from_arrays([request.rt_min], [request.mz])
                for request in requests
            )

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            self.raw_chromatogram_call_count += 1
            return [rt_min], [mz]

    stats = pipeline_module._RawSourceTimingStats(
        sample_stem="Sample_A",
        stage="alignment.build_owners.extract_xic",
    )
    source = pipeline_module._TimedRawSource(BatchSource(), stats=stats)

    traces = source.extract_xic_many(
        (
            XICRequest(mz=258.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0),
            XICRequest(mz=259.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0),
        )
    )

    assert [trace.intensity.tolist() for trace in traces] == [[258.0], [259.0]]
    assert stats.extract_xic_count == 2
    assert stats.extract_xic_batch_count == 1
    assert stats.raw_chromatogram_call_count == 1
    assert stats.point_count == 2


def test_timed_raw_source_records_batch_failure_metrics() -> None:
    from xic_extractor.xic_models import XICRequest

    class FailingBatchSource:
        def __init__(self) -> None:
            self.raw_chromatogram_call_count = 0

        def extract_xic_many(self, requests):
            self.raw_chromatogram_call_count += 1
            raise RuntimeError("batch failed")

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("fallback should not be used")

    stats = pipeline_module._RawSourceTimingStats(
        sample_stem="Sample_A",
        stage="alignment.build_owners.extract_xic",
    )
    source = pipeline_module._TimedRawSource(FailingBatchSource(), stats=stats)

    with pytest.raises(RuntimeError, match="batch failed"):
        source.extract_xic_many(
            (
                XICRequest(mz=258.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0),
                XICRequest(mz=259.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0),
            )
        )

    assert stats.extract_xic_count == 2
    assert stats.extract_xic_batch_count == 1
    assert stats.raw_chromatogram_call_count == 1
    assert stats.point_count == 0
    assert stats.elapsed_sec >= 0


def test_timed_raw_source_delegates_scan_window_lookup() -> None:
    from xic_extractor.xic_models import XICRequest

    class WindowSource:
        def scan_window_for_request(self, request):
            return (int(request.rt_min), int(request.rt_max))

    stats = pipeline_module._RawSourceTimingStats(
        sample_stem="Sample_A",
        stage="alignment.build_owners.extract_xic",
    )
    source = pipeline_module._TimedRawSource(WindowSource(), stats=stats)

    window = source.scan_window_for_request(
        XICRequest(mz=258.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0)
    )

    assert window == (8, 9)


class TraceRawOpener:
    def __call__(self, raw_path: Path, dll_dir: Path):
        return TraceRawContext(raw_path)


class TraceRawContext:
    def __init__(self, raw_path: Path) -> None:
        self.raw_path = raw_path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
        return np.asarray([7.9, 8.0, 8.1]), np.asarray([10.0, 100.0, 10.0])


def _ok_alignment_peak(*args, **kwargs):
    peak = SimpleNamespace(
        rt=8.0,
        intensity=100.0,
        area=25.0,
        peak_start=7.9,
        peak_end=8.1,
    )
    return SimpleNamespace(status="OK", peak=peak)
