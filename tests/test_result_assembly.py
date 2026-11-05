from dataclasses import replace

import numpy as np
import pytest

from xic_extractor.configuration.models import Target
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.extraction.result_assembly import build_extraction_result
from xic_extractor.extraction.result_models import ExtractionResult
from xic_extractor.extraction.targeted_peak_selection import (
    build_production_peak_hypotheses,
    selected_peak_hypothesis,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlCandidateInterval,
    CidNlEventQuery,
    evaluate_cid_nl_method_relations,
)
from xic_extractor.peak_detection.cid_nl_peak_support import DdaCidNlPeakSupportPolicy
from xic_extractor.peak_detection.evidence_facts import (
    CandidateEvidenceFacts,
    build_candidate_evidence_facts,
)
from xic_extractor.peak_detection.evidence_semantics import EvidenceDecisionSemantics
from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidateScore,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation
from xic_extractor.peak_detection.scoring_models import ScoringContext
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
)
from xic_extractor.peak_detection.signal_quality import SignalQuality
from xic_extractor.precursor_evidence import PrecursorLossAssessment


def test_build_extraction_result_retains_unscored_peak() -> None:
    target = _target()
    candidate = _candidate()
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=12,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
    selected = selected_peak_hypothesis(
        build_production_peak_hypotheses(
            config=_Config(),
            sample_name="SampleA",
            target=target,
            peak_result=peak_result,
        )
    )

    result = build_extraction_result(
        peak_result=peak_result,
        nl_result=None,
        candidate_ms2_evidence=None,
        target=target,
        candidate=candidate,
        scoring_context_builder=None,
        selected_hypothesis=selected,
    )

    assert selected is not None
    assert result.selection_decision is not None
    assert result.reason == ""


def test_build_extraction_result_uses_typed_evidence_reason() -> None:
    target = _target()
    candidate = _candidate(quality_flags=())
    typed_facts = _clean_typed_facts(candidate)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=12,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
        reason="decision: review only, not counted; cap: VERY_LOW",
        candidate_scores=(
            PeakCandidateScore(
                candidate=candidate,
                reason="decision: detected",
                raw_score=95,
                evidence_facts=typed_facts,
            ),
        ),
    )
    selected = selected_peak_hypothesis(
        build_production_peak_hypotheses(
            config=_Config(),
            sample_name="SampleA",
            target=target,
            peak_result=peak_result,
        )
    )

    result = build_extraction_result(
        peak_result=peak_result,
        nl_result=None,
        candidate_ms2_evidence=None,
        target=target,
        candidate=candidate,
        scoring_context_builder=None,
        selected_hypothesis=selected,
    )

    assert selected is not None
    assert result.selection_decision is not None
    assert (
        result.selection_decision.projected_reason
        == "decision: accepted; evidence: ms1_coherent; candidate_aligned_ms2_nl; "
        "role_aware_rt_support"
    )
    assert result.reason == (
        "decision: accepted; evidence: ms1_coherent; candidate_aligned_ms2_nl; "
        "role_aware_rt_support"
    )


def test_build_extraction_result_uses_selection_decision_projection() -> None:
    peak_result = _peak_result_with_candidate()
    selected = _selected_hypothesis_with_integration(
        IntegrationResult(
            rt_left_min=8.4,
            rt_apex_min=8.5,
            rt_right_min=8.6,
            raw_apex_rt_min=8.5,
            rt_width_min=0.2,
            height_raw=1200.0,
            height_smoothed=1100.0,
            area_raw_counts_seconds=1234.5,
        )
    )
    decision = PeakHypothesisSelectionDecision(
        selected_candidate_id=selected.hypothesis_id,
        trace_group_id=selected.trace_group_id,
        decision_class="review",
        projected_reason="decision projection",
        review_reasons=("explicit_decision_projection",),
        not_counted_reasons=("selected_envelope_boundary_defer",),
    )

    result = build_extraction_result(
        peak_result=peak_result,
        nl_result=None,
        candidate_ms2_evidence=None,
        target=_target(),
        candidate=peak_result.candidates[0],
        scoring_context_builder=None,
        selected_hypothesis=selected,
        selection_decision=decision,
    )

    assert result.selection_decision is decision
    assert result.reason == "decision projection"
    assert result.targeted_product_projection is not None
    assert result.targeted_product_projection.product_state == "not_counted"
    assert result.targeted_product_projection.counted_detection is False
    assert (
        "selected_envelope_boundary_defer"
        in result.targeted_product_projection.not_counted_reasons
    )


def test_extraction_result_reports_selected_integration_values() -> None:
    peak_result = _peak_result_with_candidate()
    selected = _selected_hypothesis_with_integration(
        IntegrationResult(
            rt_left_min=8.7,
            rt_apex_min=8.95,
            rt_right_min=9.3,
            raw_apex_rt_min=8.96,
            rt_width_min=-0.42,
            height_raw=765.0,
            height_smoothed=700.0,
            area_raw_counts_seconds=4567.89,
        )
    )

    result = build_extraction_result(
        peak_result=peak_result,
        nl_result=None,
        candidate_ms2_evidence=None,
        target=_target(),
        candidate=peak_result.candidates[0],
        scoring_context_builder=None,
        selected_hypothesis=selected,
    )

    assert result.reported_rt == 8.95
    assert result.reported_peak_area == 4567.89
    assert result.reported_peak_intensity == 765.0
    assert result.reported_peak_start == 8.7
    assert result.reported_peak_end == 9.3
    assert result.reported_peak_width == pytest.approx(0.42)


def test_extraction_result_reports_ms1_morphology_area_when_available() -> None:
    peak_result = _peak_result_with_candidate()
    selected = _selected_hypothesis_with_integration(
        IntegrationResult(
            rt_left_min=8.7,
            rt_apex_min=8.95,
            rt_right_min=9.3,
            raw_apex_rt_min=8.96,
            rt_width_min=0.42,
            height_raw=765.0,
            height_smoothed=700.0,
            area_raw_counts_seconds=4567.89,
            area_ms1_morphology=4321.0,
            ms1_morphology_area_source="gaussian15_positive_asls_residual",
        )
    )

    result = build_extraction_result(
        peak_result=peak_result,
        nl_result=None,
        candidate_ms2_evidence=None,
        target=_target(),
        candidate=peak_result.candidates[0],
        scoring_context_builder=None,
        selected_hypothesis=selected,
    )

    assert result.reported_peak_area == pytest.approx(4321.0)


def test_selected_physical_shoulder_adds_review_without_changing_values() -> None:
    evaluation = _physical_evaluation()
    selected = evaluation.answer.selected_segment
    assert selected is not None
    shoulder = replace(
        selected,
        segment_class="shoulder_candidate",
        boundary_stop_reason="shoulder_overlap",
        evidence_sources=(*selected.evidence_sources, "shoulder_overlap"),
    )
    evaluation = replace(
        evaluation,
        answer=replace(evaluation.answer, selected_segment=shoulder),
        segment_universe=replace(
            evaluation.segment_universe,
            segments=(shoulder,),
        ),
    )

    result = _result_with_targeted_projection_semantics(
        target=_target(),
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=(),
        physical_evaluation=evaluation,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_flagged"
    assert "physical_shoulder_overlap" in projection.review_reasons
    assert "physical_shoulder_overlap" in projection.projection_reason
    assert evaluation.facts is not None
    assert result.reported_rt == evaluation.facts.apex_rt_min
    assert result.reported_peak_start == evaluation.facts.left_rt_min
    assert result.reported_peak_end == evaluation.facts.right_rt_min
    assert result.reported_peak_area == evaluation.facts.area_counts_seconds


def test_unselected_physical_shoulder_does_not_flag_clean_projection() -> None:
    evaluation = _physical_evaluation()
    selected = evaluation.answer.selected_segment
    assert selected is not None
    unselected_shoulder = replace(
        selected,
        segment_class="shoulder_candidate",
        boundary_stop_reason="shoulder_overlap",
        evidence_sources=(*selected.evidence_sources, "shoulder_overlap"),
    )
    evaluation = replace(
        evaluation,
        segment_universe=replace(
            evaluation.segment_universe,
            segments=(selected, unselected_shoulder),
        ),
    )

    result = _result_with_targeted_projection_semantics(
        target=_target(),
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=(),
        physical_evaluation=evaluation,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_clean"
    assert "physical_shoulder_overlap" not in projection.review_reasons


def test_extraction_result_projection_accessors_fall_back_to_legacy_peak() -> None:
    peak_result = _peak_result_with_candidate()
    result = build_extraction_result(
        peak_result=peak_result,
        nl_result=None,
        candidate_ms2_evidence=None,
        target=_target(),
        candidate=peak_result.candidates[0],
        scoring_context_builder=None,
        selected_hypothesis=None,
    )

    peak = peak_result.peak
    assert peak is not None
    assert result.reported_rt == peak_result.candidates[0].selection_apex_rt
    assert result.reported_peak_area == peak.area
    assert result.reported_peak_intensity == peak.intensity
    assert result.reported_peak_start == peak.peak_start
    assert result.reported_peak_end == peak.peak_end
    assert result.reported_peak_width == abs(peak.peak_end - peak.peak_start)


def test_build_extraction_result_adds_targeted_product_projection() -> None:
    target = _target(is_istd=True)
    candidate = _candidate(quality_flags=())
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=12,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
        reason="decision: review only, not counted; cap: nl fail",
        severities=((3, "nl_support"),),
        candidate_scores=(
            PeakCandidateScore(
                candidate=candidate,
                reason="decision: review only, not counted; cap: nl fail",
                raw_score=10,
                support_labels=("local_sn_strong", "shape_clean"),
                concern_labels=("nl_fail",),
                cap_labels=("nl_fail_cap",),
            ),
        ),
    )
    selected = selected_peak_hypothesis(
        build_production_peak_hypotheses(
            config=_Config(),
            sample_name="SampleA",
            target=target,
            peak_result=peak_result,
            candidate_ms2_evidence={candidate: _ms2_evidence()},
        )
    )

    result = build_extraction_result(
        peak_result=peak_result,
        nl_result=None,
        candidate_ms2_evidence=_ms2_evidence(),
        target=target,
        candidate=candidate,
        scoring_context_builder=None,
        selected_hypothesis=selected,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert result.nl_token == "NL_FAIL"
    assert projection.product_state == "not_counted"
    assert projection.counted_detection is False
    assert "cid_nl_method_not_supported" in projection.not_counted_reasons


def test_istd_rt_conflict_inside_target_window_stays_counted() -> None:
    target = _target(is_istd=True)
    candidate = _candidate(quality_flags=())
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=12,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
        reason="decision: accepted",
        candidate_scores=(
            PeakCandidateScore(
                candidate=candidate,
                reason="decision: accepted",
                raw_score=95,
                support_labels=("local_sn_strong", "shape_clean"),
            ),
        ),
    )
    selected = PeakHypothesis(
        hypothesis_id="SampleA|ISTD|selected",
        trace_group_id="SampleA|ISTD|targeted",
        target_label="ISTD",
        role="ISTD",
        istd_pair="ISTD",
        analysis_mode="targeted",
        resolver_mode="local_minimum",
        integration=IntegrationResult(
            rt_left_min=8.4,
            rt_apex_min=8.5,
            rt_right_min=8.6,
            raw_apex_rt_min=8.5,
            rt_width_min=0.2,
            height_raw=1200.0,
            height_smoothed=1100.0,
            area_raw_counts_seconds=1234.5,
        ),
        evidence=EvidenceVector(
            reason="decision: accepted",
            decision_semantics=EvidenceDecisionSemantics(
                decision_class="ambiguous",
                support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
                conflict_reasons=("targeted_rt_conflict",),
                review_reasons=("targeted_rt_review",),
            ),
        ),
        audit=AuditTrail(selected=True),
    )
    ms2_evidence = CandidateMS2Evidence(
        ms2_present=True,
        nl_match=True,
        nl_status="OK",
        trigger_scan_count=1,
        strict_nl_scan_count=1,
        best_loss_ppm=2.5,
        best_scan_rt=8.5,
        best_product_base_ratio=0.8,
        alignment_source="region",
    )

    result = build_extraction_result(
        peak_result=peak_result,
        nl_result=NLResult("OK", 2.5, 8.5, 1, 0, 1),
        candidate_ms2_evidence=ms2_evidence,
        target=target,
        candidate=candidate,
        scoring_context_builder=None,
        selected_hypothesis=selected,
        cid_nl_relations=_method_relations(target, candidate),
        selected_cid_nl_candidate_index=0,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_clean"
    assert projection.counted_detection is True
    assert "targeted_rt_conflict" not in projection.conflict_reasons
    assert "targeted_rt_review" not in projection.review_reasons


@pytest.mark.parametrize(
    "conflict_reason",
    ("trace_morphology_conflict", "hard_local_quality_conflict"),
)
def test_istd_trace_conflict_with_product_support_stays_counted(
    conflict_reason: str,
) -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=True),
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=(conflict_reason,),
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_flagged"
    assert projection.counted_detection is True
    assert conflict_reason not in projection.conflict_reasons
    assert "trace_morphology_review" in projection.review_reasons


@pytest.mark.parametrize("unresolved", [(), ("identity_conflict",)])
def test_istd_combined_reviews_preserve_unresolved_conflicts(
    unresolved: tuple[str, ...],
) -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=True),
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=(
            "targeted_rt_conflict",
            "trace_morphology_conflict",
            *unresolved,
        ),
        quality_flags=("too_broad",),
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.conflict_reasons == unresolved
    assert projection.review_reasons == ("trace_morphology_review",)
    assert projection.counted_detection is (not unresolved)
    assert projection.product_state == (
        "ambiguous" if unresolved else "detected_flagged"
    )
    assert result.reported_rt == 8.5
    assert result.reported_peak_area == 1234.5


def test_istd_nl_dropout_waits_for_backfill_even_when_ms1_coherent() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=True),
        support_reasons=("ms1_coherent",),
        conflict_reasons=("targeted_rt_conflict",),
        review_reasons=("plausible_nl_dropout_review",),
        nl_status="NL_FAIL",
        reason="decision: review only, not counted; cap: VERY_LOW due to nl fail",
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "not_counted"
    assert projection.counted_detection is False
    assert "targeted_rt_conflict" not in projection.conflict_reasons
    assert "targeted_rt_review" not in projection.review_reasons
    assert "cid_nl_method_not_supported" in projection.not_counted_reasons
    assert "istd_nl_fail_without_dropout_support" not in projection.not_counted_reasons
    assert "role_aware_istd_expected_present" not in projection.support_reasons


def test_analyte_trace_quality_is_review_not_identity() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=("trace_morphology_conflict",),
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_flagged"
    assert projection.counted_detection is True
    assert "trace_morphology_review" in projection.review_reasons


def test_paired_analyte_anchor_selected_trace_conflict_stays_counted() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=("candidate_aligned_ms2_nl",),
        conflict_reasons=("trace_morphology_conflict",),
        paired_istd_anchor_rt=8.5,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_flagged"
    assert projection.counted_detection is True
    assert "trace_morphology_conflict" not in projection.conflict_reasons
    assert "trace_morphology_review" in projection.review_reasons


def test_paired_analyte_reviews_trace_quality_not_anchor_distance() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=("candidate_aligned_ms2_nl",),
        conflict_reasons=("anchor_conflict", "trace_morphology_conflict"),
        selection_reference_rt=8.5,
        paired_istd_anchor_rt=8.9,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_flagged"
    assert projection.counted_detection is True
    assert "targeted_rt_review" not in projection.review_reasons
    assert "trace_morphology_review" in projection.review_reasons


def test_paired_analyte_anchor_does_not_confirm_nl_fail() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=(),
        conflict_reasons=("candidate_aligned_ms2_nl_conflict",),
        nl_status="NL_FAIL",
        paired_istd_anchor_rt=8.5,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "not_counted"
    assert projection.counted_detection is False
    assert "missing_nl_support_review" in projection.review_reasons
    assert "cid_nl_method_not_supported" in projection.not_counted_reasons
    assert "paired_istd_anchor_support" in projection.support_reasons


def test_paired_analyte_anchor_ratio_keeps_nl_fail_not_counted() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=("ms1_coherent", "paired_area_ratio_support"),
        conflict_reasons=("candidate_aligned_ms2_nl_conflict",),
        nl_status="NL_FAIL",
        paired_istd_anchor_rt=8.5,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "not_counted"
    assert projection.counted_detection is False
    assert "missing_nl_support_review" in projection.review_reasons
    assert "cid_nl_method_not_supported" in projection.not_counted_reasons
    assert "missing_nl_support_review" in projection.review_reasons
    assert "paired_istd_anchor_support" in projection.support_reasons
    assert "paired_area_ratio_support" in projection.support_reasons


def test_paired_analyte_nl_fail_requires_anchor_inside_selected_interval() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=(),
        conflict_reasons=("candidate_aligned_ms2_nl_conflict",),
        nl_status="NL_FAIL",
        paired_istd_anchor_rt=8.9,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "not_counted"
    assert projection.counted_detection is False
    assert "missing_nl_support_review" in projection.review_reasons
    assert "cid_nl_method_not_supported" in projection.not_counted_reasons


def test_paired_analyte_role_support_keeps_nl_fail_not_counted() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=(
            "ms1_coherent",
            "role_aware_rt_support",
            "paired_area_ratio_support",
        ),
        conflict_reasons=("candidate_aligned_ms2_nl_conflict",),
        nl_status="NL_FAIL",
        paired_istd_anchor_rt=8.9,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "not_counted"
    assert projection.counted_detection is False
    assert "missing_nl_support_review" in projection.review_reasons
    assert "cid_nl_method_not_supported" in projection.not_counted_reasons
    assert "missing_nl_support_review" in projection.review_reasons
    assert "role_aware_rt_support" in projection.support_reasons
    assert "paired_area_ratio_support" in projection.support_reasons
    assert "paired_istd_anchor_support" not in projection.support_reasons


def test_paired_analyte_role_support_without_area_ratio_keeps_nl_fail_not_counted() -> (
    None
):
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=("ms1_coherent", "role_aware_rt_support"),
        conflict_reasons=("candidate_aligned_ms2_nl_conflict",),
        nl_status="NL_FAIL",
        paired_istd_anchor_rt=8.9,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "not_counted"
    assert projection.counted_detection is False
    assert "missing_nl_support_review" in projection.review_reasons
    assert "cid_nl_method_not_supported" in projection.not_counted_reasons
    assert "role_aware_rt_support" in projection.support_reasons
    assert "paired_area_ratio_support" not in projection.support_reasons
    assert "paired_istd_anchor_support" not in projection.support_reasons


def test_paired_analyte_nl_fail_with_quality_flags_stays_not_counted() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=(),
        conflict_reasons=("candidate_aligned_ms2_nl_conflict",),
        nl_status="NL_FAIL",
        paired_istd_anchor_rt=8.5,
        quality_flags=("low_scan_support",),
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "not_counted"
    assert projection.counted_detection is False
    assert "missing_nl_support_review" in projection.review_reasons
    assert "cid_nl_method_not_supported" in projection.not_counted_reasons


def test_paired_analyte_reference_rt_without_istd_anchor_is_not_counted() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=(),
        conflict_reasons=("candidate_aligned_ms2_nl_conflict",),
        nl_status="NL_FAIL",
        selection_reference_rt=8.5,
        paired_istd_anchor_rt=None,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "not_counted"
    assert projection.counted_detection is False
    assert "missing_nl_support_review" in projection.review_reasons
    assert "paired_istd_anchor_support" not in projection.support_reasons
    assert "cid_nl_method_not_supported" in projection.not_counted_reasons


@pytest.mark.parametrize("is_istd", [False, True])
def test_confirmed_rt_drift_is_context_not_review(is_istd) -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=is_istd),
        support_reasons=("candidate_aligned_ms2_nl",),
        conflict_reasons=("targeted_rt_conflict", "anchor_conflict"),
        review_reasons=("targeted_rt_review", "paired_istd_rt_review"),
    )
    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.counted_detection is True
    assert projection.product_state == "detected_clean"
    assert projection.review_state == "none"
    assert projection.review_reasons == ()
    assert projection.conflict_reasons == ()
    assert result.reported_rt == 8.5
    assert result.reported_peak_area == 1234.5


@pytest.mark.parametrize("quality_review", [(), ("trace_morphology_review",)])
def test_confirmed_backfill_resolves_dropout_review_but_not_quality(quality_review):
    from xic_extractor.extraction.targeted_product_projection import (
        build_targeted_product_projection,
    )
    from xic_extractor.output.targeted_product_model import TargetedProductContext

    projection = build_targeted_product_projection(
        TargetedProductContext(role="ISTD", neutral_loss_required=True),
        rt=9.1,
        area=1234.5,
        backfill_supported=True,
        review_reasons=(
            "targeted_rt_review",
            "plausible_nl_dropout_review",
            "missing_ms2_not_observed",
            "backfill_identity_confirmed",
            *quality_review,
        ),
    )
    assert projection.counted_detection is True
    assert projection.review_reasons == quality_review
    assert projection.review_state == ("flagged" if quality_review else "none")
    assert "cross_sample_backfill_identity" in projection.support_reasons


def test_display_reason_does_not_decide_counting() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=(),
        reason="diagnostic review note",
        not_counted_reasons=(),
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.counted_detection is True
    assert projection.product_state == "detected_clean"
    assert projection.review_reasons == ()
    assert projection.not_counted_reasons == ()


def test_analyte_no_ms2_still_requires_policy() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=("ms1_coherent",),
        conflict_reasons=(),
        nl_status="NO_MS2",
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "not_counted"
    assert projection.counted_detection is False
    assert "cid_nl_method_not_supported" in projection.not_counted_reasons


def test_istd_raw_hard_quality_flag_with_product_support_stays_counted() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=True),
        support_reasons=("candidate_aligned_ms2_nl", "role_aware_rt_support"),
        conflict_reasons=(),
        quality_flags=("shape_poor",),
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_flagged"
    assert projection.counted_detection is True
    assert "hard_quality_flag:shape_poor" not in projection.conflict_reasons
    assert "trace_morphology_review" in projection.review_reasons


def test_rna_containing_strict_nl_target_downgrades_targeted_rt_conflict() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(sample_applicability="rna_containing"),
        sample_name="TumorBC2304_DNAandRNA",
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=("targeted_rt_conflict",),
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_clean"
    assert projection.counted_detection is True
    assert "targeted_rt_conflict" not in projection.conflict_reasons
    assert "targeted_rt_review" not in projection.review_reasons


def test_strict_nl_analyte_does_not_treat_rt_prior_as_review() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(),
        sample_name="STDS",
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=("targeted_rt_conflict",),
    )
    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_clean"
    assert projection.counted_detection is True
    assert "targeted_rt_conflict" not in projection.conflict_reasons
    assert "targeted_rt_review" not in projection.review_reasons


@pytest.mark.parametrize(
    "sample_name", ["EC013_2", "U00001ZBEE", "QC1", "Tumor_DNA", "Tumor_RNA"]
)
@pytest.mark.parametrize(
    "cid_nl_tag,neutral_loss_da", [("dR", 116.0474), ("R", 132.0423), ("MeR", 146.0579)]
)
def test_targeted_configured_targets_ignore_sample_name_and_legacy_applicability(
    sample_name, cid_nl_tag, neutral_loss_da
) -> None:
    result = _result_with_targeted_projection_semantics(
        target=replace(
            _target(sample_applicability="rna_containing"),
            cid_nl_tag=cid_nl_tag,
            neutral_loss_da=neutral_loss_da,
        ),
        sample_name=sample_name,
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=(),
    )
    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_clean"
    assert projection.counted_detection is True
    assert projection.exclusion_reasons == ()


class _Config:
    resolver_mode = "local_minimum"
    baseline_integration_method = "asls"
    count_no_ms2_as_detected = False
    ms1_morphology_smoothing_window_points = 15


def _target(
    *,
    is_istd: bool = False,
    sample_applicability: str = "all",
) -> Target:
    return Target(
        label="ISTD" if is_istd else "Analyte",
        mz=258.1085,
        rt_min=8.0,
        rt_max=9.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=is_istd,
        istd_pair="ISTD",
        sample_applicability=sample_applicability,
    )


def _candidate(quality_flags: tuple[str, ...] = ("too_broad",)) -> PeakCandidate:
    peak = PeakResult(
        rt=8.5,
        intensity=1200.0,
        intensity_smoothed=1100.0,
        area=1234.5,
        peak_start=8.4,
        peak_end=8.6,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=8.5,
        selection_apex_intensity=1100.0,
        selection_apex_index=1,
        raw_apex_rt=8.5,
        raw_apex_intensity=1200.0,
        raw_apex_index=1,
        prominence=700.0,
        quality_flags=quality_flags,
    )


def _clean_typed_facts(candidate: PeakCandidate) -> CandidateEvidenceFacts:
    rt_array = np.linspace(8.0, 9.0, 201)
    intensity_array = 1200.0 * np.exp(-(((rt_array - 8.5) / 0.05) ** 2)) + 5.0
    return build_candidate_evidence_facts(
        candidate,
        ScoringContext(
            rt_array=rt_array,
            intensity_array=intensity_array,
            apex_index=100,
            signal_quality=SignalQuality("available", 1.0, 100.0, False),
            half_width_ratio=1.0,
            fwhm_ratio=1.0,
            ms2_present=True,
            nl_match=True,
            rt_prior=8.5,
            rt_prior_sigma=0.1,
            rt_min=8.0,
            rt_max=9.0,
        ),
    )


def _peak_result_with_candidate() -> PeakDetectionResult:
    candidate = _candidate()
    return PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=12,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
        reason="concerns: local_sn",
        severities=((1, "local_sn"),),
        score_breakdown=(("Raw Score", "41"),),
        candidate_scores=(_score(candidate),),
    )


def _result_with_targeted_projection_semantics(
    *,
    target: Target,
    sample_name: str = "SampleA",
    support_reasons: tuple[str, ...],
    conflict_reasons: tuple[str, ...],
    reason: str = "decision: accepted",
    review_reasons: tuple[str, ...] = (),
    not_counted_reasons: tuple[str, ...] = (),
    quality_flags: tuple[str, ...] = (),
    nl_status: str = "OK",
    selection_reference_rt: float | None = None,
    paired_istd_anchor_rt: float | None = None,
    physical_evaluation: PhysicalPeakEvaluation | None = None,
) -> ExtractionResult:
    candidate = _candidate(quality_flags=())
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=12,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
        reason=reason,
        candidate_scores=(
            PeakCandidateScore(
                candidate=candidate,
                reason=reason,
                raw_score=95,
                support_labels=("local_sn_strong", "shape_clean"),
            ),
        ),
        selection_reference_rt=selection_reference_rt,
        paired_istd_anchor_rt=paired_istd_anchor_rt,
    )
    selected = PeakHypothesis(
        hypothesis_id=f"{sample_name}|{target.label}|selected",
        trace_group_id=f"{sample_name}|{target.label}|targeted",
        target_label=target.label,
        role="ISTD" if target.is_istd else "Analyte",
        istd_pair=target.istd_pair,
        analysis_mode="targeted",
        resolver_mode="local_minimum",
        integration=(
            physical_evaluation.integration
            if physical_evaluation is not None
            and physical_evaluation.integration is not None
            else IntegrationResult(
                rt_left_min=8.4,
                rt_apex_min=8.5,
                rt_right_min=8.6,
                raw_apex_rt_min=8.5,
                rt_width_min=0.2,
                height_raw=1200.0,
                height_smoothed=1100.0,
                area_raw_counts_seconds=1234.5,
            )
        ),
        evidence=EvidenceVector(
            reason=reason,
            quality_flags=quality_flags,
            decision_semantics=EvidenceDecisionSemantics(
                decision_class="ambiguous",
                support_reasons=support_reasons,
                review_reasons=review_reasons,
                conflict_reasons=conflict_reasons,
                not_counted_reasons=not_counted_reasons,
            ),
        ),
        audit=AuditTrail(selected=True),
    )
    return build_extraction_result(
        peak_result=peak_result,
        nl_result=NLResult(nl_status, 2.5 if nl_status == "OK" else None, 8.5, 1, 0, 1),
        candidate_ms2_evidence=(
            CandidateMS2Evidence(
                ms2_present=nl_status != "NO_MS2",
                nl_match=nl_status == "OK",
                nl_status=nl_status,
                trigger_scan_count=0 if nl_status == "NO_MS2" else 1,
                strict_nl_scan_count=1 if nl_status == "OK" else 0,
                best_loss_ppm=2.5 if nl_status == "OK" else None,
                best_scan_rt=8.5,
                best_product_base_ratio=0.8 if nl_status == "OK" else None,
                alignment_source="region",
            )
        ),
        target=target,
        candidate=candidate,
        scoring_context_builder=None,
        selected_hypothesis=selected,
        physical_evaluation=physical_evaluation,
        sample_name=sample_name,
        cid_nl_relations=_method_relations(target, candidate, nl_status),
        selected_cid_nl_candidate_index=0,
    )


def _method_relations(target: Target, candidate: PeakCandidate, nl_status: str = "OK"):
    return evaluate_cid_nl_method_relations(
        candidate_intervals=(
            CidNlCandidateInterval(
                target.mz, candidate.peak.peak_start, candidate.peak.peak_end
            ),
        ),
        event_queries=(
            CidNlEventQuery(
                PrecursorLossAssessment(
                    "compatible" if nl_status == "OK" else "neutral_loss_mismatch",
                    "scan_precursor",
                    target.mz,
                    target.neutral_loss_da,
                    0.0,
                    "unknown",
                ),
                candidate.selection_apex_rt,
                candidate.selection_apex_rt,
            ),
        ),
        policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=target.mz * target.ppm_tol / 1e6,
            neutral_loss_tolerance_ppm=target.nl_ppm_max,
        ),
    )


def _physical_evaluation() -> PhysicalPeakEvaluation:
    rt = np.linspace(8.0, 9.0, 201)
    intensity = 10.0 + 1200.0 * np.exp(-0.5 * ((rt - 8.5) / 0.06) ** 2)
    prepared = prepare_physical_signal(
        rt,
        intensity,
        profile=SignalProcessingProfile(window_points=15),
    )
    evaluation = evaluate_physical_peak(
        prepared,
        query_rt_min=8.5,
        request_context_rt_start_min=8.0,
        request_context_rt_end_min=9.0,
    )
    assert evaluation.integration is not None
    return evaluation


def _selected_hypothesis_with_integration(
    integration: IntegrationResult,
    *,
    hypothesis_id: str = "SampleA|Analyte|selected",
    selected: bool = True,
    support_reasons: tuple[str, ...] = (),
) -> PeakHypothesis:
    return PeakHypothesis(
        hypothesis_id=hypothesis_id,
        trace_group_id="SampleA|Analyte|targeted",
        target_label="Analyte",
        role="Analyte",
        istd_pair="ISTD",
        analysis_mode="targeted",
        resolver_mode="local_minimum",
        integration=integration,
        evidence=EvidenceVector(
            reason="selected spine",
            decision_semantics=EvidenceDecisionSemantics(
                decision_class="review",
                support_reasons=support_reasons,
                review_reasons=("trace_morphology_review",),
            ),
        ),
        audit=AuditTrail(selected=selected),
    )


def _score(candidate: PeakCandidate) -> PeakCandidateScore:
    return PeakCandidateScore(
        candidate=candidate,
        reason="concerns: local_sn",
        raw_score=41,
        concern_labels=("local_sn",),
    )


def _ms2_evidence() -> CandidateMS2Evidence:
    return CandidateMS2Evidence(
        ms2_present=True,
        nl_match=False,
        nl_status="NL_FAIL",
        trigger_scan_count=1,
        strict_nl_scan_count=0,
        best_loss_ppm=125.0,
        best_scan_rt=8.5,
        best_product_base_ratio=0.4,
        alignment_source="region",
    )


@pytest.mark.parametrize("is_istd", [False, True])
def test_method_supported_quantifiable_peak_reviews_shape_not_rt(
    is_istd: bool,
) -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=is_istd),
        support_reasons=("candidate_aligned_ms2_nl",),
        conflict_reasons=(
            "targeted_rt_conflict",
            "anchor_conflict",
            "hard_local_quality_conflict",
            "trace_morphology_conflict",
        ),
        quality_flags=("too_broad",),
    )
    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.counted_detection is True
    assert projection.conflict_reasons == ()
    assert "targeted_rt_review" not in projection.review_reasons
    assert "trace_morphology_review" in projection.review_reasons
    assert result.reported_peak_area == 1234.5
