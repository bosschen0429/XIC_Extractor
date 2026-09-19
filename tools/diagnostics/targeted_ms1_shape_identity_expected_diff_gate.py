"""Gate targeted MS1 shape identity expected-diff artifacts."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.diagnostics.targeted_ms1_shape_identity_expected_diff import (
    evaluate_limited_targeted_ms1_shape_identity_expected_diff_paths,
    write_expected_diff_gate_summary,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    summary = evaluate_limited_targeted_ms1_shape_identity_expected_diff_paths(
        expected_diff_summary_tsv=args.expected_diff_summary_tsv,
        matrix_diff_summary_tsv=args.matrix_diff_summary_tsv,
        support_tsv=args.support_tsv,
        expected_long_row_count=args.expected_long_row_count,
        expected_matrix_cell_count=args.expected_matrix_cell_count,
    )
    if args.summary_tsv is not None:
        args.summary_tsv.parent.mkdir(parents=True, exist_ok=True)
        write_expected_diff_gate_summary(args.summary_tsv, summary)
        print(f"Expected-diff gate summary TSV: {args.summary_tsv}")
    print("Expected-diff gate status: pass")
    print(f"Long changed rows: {summary.long_changed_rows}")
    print(f"Matrix changed cells: {summary.matrix_changed_cells}")
    if summary.support_tsv_supported_rows is not None:
        print(f"Support TSV supported rows: {summary.support_tsv_supported_rows}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-diff-summary-tsv", type=Path, required=True)
    parser.add_argument("--matrix-diff-summary-tsv", type=Path, required=True)
    parser.add_argument(
        "--support-tsv",
        type=Path,
        required=True,
        help=(
            "Required targeted_ms1_shape_identity support TSV. Supported "
            "sample/target keys must exactly match the long-row diff."
        ),
    )
    parser.add_argument("--summary-tsv", type=Path)
    parser.add_argument("--expected-long-row-count", type=int)
    parser.add_argument("--expected-matrix-cell-count", type=int)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
