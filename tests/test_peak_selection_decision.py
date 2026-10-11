from xic_extractor.peak_detection.evidence_semantics import EvidenceDecisionSemantics
from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.models import PeakDetectionResult, PeakResult
from xic_extractor.peak_detection.selection_decision import (
    selection_decision_from_hypothesis,
)


def test_selection_decision_projects_accepted_typed_semantics() -> None:
    hypothesis = _hypothesis(
        evidence=EvidenceVector(
            confidence="HIGH",
            raw_score=95,
            support_labels=("strict_nl_ok", "local_sn_strong"),
            reason="decision: accepted",
            ms2_present=True,
            nl_match=True,
            decision_semantics=EvidenceDecisionSemantics(
                decision_class="accepted",
                support_reasons=("ms1_coherent", "candidate_aligned_ms2_nl"),
                compatibility_labels=("strict_nl_ok", "local_sn_strong"),
            ),
        )
    )

    decision = selection_decision_from_hypothesis(hypothesis)

    assert decision.selected_candidate_id == "SampleA|Analyte|selected"
    assert decision.trace_group_id == "SampleA|Analyte|trace"
    assert decision.decision_class == "accepted"
    assert decision.projected_confidence == "HIGH"
    assert decision.projected_reason == "decision: accepted"
    assert decision.support_reasons == ("ms1_coherent", "candidate_aligned_ms2_nl")
    assert decision.compatibility_labels == ("strict_nl_ok", "local_sn_strong")
    assert decision.legacy_projection_status == "active_policy_remaining"
    assert decision.policy_source == "selected_hypothesis_decision"
    assert decision.compatibility_oracle == "legacy_peak_scoring_current_oracle"
    assert "candidate_aligned_ms2_nl" in decision.evidence_sources
    assert "legacy_compatibility_projection" in decision.evidence_sources


def test_selection_decision_projects_review_and_source_context() -> None:
    hypothesis = _hypothesis(
        evidence=EvidenceVector(
            confidence="MEDIUM",
            support_labels=("cwt_same_apex_support",),
            concern_labels=("rt_prior_far",),
            cap_labels=("rt_window_cap",),
            reason="decision: review",
            quality_flags=("low_trace_continuity",),
            rt_prior_min=8.45,
            cwt_best_scale=4.0,
            region_trace_continuity=0.72,
            decision_semantics=EvidenceDecisionSemantics(
                decision_class="review",
                support_reasons=("cwt_boundary_morphology_context",),
                conflict_reasons=("targeted_rt_conflict",),
                review_reasons=("targeted_rt_review",),
                compatibility_labels=(
                    "cwt_same_apex_support",
                    "rt_window_cap",
                ),
            ),
        ),
        proposal_sources=("centwave_cwt",),
    )

    decision = selection_decision_from_hypothesis(hypothesis)

    assert decision.decision_class == "review"
    assert decision.conflict_reasons == ("targeted_rt_conflict",)
    assert decision.review_reasons == ("targeted_rt_review",)
    assert "role_aware_rt" in decision.evidence_sources
    assert "cwt_boundary_morphology_context" in decision.evidence_sources
    assert "trace_morphology" in decision.evidence_sources


def test_selection_decision_projects_chrom_segment_source_context() -> None:
    hypothesis = _hypothesis(
        evidence=EvidenceVector(
            confidence="HIGH",
            support_labels=("local_sn_strong", "trace_clean"),
            reason="decision: accepted",
            decision_semantics=EvidenceDecisionSemantics(
                decision_class="accepted",
                support_reasons=("ms1_coherent", "chrom_peak_segment_context"),
                compatibility_labels=(
                    "local_sn_strong",
                    "trace_clean",
                    "chrom_peak_segment",
                ),
            ),
        ),
        proposal_sources=("local_minimum", "chrom_peak_segment"),
    )

    decision = selection_decision_from_hypothesis(hypothesis)

    assert decision.decision_class == "accepted"
    assert "chrom_peak_segment_context" in decision.evidence_sources
    assert "trace_morphology" in decision.evidence_sources


def test_selection_decision_marks_successor_owned_no_ms2_policy() -> None:
    hypothesis = _hypothesis(
        evidence=EvidenceVector(
            confidence="VERY_LOW",
            reason="decision: review only, not counted",
            cap_labels=("no_ms2_cap",),
            decision_semantics=EvidenceDecisionSemantics(
                decision_class="not_counted",
                review_reasons=("missing_ms2_not_observed",),
                not_counted_reasons=(
                    "missing_ms2_policy_not_counted",
                    "missing_ms2_compatibility_cap",
                ),
                compatibility_labels=("no_ms2_cap",),
            ),
        )
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=PeakResult(
            rt=8.5,
            intensity=1200.0,
            intensity_smoothed=1100.0,
            area=1234.0,
            peak_start=8.4,
            peak_end=8.6,
        ),
        n_points=10,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        confidence="VERY_LOW",
        reason="decision: review only, not counted",
    )

    decision = selection_decision_from_hypothesis(hypothesis, peak_result=peak_result)

    assert decision.decision_class == "not_counted"
    assert decision.projected_confidence == "VERY_LOW"
    assert decision.projected_reason == "decision: review only, not counted"
    assert decision.not_counted_reasons == (
        "missing_ms2_policy_not_counted",
        "missing_ms2_compatibility_cap",
    )
    assert decision.legacy_projection_status == "successor_owned"
    assert decision.compatibility_oracle == "successor_evidence_decision_semantics"
    assert "candidate_aligned_ms2_nl" in decision.evidence_sources


def test_selection_decision_falls_back_when_typed_semantics_are_missing() -> None:
    hypothesis = _hypothesis(
        evidence=EvidenceVector(
            confidence="LOW",
            reason="legacy reason",
            support_labels=("strict_nl_ok",),
            cap_labels=("trace_quality_cap",),
        )
    )

    decision = selection_decision_from_hypothesis(hypothesis)

    assert decision.decision_class == "review"
    assert decision.review_reasons == ("insufficient_typed_evidence",)
    assert decision.compatibility_labels == (
        "strict_nl_ok",
        "trace_quality_cap",
        "local_minimum",
    )


def _hypothesis(
    *,
    evidence: EvidenceVector,
    proposal_sources: tuple[str, ...] = ("local_minimum",),
) -> PeakHypothesis:
    return PeakHypothesis(
        hypothesis_id="SampleA|Analyte|selected",
        trace_group_id="SampleA|Analyte|trace",
        target_label="Analyte",
        role="Analyte",
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
            area_raw_counts_seconds=1234.0,
        ),
        evidence=evidence,
        audit=AuditTrail(
            proposal_sources=proposal_sources,
            selected=True,
        ),
    )
