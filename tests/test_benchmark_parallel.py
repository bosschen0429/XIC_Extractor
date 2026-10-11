import csv
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.benchmark_parallel import (
    _run_extraction_once,
    build_run_specs,
    run_benchmark,
)
from scripts.compare_workbooks import WorkbookCompareResult
from xic_extractor.configuration.models import ExtractionConfig


def test_build_run_specs_uses_expected_mode_worker_matrix_and_output_dirs(
    tmp_path: Path,
) -> None:
    specs = build_run_specs(tmp_path / "parallel_benchmark", workers=(2, 4))

    assert [(spec.mode, spec.workers) for spec in specs] == [
        ("serial", 1),
        ("process", 2),
        ("process", 4),
    ]
    assert [spec.output_dir.name for spec in specs] == [
        "serial_w1",
        "process_w2",
        "process_w4",
    ]


def test_run_benchmark_compares_exact_returned_workbook_paths(tmp_path: Path) -> None:
    data_dir = tmp_path / "validation"
    data_dir.mkdir()
    for name in ("A.raw", "B.raw", "C.raw"):
        (data_dir / name).mkdir()
    returned_paths: dict[tuple[str, int], Path] = {}
    runner_calls: list[tuple[str, int, Path]] = []
    compare_calls: list[tuple[Path, Path]] = []

    def fake_runner(
        *,
        base_dir: Path,
        data_dir: Path,
        mode: str,
        workers: int,
        output_dir: Path,
    ) -> Path:
        runner_calls.append((mode, workers, output_dir))
        workbook_path = output_dir / f"actual_{mode}_w{workers}.xlsx"
        returned_paths[(mode, workers)] = workbook_path
        return workbook_path

    def fake_compare(left: Path, right: Path) -> WorkbookCompareResult:
        compare_calls.append((left, right))
        return WorkbookCompareResult(matched=True, differences=[])

    results = run_benchmark(
        base_dir=tmp_path,
        data_dir=data_dir,
        workers=(2, 4),
        output_dir=tmp_path / "parallel_benchmark",
        runner=fake_runner,
        comparer=fake_compare,
        timer=_fake_timer(),
    )

    assert runner_calls == [
        ("serial", 1, tmp_path / "parallel_benchmark" / "serial_w1"),
        ("process", 2, tmp_path / "parallel_benchmark" / "process_w2"),
        ("process", 4, tmp_path / "parallel_benchmark" / "process_w4"),
    ]
    assert compare_calls == [
        (returned_paths[("serial", 1)], returned_paths[("process", 2)]),
        (returned_paths[("serial", 1)], returned_paths[("process", 4)]),
    ]
    assert [(result.mode, result.workers, result.raw_count) for result in results] == [
        ("serial", 1, 3),
        ("process", 2, 3),
        ("process", 4, 3),
    ]
    assert [result.compare_result for result in results] == [
        "baseline",
        "pass",
        "pass",
    ]


def test_run_benchmark_writes_summary_csv(tmp_path: Path) -> None:
    data_dir = tmp_path / "validation"
    data_dir.mkdir()
    (data_dir / "A.raw").mkdir()

    def fake_runner(
        *,
        base_dir: Path,
        data_dir: Path,
        mode: str,
        workers: int,
        output_dir: Path,
    ) -> Path:
        return output_dir / f"{mode}_w{workers}.xlsx"

    run_benchmark(
        base_dir=tmp_path,
        data_dir=data_dir,
        workers=(2,),
        output_dir=tmp_path / "parallel_benchmark",
        runner=fake_runner,
        comparer=lambda _left, _right: WorkbookCompareResult(
            matched=False,
            differences=["XIC Results!R2C3: 1 != 2"],
        ),
        timer=_fake_timer(),
    )

    summary_path = tmp_path / "parallel_benchmark" / "benchmark_summary.csv"
    raw_summary = summary_path.read_bytes()
    assert raw_summary.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw_summary
    summary = summary_path.read_text(encoding="utf-8-sig")
    assert summary.splitlines()[0].split(",") == [
        "mode",
        "workers",
        "raw_count",
        "elapsed_seconds",
        "workbook_path",
        "compare_result",
        "compare_differences",
    ]

    assert (
        "mode,workers,raw_count,elapsed_seconds,workbook_path,compare_result" in summary
    )
    assert "serial,1,1,1.000," in summary
    assert "baseline" in summary
    assert "process,2,1,2.000," in summary
    assert "fail" in summary
    assert "XIC Results!R2C3: 1 != 2" in summary


def test_run_extraction_once_applies_data_dir_override_before_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "config"
    validation_dir = tmp_path / "validation"
    dll_dir = tmp_path / "dll"
    validation_dir.mkdir()
    dll_dir.mkdir()
    _write_benchmark_settings(
        config_dir,
        data_dir=tmp_path / "placeholder_missing",
        dll_dir=dll_dir,
    )
    _write_benchmark_targets(config_dir)
    captured_configs: list[ExtractionConfig] = []
    captured: dict[str, object] = {}

    def fake_run(config, targets, *, model_selection_expected_diff_approvals=None):
        captured_configs.append(config)
        captured["targets"] = targets
        captured["approvals"] = model_selection_expected_diff_approvals
        return object()

    monkeypatch.setattr("scripts.benchmark_parallel.extractor.acquire", fake_run)
    monkeypatch.setattr(
        "scripts.benchmark_parallel.write_excel_from_run_output",
        lambda _config, _targets, _output, *, output_path: output_path,
    )

    workbook_path = _run_extraction_once(
        base_dir=tmp_path,
        data_dir=validation_dir,
        mode="serial",
        workers=1,
        output_dir=tmp_path / "out",
    )

    assert captured_configs[0].data_dir == validation_dir
    assert captured["approvals"] is None
    assert workbook_path == tmp_path / "out" / "xic_results_serial_w1.xlsx"


def test_run_extraction_once_applies_additional_settings_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "config"
    validation_dir = tmp_path / "validation"
    dll_dir = tmp_path / "dll"
    validation_dir.mkdir()
    dll_dir.mkdir()
    _write_benchmark_settings(
        config_dir,
        data_dir=tmp_path / "placeholder_missing",
        dll_dir=dll_dir,
    )
    _write_benchmark_targets(config_dir)
    captured_configs: list[ExtractionConfig] = []
    captured: dict[str, object] = {}

    def fake_run(config, targets, *, model_selection_expected_diff_approvals=None):
        captured_configs.append(config)
        captured["targets"] = targets
        captured["approvals"] = model_selection_expected_diff_approvals
        return object()

    monkeypatch.setattr("scripts.benchmark_parallel.extractor.acquire", fake_run)
    monkeypatch.setattr(
        "scripts.benchmark_parallel.write_excel_from_run_output",
        lambda _config, _targets, _output, *, output_path: output_path,
    )

    _run_extraction_once(
        base_dir=tmp_path,
        data_dir=validation_dir,
        mode="process",
        workers=4,
        output_dir=tmp_path / "out",
        settings_overrides={"resolver_mode": "local_minimum"},
    )

    assert captured_configs[0].data_dir == validation_dir
    assert captured_configs[0].resolver_mode == "local_minimum"
    assert captured_configs[0].parallel_mode == "process"
    assert captured_configs[0].parallel_workers == 4
    assert captured["approvals"] is None


def test_benchmark_parallel_script_help_runs_from_documented_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [sys.executable, "scripts/benchmark_parallel.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Benchmark serial and process RAW extraction modes." in completed.stdout


@pytest.mark.parametrize("workers", ["0", "bad"])
def test_benchmark_parallel_rejects_invalid_workers(
    tmp_path: Path, workers: str
) -> None:
    import scripts.benchmark_parallel as module

    with pytest.raises(SystemExit) as exc_info:
        module.main(["--data-dir", str(tmp_path), "--workers", workers])

    assert exc_info.value.code == 2


def _fake_timer():
    values = iter([0.0, 1.0, 1.0, 3.0, 3.0, 6.0])
    return lambda: next(values)


def _write_benchmark_settings(
    config_dir: Path, *, data_dir: Path, dll_dir: Path
) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    rows = {
        "data_dir": str(data_dir),
        "dll_dir": str(dll_dir),
        "smooth_window": "15",
        "smooth_polyorder": "3",
        "peak_rel_height": "0.95",
        "peak_min_prominence_ratio": "0.10",
        "ms2_precursor_tol_da": "0.5",
        "nl_min_intensity_ratio": "0.01",
        "count_no_ms2_as_detected": "false",
    }
    with (config_dir / "settings.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=["key", "value", "description"])
        writer.writeheader()
        for key, value in rows.items():
            writer.writerow({"key": key, "value": value, "description": key})


def _write_benchmark_targets(config_dir: Path) -> None:
    fieldnames = [
        "label",
        "mz",
        "rt_min",
        "rt_max",
        "ppm_tol",
        "neutral_loss_da",
        "nl_ppm_warn",
        "nl_ppm_max",
        "is_istd",
        "istd_pair",
    ]
    with (config_dir / "targets.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "label": "Analyte",
                "mz": "258.1085",
                "rt_min": "8.0",
                "rt_max": "10.0",
                "ppm_tol": "20",
                "neutral_loss_da": "116.0474",
                "nl_ppm_warn": "20",
                "nl_ppm_max": "50",
                "is_istd": "false",
                "istd_pair": "",
            }
        )
