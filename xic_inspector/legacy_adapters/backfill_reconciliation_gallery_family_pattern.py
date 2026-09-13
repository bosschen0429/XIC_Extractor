"""Family pattern context HTML helpers for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Sequence

from xic_extractor.diagnostics.diagnostic_io import text_value

from .backfill_reconciliation_gallery_chain_html import (
    _compact_issue_label,
)
from .backfill_reconciliation_gallery_html import (
    badge as _badge,
)
from .backfill_reconciliation_gallery_html import (
    escape_attr as _escape_attr,
)
from .backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from .backfill_reconciliation_gallery_html import (
    safe_href as _safe_href,
)
from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    _ordered_unique,
)


def _family_pattern_state_html(groups: Sequence[ReconciliationGroup]) -> str:
    with_context = _family_context_available(groups)
    return (
        '<div class="state-stack" aria-label="family pattern context">'
        '<div class="state-line">'
        '<span class="state-key">map</span>'
        f"{_badge('pattern_context_only')}"
        "</div>"
        '<div class="state-line">'
        '<span class="state-key">use</span>'
        f"{_badge('pattern_available' if with_context else 'pattern_unavailable')}"
        "</div>"
        "</div>"
    )


def _family_pattern_status_html(groups: Sequence[ReconciliationGroup]) -> str:
    status = (
        "context available"
        if _family_context_available(groups)
        else "context unavailable"
    )
    return f'<span class="pattern-status">{_escape(status)} · context only</span>'


def _family_context_available(groups: Sequence[ReconciliationGroup]) -> bool:
    return any(
        _safe_href(text_value(group.family_pattern_png_path))
        or _safe_href(text_value(group.overlay_png_path))
        for group in groups
    )


def _family_anchor_summary_html(groups: Sequence[ReconciliationGroup]) -> str:
    family_detected = max((group.detected_cell_count for group in groups), default=0)
    seed_parts = []
    for index, group in enumerate(groups, start=1):
        if group.seed_detected_anchor_count:
            seed_parts.append(f"seed {index} D={group.seed_detected_anchor_count}")
    if seed_parts:
        label = f"anchors D={family_detected} · " + " · ".join(seed_parts)
    elif family_detected:
        label = f"anchors D={family_detected} · seed match unknown"
    else:
        label = "anchors D=0 · not eligible"
    if family_detected == 1:
        label += " · single-anchor review"
    return f'<span class="anchor-status">{_escape(label)}</span>'


def _family_pattern_issue_html(groups: Sequence[ReconciliationGroup]) -> str:
    issues = _ordered_unique(
        _compact_issue_label(group.family_pattern_verdict)
        for group in groups
        if group.family_pattern_verdict
    )
    if issues:
        detail = " / ".join(issues)
        detail_class = "context"
    else:
        detail = "seed-specific decisions below"
        detail_class = "support"
    return (
        '<div class="top-issue family-pattern-issue">'
        f"{_badge('pattern_context_only')}"
        f'<span class="issue-text {detail_class}" title="{_escape_attr(detail)}">'
        f"{_escape(detail)}</span>"
        "</div>"
    )
