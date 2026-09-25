import importlib
from pathlib import Path, PureWindowsPath

import pytest

from scripts.compare_workbooks import WorkbookCompareResult
from scripts.validation_harness import (
    DEFAULT_FULL_TISSUE_DIR,
    DEFAULT_TISSUE_VALIDATION_DIR,
    ValidationRunSpec,
    build_validation_specs,
    command_to_powershell,
    main,
    run_validation_specs,
)
from xic_extractor.config import ConfigError
from xic_extractor.raw_reader import RawReaderError


def test_validation_harness_defaults_use_env_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.validation_harness_core as core

    manual_root = tmp_path / "manual_truth"
    validation_root = tmp_path / "validation_fixture_cohort"
    full_root = tmp_path / "tissue_full_cohort"
    with monkeypatch.context() as env:
        env.setenv("XIC_MANUAL_2RAW_ROOT", str(manual_root))
        env.setenv("XIC_RAW_VALIDATION_DIR", str(validation_root))
        env.setenv("XIC_RAW_ROOT", str(full_root))
        importlib.reload(core)

        specs = core.build_validation_specs(
            suite_names=("manual-2raw", "tissue-fixture-cohort", "tissue-full-cohort"),
            base_dir=Path("C:/repo/XIC_Extractor"),
            output_root=tmp_path / "validation_harness",
            run_id="env_defaults",
            workers=4,
        )

        assert str(manual_root) in core.command_to_powershell(specs[0].command)
        assert str(validation_root) in core.command_to_powershell(specs[1].command)
        assert str(full_root) in core.command_to_powershell(specs[2].command)
    importlib.reload(core)


def test_validation_harness_defaults_fall_back_to_repo_placeholders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.validation_harness_core as core

    with monkeypatch.context() as env:
        env.delenv("XIC_MANUAL_2RAW_ROOT", raising=False)
        env.delenv("XIC_RAW_VALIDATION_DIR", raising=False)
        env.delenv("XIC_RAW_ROOT", raising=False)
        importlib.reload(core)

        specs = core.build_validation_specs(
            suite_names=("manual-2raw", "tissue-fixture-cohort", "tissue-full-cohort"),
            base_dir=Path("C:/repo/XIC_Extractor"),
            output_root=tmp_path / "validation_harness",
            run_id="fallback_defaults",
            workers=4,
        )

        commands = [core.command_to_powershell(spec.command) for spec in specs]
        assert "local_validation_artifacts\\manual_2raw" in commands[0]
        assert "local_validation_artifacts\\raw\\tissue_validation_8raw" in commands[1]
        assert "local_validation_artifacts\\raw\\tissue_85raw" in commands[2]
    importlib.reload(core)


def test_build_validation_specs_freezes_tiers_commands_and_output_paths(
    tmp_path: Path,
) -> None:
    specs = build_validation_specs(
        suite_names=("manual-2raw", "tissue-fixture-cohort", "tissue-full-cohort"),
        base_dir=Path("C:/repo/XIC_Extractor"),
        output_root=tmp_path / "validation_harness",
        run_id="20260506_test",
        workers=4,
    )

    assert [spec.name for spec in specs] == [
        "manual-2raw",
        "tissue-fixture-cohort",
        "tissue-full-cohort",
    ]
    assert specs[0].output_path == (
        tmp_path
        / "validation_harness"
        / "20260506_test"
        / "manual_2raw"
        / "targeted_manual_validation.xlsx"
    )
    assert specs[1].output_path == (
        tmp_path
        / "validation_harness"
        / "20260506_test"
        / "tissue_fixture_cohort"
        / "xic_results_process_w4.xlsx"
    )
    assert specs[2].requires_confirmation is True

    manual_command = command_to_powershell(specs[0].command)
    assert "scripts\\targeted_manual_validation.py" in manual_command
    assert "--parallel-mode process" in manual_command
    assert "--parallel-workers 4" in manual_command

    tissue_command = command_to_powershell(specs[1].command)
    assert str(PureWindowsPath(DEFAULT_TISSUE_VALIDATION_DIR)) in tissue_command
    assert "resolver" not in tissue_command
    assert "grid" not in tissue_command
    assert "--parallel-mode process" in tissue_command
    assert "--parallel-workers 4" in tissue_command

    full_command = command_to_powershell(specs[2].command)
    assert str(PureWindowsPath(DEFAULT_FULL_TISSUE_DIR)) in full_command
    assert "--confirm-full-run" in full_command


def test_build_validation_specs_includes_canonical_settings_in_commands(
    tmp_path: Path,
) -> None:
    specs = build_validation_specs(
        suite_names=("tissue-fixture-cohort",),
        base_dir=Path("C:/repo/XIC_Extractor"),
        output_root=tmp_path / "validation_harness",
        run_id="candidate",
        workers=4,
        settings_overrides=(("signal_processing_window_points", "21"),),
    )

    assert specs[0].settings_overrides == (
        ("signal_processing_window_points", "21"),
    )
    command = command_to_powershell(specs[0].command)
    assert "--setting signal_processing_window_points=21" in command


def test_run_validation_specs_compares_exact_baseline_workbook_paths(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "validation"
    data_dir.mkdir()
    for index in range(8):
        (data_dir / f"Sample{index + 1}.raw").mkdir()

    output_root = tmp_path / "validation_harness"
    spec = ValidationRunSpec(
        name="tissue-fixture-cohort",
        kind="extraction",
        description="8 raw subset",
        output_dir=output_root / "run1" / "tissue_fixture_cohort",
        output_path=(
            output_root
            / "run1"
            / "tissue_fixture_cohort"
            / "xic_results_process_w4.xlsx"
        ),
        command=("uv", "run", "python", "scripts/validate.py"),
        data_dir=data_dir,
        expected_raw_count=8,
        parallel_mode="process",
        workers=4,
    )
    baseline_root = tmp_path / "baseline"
    baseline_path = (
        baseline_root
        / "run1"
        / "tissue_fixture_cohort"
        / "xic_results_process_w4.xlsx"
    )
    runner_calls: list[dict[str, object]] = []
    compare_calls: list[tuple[Path, Path]] = []

    def fake_extraction_runner(**kwargs) -> Path:
        runner_calls.append(kwargs)
        return spec.output_path

    def fake_comparer(left: Path, right: Path) -> WorkbookCompareResult:
        compare_calls.append((left, right))
        return WorkbookCompareResult(matched=True, differences=[])

    results = run_validation_specs(
        [spec],
        base_dir=tmp_path,
        output_root=output_root,
        run_id="run1",
        baseline_root=baseline_root,
        extraction_runner=fake_extraction_runner,
        comparer=fake_comparer,
    )

    assert runner_calls == [
        {
            "base_dir": tmp_path,
            "data_dir": data_dir,
            "mode": "process",
            "workers": 4,
            "output_dir": spec.output_dir,
            "settings_overrides": {},
        }
    ]
    assert compare_calls == [(baseline_path, spec.output_path)]
    assert results[0].compare_result == "pass"
    summary_path = output_root / "run1" / "validation_summary.csv"
    raw_summary = summary_path.read_bytes()
    assert raw_summary.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw_summary
    summary = summary_path.read_text(encoding="utf-8-sig")
    assert summary.splitlines()[0].split(",") == [
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
    assert "suite,kind,raw_count,output_path,compare_result" in summary
    assert "tissue-fixture-cohort,extraction,8," in summary
    assert ",pass," in summary


def test_run_validation_specs_passes_canonical_settings_to_extraction_runner(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "validation"
    data_dir.mkdir()
    for index in range(8):
        (data_dir / f"Sample{index + 1}.raw").mkdir()

    output_root = tmp_path / "validation_harness"
    spec = ValidationRunSpec(
        name="tissue-fixture-cohort",
        kind="extraction",
        description="8 raw subset",
        output_dir=output_root / "run1" / "tissue_fixture_cohort",
        output_path=(
            output_root
            / "run1"
            / "tissue_fixture_cohort"
            / "xic_results_process_w4.xlsx"
        ),
        command=("uv", "run", "python", "scripts/validate.py"),
        data_dir=data_dir,
        expected_raw_count=8,
        parallel_mode="process",
        workers=4,
        settings_overrides=(("nl_min_intensity_ratio", "0.02"),),
    )
    runner_calls: list[dict[str, object]] = []

    def fake_extraction_runner(**kwargs) -> Path:
        runner_calls.append(kwargs)
        return spec.output_path

    results = run_validation_specs(
        [spec],
        base_dir=tmp_path,
        output_root=output_root,
        run_id="run1",
        extraction_runner=fake_extraction_runner,
    )

    assert results[0].status == "passed"
    assert runner_calls[0]["settings_overrides"] == {
        "nl_min_intensity_ratio": "0.02"
    }


def test_run_validation_specs_records_compare_errors_without_traceback(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "validation"
    data_dir.mkdir()
    for index in range(8):
        (data_dir / f"Sample{index + 1}.raw").mkdir()

    output_root = tmp_path / "validation_harness"
    spec = ValidationRunSpec(
        name="tissue-fixture-cohort",
        kind="extraction",
        description="8 raw subset",
        output_dir=output_root / "run1" / "tissue_fixture_cohort",
        output_path=(
            output_root
            / "run1"
            / "tissue_fixture_cohort"
            / "xic_results_process_w4.xlsx"
        ),
        command=("uv", "run", "python", "scripts/validate.py"),
        data_dir=data_dir,
        expected_raw_count=8,
        parallel_mode="process",
        workers=4,
    )

    def fake_extraction_runner(**_kwargs) -> Path:
        return spec.output_path

    def failing_comparer(_left: Path, _right: Path) -> WorkbookCompareResult:
        raise FileNotFoundError("baseline workbook is missing")

    results = run_validation_specs(
        [spec],
        base_dir=tmp_path,
        output_root=output_root,
        run_id="run1",
        baseline_root=tmp_path / "baseline",
        extraction_runner=fake_extraction_runner,
        comparer=failing_comparer,
    )

    assert results[0].status == "failed"
    assert results[0].compare_result == "fail"
    assert "baseline workbook is missing" in results[0].message


def test_run_validation_specs_records_baseline_path_errors_without_traceback(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "validation"
    data_dir.mkdir()
    for index in range(8):
        (data_dir / f"Sample{index + 1}.raw").mkdir()

    spec = ValidationRunSpec(
        name="tissue-fixture-cohort",
        kind="extraction",
        description="8 raw subset",
        output_dir=tmp_path / "outside",
        output_path=tmp_path / "outside" / "xic_results_process_w4.xlsx",
        command=("uv", "run", "python", "scripts/validate.py"),
        data_dir=data_dir,
        expected_raw_count=8,
        parallel_mode="process",
        workers=4,
    )

    results = run_validation_specs(
        [spec],
        base_dir=tmp_path,
        output_root=tmp_path / "validation_harness",
        run_id="run1",
        baseline_root=tmp_path / "baseline",
        extraction_runner=lambda **_kwargs: spec.output_path,
    )

    assert results[0].status == "failed"
    assert results[0].compare_result == "fail"
    assert "outside" in results[0].message


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (ConfigError("settings.csv missing"), "settings.csv missing"),
        (RawReaderError("pythonnet is not installed"), "pythonnet is not installed"),
    ],
)
def test_run_validation_specs_records_extraction_setup_errors_without_traceback(
    tmp_path: Path,
    error: Exception,
    expected_message: str,
) -> None:
    data_dir = tmp_path / "validation"
    data_dir.mkdir()
    for index in range(8):
        (data_dir / f"Sample{index + 1}.raw").mkdir()

    output_root = tmp_path / "validation_harness"
    spec = ValidationRunSpec(
        name="tissue-fixture-cohort",
        kind="extraction",
        description="8 raw subset",
        output_dir=output_root / "run1" / "tissue_fixture_cohort",
        output_path=(
            output_root
            / "run1"
            / "tissue_fixture_cohort"
            / "xic_results_process_w4.xlsx"
        ),
        command=("uv", "run", "python", "scripts/validate.py"),
        data_dir=data_dir,
        expected_raw_count=8,
        parallel_mode="process",
        workers=4,
    )

    def failing_extraction_runner(**_kwargs) -> Path:
        raise error

    results = run_validation_specs(
        [spec],
        base_dir=tmp_path,
        output_root=output_root,
        run_id="run1",
        extraction_runner=failing_extraction_runner,
    )

    assert results[0].status == "failed"
    assert results[0].compare_result == "not_run"
    assert expected_message in results[0].message
    summary = (output_root / "run1" / "validation_summary.csv").read_text(
        encoding="utf-8-sig"
    )
    assert expected_message in summary


def test_run_validation_specs_runs_manual_validation_with_script_arguments(
    tmp_path: Path,
) -> None:
    spec = build_validation_specs(
        suite_names=("manual-2raw",),
        base_dir=tmp_path,
        output_root=tmp_path / "validation_harness",
        run_id="run1",
        workers=4,
    )[0]
    validation_args: list[str] = []

    def fake_manual_validation_runner(argv) -> int:
        validation_args.extend(argv)
        return 0

    results = run_validation_specs(
        [spec],
        base_dir=tmp_path,
        output_root=tmp_path / "validation_harness",
        run_id="run1",
        manual_validation_runner=fake_manual_validation_runner,
    )

    assert results[0].status == "passed"
    assert validation_args[0] == "--manual-workbook"
    assert "--parallel-workers" in validation_args


def test_cli_accepts_repeated_canonical_settings_for_extraction_suite(
    tmp_path: Path, capsys
) -> None:
    exit_code = main(
        [
            "--suite",
            "tissue-fixture-cohort",
            "--setting",
            "signal_processing_window_points=21",
            "--setting",
            "nl_min_intensity_ratio=0.02",
            "--output-root",
            str(tmp_path / "validation_harness"),
            "--run-id",
            "candidate",
            "--dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "--setting signal_processing_window_points=21" in output
    assert "--setting nl_min_intensity_ratio=0.02" in output


def test_cli_strips_canonical_setting_values(tmp_path: Path, capsys) -> None:
    exit_code = main(
        [
            "--suite",
            "tissue-fixture-cohort",
            "--setting",
            "nl_min_intensity_ratio= 0.02 ",
            "--output-root",
            str(tmp_path / "validation_harness"),
            "--run-id",
            "candidate",
            "--dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "--setting nl_min_intensity_ratio=0.02" in output


@pytest.mark.parametrize(
    "setting",
    [
        "nl_min_intensity_ratio=",
        "nl_min_intensity_ratio=   ",
        "=0.02",
    ],
)
def test_cli_rejects_malformed_setting(tmp_path: Path, setting: str) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "--suite",
                "tissue-fixture-cohort",
                "--setting",
                setting,
                "--output-root",
                str(tmp_path / "validation_harness"),
                "--run-id",
                "candidate",
                "--dry-run",
            ]
        )


def test_run_validation_specs_rejects_wrong_raw_count(tmp_path: Path) -> None:
    data_dir = tmp_path / "validation"
    data_dir.mkdir()
    (data_dir / "OnlyOne.raw").mkdir()
    spec = ValidationRunSpec(
        name="tissue-fixture-cohort",
        kind="extraction",
        description="8 raw subset",
        output_dir=tmp_path / "out",
        output_path=tmp_path / "out" / "xic_results_process_w4.xlsx",
        command=("uv", "run", "python", "scripts/validate.py"),
        data_dir=data_dir,
        expected_raw_count=8,
        parallel_mode="process",
        workers=4,
    )

    results = run_validation_specs(
        [spec],
        base_dir=tmp_path,
        output_root=tmp_path / "validation_harness",
        run_id="run1",
        extraction_runner=lambda **_kwargs: spec.output_path,
    )

    assert results[0].status == "failed"
    assert "expected 8 raw files, found 1" in results[0].message


def test_cli_refuses_full85_without_confirmation(tmp_path: Path, capsys) -> None:
    exit_code = main(
        [
            "--suite",
            "tissue-full-cohort",
            "--output-root",
            str(tmp_path / "validation_harness"),
            "--run-id",
            "run1",
            "--dry-run",
        ]
    )

    assert exit_code == 2
    assert "requires --confirm-full-run" in capsys.readouterr().err
