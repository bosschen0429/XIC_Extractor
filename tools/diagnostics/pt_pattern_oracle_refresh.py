"""Build a Pt pattern v2 manual-oracle refresh worklist."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_oracle_refresh import run_oracle_refresh


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare adjudicated Pt pattern oracle rows with current v2 manual "
            "review packets and write carryover, pending-review, and stale-row "
            "refresh artifacts."
        )
    )
    parser.add_argument("--adjudicated-oracle-csv", type=Path, required=True)
    parser.add_argument(
        "--review-packet-csv",
        type=Path,
        action="append",
        required=True,
        help="Current manual review packet CSV. Repeat for multi-packet refresh.",
    )
    parser.add_argument(
        "--machine-calls-csv",
        type=Path,
        action="append",
        default=[],
        help=(
            "Current sealed machine-call CSV. Its sibling "
            "pt_pattern_blind_overlay_summary.json must bind the complete "
            "overlay bundle. Repeat for multi-packet refresh."
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--refresh-packet-id", required=True)
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.adjudicated_oracle_csv.is_file():
        parser.error(
            f"{args.adjudicated_oracle_csv}: adjudicated oracle CSV does not exist"
        )
    for review_packet_csv in args.review_packet_csv:
        if not review_packet_csv.is_file():
            parser.error(f"{review_packet_csv}: review packet CSV does not exist")
    for machine_calls_csv in args.machine_calls_csv:
        if not machine_calls_csv.is_file():
            parser.error(f"{machine_calls_csv}: machine-call CSV does not exist")
    if not args.refresh_packet_id.strip():
        parser.error("--refresh-packet-id must be non-empty")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)
    try:
        outputs = run_oracle_refresh(
            adjudicated_oracle_csv=args.adjudicated_oracle_csv.resolve(),
            review_packet_csvs=[
                path.resolve() for path in args.review_packet_csv
            ],
            machine_call_csvs=[
                path.resolve() for path in args.machine_calls_csv
            ],
            output_dir=args.output_dir.resolve(),
            refresh_packet_id=args.refresh_packet_id,
        )
    except (OSError, ValueError) as exc:
        print(f"pt_pattern_oracle_refresh failed: {exc}", file=sys.stderr)
        return 2

    print(f"Pt pattern oracle refresh draft: {outputs['refresh_draft_csv']}")
    print(f"Pt pattern oracle pending review: {outputs['pending_review_csv']}")
    print(f"Pt pattern oracle stale rows: {outputs['stale_v1_rows_csv']}")
    print(f"Pt pattern oracle refresh summary: {outputs['summary_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
