"""Build a review-only report for QuantMatrixVersion outputs.

This review report renderer renders review artifacts only. It does not run
RAW, recompute evidence, change ProductWriter defaults, update workbooks, or
touch GUI behavior.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xic_extractor.diagnostics.quant_matrix.report import (
    build_quant_matrix_review_report,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        outputs = build_quant_matrix_review_report(
            quant_matrix_tsv=args.quant_matrix_tsv,
            cell_provenance_tsv=args.cell_provenance_tsv,
            row_summary_tsv=args.row_summary_tsv,
            source_summary_tsv=args.source_summary_tsv,
            output_dir=args.output_dir,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quant-matrix-tsv", type=Path, required=True)
    parser.add_argument("--cell-provenance-tsv", type=Path, required=True)
    parser.add_argument("--row-summary-tsv", type=Path, required=True)
    parser.add_argument("--source-summary-tsv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
