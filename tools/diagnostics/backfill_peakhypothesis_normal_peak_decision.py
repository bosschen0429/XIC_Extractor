"""Build normal-peak PeakHypothesis backfill decision outputs."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.diagnostics import backfill_peakhypothesis_normal_peak_decision
from xic_extractor.diagnostics.diagnostic_io import read_tsv_required


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        index = (
            backfill_peakhypothesis_normal_peak_decision
            .build_normal_peak_decision_index(
                promotion_rows=read_tsv_required(
                    args.promotion_cells_tsv,
                    (
                        backfill_peakhypothesis_normal_peak_decision
                        .PROMOTION_REQUIRED_COLUMNS
                    ),
                ),
                full_cohort_acceptance_gate_rows=read_tsv_required(
                    args.full_cohort_acceptance_gate_tsv,
                    (
                        backfill_peakhypothesis_normal_peak_decision
                        .FULL_COHORT_SLICE_REQUIRED_COLUMNS
                    ),
                ),
                manual_verdict_rows=read_tsv_required(
                    args.full_cohort_manual_verdict_tsv,
                    (
                        backfill_peakhypothesis_normal_peak_decision
                        .MANUAL_VERDICT_REQUIRED_COLUMNS
                    ),
                ),
                source_run_id=args.source_run_id,
            )
        )
        outputs = (
            backfill_peakhypothesis_normal_peak_decision
            .write_normal_peak_decision_outputs(args.output_dir, index)
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Normal-peak decision TSV: {outputs.decisions_tsv}")
    print(f"Normal-peak decision summary JSON: {outputs.summary_json}")
    return 0 if not index.summary["blocked_count"] else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promotion-cells-tsv", type=Path, required=True)
    parser.add_argument("--full-cohort-acceptance-gate-tsv", type=Path, required=True)
    parser.add_argument("--full_cohort-manual-verdict-tsv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-run-id", default="")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
