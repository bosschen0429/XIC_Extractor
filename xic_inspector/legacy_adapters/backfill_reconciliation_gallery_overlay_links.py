"""Overlay link and missing-status rendering for the reconciliation gallery."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.diagnostics.diagnostic_io import text_value
from xic_inspector.legacy_adapters import (
    backfill_reconciliation_gallery_review_modes as _gallery_review_modes,
)

from .backfill_reconciliation_gallery_html import (
    detected_url_scheme as _detected_url_scheme,
)
from .backfill_reconciliation_gallery_html import (
    escape_attr as _escape_attr,
)
from .backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from .backfill_reconciliation_gallery_html import (
    href_for_path as _href_for_path,
)
from .backfill_reconciliation_gallery_html import (
    safe_href as _safe_href,
)
from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    ShadowPolicyCell,
    ShadowProjectionCell,
)

_is_cid_nl_successor_review_group = (
    _gallery_review_modes._is_cid_nl_successor_review_group
)
_is_cid_nl_differential_review_group = (
    _gallery_review_modes._is_cid_nl_differential_review_group
)
_cid_nl_transition_label = _gallery_review_modes._cid_nl_transition_label


def _missing_overlay_status_html(input_artifacts: object, label: str) -> str:
    reason = _missing_overlay_reason_text(input_artifacts)
    return (
        '<span class="overlay-scope muted">'
        f"{_escape(label)}<br>"
        f'<span class="overlay-missing-reason">{_escape(reason)}</span>'
        "</span>"
    )


def _overlay_not_required_status_html(label: str) -> str:
    return (
        '<span class="overlay-scope muted">'
        f"{_escape(label)}<br>"
        '<span class="overlay-missing-reason">'
        "not required: high detected anchors, low candidate load"
        "</span>"
        "</span>"
    )


def _overlay_not_required_by_gate(group: ReconciliationGroup) -> bool:
    return (
        group.evidence_authority_state == "machine_support_no_overlay"
        or group.reconciliation_class == "machine_support_no_overlay"
        or "high_detected_anchor_low_rescue_machine_support"
        in group.dependent_context_components
    )


def _missing_overlay_reason_text(input_artifacts: object) -> str:
    artifacts = _string_object_mapping(input_artifacts)
    if _overlay_batch_supplied(artifacts):
        return "not in supplied overlay batch"
    return "overlay artifact not supplied"


def _overlay_batch_supplied(input_artifacts: Mapping[str, object]) -> bool:
    for key in ("overlay_batch_summary_tsvs", "overlay_batch_summary_hashes"):
        value = input_artifacts.get(key)
        if isinstance(value, Sequence) and not isinstance(value, str):
            if len(value) > 0:
                return True
        elif text_value(value):
            return True
    return False


def _family_pattern_link_html(
    groups: Sequence[ReconciliationGroup],
    html_path: Path,
    input_artifacts: object,
) -> str:
    for group in groups:
        href = _href_for_path(group.family_pattern_png_path, html_path)
        if not href:
            continue
        caption = f"{group.feature_family_id} | family MS1 pattern context only"
        return (
            f'<a class="png-link pattern-link" href="{_escape_attr(href)}" '
            f'data-lightbox-src="{_escape_attr(href)}" '
            f'data-lightbox-caption="{_escape_attr(caption)}" '
            'data-lightbox-title="FAMILY CONTEXT" '
            'data-lightbox-interpretation="Absolute RT own-max and raw '
            'intensity context; not a seed-specific decision." '
            'title="family MS1 pattern context only">pattern PNG</a>'
        )
    for group in groups:
        href = _href_for_path(group.overlay_png_path, html_path)
        if not href:
            continue
        caption = f"{group.feature_family_id} | family MS1 context fallback"
        return (
            f'<a class="png-link pattern-link" href="{_escape_attr(href)}" '
            f'data-lightbox-src="{_escape_attr(href)}" '
            f'data-lightbox-caption="{_escape_attr(caption)}" '
            'data-lightbox-title="FAMILY CONTEXT" '
            'data-lightbox-interpretation="Family/header context only; '
            'child-row hypothesis PNG is shown only when generated." '
            'title="family MS1 context fallback">family context</a>'
        )
    if any(_overlay_not_required_by_gate(group) for group in groups):
        return _overlay_not_required_status_html("no family context")
    return _missing_overlay_status_html(input_artifacts, "no family context")


def _family_overlay_links(
    groups: Sequence[ReconciliationGroup],
    html_path: Path,
    input_artifacts: object,
) -> str:
    links: list[str] = []
    seen_hrefs: set[str] = set()
    unique_items: list[tuple[int, ReconciliationGroup]] = []
    for index, group in enumerate(groups, start=1):
        href = _href_for_path(group.overlay_png_path, html_path)
        if not href or href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        unique_items.append((index, group))
    if not unique_items:
        if any(_overlay_not_required_by_gate(group) for group in groups):
            return _overlay_not_required_status_html("no family overlay")
        return _missing_overlay_status_html(input_artifacts, "no family overlay")
    if len(groups) > 1 and len(unique_items) == 1:
        _, group = unique_items[0]
        link = _overlay_link_html(
            group,
            html_path,
            label="shared family context",
            caption=f"{group.feature_family_id} | shared family MS1 context",
        )
        if link:
            links.append(link)
        links.append(
            '<span class="overlay-scope" '
            'title="This PNG is family-level, not seed-specific.">'
            "shared family context · not seed-specific</span>",
        )
        return "<br>".join(links)
    for index, group in unique_items:
        label = "family context PNG" if len(groups) == 1 else f"H{index} family context"
        link = _overlay_link_html(group, html_path, label=label)
        if link:
            links.append(link)
    if links:
        return "<br>".join(links)
    return _missing_overlay_status_html(input_artifacts, "no family overlay")


def _overlay_link_html(
    group: ReconciliationGroup,
    html_path: Path,
    *,
    label: str = "PNG",
    caption: str | None = None,
    scope: str = "FAMILY CONTEXT",
    interpretation: str = (
        "Family-level MS1 context; use hypothesis evidence when available."
    ),
) -> str:
    png_href = _href_for_path(group.overlay_png_path, html_path)
    if not png_href:
        return ""
    if _is_cid_nl_differential_review_group(group):
        transition = _cid_nl_transition_label(group)
        if "family context" in label:
            label = label.replace("family context", "hypothesis differential")
        if label == "PNG":
            label = "hypothesis differential PNG"
        if scope == "FAMILY CONTEXT":
            scope = "HYPOTHESIS DIFFERENTIAL OVERLAY"
        if caption is None or "family context" in caption:
            caption = (
                f"{transition} | paired source/successor PeakHypothesis MS1 "
                "overlay"
            )
        if interpretation == (
            "Family-level MS1 context; use hypothesis evidence when available."
        ):
            interpretation = (
                "Source and successor PeakHypothesis MS1 trace comparison; "
                "Gaussian15-smoothed visual evidence only, not NL-tag coverage "
                "and not ProductWriter authority."
            )
    elif _is_cid_nl_successor_review_group(group) and interpretation == (
        "Family-level MS1 context; use hypothesis evidence when available."
    ):
        interpretation = (
            "MS1 detected/rescued trace context only; not NL-tag coverage "
            "and not ProductWriter authority."
        )
    lightbox_caption = (
        caption
        if caption is not None
        else f"{group.feature_family_id} | {group.seed_group_id}"
    )
    return (
        f'<a class="png-link" href="{_escape_attr(png_href)}" '
        f'data-lightbox-src="{_escape_attr(png_href)}" '
        f'data-lightbox-caption="{_escape_attr(lightbox_caption)}" '
        f'data-lightbox-title="{_escape_attr(scope)}" '
        f'data-lightbox-interpretation="{_escape_attr(interpretation)}">'
        f"{_escape(label)}</a>"
    )


def _path_overlay_link_html(
    path_text: str,
    html_path: Path,
    *,
    label: str,
    caption: str,
    scope: str,
    interpretation: str,
) -> str:
    png_href = _href_for_path(path_text, html_path)
    if not png_href:
        return ""
    return (
        f'<a class="png-link" href="{_escape_attr(png_href)}" '
        f'data-lightbox-src="{_escape_attr(png_href)}" '
        f'data-lightbox-caption="{_escape_attr(caption)}" '
        f'data-lightbox-title="{_escape_attr(scope)}" '
        f'data-lightbox-interpretation="{_escape_attr(interpretation)}">'
        f"{_escape(label)}</a>"
    )


def _hypothesis_overlay_path(
    group: ReconciliationGroup,
    html_path: Path,
) -> str:
    value = _safe_href(text_value(group.overlay_png_path))
    if not value or _detected_url_scheme(value):
        return ""
    raw_path = Path(value)
    candidates = (
        (raw_path,)
        if raw_path.is_absolute()
        else (html_path.parent / raw_path, Path.cwd() / raw_path)
    )
    for candidate in candidates:
        suffix = candidate.suffix or ".png"
        hypothesis_path = candidate.with_name(f"{candidate.stem}_hypothesis{suffix}")
        if hypothesis_path.exists():
            return str(hypothesis_path)
    return ""


def _hypothesis_overlay_link_html(
    group: ReconciliationGroup,
    html_path: Path,
    *,
    label: str = "hypothesis PNG",
) -> str:
    path = _hypothesis_overlay_path(group, html_path)
    if not path:
        return ""
    if _is_cid_nl_successor_review_group(group):
        caption = (
            f"{group.feature_family_id} | m/z {group.seed_mz or 'unknown'} | "
            f"RT {group.seed_rt or 'unknown'} | CID-NL successor MS1 trace context"
        )
        return _path_overlay_link_html(
            path,
            html_path,
            label="MS1 context PNG" if label == "hypothesis PNG" else label,
            caption=caption,
            scope="MS1 TRACE CONTEXT",
            interpretation=(
                "MS1 detected/rescued trace context only; not NL-tag coverage "
                "and not ProductWriter authority."
            ),
        )
    caption = (
        f"{group.feature_family_id} | m/z {group.seed_mz or 'unknown'} | "
        f"RT {group.seed_rt or 'unknown'} | detected-anchor hypothesis evidence"
    )
    return _path_overlay_link_html(
        path,
        html_path,
        label=label,
        caption=caption,
        scope="HYPOTHESIS EVIDENCE",
        interpretation=(
            "Detected-anchor apex-aligned MS1 shape plus selected-peak raw intensity."
        ),
    )


def _overlay_href_context(
    groups: Sequence[ReconciliationGroup],
    html_path: Path,
) -> tuple[Counter[str], dict[str, int]]:
    href_counts: Counter[str] = Counter()
    first_index_by_href: dict[str, int] = {}
    for index, group in enumerate(groups, start=1):
        href = _href_for_path(group.overlay_png_path, html_path)
        if not href:
            continue
        href_counts[href] += 1
        first_index_by_href.setdefault(href, index)
    return href_counts, first_index_by_href


def _seed_overlay_cell_html(
    group: ReconciliationGroup,
    *,
    seed_index: int,
    html_path: Path,
    href_counts: Mapping[str, int],
    first_index_by_href: Mapping[str, int],
    total_seed_groups: int,
    input_artifacts: object,
) -> str:
    href = _href_for_path(group.overlay_png_path, html_path)
    if not href:
        if _overlay_not_required_by_gate(group):
            return _overlay_not_required_status_html("no seed overlay")
        return _missing_overlay_status_html(input_artifacts, "no seed overlay")
    hypothesis_link = _hypothesis_overlay_link_html(group, html_path)
    if hypothesis_link:
        return hypothesis_link
    if href_counts.get(href, 0) <= 1:
        label = (
            "family context PNG"
            if total_seed_groups == 1
            else f"H{seed_index} family context"
        )
        return (
            _overlay_link_html(group, html_path, label=label)
            or _missing_overlay_status_html(input_artifacts, "no seed overlay")
        )
    first_index = first_index_by_href.get(href, seed_index)
    if first_index != seed_index:
        return (
            '<span class="overlay-scope muted" '
            f'title="Same shared family context as H{first_index}; '
            'not seed-specific.">'
            f"same family context as H{first_index}</span>"
        )
    link = _overlay_link_html(
        group,
        html_path,
        label="shared family context",
        caption=f"{group.feature_family_id} | shared family MS1 context",
    )
    if not link:
        return _missing_overlay_status_html(input_artifacts, "no seed overlay")
    return (
        f"{link}<br>"
        '<span class="overlay-scope" '
        'title="This PNG is family-level, not seed-specific.">'
        "shared family context · not hypothesis evidence</span>"
    )


def _shadow_projection_overlay_link_html(
    cell: ShadowProjectionCell,
    html_path: Path,
) -> str:
    png_href = _href_for_path(cell.overlay_png_path, html_path)
    if not png_href:
        return "no overlay"
    caption = f"{cell.feature_family_id} | {cell.seed_group_id} | {cell.sample_stem}"
    return (
        f'<a class="png-link" href="{_escape_attr(png_href)}" '
        f'data-lightbox-src="{_escape_attr(png_href)}" '
        f'data-lightbox-caption="{_escape_attr(caption)}">PNG</a>'
    )


def _shadow_policy_overlay_link_html(
    cell: ShadowPolicyCell,
    html_path: Path,
) -> str:
    png_href = _href_for_path(cell.overlay_png_path, html_path)
    if not png_href:
        return "no overlay"
    caption = f"{cell.feature_family_id} | {cell.seed_group_id} | {cell.sample_stem}"
    return (
        f'<a class="png-link" href="{_escape_attr(png_href)}" '
        f'data-lightbox-src="{_escape_attr(png_href)}" '
        f'data-lightbox-caption="{_escape_attr(caption)}">PNG</a>'
    )


def _string_object_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {text_value(key): item for key, item in value.items() if text_value(key)}
