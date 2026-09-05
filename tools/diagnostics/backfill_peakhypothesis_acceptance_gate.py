"""Gate reviewed PeakHypothesis backfill promotions against FULL COHORT artifacts."""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.diagnostics import backfill_peakhypothesis_acceptance_gate


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        index = (
            backfill_peakhypothesis_acceptance_gate.build_full_cohort_acceptance_gate(
                promotion_rows=_read_tsv(args.promotion_cells_tsv),
                full_cohort_review_rows=_read_tsv(
                    args.full_cohort_alignment_review_tsv
                ),
                full_cohort_cell_rows=_read_tsv(args.full_cohort_alignment_cells_tsv),
                source_run_id=args.source_run_id,
            )
        )
        write_outputs = (
            backfill_peakhypothesis_acceptance_gate.write_full_cohort_acceptance_gate_outputs
        )
        outputs = write_outputs(args.output_dir, index)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"FULL COHORT slice gate TSV: {outputs.cells_tsv}")
    print(f"FULL COHORT slice gate summary JSON: {outputs.summary_json}")
    return 0 if index.summary["gate_status"] == "pass" else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promotion-cells-tsv", type=Path, required=True)
    parser.add_argument("--full_cohort-alignment-review-tsv", type=Path, required=True)
    parser.add_argument("--full_cohort-alignment-cells-tsv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-run-id", default="")
    return parser.parse_args(argv)


def _read_tsv(path: Path) -> tuple[dict[str, str], ...]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle, delimiter="\t"))


if __name__ == "__main__":
    raise SystemExit(main())
