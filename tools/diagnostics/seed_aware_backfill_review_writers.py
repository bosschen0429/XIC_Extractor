"""Output writers for seed-aware backfill review diagnostics."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.diagnostics.diagnostic_io import write_tsv
from tools.diagnostics.seed_aware_backfill_review_constants import (
    CLASS_NEIGHBOR,
    CLASS_SEED_SUPPORTED,
    CLASS_SHAPE,
)


def write_outputs(output_dir: Path, result: Mapping[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    families = list(result["families"])
    blast_rows = list(result["blast_radius"])
    summary = list(result["summary"])
    _write_tsv(
        output_dir / "seed_aware_backfill_review_families.tsv",
        families,
        _family_fields(),
    )
    _write_tsv(
        output_dir / "seed_aware_backfill_review_summary.tsv",
        summary,
        ("metric", "value"),
    )
    _write_tsv(
        output_dir / "seed_aware_backfill_blast_radius.tsv",
        blast_rows,
        _blast_fields(),
    )
    (output_dir / "seed_aware_backfill_review.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_markdown(output_dir / "seed_aware_backfill_review.md", result)
    _write_blast_markdown(
        output_dir / "seed_aware_backfill_blast_radius.md",
        result,
    )


def _write_markdown(path: Path, result: Mapping[str, Any]) -> None:
    summary = {row["metric"]: row["value"] for row in result["summary"]}
    families = list(result["families"])
    lines = [
        "# Seed-Aware Backfill Review",
        "",
        "## Verdict",
        "",
        f"- Reviewed families: `{summary.get('family_count', '0')}`",
        (
            "- Shadow-supported candidates: "
            f"`{summary.get('classification:' + CLASS_SEED_SUPPORTED, '0')}`"
        ),
        (
            "- Neighboring interference review: "
            f"`{summary.get('classification:' + CLASS_NEIGHBOR, '0')}`"
        ),
        (
            "- Shape insufficient review: "
            f"`{summary.get('classification:' + CLASS_SHAPE, '0')}`"
        ),
        "- Production matrix changes: `none`",
        "",
        "## Inputs",
        "",
    ]
    for name, value in result["inputs"].items():
        if isinstance(value, list):
            joined = "; ".join(str(item) for item in value)
        else:
            joined = str(value)
        lines.append(f"- {name}: `{joined}`")
    lines.extend(
        [
            "",
            "## Top Families",
            "",
            (
                "| family | m/z | RT | class | rescued | max interference "
                "| overlay | reason |"
            ),
            "|---|---:|---:|---|---:|---:|---|---|",
        ]
    )
    for row in families[:20]:
        lines.append(_markdown_family_row(row))
    lines.extend(
        [
            "",
            "## Output Files",
            "",
            "- `seed_aware_backfill_review_summary.tsv`",
            "- `seed_aware_backfill_review_families.tsv`",
            "- `seed_aware_backfill_review.json`",
            "- `seed_aware_backfill_blast_radius.tsv`",
            "- `seed_aware_backfill_blast_radius.md`",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_blast_markdown(path: Path, result: Mapping[str, Any]) -> None:
    blast_rows = list(result["blast_radius"])
    affected = [
        row for row in blast_rows if row["blast_radius_action"] != "no_shadow_withhold"
    ]
    lines = [
        "# Seed-Aware Backfill Blast Radius",
        "",
        "## Verdict",
        "",
        f"- Families reviewed: `{len(blast_rows)}`",
        f"- Families with shadow withheld rescued cells: `{len(affected)}`",
        (
            "- Shadow-withheld rescued cells: "
            f"`{sum(int(row['would_withhold_rescued_cells']) for row in affected)}`"
        ),
        "- Production matrix changes: `none`",
        "",
        "## Affected Families",
        "",
        "| family | m/z | RT | class | withheld rescued cells | action | reason |",
        "|---|---:|---:|---|---:|---|---|",
    ]
    for row in affected[:30]:
        lines.append(_markdown_blast_row(row))
    if not affected:
        lines.append("| none |  |  |  |  |  |  |")
    path.write_text("\n".join(lines), encoding="utf-8")


def _markdown_family_row(row: Mapping[str, Any]) -> str:
    return (
        f"| `{row['feature_family_id']}` "
        f"| {_format_value(row.get('family_center_mz'))} "
        f"| {_format_value(row.get('family_center_rt'))} "
        f"| `{row['review_classification']}` "
        f"| {row['accepted_backfill_count']} "
        f"| {_format_value(row.get('max_global_apex_interference_fraction'))} "
        f"| {_first_path(row.get('png_paths'))} "
        f"| {str(row['review_reason']).replace('|', '/')} |"
    )


def _markdown_blast_row(row: Mapping[str, Any]) -> str:
    return (
        f"| `{row['feature_family_id']}` "
        f"| {_format_value(row.get('family_center_mz'))} "
        f"| {_format_value(row.get('family_center_rt'))} "
        f"| `{row['review_classification']}` "
        f"| {row['would_withhold_rescued_cells']} "
        f"| `{row['blast_radius_action']}` "
        f"| {str(row['review_reason']).replace('|', '/')} |"
    )


def _family_fields() -> tuple[str, ...]:
    return (
        "feature_family_id",
        "neutral_loss_tag",
        "family_center_mz",
        "family_center_rt",
        "detected_count",
        "accepted_backfill_count",
        "accepted_cell_count",
        "input_review_classification",
        "all_overlay_row_count",
        "seed_overlay_row_count",
        "overlay_row_count",
        "overlay_success_count",
        "overlay_support_count",
        "overlay_neighbor_count",
        "overlay_failed_count",
        "max_global_apex_interference_fraction",
        "min_selected_apex_in_trace_window_fraction",
        "min_global_apex_assessable_fraction",
        "min_shape_supported_fraction",
        "seed_audit_row_count",
        "seed_group_count",
        "seed_rt_span",
        "low_ms1_detail_row_count",
        "protected_family",
        "review_classification",
        "recommended_next_action",
        "review_reason",
        "would_withhold_rescued_cells",
        "png_paths",
        "pdf_paths",
        "row_flags",
        "primary_evidence",
        "reason",
    )


def _blast_fields() -> tuple[str, ...]:
    return (
        "feature_family_id",
        "family_center_mz",
        "family_center_rt",
        "review_classification",
        "detected_count",
        "accepted_backfill_count",
        "accepted_cell_count",
        "would_withhold_family",
        "would_withhold_rescued_cells",
        "protected_family",
        "blast_radius_action",
        "review_reason",
    )


def _write_tsv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
) -> None:
    write_tsv(path, rows, fields, formatter=_format_value, lineterminator="\n")


def _first_path(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    return text.split(";", 1)[0]


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)
