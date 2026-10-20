"""Build a bounded manual-review packet for Pt pattern candidates."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_review_packet import run_review_packet


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Join Pt pattern candidates with trace diagnosis outputs and write "
            "a bounded manual-review packet for accepted/rejected oracle work."
        )
    )
    parser.add_argument("--candidates-csv", type=Path, required=True)
    parser.add_argument("--trace-summary-csv", type=Path, required=True)
    parser.add_argument("--trace-metrics-csv", type=Path, required=True)
    parser.add_argument(
        "--trace-manifest-json",
        type=Path,
        help=(
            "Optional explicit trace bundle manifest. Defaults to the canonical "
            "sidecar next to --trace-summary-csv."
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--packet-label", required=True)
    parser.add_argument("--source-label", required=True)
    parser.add_argument("--max-per-status", type=int, default=5)
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.candidates_csv.is_file():
        parser.error(f"{args.candidates_csv}: candidates CSV does not exist")
    if not args.trace_summary_csv.is_file():
        parser.error(f"{args.trace_summary_csv}: trace summary CSV does not exist")
    if not args.trace_metrics_csv.is_file():
        parser.error(f"{args.trace_metrics_csv}: trace metrics CSV does not exist")
    if args.trace_manifest_json is not None and not args.trace_manifest_json.is_file():
        parser.error(f"{args.trace_manifest_json}: trace manifest does not exist")
    if args.max_per_status <= 0:
        parser.error("--max-per-status must be > 0")
    if not args.packet_label.strip():
        parser.error("--packet-label must be non-empty")
    if not args.source_label.strip():
        parser.error("--source-label must be non-empty")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)
    try:
        outputs = run_review_packet(
            candidates_csv=args.candidates_csv.resolve(),
            trace_summary_csv=args.trace_summary_csv.resolve(),
            trace_metrics_csv=args.trace_metrics_csv.resolve(),
            output_dir=args.output_dir.resolve(),
            packet_label=args.packet_label,
            source_label=args.source_label,
            max_per_status=args.max_per_status,
            trace_manifest_json=(
                args.trace_manifest_json.resolve()
                if args.trace_manifest_json is not None
                else None
            ),
        )
    except (OSError, ValueError) as exc:
        print(f"pt_pattern_review_packet failed: {exc}", file=sys.stderr)
        return 2

    print(f"Pt pattern manual review packet CSV: {outputs['packet_csv']}")
    print(f"Pt pattern manual review packet JSON: {outputs['sidecar_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
