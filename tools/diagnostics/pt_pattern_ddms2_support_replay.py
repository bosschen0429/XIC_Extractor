"""Run Fullscan-seeded DDMS2 support replay for Pt pattern Discovery."""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_ddms2_support_replay import (
    DEFAULT_MZ_TOLERANCE_PPM,
    DEFAULT_RT_WINDOW_SEC,
    DEFAULT_SEED_TRACE_RULE_CALL,
    run_ddms2_support_replay,
    write_ddms2_support_replay_outputs,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Fullscan-accepted Pt seeds against a DDMS2 local MS1 "
            "feature table. DDMS2 evidence is diagnostic support only."
        )
    )
    parser.add_argument("--fullscan-candidates-csv", type=Path, required=True)
    parser.add_argument("--fullscan-evidence-csv", type=Path, required=True)
    parser.add_argument("--ddms2-detected-features-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ddms2-sample-stem")
    parser.add_argument(
        "--mz-tolerance-ppm",
        type=float,
        default=DEFAULT_MZ_TOLERANCE_PPM,
    )
    parser.add_argument(
        "--rt-window-sec",
        type=float,
        default=DEFAULT_RT_WINDOW_SEC,
    )
    parser.add_argument(
        "--seed-trace-rule-call",
        default=DEFAULT_SEED_TRACE_RULE_CALL,
        help=(
            "Fullscan evidence trace_rule_call value to use as replay seeds "
            "(default: accept)."
        ),
    )
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    for attribute in (
        "fullscan_candidates_csv",
        "fullscan_evidence_csv",
        "ddms2_detected_features_csv",
    ):
        path = getattr(args, attribute)
        if not path.is_file():
            parser.error(f"{path}: input file does not exist")
    if not math.isfinite(args.mz_tolerance_ppm) or args.mz_tolerance_ppm <= 0:
        parser.error("--mz-tolerance-ppm must be > 0 and finite")
    if not math.isfinite(args.rt_window_sec) or args.rt_window_sec <= 0:
        parser.error("--rt-window-sec must be > 0 and finite")
    if not args.seed_trace_rule_call.strip():
        parser.error("--seed-trace-rule-call must be non-empty")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)
    try:
        result = run_ddms2_support_replay(
            fullscan_candidates_csv=args.fullscan_candidates_csv.resolve(),
            fullscan_evidence_csv=args.fullscan_evidence_csv.resolve(),
            ddms2_detected_features_csv=args.ddms2_detected_features_csv.resolve(),
            ddms2_sample_stem=args.ddms2_sample_stem,
            mz_tolerance_ppm=args.mz_tolerance_ppm,
            rt_window_sec=args.rt_window_sec,
            seed_trace_rule_call=args.seed_trace_rule_call.strip(),
        )
        outputs = write_ddms2_support_replay_outputs(
            result,
            output_dir=args.output_dir.resolve(),
        )
    except (OSError, ValueError) as exc:
        print(f"pt_pattern_ddms2_support_replay failed: {exc}", file=sys.stderr)
        return 2

    print(f"Fullscan-seeded DDMS2 support rows: {outputs['rows_csv']}")
    print(f"Fullscan-seeded DDMS2 support summary: {outputs['summary_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
