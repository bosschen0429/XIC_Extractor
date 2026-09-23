"""Models and constants for untargeted alignment guardrails."""

from __future__ import annotations

from dataclasses import dataclass

PRODUCTION_STATUSES = {"detected", "backfilled"}
COMPARISON_METRICS = [
    "duplicate_only_families",
    "zero_present_families",
    "review_backfill_count",
    "rescue_only_review_families",
    "identity_anchor_lost_families",
    "duplicate_claim_pressure_families",
    "negative_checkpoint_production_families",
]
TARGETED_ISTD_BENCHMARK_COLUMNS = [
    "metric",
    "value",
    "threshold",
    "status",
    "note",
]
CASE_SUMMARY_COLUMNS = [
    "case",
    "production_family_count",
    "owner_count",
    "event_count",
    "supporting_event_count",
    "strong_edge_count",
    "preserved_split_or_ambiguous",
    "status",
    "reason",
]


@dataclass(frozen=True)
class CaseWindow:
    name: str
    mz: float
    ppm: float
    rt_min: float
    rt_max: float


@dataclass(frozen=True)
class CaseAssertion:
    production_family_count: int
    owner_count: int
    event_count: int
    supporting_event_count: int
    strong_edge_count: int
    preserved_split_or_ambiguous: bool
    status: str
    reason: str


@dataclass(frozen=True)
class GuardrailMetrics:
    zero_present_families: int
    duplicate_only_families: int
    high_backfill_dependency_families: int
    negative_8oxodg_production_families: int
    negative_checkpoint_production_families: int
    accepted_quantitative_cells: int
    accepted_backfill_cells: int
    accepted_backfill_rate: float
    review_backfill_count: int
    rescue_only_review_families: int
    identity_anchor_lost_families: int
    duplicate_claim_pressure_families: int
    istd_false_missing_recovery: int
    case_assertions: dict[str, CaseAssertion]


CASE_WINDOWS = [
    CaseWindow("case1_mz242_5medC_like", 242.114, 20.0, 11.0, 13.2),
    CaseWindow("case2_mz296_dense_duplicate", 296.074, 20.0, 19.2, 20.0),
    CaseWindow("case3_mz322_dense_duplicate", 322.143, 20.0, 22.4, 24.1),
    CaseWindow("case4_mz251_anchor_shadow_duplicates", 251.084, 20.0, 8.0, 9.0),
]
