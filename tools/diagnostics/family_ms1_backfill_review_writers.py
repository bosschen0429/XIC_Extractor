"""Write legacy family-id peak-group MS1 backfill review reports."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.diagnostics.diagnostic_io import write_tsv
from tools.diagnostics.family_ms1_backfill_review_model import _float


def write_outputs(output_dir: Path, result: Mapping[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = list(result["candidates"])
    queue = list(result["image_queue"])
    summary = list(result["summary"])
    _write_tsv(
        output_dir / "family_ms1_backfill_review_candidates.tsv",
        candidates,
        _candidate_fields(),
    )
    _write_tsv(
        output_dir / "family_ms1_backfill_review_queue.tsv",
        queue,
        _candidate_fields(),
    )
    _write_tsv(
        output_dir / "family_ms1_backfill_review_summary.tsv",
        summary,
        ("metric", "value"),
    )
    (output_dir / "family_ms1_backfill_review.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_markdown(output_dir / "family_ms1_backfill_review.md", result)


def _write_markdown(path: Path, result: Mapping[str, Any]) -> None:
    summary = {row["metric"]: row["value"] for row in result["summary"]}
    supported_count = _summary_int(
        summary,
        "classification:ms1_supported_dda_limited_backfill",
    ) + _summary_int(summary, "classification:ms1_supported_backfill")
    manual_review_count = (
        _summary_int(summary, "classification:neighboring_interference_review")
        + _summary_int(summary, "classification:uncertain_shape_review")
        + _summary_int(summary, "classification:overlay_review_required")
    )
    high_priority_count = _summary_int(
        summary,
        "classification:needs_ms1_overlay_high_priority",
    )
    pending_overlay_count = high_priority_count + _summary_int(
        summary,
        "classification:needs_ms1_overlay",
    )
    lines = [
        "# Peak-Group MS1 Backfill Review Report",
        "",
        "## Review Verdict",
        "",
        (
            f"- {summary.get('candidate_count', '0')} low-seed/high-backfill "
            "primary peak groups need MS1 review discipline."
        ),
        (
            f"- {supported_count} already have overlay evidence supporting "
            "MS1-backed DDA-limited backfill."
        ),
        (
            f"- {manual_review_count} already show interference or uncertain "
            "shape and should not feed a production gate automatically."
        ),
        (
            f"- {pending_overlay_count} still need RAW-backed overlay evidence; "
            f"the first {summary.get('image_queue_count', '0')} are queued."
        ),
        "",
        "## Run Context",
        "",
        f"- Alignment dir: `{result['alignment_dir']}`",
        f"- Neutral loss tag: `{result['neutral_loss_tag']}`",
        "",
        "## Classification Counts",
        "",
    ]
    for key, value in summary.items():
        if key.startswith("classification:"):
            lines.append(f"- `{key.removeprefix('classification:')}`: {value}")
    lines.extend(
        [
            "",
            "## Top Image Queue",
            "",
            (
                "| # | peak group | m/z | RT window | seeds/backfill | class | "
                "next action |"
            ),
            "|---:|---|---:|---|---:|---|---|",
        ]
    )
    for index, row in enumerate(result["image_queue"][:10], start=1):
        lines.append(_markdown_queue_row(index, row))
    lines.extend(
        [
            "",
            "## Output Files",
            "",
            (
                "- `family_ms1_backfill_review_candidates.tsv`: full "
                "machine-readable candidate table."
            ),
            (
                "- `family_ms1_backfill_review_queue.tsv`: human review queue "
                "with overlay command arguments."
            ),
            "- `family_ms1_backfill_review_summary.tsv`: compact count summary.",
            "- `family_ms1_backfill_review.json`: complete structured report.",
            "",
            "## Intended Use",
            "",
            (
                "This report separates cheap alignment-level screening from RAW-backed "
                "MS1 overlay evidence. Generate plots only for queued or "
                "manually selected peak groups. The queue TSV includes "
                "per-group overlay command arguments; add the run-level "
                "alignment-cells, RAW, DLL, and output "
                "paths when rendering plots."
            ),
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _summary_int(summary: Mapping[str, str], key: str) -> int:
    return int(_float(summary.get(key)) or 0)


def _markdown_queue_row(index: int, row: Mapping[str, Any]) -> str:
    window = f"{row.get('suggested_rt_min', '')}-{row.get('suggested_rt_max', '')}"
    seeds = f"{row.get('detected_count', '')}/{row.get('accepted_backfill_count', '')}"
    return (
        f"| {index} | `{row.get('feature_family_id', '')}` "
        f"| {row.get('family_center_mz', '')} "
        f"| {window} "
        f"| {seeds} "
        f"| `{row.get('review_classification', '')}` "
        f"| `{row.get('recommended_next_action', '')}` |"
    )


def _write_tsv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
) -> None:
    write_tsv(path, rows, fields, formatter=_format_value, lineterminator="\n")


def _candidate_fields() -> tuple[str, ...]:
    return (
        "feature_family_id",
        "neutral_loss_tag",
        "family_center_mz",
        "family_center_rt",
        "suggested_rt_min",
        "suggested_rt_max",
        "suggested_output_prefix",
        "suggested_overlay_command_args",
        "detected_count",
        "accepted_backfill_count",
        "accepted_cell_count",
        "rescue_fraction",
        "rescue_per_detected_seed",
        "detected_height_median",
        "rescued_height_median",
        "detected_to_rescued_height_ratio",
        "detected_area_median",
        "rescued_area_median",
        "detected_to_rescued_area_ratio",
        "overlay_status",
        "overlay_family_verdict",
        "dda_trigger_limited_ms2_support",
        "detected_backfilled_count",
        "global_apex_assessable_trace_count",
        "global_apex_assessable_fraction",
        "selected_apex_in_trace_window_count",
        "selected_apex_in_trace_window_fraction",
        "local_apex_assessable_trace_count",
        "global_apex_interference_count",
        "shape_supported_fraction",
        "global_apex_interference_fraction",
        "local_apex_supported_count",
        "local_apex_supported_fraction",
        "review_classification",
        "recommended_next_action",
        "review_priority_score",
        "row_flags",
        "primary_evidence",
        "reason",
    )


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)
