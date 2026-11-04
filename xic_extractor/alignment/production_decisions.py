from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from xic_extractor.alignment.cell_quality import (
    CellQualityDecision,
    build_cell_quality_decisions,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import (
    AlignedCell,
    AlignmentMatrix,
    cells_by_cluster,
    row_id,
)
from xic_extractor.alignment.matrix_identity import (
    MatrixIdentityRowDecision,
    build_matrix_identity_decisions,
)
from xic_extractor.alignment.promotion_policy import (
    BACKFILL_CELL_EVIDENCE_REQUIRED_FLAG,
    LOW_MS1_COVERAGE_BLOCKED_REASON,
    MISSING_BACKFILL_EVIDENCE_BLOCKED_REASON,
    NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
    cell_evidence_from_aligned,
)

ProductionStatus = Literal[
    "detected",
    "accepted_backfill",
    "review_backfill",
    "rejected_backfill",
    "blank",
]
BackfillTier = Literal["", "accepted_backfill", "review_backfill", "rejected_backfill"]


@dataclass(frozen=True)
class ProductionCellDecision:
    feature_id: str
    sample_stem: str
    raw_status: str
    production_status: ProductionStatus
    backfill_tier: BackfillTier
    write_matrix_value: bool
    matrix_value: float | None
    blank_reason: str


@dataclass(frozen=True)
class ProductionRowDecision:
    feature_id: str
    include_in_primary_matrix: bool
    identity_decision: str
    identity_confidence: str
    primary_evidence: str
    identity_reason: str
    quantifiable_detected_count: int
    quantifiable_backfill_count: int
    accepted_cell_count: int
    detected_count: int
    accepted_backfill_count: int
    review_backfill_count: int
    duplicate_assigned_count: int
    ambiguous_ms1_owner_count: int
    row_flags: tuple[str, ...]


@dataclass(frozen=True)
class ProductionDecisionSet:
    cells: dict[tuple[str, str], ProductionCellDecision]
    rows: dict[str, ProductionRowDecision]

    def cell(self, feature_id: str, sample_stem: str) -> ProductionCellDecision:
        return self.cells[(feature_id, sample_stem)]

    def row(self, feature_id: str) -> ProductionRowDecision:
        return self.rows[feature_id]


def build_production_decisions(
    matrix: AlignmentMatrix,
    config: AlignmentConfig,
) -> ProductionDecisionSet:
    grouped_cells = cells_by_cluster(matrix)
    quality_decisions = build_cell_quality_decisions(matrix.cells, config)
    identity_decisions = build_matrix_identity_decisions(
        matrix,
        config,
        cell_quality=quality_decisions,
    )
    cell_decisions: dict[tuple[str, str], ProductionCellDecision] = {}
    row_decisions: dict[str, ProductionRowDecision] = {}

    for cluster in matrix.clusters:
        cluster_id = row_id(cluster)
        cluster_cells = grouped_cells.get(cluster_id, ())
        identity_row = identity_decisions.row(cluster_id)

        decisions: list[ProductionCellDecision] = []
        for cell in cluster_cells:
            decision = _cell_decision(
                cell,
                quality_decisions[(cell.cluster_id, cell.sample_stem)],
                identity_row=identity_row,
            )
            cell_decisions[(cell.cluster_id, cell.sample_stem)] = decision
            decisions.append(decision)
        row_decisions[cluster_id] = _row_decision(
            cluster_id,
            cluster_cells,
            tuple(decisions),
            identity_row=identity_row,
        )

    return ProductionDecisionSet(cells=cell_decisions, rows=row_decisions)


_PRODUCTION_STATUS_RANK: dict[ProductionStatus, int] = {
    "detected": 0,
    "accepted_backfill": 1,
    "review_backfill": 2,
    "rejected_backfill": 3,
    "blank": 4,
}
_BACKFILL_TIER_RANK: dict[BackfillTier, int] = {
    "": 0,
    "accepted_backfill": 1,
    "review_backfill": 2,
    "rejected_backfill": 3,
}


def _cell_decision(
    cell: AlignedCell,
    quality: CellQualityDecision,
    *,
    identity_row: MatrixIdentityRowDecision,
) -> ProductionCellDecision:
    if quality.quality_status == "detected_quantifiable":
        if not identity_row.include_in_primary_matrix:
            return _blank(cell, "blank", "", "missing_row_identity_support")
        return ProductionCellDecision(
            feature_id=cell.cluster_id,
            sample_stem=cell.sample_stem,
            raw_status=cell.status,
            production_status="detected",
            backfill_tier="",
            write_matrix_value=True,
            matrix_value=quality.matrix_area,
            blank_reason="",
        )
    if quality.quality_status == "rescue_quantifiable":
        if not identity_row.include_in_primary_matrix:
            return _blank(
                cell,
                "review_backfill",
                "review_backfill",
                "missing_row_identity_support",
            )
        if BACKFILL_CELL_EVIDENCE_REQUIRED_FLAG in identity_row.row_flags:
            backfill_evidence = cell_evidence_from_aligned(cell, quality)
            if not backfill_evidence.supported_for_backfill:
                return _blank(
                    cell,
                    "review_backfill",
                    "review_backfill",
                    _backfill_cell_blank_reason(backfill_evidence),
                )
        return ProductionCellDecision(
            feature_id=cell.cluster_id,
            sample_stem=cell.sample_stem,
            raw_status=cell.status,
            production_status="accepted_backfill",
            backfill_tier="accepted_backfill",
            write_matrix_value=True,
            matrix_value=quality.matrix_area,
            blank_reason="",
        )
    if quality.quality_status == "review_backfill":
        return _blank(
            cell,
            "review_backfill",
            "review_backfill",
            quality.quality_reason,
        )
    if quality.quality_status == "duplicate_loser":
        return _blank(cell, "blank", "", "duplicate_loser")
    if quality.quality_status == "ambiguous_owner":
        return _blank(cell, "blank", "", "ambiguous_ms1_owner")
    if quality.quality_status == "blank" and cell.status == "absent":
        return _blank(cell, "blank", "", "absent")
    if quality.quality_status == "blank" and cell.status == "unchecked":
        return _blank(cell, "blank", "", "unchecked")
    if quality.quality_status == "invalid" and cell.status == "backfilled":
        return _blank(
            cell,
            "rejected_backfill",
            "rejected_backfill",
            quality.quality_reason,
        )
    return _blank(cell, "blank", "", quality.quality_reason)


def _backfill_cell_blank_reason(backfill_evidence: object) -> str:
    if getattr(backfill_evidence, "high_neighbor_interference", False):
        return NEIGHBOR_INTERFERENCE_BLOCKED_REASON
    if getattr(backfill_evidence, "low_assessable_coverage", False):
        return LOW_MS1_COVERAGE_BLOCKED_REASON
    reason = str(getattr(backfill_evidence, "backfill_identity_block_reason", ""))
    return reason or MISSING_BACKFILL_EVIDENCE_BLOCKED_REASON


def _row_decision(
    cluster_id: str,
    cells: tuple[AlignedCell, ...],
    decisions: tuple[ProductionCellDecision, ...],
    *,
    identity_row: MatrixIdentityRowDecision,
) -> ProductionRowDecision:
    detected_count = sum(1 for cell in cells if cell.status == "detected")
    accepted_backfill_count = sum(
        1 for decision in decisions if decision.production_status == "accepted_backfill"
    )
    review_backfill_count = sum(
        1 for decision in decisions if decision.production_status == "review_backfill"
    )
    duplicate_count = identity_row.duplicate_assigned_count
    ambiguous_count = identity_row.ambiguous_ms1_owner_count
    accepted_cell_count = sum(
        1 for decision in decisions if decision.write_matrix_value
    )

    flags: list[str] = list(identity_row.row_flags)
    if accepted_cell_count == 0 and review_backfill_count > 0:
        flags.append("rescue_only_review")

    return ProductionRowDecision(
        feature_id=cluster_id,
        include_in_primary_matrix=(
            identity_row.include_in_primary_matrix and accepted_cell_count > 0
        ),
        identity_decision=identity_row.identity_decision,
        identity_confidence=identity_row.identity_confidence,
        primary_evidence=identity_row.primary_evidence,
        identity_reason=identity_row.identity_reason,
        quantifiable_detected_count=identity_row.quantifiable_detected_count,
        quantifiable_backfill_count=identity_row.quantifiable_backfill_count,
        accepted_cell_count=accepted_cell_count,
        detected_count=detected_count,
        accepted_backfill_count=accepted_backfill_count,
        review_backfill_count=review_backfill_count,
        duplicate_assigned_count=duplicate_count,
        ambiguous_ms1_owner_count=ambiguous_count,
        row_flags=tuple(dict.fromkeys(flags)),
    )


def _blank(
    cell: AlignedCell,
    production_status: ProductionStatus,
    backfill_tier: BackfillTier,
    blank_reason: str,
) -> ProductionCellDecision:
    return ProductionCellDecision(
        feature_id=cell.cluster_id,
        sample_stem=cell.sample_stem,
        raw_status=cell.status,
        production_status=production_status,
        backfill_tier=backfill_tier,
        write_matrix_value=False,
        matrix_value=None,
        blank_reason=blank_reason,
    )
