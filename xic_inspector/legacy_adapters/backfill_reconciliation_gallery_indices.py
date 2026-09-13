"""Indexing and row adapters for the backfill reconciliation gallery."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from xic_extractor.diagnostics.diagnostic_io import (
    bool_value,
    optional_float,
    split_semicolon_labels,
    text_value,
)

from .backfill_reconciliation_gallery_models import (
    RECONCILIATION_CLASS_PRIORITY,
    ActivationDeltaCell,
    ReconciliationGroup,
    RepresentativeCell,
    ShadowPolicyCell,
    ShadowProjectionCell,
    TargetBenchmarkContext,
)

_CLASS_PRIORITY = {
    name: index for index, name in enumerate(RECONCILIATION_CLASS_PRIORITY)
}
_ROLE_PRIORITY = {
    "strongest_support": 0,
    "strongest_blocker": 1,
    "lowest_similarity": 2,
    "highest_interference": 3,
    "seed_representative": 4,
    "product_disagreement_example": 5,
}


def _shadow_policy_cell_from_row(row: Mapping[str, str]) -> ShadowPolicyCell:
    return ShadowPolicyCell(
        feature_family_id=text_value(row.get("feature_family_id")),
        seed_group_id=text_value(row.get("seed_group_id")),
        sample_stem=text_value(row.get("sample_stem")),
        current_product_cell_state=text_value(row.get("current_product_cell_state")),
        shadow_policy_decision=text_value(row.get("shadow_policy_decision")),
        decision_reason=text_value(row.get("decision_reason")),
        production_gap=text_value(row.get("production_gap")),
        diagnostic_authority=text_value(row.get("diagnostic_authority")),
        cell_status=text_value(row.get("cell_status")),
        evidence_gate_status=text_value(row.get("evidence_gate_status")),
        overlay_family_verdict=text_value(row.get("overlay_family_verdict")),
        own_max_shape_supported_fraction=text_value(
            row.get("own_max_shape_supported_fraction"),
        ),
        absolute_trace_apex_cluster_fraction=text_value(
            row.get("absolute_trace_apex_cluster_fraction"),
        ),
        support_components=text_value(row.get("support_components")),
        blockers=text_value(row.get("blockers")),
        missing_evidence=text_value(row.get("missing_evidence")),
        overlay_png_path=text_value(row.get("overlay_png_path")),
    )


def _shadow_projection_cell_from_row(
    row: Mapping[str, str],
) -> ShadowProjectionCell:
    return ShadowProjectionCell(
        feature_family_id=text_value(row.get("feature_family_id")),
        seed_group_id=text_value(row.get("seed_group_id")),
        sample_stem=text_value(row.get("sample_stem")),
        current_raw_status=text_value(row.get("current_raw_status")),
        current_production_status=text_value(row.get("current_production_status")),
        current_rescue_tier=text_value(row.get("current_rescue_tier")),
        current_matrix_written=bool_value(row.get("current_matrix_written")) is True,
        current_matrix_value=text_value(row.get("current_matrix_value")),
        current_blank_reason=text_value(row.get("current_blank_reason")),
        current_matrix_source=text_value(row.get("current_matrix_source")),
        review_rescued_cell=bool_value(row.get("review_rescued_cell")) is True,
        shadow_decision=text_value(row.get("shadow_decision")),
        shadow_reasons=tuple(split_semicolon_labels(row.get("shadow_reasons"))),
        shadow_warnings=tuple(split_semicolon_labels(row.get("shadow_warnings"))),
        projected_matrix_written=(
            bool_value(row.get("projected_matrix_written")) is True
        ),
        projected_matrix_value=text_value(row.get("projected_matrix_value")),
        projection_authority=text_value(row.get("projection_authority")),
        product_authority_chain=text_value(row.get("product_authority_chain")),
        detected_anchor_count=text_value(row.get("detected_anchor_count")),
        rescued_cell_count=text_value(row.get("rescued_cell_count")),
        request_window_overlap=(
            text_value(row.get("request_window_overlap"))
            or text_value(row.get("same_peak_segment"))
        ),
        local_global_ratio=text_value(row.get("local_global_ratio")),
        evidence_gate_status=text_value(row.get("evidence_gate_status")),
        support_components=tuple(split_semicolon_labels(row.get("support_components"))),
        hard_blockers=tuple(split_semicolon_labels(row.get("hard_blockers"))),
        missing_evidence=tuple(split_semicolon_labels(row.get("missing_evidence"))),
        overlay_verdict=text_value(row.get("overlay_verdict")),
        overlay_png_path=text_value(row.get("overlay_png_path")),
    )


def _activation_delta_cell_from_row(
    row: Mapping[str, str],
) -> ActivationDeltaCell:
    return ActivationDeltaCell(
        feature_family_id=text_value(row.get("feature_family_id")),
        sample_id=text_value(row.get("sample_id")),
        activation_status=text_value(row.get("activation_status")),
        product_effect=text_value(row.get("product_effect")),
        activated_matrix_value=text_value(row.get("activated_matrix_value")),
        matrix_value_effect=text_value(row.get("matrix_value_effect")),
        activation_reason=text_value(row.get("activation_reason")),
    )


def _target_benchmark_context_from_row(
    row: Mapping[str, str],
) -> TargetBenchmarkContext | None:
    selected_feature_id = text_value(row.get("selected_feature_id"))
    primary_feature_ids = split_semicolon_labels(row.get("primary_feature_ids"))
    if not selected_feature_id and not primary_feature_ids:
        return None
    return TargetBenchmarkContext(
        target_label=text_value(row.get("target_label")),
        role=text_value(row.get("role")),
        active_tag=text_value(row.get("active_tag")),
        status=text_value(row.get("status")),
        selected_feature_id=selected_feature_id,
        primary_feature_ids=tuple(primary_feature_ids),
        targeted_positive_count=text_value(row.get("targeted_positive_count")),
        untargeted_positive_count=text_value(row.get("untargeted_positive_count")),
        coverage_minimum=text_value(row.get("coverage_minimum")),
        failure_modes=tuple(split_semicolon_labels(row.get("failure_modes"))),
        note=text_value(row.get("note")),
    )


def _target_benchmark_contexts_by_family(
    contexts: Sequence[TargetBenchmarkContext],
) -> dict[str, tuple[TargetBenchmarkContext, ...]]:
    grouped: dict[str, list[TargetBenchmarkContext]] = {}
    for context in contexts:
        for family in context.feature_family_ids:
            grouped.setdefault(family, []).append(context)
    return {
        family: tuple(sorted(items, key=_target_benchmark_context_sort_key))
        for family, items in grouped.items()
    }


def _target_benchmark_context_counts(
    contexts: Sequence[TargetBenchmarkContext],
) -> dict[str, int]:
    counts = Counter(context.status or "UNKNOWN" for context in contexts)
    return dict(sorted(counts.items()))


def _target_benchmark_context_sort_key(
    context: TargetBenchmarkContext,
) -> tuple[str, str, str]:
    return (
        context.status,
        context.target_label,
        context.selected_feature_id,
    )


def _shadow_policy_cells_by_group(
    cells: Sequence[ShadowPolicyCell],
) -> dict[tuple[str, str], tuple[ShadowPolicyCell, ...]]:
    grouped: dict[tuple[str, str], list[ShadowPolicyCell]] = {}
    for cell in cells:
        if not cell.feature_family_id or not cell.seed_group_id:
            continue
        grouped.setdefault(
            (cell.feature_family_id, cell.seed_group_id),
            [],
        ).append(cell)
    return {
        key: tuple(sorted(items, key=_shadow_policy_cell_sort_key))
        for key, items in grouped.items()
    }


def _shadow_projection_cells_by_group(
    cells: Sequence[ShadowProjectionCell],
) -> dict[tuple[str, str], tuple[ShadowProjectionCell, ...]]:
    grouped: dict[tuple[str, str], list[ShadowProjectionCell]] = {}
    for cell in cells:
        if not cell.feature_family_id or not cell.seed_group_id:
            continue
        grouped.setdefault(
            (cell.feature_family_id, cell.seed_group_id),
            [],
        ).append(cell)
    return {
        key: tuple(sorted(items, key=_shadow_projection_cell_sort_key))
        for key, items in grouped.items()
    }


def _shadow_policy_cells_by_family(
    cells: Sequence[ShadowPolicyCell],
) -> dict[str, tuple[ShadowPolicyCell, ...]]:
    grouped: dict[str, list[ShadowPolicyCell]] = {}
    for cell in cells:
        if cell.feature_family_id:
            grouped.setdefault(cell.feature_family_id, []).append(cell)
    return {
        family: tuple(sorted(items, key=_shadow_policy_cell_sort_key))
        for family, items in grouped.items()
    }


def _shadow_projection_cells_by_family(
    cells: Sequence[ShadowProjectionCell],
) -> dict[str, tuple[ShadowProjectionCell, ...]]:
    grouped: dict[str, list[ShadowProjectionCell]] = {}
    for cell in cells:
        if cell.feature_family_id:
            grouped.setdefault(cell.feature_family_id, []).append(cell)
    return {
        family: tuple(sorted(items, key=_shadow_projection_cell_sort_key))
        for family, items in grouped.items()
    }


def _shadow_policy_cells_for_family_groups(
    groups: Sequence[ReconciliationGroup],
    *,
    shadow_policy_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowPolicyCell, ...],
    ],
    shadow_policy_cells_by_family: Mapping[str, tuple[ShadowPolicyCell, ...]],
) -> tuple[ShadowPolicyCell, ...]:
    exact: list[ShadowPolicyCell] = []
    for group in groups:
        exact.extend(
            shadow_policy_cells_by_group.get(
                (group.feature_family_id, group.seed_group_id),
                (),
            ),
        )
    if exact:
        return tuple(sorted(exact, key=_shadow_policy_cell_sort_key))
    family = groups[0].feature_family_id if groups else ""
    return shadow_policy_cells_by_family.get(family, ())


def _shadow_projection_cells_for_family_groups(
    groups: Sequence[ReconciliationGroup],
    *,
    shadow_projection_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowProjectionCell, ...],
    ],
    shadow_projection_cells_by_family: Mapping[str, tuple[ShadowProjectionCell, ...]],
) -> tuple[ShadowProjectionCell, ...]:
    exact: list[ShadowProjectionCell] = []
    for group in groups:
        exact.extend(
            shadow_projection_cells_by_group.get(
                (group.feature_family_id, group.seed_group_id),
                (),
            ),
        )
    if exact:
        return tuple(sorted(exact, key=_shadow_projection_cell_sort_key))
    family = groups[0].feature_family_id if groups else ""
    return shadow_projection_cells_by_family.get(family, ())


def _shadow_policy_cells_for_group(
    group: ReconciliationGroup,
    *,
    shadow_policy_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowPolicyCell, ...],
    ],
    shadow_policy_cells_by_family: Mapping[str, tuple[ShadowPolicyCell, ...]],
    allow_family_fallback: bool,
) -> tuple[ShadowPolicyCell, ...]:
    exact = shadow_policy_cells_by_group.get(
        (group.feature_family_id, group.seed_group_id),
        (),
    )
    if exact or not allow_family_fallback:
        return exact
    return shadow_policy_cells_by_family.get(group.feature_family_id, ())


def _shadow_projection_cells_for_group(
    group: ReconciliationGroup,
    *,
    shadow_projection_cells_by_group: Mapping[
        tuple[str, str],
        tuple[ShadowProjectionCell, ...],
    ],
    shadow_projection_cells_by_family: Mapping[str, tuple[ShadowProjectionCell, ...]],
    allow_family_fallback: bool,
) -> tuple[ShadowProjectionCell, ...]:
    exact = shadow_projection_cells_by_group.get(
        (group.feature_family_id, group.seed_group_id),
        (),
    )
    if exact or not allow_family_fallback:
        return exact
    return shadow_projection_cells_by_family.get(group.feature_family_id, ())


def _shadow_policy_compact_summary(
    cells: Sequence[ShadowPolicyCell],
) -> str:
    if not cells:
        return ""
    counts = Counter(cell.shadow_policy_decision for cell in cells)
    labels = (
        ("fill_now", "fill"),
        ("would_fill_under_ms1_rt_policy", "would"),
        ("needs_ms1_same_peak_evidence", "needs MS1"),
        ("blocked", "block"),
    )
    parts = [f"{label} {counts[key]}" for key, label in labels if counts[key]]
    return "shadow: " + " · ".join(parts)


def _shadow_projection_compact_summary(
    cells: Sequence[ShadowProjectionCell],
) -> str:
    if not cells:
        return ""
    current = sum(cell.current_matrix_written for cell in cells)
    review_only = sum(
        cell.shadow_decision == "context" and not cell.current_matrix_written
        for cell in cells
    )
    block = sum(cell.shadow_decision == "block" for cell in cells)
    accept = sum(cell.shadow_decision == "accept" for cell in cells)
    projected_new = sum(
        not cell.current_matrix_written and cell.projected_matrix_written
        for cell in cells
    )
    if review_only and not accept and not block and not current:
        label = "candidate cell" if review_only == 1 else "candidate cells"
        return f"review-only: {review_only} {label}"
    if current and not accept and not block and not review_only:
        label = "cell" if current == 1 else "cells"
        return f"already written: {current} current {label}"
    parts = []
    if current:
        parts.append(f"current {current}")
    if accept:
        parts.append(f"write {accept}")
    if block:
        parts.append(f"block {block}")
    if review_only:
        parts.append(f"review-only {review_only}")
    if projected_new:
        parts.append(f"+{projected_new} matrix")
    return "projection: " + " · ".join(parts)


def _shadow_policy_decision_counts(
    cells: Sequence[ShadowPolicyCell],
) -> dict[str, int]:
    counts = Counter(cell.shadow_policy_decision for cell in cells)
    return {key: counts[key] for key in sorted(counts) if key}


def _shadow_projection_decision_counts(
    cells: Sequence[ShadowProjectionCell],
) -> dict[str, int]:
    counts = Counter(cell.shadow_decision for cell in cells)
    return {key: counts[key] for key in sorted(counts) if key}


def _shadow_projection_matrix_counts(
    cells: Sequence[ShadowProjectionCell],
) -> dict[str, int]:
    return {
        "current_decision_written": sum(cell.current_matrix_written for cell in cells),
        "projected_decision_written": sum(
            cell.projected_matrix_written for cell in cells
        ),
        "projected_new_decision_write": sum(
            not cell.current_matrix_written and cell.projected_matrix_written
            for cell in cells
        ),
        "review_rescued_target": sum(cell.review_rescued_cell for cell in cells),
    }


def _activation_application_summary(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, str]:
    if not rows:
        return {}
    row = rows[0]
    keys = (
        "application_status",
        "activation_output_mode",
        "acceptance_status",
        "decision_rows_total",
        "auto_activate_count",
        "auto_block_count",
        "matrix_cells_written",
        "matrix_cells_blanked",
        "summary_reason",
    )
    return {key: text_value(row.get(key)) for key in keys if text_value(row.get(key))}


def _activation_value_delta_matrix_effect_counts(
    cells: Sequence[ActivationDeltaCell],
) -> dict[str, int]:
    counts = Counter(cell.matrix_value_effect for cell in cells)
    return {key: counts[key] for key in sorted(counts) if key}


def _shadow_policy_production_gap_counts(
    cells: Sequence[ShadowPolicyCell],
) -> dict[str, int]:
    counts = Counter(cell.production_gap for cell in cells if cell.production_gap)
    return {key: counts[key] for key in sorted(counts) if key}


def _shadow_policy_cell_sort_key(cell: ShadowPolicyCell) -> tuple[str, str, str]:
    return (cell.feature_family_id, cell.seed_group_id, cell.sample_stem)


def _shadow_projection_cell_sort_key(
    cell: ShadowProjectionCell,
) -> tuple[str, str, str]:
    return (cell.feature_family_id, cell.seed_group_id, cell.sample_stem)


def _shadow_projection_accept_group_keys(
    cells: Sequence[ShadowProjectionCell],
) -> set[tuple[str, str]]:
    return {
        (cell.feature_family_id, cell.seed_group_id)
        for cell in cells
        if _is_projected_new_accept(cell)
    }


def _activation_written_projection_group_keys(
    projection_cells: Sequence[ShadowProjectionCell],
    activation_cells: Sequence[ActivationDeltaCell],
) -> set[tuple[str, str]]:
    written_cell_keys = _activation_written_cell_keys(activation_cells)
    return {
        (cell.feature_family_id, cell.seed_group_id)
        for cell in projection_cells
        if _is_projected_new_accept(cell)
        and (cell.feature_family_id, cell.sample_stem) in written_cell_keys
    }


def _activation_written_projection_cell_count(
    projection_cells: Sequence[ShadowProjectionCell],
    activation_cells: Sequence[ActivationDeltaCell],
) -> int:
    written_cell_keys = _activation_written_cell_keys(activation_cells)
    return sum(
        1
        for cell in projection_cells
        if _is_projected_new_accept(cell)
        and (cell.feature_family_id, cell.sample_stem) in written_cell_keys
    )


def _current_matrix_written_projection_group_keys(
    projection_cells: Sequence[ShadowProjectionCell],
) -> set[tuple[str, str]]:
    return {
        (cell.feature_family_id, cell.seed_group_id)
        for cell in projection_cells
        if _is_current_backfill_matrix_write(cell)
    }


def _current_matrix_written_projection_cell_count(
    projection_cells: Sequence[ShadowProjectionCell],
) -> int:
    return sum(
        1 for cell in projection_cells if _is_current_backfill_matrix_write(cell)
    )


def _is_current_backfill_matrix_write(cell: ShadowProjectionCell) -> bool:
    return (
        cell.current_matrix_written
        and cell.current_raw_status == "rescued"
        and cell.current_production_status == "accepted_rescue"
    )


def _activation_written_cell_keys(
    cells: Sequence[ActivationDeltaCell],
) -> set[tuple[str, str]]:
    return {
        (cell.feature_family_id, cell.sample_id)
        for cell in cells
        if _is_activation_matrix_write(cell)
    }


def _is_activation_matrix_write(cell: ActivationDeltaCell) -> bool:
    activated_value = optional_float(cell.activated_matrix_value)
    return (
        cell.activation_status == "auto_activate"
        and cell.matrix_value_effect == "written"
        and cell.product_effect in {"", "accept_label_or_rescue"}
        and activated_value is not None
        and activated_value > 0
    )


def _is_projected_new_accept(cell: ShadowProjectionCell) -> bool:
    projected_value = optional_float(cell.projected_matrix_value)
    return (
        cell.shadow_decision == "accept"
        and cell.projected_matrix_written
        and not cell.current_matrix_written
        and projected_value is not None
        and projected_value > 0
    )


def _group_sort_key(group: ReconciliationGroup) -> tuple[int, str, str]:
    return (
        _CLASS_PRIORITY.get(group.reconciliation_class, len(_CLASS_PRIORITY)),
        group.feature_family_id,
        group.seed_group_id,
    )


def _representative_sort_key(cell: RepresentativeCell) -> tuple[str, str, str, int]:
    role = cell.representative_roles[0] if cell.representative_roles else ""
    return (
        cell.feature_family_id,
        cell.seed_group_id,
        cell.sample_stem,
        _ROLE_PRIORITY.get(role, len(_ROLE_PRIORITY)),
    )


def _representatives_by_group(
    cells: Sequence[RepresentativeCell],
) -> dict[tuple[str, str], tuple[RepresentativeCell, ...]]:
    grouped: dict[tuple[str, str], list[RepresentativeCell]] = {}
    for cell in cells:
        grouped.setdefault(
            (cell.feature_family_id, cell.seed_group_id),
            [],
        ).append(cell)
    return {
        key: tuple(sorted(items, key=_representative_sort_key))
        for key, items in grouped.items()
    }
