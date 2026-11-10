"""Writer-facing Matrix decisions minted by the finalized Untargeted owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
    identity_reason: str
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
