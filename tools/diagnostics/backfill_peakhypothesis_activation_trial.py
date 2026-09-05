"""Run no-RAW FULL COHORT normal-peak activation trial counters."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.diagnostics import backfill_peakhypothesis_activation_trial


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        index = (
            backfill_peakhypothesis_activation_trial
            .build_activation_trial_index(
                current_full_cohort_artifact_dir=args.current_full_cohort_artifact_dir,
                normal_peak_decision_rows=(
                    backfill_peakhypothesis_activation_trial
                    .read_normal_peak_decision_rows(args.normal_peak_decisions_tsv)
                ),
                manual_verdict_rows=(
                    backfill_peakhypothesis_activation_trial
                    .read_manual_verdict_rows(args.full_cohort_manual_verdicts_tsv)
                    if args.full_cohort_manual_verdicts_tsv is not None
                    else ()
                ),
                source_run_id=args.source_run_id,
            )
        )
        outputs = (
            backfill_peakhypothesis_activation_trial
            .write_activation_trial_outputs(args.output_dir, index)
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"FULL COHORT activation trial TSV: {outputs.trial_tsv}")
    print(f"FULL COHORT activation trial summary JSON: {outputs.summary_json}")
    return 0 if index.summary.get("trial_status") == "pass" else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-full_cohort-artifact-dir", type=Path, required=True)
    parser.add_argument("--normal-peak-decisions-tsv", type=Path, required=True)
    parser.add_argument("--full_cohort-manual-verdicts-tsv", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-run-id", default="")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
