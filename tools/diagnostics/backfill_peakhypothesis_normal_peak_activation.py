"""Run normal-peak backfill activation end to end."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.diagnostics import (
    backfill_peakhypothesis_normal_peak_activation,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        outputs = (
            backfill_peakhypothesis_normal_peak_activation
            .run_normal_peak_activation(
                output_dir=args.output_dir,
                alignment_matrix_tsv=args.alignment_matrix_tsv,
                alignment_matrix_identity_tsv=args.alignment_matrix_identity_tsv,
                alignment_review_tsv=args.alignment_review_tsv,
                promotion_cells_tsv=args.promotion_cells_tsv,
                full_cohort_acceptance_gate_tsv=args.full_cohort_acceptance_gate_tsv,
                full_cohort_manual_verdict_tsv=args.full_cohort_manual_verdict_tsv,
                machine_shape_evidence_tsv=args.machine_shape_evidence_tsv,
                normal_peak_decisions_tsv=args.normal_peak_decisions_tsv,
                activation_trial_tsv=args.activation_trial_tsv,
                current_full_cohort_artifact_dir=args.current_full_cohort_artifact_dir,
                source_run_id=args.source_run_id,
                validation_scope=args.validation_scope,
            )
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Normal-peak activation summary TSV: {outputs.summary_tsv}")
    print(f"Normal-peak activation summary JSON: {outputs.summary_json}")
    if outputs.activated_alignment_matrix_tsv is not None:
        print(
            "Activated alignment matrix TSV: "
            f"{outputs.activated_alignment_matrix_tsv}",
        )
    if outputs.activation_acceptance_tsv is not None:
        print(f"Activation acceptance TSV: {outputs.activation_acceptance_tsv}")
    return 0 if outputs.status == "pass" else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promotion-cells-tsv", type=Path)
    parser.add_argument("--full-cohort-acceptance-gate-tsv", type=Path)
    parser.add_argument("--full_cohort-manual-verdict-tsv", type=Path)
    parser.add_argument("--machine-shape-evidence-tsv", type=Path)
    parser.add_argument("--normal-peak-decisions-tsv", type=Path)
    parser.add_argument("--activation-trial-tsv", type=Path)
    parser.add_argument("--current-full_cohort-artifact-dir", type=Path)
    parser.add_argument("--alignment-matrix-tsv", type=Path, required=True)
    parser.add_argument(
        "--alignment-matrix-identity-tsv",
        type=Path,
        required=True,
    )
    parser.add_argument("--alignment-review-tsv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-run-id", default="")
    parser.add_argument(
        "--validation-scope",
        default="full_cohort_current_writer_matrix_diff",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
