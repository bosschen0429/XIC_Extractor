"""Query Discovery and replay grouping snapshots without RAW.

Save replay inputs with `scripts.run_alignment --stop-after grouping
--save-grouping-inputs`.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path
from time import perf_counter
from typing import Any

from xic_extractor.alignment.grouping_experiment import (
    calibrate_grouping,
    replay_grouping,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import RtCalibrationPolicy
from xic_extractor.diagnostics.grouping_experiment_io import (
    query_discovery,
    read_grouping_inputs,
    read_rt_calibration,
    write_grouping_result,
    write_json,
    write_membership_comparison,
    write_rt_calibration,
    write_table,
)


def _range(value: str) -> tuple[float, float]:
    try:
        lower, upper = map(float, value.split(":"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use LOWER:UPPER, e.g. 242.12:242.13") from exc
    if not math.isfinite(lower + upper) or not 0 < lower <= upper:
        raise argparse.ArgumentTypeError(
            "m/z bounds must be positive, finite and ordered"
        )
    return lower, upper


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    query = commands.add_parser(
        "query", help="Query measured candidates across every sample; no RAW"
    )
    replay = commands.add_parser(
        "replay", help="Regroup compact observations without RAW or Backfill"
    )
    compare = commands.add_parser(
        "compare",
        help="Compare saved memberships from the same observations without replay",
    )
    compare.add_argument(
        "--before", type=Path, required=True, help="Baseline members.tsv"
    )
    compare.add_argument(
        "--after", type=Path, required=True, help="Variant members.tsv"
    )
    for command in (query, compare):
        command.add_argument("--output-dir", required=True, type=Path)
    query.add_argument("--discovery-batch-index", required=True, type=Path)
    query.add_argument("--mz-range", action="append", type=_range, required=True)
    _add_replay_arguments(replay)
    return parser


def _add_replay_arguments(replay: argparse.ArgumentParser) -> None:
    replay.add_argument(
        "--inputs",
        required=True,
        type=Path,
        help="Snapshot saved with --stop-after grouping --save-grouping-inputs",
    )
    replay.add_argument("--output-dir", required=True, type=Path)
    replay.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="FIELD=JSON",
        help="Override an AlignmentConfig field (Backfill does not affect grouping)",
    )
    calibration = replay.add_mutually_exclusive_group()
    calibration.add_argument(
        "--calibrate",
        action="store_true",
        help="Fit the shared RT model from directly observed baseline groups",
    )
    calibration.add_argument(
        "--rt-calibration", type=Path, help="Use an existing sample_rt_calibration JSON"
    )
    replay.add_argument("--loess-fraction", type=float, default=0.3)
    replay.add_argument(
        "--compare-to",
        type=Path,
        help="Previous replay output dir of the same snapshot (e.g. another commit)",
    )


def regroup_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        usage="%(prog)s regroup --inputs SNAPSHOT --output-dir DIR [options]",
        description="Recompute initial scalar CID-NL groups from frozen observations "
        "without Discovery, RAW, DLL, Backfill or Matrix publication.",
    )
    _add_replay_arguments(parser)
    parser.set_defaults(command="replay")
    return _execute(parser, argv)


def main(argv: Sequence[str] | None = None) -> int:
    return _execute(_parser(), argv)


def _execute(parser: argparse.ArgumentParser, argv: Sequence[str] | None) -> int:
    args = parser.parse_args(argv)
    started = perf_counter()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        if args.command == "query":
            samples, rows = query_discovery(args.discovery_batch_index, args.mz_range)
            columns = (
                "sample_stem",
                "candidate_id",
                "mz",
                "apex_rt",
                "peak_status",
                "attached_event_count",
                "best_ms2_scan_id",
            )
            write_table(args.output_dir / "candidates.tsv", rows, columns)
            counts = Counter(row["sample_stem"] for row in rows)
            write_table(
                args.output_dir / "samples.tsv",
                [
                    {"sample_stem": sample, "candidate_count": counts[sample]}
                    for sample in samples
                ],
                ("sample_stem", "candidate_count"),
            )
            summary = {
                "sample_count": len(samples),
                "candidate_count": len(rows),
                "mz_ranges": args.mz_range,
            }
        elif args.command == "compare":
            summary = {
                "compared_to": str(args.before),
                "membership_diff": write_membership_comparison(
                    args.before, args.after, args.output_dir
                ),
            }
        else:
            if (
                args.compare_to is not None
                and args.compare_to.resolve() == args.output_dir.resolve()
            ):
                raise ValueError(
                    "Comparison baseline must not be the replay output directory"
                )
            inputs, provenance = read_grouping_inputs(args.inputs)
            config_values = asdict(inputs.alignment_config)
            overrides: dict[str, Any] = {}
            for setting in args.set:
                key, value = setting.split("=", 1)
                if key not in config_values:
                    raise ValueError(f"Unknown AlignmentConfig field: {key}")
                decoded = json.loads(value)
                overrides[key] = (
                    tuple(decoded) if isinstance(config_values[key], tuple) else decoded
                )
            config = replace(inputs.alignment_config, **overrides)
            baseline = replay_grouping(inputs)
            calibration = None
            if args.calibrate:
                calibration = calibrate_grouping(
                    inputs,
                    baseline,
                    policy=RtCalibrationPolicy(loess_fraction=args.loess_fraction),
                )
            elif args.rt_calibration:
                calibration = read_rt_calibration(args.rt_calibration)
            if calibration is not None:
                write_rt_calibration(
                    args.output_dir / "rt-calibration.json", calibration
                )
            rt_frame = (
                SampleRtFrame().with_sample_calibration(calibration)
                if calibration is not None
                else None
            )
            # Compare the validated configs without the Backfill-only switch.
            grouping_config_changed = config != replace(
                inputs.alignment_config, backfill_enabled=config.backfill_enabled
            )
            # An explicit sample calibration also replaces each workset's drift model.
            variant = grouping_config_changed or (
                calibration is not None
                and any(
                    workset.rt_drift is not None
                    or workset.rt_calibration != calibration
                    for workset in inputs.worksets
                )
            )
            groups = (
                replay_grouping(inputs, config=config, rt_coordinates=rt_frame)
                if variant
                else baseline
            )
            summary = {
                **write_grouping_result(
                    args.output_dir,
                    inputs,
                    groups,
                    baseline=baseline if variant else (),
                    compare_to=args.compare_to,
                    rt_coordinates=rt_frame,
                ),
                "source": provenance,
                "overrides": overrides,
                "rt_calibration": bool(calibration),
                "calibration_knot_count": len(calibration.knots) if calibration else 0,
                "calibrated_sample_count": sum(
                    bool(sample.knots) for sample in calibration.samples.values()
                )
                if calibration
                else 0,
            }
        summary["elapsed_seconds"] = round(perf_counter() - started, 3)
        write_json(args.output_dir / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
