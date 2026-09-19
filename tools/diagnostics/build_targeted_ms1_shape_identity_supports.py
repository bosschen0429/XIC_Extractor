"""Build targeted MS1 shape-identity support TSV from baseline long CSV + RAW."""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.diagnostics.targeted_ms1_shape_identity_support_builder import (
    DEFAULT_CANDIDATE_SEARCH_HALF_WINDOW_MIN,
    DEFAULT_MIN_REFERENCE_POINTS,
)
from xic_extractor.diagnostics.targeted_ms1_shape_identity_support_producer import (
    run_build_targeted_ms1_shape_identity_supports,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build diagnostic targeted_ms1_shape_identity support rows from "
            "a baseline xic_results_long.csv and RAW MS1 traces."
        ),
    )
    parser.add_argument("--long-csv", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--output-tsv", type=Path, required=True)
    parser.add_argument(
        "--target",
        dest="target_names",
        action="append",
        default=[],
        help="Optional target label filter. Repeat for multiple targets.",
    )
    parser.add_argument(
        "--min-reference-points",
        type=int,
        default=DEFAULT_MIN_REFERENCE_POINTS,
    )
    parser.add_argument(
        "--candidate-search-half-window-min",
        type=float,
        default=DEFAULT_CANDIDATE_SEARCH_HALF_WINDOW_MIN,
    )
    parser.add_argument("--min-own-max-similarity", type=float, default=0.80)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        outputs = run_build_targeted_ms1_shape_identity_supports(
            long_csv=args.long_csv,
            raw_dir=args.raw_dir,
            dll_dir=args.dll_dir,
            config_dir=args.config_dir,
            output_tsv=args.output_tsv,
            target_names=args.target_names,
            min_reference_points=args.min_reference_points,
            candidate_search_half_window_min=args.candidate_search_half_window_min,
            min_own_max_similarity=args.min_own_max_similarity,
        )
    except (OSError, ValueError, csv.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Evidence TSV: {outputs.evidence_tsv}")
    print(f"Candidate rows: {outputs.candidate_count}")
    print(f"Evidence rows: {outputs.evidence_row_count}")
    print(f"Trace requests: {outputs.trace_request_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
