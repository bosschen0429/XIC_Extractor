"""Projection model for canonical physical region audit evidence."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.peak_detection.integration_audit import CellIntegrationAuditSummary


@dataclass(frozen=True)
class PeakRegionAuditSummary:
    candidate_count: int | None = None
    selected_proposal_sources: tuple[str, ...] = ()
    selected_merge_note: str = ""
    shadow_status: str = ""
    shadow_verdict: str = ""
    merge_suggestion_source: str = ""
    area_ratio: float | None = None
    selected_interval_count: int | None = None
    selected_interval_gap_max_min: float | None = None
    local_mixture_diagnostic: str = ""
    local_mixture_reason: str = ""
    review_reason: str = ""
    decision_status: str = ""
    decision_class: str = ""
    product_action: str = ""
    promotion_reason: str = ""
    baseline_method: str = ""
    integration_audit: CellIntegrationAuditSummary | None = None
