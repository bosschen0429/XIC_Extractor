from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from tools.diagnostics import pt_pattern_trace_diagnosis as diag
from xic_extractor.discovery import (
    pt_pattern_anchor_segmentation as anchor_segmentation,
)
from xic_extractor.discovery import pt_pattern_review_packet as review_packet
from xic_extractor.discovery import pt_pattern_trace_evidence as evidence
from xic_extractor.discovery.pt_pattern_anchor_segmentation import (
    select_anchor_local_segments,
)
from xic_extractor.peak_detection.chrom_peak_segments import (
    ChromPeakSegment,
    ChromPeakSegmentEnumeration,
)
from xic_extractor.peak_detection.selected_envelope import TraceInterval
from xic_extractor.tabular_io import read_delimited_rows, write_delimited_rows
from xic_extractor.xic_models import XICTrace


def _candidate_row() -> dict[str, str]:
    return {
        "sample_stem": "SampleA",
        "candidate_id": "SampleA|pt|500.0000|10.0000",
        "base_mz": "500.0",
        "base_rt_apex_min": "10.0",
        "pt_plus_1_mz": "501.0020",
        "pt_plus_1_expected_mz": "501.0021",
        "pt_plus_2_mz": "502.0021",
        "pt_plus_2_expected_mz": "502.0023",
        "pt_plus_4_mz": "504.0050",
        "pt_plus_4_expected_mz": "504.0052",
    }


def _trace(intensities: list[float]) -> XICTrace:
    return XICTrace.from_arrays(
        np.array([9.98, 10.00, 10.02], dtype=float),
        np.array(intensities, dtype=float),
    )


def _trace_with_rt(rt: list[float], intensities: list[float]) -> XICTrace:
    return XICTrace.from_arrays(
        np.array(rt, dtype=float),
        np.array(intensities, dtype=float),
    )


def test_build_trace_requests_uses_expected_mz_and_base_rt_window() -> None:
    requests = evidence.build_trace_requests_for_candidate(
        _candidate_row(),
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
    )

    assert [item.position_label for item in requests] == [
        "base",
        "pt_plus_1",
        "pt_plus_2",
        "pt_plus_4",
    ]
    assert requests[0].request.mz == 500.0
    assert requests[1].request.mz == 501.0021
    assert requests[0].request.rt_min == 9.5
    assert requests[0].request.rt_max == 10.5
    assert all(item.request.ppm_tol == 20.0 for item in requests)


def test_load_candidate_rows_rejects_duplicate_candidate_ids_before_slicing(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    duplicate = _candidate_row()
    duplicate["base_mz"] = "600.0"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row(), duplicate],
        evidence.REQUIRED_CANDIDATE_COLUMNS,
    )

    with pytest.raises(ValueError, match="duplicate candidate_id"):
        evidence.load_candidate_rows(candidates_csv, limit=1)


def test_diagnose_candidate_traces_marks_coeluting_family_supported() -> None:
    traces = {
        "base": _trace([0.0, 100.0, 0.0]),
        "pt_plus_1": _trace([0.0, 40.0, 0.0]),
        "pt_plus_2": _trace([0.0, 20.0, 0.0]),
        "pt_plus_4": _trace([0.0, 10.0, 0.0]),
    }

    diagnosis = evidence.diagnose_candidate_traces(
        _candidate_row(),
        traces,
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=30.0,
        include_trace_points=True,
    )

    assert diagnosis.summary_row["trace_support_status"] == "supported"
    assert (
        diagnosis.summary_row["combined_pt_pattern_status"]
        == "pt_pattern_detected_diagnostic"
    )
    assert diagnosis.summary_row["scan_pattern_seed_status"] == "full_pattern_seeded"
    assert (
        diagnosis.summary_row["trace_decision_basis"] == evidence.TRACE_DECISION_BASIS
    )
    assert diagnosis.summary_row["trace_smoothing_method"] == "gaussian15"
    assert math.isclose(diagnosis.summary_row["min_median_rt_step_sec"], 1.2)
    assert diagnosis.summary_row["non_base_positions_supported"] == 3
    assert diagnosis.summary_row["max_abs_apex_delta_sec"] == 0.0
    assert math.isclose(
        diagnosis.summary_row["min_pearson_r_to_base"],
        1.0,
    )
    assert len(diagnosis.metric_rows) == 4
    base_metric = next(
        row for row in diagnosis.metric_rows if row["position_label"] == "base"
    )
    plus_4_metric = next(
        row for row in diagnosis.metric_rows if row["position_label"] == "pt_plus_4"
    )
    assert base_metric["raw_max_intensity"] == 100.0
    assert float(base_metric["max_intensity"]) < 100.0
    assert "plus4_window_tail_dominance" not in plus_4_metric["trace_quality_flags"]
    assert len(diagnosis.trace_point_rows) == 12


def test_diagnose_candidate_traces_flags_plus4_window_tail_dominance() -> None:
    rt = [9.5 + index * 0.01 for index in range(201)]
    peak = [math.exp(-0.5 * ((rt_min - 10.0) / 0.08) ** 2) * 100.0 for rt_min in rt]
    plus4_tail = [
        max(
            math.exp(-0.5 * ((rt_min - 9.95) / 0.07) ** 2) * 70.0,
            math.exp(-max(rt_min - 9.95, 0.0) / 0.85) * 70.0 if rt_min >= 9.95 else 0.0,
        )
        for rt_min in rt
    ]
    traces = {
        "base": _trace_with_rt(rt, peak),
        "pt_plus_1": _trace_with_rt(rt, [value * 0.40 for value in peak]),
        "pt_plus_2": _trace_with_rt(rt, [value * 0.30 for value in peak]),
        "pt_plus_4": _trace_with_rt(rt, plus4_tail),
    }

    diagnosis = evidence.diagnose_candidate_traces(
        _candidate_row(),
        traces,
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=30.0,
    )

    plus_4_metric = next(
        row for row in diagnosis.metric_rows if row["position_label"] == "pt_plus_4"
    )

    assert plus_4_metric["trace_support_status"] == "supported"
    assert "plus4_window_tail_dominance" in plus_4_metric["trace_quality_flags"]
    assert diagnosis.summary_row["trace_support_status"] == "supported"
    assert (
        diagnosis.summary_row["combined_pt_pattern_status"]
        == "pt_pattern_detected_diagnostic"
    )
    assert diagnosis.summary_row["trace_ambiguity_status"] == "review_required"
    assert (
        "plus4_window_tail_dominance" in diagnosis.summary_row["trace_ambiguity_flags"]
    )


def test_diagnose_candidate_traces_supports_shifted_shape() -> None:
    traces = {
        "base": _trace([0.0, 100.0, 0.0]),
        "pt_plus_1": _trace([40.0, 0.0, 0.0]),
        "pt_plus_2": _trace([0.0, 20.0, 0.0]),
        "pt_plus_4": _trace([0.0, 10.0, 0.0]),
    }

    diagnosis = evidence.diagnose_candidate_traces(
        _candidate_row(),
        traces,
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=30.0,
    )

    plus_1_metric = next(
        row for row in diagnosis.metric_rows if row["position_label"] == "pt_plus_1"
    )
    assert plus_1_metric["trace_support_status"] == "supported"
    assert plus_1_metric["reason"] == "shifted_shape_supported"
    assert "shifted_only_support" in plus_1_metric["trace_quality_flags"]
    assert "low_scan_density" in plus_1_metric["trace_quality_flags"]
    assert math.isclose(plus_1_metric["shifted_pearson_r_to_base"], 1.0)
    assert math.isclose(plus_1_metric["shape_shift_sec_to_base"], 1.2)
    assert diagnosis.summary_row["trace_support_status"] == "supported"
    assert diagnosis.summary_row["trace_ambiguity_status"] == "review_required"
    assert (
        "shifted_only_support_present" in diagnosis.summary_row["trace_ambiguity_flags"]
    )
    assert (
        diagnosis.summary_row["combined_pt_pattern_status"]
        == "pt_pattern_detected_diagnostic"
    )
    assert math.isclose(diagnosis.summary_row["min_shifted_pearson_r_to_base"], 1.0)
    assert diagnosis.summary_row["non_base_positions_supported"] == 3


def test_diagnose_candidate_traces_requires_shifted_apex_within_tolerance() -> None:
    traces = {
        "base": _trace([0.0, 100.0, 0.0]),
        "pt_plus_1": _trace([40.0, 0.0, 0.0]),
        "pt_plus_2": _trace([0.0, 20.0, 0.0]),
        "pt_plus_4": _trace([0.0, 10.0, 0.0]),
    }

    diagnosis = evidence.diagnose_candidate_traces(
        _candidate_row(),
        traces,
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=1.0,
    )

    plus_1_metric = next(
        row for row in diagnosis.metric_rows if row["position_label"] == "pt_plus_1"
    )
    assert plus_1_metric["trace_support_status"] == "review"
    assert plus_1_metric["reason"] == "apex_rt_outside_threshold"
    assert diagnosis.summary_row["non_base_positions_supported"] == 2
    assert diagnosis.summary_row["trace_support_status"] == "partial_support"
    assert (
        diagnosis.summary_row["combined_pt_pattern_status"]
        == "pt_pattern_candidate_partial"
    )


@pytest.mark.parametrize(
    ("pearson_r", "active_overlap_fraction", "shifted_pearson_r"),
    [
        (1.0, None, None),
        (None, 1.0, None),
        (None, None, 1.0),
    ],
)
def test_position_support_requires_apex_within_tolerance_before_shape(
    pearson_r: float | None,
    active_overlap_fraction: float | None,
    shifted_pearson_r: float | None,
) -> None:
    status, reason = evidence._position_support_status(
        point_count=31,
        max_intensity=40.0,
        base_max_intensity=100.0,
        apex_delta_sec=31.0,
        pearson_r=pearson_r,
        active_overlap_fraction=active_overlap_fraction,
        shifted_pearson_r=shifted_pearson_r,
        min_pearson_r=0.7,
        max_apex_delta_sec=30.0,
    )

    assert status == "review"
    assert reason == "apex_rt_outside_threshold"


def test_diagnose_candidate_traces_flags_short_effective_smoothing_window() -> None:
    rt = [9.985 + index * 0.001 for index in range(31)]
    peak = [math.exp(-0.5 * ((index - 15) / 4.0) ** 2) * 100.0 for index in range(31)]
    traces = {
        "base": _trace_with_rt(rt, peak),
        "pt_plus_1": _trace_with_rt(rt, [value * 0.40 for value in peak]),
        "pt_plus_2": _trace_with_rt(rt, [value * 0.20 for value in peak]),
        "pt_plus_4": _trace_with_rt(rt, [value * 0.10 for value in peak]),
    }

    diagnosis = evidence.diagnose_candidate_traces(
        _candidate_row(),
        traces,
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=30.0,
    )

    plus_1_metric = next(
        row for row in diagnosis.metric_rows if row["position_label"] == "pt_plus_1"
    )

    assert math.isclose(plus_1_metric["median_rt_step_sec"], 0.06)
    assert math.isclose(plus_1_metric["effective_smoothing_window_sec"], 0.9)
    assert plus_1_metric["trace_support_status"] == "supported"
    assert "short_effective_smoothing_window" in plus_1_metric["trace_quality_flags"]
    assert diagnosis.summary_row["trace_support_status"] == "supported"
    assert diagnosis.summary_row["trace_ambiguity_status"] == "review_required"
    assert (
        "short_effective_smoothing_window"
        in diagnosis.summary_row["trace_ambiguity_flags"]
    )
    assert math.isclose(
        diagnosis.summary_row["min_effective_smoothing_window_sec"],
        0.9,
    )
    assert diagnosis.summary_row["max_trace_point_count"] == 31


def test_combined_status_mapping_stays_diagnostic_not_product_authority() -> None:
    assert (
        evidence.combined_pt_pattern_status("supported")
        == "pt_pattern_detected_diagnostic"
    )
    assert (
        evidence.combined_pt_pattern_status("partial_support")
        == "pt_pattern_candidate_partial"
    )
    assert (
        evidence.combined_pt_pattern_status("incomplete_trace_signal")
        == "pt_pattern_candidate_incomplete"
    )
    assert (
        evidence.combined_pt_pattern_status("base_missing_signal")
        == "pt_pattern_candidate_base_missing"
    )
    assert (
        evidence.combined_pt_pattern_status("review") == "pt_pattern_candidate_review"
    )


def test_run_trace_diagnosis_writes_summary_metrics_and_optional_points(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row()],
        evidence.REQUIRED_CANDIDATE_COLUMNS,
    )

    class FakeRaw:
        def __init__(self) -> None:
            self.requests = ()

        def __enter__(self) -> "FakeRaw":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def extract_xic_many(self, requests):
            self.requests = requests
            rt = np.linspace(9.9, 10.1, 31)
            peak = np.exp(-0.5 * ((rt - 10.0) / 0.025) ** 2)
            return tuple(
                XICTrace.from_arrays(
                    rt,
                    peak * (100.0 if index == 0 else 25.0),
                )
                for index, _request in enumerate(requests)
            )

    fake_raw = FakeRaw()
    monkeypatch.setattr(diag, "open_raw", lambda raw, dll_dir: fake_raw)
    raw_path = tmp_path / "SampleA.raw"
    raw_path.write_bytes(b"sample-a-raw")

    outputs = diag.run_trace_diagnosis(
        candidates_csv=candidates_csv,
        raw=raw_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "trace_diagnosis",
        limit=1,
        offset=0,
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=30.0,
        write_trace_points=True,
    )

    summary_rows = read_delimited_rows(
        outputs["summary_csv"],
        required_columns=(
            "candidate_id",
            "scan_pattern_seed_status",
            "trace_support_status",
            "combined_pt_pattern_status",
        ),
        delimiter=",",
    )
    metric_rows = read_delimited_rows(
        outputs["metrics_csv"],
        required_columns=(
            "position_label",
            "pearson_r_to_base",
        ),
        delimiter=",",
    )
    trace_point_rows = read_delimited_rows(
        outputs["trace_points_csv"],
        required_columns=("position_label", "intensity_basis", "rt_min", "intensity"),
        delimiter=",",
    )

    anchor_summary_rows = read_delimited_rows(
        outputs["anchor_summary_csv"],
        required_columns=(
            "anchor_segmentation_boundary_review_status",
            "anchor_segmentation_common_end_owner_labels",
        ),
        delimiter=",",
    )
    anchor_metric_rows = read_delimited_rows(
        outputs["anchor_metrics_csv"],
        required_columns=("anchor_segment_class", "anchor_segment_right_clip_sec"),
        delimiter=",",
    )
    anchor_trace_point_rows = read_delimited_rows(
        outputs["anchor_trace_points_csv"],
        required_columns=("position_label", "intensity_basis", "rt_min", "intensity"),
        delimiter=",",
    )

    assert len(fake_raw.requests) == 4
    assert summary_rows[0]["trace_support_status"] == "supported"
    assert (
        summary_rows[0]["combined_pt_pattern_status"]
        == "pt_pattern_detected_diagnostic"
    )
    assert len(metric_rows) == 4
    assert len(anchor_metric_rows) == 4
    assert len(anchor_trace_point_rows) < len(trace_point_rows)
    assert len(trace_point_rows) == 124
    assert trace_point_rows[0]["intensity_basis"] == evidence.RAW_TRACE_BASIS
    assert summary_rows[0]["schema_version"] == evidence.SCHEMA_VERSION
    assert "anchor_segmentation_status" not in summary_rows[0]
    assert (
        anchor_summary_rows[0]["schema_version"]
        == "pt_pattern_anchor_local_trace_diagnosis.v1"
    )
    assert outputs["sidecar_json"].is_file()

    sidecar = json.loads(outputs["sidecar_json"].read_text(encoding="utf-8"))
    assert sidecar["schema_version"] == evidence.SCHEMA_VERSION
    assert sidecar["readiness_tier"] == evidence.READINESS_TIER
    assert sidecar["evidence_owner"] == evidence.EVIDENCE_OWNER
    assert sidecar["diagnostic_only"] is True
    assert sidecar["trace_request_count"] == 4
    assert sidecar["source_sample_stem"] == "SampleA"
    assert sidecar["raw_sha256"]
    assert sidecar["trace_decision_basis"] == evidence.TRACE_DECISION_BASIS
    assert sidecar["trace_smoothing_method"] == evidence.TRACE_SMOOTH_METHOD
    assert sidecar["combined_pt_pattern_status_counts"] == {
        "pt_pattern_detected_diagnostic": 1,
    }
    anchor_sidecar = json.loads(
        outputs["anchor_sidecar_json"].read_text(encoding="utf-8")
    )
    assert anchor_sidecar["schema_version"] == (
        "pt_pattern_anchor_local_trace_diagnosis.v1"
    )
    assert anchor_sidecar["anchor_interval_semantics"] == (
        "coelution_comparison_not_integration"
    )
    assert anchor_sidecar["anchor_segmentation_status_counts"] == {"clear": 1}
    assert anchor_sidecar["anchor_boundary_review_status_counts"] == {
        anchor_summary_rows[0]["anchor_segmentation_boundary_review_status"]: 1,
    }


def test_trace_summary_identity_round_trips_into_review_packet(
    tmp_path: Path,
    monkeypatch,
) -> None:
    base_mz = 500.123456789
    base_rt = 10.123456789
    candidate = _candidate_row()
    candidate.update(
        {
            "candidate_id": f"SampleA|pt|{base_mz!r}|{base_rt!r}",
            "method_label": "pt_pattern",
            "candidate_state": "pt_pattern_candidate",
            "review_priority": "HIGH",
            "evidence_tier": "A",
            "evidence_score": "90",
            "base_mz": repr(base_mz),
            "base_rt_apex_min": repr(base_rt),
            "cid_nl_base_status": "not_required",
            "cid_nl_base_join_mode": "not_required",
        }
    )
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    write_delimited_rows(
        candidates_csv,
        [candidate],
        tuple(candidate),
    )

    class FakeRaw:
        def __enter__(self) -> "FakeRaw":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def extract_xic_many(self, requests):
            rt = np.linspace(base_rt - 0.1, base_rt + 0.1, 31)
            peak = np.exp(-0.5 * ((rt - base_rt) / 0.025) ** 2)
            return tuple(
                XICTrace.from_arrays(
                    rt,
                    peak * (100.0 if index == 0 else 25.0),
                )
                for index, _request in enumerate(requests)
            )

    monkeypatch.setattr(diag, "open_raw", lambda raw, dll_dir: FakeRaw())
    raw_path = tmp_path / "SampleA.raw"
    raw_path.write_bytes(b"sample-a-raw")
    trace_outputs = diag.run_trace_diagnosis(
        candidates_csv=candidates_csv,
        raw=raw_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "trace_diagnosis",
        limit=1,
        offset=0,
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=30.0,
        write_trace_points=False,
    )

    packet_outputs = review_packet.run_review_packet(
        candidates_csv=candidates_csv,
        trace_summary_csv=trace_outputs["anchor_summary_csv"],
        trace_metrics_csv=trace_outputs["anchor_metrics_csv"],
        output_dir=tmp_path / "review_packet",
        packet_label="identity-round-trip",
        source_label="pt_only",
        max_per_status=1,
    )
    packet_rows = read_delimited_rows(
        packet_outputs["packet_csv"],
        required_columns=review_packet.PACKET_COLUMNS,
        delimiter=",",
    )

    assert packet_rows[0]["base_mz"] == repr(base_mz)
    assert packet_rows[0]["base_rt_apex_min"] == repr(base_rt)


def test_run_trace_diagnosis_uses_local_hypothesis_and_preserves_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    row = _candidate_row()
    row["candidate_id"] = "SampleA|pt|500.0000|10.3000"
    row["base_rt_apex_min"] = "10.3"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    write_delimited_rows(
        candidates_csv,
        [row],
        evidence.REQUIRED_CANDIDATE_COLUMNS,
    )
    rt = np.linspace(9.6, 11.4, 181)

    def gaussian(center: float, width: float, height: float) -> np.ndarray:
        return height * np.exp(-0.5 * ((rt - center) / width) ** 2)

    shared = gaussian(center=10.3, width=0.055, height=100.0)
    traces = (
        XICTrace.from_arrays(
            rt,
            shared + gaussian(center=11.0, width=0.065, height=180.0),
        ),
        XICTrace.from_arrays(rt, shared * 1.15),
        XICTrace.from_arrays(rt, shared * 0.95),
        XICTrace.from_arrays(rt, shared * 0.38),
    )

    class FakeRaw:
        def __enter__(self) -> "FakeRaw":
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def extract_xic_many(self, requests):
            assert len(requests) == 4
            return traces

    monkeypatch.setattr(diag, "open_raw", lambda raw, dll_dir: FakeRaw())
    raw_path = tmp_path / "SampleA.raw"
    raw_path.write_bytes(b"sample-a-raw")
    outputs = diag.run_trace_diagnosis(
        candidates_csv=candidates_csv,
        raw=raw_path,
        dll_dir=tmp_path,
        output_dir=tmp_path / "trace_diagnosis",
        limit=1,
        offset=0,
        ppm=20.0,
        rt_half_window_sec=90.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=3.0,
        write_trace_points=True,
    )

    local_rows = read_delimited_rows(
        outputs["anchor_trace_points_csv"],
        required_columns=("position_label", "rt_min"),
        delimiter=",",
    )
    context_rows = read_delimited_rows(
        outputs["trace_points_csv"],
        required_columns=("position_label", "rt_min"),
        delimiter=",",
    )
    local_base_rt = [
        float(item["rt_min"]) for item in local_rows if item["position_label"] == "base"
    ]
    context_base_rt = [
        float(item["rt_min"])
        for item in context_rows
        if item["position_label"] == "base"
    ]

    assert max(local_base_rt) < 10.7
    assert max(context_base_rt) == 11.4
    summary = read_delimited_rows(
        outputs["anchor_summary_csv"],
        required_columns=("trace_support_status", "max_abs_apex_delta_sec"),
        delimiter=",",
    )[0]
    assert summary["trace_support_status"] == "supported"
    assert float(summary["max_abs_apex_delta_sec"]) == 0.0


def test_run_trace_diagnosis_rejects_candidate_raw_sample_mismatch(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row()],
        evidence.REQUIRED_CANDIDATE_COLUMNS,
    )

    with pytest.raises(ValueError, match="source sample 'SampleB'"):
        diag.run_trace_diagnosis(
            candidates_csv=candidates_csv,
            raw=tmp_path / "SampleB.raw",
            dll_dir=tmp_path,
            output_dir=tmp_path / "trace_diagnosis",
            limit=1,
            offset=0,
            ppm=20.0,
            rt_half_window_sec=30.0,
            mz_source="expected",
            active_fraction=0.1,
            min_pearson_r=0.7,
            max_apex_delta_sec=30.0,
            write_trace_points=False,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"limit": 0},
        {"offset": -1},
        {"ppm": float("inf")},
        {"rt_half_window_sec": float("nan")},
        {"mz_source": "unknown"},
        {"active_fraction": float("nan")},
        {"min_pearson_r": float("inf")},
        {"max_apex_delta_sec": float("inf")},
        {"ppm": True},
        {"rt_half_window_sec": "30"},
    ],
)
def test_run_trace_diagnosis_rejects_invalid_settings_before_io(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    kwargs: dict[str, object] = {
        "candidates_csv": tmp_path / "missing.csv",
        "raw": tmp_path / "missing.raw",
        "dll_dir": tmp_path,
        "output_dir": tmp_path / "trace_diagnosis",
        "limit": 1,
        "offset": 0,
        "ppm": 20.0,
        "rt_half_window_sec": 30.0,
        "mz_source": "expected",
        "active_fraction": 0.1,
        "min_pearson_r": 0.7,
        "max_apex_delta_sec": 30.0,
        "write_trace_points": False,
    }
    kwargs.update(overrides)

    with pytest.raises(ValueError):
        diag.run_trace_diagnosis(**kwargs)


def test_anchor_segmentation_fails_closed_on_ambiguous_segment(
    monkeypatch,
) -> None:
    traces = {label: _trace([0.0, 100.0, 0.0]) for label in evidence.POSITION_LABELS}

    def duplicate_match(segments, anchor_rt_min):
        del anchor_rt_min
        return (segments[0], segments[0])

    monkeypatch.setattr(
        anchor_segmentation,
        "segments_containing_rt",
        duplicate_match,
    )

    segmentation = select_anchor_local_segments(traces, anchor_rt_min=10.0)

    assert segmentation.status == "ambiguous_anchor_segment"
    assert segmentation.trace_by_position == {}
    assert all(
        selection.matching_segment_count == 2
        for selection in segmentation.position_selections
    )


def test_anchor_segmentation_excludes_later_stronger_base_peak() -> None:
    rt = np.linspace(9.6, 11.4, 181)

    def gaussian(center: float, width: float, height: float) -> np.ndarray:
        return height * np.exp(-0.5 * ((rt - center) / width) ** 2)

    shared = gaussian(center=10.3, width=0.055, height=100.0)
    competing_base = gaussian(center=11.0, width=0.065, height=180.0)
    traces = {
        "base": XICTrace.from_arrays(rt, shared + competing_base),
        "pt_plus_1": XICTrace.from_arrays(rt, shared * 1.15),
        "pt_plus_2": XICTrace.from_arrays(rt, shared * 0.95),
        "pt_plus_4": XICTrace.from_arrays(rt, shared * 0.38),
    }

    segmentation = select_anchor_local_segments(traces, anchor_rt_min=10.3)

    assert segmentation.status == "clear"
    assert segmentation.common_rt_start_min is not None
    assert segmentation.common_rt_end_min is not None
    assert segmentation.common_rt_start_min < 10.3 < segmentation.common_rt_end_min
    localized_base = segmentation.trace_by_position["base"]
    assert float(localized_base.rt[np.argmax(localized_base.intensity)]) < 10.5

    diagnosis = evidence.diagnose_candidate_traces(
        _candidate_row(),
        segmentation.trace_by_position,
        ppm=20.0,
        rt_half_window_sec=90.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=3.0,
    )
    assert diagnosis.summary_row["max_abs_apex_delta_sec"] == 0.0
    assert math.isclose(
        float(diagnosis.summary_row["min_pearson_r_to_base"]),
        1.0,
        rel_tol=1e-4,
    )


def test_anchor_segmentation_externalizes_right_boundary_truncation(
    monkeypatch,
) -> None:
    rt = np.arange(11, dtype=float)
    morphology = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0, 0.0)
    traces = {
        label: XICTrace.from_arrays(rt, np.asarray(morphology, dtype=float) * 10.0)
        for label in evidence.POSITION_LABELS
    }
    call_index = 0

    def enumeration(*args, **kwargs):
        nonlocal call_index
        del args, kwargs
        position_label = evidence.POSITION_LABELS[call_index]
        call_index += 1
        rt_start = 1.0 if position_label == "pt_plus_1" else 2.0
        start_index = int(rt_start)
        rt_end = 7.0 if position_label == "pt_plus_4" else 9.0
        end_index = int(rt_end) + 1
        segment = ChromPeakSegment(
            segment_id=f"segment:{position_label}",
            interval=TraceInterval(
                start_index=start_index,
                end_index=end_index,
                rt_start_min=rt_start,
                rt_end_min=rt_end,
                scan_count=end_index - start_index,
            ),
            apex_index=5,
            apex_rt_min=5.0,
            raw_apex_residual=50.0,
            morphology_apex_residual=5.0,
            area_baseline_corrected=1.0,
            morphology_area_shadow=1.0,
            segment_class=(
                "shoulder_candidate"
                if position_label in {"pt_plus_1", "pt_plus_4"}
                else "isolated_peak"
            ),
            boundary_stop_reason=(
                "shoulder_overlap_review"
                if position_label in {"pt_plus_1", "pt_plus_4"}
                else "baseline_return"
            ),
            evidence_sources=("unit_fixture",),
        )
        return ChromPeakSegmentEnumeration(
            status="OK",
            segments=(segment,),
            n_points=len(rt),
            morphology_trace_method="gaussian_15",
            morphology_trace_window_points=15,
            morphology_trace_effective_points=11,
            morphology_trace_values=morphology,
        )

    monkeypatch.setattr(
        anchor_segmentation,
        "enumerate_chrom_peak_segments",
        enumeration,
    )

    segmentation = select_anchor_local_segments(traces, anchor_rt_min=5.0)

    assert segmentation.status == "clear"
    assert segmentation.boundary_review_status == "review_required"
    assert segmentation.boundary_review_position_labels == ("pt_plus_4",)
    by_position = {
        item.position_label: item for item in segmentation.boundary_evidence
    }
    assert by_position["pt_plus_4"].owns_common_end is True
    assert by_position["base"].right_clip_sec == 120.0
    assert math.isclose(
        by_position["base"].common_right_residual_fraction or 0.0,
        0.6,
    )
    assert by_position["base"].common_right_trend == "descending"
    assert by_position["pt_plus_1"].segment_class == "shoulder_candidate"
    assert by_position["pt_plus_1"].owns_common_start is False
    assert by_position["pt_plus_1"].owns_common_end is False
    assert by_position["pt_plus_4"].segment_class == "shoulder_candidate"
    assert (
        by_position["pt_plus_4"].boundary_stop_reason
        == "shoulder_overlap_review"
    )
