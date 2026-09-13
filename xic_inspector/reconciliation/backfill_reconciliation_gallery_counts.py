"""Count-stack HTML helpers for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Sequence

from .backfill_reconciliation_gallery_html import (
    escape_attr as _escape_attr,
)
from .backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    ShadowProjectionCell,
)
from .backfill_reconciliation_gallery_review_modes import (
    _is_cid_nl_successor_review_group,
)


def _counts_html(
    group: ReconciliationGroup,
    shadow_projection_cells: Sequence[ShadowProjectionCell] = (),
) -> str:
    if shadow_projection_cells:
        return _projection_counts_html(group, shadow_projection_cells)
    if _is_cid_nl_successor_review_group(group):
        return _cid_nl_successor_counts_html(group)
    return _impact_counts_html(
        detected=group.detected_cell_count,
        rescued=group.rescued_cell_count,
        duplicate=group.duplicate_assigned_cell_count,
        provisional=group.provisional_cell_count,
        aria_label=(
            "NL anchors are family detected required-tag anchors; "
            "Candidate-only is hypothesis candidate cells, not matrix-written; "
            "Dup is family duplicate-assigned cell context; "
            "Review is hypothesis provisional cell context. "
            "These are alignment cell provenance counts, not target benchmark coverage."
        ),
    )


def _cid_nl_successor_counts_html(group: ReconciliationGroup) -> str:
    items = [
        _count_pill(
            "Candidate",
            "successor MS1-backed feature-inclusion candidate cells; "
            "not active matrix writes",
            group.rescued_cell_count,
        ),
        _count_pill(
            "Existing",
            "cells where the successor feature already has a detected baseline value",
            group.detected_cell_count,
        ),
    ]
    if group.provisional_cell_count:
        items.append(
            _count_pill(
                "Omit",
                "legacy cells omitted because no safe successor write target exists",
                group.provisional_cell_count,
            ),
        )
    return (
        '<dl class="count-stack cid-nl-successor-counts" '
        'aria-label="CID-NL feature-inclusion review counts. '
        'Candidate is successor MS1 feature-inclusion candidate cells; Existing '
        'is detected-baseline successor feature context; Omit is no safe '
        'successor target. These are not NL-tag coverage counts and are not '
        'active matrix writes.">'
        f"{''.join(items)}"
        "</dl>"
    )


def _projection_counts_html(
    group: ReconciliationGroup,
    cells: Sequence[ShadowProjectionCell],
) -> str:
    del group
    return _projection_impact_counts_html(
        current=sum(cell.current_matrix_written for cell in cells),
        review=sum(cell.review_rescued_cell for cell in cells),
        accept=sum(
            cell.shadow_decision == "accept" and cell.projected_matrix_written
            for cell in cells
        ),
        block=sum(cell.shadow_decision == "block" for cell in cells),
        aria_label=(
            "Shadow production projection impact. Current is cells already "
            "written by the production-decision snapshot; Review is the "
            "projection candidate pool; Write is new projected matrix writes; "
            "Block is hard projection blockers."
        ),
    )


def _projection_impact_counts_html(
    *,
    current: int,
    review: int,
    accept: int,
    block: int,
    aria_label: str,
) -> str:
    items = [
        _count_pill("Current", "already written in the production snapshot", current),
        _count_pill(
            "Review",
            "projection candidate pool; final status is Current, Write, or Block",
            review,
        ),
        _count_pill("Write", "new matrix cells authorized by this projection", accept),
        _count_pill("Block", "shadow hard-blocked cells", block),
    ]
    return (
        '<dl class="count-stack projection-counts" '
        f'aria-label="{_escape_attr(aria_label)}">'
        f"{''.join(items)}"
        "</dl>"
    )


def _impact_counts_html(
    *,
    detected: int,
    rescued: int,
    duplicate: int,
    provisional: int,
    aria_label: str,
) -> str:
    items = [
        _count_pill("NL", "family detected required-tag anchors", detected),
        _count_pill(
            "Candidate-only",
            "hypothesis candidate-only cells; not matrix-written",
            rescued,
        ),
    ]
    if duplicate:
        items.append(_count_pill("Dup", "family duplicate-assigned cells", duplicate))
    if provisional:
        items.append(_count_pill("Review", "hypothesis provisional cells", provisional))
    return (
        '<dl class="count-stack" '
        f'aria-label="{_escape_attr(aria_label)}">'
        f"{''.join(items)}"
        "</dl>"
    )


def _count_pill(label: str, title: str, value: int) -> str:
    return (
        f'<div title="{_escape_attr(title)}">'
        f"<dt>{_escape(label)}</dt><dd>{value}</dd></div>"
    )


def _consolidated_counts_html(
    groups: Sequence[ReconciliationGroup],
    shadow_projection_cells: Sequence[ShadowProjectionCell] = (),
) -> str:
    if shadow_projection_cells:
        return _projection_counts_html(groups[0], shadow_projection_cells)
    detected = max((group.detected_cell_count for group in groups), default=0)
    rescued = sum(group.rescued_cell_count for group in groups)
    duplicate = max(
        (group.duplicate_assigned_cell_count for group in groups),
        default=0,
    )
    provisional = sum(group.provisional_cell_count for group in groups)
    return _impact_counts_html(
        detected=detected,
        rescued=rescued,
        duplicate=duplicate,
        provisional=provisional,
        aria_label=(
            "Consolidated product hypothesis impact. "
            "NL anchors are family detected required-tag anchors; "
            "Candidate-only and Review are summed seed-alias "
            "candidate/provisional cells, not matrix-written counts; "
            "Dup is family duplicate-assigned "
            "cell context."
        ),
    )
