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

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.grouping_experiment import (
    GroupingInputs,
    GroupingWorkset,
    refit_rt_models,
    replay_grouping,
)
from xic_extractor.alignment.rt_frame import RtFrame, SampleRtFrame
from xic_extractor.configuration.execution_profile import (
    ExecutionProfile,
    load_execution_profile,
)
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
from xic_extractor.execution.workers import WorkerAdmissionPool
from xic_extractor.rt_calibration_codec import rt_calibration_payload, rt_drift_payload


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
    replay.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="New or empty directory for grouping results",
    )
    replay.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="FIELD=JSON",
        help="Override an AlignmentConfig field (Backfill does not affect grouping)",
    )
    replay.add_argument(
        "--rt-calibration",
        type=Path,
        help="Use an existing sample_rt_calibration JSON instead of the batch RT "
        "models refitted from the snapshot's anchors",
    )
    replay.add_argument(
        "--compare-to",
        type=Path,
        help="Previous replay or capture output dir of the same snapshot (e.g. "
        "another commit); a variant uses it as baseline instead of replaying the "
        "frozen snapshot, so its changes include any grouping-code difference "
        "from that run",
    )


def regroup_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        usage="%(prog)s regroup --inputs SNAPSHOT --output-dir DIR [options]",
        description="Refit batch RT models from the snapshot's anchors and recompute "
        "initial scalar CID-NL groups from frozen observations, without Discovery, "
        "RAW, DLL, Backfill or Matrix publication.",
    )
    _add_replay_arguments(parser)
    parser.set_defaults(command="replay")
    return _execute(parser, argv)


def main(argv: Sequence[str] | None = None) -> int:
    return _execute(_parser(), argv)


def _execute(parser: argparse.ArgumentParser, argv: Sequence[str] | None) -> int:
    args = parser.parse_args(argv)
    execution = load_execution_profile()
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
            if any(args.output_dir.iterdir()):
                raise ValueError(
                    f"Regrouping requires an empty output directory: {args.output_dir}"
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
            rt_frame = None
            if args.rt_calibration:
                calibration = read_rt_calibration(args.rt_calibration)
                write_rt_calibration(
                    args.output_dir / "rt-calibration.json", calibration
                )
                rt_frame = SampleRtFrame().with_sample_calibration(calibration)
                current = inputs
                rt_model = "external"
                rt_model_changed = any(
                    workset.rt_drift is not None
                    or workset.rt_calibration != calibration
                    for workset in inputs.worksets
                )
            else:
                current = refit_rt_models(inputs)
                rt_model = (
                    "refit"
                    if any(item.rt_model_inputs is not None for item in inputs.worksets)
                    else "snapshot"
                )
                rt_model_changed = _rt_models_changed(inputs, current)
            grouping_config_changed = config != replace(
                inputs.alignment_config, backfill_enabled=config.backfill_enabled
            )
            baseline: Sequence[CrossSamplePeakGroupHypothesis] | Path = ()
            if grouping_config_changed or rt_model_changed:
                groups = _replay_grouping(
                    current,
                    execution=execution,
                    config=config,
                    rt_coordinates=rt_frame,
                )
                baseline = (
                    args.compare_to
                    if args.compare_to is not None
                    else _replay_grouping(inputs, execution=execution)
                )
            else:
                groups = _replay_grouping(inputs, execution=execution)
            summary = {
                **write_grouping_result(
                    args.output_dir,
                    current,
                    groups,
                    baseline=baseline,
                    compare_to=args.compare_to,
                    rt_coordinates=rt_frame,
                ),
                "source": provenance,
                "overrides": overrides,
                "rt_model": rt_model,
                "rt_model_changed": rt_model_changed,
            }
        summary["elapsed_seconds"] = round(perf_counter() - started, 3)
        write_json(args.output_dir / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"{parser.prog}: {exc}\n")


def _replay_grouping(
    inputs: GroupingInputs,
    *,
    execution: ExecutionProfile,
    config: AlignmentConfig | None = None,
    rt_coordinates: RtFrame | None = None,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """``replay_grouping`` with worksets spread over processes, largest first.

    Worksets share no grouping state, so the groups and their order are unchanged.
    """
    worksets = inputs.worksets
    workers = min(execution.parallel_workers, len(worksets))
    if execution.backend != "process" or workers < 2:
        return replay_grouping(inputs, config=config, rt_coordinates=rt_coordinates)
    context = (inputs, config, rt_coordinates)
    results: dict[int, tuple[CrossSamplePeakGroupHypothesis, ...]] = {}

    def collect(
        _completed: int,
        _total: int,
        index: int,
        groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    ) -> None:
        results[index] = groups

    with WorkerAdmissionPool(
        max_workers=workers, backend=execution.backend, shared_objects=(context,)
    ) as pool:
        pool.run_windowed(
            sorted(range(len(worksets)), key=lambda i: -len(worksets[i].owners)),
            _replay_workset,
            task_arg_fn=lambda index: (context, index),
            on_completed=collect,
        )
    if pool.first_error is not None:
        raise pool.first_error
    return tuple(group for index in range(len(worksets)) for group in results[index])


def _replay_workset(
    task: tuple[tuple[GroupingInputs, AlignmentConfig | None, RtFrame | None], int],
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    (inputs, config, rt_coordinates), index = task
    return replay_grouping(
        replace(inputs, worksets=(inputs.worksets[index],)),
        config=config,
        rt_coordinates=rt_coordinates,
    )


def _rt_models_changed(frozen: GroupingInputs, refitted: GroupingInputs) -> bool:
    """Compare each batch's models as the snapshot persists them."""
    pairs = {
        (
            id(before.rt_calibration),
            id(before.rt_drift),
            id(after.rt_calibration),
            id(after.rt_drift),
        ): (before, after)
        for before, after in zip(frozen.worksets, refitted.worksets, strict=True)
    }
    return any(
        _rt_model_payload(before) != _rt_model_payload(after)
        for before, after in pairs.values()
    )


def _rt_model_payload(workset: GroupingWorkset) -> tuple[object, object]:
    return (
        None
        if workset.rt_calibration is None
        else rt_calibration_payload(workset.rt_calibration),
        None if workset.rt_drift is None else rt_drift_payload(workset.rt_drift),
    )


if __name__ == "__main__":
    raise SystemExit(main())
