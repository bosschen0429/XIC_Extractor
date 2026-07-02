"""Replay the calibrated Pt pattern diagnostic gate on review packets."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_diagnostic_gate import run_diagnostic_gate
from xic_extractor.discovery.pt_pattern_oracle_thresholds import (
    DEFAULT_APEX_DELTA_MAX_SEC,
    DEFAULT_OVERLAP_MIN,
    DEFAULT_PEARSON_MIN,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the calibrated Pt full-pattern diagnostic gate to one or "
            "more manual review packets, optionally replaying against a "
            "bounded oracle promotion packet."
        )
    )
    parser.add_argument(
        "--review-packet-csv",
        type=Path,
        action="append",
        required=True,
        help="Manual review packet CSV. Repeat for multi-packet replay.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--gate-id", required=True)
    parser.add_argument("--oracle-promotion-csv", type=Path)
    parser.add_argument(
        "--max-abs-apex-delta-sec",
        type=float,
        default=DEFAULT_APEX_DELTA_MAX_SEC,
    )
    parser.add_argument(
        "--min-pearson-r-to-base",
        type=float,
        default=DEFAULT_PEARSON_MIN,
    )
    parser.add_argument(
        "--min-active-overlap-fraction",
        type=float,
        default=DEFAULT_OVERLAP_MIN,
    )
    parser.add_argument(
        "--require-zero-fpfn",
        action="store_true",
        help="Return exit code 3 unless oracle replay has zero FP and zero FN.",
    )
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    for review_packet_csv in args.review_packet_csv:
        if not review_packet_csv.is_file():
            parser.error(f"{review_packet_csv}: review packet CSV does not exist")
    if args.oracle_promotion_csv and not args.oracle_promotion_csv.is_file():
        parser.error(
            f"{args.oracle_promotion_csv}: oracle promotion CSV does not exist"
        )
    if not args.gate_id.strip():
        parser.error("--gate-id must be non-empty")
    if args.max_abs_apex_delta_sec <= 0:
        parser.error("--max-abs-apex-delta-sec must be > 0")
    if not 0 <= args.min_pearson_r_to_base <= 1:
        parser.error("--min-pearson-r-to-base must be between 0 and 1")
    if not 0 <= args.min_active_overlap_fraction <= 1:
        parser.error("--min-active-overlap-fraction must be between 0 and 1")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)
    try:
        outputs = run_diagnostic_gate(
            review_packet_csvs=[path.resolve() for path in args.review_packet_csv],
            output_dir=args.output_dir.resolve(),
            gate_id=args.gate_id,
            oracle_promotion_csv=(
                args.oracle_promotion_csv.resolve()
                if args.oracle_promotion_csv
                else None
            ),
            apex_delta_max_sec=args.max_abs_apex_delta_sec,
            pearson_min=args.min_pearson_r_to_base,
            overlap_min=args.min_active_overlap_fraction,
        )
    except (OSError, ValueError) as exc:
        print(f"pt_pattern_diagnostic_gate failed: {exc}", file=sys.stderr)
        return 2

    print(f"Pt pattern diagnostic gate rows: {outputs['gate_rows_csv']}")
    print(f"Pt pattern diagnostic gate summary: {outputs['summary_json']}")
    if args.require_zero_fpfn:
        summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
        if summary["replay_status"] != "diagnostic_gate_replay_passed":
            print(
                "pt_pattern_diagnostic_gate failed zero-FP/FN replay: "
                f"{summary['replay_status']}",
                file=sys.stderr,
            )
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
