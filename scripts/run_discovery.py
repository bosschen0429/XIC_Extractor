import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.config import ExtractionConfig
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.discovery.pipeline import run_discovery, run_discovery_batch
from xic_extractor.discovery.tag_profiles import (
    load_feature_tag_profiles,
    resolve_selected_tag_profiles,
)
from xic_extractor.raw_reader import RawReaderError
from xic_extractor.settings_schema import (
    ARBITRATED_RESOLVER_RETIRED_MESSAGE,
    CANONICAL_SETTINGS_DEFAULTS,
    RESOLVER_MODES,
)

_RT_BOUND_ERROR = "RT bounds must be finite values >= 0"


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

    settings = _discovery_settings(args)
    data_dir = raw_path.parent if raw_path is not None else raw_dir
    peak_config = _peak_config(data_dir, dll_dir, output_dir, settings)
    timing_recorder = (
        TimingRecorder(
            "discovery",
            live_output_path=args.timing_output.resolve(),
        )
        if args.timing_output is not None
        else None
    )
    timing_kwargs = (
        {"timing_recorder": timing_recorder}
        if timing_recorder is not None
        else {}
    )
    try:
        if raw_path is not None:
            outputs = run_discovery(
                raw_path,
                output_dir=output_dir,
                settings=settings,
                peak_config=peak_config,
                **timing_kwargs,
            )
            print(f"Discovery candidates CSV: {outputs.candidates_csv}")
            print(f"Discovery review CSV: {outputs.review_csv}")
        else:
            outputs = run_discovery_batch(
                raw_paths,
                output_dir=output_dir,
                settings=settings,
                peak_config=peak_config,
                **timing_kwargs,
            )
            print(f"Discovery batch index: {outputs.batch_index_csv}")
    except RawReaderError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if timing_recorder is not None:
        timing_path = args.timing_output.resolve()
        timing_recorder.write_json(timing_path)
        print(f"Timing JSON: {timing_path}")

    return 0


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
    parser.add_argument("--neutral-loss-tag", default="DNA_dR")
    parser.add_argument("--neutral-loss-da", type=_positive_float, default=116.0474)
    parser.add_argument("--feature-list", type=Path)
    parser.add_argument(
        "--selected-tags",
        help="Comma-separated FH tag names or labels.",
    )
    parser.add_argument(
        "--tag-combine-mode",
        choices=("union", "intersection"),
        default="union",
    )
    parser.add_argument("--nl-tolerance-ppm", type=_positive_float, default=20.0)
    parser.add_argument(
        "--precursor-mz-tolerance-ppm",
        type=_positive_float,
        default=20.0,
    )
    parser.add_argument(
        "--product-mz-tolerance-ppm",
        type=_positive_float,
        default=20.0,
    )
    parser.add_argument("--product-search-ppm", type=_positive_float, default=50.0)
    parser.add_argument(
        "--ms2-precursor-tol-da",
        type=_positive_float,
        default=float(CANONICAL_SETTINGS_DEFAULTS["ms2_precursor_tol_da"]),
        help=(
            "MS2 filter precursor / isolation window tolerance in Da for "
            "product+neutral-loss inferred discovery seeds."
        ),
    )
    parser.add_argument("--nl-min-intensity-ratio", type=_positive_float, default=0.01)
    parser.add_argument("--seed-rt-gap-min", type=_positive_float, default=0.20)
    parser.add_argument("--ms1-search-padding-min", type=_positive_float, default=0.20)
    parser.add_argument("--rt-min", type=_rt_bound, default=0.0)
    parser.add_argument("--rt-max", type=_rt_bound, default=999.0)
    parser.add_argument(
        "--resolver-mode",
        type=_resolver_mode,
        default="local_minimum",
    )
    args = parser.parse_args(argv)
    if args.rt_min > args.rt_max:
        parser.error("rt-min must be <= rt-max")
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
    if args.feature_list is None:
        return DiscoverySettings(
            neutral_loss_profile=NeutralLossProfile(
                tag=args.neutral_loss_tag,
                neutral_loss_da=args.neutral_loss_da,
            ),
            tag_combine_mode="single",
            **common,
        )

    selected = [
        value.strip()
        for value in str(args.selected_tags).split(",")
        if value.strip()
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
        tag_combine_mode=args.tag_combine_mode,
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
