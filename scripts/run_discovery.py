import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.config import ExtractionConfig
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.discovery.pipeline import (
    DiscoveryProcessExecutionError,
    run_discovery,
    run_discovery_batch,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec_from_config as _compile_processing_run_spec,
)
from xic_extractor.discovery.tag_profiles import (
    load_feature_tag_profiles,
    resolve_selected_tag_profiles,
)
from xic_extractor.presets import PresetError, bind_cid_nl_recipe, load_preset
from xic_extractor.raw_reader import RawReaderError
from xic_extractor.settings_schema import (
    ARBITRATED_RESOLVER_RETIRED_MESSAGE,
    CANONICAL_SETTINGS_DEFAULTS,
    RESOLVER_MODES,
    default_parallel_workers,
)

_RT_BOUND_ERROR = "RT bounds must be finite values >= 0"
_RUN_SPEC_ERROR_TEXT_LIMIT = 400


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    raw_path = args.raw.resolve() if args.raw is not None else None
    raw_dir = args.raw_dir.resolve() if args.raw_dir is not None else None
    dll_dir = args.dll_dir.resolve()
    output_dir = args.output_dir.resolve()
    if raw_path is not None and not raw_path.is_file():
        print(f"{raw_path}: raw file does not exist", file=sys.stderr)
        return 2
    if raw_dir is not None and not raw_dir.is_dir():
        print(f"{raw_dir}: raw directory does not exist", file=sys.stderr)
        return 2
    raw_paths = (raw_path,) if raw_path is not None else _raw_paths_from_dir(raw_dir)
    if not raw_paths:
        print(f"{raw_dir}: raw directory contains no .raw files", file=sys.stderr)
        return 2
    if not dll_dir.is_dir():
        print(f"{dll_dir}: dll directory does not exist", file=sys.stderr)
        return 2

    try:
        settings = _discovery_settings(args)
    except (OSError, PresetError, ValueError) as exc:
        print(_bounded_run_spec_error(exc), file=sys.stderr)
        return 2
    data_dir = raw_path.parent if raw_path is not None else raw_dir
    if data_dir is None:
        raise ValueError("Discovery input directory was not resolved")
    peak_config = _peak_config(data_dir, dll_dir, output_dir, settings)
    timing_recorder = (
        TimingRecorder(
            "discovery",
            live_output_path=args.timing_output.resolve(),
        )
        if args.timing_output is not None
        else None
    )
    try:
        if timing_recorder is None:
            run_spec = _compile_processing_run_spec(raw_paths, settings, peak_config)
        else:
            with timing_recorder.stage(
                "discover.compile_run_spec",
                metrics={"raw_count": len(raw_paths)},
            ):
                run_spec = _compile_processing_run_spec(
                    raw_paths,
                    settings,
                    peak_config,
                )
    except (OSError, TypeError, ValueError) as exc:
        print(_bounded_run_spec_error(exc), file=sys.stderr)
        return 2
    try:
        if raw_path is not None:
            if timing_recorder is None:
                single_outputs = run_discovery(
                    raw_path,
                    output_dir=output_dir,
                    settings=settings,
                    peak_config=peak_config,
                    run_spec=run_spec,
                )
            else:
                single_outputs = run_discovery(
                    raw_path,
                    output_dir=output_dir,
                    settings=settings,
                    peak_config=peak_config,
                    run_spec=run_spec,
                    timing_recorder=timing_recorder,
                )
            print(f"Discovery candidates CSV: {single_outputs.candidates_csv}")
            print(f"Discovery review CSV: {single_outputs.review_csv}")
        else:
            max_workers = (
                args.max_workers
                if args.max_workers is not None
                else min(len(raw_paths), default_parallel_workers())
            )
            if timing_recorder is None:
                batch_outputs = run_discovery_batch(
                    raw_paths,
                    output_dir=output_dir,
                    settings=settings,
                    peak_config=peak_config,
                    run_spec=run_spec,
                    max_workers=max_workers,
                )
            else:
                batch_outputs = run_discovery_batch(
                    raw_paths,
                    output_dir=output_dir,
                    settings=settings,
                    peak_config=peak_config,
                    run_spec=run_spec,
                    max_workers=max_workers,
                    timing_recorder=timing_recorder,
                )
            print(f"Discovery batch index: {batch_outputs.batch_index_csv}")
    except (RawReaderError, DiscoveryProcessExecutionError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if timing_recorder is not None:
        timing_path = args.timing_output.resolve()
        timing_recorder.write_json(timing_path)
        print(f"Timing JSON: {timing_path}")

    return 0


def _bounded_run_spec_error(exc: Exception) -> str:
    detail = " ".join(str(exc).split()) or type(exc).__name__
    prefix = "Discovery run specification failed: "
    available = _RUN_SPEC_ERROR_TEXT_LIMIT - len(prefix)
    if len(detail) > available:
        detail = f"{detail[: available - 3]}..."
    return f"{prefix}{detail}"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run strict MS2 neutral-loss discovery for one RAW or a RAW directory."
        )
    )
    raw_input = parser.add_mutually_exclusive_group(required=True)
    raw_input.add_argument("--raw", type=Path, help="Thermo RAW file.")
    raw_input.add_argument(
        "--raw-dir",
        type=Path,
        help="Directory containing .raw files.",
    )
    parser.add_argument(
        "--dll-dir",
        type=Path,
        required=True,
        help="Directory containing Thermo RawFileReader DLLs.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output") / "discovery",
        help=(
            "Output directory. Single RAW writes discovery_candidates.csv and "
            "discovery_review.csv; RAW directory writes discovery_batch_index.csv "
            "and per-sample CSVs."
        ),
    )
    parser.add_argument(
        "--timing-output",
        type=Path,
        help="Optional JSON path for discovery stage timing.",
    )
    parser.add_argument(
        "--max-workers",
        type=_positive_int,
        help=(
            "Worker limit for --raw-dir. Defaults to the smaller of the RAW count "
            "and the standard parallel worker budget (CPU count minus one, minimum "
            "one). Use 1 for serial execution. Ignored for --raw."
        ),
    )
    parser.add_argument("--preset", help="Built-in CID-NL name or method TOML path.")
    parser.add_argument("--neutral-loss-tag")
    parser.add_argument("--neutral-loss-da", type=_positive_float)
    parser.add_argument("--feature-list", type=Path)
    parser.add_argument(
        "--selected-tags",
        help="Comma-separated FH tag names or labels.",
    )
    parser.add_argument(
        "--tag-combine-mode",
        choices=("union", "intersection"),
    )
    parser.add_argument("--nl-tolerance-ppm", type=_positive_float)
    parser.add_argument(
        "--precursor-mz-tolerance-ppm",
        type=_positive_float,
    )
    parser.add_argument(
        "--product-mz-tolerance-ppm",
        type=_positive_float,
    )
    parser.add_argument("--product-search-ppm", type=_positive_float)
    parser.add_argument(
        "--ms2-precursor-tol-da",
        type=_positive_float,
        help=(
            "MS2 filter precursor / isolation window tolerance in Da for "
            "product+neutral-loss inferred discovery seeds."
        ),
    )
    parser.add_argument("--nl-min-intensity-ratio", type=_positive_float)
    parser.add_argument("--seed-rt-gap-min", type=_positive_float)
    parser.add_argument("--ms1-search-padding-min", type=_positive_float)
    parser.add_argument("--rt-min", type=_rt_bound)
    parser.add_argument("--rt-max", type=_rt_bound)
    parser.add_argument(
        "--resolver-mode",
        type=_resolver_mode,
    )
    args = parser.parse_args(argv)
    rt_min = args.rt_min if args.rt_min is not None else 0.0
    rt_max = args.rt_max if args.rt_max is not None else 999.0
    if (
        args.preset is None or (args.rt_min is not None and args.rt_max is not None)
    ) and rt_min > rt_max:
        parser.error("rt-min must be <= rt-max")
    if args.preset is not None and any(
        value is not None
        for value in (
            args.neutral_loss_tag,
            args.neutral_loss_da,
            args.feature_list,
            args.selected_tags,
            args.tag_combine_mode,
        )
    ):
        parser.error("--preset cannot be combined with separate tag selectors")
    if args.feature_list is not None and not args.selected_tags:
        parser.error("--selected-tags is required when --feature-list is provided")
    return args


def _discovery_settings(args: argparse.Namespace) -> DiscoverySettings:
    common = {
        "nl_tolerance_ppm": args.nl_tolerance_ppm,
        "precursor_mz_tolerance_ppm": args.precursor_mz_tolerance_ppm,
        "product_mz_tolerance_ppm": args.product_mz_tolerance_ppm,
        "product_search_ppm": args.product_search_ppm,
        "ms2_precursor_tol_da": args.ms2_precursor_tol_da,
        "nl_min_intensity_ratio": args.nl_min_intensity_ratio,
        "seed_rt_gap_min": args.seed_rt_gap_min,
        "ms1_search_padding_min": args.ms1_search_padding_min,
        "rt_min": args.rt_min,
        "rt_max": args.rt_max,
        "resolver_mode": args.resolver_mode,
    }
    common = {key: value for key, value in common.items() if value is not None}
    if args.preset is not None:
        return bind_cid_nl_recipe(
            load_preset(args.preset), "discovery_only", recipe_overrides=common
        ).discovery_settings
    if args.feature_list is None:
        return DiscoverySettings(
            neutral_loss_profile=NeutralLossProfile(
                tag=args.neutral_loss_tag or "DNA_dR",
                neutral_loss_da=args.neutral_loss_da
                if args.neutral_loss_da is not None
                else 116.0474,
            ),
            tag_combine_mode="single",
            **common,
        )

    selected = [
        value.strip() for value in str(args.selected_tags).split(",") if value.strip()
    ]
    profiles = resolve_selected_tag_profiles(
        load_feature_tag_profiles(args.feature_list),
        selected,
    )
    return DiscoverySettings(
        neutral_loss_profiles=tuple(
            NeutralLossProfile(
                tag=profile.tag_name,
                neutral_loss_da=profile.parameter_mz_or_da,
            )
            for profile in profiles
        ),
        selected_tag_names=tuple(profile.tag_name for profile in profiles),
        tag_combine_mode=args.tag_combine_mode or "union",
        **common,
    )


def _peak_config(
    data_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    settings: DiscoverySettings,
) -> ExtractionConfig:
    defaults = CANONICAL_SETTINGS_DEFAULTS
    return ExtractionConfig(
        data_dir=data_dir,
        dll_dir=dll_dir,
        output_csv=output_dir / "xic_results.csv",
        diagnostics_csv=output_dir / "xic_diagnostics.csv",
        smooth_window=int(defaults["smooth_window"]),
        smooth_polyorder=int(defaults["smooth_polyorder"]),
        ms1_morphology_smoothing_window_points=int(
            defaults["ms1_morphology_smoothing_window_points"]
        ),
        peak_rel_height=float(defaults["peak_rel_height"]),
        peak_min_prominence_ratio=float(defaults["peak_min_prominence_ratio"]),
        ms2_precursor_tol_da=settings.ms2_precursor_tol_da,
        nl_min_intensity_ratio=settings.nl_min_intensity_ratio,
        resolver_mode=settings.resolver_mode,
        resolver_chrom_threshold=float(defaults["resolver_chrom_threshold"]),
        resolver_min_search_range_min=float(defaults["resolver_min_search_range_min"]),
        resolver_min_relative_height=float(defaults["resolver_min_relative_height"]),
        resolver_min_absolute_height=float(defaults["resolver_min_absolute_height"]),
        resolver_min_ratio_top_edge=float(defaults["resolver_min_ratio_top_edge"]),
        resolver_peak_duration_min=float(defaults["resolver_peak_duration_min"]),
        resolver_peak_duration_max=float(defaults["resolver_peak_duration_max"]),
        resolver_min_scans=int(defaults["resolver_min_scans"]),
    )


def _raw_paths_from_dir(raw_dir: Path | None) -> tuple[Path, ...]:
    if raw_dir is None:
        return ()
    return tuple(sorted(raw_dir.glob("*.raw"), key=lambda path: path.name.lower()))


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be > 0")
    return parsed


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a float") from exc
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise argparse.ArgumentTypeError("value must be > 0")
    return parsed


def _resolver_mode(value: str) -> str:
    parsed = value.strip()
    if parsed == "arbitrated":
        raise argparse.ArgumentTypeError(ARBITRATED_RESOLVER_RETIRED_MESSAGE)
    if parsed not in RESOLVER_MODES:
        allowed = ", ".join(RESOLVER_MODES[:-1]) + f", or {RESOLVER_MODES[-1]}"
        raise argparse.ArgumentTypeError(f"resolver-mode must be {allowed}")
    return parsed


def _rt_bound(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(_RT_BOUND_ERROR) from exc
    if not math.isfinite(parsed) or parsed < 0.0:
        raise argparse.ArgumentTypeError(_RT_BOUND_ERROR)
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
