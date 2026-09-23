"""Build a review queue for low-seed/high-backfill peak-group rows.

The module/file name is legacy compatibility: `family` means the public
`feature_family_id` row label, not product identity authority.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.diagnostics.family_ms1_backfill_review_io import (
    _cells_by_family,
    _load_overlay_evidence,
    _read_tsv,
)
from tools.diagnostics.family_ms1_backfill_review_model import (
    _candidate_row,
    _classification_sort_key,
    _is_candidate_review_row,
    _summary_rows,
)
from tools.diagnostics.family_ms1_backfill_review_writers import write_outputs

REVIEW_REQUIRED_COLUMNS = (
    "feature_family_id",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "detected_count",
    "accepted_backfill_count",
    "accepted_cell_count",
    "include_in_primary_matrix",
)
CELLS_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "area",
    "height",
)

DEFAULT_TAG = "DNA_dR"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = build_review_report(
            alignment_dir=args.alignment_dir,
            overlay_trace_data_dirs=args.overlay_trace_data_dir or (),
            overlay_trace_data_files=args.overlay_trace_data or (),
            neutral_loss_tag=args.neutral_loss_tag,
            max_detected_count=args.max_detected_count,
            min_rescue_count=args.min_rescue_count,
            min_accepted_count=args.min_accepted_count,
            image_queue_limit=args.image_queue_limit,
        )
        write_outputs(args.output_dir, result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"peak-group MS1 backfill review report: {args.output_dir}")
    return 0


def build_review_report(
    *,
    alignment_dir: Path,
    overlay_trace_data_dirs: Sequence[Path] = (),
    overlay_trace_data_files: Sequence[Path] = (),
    neutral_loss_tag: str = DEFAULT_TAG,
    max_detected_count: int = 8,
    min_rescue_count: int = 40,
    min_accepted_count: int = 60,
    image_queue_limit: int = 30,
) -> dict[str, Any]:
    review_rows = _read_tsv(
        alignment_dir / "alignment_review.tsv",
        required_columns=REVIEW_REQUIRED_COLUMNS,
    )
    cell_rows = _read_tsv(
        alignment_dir / "alignment_cells.tsv",
        required_columns=CELLS_REQUIRED_COLUMNS,
    )
    cells_by_family = _cells_by_family(cell_rows)
    overlay_by_family = _load_overlay_evidence(
        overlay_trace_data_dirs=overlay_trace_data_dirs,
        overlay_trace_data_files=overlay_trace_data_files,
    )

    candidates: list[dict[str, Any]] = []
    for review_row in review_rows:
        if not _is_candidate_review_row(
            review_row,
            neutral_loss_tag=neutral_loss_tag,
            max_detected_count=max_detected_count,
            min_rescue_count=min_rescue_count,
            min_accepted_count=min_accepted_count,
        ):
            continue
        family_id = review_row["feature_family_id"]
        family = _candidate_row(
            review_row,
            cells_by_family.get(family_id, ()),
            overlay_by_family.get(family_id),
        )
        candidates.append(family)

    candidates.sort(key=_candidate_sort_key)
    queue = [
        row
        for row in candidates
        if row["overlay_status"] == "not_provided"
    ][:image_queue_limit]
    summary = _summary_rows(candidates, queue)
    return {
        "alignment_dir": str(alignment_dir),
        "neutral_loss_tag": neutral_loss_tag,
        "thresholds": {
            "max_detected_count": max_detected_count,
            "min_rescue_count": min_rescue_count,
            "min_accepted_count": min_accepted_count,
            "image_queue_limit": image_queue_limit,
        },
        "overlay_trace_data_dirs": [str(path) for path in overlay_trace_data_dirs],
        "overlay_trace_data_files": [str(path) for path in overlay_trace_data_files],
        "summary": summary,
        "candidates": candidates,
        "image_queue": queue,
    }


def _candidate_sort_key(row: Mapping[str, Any]) -> tuple[int, float, str]:
    return (
        _classification_sort_key(str(row["review_classification"])),
        -float(row["review_priority_score"]),
        str(row["feature_family_id"]),
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--alignment-dir",
        type=Path,
        required=True,
        help=(
            "Alignment output directory containing alignment_review.tsv and "
            "alignment_cells.tsv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for review queue, summary, JSON, and Markdown outputs",
    )
    parser.add_argument(
        "--overlay-trace-data-dir",
        type=Path,
        action="append",
        help=(
            "Directory containing *_trace_data.json files from "
            "family_ms1_overlay_plot.py"
        ),
    )
    parser.add_argument(
        "--overlay-trace-data",
        type=Path,
        action="append",
        help="Single trace_data.json file from family_ms1_overlay_plot.py",
    )
    parser.add_argument(
        "--neutral-loss-tag",
        default=DEFAULT_TAG,
        help=(
            "Primary-row neutral loss tag scope for candidate screening "
            f"(default: {DEFAULT_TAG})"
        ),
    )
    parser.add_argument(
        "--max-detected-count",
        type=int,
        default=8,
        help=(
            "Maximum detected seed count for low-seed/high-backfill candidates "
            "(default: 8)"
        ),
    )
    parser.add_argument(
        "--min-rescue-count",
        type=int,
        default=40,
        help="Minimum rescued cell count for high-backfill candidates (default: 40)",
    )
    parser.add_argument(
        "--min-accepted-count",
        type=int,
        default=60,
        help="Minimum detected+rescued accepted cell count (default: 60)",
    )
    parser.add_argument(
        "--image-queue-limit",
        type=int,
        default=30,
        help=(
            "Maximum not-yet-overlayed peak groups to place in the plotting queue "
            "(default: 30)"
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
