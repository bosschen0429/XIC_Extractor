"""HTML safety and link helpers for the backfill reconciliation gallery."""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

from xic_extractor.diagnostics.diagnostic_io import text_value

_URL_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
_DANGEROUS_SCHEMES = {"javascript", "data", "vbscript"}


def path_link_html(path_text: str, *, html_path: Path, label: str) -> str:
    href = href_for_path(path_text, html_path)
    if not href:
        return escape_html(label)
    return (
        f'<a href="{escape_attr(href)}" title="{escape_attr(path_text)}">'
        f"{escape_html(label)}</a>"
    )


def compact_path_label(path_text: str) -> str:
    parts = [part for part in slash_path(path_text).split("/") if part]
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return text_value(path_text)


def href_for_path(value: object, html_path: Path) -> str:
    href = safe_href(text_value(value))
    if not href:
        return ""
    if detected_url_scheme(href):
        return slash_path(href)
    raw_path = Path(href)
    target: Path
    if raw_path.is_absolute():
        target = raw_path
    else:
        resolved_target: Path | None = None
        for candidate in (html_path.parent / raw_path, Path.cwd() / raw_path):
            if candidate.exists():
                resolved_target = candidate.resolve()
                break
        if resolved_target is None:
            return slash_path(href)
        target = resolved_target
    try:
        return slash_path(os.path.relpath(target, html_path.parent))
    except ValueError:
        return slash_path(str(target))


def slash_path(value: str) -> str:
    return value.replace("\\", "/")


def safe_href(value: str) -> str:
    sanitized = _remove_control_chars(text_value(value))
    scheme = detected_url_scheme(sanitized)
    if scheme in _DANGEROUS_SCHEMES:
        return ""
    return sanitized


def detected_url_scheme(value: str) -> str:
    compact = "".join(ch for ch in text_value(value) if ord(ch) > 32)
    match = _URL_SCHEME_RE.match(compact)
    if not match:
        return ""
    scheme = match.group(1).lower()
    if len(scheme) == 1 and len(compact) >= 3 and compact[1:3] in {":\\", ":/"}:
        return ""
    return scheme


def escape_html(value: object) -> str:
    return html.escape(text_value(value), quote=True)


def escape_attr(value: object) -> str:
    return escape_html(value)


def badge(value: str) -> str:
    label = badge_label(value)
    return (
        f'<span class="badge {escape_attr(value)}" title="{escape_attr(label)}">'
        f"{escape_html(label)}</span>"
    )


def badge_label(value: str) -> str:
    labels = {
        "product_grade_support": "product-grade",
        "review_only_visual_support": "visual support",
        "dependent_context_only": "context only",
        "human_visual_judgment_only": "human review",
        "evidence_blocks_backfill": "blocks",
        "evidence_inconclusive": "inconclusive",
        "not_assessable": "not assessable",
        "product_accepts_and_product_grade_supports": "matrix + product-grade",
        "product_accepts_and_visual_supports": "matrix + visual",
        "product_rejects_but_product_grade_supports": "not written + product-grade",
        "product_rejects_but_visual_supports": "not written + visual",
        "product_accepts_but_evidence_conflicts": "matrix + conflict",
        "product_rejects_and_evidence_blocks": "not written + blocks",
        "not_assessable_missing_overlay": "missing overlay",
        "not_assessable_missing_seed_provenance": "missing seed",
        "not_assessable_join_gap": "join gap",
        "product_primary_backfilled": "matrix written",
        "product_rescued_context_only": "candidate only",
        "candidate_context": "candidate only",
        "product_provisional": "provisional",
        "product_review_only": "review only",
        "product_not_backfilled": "not written",
        "product_unknown": "unknown",
        "pattern_context_only": "family map",
        "pattern_available": "context available",
        "pattern_unavailable": "context unavailable",
    }
    return labels.get(value, text_value(value).replace("_", " "))


def _remove_control_chars(value: str) -> str:
    return "".join(ch for ch in value if ord(ch) >= 32)
