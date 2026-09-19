import csv
from pathlib import Path

import numpy as np

import xic_extractor.extraction.peak_candidate_boundaries as boundary_rows
from xic_extractor.extraction.peak_candidate_boundaries import (
    PEAK_CANDIDATE_BOUNDARY_HEADERS,
    _apply_boundary_nonoverlap_selection,
    build_peak_candidate_boundary_rows,
    build_peak_candidate_boundary_rows_from_hypotheses,
)
from xic_extractor.extraction.peak_candidate_boundary_summary import (
    build_peak_candidate_boundary_summary_rows,
)
from xic_extractor.output.peak_candidate_boundaries import (
    write_peak_candidate_boundaries_tsv,
)
from xic_extractor.output.peak_candidate_boundary_summary import (
    write_peak_candidate_boundary_summary_tsv,
)
from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.traces import Trace, targeted_trace_group
from xic_extractor.signal_processing import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)


def test_build_boundary_rows_emits_alternatives_for_each_candidate() -> None:
    selected = _candidate(8.30, left=8.00, right=8.60)
    rejected = _candidate(8.80, left=8.50, right=9.00, sources=("local_minimum",))
    peak_result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=11,
        max_smoothed=100.0,
        n_prominent_peaks=2,
        candidates=(selected, rejected),
    )
    rt = np.asarray([8.0, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 9.0])
    intensity = np.asarray(
        [10.0, 18.0, 70.0, 100.0, 70.0, 18.0, 10.0, 20.0, 80.0, 20.0, 10.0]
    )

    rows = build_peak_candidate_boundary_rows(
        sample_name="SampleA",
        target_label="Analyte",
        target_mz=258.1085,
        role="Analyte",
        istd_pair="ISTD",
        resolver_mode="region_first_safe_merge",
        peak_result=peak_result,
        rt=rt,
        intensity=intensity,
        group="Tumor",
    )

    assert {row["target_label"] for row in rows} == {"Analyte"}
    assert {row["target_mz"] for row in rows} == {"258.10850"}
    assert {row["resolver_mode"] for row in rows} == {"region_first_safe_merge"}
    assert {row["analysis_mode"] for row in rows} == {"targeted"}
    assert {row["selected_candidate"] for row in rows} == {"TRUE", "FALSE"}
    assert {row["candidate_id"] for row in rows} == {
        "SampleA|Analyte|region_first_safe_merge|legacy_savgol|8.30000|8.00000|8.60000",
        "SampleA|Analyte|region_first_safe_merge|local_minimum|8.80000|8.50000|9.00000",
    }
    selected_rows = [row for row in rows if row["selected_candidate"] == "TRUE"]
    assert any("candidate_interval" in row["boundary_sources"] for row in selected_rows)
    assert any("half_height" in row["boundary_sources"] for row in selected_rows)
    assert any(
        "derivative_zero_crossing" in row["boundary_sources"]
        for row in selected_rows
    )
    assert all(row["boundary_id"].startswith(row["candidate_id"]) for row in rows)
    assert all(row["area_delta_vs_candidate_interval"] != "" for row in rows)
    assert all(row["area_baseline_corrected"] != "" for row in rows)
    assert all(row["area_uncertainty"] != "" for row in rows)
    assert {row["area_uncertainty_formula_version"] for row in rows} == {
        "baseline_residual_mad"
    }
    assert all(row["baseline_residual_mad"] != "" for row in rows)
    assert all(row["area_uncertainty_noise_source"] != "" for row in rows)
    assert {row["baseline_type"] for row in rows} == {"asls"}
    assert all(row["baseline_score"] != "" for row in rows)
    assert all(row["boundary_audit_score"] != "" for row in rows)
    assert all(row["boundary_audit_rank"] != "" for row in rows)
    top_by_candidate = {
        row["candidate_id"]: 0
        for row in rows
    }
    for row in rows:
        if row["boundary_audit_top"] == "TRUE":
            top_by_candidate[row["candidate_id"]] += 1
    assert set(top_by_candidate.values()) == {1}
    assert all(row["boundary_nonoverlap_selected"] != "" for row in rows)
    assert any(
        row["boundary_nonoverlap_selected"] == "TRUE"
        and row["boundary_nonoverlap_note"] == "selected_nonoverlap"
        for row in rows
    )
    assert any(
        "trace_continuity_ok" in row["boundary_support_labels"]
        for row in rows
    )
    assert any(row["boundary_concern_labels"] != "" for row in rows)


def test_source_only_cwt_width_boundary_rows_are_marked_legacy_audit() -> None:
    candidate = _candidate(
        8.30,
        left=8.00,
        right=8.60,
        sources=("legacy_savgol", "centwave_cwt"),
        cwt_best_scale=3.0,
    )
    rows = build_peak_candidate_boundary_rows(
        sample_name="SampleA",
        target_label="Analyte",
        target_mz=258.1085,
        role="Analyte",
        istd_pair="ISTD",
        resolver_mode="region_first_safe_merge",
        peak_result=PeakDetectionResult(
            status="OK",
            peak=candidate.peak,
            n_points=11,
            max_smoothed=100.0,
            n_prominent_peaks=1,
            candidates=(candidate,),
        ),
        rt=np.asarray([8.0, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 9.0]),
        intensity=np.asarray(
            [10.0, 18.0, 70.0, 100.0, 70.0, 18.0, 10.0, 10.0, 10.0, 10.0, 10.0]
        ),
    )

    cwt_rows = [row for row in rows if row["boundary_sources"] == "cwt_width"]
    non_cwt_rows = [row for row in rows if row["boundary_sources"] != "cwt_width"]
    assert cwt_rows
    assert {row["cwt_audit_filter_reason"] for row in cwt_rows} == {
        "legacy_cwt_width_not_real_cwt"
    }
    assert all(row["cwt_audit_filter_reason"] == "" for row in non_cwt_rows)


def test_build_boundary_rows_prefers_shared_trace_group_arrays() -> None:
    selected = _candidate(8.30, left=8.00, right=8.60)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=7,
        max_smoothed=100.0,
        n_prominent_peaks=1,
        candidates=(selected,),
    )
    trace = Trace.from_arrays(
        sample_name="SampleA",
        mz=258.1085,
        rt=[8.0, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6],
        intensity=[10.0, 18.0, 70.0, 100.0, 70.0, 18.0, 10.0],
        rt_min=8.0,
        rt_max=8.6,
        ppm_tol=20.0,
    )

    rows = build_peak_candidate_boundary_rows(
        sample_name="SampleA",
        target_label="Analyte",
        target_mz=258.1085,
        role="Analyte",
        istd_pair="ISTD",
        resolver_mode="region_first_safe_merge",
        peak_result=peak_result,
        rt=np.asarray([8.0, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6]),
        intensity=np.zeros(7),
        trace_group=targeted_trace_group(
            trace,
            target_label="Analyte",
            resolver_mode="region_first_safe_merge",
        ),
    )

    candidate_row = next(
        row for row in rows if row["is_candidate_interval"] == "TRUE"
    )
    assert candidate_row["area_raw_counts_seconds"] != "0.00"
    assert candidate_row["baseline_score"] != "0.00000"


def test_build_boundary_rows_from_hypotheses_without_legacy_result() -> None:
    hypothesis = PeakHypothesis(
        hypothesis_id="hypothesis-boundary-row-id",
        trace_group_id="SampleA|HypothesisTarget|hypothesis_resolver",
        target_label="HypothesisTarget",
        role="ISTD",
        istd_pair="HypothesisPair",
        analysis_mode="targeted",
        resolver_mode="hypothesis_resolver",
        integration=IntegrationResult(
            rt_left_min=8.00,
            rt_apex_min=8.30,
            rt_right_min=8.60,
            raw_apex_rt_min=8.30,
            rt_width_min=0.60,
            height_raw=100.0,
            height_smoothed=95.0,
            area_raw_counts_seconds=1200.0,
        ),
        evidence=EvidenceVector(cwt_best_scale=3.0),
        audit=AuditTrail(
            proposal_sources=("hypothesis_source",),
            selected=True,
        ),
    )

    rows = build_peak_candidate_boundary_rows_from_hypotheses(
        sample_name="SampleA",
        group="Tumor",
        target_mz=258.1085,
        hypotheses=(hypothesis,),
        rt=np.asarray([8.0, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6]),
        intensity=np.asarray([10.0, 18.0, 70.0, 100.0, 70.0, 18.0, 10.0]),
    )

    assert rows
    assert {row["candidate_id"] for row in rows} == {"hypothesis-boundary-row-id"}
    assert {row["group"] for row in rows} == {"Tumor"}
    assert {row["target_label"] for row in rows} == {"HypothesisTarget"}
    assert {row["target_mz"] for row in rows} == {"258.10850"}
    assert {row["role"] for row in rows} == {"ISTD"}
    assert {row["istd_pair"] for row in rows} == {"HypothesisPair"}
    assert {row["proposal_sources"] for row in rows} == {"hypothesis_source"}
    assert {row["selected_candidate"] for row in rows} == {"TRUE"}
    assert any("candidate_interval" in row["boundary_sources"] for row in rows)


def test_build_boundary_rows_projects_from_peak_hypothesis_spine(monkeypatch) -> None:
    legacy_candidate = _candidate(8.30, left=8.00, right=8.60)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=legacy_candidate.peak,
        n_points=7,
        max_smoothed=100.0,
        n_prominent_peaks=1,
        candidates=(legacy_candidate,),
    )
    hypothesis = PeakHypothesis(
        hypothesis_id="hypothesis-row-id",
        trace_group_id="SampleA|HypothesisTarget|hypothesis_resolver",
        target_label="HypothesisTarget",
        role="ISTD",
        istd_pair="HypothesisPair",
        analysis_mode="targeted",
        resolver_mode="hypothesis_resolver",
        integration=IntegrationResult(
            rt_left_min=8.00,
            rt_apex_min=8.30,
            rt_right_min=8.60,
            raw_apex_rt_min=8.30,
            rt_width_min=0.60,
            height_raw=100.0,
            height_smoothed=95.0,
            area_raw_counts_seconds=1200.0,
        ),
        evidence=EvidenceVector(cwt_best_scale=3.0),
        audit=AuditTrail(
            proposal_sources=("hypothesis_source",),
            selected=True,
        ),
    )

    captured_kwargs = {}

    def _fake_build_peak_hypotheses(**kwargs):
        captured_kwargs.update(kwargs)
        return (hypothesis,)

    monkeypatch.setattr(
        boundary_rows,
        "build_peak_hypotheses",
        _fake_build_peak_hypotheses,
    )

    rt = np.asarray([8.0, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6])
    intensity = np.asarray([10.0, 18.0, 70.0, 100.0, 70.0, 18.0, 10.0])

    rows = build_peak_candidate_boundary_rows(
        sample_name="SampleA",
        target_label="LegacyTarget",
        role="Analyte",
        istd_pair="LegacyPair",
        resolver_mode="legacy_savgol",
        peak_result=peak_result,
        rt=rt,
        intensity=intensity,
    )

    assert rows
    assert {row["candidate_id"] for row in rows} == {"hypothesis-row-id"}
    assert {row["target_label"] for row in rows} == {"HypothesisTarget"}
    assert {row["role"] for row in rows} == {"ISTD"}
    assert {row["istd_pair"] for row in rows} == {"HypothesisPair"}
    assert {row["resolver_mode"] for row in rows} == {"hypothesis_resolver"}
    assert {row["proposal_sources"] for row in rows} == {"hypothesis_source"}
    assert {row["selected_candidate"] for row in rows} == {"TRUE"}
    assert captured_kwargs["rt"] is rt
    assert captured_kwargs["intensity"] is intensity
    assert captured_kwargs["trace_group"] is None


def test_write_peak_candidate_boundaries_tsv_serializes_rows_safely(
    tmp_path: Path,
) -> None:
    path = tmp_path / "peak_candidate_boundaries.tsv"
    row = build_peak_candidate_boundary_rows(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="legacy_savgol",
        peak_result=PeakDetectionResult(
            status="OK",
            peak=_candidate(8.30, left=8.00, right=8.60).peak,
            n_points=11,
            max_smoothed=100.0,
            n_prominent_peaks=1,
            candidates=(_candidate(8.30, left=8.00, right=8.60),),
        ),
        rt=np.asarray([8.0, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6]),
        intensity=np.asarray([10.0, 18.0, 70.0, 100.0, 70.0, 18.0, 10.0]),
    )[0]
    row["boundary_sources"] = "line1\nline2\twith tab"
    row["trace_group_id"] = "debug-only-internal-id"
    row.pop("boundary_concern_labels")

    write_peak_candidate_boundaries_tsv(path, [row])

    text = path.read_text(encoding="utf-8-sig")
    assert "line1 line2 with tab" in text
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert reader.fieldnames == list(PEAK_CANDIDATE_BOUNDARY_HEADERS)
        assert "trace_group_id" not in reader.fieldnames
        rows = list(reader)
    assert rows[0]["sample_name"] == "SampleA"
    assert rows[0]["boundary_sources"] == "line1 line2 with tab"
    assert rows[0]["boundary_concern_labels"] == ""


def test_build_boundary_summary_keeps_only_top_rows_with_review_context() -> None:
    first = _candidate(8.30, left=8.00, right=8.60)
    second = _candidate(8.80, left=8.50, right=9.00, sources=("local_minimum",))
    peak_result = PeakDetectionResult(
        status="OK",
        peak=first.peak,
        n_points=11,
        max_smoothed=100.0,
        n_prominent_peaks=2,
        candidates=(first, second),
    )
    rows = build_peak_candidate_boundary_rows(
        sample_name="SampleA",
        target_label="Analyte",
        target_mz=258.1085,
        role="Analyte",
        istd_pair="ISTD",
        resolver_mode="region_first_safe_merge",
        peak_result=peak_result,
        rt=np.asarray([8.0, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 9.0]),
        intensity=np.asarray(
            [10.0, 18.0, 70.0, 100.0, 70.0, 18.0, 10.0, 20.0, 80.0, 20.0, 10.0]
        ),
    )

    summary = build_peak_candidate_boundary_summary_rows(rows)

    assert len(summary) == 2
    assert {row["target_mz"] for row in summary} == {"258.10850"}
    assert {row["top_boundary_audit_rank"] for row in summary} == {"1"}
    assert all(row["top_boundary_id"] for row in summary)
    assert all(row["nonoverlap_selected"] for row in summary)
    assert all(row["top_boundary_support_labels"] for row in summary)


def test_write_peak_candidate_boundary_summary_tsv_serializes_rows_safely(
    tmp_path: Path,
) -> None:
    path = tmp_path / "peak_candidate_boundary_summary.tsv"
    row = {
        "sample_name": "SampleA",
        "target_label": "line1\nline2\twith tab",
        "target_mz": "258.10850",
        "top_boundary_id": "boundary-1",
    }

    write_peak_candidate_boundary_summary_tsv(path, [row])

    text = path.read_text(encoding="utf-8-sig")
    assert "line1 line2 with tab" in text
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert rows[0]["target_label"] == "line1 line2 with tab"
    assert rows[0]["target_mz"] == "258.10850"


def test_nonoverlap_audit_selects_higher_score_top_boundary_on_overlap() -> None:
    first = _candidate(8.30, left=8.00, right=8.60)
    second = _candidate(8.45, left=8.20, right=8.70, sources=("local_minimum",))
    peak_result = PeakDetectionResult(
        status="OK",
        peak=first.peak,
        n_points=8,
        max_smoothed=100.0,
        n_prominent_peaks=2,
        candidates=(first, second),
    )

    rows = build_peak_candidate_boundary_rows(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="region_first_safe_merge",
        peak_result=peak_result,
        rt=np.asarray([8.0, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7]),
        intensity=np.asarray([5.0, 20.0, 80.0, 100.0, 90.0, 60.0, 20.0, 5.0]),
    )

    top_rows = [row for row in rows if row["boundary_audit_top"] == "TRUE"]
    selected = [
        row
        for row in top_rows
        if row["boundary_nonoverlap_selected"] == "TRUE"
    ]
    rejected = [
        row
        for row in top_rows
        if row["boundary_nonoverlap_selected"] == "FALSE"
    ]

    assert len(selected) == 1
    assert len(rejected) == 1
    assert rejected[0]["boundary_nonoverlap_note"] == "overlaps_higher_score"
    assert rejected[0]["boundary_nonoverlap_blocker_id"] == selected[0]["boundary_id"]


def test_nonoverlap_audit_uses_weighted_interval_selection() -> None:
    rows = [
        _boundary_row("wide", left=8.0, right=8.4, score=60),
        _boundary_row("left", left=8.0, right=8.2, score=40),
        _boundary_row("right", left=8.2, right=8.4, score=40),
    ]

    _apply_boundary_nonoverlap_selection(rows)

    selected_ids = {
        row["boundary_id"]
        for row in rows
        if row["boundary_nonoverlap_selected"] == "TRUE"
    }
    wide = next(row for row in rows if row["boundary_id"] == "wide")

    assert selected_ids == {"left", "right"}
    assert wide["boundary_nonoverlap_note"] == "overlaps_weighted_selection"
    assert wide["boundary_nonoverlap_blocker_id"] == "left;right"


def test_disabled_boundary_writer_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "peak_candidate_boundaries.tsv"

    write_peak_candidate_boundaries_tsv(
        path,
        [{"sample_name": "SampleA"}],
        enabled=False,
    )

    assert not path.exists()


def _candidate(
    rt: float,
    *,
    left: float,
    right: float,
    sources: tuple[str, ...] = ("legacy_savgol",),
    cwt_best_scale: float | None = None,
) -> PeakCandidate:
    peak = PeakResult(
        rt=rt,
        intensity=100.0,
        intensity_smoothed=95.0,
        area=1200.0,
        peak_start=left,
        peak_end=right,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=rt,
        selection_apex_intensity=95.0,
        selection_apex_index=3,
        raw_apex_rt=rt,
        raw_apex_intensity=100.0,
        raw_apex_index=3,
        prominence=90.0,
        cwt_best_scale=cwt_best_scale,
        proposal_sources=sources,
        source_apex_rank=1,
    )


def _boundary_row(
    boundary_id: str,
    *,
    left: float,
    right: float,
    score: int,
) -> dict[str, str]:
    return {
        "boundary_id": boundary_id,
        "boundary_audit_score": str(score),
        "boundary_audit_top": "TRUE",
        "selected_candidate": "FALSE",
        "is_candidate_interval": "TRUE",
        "rt_left_min": f"{left:.5f}",
        "rt_right_min": f"{right:.5f}",
    }
