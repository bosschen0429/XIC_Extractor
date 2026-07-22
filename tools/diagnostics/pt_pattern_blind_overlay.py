"""Build blind Pt pattern MS1/XIC overlay review HTML."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_blind_overlay import run_blind_overlay


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a blind MS1/XIC overlay gallery from a Pt pattern review "
            "packet and trace-points CSV. Machine calls are written separately "
            "and are not embedded in the HTML."
        )
    )
    parser.add_argument("--review-packet-csv", type=Path, required=True)
    parser.add_argument(
        "--review-packet-manifest-json",
        type=Path,
        help="Optional explicit review packet manifest; defaults to its sibling.",
    )
    parser.add_argument("--trace-points-csv", type=Path, required=True)
    parser.add_argument(
        "--trace-manifest-json",
        type=Path,
        help="Optional explicit manifest for the primary trace-points CSV.",
    )
    parser.add_argument(
        "--context-trace-points-csv",
        type=Path,
        help=(
            "Optional full-window trace points. When provided, the primary "
            "trace-points CSV is treated as the anchor-local hypothesis."
        ),
    )
    parser.add_argument(
        "--context-trace-manifest-json",
        type=Path,
        help="Optional explicit manifest for --context-trace-points-csv.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--title", default="Pt Pattern Blind Overlay Review")
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.review_packet_csv.is_file():
        parser.error(f"{args.review_packet_csv}: review packet CSV does not exist")
    if not args.trace_points_csv.is_file():
        parser.error(f"{args.trace_points_csv}: trace-points CSV does not exist")
    for label, path in (
        ("review packet manifest", args.review_packet_manifest_json),
        ("trace manifest", args.trace_manifest_json),
        ("context trace manifest", args.context_trace_manifest_json),
    ):
        if path is not None and not path.is_file():
            parser.error(f"{path}: {label} does not exist")
    if (
        args.context_trace_points_csv is not None
        and not args.context_trace_points_csv.is_file()
    ):
        parser.error(
            f"{args.context_trace_points_csv}: context trace-points CSV does not exist"
        )
    if not args.title.strip():
        parser.error("--title must be non-empty")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)
    try:
        outputs = run_blind_overlay(
            review_packet_csv=args.review_packet_csv.resolve(),
            trace_points_csv=args.trace_points_csv.resolve(),
            context_trace_points_csv=(
                args.context_trace_points_csv.resolve()
                if args.context_trace_points_csv is not None
                else None
            ),
            review_packet_manifest_json=(
                args.review_packet_manifest_json.resolve()
                if args.review_packet_manifest_json is not None
                else None
            ),
            trace_manifest_json=(
                args.trace_manifest_json.resolve()
                if args.trace_manifest_json is not None
                else None
            ),
            context_trace_manifest_json=(
                args.context_trace_manifest_json.resolve()
                if args.context_trace_manifest_json is not None
                else None
            ),
            output_dir=args.output_dir.resolve(),
            title=args.title,
        )
    except (OSError, ValueError) as exc:
        print(f"pt_pattern_blind_overlay failed: {exc}", file=sys.stderr)
        return 2

    print(f"Pt pattern blind overlay HTML: {outputs['gallery_html']}")
    print(f"Pt pattern sealed machine calls: {outputs['machine_calls_csv']}")
    print(f"Pt pattern blind overlay summary: {outputs['summary_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
