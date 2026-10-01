import numpy as np

from xic_extractor.peak_detection.chrom_peak_segment_projection import (
    chrom_peak_segment_promoted_hypothesis_from_hypothesis,
)
from xic_extractor.peak_detection.chrom_peak_segments import ChromPeakSegmentPolicy
from xic_extractor.peak_detection.evidence_semantics import EvidenceDecisionSemantics
from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.selected_envelope import (
    SelectedEnvelopeBoundaryEvaluation,
    SelectedEnvelopePolicy,
    TraceInterval,
)


def test_chrom_peak_segment_projection_promotes_selected_apex_segment_boundary(
) -> None:
    residual = np.asarray(
        [0, 0, 10, 50, 10, 0, 0, 8, 80, 8, 0, 0],
        dtype=float,
    )
    rt = np.arange(float(len(residual)), dtype=float)
    intensity = residual + 10.0
    hypothesis = _hypothesis(rt_left_min=1.0, rt_apex_min=3.0, rt_right_min=10.0)

    promoted, projection = chrom_peak_segment_promoted_hypothesis_from_hypothesis(
        hypothesis,
        rt_values=rt,
        intensity_values=intensity,
        quantitation_context_rt_start=0.0,
        quantitation_context_rt_end=11.0,
    )

    assert projection.accepted is True
    assert projection.boundary_change_class == "chrom_peak_segment_narrowed"
    assert projection.selected_segment_class == "separate_peak"
    assert promoted is not hypothesis
    assert promoted.integration.integration_method == "chrom_peak_segment_gaussian15"
    assert promoted.integration.rt_left_min <= 2.0
    assert promoted.integration.rt_right_min < 6.0
    assert promoted.integration.area_ms1_morphology is not None
    assert "chrom_peak_segment" in promoted.integration.boundary_sources
    assert "chrom_peak_segment" in promoted.audit.proposal_sources
    assert promoted.evidence.decision_semantics is not None
    assert (
        "chrom_peak_segment_context"
        in promoted.evidence.decision_semantics.support_reasons
    )


def test_chrom_peak_segment_projection_marks_shoulder_as_review_support() -> None:
    residual = np.asarray([0, 0, 10, 50, 40, 45, 30, 0, 0], dtype=float)
    rt = np.arange(float(len(residual)), dtype=float)
    intensity = residual + 10.0
    hypothesis = _hypothesis(rt_left_min=1.0, rt_apex_min=3.0, rt_right_min=7.0)

    promoted, projection = chrom_peak_segment_promoted_hypothesis_from_hypothesis(
        hypothesis,
        rt_values=rt,
        intensity_values=intensity,
        quantitation_context_rt_start=0.0,
        quantitation_context_rt_end=8.0,
    )

    assert projection.accepted is False
    assert projection.selected_segment_class == "shoulder_candidate"
    assert promoted.evidence.decision_semantics is not None
    assert (
        "chrom_peak_segment_shoulder_review"
        in promoted.evidence.decision_semantics.review_reasons
    )


def test_chrom_peak_segment_projection_uses_dominant_gaussian_peak_in_conflict(
) -> None:
    residual = np.asarray(
        [0, 0, 10, 80, 30, 0, 30, 60, 30, 20, 0, 0],
        dtype=float,
    )
    rt = np.arange(float(len(residual)), dtype=float) / 10.0
    intensity = residual + 10.0
    hypothesis = _hypothesis(rt_left_min=0.2, rt_apex_min=0.3, rt_right_min=0.5)
    selected_envelope_evaluation = _selected_envelope_evaluation(
        selected_candidate_id=hypothesis.hypothesis_id,
        resolver_interval=TraceInterval(2, 6, 0.2, 0.5, 4),
        selected_envelope_interval=TraceInterval(2, 11, 0.2, 1.0, 9),
        threshold=8.0,
    )

    promoted, projection = chrom_peak_segment_promoted_hypothesis_from_hypothesis(
        hypothesis,
        rt_values=rt,
        intensity_values=intensity,
        quantitation_context_rt_start=0.0,
        quantitation_context_rt_end=1.1,
        selected_envelope_evaluation=selected_envelope_evaluation,
        policy=ChromPeakSegmentPolicy(
            baseline_return_fraction=0.10,
            morphology_trace_method="raw",
        ),
    )

    assert projection.accepted is True
    assert projection.boundary_change_class == (
        "chrom_peak_segment_gaussian15_peak_group"
    )
    assert projection.selected_segment_rt_start == 0.6
    assert projection.selected_segment_rt_end == 1.0
    assert promoted.integration.integration_method == "chrom_peak_segment_gaussian15"
    assert promoted.integration.rt_left_min == 0.6
    assert promoted.integration.rt_apex_min == 0.7
    assert promoted.integration.rt_right_min == 1.0
    assert (
        "gaussian15_morphology_peak_group"
        in promoted.integration.boundary_sources
    )


def test_chrom_peak_segment_projection_merges_connected_shoulder_group(
) -> None:
    residual = np.asarray(
        [0, 0, 10, 80, 50, 60, 70, 50, 20, 0, 0, 30, 90, 30, 0],
        dtype=float,
    )
    rt = np.arange(float(len(residual)), dtype=float) / 10.0
    intensity = residual + 10.0
    hypothesis = _hypothesis(rt_left_min=0.2, rt_apex_min=0.3, rt_right_min=0.5)
    selected_envelope_evaluation = _selected_envelope_evaluation(
        selected_candidate_id=hypothesis.hypothesis_id,
        resolver_interval=TraceInterval(2, 6, 0.2, 0.5, 4),
        selected_envelope_interval=TraceInterval(2, 12, 0.2, 1.1, 10),
        threshold=8.0,
    )

    promoted, projection = chrom_peak_segment_promoted_hypothesis_from_hypothesis(
        hypothesis,
        rt_values=rt,
        intensity_values=intensity,
        quantitation_context_rt_start=0.0,
        quantitation_context_rt_end=1.4,
        selected_envelope_evaluation=selected_envelope_evaluation,
        policy=ChromPeakSegmentPolicy(
            baseline_return_fraction=0.10,
            morphology_trace_method="raw",
        ),
    )

    assert projection.accepted is True
    assert projection.boundary_change_class == (
        "chrom_peak_segment_gaussian15_peak_group"
    )
    assert projection.selected_segment_class == "shoulder_candidate"
    assert projection.selected_segment_rt_start == 0.2
    assert projection.selected_segment_rt_end == 0.8
    assert promoted.integration.rt_apex_min == 0.6
    assert promoted.integration.rt_right_min < 1.1
    assert (
        "gaussian15_morphology_peak_group"
        in promoted.integration.boundary_sources
    )


def test_chrom_peak_segment_projection_does_not_bridge_sustained_baseline_gap(
) -> None:
    residual = np.asarray(
        [0, 0, 10, 80, 30, 0, 0, 60, 30, 20, 0, 0],
        dtype=float,
    )
    rt = np.arange(float(len(residual)), dtype=float)
    intensity = residual + 10.0
    hypothesis = _hypothesis(rt_left_min=2.0, rt_apex_min=3.0, rt_right_min=5.0)
    selected_envelope_evaluation = _selected_envelope_evaluation(
        selected_candidate_id=hypothesis.hypothesis_id,
        resolver_interval=TraceInterval(2, 6, 2.0, 5.0, 4),
        selected_envelope_interval=TraceInterval(2, 11, 2.0, 10.0, 9),
        threshold=8.0,
    )

    promoted, projection = chrom_peak_segment_promoted_hypothesis_from_hypothesis(
        hypothesis,
        rt_values=rt,
        intensity_values=intensity,
        quantitation_context_rt_start=0.0,
        quantitation_context_rt_end=11.0,
        selected_envelope_evaluation=selected_envelope_evaluation,
        policy=ChromPeakSegmentPolicy(
            baseline_return_fraction=0.10,
            morphology_trace_method="raw",
        ),
    )

    assert projection.accepted is True
    assert projection.boundary_change_class == "chrom_peak_segment_narrowed"
    assert projection.selected_segment_rt_end == 4.0
    assert promoted.integration.rt_right_min == 4.0
    assert "selected_envelope_right_tail" not in promoted.integration.boundary_sources


def test_chrom_peak_segment_projection_externalizes_apex_without_segment() -> None:
    residual = np.asarray([0, 0, 10, 50, 10, 0, 0], dtype=float)
    rt = np.arange(float(len(residual)), dtype=float)
    intensity = residual + 10.0
    hypothesis = _hypothesis(rt_left_min=1.0, rt_apex_min=6.5, rt_right_min=6.8)

    promoted, projection = chrom_peak_segment_promoted_hypothesis_from_hypothesis(
        hypothesis,
        rt_values=rt,
        intensity_values=intensity,
        quantitation_context_rt_start=0.0,
        quantitation_context_rt_end=6.0,
    )

    assert promoted is hypothesis
    assert projection.accepted is False
    assert projection.boundary_change_class == "chrom_peak_segment_apex_mismatch"


def _selected_envelope_evaluation(
    *,
    selected_candidate_id: str,
    resolver_interval: TraceInterval,
    selected_envelope_interval: TraceInterval,
    threshold: float,
) -> SelectedEnvelopeBoundaryEvaluation:
    policy = SelectedEnvelopePolicy(
        baseline_return_fraction=0.10,
        morphology_trace_method="raw",
    )
    return SelectedEnvelopeBoundaryEvaluation(
        selected_candidate_id=selected_candidate_id,
        resolver_interval=resolver_interval,
        selected_envelope_interval=selected_envelope_interval,
        quantitation_context_interval=TraceInterval(0, 12, 0.0, 11.0, 12),
        policy_snapshot=policy,
        resolved_baseline_return_threshold=threshold,
        morphology_trace_method=policy.morphology_trace_method,
        morphology_trace_window_points=policy.morphology_trace_window_points,
        morphology_trace_effective_points=1,
        selected_boundary_mode="review_only",
        legacy_resolver_provenance="",
        boundary_change_class="context_apex_conflict",
        boundary_evidence_sources=("selected_envelope", "context_apex_conflict"),
        boundary_stop_reason="stronger_context_apex_outside_envelope",
        asls_area_old_interval=1.0,
        asls_area_selected_envelope=2.0,
        area_delta_ratio=1.0,
        row_boundary_decision="externalize",
        gaussian15_area_old_interval_shadow=1.0,
        gaussian15_area_selected_envelope_shadow=2.0,
        gaussian15_area_delta_ratio_shadow=1.0,
    )


def _hypothesis(
    *,
    rt_left_min: float,
    rt_apex_min: float,
    rt_right_min: float,
) -> PeakHypothesis:
    return PeakHypothesis(
        hypothesis_id="hypothesis-001",
        trace_group_id="trace-001",
        target_label="Analyte",
        role="Analyte",
        istd_pair="ISTD",
        analysis_mode="targeted",
        resolver_mode="local_minimum",
        integration=IntegrationResult(
            rt_left_min=rt_left_min,
            rt_apex_min=rt_apex_min,
            rt_right_min=rt_right_min,
            raw_apex_rt_min=rt_apex_min,
            rt_width_min=rt_right_min - rt_left_min,
            height_raw=100.0,
            height_smoothed=90.0,
            area_raw_counts_seconds=1234.5,
        ),
        evidence=EvidenceVector(
            decision_semantics=EvidenceDecisionSemantics(
                decision_class="review",
                support_reasons=("ms1_coherent",),
            )
        ),
        audit=AuditTrail(selected=True, selection_rank=1),
    )
