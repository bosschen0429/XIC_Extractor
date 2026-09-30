from __future__ import annotations

import argparse
import json
import math
from collections.abc import Sequence
from pathlib import Path
from statistics import median

from xic_extractor.tabular_io import (
    optional_float,
    optional_int,
    read_tsv_required,
    text_value,
    write_tsv,
)

READINESS_LABEL = "diagnostic_pressure_test_surface"
PRODUCT_ACTION = "diagnostic_only_no_product_mutation"

REQUIRED_COLUMNS = (
    "sample_name",
    "target_label",
    "candidate_id",
    "selected",
    "area_raw_counts_seconds",
    "area_ms1_morphology",
    "ms1_morphology_area_source",
    "ms1_morphology_trace_method",
    "ms1_morphology_trace_window_points",
    "ms1_morphology_trace_effective_points",
    "region_scan_count",
    "region_duration_min",
)

ROW_FIELDS = (
    "sample_name",
    "target_label",
    "candidate_id",
    "selected",
    "area_raw_counts_seconds",
    "area_ms1_morphology",
    "gaussian_to_raw_area_ratio",
    "area_pressure_class",
    "region_scan_count",
    "region_duration_min",
    "estimated_scan_interval_sec",
    "ms1_morphology_area_source",
    "ms1_morphology_trace_method",
    "ms1_morphology_trace_window_points",
    "ms1_morphology_trace_effective_points",
    "estimated_gaussian15_window_sec",
    "scan_rate_pressure_class",
    "readiness_label",
    "product_action",
)

SUMMARY_FIELDS = (
    "readiness_label",
    "product_action",
    "candidate_row_count",
    "selected_candidate_count",
    "comparable_area_count",
    "missing_gaussian_area_count",
    "missing_raw_area_count",
    "large_area_delta_count",
    "selected_large_area_delta_count",
    "fixed_window_unknown_count",
    "fixed_window_edge_truncated_count",
    "fixed_window_wide_count",
    "median_gaussian_to_raw_ratio",
    "p95_gaussian_to_raw_ratio",
    "max_estimated_gaussian15_window_sec",
)


def summarize_gaussian15_area_pressure(path: Path) -> dict[str, int | float | str]:
    rows = gaussian15_area_pressure_rows(path)
    return _summarize_gaussian15_area_pressure_rows(rows)


def _summarize_gaussian15_area_pressure_rows(
    rows: Sequence[dict[str, str | float]],
) -> dict[str, int | float | str]:
    ratios = [
        row["gaussian_to_raw_area_ratio"]
        for row in rows
        if isinstance(row["gaussian_to_raw_area_ratio"], float)
    ]
    window_seconds = [
        row["estimated_gaussian15_window_sec"]
        for row in rows
        if isinstance(row["estimated_gaussian15_window_sec"], float)
    ]
    return {
        "readiness_label": READINESS_LABEL,
        "product_action": PRODUCT_ACTION,
        "candidate_row_count": len(rows),
        "selected_candidate_count": sum(1 for row in rows if row["selected"] == "TRUE"),
        "comparable_area_count": len(ratios),
        "missing_gaussian_area_count": _count(rows, "missing_gaussian_area"),
        "missing_raw_area_count": _count(rows, "missing_raw_area"),
        "large_area_delta_count": _count(rows, "large_delta"),
        "selected_large_area_delta_count": sum(
            1
            for row in rows
            if row["selected"] == "TRUE" and row["area_pressure_class"] == "large_delta"
        ),
        "fixed_window_unknown_count": _scan_rate_count(rows, "unknown_scan_rate"),
        "fixed_window_edge_truncated_count": _scan_rate_count(
            rows,
            "edge_truncated_or_short_trace",
        ),
        "fixed_window_wide_count": _scan_rate_count(rows, "wide_fixed_point_window"),
        "median_gaussian_to_raw_ratio": round(median(ratios), 6) if ratios else "",
        "p95_gaussian_to_raw_ratio": _nearest_rank_percentile(ratios, 0.95),
        "max_estimated_gaussian15_window_sec": (
            round(max(window_seconds), 6) if window_seconds else ""
        ),
    }


def gaussian15_area_pressure_rows(path: Path) -> list[dict[str, str | float]]:
    rows = read_tsv_required(path, REQUIRED_COLUMNS)
    return [_audit_row(row) for row in rows]


def _audit_row(row: dict[str, str]) -> dict[str, str | float]:
    raw_area = _positive_float(row.get("area_raw_counts_seconds", ""))
    morphology_area = _positive_float(row.get("area_ms1_morphology", ""))
    ratio = (
        round(morphology_area / raw_area, 6)
        if raw_area is not None and morphology_area is not None
        else ""
    )
    scan_interval_sec = _estimated_scan_interval_sec(row)
    window_sec = _estimated_gaussian15_window_sec(row, scan_interval_sec)
    return {
        "sample_name": text_value(row.get("sample_name", "")),
        "target_label": text_value(row.get("target_label", "")),
        "candidate_id": text_value(row.get("candidate_id", "")),
        "selected": _selected_text(row.get("selected", "")),
        "area_raw_counts_seconds": row.get("area_raw_counts_seconds", ""),
        "area_ms1_morphology": row.get("area_ms1_morphology", ""),
        "gaussian_to_raw_area_ratio": ratio,
        "area_pressure_class": _area_pressure_class(raw_area, morphology_area, ratio),
        "region_scan_count": row.get("region_scan_count", ""),
        "region_duration_min": row.get("region_duration_min", ""),
        "estimated_scan_interval_sec": scan_interval_sec or "",
        "ms1_morphology_area_source": text_value(
            row.get("ms1_morphology_area_source", "")
        ),
        "ms1_morphology_trace_method": text_value(
            row.get("ms1_morphology_trace_method", "")
        ),
        "ms1_morphology_trace_window_points": row.get(
            "ms1_morphology_trace_window_points",
            "",
        ),
        "ms1_morphology_trace_effective_points": row.get(
            "ms1_morphology_trace_effective_points",
            "",
        ),
        "estimated_gaussian15_window_sec": window_sec or "",
        "scan_rate_pressure_class": _scan_rate_pressure_class(row, window_sec),
        "readiness_label": READINESS_LABEL,
        "product_action": PRODUCT_ACTION,
    }


def _area_pressure_class(
    raw_area: float | None,
    morphology_area: float | None,
    ratio: str | float,
) -> str:
    if morphology_area is None:
        return "missing_gaussian_area"
    if raw_area is None:
        return "missing_raw_area"
    if not isinstance(ratio, float):
        return "missing_comparison"
    if 0.8 <= ratio <= 1.2:
        return "within_20pct"
    if 0.5 <= ratio <= 2.0:
        return "moderate_delta"
    return "large_delta"


def _estimated_scan_interval_sec(row: dict[str, str]) -> float | None:
    scan_count = optional_int(row.get("region_scan_count", ""))
    duration_min = optional_float(row.get("region_duration_min", ""))
    if (
        scan_count is None
        or scan_count < 2
        or duration_min is None
        or duration_min <= 0
    ):
        return None
    return round((duration_min * 60.0) / (scan_count - 1), 6)


def _estimated_gaussian15_window_sec(
    row: dict[str, str],
    scan_interval_sec: float | None,
) -> float | None:
    effective_points = optional_int(
        row.get("ms1_morphology_trace_effective_points", "")
    )
    if scan_interval_sec is None or effective_points is None or effective_points <= 0:
        return None
    return round(scan_interval_sec * effective_points, 6)


def _scan_rate_pressure_class(
    row: dict[str, str],
    window_sec: float | None,
) -> str:
    effective_points = optional_int(
        row.get("ms1_morphology_trace_effective_points", "")
    )
    window_points = optional_int(row.get("ms1_morphology_trace_window_points", ""))
    if window_sec is None or effective_points is None:
        return "unknown_scan_rate"
    if window_points is not None and effective_points < window_points:
        return "edge_truncated_or_short_trace"
    if window_sec > 30.0:
        return "wide_fixed_point_window"
    if window_sec < 3.0:
        return "narrow_fixed_point_window"
    return "nominal_observed"


def _positive_float(value: object) -> float | None:
    parsed = optional_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _selected_text(value: object) -> str:
    return (
        "TRUE"
        if text_value(value).upper() in {"TRUE", "T", "YES", "Y", "1"}
        else "FALSE"
    )


def _nearest_rank_percentile(values: list[float], percentile: float) -> float | str:
    if not values:
        return ""
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 6)


def _count(rows: Sequence[dict[str, str | float]], area_class: str) -> int:
    return sum(1 for row in rows if row["area_pressure_class"] == area_class)


def _scan_rate_count(
    rows: Sequence[dict[str, str | float]],
    scan_rate_class: str,
) -> int:
    return sum(
        1 for row in rows if row["scan_rate_pressure_class"] == scan_rate_class
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a diagnostic-only pressure audit for Gaussian15 morphology "
            "area versus raw area and fixed-point smoothing duration."
        )
    )
    parser.add_argument("--peak-candidates-tsv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = gaussian15_area_pressure_rows(args.peak_candidates_tsv)
    summary = _summarize_gaussian15_area_pressure_rows(rows)
    (args.output_dir / "gaussian15_area_pressure_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_tsv(
        args.output_dir / "gaussian15_area_pressure_summary.tsv",
        [summary],
        SUMMARY_FIELDS,
    )
    write_tsv(
        args.output_dir / "gaussian15_area_pressure_rows.tsv",
        rows,
        ROW_FIELDS,
    )


if __name__ == "__main__":
    main()
