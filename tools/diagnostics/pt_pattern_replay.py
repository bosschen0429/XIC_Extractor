"""Explain Pt candidate assembly, conflicts, or DDMS2 support from saved CSVs."""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_candidate_assembly_replay import (
    run_candidate_assembly_replay,
)
from xic_extractor.discovery.pt_pattern_conflict_graph_replay import (
    GRAPH_RULE_VERSION,
    run_conflict_graph_replay,
)
from xic_extractor.discovery.pt_pattern_ddms2_support_replay import (
    DEFAULT_MZ_TOLERANCE_PPM,
    DEFAULT_RT_WINDOW_SEC,
    DEFAULT_SEED_TRACE_RULE_CALL,
    run_ddms2_support_replay,
    write_ddms2_support_replay_outputs,
)


def _input_file(value: str) -> Path:
    path = Path(value)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"{path}: input file does not exist")
    return path.resolve()


def _positive_finite(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be > 0 and finite")
    return number


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__ + " Diagnostic only; no RAW access or product writes."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    assembly = commands.add_parser(
        "assembly", help="Explain the first failed candidate-assembly condition."
    )
    assembly.add_argument("--detected-features-csv", type=_input_file, required=True)
    assembly.add_argument("--top102-diagnosis-csv", type=_input_file, required=True)
    assembly.add_argument("--known-diagnosis-csv", type=_input_file, required=True)
    conflicts = commands.add_parser(
        "conflicts",
        help="Inspect pairwise shared-feature conflicts in saved candidates.",
    )
    conflicts.add_argument("--candidates-csv", type=_input_file, required=True)
    conflicts.add_argument("--detected-features-csv", type=_input_file, required=True)
    conflicts.add_argument("--legacy-conflicts-csv", type=_input_file)
    conflicts.add_argument("--graph-rule-version", default=GRAPH_RULE_VERSION)
    support = commands.add_parser(
        "ddms2-support", help="Look for DDMS2 MS1 support near Fullscan-accepted seeds."
    )
    support.add_argument("--fullscan-candidates-csv", type=_input_file, required=True)
    support.add_argument("--fullscan-evidence-csv", type=_input_file, required=True)
    support.add_argument(
        "--ddms2-detected-features-csv", type=_input_file, required=True
    )
    support.add_argument("--ddms2-sample-stem")
    support.add_argument(
        "--mz-tolerance-ppm", type=_positive_finite, default=DEFAULT_MZ_TOLERANCE_PPM
    )
    support.add_argument(
        "--rt-window-sec", type=_positive_finite, default=DEFAULT_RT_WINDOW_SEC
    )
    support.add_argument("--seed-trace-rule-call", default=DEFAULT_SEED_TRACE_RULE_CALL)
    for command in (assembly, conflicts, support):
        command.add_argument("--output-dir", type=Path, required=True)
    for command in (assembly, conflicts):
        command.add_argument("--report-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "assembly" and not args.report_id.strip():
        parser.error("--report-id must be non-empty")
    if args.command == "ddms2-support" and not args.seed_trace_rule_call.strip():
        parser.error("--seed-trace-rule-call must be non-empty")
    output_dir = args.output_dir.resolve()
    try:
        if args.command == "assembly":
            outputs = run_candidate_assembly_replay(
                detected_features_csv=args.detected_features_csv,
                top102_diagnosis_csv=args.top102_diagnosis_csv,
                known_diagnosis_csv=args.known_diagnosis_csv,
                output_dir=output_dir,
                report_id=args.report_id,
            )
        elif args.command == "conflicts":
            outputs = run_conflict_graph_replay(
                candidates_csv=args.candidates_csv,
                detected_features_csv=args.detected_features_csv,
                legacy_conflicts_csv=args.legacy_conflicts_csv,
                output_dir=output_dir,
                report_id=args.report_id,
                graph_rule_version=args.graph_rule_version,
            )
        else:
            result = run_ddms2_support_replay(
                fullscan_candidates_csv=args.fullscan_candidates_csv,
                fullscan_evidence_csv=args.fullscan_evidence_csv,
                ddms2_detected_features_csv=args.ddms2_detected_features_csv,
                ddms2_sample_stem=args.ddms2_sample_stem,
                mz_tolerance_ppm=args.mz_tolerance_ppm,
                rt_window_sec=args.rt_window_sec,
                seed_trace_rule_call=args.seed_trace_rule_call.strip(),
            )
            outputs = write_ddms2_support_replay_outputs(result, output_dir=output_dir)
    except (OSError, ValueError) as exc:
        print(f"Pt {args.command} replay failed: {exc}", file=sys.stderr)
        return 2
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
