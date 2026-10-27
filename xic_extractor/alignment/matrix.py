from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from xic_extractor.alignment.models import AlignmentCluster
from xic_extractor.alignment.primary_matrix_area import (
    primary_matrix_area_from_integration,
)

if TYPE_CHECKING:
    from xic_extractor.peak_detection.hypotheses import IntegrationResult
else:
    IntegrationResult = Any

CellStatus = Literal[
    "detected",
    "backfilled",
    "absent",
    "unchecked",
    "ambiguous_ms1_owner",
    "duplicate_assigned",
]


class AlignmentRowLike(Protocol):
    @property
    def neutral_loss_tag(self) -> str: ...

    @property
    def has_anchor(self) -> bool: ...


@dataclass(frozen=True)
class AlignedCell:
    sample_stem: str
    cluster_id: str
    status: CellStatus
    area: float | None
    apex_rt: float | None
    height: float | None
    peak_start_rt: float | None
    peak_end_rt: float | None
    rt_delta_sec: float | None
    trace_quality: str
    scan_support_score: float | None
    source_candidate_id: str | None
    reason: str
    selected_integration: IntegrationResult | None = None
    backfill_seed_mz: float | None = None
    backfill_seed_rt: float | None = None
    backfill_request_rt_min: float | None = None
    backfill_request_rt_max: float | None = None
    backfill_request_ppm: float | None = None
    group_hypothesis_id: str = ""
    feature_id: str = ""
    group_construction_role: str = ""
    group_delivery_role: str = ""
    group_membership_source: str = ""
    gap_fill_state: str = ""
    gap_fill_reason: str = ""
    missing_observation_state: str = ""

    @property
    def matrix_area(self) -> float | None:
        return primary_matrix_area_from_integration(self.selected_integration).value

    @property
    def matrix_area_source(self) -> str:
        decision = primary_matrix_area_from_integration(self.selected_integration)
        return decision.source or self.matrix_area_missing_reason

    @property
    def matrix_area_missing_reason(self) -> str:
        if self.status not in {"detected", "backfilled"}:
            return ""
        return primary_matrix_area_from_integration(self.selected_integration).reason


@dataclass(frozen=True)
class AlignmentMatrix:
    clusters: tuple[AlignmentCluster | AlignmentRowLike, ...]
    cells: tuple[AlignedCell, ...]
    sample_order: tuple[str, ...]


def cells_by_cluster(matrix: AlignmentMatrix) -> dict[str, tuple[AlignedCell, ...]]:
    grouped: dict[str, list[AlignedCell]] = {}
    for cell in matrix.cells:
        grouped.setdefault(cell.cluster_id, []).append(cell)
    return {cluster_id: tuple(cells) for cluster_id, cells in grouped.items()}


def row_id(row: Any) -> str:
    if hasattr(row, "feature_id"):
        return str(row.feature_id)
    return str(row.cluster_id)
