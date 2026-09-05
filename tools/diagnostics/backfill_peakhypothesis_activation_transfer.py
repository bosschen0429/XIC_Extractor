"""Run normal-peak FULL COHORT activation transfer bridge."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.diagnostics import (
    backfill_peakhypothesis_activation_transfer,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        index = (
            backfill_peakhypothesis_activation_transfer
            .build_activation_transfer_index(
                normal_peak_decision_rows=(
                    backfill_peakhypothesis_activation_transfer
                    .read_normal_peak_decision_rows(args.normal_peak_decisions_tsv)
                ),
                activation_trial_rows=(
                    backfill_peakhypothesis_activation_transfer
                    .read_activation_trial_rows(args.activation_trial_tsv)
                ),
                source_artifact_sha256=(
                    backfill_peakhypothesis_activation_transfer
                    .input_bundle_sha256(
                        args.normal_peak_decisions_tsv,
                        args.activation_trial_tsv,
                    )
                ),
                source_run_id=args.source_run_id,
            )
        )
        outputs = (
            backfill_peakhypothesis_activation_transfer
            .write_activation_transfer_outputs(args.output_dir, index)
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"FULL COHORT transfer promotion cells TSV: {outputs.promotion_cells_tsv}")
    print(f"FULL COHORT activation transfer TSV: {outputs.transfer_tsv}")
    print(f"FULL COHORT activation transfer summary JSON: {outputs.summary_json}")
    return 0 if index.summary.get("transfer_status") == "pass" else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normal-peak-decisions-tsv", type=Path, required=True)
    parser.add_argument("--activation-trial-tsv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-run-id", default="")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
