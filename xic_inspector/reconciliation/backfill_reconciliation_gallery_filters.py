"""Review category and filter rendering for the reconciliation gallery."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from .backfill_reconciliation_gallery_html import (
    escape_attr as _escape_attr,
)
from .backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from .backfill_reconciliation_gallery_indices import (
    _is_projected_new_accept,
)
from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    ShadowProjectionCell,
    _ordered_unique,
)

_REVIEW_CATEGORY_LABELS = {
    "needs_review": "Needs review",
    "accepted_supported": "Evidence-supported rows",
    "conflict_or_blocked": "Conflict / blocked",
    "missing_evidence": "Missing evidence",
}
_DEFAULT_FILTER_CATEGORY = "product_rows"
_REVIEW_FILTER_LABELS = {
    "product_rows": "Review queue",
    "projection_accepts": "Projected matrix writes",
    **_REVIEW_CATEGORY_LABELS,
    "debug_rows": "Duplicate / audit debug",
}
_REVIEW_CATEGORY_SUMMARY_LABELS = {
    "needs_review": "Review",
    "accepted_supported": "Evidence-supported",
    "conflict_or_blocked": "Conflict",
    "missing_evidence": "Missing",
}
_REVIEW_CATEGORY_BY_CLASS = {
    "product_rejects_but_product_grade_supports": "needs_review",
    "product_rejects_but_visual_supports": "needs_review",
    "evidence_inconclusive": "needs_review",
    "machine_support_no_overlay": "accepted_supported",
    "product_accepts_and_product_grade_supports": "accepted_supported",
    "product_accepts_and_visual_supports": "accepted_supported",
    "product_accepts_but_evidence_conflicts": "conflict_or_blocked",
    "product_rejects_and_evidence_blocks": "conflict_or_blocked",
    "not_assessable_missing_overlay": "missing_evidence",
    "not_assessable_missing_seed_provenance": "missing_evidence",
    "not_assessable_join_gap": "missing_evidence",
}


def _filter_html(
    *,
    total_families: int,
    default_visible_families: int,
    has_shadow_projection: bool,
) -> list[str]:
    labels = dict(_REVIEW_FILTER_LABELS)
    if not has_shadow_projection:
        labels.pop("projection_accepts", None)
    return [
        '<section class="filters" aria-label="table filters">',
        '<label for="categoryFilter">Focus</label>',
        '<select id="categoryFilter" data-filter-control>',
        *[
            (
                f'<option value="{_escape_attr(value)}"'
                f'{" selected" if value == _DEFAULT_FILTER_CATEGORY else ""}>'
                f"{_escape(label)}</option>"
            )
            for value, label in labels.items()
        ],
        '<option value="">All rows</option>',
        "</select>",
        '<label for="searchBox">Search</label>',
        '<input id="searchBox" type="search" data-search-control '
        'aria-label="Search family, seed group, support, blocker">',
        (
            '<span class="result-count" data-result-count '
            f'data-total-families="{total_families}">'
            f"顯示 {default_visible_families} / {total_families} families</span>"
        ),
        "</section>",
    ]


def _default_visible_family_count(groups: Sequence[ReconciliationGroup]) -> int:
    return sum(
        1
        for family_groups in _family_groups_for_filter(groups)
        if _DEFAULT_FILTER_CATEGORY in _family_filter_categories(family_groups)
    )


def _review_category(reconciliation_class: str) -> str:
    return _REVIEW_CATEGORY_BY_CLASS.get(reconciliation_class, "needs_review")


def _family_filter_categories(
    groups: Sequence[ReconciliationGroup],
    shadow_projection_cells: Sequence[ShadowProjectionCell] = (),
) -> tuple[str, ...]:
    categories: list[str] = []
    for group in groups:
        categories.extend(_group_filter_categories(group))
    categories.extend(_shadow_projection_filter_categories(shadow_projection_cells))
    return tuple(_ordered_unique(categories))


def _group_filter_categories(group: ReconciliationGroup) -> tuple[str, ...]:
    category = _review_category(group.reconciliation_class)
    if _debug_only_group(group):
        return (category, "debug_rows")
    return (category, "product_rows")


def _debug_only_group(group: ReconciliationGroup) -> bool:
    flags = group.row_flags.lower()
    identity = group.identity_decision.lower()
    if group.include_in_primary_matrix:
        return False
    return (
        "family_consolidation_loser" in flags
        or "duplicate_only" in flags
        or (
            "audit_family" in identity
            and group.duplicate_assigned_cell_count > 0
            and group.accepted_cell_count == 0
        )
    )


def _shadow_projection_filter_categories(
    cells: Sequence[ShadowProjectionCell],
) -> tuple[str, ...]:
    if any(_is_projected_new_accept(cell) for cell in cells):
        return ("projection_accepts",)
    return ()


def _review_category_counts(
    groups: Sequence[ReconciliationGroup],
) -> dict[str, int]:
    counts: Counter[str] = Counter(
        _review_category(group.reconciliation_class) for group in groups
    )
    return {
        key: counts[key]
        for key in _REVIEW_CATEGORY_LABELS
        if counts[key]
    }


def _family_groups_for_filter(
    groups: Sequence[ReconciliationGroup],
) -> tuple[tuple[ReconciliationGroup, ...], ...]:
    grouped: dict[str, list[ReconciliationGroup]] = {}
    for group in groups:
        grouped.setdefault(group.feature_family_id, []).append(group)
    return tuple(tuple(items) for items in grouped.values())
