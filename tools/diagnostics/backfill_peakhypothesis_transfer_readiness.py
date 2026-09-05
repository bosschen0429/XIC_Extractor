"""Summarize transfer readiness for a reviewed PeakHypothesis backfill slice."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.diagnostics import backfill_peakhypothesis_transfer_readiness


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        promotion_summary = _read_json(args.promotion_summary_json)
        activation_acceptance_rows = _read_tsv(args.activation_acceptance_tsv)
        full_cohort_metadata = _read_json(
            args.full_cohort_alignment_dir / "alignment_run_metadata.json",
        )
        full_cohort_counts = _full_cohort_counts(args.full_cohort_alignment_dir)
        full_cohort_acceptance_gate_summary = (
            _read_json(args.full_cohort_acceptance_gate_summary_json)
            if args.full_cohort_acceptance_gate_summary_json is not None
            else None
        )
        full_cohort_winner_remap_summary = (
            _read_json(args.full_cohort_winner_remap_summary_json)
            if args.full_cohort_winner_remap_summary_json is not None
            else None
        )
        full_cohort_hypothesis_review_summary = (
            _read_json(args.full_cohort_hypothesis_review_summary_json)
            if args.full_cohort_hypothesis_review_summary_json is not None
            else None
        )
        index = (
            backfill_peakhypothesis_transfer_readiness.build_transfer_readiness(
                promotion_summary=promotion_summary,
                activation_acceptance_rows=activation_acceptance_rows,
                full_cohort_metadata=full_cohort_metadata,
                full_cohort_counts=full_cohort_counts,
                full_cohort_acceptance_gate_summary=full_cohort_acceptance_gate_summary,
                full_cohort_winner_remap_summary=full_cohort_winner_remap_summary,
                full_cohort_hypothesis_review_summary=full_cohort_hypothesis_review_summary,
                source_run_id=args.source_run_id,
                expected_full_cohort_sample_columns=args.expected_full_cohort_sample_columns,
            )
        )
        outputs = (
            backfill_peakhypothesis_transfer_readiness.write_transfer_readiness_outputs(
                args.output_dir,
                index,
            )
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Transfer readiness TSV: {outputs.readiness_tsv}")
    print(f"Transfer readiness summary JSON: {outputs.summary_json}")
    return 0 if not index.readiness_row["hard_fail_reasons"] else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promotion-summary-json", type=Path, required=True)
    parser.add_argument("--activation-acceptance-tsv", type=Path, required=True)
    parser.add_argument("--full_cohort-alignment-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-run-id", default="")
    parser.add_argument("--expected-full_cohort-sample-columns", type=int, default=85)
    parser.add_argument("--full-cohort-acceptance-gate-summary-json", type=Path)
    parser.add_argument("--full_cohort-winner-remap-summary-json", type=Path)
    parser.add_argument("--full_cohort-hypothesis-review-summary-json", type=Path)
    return parser.parse_args(argv)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _read_tsv(path: Path) -> tuple[dict[str, str], ...]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle, delimiter="\t"))


def _full_cohort_counts(alignment_dir: Path) -> dict[str, int]:
    matrix_path = alignment_dir / "alignment_matrix.tsv"
    review_path = alignment_dir / "alignment_review.tsv"
    cells_path = alignment_dir / "alignment_cells.tsv"
    skipped_path = alignment_dir / "skipped_evidence_ledger.tsv"
    return {
        "matrix_row_count": _data_row_count(matrix_path),
        "sample_column_count": _sample_column_count(matrix_path),
        "review_row_count": _data_row_count(review_path),
        "cell_row_count": _data_row_count(cells_path),
        "skipped_evidence_row_count": (
            _data_row_count(skipped_path) if skipped_path.exists() else 0
        ),
    }


def _data_row_count(path: Path) -> int:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        line_count = sum(1 for _line in handle)
    return max(0, line_count - 1)


def _sample_column_count(matrix_path: Path) -> int:
    if not matrix_path.exists():
        raise FileNotFoundError(str(matrix_path))
    with matrix_path.open("r", encoding="utf-8-sig", newline="") as handle:
        header = handle.readline().rstrip("\n\r").split("\t")
    return len([column for column in header if column not in {"Mz", "RT"}])


if __name__ == "__main__":
    raise SystemExit(main())
