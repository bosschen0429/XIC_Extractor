import numpy as np
import pytest

from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence
from xic_extractor.extraction.targeted_peak_hypotheses import (
    build_targeted_peak_hypotheses as build_peak_hypotheses,
)
from xic_extractor.ms2_trace_evidence import MS2TraceEvidence
from xic_extractor.peak_detection.hypotheses import hypothesis_audit_id
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidateScore,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.traces import Trace, targeted_trace_group


def test_hypothesis_id_is_deterministic() -> None:
    candidate = _candidate(
        8.1234567,
        left=8.001234,
        right=8.654321,
        proposal_sources=("legacy_savgol",),
    )

    first = hypothesis_audit_id(
        sample_name="SampleA",
        target_label="Analyte",
        resolver_mode="savgol",
        candidate=candidate,
    )
    second = hypothesis_audit_id(
        sample_name="SampleA",
        target_label="Analyte",
        resolver_mode="savgol",
        candidate=candidate,
    )

    assert first == second
    assert first == ("SampleA|Analyte|savgol|legacy_savgol|8.12346|8.00123|8.65432")


def test_build_peak_hypotheses_marks_selected_and_rejected_candidates() -> None:
    selected = _candidate(8.5, area=5000.0, proposal_sources=("legacy_savgol",))
    rejected = _candidate(8.9, area=900.0, proposal_sources=("local_minimum",))
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=20,
        max_smoothed=3000.0,
        n_prominent_peaks=2,
        candidates=(selected, rejected),
        candidate_scores=(
            _score(selected, raw_score=92, confidence="HIGH"),
            _score(rejected, raw_score=58, confidence="LOW"),
        ),
        selection_reference_rt=8.45,
    )

    hypotheses = build_peak_hypotheses(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="ISTD",
        resolver_mode="local_minimum",
        peak_result=result,
        candidate_ms2_evidence={
            selected: _ms2_evidence(nl_match=True),
            rejected: _ms2_evidence(nl_match=False),
        },
    )

    assert [hypothesis.audit.selected for hypothesis in hypotheses] == [True, False]
    assert hypotheses[0].audit.proposal_sources == ("legacy_savgol",)
    assert hypotheses[0].evidence.ms2_present is True
    assert hypotheses[0].evidence.nl_match is True
    assert hypotheses[0].evidence.raw_score == 92
    assert hypotheses[0].evidence.support_labels == ("strict_nl_ok", "shape_clean")
    assert hypotheses[0].integration.boundary_sources == ("candidate_interval",)
    assert hypotheses[1].audit.proposal_sources == ("local_minimum",)
    assert hypotheses[1].audit.rejection_reason == "lower_confidence"
    assert hypotheses[1].evidence.nl_match is False
    assert hypotheses[1].evidence.concern_labels == ("nl_fail",)


def test_hypothesis_selection_reference_comes_from_detection_result() -> None:
    selected = _candidate(8.5)
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=20,
        max_smoothed=3000.0,
        n_prominent_peaks=1,
        candidates=(selected,),
        selection_reference_rt=None,
    )

    hypothesis = build_peak_hypotheses(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="savgol",
        peak_result=result,
    )[0]

    assert hypothesis.audit.selection_reference_rt_min is None


def test_build_peak_hypotheses_projects_scorer_facts_to_successor_evidence() -> None:
    selected = _candidate(
        8.5,
        left=8.25,
        right=8.75,
        area=1234.0,
        proposal_sources=("legacy_savgol", "centwave_cwt"),
        quality_flags=("low_trace_continuity",),
        region_scan_count=8,
        region_duration_min=0.5,
        region_edge_ratio=0.82,
        region_trace_continuity=0.75,
        cwt_best_scale=4.0,
        cwt_ridge_persistence=0.6,
    )
    score = PeakCandidateScore(
        candidate=selected,
        confidence="MEDIUM",
        reason="decision: medium with trace support",
        raw_score=73,
        support_labels=(
            "strict_nl_ok",
            "ms2_trace_strong",
            "cwt_same_apex_support",
        ),
        concern_labels=("trace_quality_review",),
        cap_labels=("rt_window_cap",),
        prior_rt=8.45,
        quality_penalty=2,
    )
    ms2_evidence = CandidateMS2Evidence(
        ms2_present=True,
        nl_match=True,
        nl_status="OK",
        trigger_scan_count=3,
        strict_nl_scan_count=2,
        best_loss_ppm=1.23,
        best_scan_rt=8.55,
        best_product_base_ratio=0.42,
        alignment_source="boundary_rescue",
        diagnostic_product_absence_reason="",
        nearest_product_loss_ppm=4.5,
        nearest_product_base_ratio=0.18,
        nearest_product_mz=123.456,
        trace=MS2TraceEvidence(
            product_point_count=3,
            product_apex_rt=8.55,
            product_apex_delta_min=0.05,
            product_height=900.0,
            product_area=180.0,
            trace_continuity=1.0,
            strength="strong",
        ),
    )
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=20,
        max_smoothed=3000.0,
        n_prominent_peaks=1,
        candidates=(selected,),
        candidate_scores=(score,),
        selection_reference_rt=8.45,
    )

    hypothesis = build_peak_hypotheses(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="ISTD",
        resolver_mode="local_minimum",
        peak_result=result,
        candidate_ms2_evidence={selected: ms2_evidence},
    )[0]

    evidence = hypothesis.evidence
    assert evidence.confidence == "MEDIUM"
    assert evidence.raw_score == 73
    assert evidence.support_labels == (
        "strict_nl_ok",
        "ms2_trace_strong",
        "cwt_same_apex_support",
    )
    assert evidence.concern_labels == ("trace_quality_review",)
    assert evidence.cap_labels == ("rt_window_cap",)
    assert evidence.reason == "decision: medium with trace support"
    assert evidence.quality_flags == ("low_trace_continuity",)
    assert evidence.region_scan_count == 8
    assert evidence.region_duration_min == 0.5
    assert evidence.region_edge_ratio == 0.82
    assert evidence.region_trace_continuity == 0.75
    assert evidence.ms2_present is True
    assert evidence.nl_match is True
    assert evidence.ms2_trace_strength == "strong"
    assert evidence.nl_status == "OK"
    assert evidence.best_loss_ppm == 1.23
    assert evidence.best_ms2_scan_rt_min == 8.55
    assert evidence.apex_ms2_delta_min == pytest.approx(0.05)
    assert evidence.best_product_base_ratio == 0.42
    assert evidence.trigger_scan_count == 3
    assert evidence.strict_nl_scan_count == 2
    assert evidence.ms2_alignment_source == "boundary_rescue"
    assert evidence.nearest_product_loss_ppm == 4.5
    assert evidence.nearest_product_base_ratio == 0.18
    assert evidence.nearest_product_mz == 123.456
    assert evidence.rt_prior_min == 8.45
    assert evidence.cwt_best_scale == 4.0
    assert evidence.cwt_ridge_persistence == 0.6

    assert evidence.common is not None
    assert evidence.common.ms1_apex_rt_min == 8.5
    assert evidence.common.ms1_area == 1234.0
    assert evidence.common.ms1_height == 1200.0
    assert evidence.common.ms1_peak_rt_start == 8.25
    assert evidence.common.ms1_peak_rt_end == 8.75
    assert evidence.common.ms2_present is True
    assert evidence.common.nl_match is True
    assert evidence.common.ms2_trace_strength == "strong"
    assert evidence.common.neutral_loss_error_ppm == 1.23
    assert evidence.common.confidence == "MEDIUM"
    assert evidence.common.evidence_score == 73
    assert evidence.common.reason == "decision: medium with trace support"

    assert evidence.decision_semantics is not None
    assert evidence.decision_semantics.decision_class == "review"
    assert evidence.decision_semantics.support_reasons == (
        "candidate_aligned_ms2_nl",
        "cwt_boundary_morphology_context",
    )
    assert "targeted_rt_conflict" in evidence.decision_semantics.conflict_reasons
    assert "targeted_rt_review" in evidence.decision_semantics.review_reasons


def test_build_peak_hypotheses_scan_indices_match_bounded_baseline_interval() -> None:
    selected = _candidate(9.8, left=9.8, right=10.2)
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=3,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(selected,),
    )

    hypothesis = build_peak_hypotheses(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="savgol",
        peak_result=result,
        rt=np.asarray([8.0, 8.5, 9.0]),
        intensity=np.asarray([10.0, 80.0, 20.0]),
    )[0]

    assert hypothesis.integration.raw_scan_indices == (1, 2)
    assert hypothesis.integration.baseline_type == "asls"


def test_build_peak_hypotheses_rejects_retired_linear_edge_baseline_override() -> None:
    selected = _candidate(8.5, left=8.0, right=9.0)
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=3,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(selected,),
    )

    with pytest.raises(ValueError, match="retired; use asls"):
        build_peak_hypotheses(
            sample_name="SampleA",
            target_label="Analyte",
            role="Analyte",
            istd_pair="",
            resolver_mode="savgol",
            peak_result=result,
            rt=np.asarray([8.0, 8.5, 9.0]),
            intensity=np.asarray([10.0, 80.0, 20.0]),
            baseline_integration_method="linear_edge",
        )


def test_build_peak_hypotheses_accepts_shared_trace_group() -> None:
    selected = _candidate(9.8, left=9.8, right=10.2)
    result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=3,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(selected,),
    )
    trace = Trace.from_arrays(
        sample_name="SampleA",
        mz=269.1388,
        rt=[8.0, 8.5, 9.0],
        intensity=[10.0, 80.0, 20.0],
        rt_min=8.0,
        rt_max=9.0,
        ppm_tol=10.0,
    )
    trace_group = targeted_trace_group(
        trace,
        target_label="Analyte",
        resolver_mode="savgol",
    )

    hypothesis = build_peak_hypotheses(
        sample_name="SampleA",
        target_label="Analyte",
        role="Analyte",
        istd_pair="",
        resolver_mode="savgol",
        peak_result=result,
        trace_group=trace_group,
    )[0]

    assert hypothesis.trace_group_id == "SampleA|Analyte|savgol"
    assert hypothesis.integration.raw_scan_indices == (1, 2)
    assert hypothesis.integration.baseline_type == "asls"


def test_build_peak_hypotheses_returns_empty_without_candidate_intervals() -> None:
    result = PeakDetectionResult(
        status="PEAK_NOT_FOUND",
        peak=None,
        n_points=20,
        max_smoothed=3000.0,
        n_prominent_peaks=0,
        candidates=(),
    )

    assert (
        build_peak_hypotheses(
            sample_name="SampleA",
            target_label="Analyte",
            role="Analyte",
            istd_pair="",
            resolver_mode="savgol",
            peak_result=result,
        )
        == ()
    )


def _candidate(
    rt: float,
    *,
    left: float | None = None,
    right: float | None = None,
    area: float = 1000.0,
    proposal_sources: tuple[str, ...] = ("legacy_savgol",),
    quality_flags: tuple[str, ...] = (),
    region_scan_count: int | None = None,
    region_duration_min: float | None = None,
    region_edge_ratio: float | None = None,
    region_trace_continuity: float | None = None,
    cwt_best_scale: float | None = None,
    cwt_ridge_persistence: float | None = None,
) -> PeakCandidate:
    peak_start = rt - 0.2 if left is None else left
    peak_end = rt + 0.2 if right is None else right
    peak = PeakResult(
        rt=rt,
        intensity=1200.0,
        intensity_smoothed=1100.0,
        area=area,
        peak_start=peak_start,
        peak_end=peak_end,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=rt,
        selection_apex_intensity=1100.0,
        selection_apex_index=7,
        raw_apex_rt=rt,
        raw_apex_intensity=1200.0,
        raw_apex_index=7,
        prominence=700.0,
        quality_flags=quality_flags,
        region_scan_count=region_scan_count,
        region_duration_min=region_duration_min,
        region_edge_ratio=region_edge_ratio,
        region_trace_continuity=region_trace_continuity,
        cwt_best_scale=cwt_best_scale,
        cwt_ridge_persistence=cwt_ridge_persistence,
        proposal_sources=proposal_sources,
        source_apex_rank=1,
    )


def _score(
    candidate: PeakCandidate,
    *,
    raw_score: int,
    confidence: str,
) -> PeakCandidateScore:
    return PeakCandidateScore(
        candidate=candidate,
        confidence=confidence,
        reason=f"decision: {confidence.lower()}",
        raw_score=raw_score,
        support_labels=("strict_nl_ok", "shape_clean"),
        concern_labels=() if confidence == "HIGH" else ("nl_fail",),
        cap_labels=() if confidence == "HIGH" else ("nl_fail_cap",),
    )


def _ms2_evidence(*, nl_match: bool) -> CandidateMS2Evidence:
    return CandidateMS2Evidence(
        ms2_present=True,
        nl_match=nl_match,
        nl_status="OK" if nl_match else "NL_FAIL",
        trigger_scan_count=1,
        strict_nl_scan_count=1 if nl_match else 0,
        best_loss_ppm=1.0 if nl_match else None,
        best_scan_rt=8.5,
        best_product_base_ratio=0.3,
        alignment_source="region",
    )
