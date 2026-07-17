from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from xic_extractor.alignment.cell_quality import (
    CellQualityDecision,
    build_cell_quality_decisions,
    decision_map_by_family,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.identity_gates import (
    EXTREME_BACKFILL_REASON,
    WEAK_SEED_BACKFILL_REASON,
    WEAK_SEED_TOLERATED_REASON,
    DetectedSeedRef,
    SeedQualitySummary,
    classify_single_dr_backfill_dependency,
    summarize_detected_seed_quality,
)
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.output_rows import row_id
from xic_extractor.alignment.promotion_policy import (
    BACKFILL_CELL_EVIDENCE_REQUIRED_FLAG,
    BACKFILL_RESCUE_REVIEW_ONLY_FLAG,
    PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON,
    RESCUE_ONLY_BLOCKED_REASON,
    BackfillPromotionDecision,
    BackfillPromotionEvidence,
    classify_backfill_promotion,
    evidence_from_alignment,
    is_backfill_cell_only_block_reason,
)
from xic_extractor.decision_policy import (
    DECISION_CLASS_RANK,
    DecisionRecord,
    DecisionTerm,
)
from xic_extractor.evidence_semantics import DecisionClass

IdentityDecision = Literal[
    "production_family",
    "provisional_discovery",
    "audit_family",
]
IdentityConfidence = Literal["high", "medium", "review", "none"]

@dataclass(frozen=True)
class MatrixIdentityRowDecision:
    feature_family_id: str
    include_in_primary_matrix: bool
    identity_decision: IdentityDecision
    identity_confidence: IdentityConfidence
    primary_evidence: str
    identity_reason: str
    quantifiable_detected_count: int
    quantifiable_rescue_count: int
    review_rescue_count: int
    duplicate_assigned_count: int
    ambiguous_ms1_owner_count: int
    row_flags: tuple[str, ...]

    @property
    def decision_record(self) -> DecisionRecord:
        return matrix_identity_row_decision_record(self)


@dataclass(frozen=True)
class MatrixIdentityDecisionSet:
    rows: dict[str, MatrixIdentityRowDecision]
    cell_quality: dict[tuple[str, str], CellQualityDecision]

    def row(self, feature_family_id: str) -> MatrixIdentityRowDecision:
        return self.rows[feature_family_id]


def build_matrix_identity_decisions(
    matrix: AlignmentMatrix,
    config: AlignmentConfig,
    *,
    cell_quality: Mapping[tuple[str, str], CellQualityDecision] | None = None,
) -> MatrixIdentityDecisionSet:
    quality = (
        dict(cell_quality)
        if cell_quality is not None
        else build_cell_quality_decisions(matrix.cells, config)
    )
    quality_by_family = decision_map_by_family(quality)
    cells_by_family = _cells_by_family(matrix.cells)
    rows: dict[str, MatrixIdentityRowDecision] = {}
    for cluster in matrix.clusters:
        family_id = row_id(cluster)
        rows[family_id] = decide_matrix_identity_row(
            cluster,
            quality_by_family.get(family_id, ()),
            cells_by_family.get(family_id, ()),
        )
    return MatrixIdentityDecisionSet(rows=rows, cell_quality=quality)


def matrix_identity_row_decision_record(
    decision: MatrixIdentityRowDecision,
) -> DecisionRecord:
    decision_class = _matrix_identity_decision_class(decision)
    blockers = _matrix_identity_blockers(decision)
    gate: tuple[DecisionTerm, ...] = (
        ("decision_class_rank", float(DECISION_CLASS_RANK[decision_class])),
        (
            "primary_matrix_projection_rank",
            0.0 if decision.include_in_primary_matrix else 1.0,
        ),
        ("blocker_count", float(len(blockers))),
    )
    tie_break: tuple[DecisionTerm, ...] = (
        (
            "identity_confidence_rank",
            float(_IDENTITY_CONFIDENCE_RANK[decision.identity_confidence]),
        ),
        (
            "negative_detected_identity_cells",
            -float(decision.quantifiable_detected_count),
        ),
        (
            "negative_quantifiable_rescue_cells",
            -float(decision.quantifiable_rescue_count),
        ),
        ("duplicate_pressure_count", float(decision.duplicate_assigned_count)),
        ("ambiguous_owner_count", float(decision.ambiguous_ms1_owner_count)),
    )
    return DecisionRecord(
        workflow="alignment_matrix_identity_row",
        unit_id=decision.feature_family_id,
        required_evidence=(
            "matrix_identity_row",
            "cell_quality_decisions",
            "row_identity_evidence",
            "backfill_promotion_policy",
        ),
        decision_class=decision_class,
        blockers=blockers,
        support=_matrix_identity_support(decision),
        gate=gate,
        tie_break=tie_break,
        projection_authority="build_matrix_identity_decisions",
    )


def decide_matrix_identity_row(
    cluster: Any,
    cell_quality: Sequence[CellQualityDecision],
    cells: Sequence[AlignedCell] = (),
) -> MatrixIdentityRowDecision:
    family_id = row_id(cluster)
    evidence = _family_evidence(cluster)
    primary_evidence = _primary_evidence(cluster, evidence, cell_quality)
    q_detected = sum(
        1 for decision in cell_quality if decision.is_detected_identity_support
    )
    q_rescue = sum(
        1
        for decision in cell_quality
        if decision.quality_status == "rescue_quantifiable"
    )
    review_rescue = sum(
        1 for decision in cell_quality if decision.quality_status == "review_rescue"
    )
    duplicate_count = sum(
        1 for decision in cell_quality if decision.quality_status == "duplicate_loser"
    )
    ambiguous_count = sum(
        1 for decision in cell_quality if decision.quality_status == "ambiguous_owner"
    )
    cell_count = len(cell_quality)
    backfill_dependency, seed_quality = _single_dr_backfill_context(
        cluster,
        q_detected=q_detected,
        q_rescue=q_rescue,
        cell_count=cell_count,
        cells=cells,
    )

    flags = _row_flags(
        cluster=cluster,
        evidence=evidence,
        primary_evidence=primary_evidence,
        q_detected=q_detected,
        q_rescue=q_rescue,
        duplicate_count=duplicate_count,
        ambiguous_count=ambiguous_count,
        backfill_dependency=backfill_dependency,
    )
    policy_evidence = evidence_from_alignment(
        neutral_loss_tag=str(getattr(cluster, "neutral_loss_tag", "")),
        primary_evidence=primary_evidence,
        q_detected=q_detected,
        q_rescue=q_rescue,
        duplicate_count=duplicate_count,
        ambiguous_count=ambiguous_count,
        backfill_dependency=backfill_dependency,
        seed_quality=seed_quality,
        cells=cells,
        cell_quality=cell_quality,
    )
    promotion_policy = classify_backfill_promotion(policy_evidence)
    if promotion_policy.supported and duplicate_count > q_detected:
        flags = [
            "same_peak_multi_claim" if flag == "duplicate_claim_pressure" else flag
            for flag in flags
        ]
    if _is_provisional_retention_candidate(
        cluster,
        evidence=evidence,
        primary_evidence=primary_evidence,
        q_detected=q_detected,
        q_rescue=q_rescue,
        duplicate_count=duplicate_count,
        ambiguous_count=ambiguous_count,
        policy_evidence=policy_evidence,
    ):
        flags.append("provisional_retention_candidate")
        flags.append("skip_expensive_evidence")
    rescue_cell_only_block = (
        promotion_policy.blocked
        and is_backfill_cell_only_block_reason(promotion_policy.reason)
    )
    if rescue_cell_only_block:
        flags.append(BACKFILL_CELL_EVIDENCE_REQUIRED_FLAG)
        flags.append(BACKFILL_RESCUE_REVIEW_ONLY_FLAG)
    flags.extend(promotion_policy.flags)
    include, identity_decision, confidence, reason = _promotion_decision(
        cluster,
        evidence=evidence,
        primary_evidence=primary_evidence,
        q_detected=q_detected,
        q_rescue=q_rescue,
        duplicate_count=duplicate_count,
        ambiguous_count=ambiguous_count,
        backfill_dependency=backfill_dependency,
        promotion_policy=promotion_policy,
    )
    return MatrixIdentityRowDecision(
        feature_family_id=family_id,
        include_in_primary_matrix=include,
        identity_decision=identity_decision,
        identity_confidence=confidence,
        primary_evidence=primary_evidence,
        identity_reason=reason,
        quantifiable_detected_count=q_detected,
        quantifiable_rescue_count=q_rescue,
        review_rescue_count=review_rescue,
        duplicate_assigned_count=duplicate_count,
        ambiguous_ms1_owner_count=ambiguous_count,
        row_flags=tuple(flags),
    )


_IDENTITY_CONFIDENCE_RANK: dict[IdentityConfidence, int] = {
    "high": 0,
    "medium": 1,
    "review": 2,
    "none": 3,
}


def _matrix_identity_decision_class(
    decision: MatrixIdentityRowDecision,
) -> DecisionClass:
    if decision.include_in_primary_matrix:
        return "accepted"
    if (
        decision.identity_reason == "ambiguous_only"
        or "ambiguous_only" in decision.row_flags
        or "ambiguous_ms1_owner_pressure" in decision.row_flags
    ):
        return "ambiguous"
    if decision.identity_decision == "audit_family":
        if (
            decision.identity_reason in {"review_only", "family_consolidation_loser"}
            or "family_consolidation_loser" in decision.row_flags
        ):
            return "excluded"
        return "not_counted"
    return "review"


def _matrix_identity_blockers(
    decision: MatrixIdentityRowDecision,
) -> tuple[str, ...]:
    if decision.include_in_primary_matrix:
        return ()
    return tuple(
        dict.fromkeys(
            part
            for part in (decision.identity_reason, *decision.row_flags)
            if part
        )
    )


def _matrix_identity_support(
    decision: MatrixIdentityRowDecision,
) -> tuple[str, ...]:
    support = [
        f"identity_decision:{decision.identity_decision}",
        f"identity_confidence:{decision.identity_confidence}",
    ]
    if decision.include_in_primary_matrix:
        support.append("include_in_primary_matrix")
    if decision.primary_evidence and decision.primary_evidence != "none":
        support.append(f"primary_evidence:{decision.primary_evidence}")
    if decision.quantifiable_detected_count:
        support.append(
            f"detected_identity_cells:{decision.quantifiable_detected_count}"
        )
    if decision.quantifiable_rescue_count:
        support.append(
            f"quantifiable_rescue_cells:{decision.quantifiable_rescue_count}"
        )
    return tuple(dict.fromkeys(support))


def _promotion_decision(
    cluster: Any,
    *,
    evidence: str,
    primary_evidence: str,
    q_detected: int,
    q_rescue: int,
    duplicate_count: int,
    ambiguous_count: int,
    backfill_dependency: str | None,
    promotion_policy: BackfillPromotionDecision,
) -> tuple[bool, IdentityDecision, IdentityConfidence, str]:
    if bool(getattr(cluster, "review_only", False)):
        return False, "audit_family", "review", "review_only"
    if _is_consolidation_loser(evidence):
        return False, "audit_family", "review", "family_consolidation_loser"
    if q_detected == 0 and q_rescue > 0:
        return False, "audit_family", "review", RESCUE_ONLY_BLOCKED_REASON
    if q_detected == 0 and duplicate_count > 0 and ambiguous_count == 0:
        return False, "audit_family", "review", "duplicate_only"
    if q_detected == 0 and ambiguous_count > 0 and duplicate_count == 0:
        return False, "audit_family", "review", "ambiguous_only"
    if q_detected == 0:
        return False, "audit_family", "none", "zero_quantifiable_detected"
    if duplicate_count > q_detected and not promotion_policy.supported:
        return False, "audit_family", "review", "duplicate_claim_pressure"
    rescue_cell_only_block = (
        promotion_policy.blocked
        and is_backfill_cell_only_block_reason(promotion_policy.reason)
    )
    if promotion_policy.blocked and not rescue_cell_only_block:
        return (
            False,
            "provisional_discovery",
            "review",
            promotion_policy.reason,
        )
    if promotion_policy.supported:
        return (
            True,
            "production_family",
            "medium",
            promotion_policy.reason,
        )
    if (
        backfill_dependency == EXTREME_BACKFILL_REASON
        and not rescue_cell_only_block
    ):
        return (
            False,
            "provisional_discovery",
            "review",
            "extreme_backfill_dependency",
        )
    if (
        backfill_dependency == WEAK_SEED_BACKFILL_REASON
        and not rescue_cell_only_block
    ):
        return (
            False,
            "provisional_discovery",
            "review",
            "weak_seed_backfill_dependency",
        )
    if primary_evidence == "single_sample_local_owner":
        return (
            False,
            "provisional_discovery",
            "review",
            "single_sample_local_owner",
        )
    if primary_evidence in {"owner_complete_link", "cid_nl_only", "owner_identity"}:
        if q_detected >= 2:
            if rescue_cell_only_block:
                return (
                    True,
                    "production_family",
                    "review",
                    PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON,
                )
            if backfill_dependency == WEAK_SEED_TOLERATED_REASON:
                return (
                    True,
                    "production_family",
                    "medium",
                    "weak_seed_tolerated",
                )
            return True, "production_family", "high", primary_evidence
        return (
            False,
            "provisional_discovery",
            "review",
            "insufficient_detected_identity_support",
            )
    if primary_evidence == "multi_sample_detected":
        if rescue_cell_only_block:
            return (
                True,
                "production_family",
                "review",
                PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON,
            )
        return True, "production_family", "medium", "multi_sample_detected"
    if primary_evidence == "anchored_family":
        return (
            False,
            "provisional_discovery",
            "review",
            "anchored_single_detected_phase_a",
        )
    return (
        False,
        "provisional_discovery",
        "review",
        "insufficient_detected_identity_support",
    )


def _is_provisional_retention_candidate(
    cluster: Any,
    *,
    evidence: str,
    primary_evidence: str,
    q_detected: int,
    q_rescue: int,
    duplicate_count: int,
    ambiguous_count: int,
    policy_evidence: BackfillPromotionEvidence,
) -> bool:
    if bool(getattr(cluster, "review_only", False)):
        return False
    if _is_consolidation_loser(evidence):
        return False
    if primary_evidence in {"none", "single_sample_local_owner"}:
        return False
    if q_detected != 1 or q_rescue <= 0:
        return False
    if duplicate_count != 0 or ambiguous_count != 0:
        return False
    supported_rescue_count = sum(
        1
        for cell in policy_evidence.cells
        if (
            cell.status == "rescued"
            and cell.is_rescued_quantifiable
            and cell.supported_for_backfill
        )
    )
    return supported_rescue_count == q_rescue


def _row_flags(
    *,
    cluster: Any,
    evidence: str,
    primary_evidence: str,
    q_detected: int,
    q_rescue: int,
    duplicate_count: int,
    ambiguous_count: int,
    backfill_dependency: str | None,
) -> list[str]:
    flags: list[str] = []
    if primary_evidence == "single_sample_local_owner":
        flags.append("single_sample_local_owner")
    if q_detected == 1:
        flags.append("single_detected_seed")
    if _is_consolidation_loser(evidence):
        flags.append("family_consolidation_loser")
    if primary_evidence == "anchored_family" and q_detected == 1:
        flags.append("anchored_single_detected")
    if q_rescue > q_detected and q_detected > 0:
        flags.append("rescue_heavy")
    if backfill_dependency == EXTREME_BACKFILL_REASON:
        flags.append("high_backfill_dependency")
    if backfill_dependency == WEAK_SEED_BACKFILL_REASON:
        flags.append("weak_seed_backfill_dependency")
    if backfill_dependency == WEAK_SEED_TOLERATED_REASON:
        flags.append("weak_seed_tolerated")
    if q_detected == 0 and q_rescue > 0:
        flags.append("rescue_only")
    if duplicate_count > 0:
        flags.append("duplicate_claim_pressure")
    if q_detected == 0 and duplicate_count > 0 and q_rescue == 0:
        flags.append("duplicate_only")
    if ambiguous_count > 0:
        flags.append("ambiguous_ms1_owner_pressure")
    if q_detected == 0 and ambiguous_count > 0 and q_rescue == 0:
        flags.append("ambiguous_only")
    if q_detected == 0 and q_rescue == 0 and duplicate_count == 0:
        flags.append("zero_present")
    return flags


def _primary_evidence(
    cluster: Any,
    evidence: str,
    cell_quality: Sequence[CellQualityDecision],
) -> str:
    q_detected = sum(
        1 for decision in cell_quality if decision.is_detected_identity_support
    )
    if evidence == "single_sample_local_owner":
        return "single_sample_local_owner"
    if evidence.startswith("owner_complete_link"):
        return "owner_complete_link"
    if evidence.startswith("owner_identity"):
        return "owner_identity"
    if evidence.startswith("cid_nl_only"):
        return "cid_nl_only"
    if q_detected >= 2:
        return "multi_sample_detected"
    if q_detected == 1 and bool(getattr(cluster, "has_anchor", False)):
        return "anchored_family"
    return evidence or "none"


def _is_consolidation_loser(evidence: str) -> bool:
    return (
        "primary_family_consolidation_loser" in evidence
        or "pre_backfill_identity_consolidation_loser" in evidence
    )


def _single_dr_backfill_context(
    cluster: Any,
    *,
    q_detected: int,
    q_rescue: int,
    cell_count: int,
    cells: Sequence[AlignedCell],
) -> tuple[str | None, SeedQualitySummary]:
    candidates = _seed_candidates_by_id(cluster)
    seed_quality = summarize_detected_seed_quality(
        tuple(
            DetectedSeedRef(
                sample_stem=cell.sample_stem,
                source_candidate_id=cell.source_candidate_id or "",
            )
            for cell in cells
            if cell.status == "detected"
        ),
        candidates,
        enrichment_available=bool(candidates),
    )
    return (
        classify_single_dr_backfill_dependency(
            neutral_loss_tag=str(getattr(cluster, "neutral_loss_tag", "")),
            q_detected=q_detected,
            q_rescue=q_rescue,
            cell_count=cell_count,
            seed_quality=seed_quality,
        ),
        seed_quality,
    )


def _seed_candidates_by_id(cluster: Any) -> dict[str, Any]:
    candidates: dict[str, Any] = {}
    for candidate in _cluster_seed_candidates(cluster):
        candidate_id = str(getattr(candidate, "candidate_id", "") or "")
        if candidate_id:
            candidates[candidate_id] = candidate
    return candidates


def _cluster_seed_candidates(cluster: Any) -> tuple[Any, ...]:
    members = getattr(cluster, "members", ())
    if members:
        return tuple(
            candidate
            for member in members
            for candidate in _seed_candidates_from_member(member)
        )
    return ()


def _seed_candidates_from_member(member: Any) -> tuple[Any, ...]:
    events = getattr(member, "all_events", None)
    if events is not None:
        return tuple(events)
    primary = getattr(member, "primary_identity_event", None)
    supporting = getattr(member, "supporting_events", ())
    if primary is not None:
        return (primary, *tuple(supporting))
    return (member,)


def _cells_by_family(
    cells: Sequence[AlignedCell],
) -> dict[str, tuple[AlignedCell, ...]]:
    grouped: dict[str, list[AlignedCell]] = {}
    for cell in cells:
        grouped.setdefault(cell.cluster_id, []).append(cell)
    return {
        family_id: tuple(sorted(items, key=lambda item: item.sample_stem))
        for family_id, items in grouped.items()
    }


def _family_evidence(cluster: Any) -> str:
    if hasattr(cluster, "evidence"):
        return str(cluster.evidence)
    if hasattr(cluster, "fold_evidence"):
        return str(cluster.fold_evidence)
    return ""
