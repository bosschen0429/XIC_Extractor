"""Replay Pt candidate assembly against a detected feature table."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_candidate_assembly_replay import (
    run_candidate_assembly_replay,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Classify the first assembly break for Pt list rows whose four "
            "list-relative m/z pools exist, without opening RAW files."
        )
    )
    parser.add_argument("--detected-features-csv", type=Path, required=True)
    parser.add_argument("--top102-diagnosis-csv", type=Path, required=True)
    parser.add_argument("--known-diagnosis-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    for label, path in (
        ("detected-features-csv", args.detected_features_csv),
        ("top102-diagnosis-csv", args.top102_diagnosis_csv),
        ("known-diagnosis-csv", args.known_diagnosis_csv),
    ):
        if not path.is_file():
            parser.error(f"--{label} does not exist: {path}")
    if not args.report_id.strip():
        parser.error("--report-id must be non-empty")
    try:
        outputs = run_candidate_assembly_replay(
            detected_features_csv=args.detected_features_csv.resolve(),
            top102_diagnosis_csv=args.top102_diagnosis_csv.resolve(),
            known_diagnosis_csv=args.known_diagnosis_csv.resolve(),
            output_dir=args.output_dir.resolve(),
            report_id=args.report_id,
        )
    except (OSError, ValueError) as exc:
        print(f"pt_pattern_candidate_assembly_replay failed: {exc}", file=sys.stderr)
        return 2

    print(f"Replay CSV: {outputs['replay_csv']}")
    print(f"Replay summary: {outputs['summary_json']}")
    print(f"Replay HTML: {outputs['html']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
