import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.compare_workbooks import WorkbookCompareResult, compare_workbooks
from xic_extractor import extractor
from xic_extractor.configuration.loader import load_config
from xic_extractor.configuration.models import ConfigError
from xic_extractor.output.excel_pipeline import write_excel_from_run_output
from xic_extractor.peak_detection.model_selection_approval_registry import (
    load_expected_diff_approval_registry,
)
from xic_extractor.raw_reader import RawReaderError
from xic_extractor.tabular_io import write_delimited_rows


@dataclass(frozen=True)
class BenchmarkRunSpec:
    mode: str
    workers: int
    output_dir: Path


@dataclass(frozen=True)
class BenchmarkRunResult:
    mode: str
    workers: int
    raw_count: int
    elapsed_seconds: float
    workbook_path: Path
    compare_result: str
    compare_differences: tuple[str, ...] = ()


Runner = Callable[..., Path]
Comparer = Callable[[Path, Path], WorkbookCompareResult]
Timer = Callable[[], float]


def build_run_specs(
    output_dir: Path, *, workers: Sequence[int]
) -> list[BenchmarkRunSpec]:
    specs = [BenchmarkRunSpec("serial", 1, output_dir / "serial_w1")]
    specs.extend(
        BenchmarkRunSpec(
            "process",
            worker_count,
            output_dir / f"process_w{worker_count}",
        )
        for worker_count in workers
    )
    return specs


def run_benchmark(
    *,
    base_dir: Path,
    data_dir: Path,
    workers: Sequence[int],
    output_dir: Path,
    runner: Runner | None = None,
    comparer: Comparer = compare_workbooks,
    timer: Timer = perf_counter,
) -> list[BenchmarkRunResult]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if runner is None:
        runner = _run_extraction_once
    raw_count = len(sorted(data_dir.glob("*.raw")))
    baseline_path: Path | None = None
    results: list[BenchmarkRunResult] = []

    for spec in build_run_specs(output_dir, workers=workers):
        spec.output_dir.mkdir(parents=True, exist_ok=True)
        started_at = timer()
        workbook_path = runner(
            base_dir=base_dir,
            data_dir=data_dir,
            mode=spec.mode,
            workers=spec.workers,
            output_dir=spec.output_dir,
        )
        elapsed_seconds = timer() - started_at

        compare_result = "baseline"
        differences: tuple[str, ...] = ()
        if baseline_path is None:
            baseline_path = workbook_path
        else:
            comparison = comparer(baseline_path, workbook_path)
            compare_result = "pass" if comparison.matched else "fail"
            differences = tuple(comparison.differences)

        results.append(
            BenchmarkRunResult(
                mode=spec.mode,
                workers=spec.workers,
                raw_count=raw_count,
                elapsed_seconds=elapsed_seconds,
                workbook_path=workbook_path,
                compare_result=compare_result,
                compare_differences=differences,
            )
        )

    _write_summary_csv(output_dir / "benchmark_summary.csv", results)
    return results


def _run_extraction_once(
    *,
    base_dir: Path,
    data_dir: Path,
    mode: str,
    workers: int,
    output_dir: Path,
    settings_overrides: Mapping[str, str] | None = None,
) -> Path:
    effective_overrides = {
        **dict(settings_overrides or {}),
        "data_dir": str(data_dir),
    }
    config, targets = load_config(
        base_dir / "config",
        settings_overrides=effective_overrides,
    )
    run_config = replace(
        config,
        data_dir=data_dir,
        output_csv=output_dir / "xic_results.csv",
        diagnostics_csv=output_dir / "xic_diagnostics.csv",
        parallel_mode=mode,
        parallel_workers=workers,
    )
    expected_diff_approvals = (
        load_expected_diff_approval_registry(
            run_config.model_selection_expected_diff_approval_registry
        )
        if run_config.model_selection_expected_diff_approval_registry is not None
        else None
    )
    output = extractor.acquire(
        run_config,
        targets,
        model_selection_expected_diff_approvals=expected_diff_approvals,
    )
    workbook_path = output_dir / f"xic_results_{mode}_w{workers}.xlsx"
    return write_excel_from_run_output(
        run_config,
        targets,
        output,
        output_path=workbook_path,
    )


def _write_summary_csv(path: Path, results: Sequence[BenchmarkRunResult]) -> Path:
    fieldnames = [
        "mode",
        "workers",
        "raw_count",
        "elapsed_seconds",
        "workbook_path",
        "compare_result",
        "compare_differences",
    ]
    rows = [
        {
            "mode": result.mode,
            "workers": result.workers,
            "raw_count": result.raw_count,
            "elapsed_seconds": f"{result.elapsed_seconds:.3f}",
            "workbook_path": str(result.workbook_path),
            "compare_result": result.compare_result,
            "compare_differences": " | ".join(result.compare_differences),
        }
        for result in results
    ]
    write_delimited_rows(path, rows, fieldnames, delimiter=",", encoding="utf-8-sig")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        results = run_benchmark(
            base_dir=args.base_dir.resolve(),
            data_dir=args.data_dir.resolve(),
            workers=args.workers,
            output_dir=args.output_dir.resolve(),
        )
    except (ConfigError, RawReaderError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for result in results:
        print(
            f"{result.mode} w{result.workers}: "
            f"{result.elapsed_seconds:.3f}s, compare={result.compare_result}, "
            f"workbook={result.workbook_path}"
        )
    return 1 if any(result.compare_result == "fail" for result in results) else 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark serial and process RAW extraction modes."
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path.cwd(),
        help="Project/base directory containing config/ and output/.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Validation subset directory containing .raw entries.",
    )
    parser.add_argument(
        "--workers",
        type=_parse_workers,
        default=(2, 4),
        help="Comma-separated process worker counts, for example 2,4.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/parallel_benchmark"),
        help="Directory for isolated benchmark outputs.",
    )
    return parser.parse_args(argv)


def _parse_workers(value: str) -> tuple[int, ...]:
    workers: list[int] = []
    for part in value.split(","):
        try:
            worker_count = int(part.strip())
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                "workers must be comma-separated integers"
            ) from exc
        if worker_count < 1:
            raise argparse.ArgumentTypeError("workers must be >= 1")
        workers.append(worker_count)
    return tuple(workers)


if __name__ == "__main__":
    raise SystemExit(main())
