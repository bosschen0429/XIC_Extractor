from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from xic_extractor.alignment.models import AlignmentCluster
from xic_extractor.alignment.primary_matrix_area import (
    primary_matrix_area_from_integration,
)
from xic_extractor.peak_detection.integration_audit import CellIntegrationAuditSummary

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
    region_candidate_count: int | None = None
    region_selected_proposal_sources: tuple[str, ...] = ()
    region_selected_merge_note: str = ""
    region_shadow_status: str = ""
    region_shadow_verdict: str = ""
    region_merge_suggestion_source: str = ""
    region_area_ratio: float | None = None
    region_selected_interval_count: int | None = None
    region_selected_interval_gap_max_min: float | None = None
    region_local_mixture_diagnostic: str = ""
    region_local_mixture_reason: str = ""
    region_review_reason: str = ""
    region_decision_status: str = ""
    region_decision_class: str = ""
    region_product_action: str = ""
    region_promotion_reason: str = ""
    region_baseline_method: str = ""
    integration_audit: CellIntegrationAuditSummary | None = None
    selected_integration: IntegrationResult | None = None
    backfill_seed_mz: float | None = None
    backfill_seed_rt: float | None = None
    backfill_request_rt_min: float | None = None
    backfill_request_rt_max: float | None = None
    backfill_request_ppm: float | None = None
    group_hypothesis_id: str = ""
    public_family_id: str = ""
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
