from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.validation_harness_core import (
    DEFAULT_FULL_TISSUE_DIR,
    DEFAULT_TISSUE_VALIDATION_DIR,
    SUITE_CHOICES,
    ValidationRunResult,
    ValidationRunSpec,
    build_validation_specs,
    command_to_powershell,
    run_validation_specs,
)

__all__ = [
    "DEFAULT_FULL_TISSUE_DIR",
    "DEFAULT_TISSUE_VALIDATION_DIR",
    "ValidationRunResult",
    "ValidationRunSpec",
    "build_validation_specs",
    "command_to_powershell",
    "main",
    "run_validation_specs",
]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    suite_names = tuple(args.suite or ("manual-2raw", "tissue-fixture-cohort"))
    if "all" in suite_names:
        expanded = SUITE_CHOICES
    else:
        expanded = suite_names
    if "tissue-full-cohort" in expanded and not args.confirm_full_run:
        print("tissue-full-cohort requires --confirm-full-run", file=sys.stderr)
        return 2

    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    specs = build_validation_specs(
        suite_names=expanded,
        base_dir=args.base_dir.resolve(),
        output_root=args.output_root.resolve(),
        run_id=run_id,
        workers=args.parallel_workers,
        parallel_mode=args.parallel_mode,
        data_dir_override=args.data_dir.resolve() if args.data_dir else None,
        settings_overrides=tuple(args.setting or ()),
    )

    if args.dry_run:
        for spec in specs:
            print(f"{spec.name}: {command_to_powershell(spec.command)}")
        return 0

    results = run_validation_specs(
        specs,
        base_dir=args.base_dir.resolve(),
        output_root=args.output_root.resolve(),
        run_id=run_id,
        baseline_root=args.baseline_root.resolve() if args.baseline_root else None,
    )
    for result in results:
        print(
            f"{result.suite}: {result.status}, compare={result.compare_result}, "
            f"output={result.output_path}"
        )
        if result.message:
            print(f"  {result.message}")
    return 1 if any(result.status != "passed" for result in results) else 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run targeted real-data validation tiers and workbook comparisons. "
            "This harness does not validate untargeted Alignment."
        ),
        epilog=(
            "Suites:\n"
            "  manual-2raw   manual-truth area/RT comparison\n"
            "  tissue-fixture-cohort  targeted workbook smoke or A/B comparison\n"
            "  tissue-full-cohort opt-in full targeted workbook gate; requires "
            "--confirm-full-run\n\n"
            "Workflow:\n"
            "  inspect  use --dry-run before RAW-backed work\n"
            "  output   <output-root>/<run-id>/validation_summary.csv\n"
            "  compare  reuse the run ID and pass --baseline-root\n"
            "  stop     fix an inconclusive FIXTURE_COHORT gate; do not escalate it to "
            "FULL_COHORT\n"
            "  safety   docs/agent/parameter-settings.md and "
            "xic-raw-validation"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--suite",
        action="append",
        choices=(*SUITE_CHOICES, "all"),
        help=(
            "Validation suite to run. Repeatable. Default: manual-2raw + "
            "tissue-fixture-cohort."
        ),
    )
    parser.add_argument("--base-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("output/validation_harness"),
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--baseline-root", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument(
        "--setting",
        action="append",
        type=_setting_override,
        default=None,
        metavar="KEY=VALUE",
        help=(
            "Additional canonical setting override for extraction suites. "
            "Repeatable."
        ),
    )
    parser.add_argument(
        "--parallel-mode",
        choices=("serial", "process"),
        default="process",
    )
    parser.add_argument("--parallel-workers", type=_positive_int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-full-run", action="store_true")
    return parser.parse_args(argv)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("parallel-workers must be >= 1")
    return parsed


def _setting_override(value: str) -> tuple[str, str]:
    key, separator, setting_value = value.partition("=")
    key = key.strip()
    setting_value = setting_value.strip()
    if not separator or not key or not setting_value:
        raise argparse.ArgumentTypeError("setting must be KEY=VALUE")
    return key, setting_value


if __name__ == "__main__":
    raise SystemExit(main())
