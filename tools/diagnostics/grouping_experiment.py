"""Query Discovery and replay grouping snapshots without RAW.

Write a snapshot with `scripts.run_alignment --stop-after grouping`.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
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
    compare_membership,
    query_discovery,
    read_grouping_inputs,
    read_rt_calibration,
    write_grouping_result,
    write_json,
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
    for command in (query, replay):
        command.add_argument("--output-dir", required=True, type=Path)
    query.add_argument("--discovery-batch-index", required=True, type=Path)
    query.add_argument("--mz-range", action="append", type=_range, required=True)
    replay.add_argument("--inputs", required=True, type=Path)
    replay.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="FIELD=JSON",
        help="Override an AlignmentConfig field",
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
    return parser


def _membership_comparison(args: argparse.Namespace, groups) -> dict[str, Any]:
    if args.compare_to is None:
        return {}
    rows, counts = compare_membership(args.compare_to / "members.tsv", groups)
    write_table(
        args.output_dir / "membership-diff.tsv",
        rows,
        (
            "change",
            "group_id",
            "mz",
            "part_count",
            "part_sizes",
            "part_apex_rt",
            "apex_gap_sec",
            "part_samples",
        ),
    )
    return {"compared_to": str(args.compare_to), "membership_diff": counts}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
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
                "area",
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
        else:
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
            groups = replay_grouping(inputs, config=config, rt_coordinates=rt_frame)
            summary = {
                **write_grouping_result(
                    args.output_dir,
                    inputs,
                    groups,
                    baseline=baseline,
                    rt_coordinates=rt_frame,
                ),
                "source": provenance,
                **_membership_comparison(args, groups),
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
        parser.exit(2, f"grouping-experiment: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
