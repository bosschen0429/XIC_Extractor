from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from xic_extractor.peak_detection.decision_policy import (
    DECISION_CLASS_RANK,
    DecisionRecord,
    DecisionTerm,
    decision_blockers,
    decision_gate_terms,
)
from xic_extractor.peak_detection.evidence_semantics import DecisionClass
from xic_extractor.peak_detection.hypotheses import (
    PeakHypothesis,
    validate_peak_hypothesis_id_selection_authority,
    validate_peak_hypothesis_selection_authority,
)
from xic_extractor.peak_detection.selection_decision import (
    SelectionDecisionCompatibilityOracle,
)

ModelSelectionStatus = Literal[
    "parity",
    "expected_diff",
    "blocked_diff",
    "inconclusive",
]
EvidenceComparisonPolicy = Literal[
    "complete_candidate_evidence",
    "limited_evidence_shadow",
]
ModelSelectionPolicySource = Literal["selected_hypothesis_model_selection",]
_CONFIDENCE_RANK = {
    "HIGH": 0,
    "MEDIUM": 1,
    "LOW": 2,
    "VERY_LOW": 3,
    "": 4,
}


@dataclass(frozen=True)
class PeakModelSelectionResult:
    selected_candidate_id: str
    legacy_selected_candidate_id: str
    stable_row_id: str
    trace_group_id: str
    decision_class: DecisionClass
    selection_status: ModelSelectionStatus
    selection_reasons: tuple[str, ...]
    legacy_reasons: tuple[str, ...]
    diff_reasons: tuple[str, ...]
    public_projection: Mapping[str, str]
    evidence_sources: tuple[str, ...]
    compatibility_oracle: SelectionDecisionCompatibilityOracle
    policy_source: ModelSelectionPolicySource
    product_switch_allowed: bool
    evidence_comparison_policy: EvidenceComparisonPolicy

    def __post_init__(self) -> None:
        validate_peak_hypothesis_id_selection_authority(
            self.selected_candidate_id,
            consumer="PeakModelSelectionResult",
        )
        if self.legacy_selected_candidate_id:
            validate_peak_hypothesis_id_selection_authority(
                self.legacy_selected_candidate_id,
                consumer="PeakModelSelectionResult",
            )


def expected_diff_stable_row_id(
    *,
    legacy_selected_candidate_id: str,
    successor_selected_candidate_id: str,
) -> str:
    return (
        "model_selection|legacy="
        f"{legacy_selected_candidate_id}"
        f"|successor={successor_selected_candidate_id}"
    )


def model_select_peak_hypothesis(
    hypotheses: tuple[PeakHypothesis, ...],
    *,
    force_selection_status: ModelSelectionStatus | None = None,
    diff_reasons: tuple[str, ...] = (),
    evidence_comparison_policy: EvidenceComparisonPolicy = "limited_evidence_shadow",
) -> PeakModelSelectionResult:
    for hypothesis in hypotheses:
        validate_peak_hypothesis_selection_authority(
            hypothesis,
            consumer="model_select_peak_hypothesis",
        )
    legacy_selected = _legacy_selected_hypothesis(hypotheses)
    if legacy_selected is None:
        return _blocked_result(
            hypotheses,
            status="inconclusive",
            diff_reasons=("missing_legacy_selected_hypothesis", *diff_reasons),
            evidence_comparison_policy=evidence_comparison_policy,
        )

    selected = _successor_selected_hypothesis(hypotheses)
    if selected is None:
        return _blocked_result(
            hypotheses,
            status="inconclusive",
            legacy_selected_candidate_id=legacy_selected.hypothesis_id,
            diff_reasons=("missing_successor_selected_hypothesis", *diff_reasons),
            evidence_comparison_policy=evidence_comparison_policy,
        )

    status = force_selection_status or (
        "parity"
        if selected.hypothesis_id == legacy_selected.hypothesis_id
        else "expected_diff"
    )
    if status == "expected_diff":
        # The successor is recorded for comparison; it never replaces the product.
        final_diff_reasons = tuple(
            dict.fromkeys((*diff_reasons, "successor_selection_not_adopted"))
        )
    elif status == "parity":
        final_diff_reasons = _parity_diff_reasons(
            selected=selected,
            legacy_selected=legacy_selected,
            existing=diff_reasons,
        )
    else:
        final_diff_reasons = diff_reasons

    return PeakModelSelectionResult(
        selected_candidate_id=selected.hypothesis_id,
        legacy_selected_candidate_id=legacy_selected.hypothesis_id,
        stable_row_id=expected_diff_stable_row_id(
            legacy_selected_candidate_id=legacy_selected.hypothesis_id,
            successor_selected_candidate_id=selected.hypothesis_id,
        ),
        trace_group_id=selected.trace_group_id,
        decision_class=_decision_class(selected),
        selection_status=status,
        selection_reasons=_selection_reasons(selected),
        legacy_reasons=_selection_reasons(legacy_selected),
        diff_reasons=final_diff_reasons,
        public_projection=_public_projection(selected),
        evidence_sources=_evidence_sources(selected),
        compatibility_oracle="legacy_peak_scoring_current_oracle",
        policy_source="selected_hypothesis_model_selection",
        product_switch_allowed=status == "parity" and not final_diff_reasons,
        evidence_comparison_policy=evidence_comparison_policy,
    )


def _legacy_selected_hypothesis(
    hypotheses: tuple[PeakHypothesis, ...],
) -> PeakHypothesis | None:
    for hypothesis in hypotheses:
        if hypothesis.audit.selected:
            return hypothesis
    return None


def _successor_selected_hypothesis(
    hypotheses: tuple[PeakHypothesis, ...],
) -> PeakHypothesis | None:
    if not hypotheses:
        return None
    return min(hypotheses, key=_successor_selection_key)


def _successor_selection_key(hypothesis: PeakHypothesis) -> tuple[float, ...]:
    return _peak_hypothesis_selection_ordering_key(
        peak_hypothesis_decision_record(hypothesis)
    )


def _peak_hypothesis_selection_ordering_key(
    record: DecisionRecord,
) -> tuple[float, ...]:
    return tuple(value for _name, value in (*record.gate, *record.tie_break))


def peak_hypothesis_decision_record(
    hypothesis: PeakHypothesis,
) -> DecisionRecord:
    semantics = hypothesis.evidence.decision_semantics
    reasons = _selection_reasons(hypothesis)
    if semantics is None:
        gate: tuple[DecisionTerm, ...] = (
            ("decision_class_rank", float(DECISION_CLASS_RANK["review"])),
            ("blocker_count", float(_blocking_reason_count(hypothesis))),
        )
        blockers = tuple(
            dict.fromkeys(
                (
                    *hypothesis.evidence.concern_labels,
                    *hypothesis.evidence.cap_labels,
                )
            )
        )
        support = tuple(hypothesis.evidence.support_labels)
        decision_class: DecisionClass = "review"
    else:
        gate = decision_gate_terms(semantics)
        blockers = decision_blockers(semantics)
        support = semantics.support_reasons
        decision_class = semantics.decision_class
    tie_break: tuple[DecisionTerm, ...] = (
        (
            "projected_confidence_rank",
            float(_CONFIDENCE_RANK.get(_projected_confidence(hypothesis), 4)),
        ),
        ("negative_selection_reason_count", -float(len(reasons))),
        ("chemical_evidence_rank", float(_chemical_evidence_rank(hypothesis))),
        ("selection_reference_distance", _selection_reference_distance(hypothesis)),
        (
            "legacy_selection_rank",
            float(
                hypothesis.audit.selection_rank
                if hypothesis.audit.selection_rank is not None
                else 1_000_000
            ),
        ),
    )
    return DecisionRecord(
        workflow="peak_hypothesis_model_selection",
        unit_id=hypothesis.hypothesis_id,
        required_evidence=(
            "peak_hypothesis",
            "integration_result",
            "evidence_vector",
            "audit_trail",
        ),
        decision_class=decision_class,
        blockers=blockers,
        support=support,
        gate=gate,
        tie_break=tie_break,
        projection_authority="selected_hypothesis_model_selection",
    )


def _blocking_reason_count(hypothesis: PeakHypothesis) -> int:
    semantics = hypothesis.evidence.decision_semantics
    if semantics is None:
        return len(hypothesis.evidence.concern_labels) + len(
            hypothesis.evidence.cap_labels
        )
    return (
        len(semantics.conflict_reasons)
        + len(semantics.review_reasons)
        + len(semantics.not_counted_reasons)
        + len(semantics.exclusion_reasons)
        + len(semantics.ambiguity_reasons)
    )


def _selection_reference_distance(hypothesis: PeakHypothesis) -> float:
    reference = hypothesis.audit.selection_reference_rt_min
    if reference is None:
        return float("inf")
    return abs(hypothesis.integration.rt_apex_min - reference)


def _blocked_result(
    hypotheses: tuple[PeakHypothesis, ...],
    *,
    status: Literal["blocked_diff", "inconclusive"],
    diff_reasons: tuple[str, ...],
    evidence_comparison_policy: EvidenceComparisonPolicy,
    legacy_selected_candidate_id: str = "",
) -> PeakModelSelectionResult:
    trace_group_id = hypotheses[0].trace_group_id if hypotheses else ""
    return PeakModelSelectionResult(
        selected_candidate_id="",
        legacy_selected_candidate_id=legacy_selected_candidate_id,
        stable_row_id="",
        trace_group_id=trace_group_id,
        decision_class="ambiguous",
        selection_status=status,
        selection_reasons=(),
        legacy_reasons=(),
        diff_reasons=diff_reasons,
        public_projection={},
        evidence_sources=(),
        compatibility_oracle="legacy_peak_scoring_current_oracle",
        policy_source="selected_hypothesis_model_selection",
        product_switch_allowed=False,
        evidence_comparison_policy=evidence_comparison_policy,
    )


def _selection_reasons(hypothesis: PeakHypothesis) -> tuple[str, ...]:
    semantics = hypothesis.evidence.decision_semantics
    if semantics is None:
        return tuple(
            dict.fromkeys(
                (
                    *hypothesis.evidence.support_labels,
                    *hypothesis.evidence.concern_labels,
                    *hypothesis.evidence.cap_labels,
                )
            )
        )
    return tuple(
        dict.fromkeys(
            (
                *semantics.support_reasons,
                *semantics.conflict_reasons,
                *semantics.review_reasons,
                *semantics.not_counted_reasons,
                *semantics.exclusion_reasons,
                *semantics.ambiguity_reasons,
            )
        )
    )


def _decision_class(hypothesis: PeakHypothesis) -> DecisionClass:
    semantics = hypothesis.evidence.decision_semantics
    return semantics.decision_class if semantics is not None else "review"


def _public_projection(hypothesis: PeakHypothesis) -> dict[str, str]:
    return {
        "confidence": _projected_confidence(hypothesis),
        "reason": _projected_reason(hypothesis),
        "compatibility_labels": ";".join(_compatibility_labels(hypothesis)),
    }


def _projected_confidence(hypothesis: PeakHypothesis) -> str:
    return hypothesis.evidence.projected_confidence or hypothesis.evidence.confidence


def _projected_reason(hypothesis: PeakHypothesis) -> str:
    return hypothesis.evidence.projected_reason or hypothesis.evidence.reason


def _chemical_evidence_rank(hypothesis: PeakHypothesis) -> int:
    facts = hypothesis.evidence.evidence_facts
    if facts is None:
        if hypothesis.evidence.ms2_present and hypothesis.evidence.nl_match:
            return 0
        if hypothesis.evidence.ms2_present and hypothesis.evidence.nl_match is False:
            return 3
        return 2
    if not facts.chemical.neutral_loss_required:
        return 1
    if facts.chemical.ms2_present and facts.chemical.nl_match:
        return 0
    if facts.chemical.ms2_present and facts.chemical.nl_match is False:
        return 3
    return 2


def _compatibility_labels(hypothesis: PeakHypothesis) -> tuple[str, ...]:
    evidence = hypothesis.evidence
    semantics = evidence.decision_semantics
    if semantics is not None and semantics.compatibility_labels:
        return semantics.compatibility_labels
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


def _evidence_sources(hypothesis: PeakHypothesis) -> tuple[str, ...]:
    sources: list[str] = []
    evidence = hypothesis.evidence
    if evidence.common is not None or evidence.prominence is not None:
        sources.append("ms1_trace")
    if evidence.ms2_present is not None or evidence.nl_match is not None:
        sources.append("candidate_aligned_ms2_nl")
    if evidence.rt_prior_min is not None:
        sources.append("role_aware_rt")
    if (
        evidence.cwt_best_scale is not None
        or evidence.cwt_ridge_persistence is not None
        or "centwave_cwt" in hypothesis.audit.proposal_sources
    ):
        sources.append("cwt_boundary_morphology_context")
    if "chrom_peak_segment" in hypothesis.audit.proposal_sources:
        sources.append("chrom_peak_segment_context")
    if (
        evidence.quality_flags
        or evidence.region_trace_continuity is not None
        or "chrom_peak_segment" in hypothesis.audit.proposal_sources
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


def _parity_diff_reasons(
    *,
    selected: PeakHypothesis,
    legacy_selected: PeakHypothesis,
    existing: tuple[str, ...],
) -> tuple[str, ...]:
    reasons = list(existing)
    if selected.hypothesis_id != legacy_selected.hypothesis_id:
        reasons.append("parity_selected_candidate_mismatch")
    if _public_projection(selected) != _public_projection(legacy_selected):
        reasons.append("parity_public_projection_mismatch")
    return tuple(dict.fromkeys(reasons))
