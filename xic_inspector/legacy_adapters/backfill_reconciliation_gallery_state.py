"""State badge HTML helpers for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Sequence

from .backfill_reconciliation_gallery_html import (
    badge as _badge,
)
from .backfill_reconciliation_gallery_html import (
    badge_label as _badge_label,
)
from .backfill_reconciliation_gallery_html import (
    escape_attr as _escape_attr,
)
from .backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from .backfill_reconciliation_gallery_indices import (
    _shadow_policy_compact_summary,
    _shadow_projection_compact_summary,
)
from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    ShadowPolicyCell,
    ShadowProjectionCell,
)


def _state_html(group: ReconciliationGroup) -> str:
    return _state_html_for_shadow(group, ())


def _state_aria_label(
    group: ReconciliationGroup,
    shadow_summary: str,
    projection_summary: str,
) -> str:
    product = _badge_label(group.product_behavior_state)
    if group.product_behavior_state == "product_rescued_context_only":
        product = "candidate only, not matrix written"
    elif group.product_behavior_state == "product_not_backfilled":
        product = "not matrix written"
    evidence = _badge_label(group.evidence_authority_state)
    parts = [f"Product: {product}", f"Evidence: {evidence}"]
    if shadow_summary:
        parts.append(f"Shadow: {shadow_summary}")
    if projection_summary:
        parts.append(f"Projection: {projection_summary}")
    return "; ".join(parts)


def _state_html_for_shadow(
    group: ReconciliationGroup,
    shadow_policy_cells: Sequence[ShadowPolicyCell],
    shadow_projection_cells: Sequence[ShadowProjectionCell] = (),
) -> str:
    shadow_summary = _shadow_policy_compact_summary(shadow_policy_cells)
    projection_summary = _shadow_projection_compact_summary(shadow_projection_cells)
    shadow_state_key = "Legacy" if projection_summary else "Shadow"
    shadow_state_label = _shadow_policy_state_label(
        shadow_summary,
        projection_summary,
    )
    shadow_html = (
        '<div class="state-line shadow-line">'
        f'<span class="state-key">{shadow_state_key}</span>'
        f'<span class="shadow-pill">{_escape(shadow_state_label)}</span>'
        "</div>"
        if shadow_summary
        else ""
    )
    projection_html = (
        '<div class="state-line projection-line">'
        '<span class="state-key">Projection</span>'
        f'<span class="shadow-pill">{_escape(projection_summary)}</span>'
        "</div>"
        if projection_summary
        else ""
    )
    aria_label = _state_aria_label(group, shadow_summary, projection_summary)
    return (
        f'<div class="state-stack" aria-label="{_escape_attr(aria_label)}">'
        '<div class="state-line">'
        '<span class="state-key">Product</span>'
        f"{_badge(group.product_behavior_state)}"
        "</div>"
        '<div class="state-line">'
        '<span class="state-key">Evidence</span>'
        f"{_badge(group.evidence_authority_state)}"
        "</div>"
        f"{shadow_html}"
        f"{projection_html}"
        "</div>"
    )


def _shadow_policy_state_label(shadow_summary: str, projection_summary: str) -> str:
    if projection_summary:
        return f"legacy reference: {shadow_summary}"
    return shadow_summary


def _shadow_policy_chain_title(
    shadow_projection_cells: Sequence[ShadowProjectionCell],
) -> str:
    if shadow_projection_cells:
        return "Legacy MS1+RT shadow policy"
    return "MS1+RT shadow policy"


def _shadow_policy_chain_subtitle(
    shadow_policy_cells: Sequence[ShadowPolicyCell],
    shadow_projection_cells: Sequence[ShadowProjectionCell],
) -> str:
    summary = _shadow_policy_compact_summary(shadow_policy_cells) or "not supplied"
    if shadow_projection_cells and summary != "not supplied":
        return f"reference only · {summary}"
    return summary


def _projection_matrix_state_html(written: bool) -> str:
    return _badge("write" if written else "blank")
