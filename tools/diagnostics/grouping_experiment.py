"""Query Discovery, prepare bounded grouping inputs, and replay without RAW."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from time import perf_counter

from xic_extractor.acquisition.istd_reference import read_known_istd_reference
from xic_extractor.alignment.grouping_experiment import (
    calibrate_grouping,
    replay_grouping,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import RtCalibrationPolicy
from xic_extractor.configuration.cid_nl_profile import DEFAULT_CID_NL_MASS_POLICY
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.diagnostics.grouping_experiment_io import (
    group_signature,
    query_discovery,
    read_grouping_inputs,
    read_rt_calibration,
    write_grouping_inputs,
    write_grouping_result,
    write_json,
    write_rt_calibration,
    write_table,
)
from xic_extractor.discovery.csv_reader import read_discovery_batch_index
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.presets import bind_cid_nl_recipe, load_preset
from xic_extractor.raw_reader import read_analysis_set
from xic_extractor.untargeted_execution.alignment import (
    prepare_untargeted_alignment_inputs,
)
from xic_extractor.untargeted_execution.grouping_experiment import (
    prepare_grouping_experiment,
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
    prepare = commands.add_parser(
        "prepare", help="Assess selected whole partitions once; stop before Backfill"
    )
    replay = commands.add_parser(
        "replay", help="Regroup compact observations without RAW or Backfill"
    )
    for command in (query, prepare, replay):
        command.add_argument("--output-dir", required=True, type=Path)
    for command in (query, prepare):
        command.add_argument("--discovery-batch-index", required=True, type=Path)
        command.add_argument("--mz-range", action="append", type=_range, required=True)
    prepare.add_argument("--raw-dir", required=True, type=Path)
    prepare.add_argument("--dll-dir", required=True, type=Path)
    prepare.add_argument("--preset", default="dna_dr")
    prepare.add_argument("--sample-metadata", type=Path)
    prepare.add_argument("--known-istd-reference", type=Path)
    prepare.add_argument("--acquisition-cache-dir", type=Path)
    prepare.add_argument("--workers", type=int, default=4)
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
        help="Fit RT coordinates from near-all-sample baseline groups",
    )
    calibration.add_argument(
        "--rt-calibration", type=Path, help="Use an existing sample_rt_calibration JSON"
    )
    replay.add_argument("--anchor-min-sample-fraction", type=float, default=0.94)
    return parser


def _prepare(args: argparse.Namespace) -> dict:
    recipe = bind_cid_nl_recipe(load_preset(args.preset), "align_only")
    profile = resolve_signal_processing_profile()
    peak_config = ExtractionConfig(
        data_dir=args.raw_dir,
        dll_dir=args.dll_dir,
        output_csv=args.output_dir / "unused.csv",
        diagnostics_csv=args.output_dir / "unused-diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=DEFAULT_CID_NL_MASS_POLICY.ms2_precursor_tol_da,
        nl_min_intensity_ratio=recipe.discovery_settings.nl_min_intensity_ratio,
        resolver_mode=recipe.discovery_settings.resolver_mode,
        signal_processing_method=profile.method,
        signal_processing_window_points=profile.window_points,
        ms1_morphology_smoothing_window_points=profile.window_points,
    )
    batch = read_discovery_batch_index(
        args.discovery_batch_index, require_owner_closure=True
    )
    resources = prepare_untargeted_alignment_inputs(
        discovery_batch=batch,
        candidates_by_sample=DiscoveryInputSource(batch),
        raw_dir=args.raw_dir,
        dll_dir=args.dll_dir,
        output_dir=args.output_dir,
        settings=recipe.discovery_settings,
        peak_config=peak_config,
        sample_metadata=read_analysis_set(
            args.raw_dir, args.dll_dir, sequence_source=args.sample_metadata
        ).sample_metadata
        if args.sample_metadata
        else None,
        signal_processing_profile=profile,
        raw_workers=args.workers,
    )
    try:
        prepared = prepare_grouping_experiment(
            resources.plan,
            mz_ranges=args.mz_range,
            spool_directory=args.output_dir,
            alignment_config=recipe.alignment_config,
            known_istd_reference=read_known_istd_reference(args.known_istd_reference)
            if args.known_istd_reference
            else None,
            acquisition_cache_dir=args.acquisition_cache_dir,
            workers=args.workers,
            progress=lambda message: print(message, flush=True),
        )
        provenance = {
            "discovery_batch_index": str(args.discovery_batch_index.resolve()),
            "mz_ranges": args.mz_range,
            "preset": args.preset,
            "selected_partition_count": prepared.selected_partition_count,
            "total_partition_count": prepared.total_partition_count,
            "initial_job_count": prepared.initial_job_count,
        }
        snapshot = args.output_dir / "grouping-inputs.json.gz"
        write_grouping_inputs(snapshot, prepared.inputs, provenance=provenance)
        restored, _ = read_grouping_inputs(snapshot)
        replayed = replay_grouping(restored)
        if group_signature(replayed) != group_signature(prepared.groups):
            raise ValueError("Snapshot replay differs from formal initial grouping")
        return {
            **provenance,
            **write_grouping_result(args.output_dir, restored, replayed),
            "snapshot_bytes": snapshot.stat().st_size,
            "baseline_parity": True,
        }
    finally:
        resources.close()


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
                "seed_mz",
                "apex_rt",
                "seed_rt",
                "state",
                "ms1_peak_found",
                "seed_event_count",
                "area",
                "reason",
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
        elif args.command == "prepare":
            summary = _prepare(args)
        else:
            inputs, provenance = read_grouping_inputs(args.inputs)
            config_values = asdict(inputs.alignment_config)
            overrides = {}
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
                    policy=RtCalibrationPolicy(
                        anchor_min_sample_fraction=args.anchor_min_sample_fraction
                    ),
                )
            elif args.rt_calibration:
                calibration = read_rt_calibration(args.rt_calibration)
            if calibration is not None:
                write_rt_calibration(
                    args.output_dir / "rt-calibration.json", calibration
                )
            rt_frame = (
                SampleRtFrame().with_coordinate_override(calibration.corrected_rt_min)
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
