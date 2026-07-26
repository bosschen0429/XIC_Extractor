from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from xic_extractor.decision_policy import (
    DECISION_CLASS_RANK,
    DecisionRecord,
    DecisionTerm,
    decision_blockers,
    decision_gate_terms,
)
from xic_extractor.evidence_semantics import DecisionClass
from xic_extractor.peak_detection.hypotheses import (
    PeakHypothesis,
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
MatrixValueImpact = Literal[
    "none",
    "area_value_changed",
    "presence_changed",
    "not_assessed",
]
ExpectedDiffValidationTier = Literal[
    "synthetic_fixture",
    "targeted_benchmark",
    "8raw",
    "manual_eic_ms2_review",
    "not_validated",
]
ExpectedDiffReviewerVerdict = Literal["approved", "blocked", "inconclusive"]
ExpectedDiffFinalLabel = Literal[
    "expected_diff",
    "blocked_diff",
    "inconclusive",
]
ModelSelectionPolicySource = Literal[
    "selected_hypothesis_model_selection_v1",
]
_MATRIX_AFFECTING_PUBLIC_OUTPUTS = frozenset(
    {
        "area",
        "boundary",
        "final matrix value",
        "integration",
        "selected area",
        "selected boundary",
        "selected rt",
    }
)
_PUBLIC_OUTPUT_ALIASES = {
    "selected candidate marker": frozenset(
        {
            "candidate table selected marker",
            "candidate tsv",
            "csv",
            "selected candidate marker",
            "selected marker",
            "workbook",
            "xlsx",
        }
    ),
    "selected rt": frozenset(
        {"csv", "selected rt", "selected apex rt", "workbook", "xlsx"}
    ),
    "area": frozenset(
        {
            "area",
            "csv",
            "final matrix value",
            "integration",
            "selected area",
            "workbook",
            "xlsx",
        }
    ),
    "boundary": frozenset(
        {"boundary", "csv", "integration", "selected boundary", "workbook", "xlsx"}
    ),
    "confidence": frozenset({"confidence", "csv", "workbook", "xlsx"}),
    "reason": frozenset({"reason", "csv", "workbook", "xlsx"}),
}
_COUNTED_DECISION_CLASSES = frozenset({"accepted", "review"})
_CONFIDENCE_RANK = {
    "HIGH": 0,
    "MEDIUM": 1,
    "LOW": 2,
    "VERY_LOW": 3,
    "": 4,
}


@dataclass(frozen=True)
class ExpectedDiffApprovalRecord:
    stable_row_id: str
    sample_name: str
    target_label: str
    legacy_selected_candidate_id: str
    successor_selected_candidate_id: str
    public_outputs_touched: tuple[str, ...]
    matrix_value_impact: MatrixValueImpact
    evidence_sources: tuple[str, ...]
    evidence_summary: str
    validation_tier: ExpectedDiffValidationTier
    reviewer_role: str
    reviewer_verdict: ExpectedDiffReviewerVerdict
    final_label: ExpectedDiffFinalLabel


ExpectedDiffApprovalRecords = Mapping[str, ExpectedDiffApprovalRecord]


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


def expected_diff_approval_for_result(
    result: PeakModelSelectionResult,
    approvals: ExpectedDiffApprovalRecords | None,
    *,
    sample_name: str,
    target_label: str,
) -> ExpectedDiffApprovalRecord | None:
    if approvals is None or result.selection_status != "expected_diff":
        return None
    for stable_row_id, approval in approvals.items():
        if approval.stable_row_id != stable_row_id:
            continue
        if approval.stable_row_id != result.stable_row_id:
            continue
        if approval.sample_name != sample_name:
            continue
        if approval.target_label != target_label:
            continue
        if (
            approval.legacy_selected_candidate_id
            != result.legacy_selected_candidate_id
        ):
            continue
        if approval.successor_selected_candidate_id != result.selected_candidate_id:
            continue
        return approval
    return None


def expected_diff_approval_for_legacy_selection(
    result: PeakModelSelectionResult,
    approvals: ExpectedDiffApprovalRecords | None,
    *,
    sample_name: str,
    target_label: str,
) -> ExpectedDiffApprovalRecord | None:
    if approvals is None or not result.legacy_selected_candidate_id:
        return None
    for stable_row_id, approval in approvals.items():
        if approval.stable_row_id != stable_row_id:
            continue
        if approval.sample_name != sample_name:
            continue
        if approval.target_label != target_label:
            continue
        if (
            approval.legacy_selected_candidate_id
            != result.legacy_selected_candidate_id
        ):
            continue
        return approval
    return None


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
    successor_selected_candidate_id: str | None = None,
    expected_diff_approval: ExpectedDiffApprovalRecord | None = None,
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

    selected = (
        _hypothesis_by_id(hypotheses, successor_selected_candidate_id)
        if successor_selected_candidate_id is not None
        else _successor_selected_hypothesis(hypotheses)
    )
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
    if (
        status == "expected_diff"
        and expected_diff_approval is not None
        and evidence_comparison_policy == "limited_evidence_shadow"
        and _uses_ms2_nl_evidence(expected_diff_approval.evidence_sources)
    ):
        status = "inconclusive"
        final_diff_reasons = (
            "ms2_expected_diff_requires_complete_candidate_evidence",
            *diff_reasons,
        )
    elif status == "expected_diff":
        final_diff_reasons = _expected_diff_reasons(
            expected_diff_approval,
            selected=selected,
            legacy_selected=legacy_selected,
            existing=diff_reasons,
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
        evidence_sources=_model_selection_evidence_sources(
            selected,
            status=status,
            expected_diff_approval=expected_diff_approval,
        ),
        compatibility_oracle="legacy_peak_scoring_current_oracle",
        policy_source="selected_hypothesis_model_selection_v1",
        product_switch_allowed=_product_switch_allowed(
            status,
            expected_diff_approval,
            final_diff_reasons,
        ),
        evidence_comparison_policy=evidence_comparison_policy,
    )


def _legacy_selected_hypothesis(
    hypotheses: tuple[PeakHypothesis, ...],
) -> PeakHypothesis | None:
    for hypothesis in hypotheses:
        if hypothesis.audit.selected:
            return hypothesis
    return None


def _hypothesis_by_id(
    hypotheses: tuple[PeakHypothesis, ...],
    hypothesis_id: str,
) -> PeakHypothesis | None:
    for hypothesis in hypotheses:
        if hypothesis.hypothesis_id == hypothesis_id:
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
        projection_authority="selected_hypothesis_model_selection_v1",
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
        policy_source="selected_hypothesis_model_selection_v1",
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


def _model_selection_evidence_sources(
    selected: PeakHypothesis,
    *,
    status: ModelSelectionStatus,
    expected_diff_approval: ExpectedDiffApprovalRecord | None,
) -> tuple[str, ...]:
    sources = list(_evidence_sources(selected))
    if status == "expected_diff" and expected_diff_approval is not None:
        sources.extend(expected_diff_approval.evidence_sources)
    return tuple(dict.fromkeys(sources))


def _expected_diff_reasons(
    approval: ExpectedDiffApprovalRecord | None,
    *,
    selected: PeakHypothesis,
    legacy_selected: PeakHypothesis,
    existing: tuple[str, ...],
) -> tuple[str, ...]:
    reasons = list(existing)
    if approval is None:
        reasons.append("missing_expected_diff_approval_record")
        return tuple(dict.fromkeys(reasons))
    if approval.reviewer_verdict != "approved":
        reasons.append("expected_diff_approval_not_approved")
    if approval.final_label != "expected_diff":
        reasons.append("expected_diff_final_label_not_expected_diff")
    if approval.legacy_selected_candidate_id != legacy_selected.hypothesis_id:
        reasons.append("expected_diff_legacy_candidate_mismatch")
    if approval.successor_selected_candidate_id != selected.hypothesis_id:
        reasons.append("expected_diff_successor_candidate_mismatch")
    if approval.stable_row_id != expected_diff_stable_row_id(
        legacy_selected_candidate_id=legacy_selected.hypothesis_id,
        successor_selected_candidate_id=selected.hypothesis_id,
    ):
        reasons.append("expected_diff_stable_row_mismatch")
    if not approval.public_outputs_touched:
        reasons.append("expected_diff_missing_public_output_impact")
    if not approval.stable_row_id.strip():
        reasons.append("expected_diff_missing_stable_row_id")
    if not approval.evidence_sources:
        reasons.append("expected_diff_missing_evidence_sources")
    if not approval.evidence_summary.strip():
        reasons.append("expected_diff_missing_evidence_summary")
    if approval.validation_tier == "not_validated":
        reasons.append("expected_diff_requires_validation")
    for output in _actual_public_output_impacts(
        selected=selected,
        legacy_selected=legacy_selected,
    ):
        if not _declares_public_output_impact(
            approval.public_outputs_touched,
            output,
        ):
            reasons.append(f"expected_diff_missing_{output.replace(' ', '_')}_impact")
    if (
        _actual_area_changed(selected=selected, legacy_selected=legacy_selected)
        and approval.matrix_value_impact != "area_value_changed"
    ):
        reasons.append("matrix_expected_diff_area_impact_mismatch")
    if (
        _actual_presence_changed(selected=selected, legacy_selected=legacy_selected)
        and approval.matrix_value_impact != "presence_changed"
    ):
        reasons.append("matrix_expected_diff_presence_impact_mismatch")
    if (
        _touches_matrix_affecting_output(approval.public_outputs_touched)
        and approval.matrix_value_impact == "not_assessed"
    ):
        reasons.append("matrix_expected_diff_requires_assessed_impact")
    if (
        (
            approval.matrix_value_impact
            in {"area_value_changed", "presence_changed"}
            or _touches_matrix_affecting_output(approval.public_outputs_touched)
            or _actual_matrix_value_changed(
                selected=selected,
                legacy_selected=legacy_selected,
            )
        )
        and approval.validation_tier == "synthetic_fixture"
    ):
        reasons.append("matrix_expected_diff_requires_real_validation")
    return tuple(dict.fromkeys(reasons))


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


def _touches_matrix_affecting_output(public_outputs: tuple[str, ...]) -> bool:
    normalized = {output.strip().lower() for output in public_outputs}
    return bool(normalized.intersection(_MATRIX_AFFECTING_PUBLIC_OUTPUTS))


def _declares_public_output_impact(
    public_outputs: tuple[str, ...],
    output: str,
) -> bool:
    normalized = {item.strip().lower() for item in public_outputs}
    return bool(normalized.intersection(_PUBLIC_OUTPUT_ALIASES[output]))


def _actual_public_output_impacts(
    *,
    selected: PeakHypothesis,
    legacy_selected: PeakHypothesis,
) -> tuple[str, ...]:
    impacts: list[str] = []
    if selected.hypothesis_id != legacy_selected.hypothesis_id:
        impacts.append("selected candidate marker")
    if _float_differs(
        selected.integration.rt_apex_min,
        legacy_selected.integration.rt_apex_min,
    ):
        impacts.append("selected rt")
    if _actual_area_changed(selected=selected, legacy_selected=legacy_selected):
        impacts.append("area")
    if _actual_boundary_changed(selected=selected, legacy_selected=legacy_selected):
        impacts.append("boundary")
    if _projected_confidence(selected) != _projected_confidence(legacy_selected):
        impacts.append("confidence")
    if _projected_reason(selected) != _projected_reason(legacy_selected):
        impacts.append("reason")
    return tuple(dict.fromkeys(impacts))


def _actual_matrix_value_changed(
    *,
    selected: PeakHypothesis,
    legacy_selected: PeakHypothesis,
) -> bool:
    return _actual_area_changed(
        selected=selected,
        legacy_selected=legacy_selected,
    ) or _actual_presence_changed(
        selected=selected,
        legacy_selected=legacy_selected,
    )


def _actual_area_changed(
    *,
    selected: PeakHypothesis,
    legacy_selected: PeakHypothesis,
) -> bool:
    return _float_differs(
        selected.integration.area_raw_counts_seconds,
        legacy_selected.integration.area_raw_counts_seconds,
    ) or _optional_float_differs(
        selected.integration.area_baseline_corrected,
        legacy_selected.integration.area_baseline_corrected,
    )


def _actual_boundary_changed(
    *,
    selected: PeakHypothesis,
    legacy_selected: PeakHypothesis,
) -> bool:
    return (
        _float_differs(
            selected.integration.rt_left_min,
            legacy_selected.integration.rt_left_min,
        )
        or _float_differs(
            selected.integration.rt_right_min,
            legacy_selected.integration.rt_right_min,
        )
        or selected.integration.boundary_sources
        != legacy_selected.integration.boundary_sources
    )


def _actual_presence_changed(
    *,
    selected: PeakHypothesis,
    legacy_selected: PeakHypothesis,
) -> bool:
    return (
        _decision_class(selected) in _COUNTED_DECISION_CLASSES
        and _decision_class(legacy_selected) not in _COUNTED_DECISION_CLASSES
    ) or (
        _decision_class(selected) not in _COUNTED_DECISION_CLASSES
        and _decision_class(legacy_selected) in _COUNTED_DECISION_CLASSES
    )


def _optional_float_differs(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left != right
    return _float_differs(left, right)


def _float_differs(left: float, right: float) -> bool:
    return abs(left - right) > 1e-9


def _uses_ms2_nl_evidence(evidence_sources: tuple[str, ...]) -> bool:
    return any(
        "ms2" in source.strip().lower() or "nl" in source.strip().lower()
        for source in evidence_sources
    )


def _product_switch_allowed(
    status: ModelSelectionStatus,
    approval: ExpectedDiffApprovalRecord | None,
    diff_reasons: tuple[str, ...],
) -> bool:
    if status == "parity":
        return not diff_reasons
    if status != "expected_diff" or approval is None or diff_reasons:
        return False
    return (
        approval.reviewer_verdict == "approved"
        and approval.final_label == "expected_diff"
    )
