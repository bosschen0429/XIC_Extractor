"""Synthesize Pt pattern ratio, overlap, seed, and assembly diagnostics."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_hypothesis_synthesis import (
    run_hypothesis_synthesis,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read existing Pt diagnostic artifacts and write a no-RAW "
            "hypothesis synthesis without changing detector or matrix behavior."
        )
    )
    parser.add_argument("--ratio-summary-csv", type=Path, required=True)
    parser.add_argument("--background-ratio-summary-csv", type=Path)
    parser.add_argument("--interference-summary-json", type=Path)
    parser.add_argument("--plus1-seed-summary-csv", type=Path)
    parser.add_argument("--inclusion-summary-csv", type=Path)
    parser.add_argument("--top102-diagnosis-csv", type=Path)
    parser.add_argument("--known-diagnosis-csv", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-id", required=True)
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    required = {"ratio-summary-csv": args.ratio_summary_csv}
    optional = {
        "background-ratio-summary-csv": args.background_ratio_summary_csv,
        "interference-summary-json": args.interference_summary_json,
        "plus1-seed-summary-csv": args.plus1_seed_summary_csv,
        "inclusion-summary-csv": args.inclusion_summary_csv,
        "top102-diagnosis-csv": args.top102_diagnosis_csv,
        "known-diagnosis-csv": args.known_diagnosis_csv,
    }
    for label, path in {**required, **optional}.items():
        if path is not None and not path.is_file():
            parser.error(f"--{label} does not exist: {path}")
    if not args.report_id.strip():
        parser.error("--report-id must be non-empty")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)
    try:
        outputs = run_hypothesis_synthesis(
            ratio_summary_csv=args.ratio_summary_csv.resolve(),
            background_ratio_summary_csv=_resolve_optional(
                args.background_ratio_summary_csv
            ),
            interference_summary_json=_resolve_optional(args.interference_summary_json),
            plus1_seed_summary_csv=_resolve_optional(args.plus1_seed_summary_csv),
            inclusion_summary_csv=_resolve_optional(args.inclusion_summary_csv),
            top102_diagnosis_csv=_resolve_optional(args.top102_diagnosis_csv),
            known_diagnosis_csv=_resolve_optional(args.known_diagnosis_csv),
            output_dir=args.output_dir.resolve(),
            report_id=args.report_id,
        )
    except (OSError, ValueError) as exc:
        print(f"pt_pattern_hypothesis_synthesis failed: {exc}", file=sys.stderr)
        return 2

    print(f"Assembly layer summary: {outputs['assembly_layer_summary_csv']}")
    print(f"Known miss examples: {outputs['known_miss_examples_csv']}")
    print(f"Synthesis markdown: {outputs['markdown']}")
    print(f"Synthesis HTML: {outputs['html']}")
    print(f"Synthesis summary: {outputs['summary_json']}")
    return 0


def _resolve_optional(path: Path | None) -> Path | None:
    return path.resolve() if path is not None else None


if __name__ == "__main__":
    raise SystemExit(main())
