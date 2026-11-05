from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from xic_extractor.peak_detection.evidence_semantics import DecisionClass
from xic_extractor.peak_detection.hypotheses import (
    PeakHypothesis,
    validate_peak_hypothesis_selection_authority,
)
from xic_extractor.peak_detection.models import PeakDetectionResult

SelectionDecisionPolicySource = Literal["selected_hypothesis_decision"]


@dataclass(frozen=True)
class PeakHypothesisSelectionDecision:
    selected_candidate_id: str
    trace_group_id: str
    decision_class: DecisionClass
    projected_reason: str
    support_reasons: tuple[str, ...] = ()
    conflict_reasons: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()
    not_counted_reasons: tuple[str, ...] = ()
    exclusion_reasons: tuple[str, ...] = ()
    ambiguity_reasons: tuple[str, ...] = ()
    evidence_sources: tuple[str, ...] = ()
    policy_source: SelectionDecisionPolicySource = "selected_hypothesis_decision"


def selection_decision_from_hypothesis(
    hypothesis: PeakHypothesis,
    *,
    peak_result: PeakDetectionResult | None = None,
    fallback_reason: str = "",
) -> PeakHypothesisSelectionDecision:
    validate_peak_hypothesis_selection_authority(
        hypothesis,
        consumer="selection_decision_from_hypothesis",
    )
    if peak_result is not None:
        fallback_reason = _fallback_reason(peak_result)

    evidence = hypothesis.evidence
    semantics = evidence.decision_semantics
    if semantics is None:
        decision_class: DecisionClass = "review"
        support_reasons: tuple[str, ...] = ()
        conflict_reasons: tuple[str, ...] = ()
        review_reasons: tuple[str, ...] = ("insufficient_typed_evidence",)
        not_counted_reasons: tuple[str, ...] = ()
        exclusion_reasons: tuple[str, ...] = ()
        ambiguity_reasons: tuple[str, ...] = ()
    else:
        decision_class = semantics.decision_class
        support_reasons = semantics.support_reasons
        conflict_reasons = semantics.conflict_reasons
        review_reasons = semantics.review_reasons
        not_counted_reasons = semantics.not_counted_reasons
        exclusion_reasons = semantics.exclusion_reasons
        ambiguity_reasons = semantics.ambiguity_reasons

    return PeakHypothesisSelectionDecision(
        selected_candidate_id=hypothesis.hypothesis_id,
        trace_group_id=hypothesis.trace_group_id,
        decision_class=decision_class,
        projected_reason=(
            evidence.projected_reason or evidence.reason or fallback_reason
        ),
        support_reasons=support_reasons,
        conflict_reasons=conflict_reasons,
        review_reasons=review_reasons,
        not_counted_reasons=not_counted_reasons,
        exclusion_reasons=exclusion_reasons,
        ambiguity_reasons=ambiguity_reasons,
        evidence_sources=_evidence_sources(hypothesis),
    )


def _fallback_reason(peak_result: PeakDetectionResult) -> str:
    return peak_result.reason or ""


def _evidence_sources(hypothesis: PeakHypothesis) -> tuple[str, ...]:
    evidence = hypothesis.evidence
    semantics = evidence.decision_semantics
    reasons = (
        ()
        if semantics is None
        else (
            *semantics.support_reasons,
            *semantics.conflict_reasons,
            *semantics.review_reasons,
            *semantics.not_counted_reasons,
            *semantics.exclusion_reasons,
            *semantics.ambiguity_reasons,
        )
    )
    sources: list[str] = []
    if (
        evidence.common is not None
        or evidence.prominence is not None
        or evidence.region_scan_count is not None
    ):
        sources.append("ms1_trace")
    if (
        evidence.ms2_present is not None
        or evidence.nl_match is not None
        or any("ms2" in reason or "nl" in reason for reason in reasons)
    ):
        sources.append("candidate_aligned_ms2_nl")
    if evidence.rt_prior_min is not None or any("rt" in reason for reason in reasons):
        sources.append("role_aware_rt")
    if (
        evidence.cwt_best_scale is not None
        or evidence.cwt_ridge_persistence is not None
        or "centwave_cwt" in hypothesis.audit.proposal_sources
        or "cwt_boundary_morphology_context" in reasons
    ):
        sources.append("cwt_boundary_morphology_context")
    if (
        "chrom_peak_segment" in hypothesis.audit.proposal_sources
        or "chrom_peak_segment_context" in reasons
    ):
        sources.append("chrom_peak_segment_context")
    if (
        evidence.quality_flags
        or evidence.region_trace_continuity is not None
        or evidence.region_edge_ratio is not None
        or "chrom_peak_segment" in hypothesis.audit.proposal_sources
        or any("trace_morphology" in reason for reason in reasons)
    ):
        sources.append("trace_morphology")
    if evidence.evidence_facts is not None or evidence.projected_reason:
        sources.append("typed_candidate_evidence_facts")
    return tuple(dict.fromkeys(sources))
