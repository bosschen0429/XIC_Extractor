"""Validate exact matrix diff after backfill PeakHypothesis activation."""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.diagnostics import backfill_peakhypothesis_activation_acceptance


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        index = (
            backfill_peakhypothesis_activation_acceptance.build_activation_acceptance(
                promotion_rows=_read_tsv(args.promotion_cells_tsv),
                activation_decision_rows=_read_tsv(args.activation_decisions_tsv),
                preflight_rows=_read_tsv(args.activation_matrix_preflight_tsv),
                application_summary_rows=_read_tsv(
                    args.activation_application_summary_tsv,
                ),
                value_delta_rows=_read_tsv(args.activation_value_delta_tsv),
                input_matrix_rows=_read_tsv(args.input_alignment_matrix_tsv),
                input_identity_rows=_read_tsv(
                    args.input_alignment_matrix_identity_tsv,
                ),
                output_matrix_rows=_read_tsv(args.output_alignment_matrix_tsv),
                output_identity_rows=_read_tsv(
                    args.output_alignment_matrix_identity_tsv,
                ),
                source_run_id=args.source_run_id,
                validation_scope=args.validation_scope,
            )
        )
        outputs = (
            backfill_peakhypothesis_activation_acceptance.write_activation_acceptance_outputs(
                args.output_dir,
                index,
            )
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Activation acceptance TSV: {outputs.acceptance_tsv}")
    print(f"Activation matrix diff TSV: {outputs.matrix_diff_tsv}")
    print(f"Activation acceptance summary JSON: {outputs.summary_json}")
    return 0 if index.acceptance_row.get("acceptance_status") == "pass" else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promotion-cells-tsv", type=Path, required=True)
    parser.add_argument("--activation-decisions-tsv", type=Path, required=True)
    parser.add_argument("--activation-matrix-preflight-tsv", type=Path, required=True)
    parser.add_argument(
        "--activation-application-summary-tsv",
        type=Path,
        required=True,
    )
    parser.add_argument("--activation-value-delta-tsv", type=Path, required=True)
    parser.add_argument("--input-alignment-matrix-tsv", type=Path, required=True)
    parser.add_argument(
        "--input-alignment-matrix-identity-tsv",
        type=Path,
        required=True,
    )
    parser.add_argument("--output-alignment-matrix-tsv", type=Path, required=True)
    parser.add_argument(
        "--output-alignment-matrix-identity-tsv",
        type=Path,
        required=True,
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-run-id", default="")
    parser.add_argument(
        "--validation-scope",
        default="fixture_cohort_current_writer_matrix_diff",
        help=(
            "Diagnostic scope label written into acceptance outputs, for example "
            "full_cohort_current_writer_matrix_diff."
        ),
    )
    return parser.parse_args(argv)


def _read_tsv(path: Path) -> tuple[dict[str, str], ...]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle, delimiter="\t"))


if __name__ == "__main__":
    raise SystemExit(main())
