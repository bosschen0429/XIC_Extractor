from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from tools.diagnostics.diagnostic_io import write_tsv

GATE_VERSION = "ms1_peak_group_nl_scope_gate"
CHROM_SOURCE = "chrom_peak_segment"
MS1_PEAK_GROUP_SOURCE = "gaussian15_ms1_peak_group"
REQUIRED_COLUMNS = (
    "sample_name",
    "target_label",
    "role",
    "candidate_id",
    "selected",
    "proposal_sources",
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "best_ms2_scan_rt_min",
    "apex_ms2_delta_min",
    "ms2_present",
    "nl_match",
    "nl_status",
    "strict_nl_scan_count",
    "ms1_peak_group_source",
    "ms1_peak_group_rt_min",
    "ms1_peak_group_rt_max",
    "ms1_peak_group_trigger_scan_count",
    "ms1_peak_group_strict_nl_scan_count",
    "ms1_peak_group_strict_nl_event_count",
    "outside_ms1_peak_group_trigger_scan_count",
    "outside_ms1_peak_group_strict_nl_scan_count",
)
REVIEW_ROW_FIELDS = (
    "sample_name",
    "target_label",
    "role",
    "candidate_id",
    "review_reason",
    "selected",
    "proposal_sources",
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "best_ms2_scan_rt_min",
    "apex_ms2_delta_min",
    "ms2_present",
    "nl_match",
    "nl_status",
    "strict_nl_scan_count",
    "ms1_peak_group_source",
    "ms1_peak_group_rt_min",
    "ms1_peak_group_rt_max",
    "ms1_peak_group_trigger_scan_count",
    "ms1_peak_group_strict_nl_scan_count",
    "ms1_peak_group_strict_nl_event_count",
    "outside_ms1_peak_group_trigger_scan_count",
    "outside_ms1_peak_group_strict_nl_scan_count",
    "diagnostic_product_absence_reason",
    "confidence",
    "reason",
)
CONTEXT_ROW_FIELDS = (
    "sample_name",
    "target_label",
    "role",
    "candidate_id",
    "context_reason",
    "selected",
    "proposal_sources",
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "best_ms2_scan_rt_min",
    "apex_ms2_delta_min",
    "ms2_present",
    "nl_match",
    "nl_status",
    "strict_nl_scan_count",
    "ms1_peak_group_source",
    "ms1_peak_group_rt_min",
    "ms1_peak_group_rt_max",
    "ms1_peak_group_trigger_scan_count",
    "ms1_peak_group_strict_nl_scan_count",
    "ms1_peak_group_strict_nl_event_count",
    "outside_ms1_peak_group_trigger_scan_count",
    "outside_ms1_peak_group_strict_nl_scan_count",
    "diagnostic_product_absence_reason",
    "confidence",
    "reason",
)


def build_gate_report(
    peak_candidate_rows: Sequence[dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    missing_columns = _missing_required_columns(peak_candidate_rows)
    if missing_columns:
        return (
            {
                "gate_version": GATE_VERSION,
                "gate_decision": "defer",
                "blocking_reasons": ["missing_required_columns"],
                "missing_required_columns": missing_columns,
                "row_count": len(peak_candidate_rows),
            },
            [],
            [],
        )

    selected_rows = [row for row in peak_candidate_rows if _is_selected(row)]
    chrom_rows = [row for row in peak_candidate_rows if _has_chrom_source(row)]
    selected_chrom_rows = [
        row for row in selected_rows if _has_chrom_source(row)
    ]
    selected_chrom_peak_group_rows = [
        row for row in selected_chrom_rows if _has_ms1_peak_group_scope(row)
    ]
    selected_chrom_missing_scope_rows = [
        row for row in selected_chrom_rows if not _has_ms1_peak_group_scope(row)
    ]
    unexpected_ms1_peak_group_source_rows = [
        row
        for row in selected_chrom_peak_group_rows
        if row.get("ms1_peak_group_source", "").strip() != MS1_PEAK_GROUP_SOURCE
    ]
    selected_apex_outside_scope_rows = [
        row
        for row in selected_chrom_peak_group_rows
        if not _selected_apex_inside_group(row)
    ]
    borrowed_strict_nl_support_rows = [
        row for row in selected_chrom_peak_group_rows if _has_borrowed_strict_nl(row)
    ]
    selected_chrom_outside_strict_nl_rows = [
        row
        for row in selected_chrom_peak_group_rows
        if _int(row.get("outside_ms1_peak_group_strict_nl_scan_count", "")) > 0
    ]
    selected_chrom_repeated_strict_nl_scan_rows = [
        row
        for row in selected_chrom_peak_group_rows
        if _int(row.get("ms1_peak_group_strict_nl_scan_count", ""))
        > _int(row.get("ms1_peak_group_strict_nl_event_count", ""))
    ]

    blocking_reasons: list[str] = []
    if not selected_chrom_rows:
        blocking_reasons.append("no_selected_chrom_peak_segment_rows")
    if selected_chrom_missing_scope_rows:
        blocking_reasons.append(
            "selected_chrom_peak_segment_missing_ms1_peak_group_scope"
        )
    if unexpected_ms1_peak_group_source_rows:
        blocking_reasons.append("unexpected_ms1_peak_group_source")
    if selected_apex_outside_scope_rows:
        blocking_reasons.append("selected_apex_outside_ms1_peak_group_scope")
    if borrowed_strict_nl_support_rows:
        blocking_reasons.append(
            "borrowed_strict_nl_support_outside_ms1_peak_group"
        )

    review_rows = [
        *_review_rows(
            selected_chrom_missing_scope_rows,
            "selected_chrom_peak_segment_missing_ms1_peak_group_scope",
        ),
        *_review_rows(
            unexpected_ms1_peak_group_source_rows,
            "unexpected_ms1_peak_group_source",
        ),
        *_review_rows(
            selected_apex_outside_scope_rows,
            "selected_apex_outside_ms1_peak_group_scope",
        ),
        *_review_rows(
            borrowed_strict_nl_support_rows,
            "borrowed_strict_nl_support_outside_ms1_peak_group",
        ),
    ]
    context_rows = _context_rows(
        selected_chrom_outside_strict_nl_rows,
        "outside_strict_nl_observed",
    )
    manifest: dict[str, Any] = {
        "gate_version": GATE_VERSION,
        "gate_decision": "defer" if blocking_reasons else "promote",
        "blocking_reasons": blocking_reasons,
        "row_count": len(peak_candidate_rows),
        "selected_count": len(selected_rows),
        "chrom_candidate_count": len(chrom_rows),
        "selected_chrom_count": len(selected_chrom_rows),
        "selected_chrom_peak_group_rows": len(selected_chrom_peak_group_rows),
        "selected_chrom_missing_scope_rows": len(
            selected_chrom_missing_scope_rows
        ),
        "unexpected_ms1_peak_group_source_rows": len(
            unexpected_ms1_peak_group_source_rows
        ),
        "selected_apex_outside_scope_rows": len(
            selected_apex_outside_scope_rows
        ),
        "borrowed_strict_nl_support_rows": len(borrowed_strict_nl_support_rows),
        "selected_chrom_outside_strict_nl_rows": len(
            selected_chrom_outside_strict_nl_rows
        ),
        "selected_chrom_repeated_strict_nl_scan_rows": len(
            selected_chrom_repeated_strict_nl_scan_rows
        ),
        "selected_chrom_peak_group_trigger_scan_count": sum(
            _int(row.get("ms1_peak_group_trigger_scan_count", ""))
            for row in selected_chrom_peak_group_rows
        ),
        "selected_chrom_peak_group_strict_nl_scan_count": sum(
            _int(row.get("ms1_peak_group_strict_nl_scan_count", ""))
            for row in selected_chrom_peak_group_rows
        ),
        "selected_chrom_peak_group_strict_nl_event_count": sum(
            _int(row.get("ms1_peak_group_strict_nl_event_count", ""))
            for row in selected_chrom_peak_group_rows
        ),
        "selected_chrom_outside_strict_nl_scan_count": sum(
            _int(row.get("outside_ms1_peak_group_strict_nl_scan_count", ""))
            for row in selected_chrom_peak_group_rows
        ),
        "selected_chrom_by_role": _counter_dict(
            row.get("role", "") for row in selected_chrom_rows
        ),
        "selected_chrom_by_ms1_peak_group_source": _counter_dict(
            row.get("ms1_peak_group_source", "")
            for row in selected_chrom_peak_group_rows
        ),
        "review_row_count": len(review_rows),
        "review_rows_by_reason": _counter_dict(
            row.get("review_reason", "") for row in review_rows
        ),
        "context_row_count": len(context_rows),
        "context_rows_by_reason": _counter_dict(
            row.get("context_reason", "") for row in context_rows
        ),
    }
    return manifest, review_rows, context_rows


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    rows = _read_tsv(args.peak_candidates_tsv)
    manifest, review_rows, context_rows = build_gate_report(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "ms1_peak_group_nl_scope_gate_manifest.json"
    review_path = args.output_dir / "ms1_peak_group_nl_scope_review_rows.tsv"
    context_path = args.output_dir / "ms1_peak_group_nl_scope_context_rows.tsv"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _write_tsv(review_path, review_rows, REVIEW_ROW_FIELDS)
    _write_tsv(context_path, context_rows, CONTEXT_ROW_FIELDS)
    return 0 if manifest["gate_decision"] == "promote" else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Gate selected chrom_peak_segment MS2/NL evidence so strict NL "
            "support must belong to the same Gaussian15 MS1 peak group."
        )
    )
    parser.add_argument("--peak-candidates-tsv", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args(argv)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _write_tsv(
    path: Path,
    rows: Sequence[dict[str, str]],
    fieldnames: Sequence[str],
) -> None:
    write_tsv(
        path,
        rows,
        fieldnames,
        extrasaction="raise",
        formatter=_format_tsv_value,
    )


def _format_tsv_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _missing_required_columns(rows: Sequence[dict[str, str]]) -> list[str]:
    observed = set().union(*(row.keys() for row in rows)) if rows else set()
    return [column for column in REQUIRED_COLUMNS if column not in observed]


def _review_rows(
    rows: Sequence[dict[str, str]],
    review_reason: str,
) -> list[dict[str, str]]:
    review_rows: list[dict[str, str]] = []
    for row in rows:
        review_row = {
            field: row.get(field, "")
            for field in REVIEW_ROW_FIELDS
            if field != "review_reason"
        }
        review_row["review_reason"] = review_reason
        review_rows.append(review_row)
    return review_rows


def _context_rows(
    rows: Sequence[dict[str, str]],
    context_reason: str,
) -> list[dict[str, str]]:
    context_rows: list[dict[str, str]] = []
    for row in rows:
        context_row = {
            field: row.get(field, "")
            for field in CONTEXT_ROW_FIELDS
            if field != "context_reason"
        }
        context_row["context_reason"] = context_reason
        context_rows.append(context_row)
    return context_rows


def _is_selected(row: dict[str, str]) -> bool:
    return row.get("selected", "").upper() == "TRUE"


def _has_chrom_source(row: dict[str, str]) -> bool:
    return CHROM_SOURCE in _split_labels(row.get("proposal_sources", ""))


def _has_ms1_peak_group_scope(row: dict[str, str]) -> bool:
    return (
        row.get("ms1_peak_group_source", "").strip() != ""
        and _float(row.get("ms1_peak_group_rt_min", "")) is not None
        and _float(row.get("ms1_peak_group_rt_max", "")) is not None
    )


def _selected_apex_inside_group(row: dict[str, str]) -> bool:
    apex = _float(row.get("rt_apex_min", ""))
    left = _float(row.get("ms1_peak_group_rt_min", ""))
    right = _float(row.get("ms1_peak_group_rt_max", ""))
    if apex is None or left is None or right is None:
        return False
    return left <= apex <= right


def _has_borrowed_strict_nl(row: dict[str, str]) -> bool:
    outside_strict_nl = _int(row.get("outside_ms1_peak_group_strict_nl_scan_count", ""))
    if outside_strict_nl <= 0:
        return False
    in_group_strict_nl = _int(row.get("ms1_peak_group_strict_nl_scan_count", ""))
    if in_group_strict_nl > 0:
        return False
    return (
        row.get("nl_match", "").upper() == "TRUE"
        or _int(row.get("strict_nl_scan_count", "")) > 0
    )


def _split_labels(value: str) -> set[str]:
    return {
        label.strip()
        for label in value.split(";")
        if label.strip()
    }


def _counter_dict(values: Sequence[str] | Any) -> dict[str, int]:
    return dict(sorted(Counter(value for value in values if value).items()))


def _int(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
