"""Promote adjudicated Pt pattern blind-review rows into an oracle packet."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_oracle_promotion import (
    run_oracle_promotion,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate adjudicated Pt pattern review rows against their manual "
            "review packets and write a bounded oracle promotion packet."
        )
    )
    parser.add_argument("--adjudicated-oracle-csv", type=Path, required=True)
    parser.add_argument(
        "--review-packet-csv",
        type=Path,
        action="append",
        required=True,
        help="Manual review packet CSV. Repeat for multi-packet promotions.",
    )
    parser.add_argument(
        "--blind-overlay-summary-json",
        type=Path,
        action="append",
        required=True,
        help=(
            "Blind-overlay summary that binds the reviewed packet, traces, "
            "gallery, and sealed calls. Repeat for multi-packet promotions."
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--oracle-packet-id", required=True)
    parser.add_argument("--adjudication-summary-json", type=Path)
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.adjudicated_oracle_csv.is_file():
        parser.error(
            f"{args.adjudicated_oracle_csv}: adjudicated oracle CSV does not exist"
        )
    for review_packet_csv in args.review_packet_csv:
        if not review_packet_csv.is_file():
            parser.error(f"{review_packet_csv}: review packet CSV does not exist")
    for overlay_summary_json in args.blind_overlay_summary_json:
        if not overlay_summary_json.is_file():
            parser.error(
                f"{overlay_summary_json}: blind-overlay summary JSON does not exist"
            )
    if args.adjudication_summary_json and not args.adjudication_summary_json.is_file():
        parser.error(
            f"{args.adjudication_summary_json}: "
            "adjudication summary JSON does not exist"
        )
    if not args.oracle_packet_id.strip():
        parser.error("--oracle-packet-id must be non-empty")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)
    try:
        outputs = run_oracle_promotion(
            adjudicated_oracle_csv=args.adjudicated_oracle_csv.resolve(),
            review_packet_csvs=[
                path.resolve() for path in args.review_packet_csv
            ],
            blind_overlay_summary_jsons=[
                path.resolve() for path in args.blind_overlay_summary_json
            ],
            output_dir=args.output_dir.resolve(),
            oracle_packet_id=args.oracle_packet_id,
            adjudication_summary_json=(
                args.adjudication_summary_json.resolve()
                if args.adjudication_summary_json
                else None
            ),
        )
    except (OSError, ValueError) as exc:
        print(f"pt_pattern_oracle_promotion failed: {exc}", file=sys.stderr)
        return 2

    print(f"Pt pattern oracle promotion CSV: {outputs['promotion_packet_csv']}")
    print(f"Pt pattern oracle promotion summary: {outputs['summary_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
