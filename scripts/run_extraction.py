import argparse
import multiprocessing
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from xic_extractor import extractor
from xic_extractor.applications.targeted.application import (
    build_canonical_targeted_application_service,
    build_targeted_application_request,
)
from xic_extractor.applications.targeted.output_boundary import (
    TargetedOutputBoundaryError,
)
from xic_extractor.configuration.loader import (
    load_config,
    prepare_targeted_config,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig
from xic_extractor.raw_reader import RawReaderError


def main(argv: Sequence[str] | None = None) -> int:
    multiprocessing.freeze_support()
    args = _parse_args(argv)
    prepared = None
    try:
        base_dir = (args.base_dir or Path.cwd()).resolve()
        config_dir = base_dir / "config"
        settings_overrides = _settings_overrides(args)
        if args.method_dir is not None:
            prepared = prepare_targeted_config(
                config_dir,
                method_dir=args.method_dir.resolve(),
                settings_overrides=settings_overrides,
                output_dir=base_dir / "output",
            )
            config, targets = prepared.config, list(prepared.targets)
        elif settings_overrides:
            config, targets = load_config(
                config_dir, settings_overrides=settings_overrides
            )
        else:
            config, targets = load_config(config_dir)
        # Execution resources are runtime choices, not part of the method hash.
        if args.parallel_mode is not None:
            config = replace(config, parallel_mode=args.parallel_mode)
        if args.parallel_workers is not None:
            config = replace(config, parallel_workers=args.parallel_workers)
        output = (
            build_canonical_targeted_application_service(extractor.acquire)
            .execute_application(
                build_targeted_application_request(
                    config,
                    targets,
                    progress_callback=_print_progress,
                    excel_output_path=_excel_output_path(config),
                )
            )
            .active_output
        )
    except (
        ConfigError,
        RawReaderError,
        TargetedOutputBoundaryError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    finally:
        if prepared is not None:
            prepared.cleanup()

    print(f"Processed files: {len(output.file_results)}")
    print(f"Diagnostics: {len(output.diagnostics)}")

    return 0


def _settings_overrides(args: argparse.Namespace) -> dict[str, str]:
    overrides: dict[str, str] = {}
    if args.data_dir is not None:
        data_dir = args.data_dir.resolve()
        if not data_dir.is_dir():
            raise ConfigError(f"{data_dir}: data_dir override must be a directory")
        overrides["data_dir"] = str(data_dir)
    if args.injection_order_source is not None:
        overrides["injection_order_source"] = str(args.injection_order_source.resolve())
    return overrides


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Targeted XIC extraction and deliver one XLSX workbook."
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=None,
        help="Project/base directory containing config/ and output/.",
    )
    parser.add_argument(
        "--method-dir",
        type=Path,
        default=None,
        help=(
            "Select a complete reusable Targeted method directory (settings.csv "
            "and targets.csv). Batch/machine settings come from --base-dir."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override settings.csv data_dir for validation subsets.",
    )
    parser.add_argument(
        "--injection-order-source",
        type=Path,
        default=None,
        help="Explicit injection-order CSV/XLSX for this RAW batch.",
    )
    parser.add_argument(
        "--parallel-mode",
        choices=("serial", "process"),
        default=None,
        help="Override settings.csv parallel_mode.",
    )
    parser.add_argument(
        "--parallel-workers",
        type=_positive_int,
        default=None,
        help="Override settings.csv parallel_workers.",
    )
    return parser.parse_args(argv)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("parallel-workers must be >= 1")
    return parsed


def _print_progress(current: int, total: int, filename: str) -> None:
    print(f"{current}/{total} {filename}")


def _excel_output_path(config: ExtractionConfig) -> Path:
    timestamp = _timestamp_text()
    return config.output_csv.parent / f"xic_results_{timestamp}.xlsx"


def _timestamp_text() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M")


if __name__ == "__main__":
    raise SystemExit(main())
