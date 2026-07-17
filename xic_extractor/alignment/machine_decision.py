from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from xic_extractor.alignment.promotion_policy import (
    BACKFILL_HYPOTHESIS_BLOCKED_REASON,
    BACKFILL_MS1_PATTERN_BLOCKED_REASON,
    BACKFILL_MS1_PATTERN_CONFLICT_REASON,
    BACKFILL_RT_EXPLANATION_BLOCKED_REASON,
    LOW_MS1_COVERAGE_BLOCKED_REASON,
    NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
    BackfillPromotionEvidence,
    classify_backfill_promotion,
    evidence_from_tsv_rows,
)

MatrixRole = Literal["primary", "provisional", "audit", "excluded"]
EvidenceTier = Literal[0, 1, 2, 3]
RecommendedAction = Literal["use", "keep_provisional", "exclude", "review"]
MachineConfidence = Literal["high", "medium", "review", "none"]

_EXCLUDE_BLOCKERS = {
    "duplicate_only",
    "family_consolidation_loser",
    "rescue_only",
    "rescue_only_blocked",
    "zero_present",
}
_AUDIT_BLOCKERS = {
    "ambiguous_ms1_owner_pressure",
    "ambiguous_only",
    BACKFILL_HYPOTHESIS_BLOCKED_REASON,
    BACKFILL_MS1_PATTERN_BLOCKED_REASON,
    BACKFILL_MS1_PATTERN_CONFLICT_REASON,
    BACKFILL_RT_EXPLANATION_BLOCKED_REASON,
    "duplicate_claim_pressure",
    "low_ms1_assessable_coverage",
    LOW_MS1_COVERAGE_BLOCKED_REASON,
    "neighboring_ms1_interference",
    NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
    "review_only",
}
_ROW_BLOCKERS = {
    *_EXCLUDE_BLOCKERS,
    *_AUDIT_BLOCKERS,
    "anchored_single_detected_phase_a",
    "extreme_backfill_dependency",
    "insufficient_detected_identity_support",
    "insufficient_identity_support",
    "single_detected_seed",
    "single_sample_local_owner",
    "skip_expensive_evidence",
    "weak_seed_backfill_dependency",
    "zero_quantifiable_detected",
}
_SUPPORT_PRIMARY_EVIDENCE = {
    "anchored_family",
    "cid_nl_only",
    "multi_sample_detected",
    "owner_complete_link",
    "owner_identity",
}
_NON_BLOCKING_IDENTITY_REASONS = {
    *_SUPPORT_PRIMARY_EVIDENCE,
    "cell_evidence_supported_backfill",
    "dda_limited_ms2_but_ms1_shape_supported",
    "weak_seed_tolerated",
}
_PROVISIONAL_DECISION = "provisional_discovery"
_AUDIT_DECISION = "audit_family"
_GENERIC_PROVISIONAL_REASONS = {"", _PROVISIONAL_DECISION}


@dataclass(frozen=True)
class MachineDecisionVector:
    feature_family_id: str
    matrix_role: MatrixRole
    evidence_tier: EvidenceTier
    support_reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    confidence: MachineConfidence
    recommended_action: RecommendedAction


def project_machine_decision(
    review_row: Mapping[str, object],
    cell_rows: Sequence[Mapping[str, object]] = (),
) -> MachineDecisionVector:
    review = _string_row(review_row)
    cells = _string_rows(cell_rows)
    evidence = evidence_from_tsv_rows(
        review,
        cells,
        seed_quality=None,
        sample_count=len(cells),
    )
    include_primary = _is_trueish(review.get("include_in_primary_matrix"))
    identity_decision = _text(review.get("identity_decision"))
    identity_reason = _text(review.get("identity_reason"))
    confidence = _confidence(review.get("identity_confidence"))
    flags = _split_tokens(review.get("row_flags"))
    support_reasons = _support_reasons(review, evidence)
    blockers = _blockers(
        identity_decision=identity_decision,
        identity_reason=identity_reason,
        flags=flags,
        evidence=evidence,
    )
    matrix_role = _matrix_role(
        include_primary=include_primary,
        identity_decision=identity_decision,
        blockers=blockers,
    )
    return MachineDecisionVector(
        feature_family_id=_text(review.get("feature_family_id")),
        matrix_role=matrix_role,
        evidence_tier=_evidence_tier(cells, support_reasons),
        support_reasons=support_reasons,
        blockers=blockers,
        confidence=confidence,
        recommended_action=_recommended_action(matrix_role),
    )


def machine_decision_as_row(
    vector: MachineDecisionVector,
) -> dict[str, str]:
    return {
        "matrix_role": vector.matrix_role,
        "evidence_tier": str(vector.evidence_tier),
        "support_reasons": ";".join(vector.support_reasons),
        "blockers": ";".join(vector.blockers),
        "confidence": vector.confidence,
        "recommended_action": vector.recommended_action,
    }


def _support_reasons(
    review_row: Mapping[str, str],
    evidence: BackfillPromotionEvidence,
) -> tuple[str, ...]:
    reasons: list[str] = []
    primary_evidence = _text(review_row.get("primary_evidence"))
    if evidence.q_detected > 0:
        reasons.append("detected_seed")
    if _all_rescue_cells_supported(evidence):
        reasons.append("ms1_backfill_supported")
        reasons.append("rt_coherent")
    elif _has_rt_coherent_cell(evidence):
        reasons.append("rt_coherent")
    if primary_evidence in _SUPPORT_PRIMARY_EVIDENCE:
        reasons.append(primary_evidence)
    return _ordered_unique(reasons)


def _blockers(
    *,
    identity_decision: str,
    identity_reason: str,
    flags: tuple[str, ...],
    evidence: BackfillPromotionEvidence,
) -> tuple[str, ...]:
    blockers: list[str] = []
    promotion = classify_backfill_promotion(evidence)
    single_detected_supported = evidence.q_detected == 1 and promotion.supported
    same_peak_backfill_supported = promotion.supported
    if evidence.q_detected == 1 and not single_detected_supported:
        blockers.append("single_detected_seed")
    if (
        identity_reason not in _NON_BLOCKING_IDENTITY_REASONS
        and identity_reason not in _GENERIC_PROVISIONAL_REASONS
    ):
        blockers.append(identity_reason)
    blockers.extend(
        flag
        for flag in flags
        if flag in _ROW_BLOCKERS
        and not (
            flag in {"single_detected_seed", "skip_expensive_evidence"}
            and single_detected_supported
        )
        and not (
            flag == "duplicate_claim_pressure" and same_peak_backfill_supported
        )
    )
    blockers.extend(_cell_evidence_blockers(evidence))
    if identity_decision == _PROVISIONAL_DECISION and not blockers:
        blockers.append("insufficient_identity_support")
    if (
        identity_decision == _PROVISIONAL_DECISION
        and evidence.q_detected == 1
        and not any(
            blocker
            not in {"single_detected_seed", "skip_expensive_evidence"}
            for blocker in blockers
        )
    ):
        blockers.append("insufficient_identity_support")
    return _ordered_unique(blockers)


def _cell_evidence_blockers(
    evidence: BackfillPromotionEvidence,
) -> tuple[str, ...]:
    if evidence.q_detected <= 0 or evidence.q_rescue <= 0:
        return ()
    rescued = tuple(
        cell
        for cell in evidence.cells
        if cell.status == "rescued" and cell.is_rescued_quantifiable
    )
    if len(rescued) < evidence.q_rescue:
        return (LOW_MS1_COVERAGE_BLOCKED_REASON,)
    if any(cell.high_neighbor_interference for cell in rescued):
        return (NEIGHBOR_INTERFERENCE_BLOCKED_REASON,)
    if any(
        cell.backfill_identity_block_reason == BACKFILL_HYPOTHESIS_BLOCKED_REASON
        for cell in rescued
    ):
        return (BACKFILL_HYPOTHESIS_BLOCKED_REASON,)
    if any(
        cell.low_assessable_coverage
        or (
            not cell.additional_ms1_support
            and not cell.same_peak_ms1_pattern_supported
        )
        for cell in rescued
    ):
        return (LOW_MS1_COVERAGE_BLOCKED_REASON,)
    for reason in (
        BACKFILL_MS1_PATTERN_CONFLICT_REASON,
        BACKFILL_RT_EXPLANATION_BLOCKED_REASON,
        BACKFILL_MS1_PATTERN_BLOCKED_REASON,
    ):
        if any(cell.backfill_identity_block_reason == reason for cell in rescued):
            return (reason,)
    if any(
        cell.low_assessable_coverage
        or not (
            cell.local_apex_supported
            or cell.product_authorized_same_peak_supported
        )
        or not cell.supported_for_backfill
        for cell in rescued
    ):
        return (LOW_MS1_COVERAGE_BLOCKED_REASON,)
    return ()


def _matrix_role(
    *,
    include_primary: bool,
    identity_decision: str,
    blockers: tuple[str, ...],
) -> MatrixRole:
    if include_primary:
        return "primary"
    blocker_set = set(blockers)
    has_audit_blocker = bool(blocker_set & _AUDIT_BLOCKERS)
    has_exclude_blocker = bool(blocker_set & _EXCLUDE_BLOCKERS)
    if identity_decision == _AUDIT_DECISION:
        if has_audit_blocker:
            return "audit"
        if has_exclude_blocker:
            return "excluded"
        return "audit"
    if has_audit_blocker:
        return "audit"
    if has_exclude_blocker:
        return "excluded"
    if identity_decision == _PROVISIONAL_DECISION:
        return "provisional"
    return "audit"


def _recommended_action(matrix_role: MatrixRole) -> RecommendedAction:
    if matrix_role == "primary":
        return "use"
    if matrix_role == "provisional":
        return "keep_provisional"
    if matrix_role == "excluded":
        return "exclude"
    return "review"


def _evidence_tier(
    cell_rows: Sequence[Mapping[str, str]],
    support_reasons: tuple[str, ...],
) -> EvidenceTier:
    if cell_rows or any(reason != "detected_seed" for reason in support_reasons):
        return 1
    return 0


def _all_rescue_cells_supported(evidence: BackfillPromotionEvidence) -> bool:
    if evidence.q_rescue <= 0:
        return False
    rescued = tuple(
        cell
        for cell in evidence.cells
        if cell.status == "rescued" and cell.is_rescued_quantifiable
    )
    return (
        len(rescued) >= evidence.q_rescue
        and bool(rescued)
        and all(cell.supported_for_backfill for cell in rescued)
    )


def _has_rt_coherent_cell(evidence: BackfillPromotionEvidence) -> bool:
    return any(cell.local_apex_supported for cell in evidence.cells)


def _confidence(value: object) -> MachineConfidence:
    text = _text(value)
    if text in {"high", "medium", "review", "none"}:
        return text  # type: ignore[return-value]
    return "none"


def _split_tokens(value: object) -> tuple[str, ...]:
    return tuple(token for token in _text(value).split(";") if token)


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _string_rows(
    rows: Sequence[Mapping[str, object]],
) -> tuple[dict[str, str], ...]:
    return tuple(_string_row(row) for row in rows)


def _string_row(row: Mapping[str, object]) -> dict[str, str]:
    return {str(key): _string_value(value) for key, value in row.items()}


def _string_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def _text(value: object) -> str:
    return str(value or "").strip()


def _is_trueish(value: object) -> bool:
    return _text(value).lower() in {"1", "true", "t", "yes", "y"}
