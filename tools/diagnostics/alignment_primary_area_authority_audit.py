from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.alignment.primary_matrix_area import (
    MISSING_MS1_MORPHOLOGY_AREA,
    PRIMARY_MATRIX_AREA_SOURCES,
)
from xic_extractor.diagnostics.diagnostic_io import read_tsv_required, write_tsv

REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "area",
    "primary_matrix_area",
    "primary_matrix_area_source",
    "primary_matrix_area_reason",
)

SUMMARY_FIELDS = (
    "alignment_cell_count",
    "gaussian_primary_area_count",
    "fail_closed_missing_morphology_count",
    "non_gaussian_primary_area_count",
    "gate_decision",
)

FLAG_ROW_FIELDS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "area",
    "primary_matrix_area",
    "primary_matrix_area_source",
    "primary_matrix_area_reason",
    "authority_class",
)


def summarize_primary_area_authority(path: Path) -> dict[str, int | str]:
    rows = read_tsv_required(path, REQUIRED_COLUMNS)
    summary, _flagged = _analyze_primary_area_authority_rows(rows)
    return summary


def flagged_primary_area_authority_rows(path: Path) -> list[dict[str, str]]:
    rows = read_tsv_required(path, REQUIRED_COLUMNS)
    _summary, flagged = _analyze_primary_area_authority_rows(rows)
    return flagged


def _analyze_primary_area_authority_rows(
    rows: Sequence[dict[str, str]],
) -> tuple[dict[str, int | str], list[dict[str, str]]]:
    counts = Counter(_authority_class(row) for row in rows)
    non_gaussian = counts["non_gaussian_primary_area"]
    fail_closed = counts["fail_closed_missing_morphology"]
    summary: dict[str, int | str] = {
        "alignment_cell_count": len(rows),
        "gaussian_primary_area_count": counts["gaussian_primary_area"],
        "fail_closed_missing_morphology_count": fail_closed,
        "non_gaussian_primary_area_count": non_gaussian,
        "gate_decision": _gate_decision(
            non_gaussian_primary_area_count=non_gaussian,
            fail_closed_missing_morphology_count=fail_closed,
        ),
    }
    flagged: list[dict[str, str]] = []
    for row in rows:
        authority_class = _authority_class(row)
        if authority_class == "gaussian_primary_area":
            continue
        flagged.append({**row, "authority_class": authority_class})
    return summary, flagged


def _authority_class(row: dict[str, str]) -> str:
    primary_value = row.get("primary_matrix_area", "")
    primary_area = _positive_float(primary_value)
    source = row.get("primary_matrix_area_source", "")
    reason = row.get("primary_matrix_area_reason", "")
    if _has_nonblank_value(primary_value) and (
        source not in PRIMARY_MATRIX_AREA_SOURCES
    ):
        return "non_gaussian_primary_area"
    if primary_area is not None and source in PRIMARY_MATRIX_AREA_SOURCES:
        return "gaussian_primary_area"
    if reason == MISSING_MS1_MORPHOLOGY_AREA:
        return "fail_closed_missing_morphology"
    return "no_primary_area"


def _has_nonblank_value(value: str) -> bool:
    return str(value).strip() != ""


def _gate_decision(
    *,
    non_gaussian_primary_area_count: int,
    fail_closed_missing_morphology_count: int,
) -> str:
    if non_gaussian_primary_area_count:
        return "fail"
    if fail_closed_missing_morphology_count:
        return "defer"
    return "promote"


def _positive_float(value: str) -> float | None:
    try:
        parsed = float(str(value).strip())
    except ValueError:
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit alignment_cells primary area authority against the "
            "Gaussian15 morphology area contract."
        )
    )
    parser.add_argument("--alignment-cells-tsv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = read_tsv_required(args.alignment_cells_tsv, REQUIRED_COLUMNS)
    summary, flagged_rows = _analyze_primary_area_authority_rows(rows)
    (args.output_dir / "alignment_primary_area_authority_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_tsv(
        args.output_dir / "alignment_primary_area_authority_summary.tsv",
        [summary],
        SUMMARY_FIELDS,
    )
    write_tsv(
        args.output_dir / "alignment_primary_area_authority_rows.tsv",
        flagged_rows,
        FLAG_ROW_FIELDS,
    )


if __name__ == "__main__":
    main()
