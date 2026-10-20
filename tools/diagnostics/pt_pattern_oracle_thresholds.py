"""Calibrate Pt pattern thresholds against a bounded manual oracle."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_oracle_thresholds import (
    run_threshold_calibration,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Join a Pt pattern oracle promotion packet with current review "
            "packet metrics and write no-RAW threshold calibration artifacts."
        )
    )
    parser.add_argument("--oracle-promotion-csv", type=Path, required=True)
    parser.add_argument(
        "--review-packet-csv",
        type=Path,
        action="append",
        required=True,
        help="Manual review packet CSV. Repeat for multi-packet calibration.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--calibration-id", required=True)
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.oracle_promotion_csv.is_file():
        parser.error(
            f"{args.oracle_promotion_csv}: oracle promotion CSV does not exist"
        )
    for review_packet_csv in args.review_packet_csv:
        if not review_packet_csv.is_file():
            parser.error(f"{review_packet_csv}: review packet CSV does not exist")
    if not args.calibration_id.strip():
        parser.error("--calibration-id must be non-empty")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)
    try:
        outputs = run_threshold_calibration(
            oracle_promotion_csv=args.oracle_promotion_csv.resolve(),
            review_packet_csvs=[path.resolve() for path in args.review_packet_csv],
            output_dir=args.output_dir.resolve(),
            calibration_id=args.calibration_id,
        )
    except (OSError, ValueError) as exc:
        print(f"pt_pattern_oracle_thresholds failed: {exc}", file=sys.stderr)
        return 2

    print(f"Pt pattern threshold candidates: {outputs['threshold_candidates_csv']}")
    print(f"Pt pattern threshold case audit: {outputs['case_audit_csv']}")
    print(f"Pt pattern threshold summary: {outputs['summary_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
