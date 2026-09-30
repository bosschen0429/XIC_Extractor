from __future__ import annotations

import html
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from tools.diagnostics.targeted_gt_alignment_audit_models import (
    DRIFT_MODE,
    DUPLICATE_MODE,
    MISS_MODE,
    PASS_MODE,
    SPLIT_MODE,
    AuditConfig,
    TargetGroundTruth,
)
from tools.diagnostics.targeted_gt_alignment_audit_utils import (
    _format_float,
    _is_numeric_text,
)
from xic_extractor.tabular_io import write_delimited_rows


def _write_report(
    path: Path,
    comparison: list[dict[str, object]],
    review_rows: list[dict[str, str]],
    config: AuditConfig,
) -> None:
    counts = Counter(str(row["failure_mode"]) for row in comparison)
    total = len(comparison)
    lines = [
        f"# Targeted GT Alignment Audit: {config.target_label}",
        "",
        f"Target m/z: {config.target_mz:.6g}",
        f"Alignment run: `{config.alignment_run}`",
        "",
        "## Summary",
        "",
        "| Mode | Count | Percent |",
        "|---|---:|---:|",
    ]
    for mode in (PASS_MODE, SPLIT_MODE, DRIFT_MODE, DUPLICATE_MODE, MISS_MODE):
        count = counts.get(mode, 0)
        percent = count / total * 100.0 if total else 0.0
        lines.append(f"| {mode} | {count} | {percent:.1f}% |")
    lines.extend(
        [
            "",
            "## Families In Target m/z Range",
            "",
            "| Family | m/z | RT | Anchor | Warning |",
            "|---|---:|---:|---|---|",
        ],
    )
    for review_row in review_rows:
        lines.append(
            "| "
            f"{review_row.get('feature_family_id', '')} | "
            f"{review_row.get('family_center_mz', '')} | "
            f"{review_row.get('family_center_rt', '')} | "
            f"{review_row.get('has_anchor', '')} | "
            f"{review_row.get('warning', '')} |"
        )
    lines.extend(["", "## Per-Sample Detail", ""])
    for comparison_row in comparison:
        lines.extend(
            [
                (
                    f"### {comparison_row['sample_stem']} - "
                    f"`{comparison_row['failure_mode']}`"
                ),
                "",
                f"- GT RT: {comparison_row['gt_target_rt_min']} min",
                (
                    "- Production families: "
                    f"{comparison_row['production_family_ids'] or '(none)'}"
                ),
                (
                    "- Duplicate families: "
                    f"{comparison_row['duplicate_family_ids'] or '(none)'}"
                ),
                (
                    "- Closest: "
                    f"{comparison_row['closest_family_id'] or '(none)'} "
                    f"{comparison_row['closest_status']} "
                    f"delta={comparison_row['closest_rt_delta_sec']} sec"
                ),
                "",
            ],
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_svg(
    path: Path,
    comparison: list[dict[str, object]],
    config: AuditConfig,
) -> None:
    row_height = 34
    width = 900
    height = max(160, 80 + len(comparison) * row_height)
    colors = {
        PASS_MODE: "#2ca02c",
        SPLIT_MODE: "#ff7f0e",
        DRIFT_MODE: "#1f77b4",
        DUPLICATE_MODE: "#9467bd",
        MISS_MODE: "#d62728",
    }
    items = [
        _svg_text(20, 30, f"Targeted GT Audit: {config.target_label}", 18),
        _svg_text(20, 55, f"Alignment run: {config.alignment_run}", 11),
    ]
    for index, row in enumerate(comparison):
        y = 90 + index * row_height
        mode = str(row["failure_mode"])
        color = colors.get(mode, "#777777")
        items.append(
            f'<rect x="20" y="{y - 18}" width="18" height="18" fill="{color}" />',
        )
        label = (
            f"{row['sample_stem']}  {mode}  "
            f"closest={row['closest_family_id'] or 'NA'} "
            f"delta={row['closest_rt_delta_sec'] or 'NA'} sec"
        )
        items.append(_svg_text(50, y - 4, label, 12))
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">\n'
        '<rect width="100%" height="100%" fill="white" />\n'
        + "\n".join(items)
        + "\n</svg>\n"
    )
    path.write_text(svg, encoding="utf-8")


def _write_dict_csv(path: Path, rows: Sequence[object]) -> None:
    serialized = [_as_output_dict(row) for row in rows]
    if not serialized:
        path.write_text("", encoding="utf-8")
        return
    write_delimited_rows(
        path,
        serialized,
        tuple(serialized[0]),
        extrasaction="raise",
        formatter=_format_csv_value,
    )


def _format_csv_value(value: object) -> str:
    escaped = _escape_excel_formula(value)
    if escaped is None:
        return ""
    return str(escaped)


def _as_output_dict(row: object) -> dict[str, object]:
    if isinstance(row, TargetGroundTruth):
        return {
            "sample_stem": row.sample_stem,
            "group": row.group,
            "target_mz": _format_float(row.target_mz, 6),
            "target_rt_min": _format_float(row.target_rt_min, 4),
            "target_peak_start_min": _format_float(row.target_peak_start_min, 4),
            "target_peak_end_min": _format_float(row.target_peak_end_min, 4),
            "target_peak_width_min": _format_float(row.target_peak_width_min, 4),
            "target_area": _format_float(row.target_area, 2),
            "target_confidence": row.target_confidence,
            "target_nl_ok": row.target_nl_ok,
            "target_reason": row.target_reason,
            "istd_rt_min": _format_float(row.istd_rt_min, 4),
            "istd_rt_delta_sec": _format_float(row.istd_rt_delta_sec, 2),
        }
    return dict(cast(Any, row))


def _escape_excel_formula(value: object) -> object:
    if (
        isinstance(value, str)
        and value.startswith(("=", "+", "-", "@"))
        and not _is_numeric_text(value)
    ):
        return f"'{value}"
    return value


def _svg_text(x: int, y: int, text: str, size: int) -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="Arial, sans-serif" '
        f'font-size="{size}">{html.escape(text)}</text>'
    )
