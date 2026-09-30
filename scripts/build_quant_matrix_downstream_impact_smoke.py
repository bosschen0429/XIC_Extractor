"""Build/check a no-RAW QuantMatrix downstream-impact smoke artifact."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xic_extractor.diagnostics.quant_matrix.downstream_impact import (
    DOWNSTREAM_IMPACT_BUNDLE_KINDS,
    build_quant_matrix_downstream_impact_smoke,
    validate_quant_matrix_downstream_impact_smoke,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.check_only:
        summary_json = args.summary_json or (
            args.output_dir / "quant_matrix_downstream_impact_smoke.json"
        )
        problems = validate_quant_matrix_downstream_impact_smoke(summary_json)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(f"downstream_impact_summary_json: {summary_json}")
        print("downstream_impact_status: pass")
        return 0
    try:
        for field in (
            "quant_matrix_tsv",
            "cell_provenance_tsv",
            "row_summary_tsv",
            "downstream_scope",
            "bundle_kind",
        ):
            if getattr(args, field) in (None, ""):
                raise ValueError(f"--{field.replace('_', '-')} is required")
        outputs = build_quant_matrix_downstream_impact_smoke(
            quant_matrix_tsv=args.quant_matrix_tsv,
            cell_provenance_tsv=args.cell_provenance_tsv,
            row_summary_tsv=args.row_summary_tsv,
            output_dir=args.output_dir,
            downstream_scope=args.downstream_scope,
            bundle_kind=args.bundle_kind,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quant-matrix-tsv", type=Path)
    parser.add_argument("--cell-provenance-tsv", type=Path)
    parser.add_argument("--row-summary-tsv", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--downstream-scope")
    parser.add_argument(
        "--bundle-kind",
        choices=DOWNSTREAM_IMPACT_BUNDLE_KINDS,
    )
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--summary-json", type=Path)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
