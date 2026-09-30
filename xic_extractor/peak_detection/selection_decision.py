from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from xic_extractor.evidence_semantics import DecisionClass
from xic_extractor.peak_detection.hypotheses import (
    PeakHypothesis,
    validate_peak_hypothesis_id_selection_authority,
    validate_peak_hypothesis_selection_authority,
)
from xic_extractor.peak_detection.models import PeakDetectionResult

LegacyProjectionStatus = Literal["active_policy_remaining", "successor_owned"]
SelectionDecisionPolicySource = Literal["selected_hypothesis_decision"]
SelectionDecisionCompatibilityOracle = Literal[
    "legacy_peak_scoring_current_oracle",
    "successor_evidence_decision_semantics",
]


@dataclass(frozen=True)
class PeakHypothesisSelectionDecision:
    selected_candidate_id: str
    trace_group_id: str
    decision_class: DecisionClass
    projected_confidence: str
    projected_reason: str
    support_reasons: tuple[str, ...] = ()
    conflict_reasons: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()
    not_counted_reasons: tuple[str, ...] = ()
    exclusion_reasons: tuple[str, ...] = ()
    ambiguity_reasons: tuple[str, ...] = ()
    compatibility_labels: tuple[str, ...] = ()
    evidence_sources: tuple[str, ...] = ()
    legacy_projection_status: LegacyProjectionStatus = "active_policy_remaining"
    policy_source: SelectionDecisionPolicySource = "selected_hypothesis_decision"
    compatibility_oracle: SelectionDecisionCompatibilityOracle = (
        "legacy_peak_scoring_current_oracle"
    )

    def __post_init__(self) -> None:
        validate_peak_hypothesis_id_selection_authority(
            self.selected_candidate_id,
            consumer="PeakHypothesisSelectionDecision",
        )


def selection_decision_from_hypothesis(
    hypothesis: PeakHypothesis,
    *,
    peak_result: PeakDetectionResult | None = None,
    fallback_confidence: str = "",
    fallback_reason: str = "",
) -> PeakHypothesisSelectionDecision:
    validate_peak_hypothesis_selection_authority(
        hypothesis,
        consumer="selection_decision_from_hypothesis",
    )
    if peak_result is not None:
        fallback_confidence = _fallback_confidence(peak_result)
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
        compatibility_labels: tuple[str, ...] = _legacy_compatibility_labels(hypothesis)
    else:
        decision_class = semantics.decision_class
        support_reasons = semantics.support_reasons
        conflict_reasons = semantics.conflict_reasons
        review_reasons = semantics.review_reasons
        not_counted_reasons = semantics.not_counted_reasons
        exclusion_reasons = semantics.exclusion_reasons
        ambiguity_reasons = semantics.ambiguity_reasons
        compatibility_labels = semantics.compatibility_labels

    legacy_projection_status, compatibility_oracle = _ownership_markers(
        not_counted_reasons,
        has_typed_projection=bool(
            evidence.evidence_facts is not None
            or evidence.projected_confidence
            or evidence.projected_reason
        ),
    )

    return PeakHypothesisSelectionDecision(
        selected_candidate_id=hypothesis.hypothesis_id,
        trace_group_id=hypothesis.trace_group_id,
        decision_class=decision_class,
        projected_confidence=(
            evidence.projected_confidence or evidence.confidence or fallback_confidence
        ),
        projected_reason=(
            evidence.projected_reason or evidence.reason or fallback_reason
        ),
        support_reasons=support_reasons,
        conflict_reasons=conflict_reasons,
        review_reasons=review_reasons,
        not_counted_reasons=not_counted_reasons,
        exclusion_reasons=exclusion_reasons,
        ambiguity_reasons=ambiguity_reasons,
        compatibility_labels=compatibility_labels,
        evidence_sources=_evidence_sources(hypothesis),
        legacy_projection_status=legacy_projection_status,
        compatibility_oracle=compatibility_oracle,
    )


def _fallback_confidence(peak_result: PeakDetectionResult) -> str:
    if peak_result.peak is None:
        return ""
    return peak_result.confidence or "HIGH"


def _fallback_reason(peak_result: PeakDetectionResult) -> str:
    return peak_result.reason or ""


def _legacy_compatibility_labels(
    hypothesis: PeakHypothesis,
) -> tuple[str, ...]:
    evidence = hypothesis.evidence
    return tuple(
        dict.fromkeys(
            (
                *evidence.support_labels,
                *evidence.concern_labels,
                *evidence.cap_labels,
                *hypothesis.audit.proposal_sources,
                *evidence.quality_flags,
            )
        )
    )


def _ownership_markers(
    not_counted_reasons: tuple[str, ...],
    *,
    has_typed_projection: bool = False,
) -> tuple[LegacyProjectionStatus, SelectionDecisionCompatibilityOracle]:
    if has_typed_projection or "missing_ms2_policy_not_counted" in not_counted_reasons:
        return "successor_owned", "successor_evidence_decision_semantics"
    return "active_policy_remaining", "legacy_peak_scoring_current_oracle"


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
    if (
        evidence.evidence_facts is not None
        or evidence.projected_confidence
        or evidence.projected_reason
    ):
        sources.append("typed_candidate_evidence_facts")
    if (
        evidence.confidence
        or evidence.reason
        or evidence.raw_score is not None
        or evidence.support_labels
        or evidence.concern_labels
        or evidence.cap_labels
    ):
        sources.append("legacy_compatibility_projection")
    return tuple(dict.fromkeys(sources))
