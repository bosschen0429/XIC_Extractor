"""Render-scope planning for the backfill reconciliation gallery."""

from __future__ import annotations

from collections.abc import Sequence

from xic_extractor.diagnostics.backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from xic_extractor.diagnostics.backfill_reconciliation_gallery_indices import (
    _group_sort_key,
    _representatives_by_group,
    _shadow_policy_cells_by_family,
    _shadow_policy_cells_by_group,
    _shadow_projection_accept_group_keys,
    _shadow_projection_cells_by_family,
    _shadow_projection_cells_by_group,
    _target_benchmark_contexts_by_family,
)
from xic_extractor.diagnostics.backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    ReconciliationIndex,
    _GalleryRenderContext,
)

_HTML_FULL_RENDER_GROUP_LIMIT = 1500
_HTML_INCONCLUSIVE_SAMPLE_LIMIT = 200
_HTML_LOW_INFORMATION_CLASSES = {
    "evidence_inconclusive",
    "not_assessable_missing_seed_provenance",
}


def _gallery_render_context(index: ReconciliationIndex) -> _GalleryRenderContext:
    groups = tuple(sorted(index.groups, key=_group_sort_key))
    projection_accept_keys = _shadow_projection_accept_group_keys(
        index.shadow_projection_cells,
    )
    html_groups = _html_render_groups(
        groups,
        projection_accept_keys=projection_accept_keys,
    )
    html_group_keys = {
        (group.feature_family_id, group.seed_group_id) for group in html_groups
    }
    html_family_ids = {group.feature_family_id for group in html_groups}
    html_shadow_policy_cells = tuple(
        cell
        for cell in index.shadow_policy_cells
        if (cell.feature_family_id, cell.seed_group_id) in html_group_keys
    )
    html_shadow_projection_cells = tuple(
        cell
        for cell in index.shadow_projection_cells
        if (cell.feature_family_id, cell.seed_group_id) in html_group_keys
    )
    target_contexts = tuple(
        context
        for context in index.target_benchmark_contexts
        if any(family in html_family_ids for family in context.feature_family_ids)
    )
    return _GalleryRenderContext(
        all_groups=groups,
        html_groups=html_groups,
        html_shadow_policy_cells=html_shadow_policy_cells,
        html_shadow_projection_cells=html_shadow_projection_cells,
        representatives_by_group=_representatives_by_group(
            index.representative_cells,
        ),
        shadow_policy_cells_by_group=_shadow_policy_cells_by_group(
            html_shadow_policy_cells,
        ),
        shadow_policy_cells_by_family=_shadow_policy_cells_by_family(
            html_shadow_policy_cells,
        ),
        shadow_projection_cells_by_group=_shadow_projection_cells_by_group(
            html_shadow_projection_cells,
        ),
        shadow_projection_cells_by_family=_shadow_projection_cells_by_family(
            html_shadow_projection_cells,
        ),
        target_benchmark_contexts_by_family=(
            _target_benchmark_contexts_by_family(target_contexts)
        ),
    )


def _html_render_groups(
    groups: Sequence[ReconciliationGroup],
    *,
    projection_accept_keys: set[tuple[str, str]] | None = None,
) -> tuple[ReconciliationGroup, ...]:
    projection_accept_keys = projection_accept_keys or set()
    sorted_groups = tuple(sorted(groups, key=_group_sort_key))
    if len(sorted_groups) <= _HTML_FULL_RENDER_GROUP_LIMIT:
        return sorted_groups
    priority_groups = [
        group
        for group in sorted_groups
        if _html_priority_group(group, projection_accept_keys=projection_accept_keys)
    ]
    priority_keys = {
        (group.feature_family_id, group.seed_group_id) for group in priority_groups
    }
    low_information_sample = [
        group
        for group in sorted_groups
        if (group.feature_family_id, group.seed_group_id) not in priority_keys
    ][: _HTML_INCONCLUSIVE_SAMPLE_LIMIT]
    return tuple(
        sorted((*priority_groups, *low_information_sample), key=_group_sort_key),
    )


def _html_priority_group(
    group: ReconciliationGroup,
    *,
    projection_accept_keys: set[tuple[str, str]],
) -> bool:
    return (
        (group.feature_family_id, group.seed_group_id) in projection_accept_keys
        or group.reconciliation_class not in _HTML_LOW_INFORMATION_CLASSES
        or bool(group.overlay_png_path)
        or bool(group.overlay_trace_json_path)
        or bool(group.family_pattern_png_path)
        or bool(group.family_pattern_trace_json_path)
        or group.evidence_authority_state == "human_visual_judgment_only"
    )


def _html_scope_notice(
    all_groups: Sequence[ReconciliationGroup],
    html_groups: Sequence[ReconciliationGroup],
) -> list[str]:
    if len(all_groups) == len(html_groups):
        return []
    hidden = len(all_groups) - len(html_groups)
    return [
        '<div class="html-scope-note" role="note">',
        (
            f"HTML 顯示 {_escape(str(len(html_groups)))} / "
            f"{_escape(str(len(all_groups)))} groups；"
            f"{_escape(str(hidden))} 個低資訊量 rows 只保留在 TSV/JSON。"
        ),
        "完整機器索引仍在 groups TSV 與 representatives TSV，產品決策未改變。",
        "</div>",
    ]
