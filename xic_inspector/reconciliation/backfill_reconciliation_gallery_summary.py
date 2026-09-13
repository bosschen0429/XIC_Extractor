"""Overview summary rendering for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from xic_extractor.diagnostics.diagnostic_io import text_value

from .backfill_reconciliation_gallery_filters import (
    _REVIEW_CATEGORY_SUMMARY_LABELS,
    _review_category_counts,
)
from .backfill_reconciliation_gallery_html import (
    escape_html as _escape,
)
from .backfill_reconciliation_gallery_models import (
    ReconciliationIndex,
)
from .backfill_reconciliation_gallery_output_rows import (
    _string_object_mapping,
    _summary,
)
from .backfill_reconciliation_gallery_provenance import (
    _artifact_links,
    _input_artifact_links,
    _interpretation_guide_callout_html,
)
from .backfill_reconciliation_gallery_source_context import (
    _int_text,
)
from .backfill_reconciliation_gallery_target_benchmark import (
    _target_benchmark_panel_html,
    _target_benchmark_summary_text,
)


def _string_int_mapping(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key, item in value.items():
        text_key = text_value(key)
        if not text_key:
            continue
        result[text_key] = _int_text(item)
    return result


def _activation_summary_text(summary: Mapping[str, object]) -> str:
    if text_value(summary.get("activation_delta_view")) == "not_supplied":
        return "not supplied"
    effect_counts = _string_int_mapping(
        summary.get("activation_value_delta_matrix_effect_counts"),
    )
    written = effect_counts.get("written", 0)
    groups = _int_text(summary.get("activation_written_projection_group_count"))
    cells = _int_text(summary.get("activation_written_projection_cell_count"))
    return f"written {written} · groups {groups} · cells {cells}"


def _current_rescue_summary_text(summary: Mapping[str, object]) -> str:
    if not summary.get("shadow_projection_matrix_counts"):
        return "not supplied"
    groups = _int_text(summary.get("current_written_projection_group_count"))
    cells = _int_text(summary.get("current_written_projection_cell_count"))
    return f"written {cells} · groups {groups}"


def _summary_html(
    index: ReconciliationIndex,
    output_paths: Mapping[str, Path],
    *,
    html_path: Path,
    local_interpretation_guide: Path | None,
) -> list[str]:
    summary = _summary(
        index.groups,
        index.representative_cells,
        _string_object_mapping(index.summary.get("input_artifacts")),
    )
    summary.update(index.summary)
    missing_counts = summary["missing_evidence_counts"]
    category_counts = _review_category_counts(index.groups)
    excluded_counts = summary.get("excluded_family_counts", {})
    target_context_counts = _string_int_mapping(
        summary.get("target_benchmark_context_counts"),
    )
    validation_label = text_value(summary.get("validation_label")) or "diagnostic_only"
    return [
        '<section class="summary" aria-label="reconciliation summary">',
        _decision_legend_html(),
        _interpretation_guide_callout_html(
            summary,
            html_path=html_path,
            local_interpretation_guide=local_interpretation_guide,
        ),
        _summary_item("validation", "Validation", validation_label),
        _summary_item("groups", "Groups", str(summary["group_count"])),
        _summary_item(
            "families",
            "Families",
            str(len({group.feature_family_id for group in index.groups})),
        ),
        _summary_item(
            "representatives",
            "Representative cells",
            str(summary["representative_cell_count"]),
        ),
        _summary_item(
            "missing-overlay",
            "Missing overlay",
            str(_count_token_prefix(missing_counts, "missing_overlay")),
        ),
        _summary_item(
            "missing-seed",
            "Missing seed provenance",
            str(_count_token_prefix(missing_counts, "missing_seed_provenance")),
        ),
        _summary_item(
            "excluded-detected-zero",
            "Excluded detected=0",
            str(_count_token_prefix(excluded_counts, "detected_zero_family")),
        ),
        _summary_item(
            "classes",
            "Review focus",
            (
                " · ".join(
                    f"{_REVIEW_CATEGORY_SUMMARY_LABELS.get(key, key)} {value}"
                    for key, value in category_counts.items()
                )
                or "none"
            ),
        ),
        _summary_item(
            "target-benchmark",
            "Target benchmark",
            _target_benchmark_summary_text(
                index.target_benchmark_contexts,
                target_context_counts,
                summary.get("input_artifacts"),
            ),
        ),
        _summary_item(
            "current-rescue",
            "Current rescue writes",
            _current_rescue_summary_text(summary),
        ),
        _summary_item(
            "activation-delta",
            "Activated writes",
            _activation_summary_text(summary),
        ),
        *_artifact_links(output_paths, html_path=html_path),
        *_target_benchmark_panel_html(
            index.target_benchmark_contexts,
            summary.get("input_artifacts"),
            target_context_counts,
        ),
        (
            '<p class="authority-note">這個 gallery 只消費既有 artifact；'
            "不會修改 alignment matrix、cells、review TSV、workbooks "
            "或 product decisions。"
            "</p>"
        ),
        *_input_artifact_links(
            _string_object_mapping(summary.get("input_artifacts")),
            html_path=html_path,
        ),
        "</section>",
    ]


def _decision_legend_html() -> str:
    items = (
        (
            "matrix written",
            "Final matrix value",
            "Only this wording means the cell is written to the delivered matrix.",
        ),
        (
            "candidate only",
            "Evidence candidate",
            "Candidate counts are review/provenance cells, not matrix writes.",
        ),
        (
            "not written + blocks",
            "Blocked or rejected",
            "The evidence chain says to leave the value out unless policy changes.",
        ),
    )
    rows = "".join(
        '<div class="decision-legend-item">'
        f'<span class="decision-token">{_escape(token)}</span>'
        f"<strong>{_escape(title)}</strong>"
        f"<p>{_escape(copy)}</p>"
        "</div>"
        for token, title, copy in items
    )
    return (
        '<div class="decision-legend" aria-label="decision wording guide">'
        '<div class="decision-legend-heading">'
        "<span>Read first</span>"
        "<strong>Candidate is not a matrix write.</strong>"
        "</div>"
        f"{rows}"
        "</div>"
    )


def _summary_item(css_class: str, label: str, value: str) -> str:
    return (
        f'<div class="summary-item {css_class}">'
        f"<span>{_escape(label)}</span><strong>{_escape(value)}</strong></div>"
    )


def _count_token_prefix(counts: object, prefix: str) -> int:
    if not isinstance(counts, Mapping):
        return 0
    return sum(
        int(value)
        for key, value in counts.items()
        if isinstance(key, str) and key.startswith(prefix)
    )
