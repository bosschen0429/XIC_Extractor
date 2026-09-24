"""Write shadow production projection rows for retained backfill cells."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.diagnostics.shadow_production_projection import (
    CELL_REQUIRED_COLUMNS,
    GATE_REQUIRED_COLUMNS,
    OVERLAY_REQUIRED_COLUMNS,
    REVIEW_REQUIRED_COLUMNS,
    ShadowProductionProjectionOutputs,
    run_shadow_production_projection,
)

__all__ = [
    "CELL_REQUIRED_COLUMNS",
    "GATE_REQUIRED_COLUMNS",
    "OVERLAY_REQUIRED_COLUMNS",
    "REVIEW_REQUIRED_COLUMNS",
    "ShadowProductionProjectionOutputs",
    "main",
    "run_shadow_production_projection",
]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        outputs = run_shadow_production_projection(
            alignment_review_tsv=args.alignment_review_tsv,
            alignment_cells_tsv=args.alignment_cells_tsv,
            retained_gate_tsv=args.retained_gate_tsv,
            output_dir=args.output_dir,
            alignment_matrix_tsv=args.alignment_matrix_tsv,
            alignment_matrix_identity_tsv=args.alignment_matrix_identity_tsv,
            overlay_batch_summary_tsvs=tuple(args.overlay_batch_summary_tsv or ()),
            source_run_id=args.source_run_id or "",
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"shadow production projection cells TSV: {outputs.tsv}")
    print(f"shadow production projection summary JSON: {outputs.json}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alignment-review-tsv", required=True, type=Path)
    parser.add_argument("--alignment-cells-tsv", required=True, type=Path)
    parser.add_argument("--retained-gate-tsv", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--alignment-matrix-tsv", type=Path)
    parser.add_argument("--alignment-matrix-identity-tsv", type=Path)
    parser.add_argument("--overlay-batch-summary-tsv", action="append", type=Path)
    parser.add_argument("--source-run-id")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
