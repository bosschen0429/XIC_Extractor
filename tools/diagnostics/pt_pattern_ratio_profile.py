"""Profile Pt pattern candidate ratios from existing no-RAW artifacts."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_ratio_profile import (
    DEFAULT_PRIMARY_TRACE_RULE_CALL,
    run_ratio_profile,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read existing Pt pattern candidate/evidence CSV artifacts and "
            "write diagnostic-only ratio profile outputs without opening RAW."
        )
    )
    parser.add_argument("--candidates-csv", type=Path, required=True)
    parser.add_argument("--evidence-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument(
        "--primary-trace-rule-call",
        default=DEFAULT_PRIMARY_TRACE_RULE_CALL,
        help=(
            "Trace-rule value used to define the primary population. "
            "Default: %(default)s"
        ),
    )
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.candidates_csv.is_file():
        parser.error(f"{args.candidates_csv}: candidates CSV does not exist")
    if not args.evidence_csv.is_file():
        parser.error(f"{args.evidence_csv}: evidence CSV does not exist")
    if not args.profile_id.strip():
        parser.error("--profile-id must be non-empty")
    if not args.primary_trace_rule_call.strip():
        parser.error("--primary-trace-rule-call must be non-empty")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)
    try:
        outputs = run_ratio_profile(
            candidates_csv=args.candidates_csv.resolve(),
            evidence_csv=args.evidence_csv.resolve(),
            output_dir=args.output_dir.resolve(),
            profile_id=args.profile_id,
            primary_trace_rule_call=args.primary_trace_rule_call,
        )
    except (OSError, ValueError) as exc:
        print(f"pt_pattern_ratio_profile failed: {exc}", file=sys.stderr)
        return 2

    print(f"Primary ratio profile: {outputs['accepted_ratio_profile_csv']}")
    print(f"Primary ratio summary: {outputs['accepted_ratio_summary_csv']}")
    print(
        "Background ratio summary: "
        f"{outputs['rejected_ratio_background_summary_csv']}"
    )
    print(f"Static diagnostic HTML: {outputs['html']}")
    print(f"Summary JSON: {outputs['summary_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
