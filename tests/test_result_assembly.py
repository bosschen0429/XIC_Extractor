import numpy as np
import pytest

from xic_extractor.config import Target
from xic_extractor.evidence_semantics import EvidenceDecisionSemantics
from xic_extractor.extraction.handoff_spine_runtime import (
    build_production_peak_hypotheses,
    selected_peak_hypothesis,
)
from xic_extractor.extraction.result_assembly import build_extraction_result
from xic_extractor.extraction.targeted_projection_reasons import (
    OWN_MAX_SAME_PEAK_SUPPORT_REASON,
)
from xic_extractor.extractor import ExtractionResult
from xic_extractor.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.peak_detection.evidence_facts import (
    CandidateEvidenceFacts,
    build_candidate_evidence_facts,
)
from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.model_selection import PeakModelSelectionResult
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidateScore,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.scoring_models import ScoringContext
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
)


def test_build_extraction_result_keeps_high_fallback_without_scoring_confidence() -> (
    None
):
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
    assert selected.evidence.confidence == ""
    assert result.selection_decision is not None
    assert result.selection_decision.projected_confidence == "HIGH"
    assert result.confidence == "HIGH"
    assert result.reason == ""


def test_build_extraction_result_uses_typed_projection_when_confidence_stale() -> None:
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
        confidence="VERY_LOW",
        reason="decision: review only, not counted; cap: VERY_LOW",
        candidate_scores=(
            PeakCandidateScore(
                candidate=candidate,
                confidence="HIGH",
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
    assert selected.evidence.confidence == "VERY_LOW"
    assert result.selection_decision is not None
    assert result.selection_decision.projected_confidence == "HIGH"
    assert (
        result.selection_decision.projected_reason
        == "decision: accepted; evidence: ms1_coherent; candidate_aligned_ms2_nl; "
        "role_aware_rt_support"
    )
    assert result.confidence == "HIGH"
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
        projected_confidence="MEDIUM",
        projected_reason="decision projection",
        review_reasons=("explicit_decision_projection",),
        not_counted_reasons=(
            "selected_envelope_boundary_defer",
            "legacy_review_only_projection",
        ),
        legacy_projection_status="active_policy_remaining",
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
    assert result.confidence == "MEDIUM"
    assert result.reason == "decision projection"
    assert result.targeted_product_projection is not None
    assert result.targeted_product_projection.product_state == "not_counted"
    assert result.targeted_product_projection.counted_detection is False
    assert (
        "selected_envelope_boundary_defer"
        in result.targeted_product_projection.not_counted_reasons
    )
    assert (
        "legacy_review_only_projection"
        not in result.targeted_product_projection.not_counted_reasons
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


def test_extraction_result_projects_approved_expected_diff_successor_values() -> None:
    peak_result = _peak_result_with_candidate()
    legacy_middle = _selected_hypothesis_with_integration(
        IntegrationResult(
            rt_left_min=16.2,
            rt_apex_min=16.43,
            rt_right_min=16.6,
            raw_apex_rt_min=16.43,
            rt_width_min=0.4,
            height_raw=16_000.0,
            height_smoothed=15_000.0,
            area_raw_counts_seconds=1_000.0,
        ),
        hypothesis_id="BenignfatBC1055_DNA|8-oxodG|legacy-middle",
        selected=True,
        selection_rank=1,
    )
    successor_right = _selected_hypothesis_with_integration(
        IntegrationResult(
            rt_left_min=17.0,
            rt_apex_min=17.18,
            rt_right_min=17.35,
            raw_apex_rt_min=17.18,
            rt_width_min=0.35,
            height_raw=125_000.0,
            height_smoothed=86_000.0,
            area_raw_counts_seconds=50_000.0,
        ),
        hypothesis_id="BenignfatBC1055_DNA|8-oxodG|successor-right",
        selected=False,
        selection_rank=2,
        support_reasons=(
            "ms1_coherent",
            "role_aware_rt_support",
            "paired_area_ratio_support",
        ),
    )
    model_selection = PeakModelSelectionResult(
        selected_candidate_id=successor_right.hypothesis_id,
        legacy_selected_candidate_id=legacy_middle.hypothesis_id,
        stable_row_id=(
            "model_selection|legacy=BenignfatBC1055_DNA|8-oxodG|legacy-middle"
            "|successor=BenignfatBC1055_DNA|8-oxodG|successor-right"
        ),
        trace_group_id=successor_right.trace_group_id,
        decision_class="review",
        selection_status="expected_diff",
        selection_reasons=("paired_area_ratio_support", "role_aware_rt_support"),
        legacy_reasons=("legacy_rt_anchor",),
        diff_reasons=(),
        public_projection={"confidence": "MEDIUM"},
        evidence_sources=("ms1_trace", "role_aware_rt", "paired_area_ratio"),
        compatibility_oracle="legacy_peak_scoring_current_oracle",
        policy_source="selected_hypothesis_model_selection_v1",
        product_switch_allowed=True,
        evidence_comparison_policy="complete_candidate_evidence",
    )
    decision = PeakHypothesisSelectionDecision(
        selected_candidate_id=successor_right.hypothesis_id,
        trace_group_id=successor_right.trace_group_id,
        decision_class="review",
        projected_confidence="MEDIUM",
        projected_reason="approved expected-diff successor projection",
        support_reasons=(
            "ms1_coherent",
            "role_aware_rt_support",
            "paired_area_ratio_support",
        ),
        review_reasons=("paired_analyte_nl_review",),
    )

    result = build_extraction_result(
        peak_result=peak_result,
        nl_result=NLResult("NL_FAIL", None, 17.18, 1, 0, 1),
        candidate_ms2_evidence=_ms2_evidence(),
        target=_target(),
        candidate=peak_result.candidates[0],
        scoring_context_builder=None,
        selected_hypothesis=successor_right,
        selection_decision=decision,
        model_selection_result=model_selection,
    )

    assert result.model_selection_result is model_selection
    assert result.selected_hypothesis is successor_right
    assert result.reported_rt == 17.18
    assert result.reported_peak_area == 50_000.0
    assert result.reported_peak_start == 17.0
    assert result.reported_peak_end == 17.35
    assert result.confidence == "MEDIUM"
    assert result.reason == "approved expected-diff successor projection"


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
        confidence="VERY_LOW",
        reason="decision: review only, not counted; cap: nl fail",
        severities=((3, "nl_support"),),
        candidate_scores=(
            PeakCandidateScore(
                candidate=candidate,
                confidence="VERY_LOW",
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
            selected_candidate_ms2_evidence=_ms2_evidence(),
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
    assert result.confidence == "VERY_LOW"
    assert result.nl_token == "NL_FAIL"
    assert projection.product_state == "detected_flagged"
    assert projection.counted_detection is True
    assert "plausible_dda_nl_dropout" in projection.review_reasons


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
        confidence="HIGH",
        reason="decision: accepted",
        candidate_scores=(
            PeakCandidateScore(
                candidate=candidate,
                confidence="HIGH",
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
        resolver_mode="region_first_safe_merge",
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
            confidence="HIGH",
            reason="decision: accepted",
            decision_semantics=EvidenceDecisionSemantics(
                decision_class="ambiguous",
                support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
                conflict_reasons=("targeted_rt_conflict",),
                review_reasons=("targeted_rt_review",),
            ),
        ),
        audit=AuditTrail(selected=True, selection_rank=1),
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
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_flagged"
    assert projection.counted_detection is True
    assert "targeted_rt_conflict" not in projection.conflict_reasons
    assert "targeted_rt_review" in projection.review_reasons


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


def test_istd_rt_conflict_with_nl_dropout_stays_counted_when_ms1_coherent() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=True),
        support_reasons=("ms1_coherent",),
        conflict_reasons=("targeted_rt_conflict",),
        review_reasons=("plausible_nl_dropout_review",),
        nl_status="NL_FAIL",
        confidence="VERY_LOW",
        reason="decision: review only, not counted; cap: VERY_LOW due to nl fail",
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_flagged"
    assert projection.counted_detection is True
    assert "targeted_rt_conflict" not in projection.conflict_reasons
    assert "targeted_rt_review" in projection.review_reasons
    assert "plausible_dda_nl_dropout" in projection.review_reasons
    assert "istd_nl_fail_without_dropout_support" not in projection.not_counted_reasons
    assert "role_aware_istd_expected_present" in projection.support_reasons


def test_analyte_trace_conflict_does_not_use_istd_projection_escape() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=("trace_morphology_conflict",),
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "ambiguous"
    assert projection.counted_detection is False
    assert "trace_morphology_conflict" in projection.conflict_reasons


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


def test_paired_analyte_anchor_conflict_blocks_trace_downgrade() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=("candidate_aligned_ms2_nl",),
        conflict_reasons=("anchor_conflict", "trace_morphology_conflict"),
        selection_reference_rt=8.5,
        paired_istd_anchor_rt=8.9,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "ambiguous"
    assert projection.counted_detection is False
    assert "anchor_conflict" in projection.conflict_reasons
    assert "trace_morphology_review" in projection.review_reasons


def test_paired_analyte_anchor_selected_nl_fail_requires_area_ratio_support() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=(),
        conflict_reasons=("candidate_aligned_ms2_nl_conflict",),
        nl_status="NL_FAIL",
        paired_istd_anchor_rt=8.5,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "ambiguous"
    assert projection.counted_detection is False
    assert "candidate_aligned_ms2_nl_conflict" in projection.conflict_reasons
    assert "analyte_nl_fail_requires_policy" in projection.not_counted_reasons
    assert "paired_istd_anchor_support" in projection.support_reasons


def test_paired_analyte_anchor_ratio_no_own_max_keeps_nl_fail_not_counted() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=("ms1_coherent", "paired_area_ratio_support"),
        conflict_reasons=("candidate_aligned_ms2_nl_conflict",),
        nl_status="NL_FAIL",
        paired_istd_anchor_rt=8.5,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "ambiguous"
    assert projection.counted_detection is False
    assert "candidate_aligned_ms2_nl_conflict" in projection.conflict_reasons
    assert "analyte_nl_fail_requires_policy" in projection.not_counted_reasons
    assert "paired_analyte_nl_review" in projection.review_reasons
    assert "paired_istd_anchor_support" in projection.support_reasons
    assert "paired_area_ratio_support" in projection.support_reasons
    assert OWN_MAX_SAME_PEAK_SUPPORT_REASON not in projection.support_reasons


def test_paired_analyte_anchor_ratio_own_max_downgrades_nl_fail_to_review() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=(
            "ms1_coherent",
            "paired_area_ratio_support",
            OWN_MAX_SAME_PEAK_SUPPORT_REASON,
        ),
        conflict_reasons=("candidate_aligned_ms2_nl_conflict",),
        nl_status="NL_FAIL",
        paired_istd_anchor_rt=8.5,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_flagged"
    assert projection.counted_detection is True
    assert "candidate_aligned_ms2_nl_conflict" not in projection.conflict_reasons
    assert "analyte_nl_fail_requires_policy" not in projection.not_counted_reasons
    assert "paired_analyte_nl_review" in projection.review_reasons
    assert "paired_istd_anchor_support" in projection.support_reasons
    assert "paired_area_ratio_support" in projection.support_reasons
    assert OWN_MAX_SAME_PEAK_SUPPORT_REASON in projection.support_reasons


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
    assert projection.product_state == "ambiguous"
    assert projection.counted_detection is False
    assert "candidate_aligned_ms2_nl_conflict" in projection.conflict_reasons
    assert "analyte_nl_fail_requires_policy" in projection.not_counted_reasons


def test_paired_analyte_role_support_without_own_max_keeps_nl_fail_not_counted() -> (
    None
):
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
    assert projection.product_state == "ambiguous"
    assert projection.counted_detection is False
    assert "candidate_aligned_ms2_nl_conflict" in projection.conflict_reasons
    assert "analyte_nl_fail_requires_policy" in projection.not_counted_reasons
    assert "paired_analyte_nl_review" in projection.review_reasons
    assert "role_aware_rt_support" in projection.support_reasons
    assert "paired_area_ratio_support" in projection.support_reasons
    assert "paired_istd_anchor_support" not in projection.support_reasons
    assert OWN_MAX_SAME_PEAK_SUPPORT_REASON not in projection.support_reasons


def test_paired_analyte_role_support_with_own_max_downgrades_nl_fail() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=(
            "ms1_coherent",
            "role_aware_rt_support",
            "paired_area_ratio_support",
            OWN_MAX_SAME_PEAK_SUPPORT_REASON,
        ),
        conflict_reasons=("candidate_aligned_ms2_nl_conflict",),
        nl_status="NL_FAIL",
        paired_istd_anchor_rt=8.9,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_flagged"
    assert projection.counted_detection is True
    assert "candidate_aligned_ms2_nl_conflict" not in projection.conflict_reasons
    assert "analyte_nl_fail_requires_policy" not in projection.not_counted_reasons
    assert "paired_analyte_nl_review" in projection.review_reasons
    assert "role_aware_rt_support" in projection.support_reasons
    assert "paired_area_ratio_support" in projection.support_reasons
    assert OWN_MAX_SAME_PEAK_SUPPORT_REASON in projection.support_reasons
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
    assert projection.product_state == "ambiguous"
    assert projection.counted_detection is False
    assert "candidate_aligned_ms2_nl_conflict" in projection.conflict_reasons
    assert "analyte_nl_fail_requires_policy" in projection.not_counted_reasons
    assert "role_aware_rt_support" in projection.support_reasons
    assert "paired_area_ratio_support" not in projection.support_reasons
    assert "paired_istd_anchor_support" not in projection.support_reasons


def test_approved_expected_diff_pair_evidence_downgrades_analyte_nl_fail() -> None:
    model_selection = PeakModelSelectionResult(
        selected_candidate_id="SampleA|Analyte|successor-right",
        legacy_selected_candidate_id="SampleA|Analyte|legacy-middle",
        stable_row_id=(
            "model_selection|legacy=SampleA|Analyte|legacy-middle"
            "|successor=SampleA|Analyte|successor-right"
        ),
        trace_group_id="SampleA|Analyte|targeted",
        decision_class="not_counted",
        selection_status="expected_diff",
        selection_reasons=("ms1_coherent",),
        legacy_reasons=("legacy_rt_anchor",),
        diff_reasons=(),
        public_projection={"confidence": "VERY_LOW"},
        evidence_sources=(
            "ms1_trace",
            "chrom_peak_segment_context",
            "role_aware_rt",
            "paired_area_ratio",
            "own_max_same_peak",
        ),
        compatibility_oracle="legacy_peak_scoring_current_oracle",
        policy_source="selected_hypothesis_model_selection_v1",
        product_switch_allowed=True,
        evidence_comparison_policy="limited_evidence_shadow",
    )
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=("ms1_coherent",),
        conflict_reasons=(),
        nl_status="NL_FAIL",
        confidence="VERY_LOW",
        model_selection_result=model_selection,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_flagged"
    assert projection.counted_detection is True
    assert "role_aware_rt_support" in projection.support_reasons
    assert "paired_area_ratio_support" in projection.support_reasons
    assert OWN_MAX_SAME_PEAK_SUPPORT_REASON in projection.support_reasons
    assert "analyte_nl_fail_requires_policy" not in projection.not_counted_reasons


def test_expected_diff_pair_evidence_without_own_max_keeps_nl_fail_not_counted() -> (
    None
):
    model_selection = PeakModelSelectionResult(
        selected_candidate_id="SampleA|Analyte|successor-right",
        legacy_selected_candidate_id="SampleA|Analyte|legacy-middle",
        stable_row_id=(
            "model_selection|legacy=SampleA|Analyte|legacy-middle"
            "|successor=SampleA|Analyte|successor-right"
        ),
        trace_group_id="SampleA|Analyte|targeted",
        decision_class="not_counted",
        selection_status="expected_diff",
        selection_reasons=("ms1_coherent",),
        legacy_reasons=("legacy_rt_anchor",),
        diff_reasons=(),
        public_projection={"confidence": "VERY_LOW"},
        evidence_sources=(
            "ms1_trace",
            "chrom_peak_segment_context",
            "role_aware_rt",
            "paired_area_ratio",
        ),
        compatibility_oracle="legacy_peak_scoring_current_oracle",
        policy_source="selected_hypothesis_model_selection_v1",
        product_switch_allowed=True,
        evidence_comparison_policy="limited_evidence_shadow",
    )
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=("ms1_coherent",),
        conflict_reasons=(),
        nl_status="NL_FAIL",
        confidence="VERY_LOW",
        model_selection_result=model_selection,
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "not_counted"
    assert projection.counted_detection is False
    assert "role_aware_rt_support" in projection.support_reasons
    assert "paired_area_ratio_support" in projection.support_reasons
    assert OWN_MAX_SAME_PEAK_SUPPORT_REASON not in projection.support_reasons
    assert "analyte_nl_fail_requires_policy" in projection.not_counted_reasons


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
    assert projection.product_state == "ambiguous"
    assert projection.counted_detection is False
    assert "candidate_aligned_ms2_nl_conflict" in projection.conflict_reasons
    assert "analyte_nl_fail_requires_policy" in projection.not_counted_reasons


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
    assert projection.product_state == "ambiguous"
    assert projection.counted_detection is False
    assert "candidate_aligned_ms2_nl_conflict" in projection.conflict_reasons
    assert "paired_istd_anchor_support" not in projection.support_reasons
    assert "analyte_nl_fail_requires_policy" in projection.not_counted_reasons


def test_legacy_very_low_does_not_decide_product_not_counted() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(is_istd=False),
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=(),
        confidence="VERY_LOW",
        reason="decision: review only, not counted; cap: legacy score",
        not_counted_reasons=("legacy_review_only_projection",),
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.counted_detection is True
    assert projection.product_state == "detected_flagged"
    assert "legacy_confidence_review" in projection.review_reasons
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
    assert "analyte_missing_ms2_requires_policy" in projection.not_counted_reasons


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
    assert projection.product_state == "detected_flagged"
    assert projection.counted_detection is True
    assert "targeted_rt_conflict" not in projection.conflict_reasons
    assert "targeted_rt_review" in projection.review_reasons


def test_strict_nl_analyte_keeps_rt_prior_conflict_as_review() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(),
        sample_name="STDS",
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=("targeted_rt_conflict",),
    )
    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_flagged"
    assert projection.counted_detection is True
    assert "targeted_rt_conflict" not in projection.conflict_reasons
    assert "targeted_rt_review" in projection.review_reasons


def test_rna_containing_target_is_excluded_in_pure_dna_sample() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(sample_applicability="rna_containing"),
        sample_name="TumorBC2306_DNA",
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=(),
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "excluded"
    assert projection.counted_detection is False
    assert "target_sample_applicability:rna_containing" in projection.exclusion_reasons


def test_rna_containing_target_stays_eligible_in_dnaandrna_sample() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(sample_applicability="rna_containing"),
        sample_name="TumorBC2304_DNAandRNA",
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=(),
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_clean"
    assert projection.counted_detection is True
    assert projection.exclusion_reasons == ()


def test_rna_containing_target_stays_eligible_in_rna_sample() -> None:
    result = _result_with_targeted_projection_semantics(
        target=_target(sample_applicability="rna_containing"),
        sample_name="TumorBC2304_RNA",
        support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
        conflict_reasons=(),
    )

    projection = result.targeted_product_projection
    assert projection is not None
    assert projection.product_state == "detected_clean"
    assert projection.counted_detection is True
    assert projection.exclusion_reasons == ()


class _Config:
    resolver_mode = "region_first_safe_merge"


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
            half_width_ratio=1.0,
            fwhm_ratio=1.0,
            ms2_present=True,
            nl_match=True,
            rt_prior=8.5,
            rt_prior_sigma=0.1,
            rt_min=8.0,
            rt_max=9.0,
            dirty_matrix=False,
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
        confidence="LOW",
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
    confidence: str = "HIGH",
    reason: str = "decision: accepted",
    review_reasons: tuple[str, ...] = (),
    not_counted_reasons: tuple[str, ...] = (),
    quality_flags: tuple[str, ...] = (),
    nl_status: str = "OK",
    selection_reference_rt: float | None = None,
    paired_istd_anchor_rt: float | None = None,
    model_selection_result: PeakModelSelectionResult | None = None,
) -> ExtractionResult:
    candidate = _candidate(quality_flags=())
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=12,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
        confidence=confidence,
        reason=reason,
        candidate_scores=(
            PeakCandidateScore(
                candidate=candidate,
                confidence=confidence,
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
        resolver_mode="region_first_safe_merge",
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
            confidence=confidence,
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
        audit=AuditTrail(selected=True, selection_rank=1),
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
        model_selection_result=model_selection_result,
        sample_name=sample_name,
    )


def _selected_hypothesis_with_integration(
    integration: IntegrationResult,
    *,
    hypothesis_id: str = "SampleA|Analyte|selected",
    selected: bool = True,
    selection_rank: int = 1,
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
            confidence="LOW",
            reason="selected spine",
            decision_semantics=EvidenceDecisionSemantics(
                decision_class="review",
                support_reasons=support_reasons,
                review_reasons=("trace_morphology_review",),
                compatibility_labels=("trace_quality_cap",),
            ),
        ),
        audit=AuditTrail(selected=selected, selection_rank=selection_rank),
    )


def _score(candidate: PeakCandidate) -> PeakCandidateScore:
    return PeakCandidateScore(
        candidate=candidate,
        confidence="LOW",
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
