"""CLI for building local MS1 detected-feature tables from one RAW file."""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.local_ms1_features import (
    LocalMs1FeatureSettings,
    build_local_ms1_feature_table,
    write_local_ms1_feature_outputs,
)
from xic_extractor.ms1_scan_index import MS1ScanReadError, build_ms1_scan_index
from xic_extractor.raw_reader import RawReaderError, open_raw

_DEFAULT_SETTINGS = LocalMs1FeatureSettings()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a local single-sample MS1 feature table for Delta Mass / "
            "Pt pattern Discovery. Outputs are screening evidence only."
        )
    )
    parser.add_argument("--raw", type=Path, required=True, help="Thermo RAW file.")
    parser.add_argument(
        "--dll-dir",
        type=Path,
        required=True,
        help="Thermo RawFileReader DLL directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for detected_features.csv and local MS1 diagnostics.",
    )
    parser.add_argument(
        "--sample-stem",
        help="Sample stem for feature IDs. Defaults to the RAW filename stem.",
    )
    parser.add_argument(
        "--mass-detection-noise-level",
        type=float,
        default=_DEFAULT_SETTINGS.mass_detection_noise_level,
    )
    parser.add_argument(
        "--min-trace-seed-intensity",
        type=float,
        default=_DEFAULT_SETTINGS.min_trace_seed_intensity,
    )
    parser.add_argument(
        "--min-consecutive-trace-intensity",
        type=float,
        default=_DEFAULT_SETTINGS.min_consecutive_trace_intensity,
    )
    parser.add_argument(
        "--feature-link-ppm",
        type=float,
        default=_DEFAULT_SETTINGS.feature_link_ppm,
    )
    parser.add_argument(
        "--max-missing-scans",
        type=int,
        default=_DEFAULT_SETTINGS.max_missing_scans,
    )
    parser.add_argument(
        "--min-observed-scans",
        type=int,
        default=_DEFAULT_SETTINGS.min_observed_scans,
    )
    parser.add_argument(
        "--min-duration-min",
        type=float,
        default=_DEFAULT_SETTINGS.min_duration_min,
    )
    parser.add_argument(
        "--max-duration-min",
        type=float,
        default=_DEFAULT_SETTINGS.max_duration_min,
    )
    parser.add_argument(
        "--gaussian-window-points",
        type=int,
        default=_DEFAULT_SETTINGS.gaussian_window_points,
    )
    parser.add_argument(
        "--min-ok-prominence",
        type=float,
        default=_DEFAULT_SETTINGS.min_ok_prominence,
    )
    parser.add_argument(
        "--weak-support-prominence",
        type=float,
        default=_DEFAULT_SETTINGS.weak_support_prominence,
    )
    parser.add_argument(
        "--max-ok-gap-fraction",
        type=float,
        default=_DEFAULT_SETTINGS.max_ok_gap_fraction,
    )
    parser.add_argument(
        "--max-weak-gap-fraction",
        type=float,
        default=_DEFAULT_SETTINGS.max_weak_gap_fraction,
    )
    parser.add_argument(
        "--max-ok-mz-ppm-std",
        type=float,
        default=_DEFAULT_SETTINGS.max_ok_mz_ppm_std,
    )
    parser.add_argument(
        "--max-weak-mz-ppm-std",
        type=float,
        default=_DEFAULT_SETTINGS.max_weak_mz_ppm_std,
    )
    parser.add_argument(
        "--optimal-component-max-size",
        type=int,
        default=_DEFAULT_SETTINGS.optimal_component_max_size,
    )
    parser.add_argument(
        "--optimal-component-max-edge-density",
        type=float,
        default=_DEFAULT_SETTINGS.optimal_component_max_edge_density,
    )
    parser.add_argument(
        "--optimal-component-max-edges",
        type=int,
        default=_DEFAULT_SETTINGS.optimal_component_max_edges,
    )
    parser.add_argument(
        "--split-valley-fraction-of-smaller-apex",
        type=float,
        default=_DEFAULT_SETTINGS.split_valley_fraction_of_smaller_apex,
    )
    return parser


def _validate_finite_float(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    attr: str,
    flag: str,
) -> float:
    value = float(getattr(args, attr))
    if not math.isfinite(value):
        parser.error(f"{flag} must be finite")
    return value


def _validate_non_negative_float(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    attr: str,
    flag: str,
) -> float:
    value = _validate_finite_float(parser, args, attr, flag)
    if value < 0:
        parser.error(f"{flag} must be >= 0")
    return value


def _validate_positive_float(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    attr: str,
    flag: str,
) -> float:
    value = _validate_finite_float(parser, args, attr, flag)
    if value <= 0:
        parser.error(f"{flag} must be > 0")
    return value


def _validate_fraction(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    attr: str,
    flag: str,
) -> float:
    value = _validate_finite_float(parser, args, attr, flag)
    if not 0 <= value <= 1:
        parser.error(f"{flag} must be between 0 and 1")
    return value


def _validate_non_negative_int(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    attr: str,
    flag: str,
) -> int:
    value = int(getattr(args, attr))
    if value < 0:
        parser.error(f"{flag} must be >= 0")
    return value


def _validate_positive_int(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    attr: str,
    flag: str,
) -> int:
    value = int(getattr(args, attr))
    if value <= 0:
        parser.error(f"{flag} must be > 0")
    return value


def _settings_from_args(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> LocalMs1FeatureSettings:
    min_duration_min = _validate_non_negative_float(
        parser,
        args,
        "min_duration_min",
        "--min-duration-min",
    )
    max_duration_min = _validate_positive_float(
        parser,
        args,
        "max_duration_min",
        "--max-duration-min",
    )
    if max_duration_min < min_duration_min:
        parser.error("--max-duration-min must be >= --min-duration-min")
    return LocalMs1FeatureSettings(
        mass_detection_noise_level=_validate_non_negative_float(
            parser,
            args,
            "mass_detection_noise_level",
            "--mass-detection-noise-level",
        ),
        min_trace_seed_intensity=_validate_non_negative_float(
            parser,
            args,
            "min_trace_seed_intensity",
            "--min-trace-seed-intensity",
        ),
        min_consecutive_trace_intensity=_validate_non_negative_float(
            parser,
            args,
            "min_consecutive_trace_intensity",
            "--min-consecutive-trace-intensity",
        ),
        feature_link_ppm=_validate_positive_float(
            parser,
            args,
            "feature_link_ppm",
            "--feature-link-ppm",
        ),
        max_missing_scans=_validate_non_negative_int(
            parser,
            args,
            "max_missing_scans",
            "--max-missing-scans",
        ),
        min_observed_scans=_validate_positive_int(
            parser,
            args,
            "min_observed_scans",
            "--min-observed-scans",
        ),
        min_duration_min=min_duration_min,
        max_duration_min=max_duration_min,
        gaussian_window_points=_validate_positive_int(
            parser,
            args,
            "gaussian_window_points",
            "--gaussian-window-points",
        ),
        min_ok_prominence=_validate_non_negative_float(
            parser,
            args,
            "min_ok_prominence",
            "--min-ok-prominence",
        ),
        weak_support_prominence=_validate_non_negative_float(
            parser,
            args,
            "weak_support_prominence",
            "--weak-support-prominence",
        ),
        max_ok_gap_fraction=_validate_fraction(
            parser,
            args,
            "max_ok_gap_fraction",
            "--max-ok-gap-fraction",
        ),
        max_weak_gap_fraction=_validate_fraction(
            parser,
            args,
            "max_weak_gap_fraction",
            "--max-weak-gap-fraction",
        ),
        max_ok_mz_ppm_std=_validate_non_negative_float(
            parser,
            args,
            "max_ok_mz_ppm_std",
            "--max-ok-mz-ppm-std",
        ),
        max_weak_mz_ppm_std=_validate_non_negative_float(
            parser,
            args,
            "max_weak_mz_ppm_std",
            "--max-weak-mz-ppm-std",
        ),
        optimal_component_max_size=_validate_positive_int(
            parser,
            args,
            "optimal_component_max_size",
            "--optimal-component-max-size",
        ),
        optimal_component_max_edge_density=_validate_fraction(
            parser,
            args,
            "optimal_component_max_edge_density",
            "--optimal-component-max-edge-density",
        ),
        optimal_component_max_edges=_validate_positive_int(
            parser,
            args,
            "optimal_component_max_edges",
            "--optimal-component-max-edges",
        ),
        split_valley_fraction_of_smaller_apex=_validate_fraction(
            parser,
            args,
            "split_valley_fraction_of_smaller_apex",
            "--split-valley-fraction-of-smaller-apex",
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = _settings_from_args(args, parser)
    raw_path = args.raw.resolve()
    dll_dir = args.dll_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not raw_path.is_file():
        print(f"{raw_path}: RAW file does not exist", file=sys.stderr)
        return 2
    if not dll_dir.is_dir():
        print(f"{dll_dir}: DLL directory does not exist", file=sys.stderr)
        return 2
    sample_stem = args.sample_stem or raw_path.stem
    raw_stat = raw_path.stat()
    try:
        with open_raw(raw_path, dll_dir) as handle:
            index = build_ms1_scan_index(handle, fail_on_malformed=True)
    except (RawReaderError, MS1ScanReadError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    features, summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem=sample_stem,
        raw_path=str(raw_path),
        raw_file_size_bytes=int(raw_stat.st_size),
        raw_file_mtime_ns=int(raw_stat.st_mtime_ns),
        settings=settings,
    )
    outputs = write_local_ms1_feature_outputs(
        output_dir,
        features,
        summary,
        diagnostics,
    )
    print(f"Detected features CSV: {outputs.detected_features_csv}")
    print(f"Local MS1 feature summary JSON: {outputs.summary_json}")
    print(f"Local MS1 feature diagnostics CSV: {outputs.diagnostics_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
