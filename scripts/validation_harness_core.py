from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from scripts.benchmark_parallel import _run_extraction_once
from scripts.compare_workbooks import WorkbookCompareResult, compare_workbooks
from xic_extractor.configuration.models import ConfigError
from xic_extractor.raw_reader import RawReaderError
from xic_extractor.tabular_io import write_delimited_rows

DEFAULT_MANUAL_DIR = Path(
    os.environ.get("XIC_MANUAL_2RAW_ROOT", "local_validation_artifacts/manual_2raw")
)
DEFAULT_MANUAL_WORKBOOK = DEFAULT_MANUAL_DIR / "20260112 UPLC splitting_forXIC.xlsx"
DEFAULT_NOSPLIT_RAW = (
    DEFAULT_MANUAL_DIR / "20251219_HESI_NoSplit_25ppb_ISTDs-1_60min_1_02.raw"
)
DEFAULT_SPLIT_RAW = (
    DEFAULT_MANUAL_DIR / "20260104_Split_NSI_w-75um-50cm_25ppb_ISTDs-1_60min_1_02.raw"
)
DEFAULT_NOSPLIT_TARGETS = DEFAULT_MANUAL_DIR / "combined_targets_file1.csv"
DEFAULT_SPLIT_TARGETS = DEFAULT_MANUAL_DIR / "combined_targets_file2.csv"
DEFAULT_TISSUE_VALIDATION_DIR = Path(
    os.environ.get(
        "XIC_RAW_VALIDATION_DIR",
        "local_validation_artifacts/raw/tissue_validation_8raw",
    )
)
DEFAULT_FULL_TISSUE_DIR = Path(
    os.environ.get("XIC_RAW_ROOT", "local_validation_artifacts/raw/tissue_85raw")
)

SUITE_CHOICES = ("manual-2raw", "tissue-fixture-cohort", "tissue-full-cohort")


@dataclass(frozen=True)
class ValidationRunSpec:
    name: str
    kind: str
    description: str
    output_dir: Path
    output_path: Path
    command: tuple[str, ...]
    data_dir: Path | None = None
    expected_raw_count: int | None = None
    parallel_mode: str = "process"
    workers: int = 4
    settings_overrides: tuple[tuple[str, str], ...] = ()
    requires_confirmation: bool = False


@dataclass(frozen=True)
class ValidationRunResult:
    suite: str
    kind: str
    raw_count: int | None
    output_path: Path
    compare_result: str
    status: str
    message: str = ""
    compare_differences: tuple[str, ...] = ()


ExtractionRunner = Callable[..., Path]
ManualValidationRunner = Callable[[Sequence[str]], int]
Comparer = Callable[[Path, Path], WorkbookCompareResult]


def build_validation_specs(
    *,
    suite_names: Sequence[str],
    base_dir: Path,
    output_root: Path,
    run_id: str,
    workers: int,
    parallel_mode: str = "process",
    data_dir_override: Path | None = None,
    settings_overrides: tuple[tuple[str, str], ...] = (),
) -> list[ValidationRunSpec]:
    settings_overrides = _normalize_settings_overrides(settings_overrides)
    run_root = output_root / run_id
    specs: list[ValidationRunSpec] = []
    for suite_name in _expand_suite_names(suite_names):
        if suite_name == "manual-2raw":
            output_dir = run_root / "manual_2raw"
            specs.append(
                ValidationRunSpec(
                    name=suite_name,
                    kind="manual_truth_validation",
                    description="Two standard RAW files scored against manual truth.",
                    output_dir=output_dir,
                    output_path=output_dir / "targeted_manual_validation.xlsx",
                    command=_manual_2raw_command(
                        output_dir=output_dir,
                        parallel_mode=parallel_mode,
                        workers=workers,
                    ),
                    expected_raw_count=2,
                    parallel_mode=parallel_mode,
                    workers=workers,
                )
            )
        elif suite_name == "tissue-fixture-cohort":
            data_dir = data_dir_override or DEFAULT_TISSUE_VALIDATION_DIR
            output_dir = run_root / "tissue_fixture_cohort"
            specs.append(
                ValidationRunSpec(
                    name=suite_name,
                    kind="extraction",
                    description="Daily tissue validation subset.",
                    output_dir=output_dir,
                    output_path=(
                        output_dir / f"xic_results_{parallel_mode}_w{workers}.xlsx"
                    ),
                    command=_harness_command(
                        suite_name=suite_name,
                        base_dir=base_dir,
                        output_root=output_root,
                        run_id=run_id,
                        parallel_mode=parallel_mode,
                        workers=workers,
                        data_dir=data_dir,
                        settings_overrides=settings_overrides,
                    ),
                    data_dir=data_dir,
                    expected_raw_count=8,
                    parallel_mode=parallel_mode,
                    workers=workers,
                    settings_overrides=settings_overrides,
                )
            )
        elif suite_name == "tissue-full-cohort":
            data_dir = data_dir_override or DEFAULT_FULL_TISSUE_DIR
            output_dir = run_root / "tissue_full_cohort"
            specs.append(
                ValidationRunSpec(
                    name=suite_name,
                    kind="extraction",
                    description="Full tissue release gate.",
                    output_dir=output_dir,
                    output_path=(
                        output_dir / f"xic_results_{parallel_mode}_w{workers}.xlsx"
                    ),
                    command=(
                        *_harness_command(
                            suite_name=suite_name,
                            base_dir=base_dir,
                            output_root=output_root,
                            run_id=run_id,
                            parallel_mode=parallel_mode,
                            workers=workers,
                            data_dir=data_dir,
                            settings_overrides=settings_overrides,
                        ),
                        "--confirm-full-run",
                    ),
                    data_dir=data_dir,
                    expected_raw_count=85,
                    parallel_mode=parallel_mode,
                    workers=workers,
                    settings_overrides=settings_overrides,
                    requires_confirmation=True,
                )
            )
    return specs


def _normalize_settings_overrides(
    settings_overrides: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    normalized: list[tuple[str, str]] = []
    for key, value in settings_overrides:
        normalized_key = key.strip()
        normalized_value = value.strip()
        if not normalized_key or not normalized_value:
            raise ValueError(
                "settings_overrides must contain non-empty KEY=VALUE pairs"
            )
        normalized.append((normalized_key, normalized_value))
    return tuple(normalized)


def command_to_powershell(command: Sequence[str]) -> str:
    return " ".join(
        _quote_powershell_arg(_powershell_path_text(part)) for part in command
    )


def _powershell_path_text(value: str) -> str:
    if "://" in value:
        return value
    windows_path = PureWindowsPath(value)
    if windows_path.drive:
        return str(windows_path)
    if "/" in value and not PurePosixPath(value).is_absolute():
        return value.replace("/", "\\")
    return value


def run_validation_specs(
    specs: Sequence[ValidationRunSpec],
    *,
    base_dir: Path,
    output_root: Path,
    run_id: str,
    baseline_root: Path | None = None,
    extraction_runner: ExtractionRunner = _run_extraction_once,
    manual_validation_runner: ManualValidationRunner | None = None,
    comparer: Comparer = compare_workbooks,
) -> list[ValidationRunResult]:
    if manual_validation_runner is None:
        manual_validation_runner = _run_manual_validation_command
    results: list[ValidationRunResult] = []
    for spec in specs:
        result = _run_one_spec(
            spec,
            base_dir=base_dir,
            output_root=output_root,
            baseline_root=baseline_root,
            extraction_runner=extraction_runner,
            manual_validation_runner=manual_validation_runner,
            comparer=comparer,
        )
        results.append(result)

    _write_validation_summary(
        output_root / run_id / "validation_summary.csv",
        specs,
        results,
    )
    return results


def _run_one_spec(
    spec: ValidationRunSpec,
    *,
    base_dir: Path,
    output_root: Path,
    baseline_root: Path | None,
    extraction_runner: ExtractionRunner,
    manual_validation_runner: ManualValidationRunner,
    comparer: Comparer,
) -> ValidationRunResult:
    raw_count = _raw_count(spec)
    if (
        spec.expected_raw_count is not None
        and raw_count is not None
        and raw_count != spec.expected_raw_count
    ):
        return ValidationRunResult(
            suite=spec.name,
            kind=spec.kind,
            raw_count=raw_count,
            output_path=spec.output_path,
            compare_result="not_run",
            status="failed",
            message=f"expected {spec.expected_raw_count} raw files, found {raw_count}",
        )

    try:
        if spec.kind == "manual_truth_validation":
            exit_code = manual_validation_runner(spec.command[4:])
            if exit_code != 0:
                return ValidationRunResult(
                    suite=spec.name,
                    kind=spec.kind,
                    raw_count=raw_count,
                    output_path=spec.output_path,
                    compare_result="manual_truth",
                    status="failed",
                    message=f"manual truth validation exited with {exit_code}",
                )
        elif spec.kind == "extraction":
            if spec.data_dir is None:
                raise ValueError(f"{spec.name}: extraction suite requires data_dir")
            workbook_path = extraction_runner(
                base_dir=base_dir,
                data_dir=spec.data_dir,
                mode=spec.parallel_mode,
                workers=spec.workers,
                output_dir=spec.output_dir,
                settings_overrides=dict(spec.settings_overrides),
            )
            if workbook_path != spec.output_path:
                raise ValueError(
                    f"{spec.name}: expected output {spec.output_path}, "
                    f"got {workbook_path}"
                )
        else:
            raise ValueError(f"Unknown validation kind: {spec.kind}")
    except (ConfigError, OSError, RawReaderError, ValueError) as exc:
        return ValidationRunResult(
            suite=spec.name,
            kind=spec.kind,
            raw_count=raw_count,
            output_path=spec.output_path,
            compare_result="not_run",
            status="failed",
            message=str(exc),
        )

    try:
        compare_result, differences = _compare_if_requested(
            spec,
            output_root=output_root,
            baseline_root=baseline_root,
            comparer=comparer,
        )
    except (OSError, ValueError) as exc:
        return ValidationRunResult(
            suite=spec.name,
            kind=spec.kind,
            raw_count=raw_count,
            output_path=spec.output_path,
            compare_result="fail",
            status="failed",
            message=str(exc),
        )
    return ValidationRunResult(
        suite=spec.name,
        kind=spec.kind,
        raw_count=raw_count,
        output_path=spec.output_path,
        compare_result=compare_result,
        status="passed" if compare_result != "fail" else "failed",
        compare_differences=differences,
    )


def _manual_2raw_command(
    *,
    output_dir: Path,
    parallel_mode: str,
    workers: int,
) -> tuple[str, ...]:
    return (
        "uv",
        "run",
        "python",
        r"scripts\targeted_manual_validation.py",
        "--manual-workbook",
        str(DEFAULT_MANUAL_WORKBOOK),
        "--nosplit-raw",
        str(DEFAULT_NOSPLIT_RAW),
        "--split-raw",
        str(DEFAULT_SPLIT_RAW),
        "--nosplit-targets",
        str(DEFAULT_NOSPLIT_TARGETS),
        "--split-targets",
        str(DEFAULT_SPLIT_TARGETS),
        "--output-dir",
        str(output_dir),
        "--parallel-mode",
        parallel_mode,
        "--parallel-workers",
        str(workers),
    )


def _harness_command(
    *,
    suite_name: str,
    base_dir: Path,
    output_root: Path,
    run_id: str,
    parallel_mode: str,
    workers: int,
    data_dir: Path,
    settings_overrides: tuple[tuple[str, str], ...] = (),
) -> tuple[str, ...]:
    command: tuple[str, ...] = (
        "uv",
        "run",
        "python",
        r"scripts\validation_harness.py",
        "--suite",
        suite_name,
        "--base-dir",
        str(base_dir),
        "--output-root",
        str(output_root),
        "--run-id",
        run_id,
        "--parallel-mode",
        parallel_mode,
        "--parallel-workers",
        str(workers),
        "--data-dir",
        str(data_dir),
    )
    for key, value in settings_overrides:
        command = (*command, "--setting", f"{key}={value}")
    return command


def _expand_suite_names(suite_names: Sequence[str]) -> tuple[str, ...]:
    expanded: list[str] = []
    for suite_name in suite_names:
        if suite_name == "all":
            expanded.extend(SUITE_CHOICES)
        else:
            expanded.append(suite_name)
    for suite_name in expanded:
        if suite_name not in SUITE_CHOICES:
            raise ValueError(f"Unknown validation suite: {suite_name}")
    return tuple(dict.fromkeys(expanded))


def _raw_count(spec: ValidationRunSpec) -> int | None:
    if spec.kind == "manual_truth_validation":
        return spec.expected_raw_count
    if spec.data_dir is None:
        return None
    return len(sorted(spec.data_dir.glob("*.raw")))


def _compare_if_requested(
    spec: ValidationRunSpec,
    *,
    output_root: Path,
    baseline_root: Path | None,
    comparer: Comparer,
) -> tuple[str, tuple[str, ...]]:
    if spec.kind == "manual_truth_validation":
        return "manual_truth", ()
    if baseline_root is None:
        return "not_requested", ()
    baseline_path = baseline_root / spec.output_path.relative_to(output_root)
    comparison = comparer(baseline_path, spec.output_path)
    return ("pass" if comparison.matched else "fail"), tuple(comparison.differences)


def _write_validation_summary(
    path: Path,
    specs: Sequence[ValidationRunSpec],
    results: Sequence[ValidationRunResult],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    specs_by_name = {spec.name: spec for spec in specs}
    fieldnames = [
        "suite",
        "kind",
        "raw_count",
        "output_path",
        "compare_result",
        "status",
        "message",
        "compare_differences",
        "command",
    ]
    rows = []
    for result in results:
        spec = specs_by_name[result.suite]
        rows.append(
            {
                "suite": result.suite,
                "kind": result.kind,
                "raw_count": "" if result.raw_count is None else result.raw_count,
                "output_path": str(result.output_path),
                "compare_result": result.compare_result,
                "status": result.status,
                "message": result.message,
                "compare_differences": " | ".join(result.compare_differences),
                "command": command_to_powershell(spec.command),
            }
        )
    write_delimited_rows(path, rows, fieldnames, delimiter=",", encoding="utf-8-sig")
    return path


def _run_manual_validation_command(argv: Sequence[str]) -> int:
    from scripts import targeted_manual_validation

    return targeted_manual_validation.main(argv)


def _quote_powershell_arg(value: str) -> str:
    if not value:
        return '""'
    if any(char.isspace() for char in value):
        return f'"{value}"'
    return value
