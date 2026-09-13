"""Data models for the backfill reconciliation gallery."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from xic_extractor.diagnostics.diagnostic_io import text_value

GROUP_TSV_COLUMNS = (
    "schema_version",
    "priority_rank",
    "feature_family_id",
    "seed_group_id",
    "seed_group_basis",
    "seed_mz",
    "seed_rt",
    "seed_rt_window",
    "seed_ppm",
    "tag_or_class",
    "product_behavior_state",
    "evidence_authority_state",
    "reconciliation_class",
    "detected_cell_count",
    "rescued_cell_count",
    "provisional_cell_count",
    "top_product_reason",
    "top_support_component",
    "top_blocker",
    "missing_evidence",
    "overlay_png_path",
    "overlay_trace_json_path",
    "source_artifacts",
    "source_warnings",
)

REPRESENTATIVE_CELL_TSV_COLUMNS = (
    "schema_version",
    "feature_family_id",
    "seed_group_id",
    "representative_roles",
    "sample_stem",
    "cell_status",
    "product_cell_state",
    "shape_similarity",
    "scan_support_score",
    "apex_delta_sec",
    "boundary_overlap",
    "interference_signal",
    "representative_reason",
    "source_row_key",
)

EVIDENCE_AUTHORITY_STATES = (
    "product_grade_support",
    "review_only_visual_support",
    "machine_support_no_overlay",
    "dependent_context_only",
    "human_visual_judgment_only",
    "evidence_blocks_backfill",
    "evidence_inconclusive",
    "not_assessable",
)

RECONCILIATION_CLASSES = (
    "product_accepts_and_product_grade_supports",
    "product_accepts_and_visual_supports",
    "product_rejects_but_product_grade_supports",
    "product_rejects_but_visual_supports",
    "product_accepts_but_evidence_conflicts",
    "product_rejects_and_evidence_blocks",
    "machine_support_no_overlay",
    "evidence_inconclusive",
    "not_assessable_missing_overlay",
    "not_assessable_missing_seed_provenance",
    "not_assessable_join_gap",
)

RECONCILIATION_CLASS_PRIORITY = (
    "product_rejects_but_product_grade_supports",
    "product_rejects_but_visual_supports",
    "product_accepts_but_evidence_conflicts",
    "not_assessable_missing_overlay",
    "not_assessable_missing_seed_provenance",
    "not_assessable_join_gap",
    "evidence_inconclusive",
    "product_accepts_and_visual_supports",
    "machine_support_no_overlay",
    "product_accepts_and_product_grade_supports",
    "product_rejects_and_evidence_blocks",
)


@dataclass(frozen=True)
class RepresentativeCell:
    feature_family_id: str
    seed_group_id: str
    representative_roles: tuple[str, ...]
    sample_stem: str
    cell_status: str
    product_cell_state: str = ""
    shape_similarity: str = ""
    scan_support_score: str = ""
    apex_delta_sec: str = ""
    boundary_overlap: str = ""
    interference_signal: str = ""
    source_peak_hypothesis_id: str = ""
    successor_peak_hypothesis_id: str = ""
    successor_decision: str = ""
    representative_reason: str = ""
    source_row_key: str = ""


@dataclass(frozen=True)
class ShadowPolicyCell:
    feature_family_id: str
    seed_group_id: str
    sample_stem: str
    current_product_cell_state: str
    shadow_policy_decision: str
    decision_reason: str
    production_gap: str = ""
    diagnostic_authority: str = ""
    cell_status: str = ""
    evidence_gate_status: str = ""
    overlay_family_verdict: str = ""
    own_max_shape_supported_fraction: str = ""
    absolute_trace_apex_cluster_fraction: str = ""
    support_components: str = ""
    blockers: str = ""
    missing_evidence: str = ""
    overlay_png_path: str = ""


@dataclass(frozen=True)
class ShadowProjectionCell:
    feature_family_id: str
    seed_group_id: str
    sample_stem: str
    current_raw_status: str
    current_production_status: str
    current_rescue_tier: str
    current_matrix_written: bool
    current_matrix_value: str
    current_blank_reason: str
    current_matrix_source: str
    review_rescued_cell: bool
    shadow_decision: str
    shadow_reasons: tuple[str, ...] = ()
    shadow_warnings: tuple[str, ...] = ()
    projected_matrix_written: bool = False
    projected_matrix_value: str = ""
    projection_authority: str = ""
    product_authority_chain: str = ""
    detected_anchor_count: str = ""
    rescued_cell_count: str = ""
    request_window_overlap: str = ""
    local_global_ratio: str = ""
    evidence_gate_status: str = ""
    support_components: tuple[str, ...] = ()
    hard_blockers: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    overlay_verdict: str = ""
    overlay_png_path: str = ""


@dataclass(frozen=True)
class ActivationDeltaCell:
    feature_family_id: str
    sample_id: str
    activation_status: str
    product_effect: str
    activated_matrix_value: str
    matrix_value_effect: str
    activation_reason: str = ""


@dataclass(frozen=True)
class TargetBenchmarkContext:
    target_label: str
    role: str
    active_tag: str
    status: str
    selected_feature_id: str
    primary_feature_ids: tuple[str, ...] = ()
    targeted_positive_count: str = ""
    untargeted_positive_count: str = ""
    coverage_minimum: str = ""
    failure_modes: tuple[str, ...] = ()
    note: str = ""

    @property
    def feature_family_ids(self) -> tuple[str, ...]:
        return tuple(
            _ordered_unique((*self.primary_feature_ids, self.selected_feature_id)),
        )


@dataclass(frozen=True)
class _ShiftAwareSamePatternEvidence:
    support: bool
    review_required: bool
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReconciliationGroup:
    feature_family_id: str
    seed_group_id: str
    seed_group_basis: str
    seed_mz: str = ""
    seed_rt: str = ""
    seed_rt_window: str = ""
    seed_ppm: str = ""
    tag_or_class: str = ""
    product_behavior_state: str = "product_unknown"
    evidence_authority_state: str = "not_assessable"
    reconciliation_class: str = "evidence_inconclusive"
    include_in_primary_matrix: bool = False
    identity_decision: str = ""
    row_flags: str = ""
    family_evidence: str = ""
    accepted_cell_count: int = 0
    detected_cell_count: int = 0
    rescued_cell_count: int = 0
    provisional_cell_count: int = 0
    seed_detected_anchor_count: int = 0
    duplicate_assigned_cell_count: int = 0
    cell_total_count: int = 0
    top_product_reason: str = ""
    top_support_component: str = ""
    top_blocker: str = ""
    missing_evidence: tuple[str, ...] = ()
    overlay_png_path: str = ""
    overlay_trace_json_path: str = ""
    family_pattern_png_path: str = ""
    family_pattern_trace_json_path: str = ""
    family_pattern_verdict: str = ""
    overlay_evidence_notes: tuple[str, ...] = ()
    source_artifacts: tuple[str, ...] = ()
    source_warnings: tuple[str, ...] = ()
    product_grade_support_components: tuple[str, ...] = ()
    review_only_visual_components: tuple[str, ...] = ()
    dependent_context_components: tuple[str, ...] = ()
    blocker_components: tuple[str, ...] = ()
    representative_cells: tuple[RepresentativeCell, ...] = ()


@dataclass(frozen=True)
class ReconciliationIndex:
    groups: tuple[ReconciliationGroup, ...]
    representative_cells: tuple[RepresentativeCell, ...]
    shadow_policy_cells: tuple[ShadowPolicyCell, ...] = ()
    shadow_projection_cells: tuple[ShadowProjectionCell, ...] = ()
    activation_delta_cells: tuple[ActivationDeltaCell, ...] = ()
    target_benchmark_contexts: tuple[TargetBenchmarkContext, ...] = ()
    summary: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ReconciliationOutputs:
    groups_tsv: Path
    representative_cells_tsv: Path
    summary_json: Path
    gallery_html: Path


@dataclass(frozen=True)
class _GalleryRenderContext:
    all_groups: tuple[ReconciliationGroup, ...]
    html_groups: tuple[ReconciliationGroup, ...]
    html_shadow_policy_cells: tuple[ShadowPolicyCell, ...]
    html_shadow_projection_cells: tuple[ShadowProjectionCell, ...]
    representatives_by_group: Mapping[
        tuple[str, str],
        tuple[RepresentativeCell, ...],
    ]
    shadow_policy_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowPolicyCell, ...],
    ]
    shadow_policy_cells_by_family: Mapping[str, tuple[ShadowPolicyCell, ...]]
    shadow_projection_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowProjectionCell, ...],
    ]
    shadow_projection_cells_by_family: Mapping[str, tuple[ShadowProjectionCell, ...]]
    target_benchmark_contexts_by_family: Mapping[
        str,
        tuple[TargetBenchmarkContext, ...],
    ]


@dataclass(frozen=True)
class _SeedRecord:
    seed_group_id: str
    seed_group_basis: str
    seed_mz: str = ""
    seed_rt: str = ""
    rt_start: str = ""
    rt_end: str = ""
    ppm: str = ""
    samples: frozenset[str] = frozenset()

    @property
    def seed_rt_window(self) -> str:
        if self.rt_start or self.rt_end:
            return f"{self.rt_start or 'unknown'}-{self.rt_end or 'unknown'}"
        return ""


def _ordered_unique(values: Iterable[object]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = text_value(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)
