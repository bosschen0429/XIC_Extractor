"""Check QuantMatrixVersion promotion readiness without running scorer or RAW.

This promotion readiness evaluation separates contract correctness from
scientific confidence. It reads existing QuantMatrixVersion/review artifacts,
writes readiness sidecars, and does not mutate ProductWriter, workbook, GUI,
selected peak, selected area, or counted-detection behavior.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xic_extractor.diagnostics.quant_matrix.promotion import (
    evaluate_quant_matrix_promotion_readiness,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        outputs = evaluate_quant_matrix_promotion_readiness(
            expected_diff_summary_tsv=args.expected_diff_summary_tsv,
            cell_provenance_tsv=args.cell_provenance_tsv,
            row_summary_tsv=args.row_summary_tsv,
            review_summary_json=args.review_summary_json,
            validation_evidence_json=args.validation_evidence_json,
            output_dir=args.output_dir,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    print(f"readiness_label: {summary['readiness_label']}")
    print(f"production_ready: {summary['production_ready']}")
    for label, path in outputs.items():
        print(f"{label}: {path}")
    if args.require_production_ready and not summary["production_ready"]:
        return 1
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-diff-summary-tsv", type=Path, required=True)
    parser.add_argument("--cell-provenance-tsv", type=Path, required=True)
    parser.add_argument("--row-summary-tsv", type=Path, required=True)
    parser.add_argument("--review-summary-json", type=Path, required=True)
    parser.add_argument("--validation-evidence-json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--require-production-ready",
        action="store_true",
        help="Exit 1 unless both contract correctness and science evidence pass.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
