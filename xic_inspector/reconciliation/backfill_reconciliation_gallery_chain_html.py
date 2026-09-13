"""Shared detail-chain HTML helpers for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Sequence

from xic_extractor.diagnostics.diagnostic_io import text_value

from .backfill_reconciliation_gallery_html import (
    escape_attr as _escape_attr,
)
from .backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from .backfill_reconciliation_gallery_models import (
    _ordered_unique,
)


def _compact_issue_label(value: str) -> str:
    text = text_value(value)
    replacements = {
        "seed_request_provenance": "seed provenance",
        "review_required_neighboring_ms1_interference": "neighboring MS1 review",
        "review_required_interference": "interference review",
        "evidence_inconclusive": "inconclusive",
        "machine_support_no_overlay": "machine support, no overlay",
        "track_machine_supported_backfill": "track machine-supported candidate",
        "ms1_shape_supports_family_backfill": (
            "MS1 shape supports same-peak candidate"
        ),
        "primary_identity_retained_backfill_review_only": (
            "primary identity retained; candidate only"
        ),
        "product_authorized_same_peak_backfill": "same-peak policy would write",
        "backfill_ms1_pattern_blocked": "MS1 pattern blocks matrix write",
        "review_only": "review only",
        "high_detected_anchor_low_rescue_machine_support": (
            "high detected anchors, low candidate load"
        ),
        "overlay_not_required_machine_supported": "overlay not required",
        "seed_specific_overlay_not_required_machine_supported": (
            "seed overlay not required"
        ),
        "product_accepts_and_visual_supports": "matrix + visual support",
        "product_rejects_but_visual_supports": "not written + visual support",
        "product_accepts_but_evidence_conflicts": "matrix + evidence conflict",
        "product_rejects_and_evidence_blocks": "not written + blocks",
        "product_primary_backfilled": "matrix written",
        "product_rescued_context_only": "candidate only",
        "product_not_backfilled": "not written",
        "candidate_context": "candidate only",
        "rescued": "candidate",
        "review_rescue": "review candidate",
        "gap_fill_rescued": "candidate",
        "not_assessable_missing_overlay": "missing overlay",
        "not_assessable_missing_seed_provenance": "missing seed provenance",
        "not_assessable_join_gap": "join gap",
    }
    if text in replacements:
        return replacements[text]
    return text.replace("_", " ")


def _compact_product_reason(value: str) -> str:
    return _compact_issue_label(value) or "no product reason supplied"


def _component_summary_text(items: Sequence[str], fallback: str) -> str:
    cleaned = _ordered_unique(text_value(item) for item in items if text_value(item))
    if not cleaned:
        return fallback
    summary = " / ".join(_compact_issue_label(item) for item in cleaned[:3])
    if len(cleaned) > 3:
        summary += f" / +{len(cleaned) - 3}"
    return summary


def _chain_item_html(
    title: str,
    state: str,
    body_html: str,
    *,
    css_class: str = "",
) -> str:
    class_attr = "chain-item" + (f" {css_class}" if css_class else "")
    return (
        f'<section class="{_escape_attr(class_attr)}">'
        '<div class="chain-head">'
        f"<h3>{_escape(title)}</h3>"
        f'<span class="chain-state">{_escape(_compact_issue_label(state))}</span>'
        "</div>"
        f'<div class="chain-body">{body_html}</div>'
        "</section>"
    )


def _component_list_html(items: Sequence[str]) -> str:
    cleaned = _ordered_unique(text_value(item) for item in items if text_value(item))
    if not cleaned:
        return ""
    return (
        '<ul class="component-list">'
        + "".join(f"<li>{_escape(_compact_issue_label(item))}</li>" for item in cleaned)
        + "</ul>"
    )


def _secondary_chain_details_html(title: str, subtitle: str, body_html: str) -> str:
    return (
        '<details class="chain-item secondary-chain">'
        "<summary>"
        f"<span>{_escape(title)}</span>"
        f'<small>{_escape(subtitle)}</small>'
        "</summary>"
        f'<div class="secondary-chain-body">{body_html}</div>'
        "</details>"
    )
