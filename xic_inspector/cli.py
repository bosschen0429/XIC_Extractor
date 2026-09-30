"""Command-line interface for XIC Inspector."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path
from typing import Sequence

from xic_extractor.diagnostics.cid_nl_explanation import REPORT_FILENAME
from xic_inspector.api import (
    InspectorCancelled,
    InspectorError,
    SelectionRequest,
    generate_report,
)

DEFAULT_OUTPUT_NAME = "xic_inspector.html"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xic-inspector",
        description=(
            "XIC Inspector — Standalone review and offline workbench "
            "for XIC Extractor."
        ),
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        help=(
            "Feature evidence SQLite report, versioned curve-bundle JSON, or an "
            f"alignment output folder containing {REPORT_FILENAME}."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Same as the positional source.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Target path for the single-file HTML workbench "
            f"(default: {DEFAULT_OUTPUT_NAME} beside the source)."
        ),
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the generated report in the default browser.",
    )
    parser.add_argument(
        "--strategy",
        choices=["sample", "attention", "query"],
        default="sample",
        help="Selection strategy: sample (default), attention, or query.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Maximum items to include in report (1 to 50, default 30).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Deterministic sampling seed for sample strategy (default 0).",
    )
    parser.add_argument(
        "--sample-id",
        action="append",
        dest="sample_ids",
        default=[],
        help="Filter by canonical sample identity ID (repeatable).",
    )
    parser.add_argument(
        "--candidate-id",
        action="append",
        dest="candidate_ids",
        default=[],
        help="Filter by candidate ID (query strategy only, repeatable).",
    )
    parser.add_argument(
        "--group-id",
        action="append",
        dest="group_ids",
        default=[],
        help="Filter by group hypothesis ID (query strategy only, repeatable).",
    )
    parser.add_argument(
        "--mz-min",
        type=float,
        default=None,
        help="Minimum precursor m/z inclusive bound (query strategy only).",
    )
    parser.add_argument(
        "--mz-max",
        type=float,
        default=None,
        help="Maximum precursor m/z inclusive bound (query strategy only).",
    )
    parser.add_argument(
        "--rt-min",
        type=float,
        default=None,
        help="Minimum RT apex minutes inclusive bound (query strategy only).",
    )
    parser.add_argument(
        "--rt-max",
        type=float,
        default=None,
        help="Maximum RT apex minutes inclusive bound (query strategy only).",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Pagination offset for query strategy (default 0).",
    )
    return parser


def _resolve_source(path: Path) -> Path:
    """Return the evidence file for a file path or an alignment output folder."""
    if not path.is_dir():
        return path
    for candidate in (path / REPORT_FILENAME, path / "alignment" / REPORT_FILENAME):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"No {REPORT_FILENAME} in {path} or its alignment folder")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.source is not None and args.report is not None:
            parser.error("give the source either positionally or with --report")
        if args.source is None and args.report is None:
            parser.error("a source report or alignment output folder is required")
    except SystemExit as exc:
        return 2 if exc.code != 0 else 0

    try:
        report = _resolve_source(args.source or args.report)
    except FileNotFoundError as exc:
        sys.stderr.write(f"Error [code 3]: Source unavailable: {exc}\n")
        return 3
    output = args.output or report.parent / DEFAULT_OUTPUT_NAME

    try:
        selection = SelectionRequest(
            strategy=args.strategy,
            limit=args.limit,
            seed=args.seed,
            sample_ids=tuple(args.sample_ids),
            candidate_ids=tuple(args.candidate_ids),
            group_ids=tuple(args.group_ids),
            mz_min=args.mz_min,
            mz_max=args.mz_max,
            rt_min=args.rt_min,
            rt_max=args.rt_max,
            offset=args.offset,
        )

        result = generate_report(
            report=report,
            selection=selection,
            output=output,
        )

        sys.stdout.write(
            f"Successfully generated XIC Inspector report: {result.output_path}\n"
            f"Capability: {result.evidence_capability}\n"
            f"Run Total: {result.run_total}, Eligible: {result.eligible_count}, "
            f"Matched: {result.matched_count}, Selected: {result.selected_count}\n"
        )
        if not args.no_open:
            webbrowser.open(Path(result.output_path).resolve().as_uri())
        return 0

    except InspectorCancelled as exc:
        sys.stderr.write(f"Cancelled [code {exc.code}]: {exc.message}\n")
        return exc.code

    except InspectorError as exc:
        sys.stderr.write(f"Error [code {exc.code}]: {exc.message}\n")
        return exc.code

    except Exception as exc:
        sys.stderr.write(f"Unexpected error [code 1]: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
