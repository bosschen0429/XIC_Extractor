import csv
import subprocess
import sys
from pathlib import Path

from openpyxl import Workbook, load_workbook

from tools.diagnostics.targeted_manual_validation import (
    ManualTruthRow,
    ProgramPeakRow,
    read_manual_truth,
    run_manual_validation,
    score_manual_validation,
    write_validation_workbook,
)


def test_read_manual_truth_parses_dna_rna_two_raw_blocks(tmp_path: Path) -> None:
    workbook = tmp_path / "manual.xlsx"
    wb = Workbook()
    header_1 = [
        "No.",
        "Name",
        "m/z",
        "NoSplit",
        None,
        None,
        None,
        None,
        "Split",
        None,
        None,
        None,
        None,
    ]
    header_2 = [
        None,
        None,
        None,
        "RT\n(min)",
        "Peak height",
        "Peak area",
        "Peak width\n(min)",
        "Shape",
        "RT\n(min)",
        "Peak height",
        "Peak area",
        "Peak width\n(min)",
        "Shape",
    ]
    ws = wb.active
    ws.title = "DNA"
    ws.append(header_1)
    ws.append(header_2)
    ws.append(
        [
            1,
            "5-hmdC",
            258.1085,
            8.55,
            3430000,
            67300000,
            1.0,
            "正常",
            9.05,
            85900,
            2270000,
            0.95,
            "正常",
        ]
    )
    ws_rna = wb.create_sheet("RNA")
    ws_rna.append(header_1)
    ws_rna.append(header_2)
    ws_rna.append(
        [
            1,
            "m6A",
            282.1197,
            24.8,
            1000,
            20000,
            0.8,
            "正常",
            None,
            None,
            None,
            None,
            None,
        ]
    )
    wb.save(workbook)

    rows = read_manual_truth(workbook)

    assert [(row.sheet, row.sample_name, row.target) for row in rows] == [
        ("DNA", "NoSplit", "5-hmdC"),
        ("DNA", "Split", "5-hmdC"),
        ("RNA", "NoSplit", "m6A"),
    ]
    assert rows[0].manual_rt == 8.55
    assert rows[0].manual_area == 67300000
    assert rows[1].manual_width == 0.95
    assert rows[2].manual_shape == "正常"


def test_score_manual_validation_tracks_numeric_errors_and_guardrails() -> None:
    truth = [
        ManualTruthRow(
            "DNA",
            "SampleA",
            "ISTD",
            10.0,
            1000.0,
            10000.0,
            0.8,
            "正常",
        ),
        ManualTruthRow(
            "DNA",
            "SampleA",
            "Analyte",
            11.0,
            2000.0,
            20000.0,
            1.0,
            "正常",
        ),
        ManualTruthRow(
            "DNA",
            "SampleA",
            "Missing",
            12.0,
            3000.0,
            30000.0,
            1.0,
            "正常",
        ),
    ]
    peaks = [
        ProgramPeakRow("SampleA", "ISTD", True, 10.02, 900.0, 9000.0, True),
        ProgramPeakRow("SampleA", "Analyte", False, 11.10, 2300.0, 26000.0, True),
    ]

    score = score_manual_validation("canonical_targeted", {}, truth, peaks)

    assert score.area_median_abs_pct_error == 0.2
    assert score.area_within_10pct == 1
    assert score.area_within_20pct == 1
    assert score.missing_manual_peaks == 1
    assert score.istd_misses == 0
    assert score.rt_median_abs_delta_min == 0.06
    assert score.rt_max_abs_delta_min == 0.10
    assert score.large_area_misses == 1


def test_run_manual_validation_scores_one_current_pass() -> None:
    truth = [
        ManualTruthRow(
            "DNA",
            "SampleA",
            "TargetA",
            10.0,
            1000.0,
            10000.0,
            1.0,
            "正常",
        )
    ]
    calls = 0

    def fake_runner() -> list[ProgramPeakRow]:
        nonlocal calls
        calls += 1
        return [
            ProgramPeakRow(
                "SampleA",
                "TargetA",
                False,
                10.01,
                1000.0,
                12000.0,
                True,
            )
        ]

    result = run_manual_validation(truth, fake_runner)

    assert calls == 1
    assert result.score.name == "canonical_targeted"
    assert result.score.area_median_abs_pct_error == 0.2


def test_write_validation_workbook_contains_required_sheets(tmp_path: Path) -> None:
    truth = [
        ManualTruthRow(
            "DNA",
            "SampleA",
            "TargetA",
            10.0,
            1000.0,
            10000.0,
            1.0,
            "正常",
        )
    ]
    peaks = [ProgramPeakRow("SampleA", "TargetA", False, 10.01, 1000.0, 10000.0, True)]
    score = score_manual_validation(
        "canonical_targeted",
        {},
        truth,
        peaks,
    )
    output = tmp_path / "summary.xlsx"

    write_validation_workbook(output, [score])

    wb = load_workbook(output, read_only=True, data_only=True)
    assert wb.sheetnames == ["Summary", "PerTarget", "Failures", "RunConfig"]
    assert wb["Summary"]["A1"].value == "Name"


def test_write_settings_csv_preserves_staging_config_contract(tmp_path: Path) -> None:
    import tools.diagnostics.targeted_manual_validation as module

    settings_csv = tmp_path / "settings.csv"

    module._write_settings_csv(
        settings_csv,
        {
            "data_dir": "C:/data",
            "custom_setting": "abc",
        },
    )

    raw_settings = settings_csv.read_bytes()
    assert raw_settings.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw_settings
    with settings_csv.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert tuple(rows[0]) == ("key", "value", "description")
    assert [row["key"] for row in rows] == ["data_dir", "custom_setting"]
    assert rows[0]["value"] == "C:/data"
    assert rows[1]["description"] == ""


def test_write_validation_workbook_lists_per_target_failures(tmp_path: Path) -> None:
    truth = [
        ManualTruthRow(
            "DNA",
            "SampleA",
            "LargeError",
            10.0,
            1000.0,
            10000.0,
            1.0,
            "正常",
        ),
        ManualTruthRow(
            "DNA",
            "SampleA",
            "Missing",
            11.0,
            1200.0,
            12000.0,
            1.0,
            "正常",
        ),
    ]
    peaks = [
        ProgramPeakRow(
            "SampleA",
            "LargeError",
            False,
            10.25,
            1000.0,
            14000.0,
            True,
        )
    ]
    score = score_manual_validation(
        "canonical_targeted",
        {},
        truth,
        peaks,
    )
    output = tmp_path / "summary.xlsx"

    write_validation_workbook(output, [score])

    wb = load_workbook(output, read_only=True, data_only=True)
    per_target_rows = list(wb["PerTarget"].iter_rows(values_only=True))
    failure_rows = list(wb["Failures"].iter_rows(values_only=True))

    assert per_target_rows[0] == (
        "Name",
        "SampleName",
        "Target",
        "ISTD",
        "ManualRT",
        "ProgramRT",
        "RTAbsDeltaMin",
        "ManualHeight",
        "ProgramHeight",
        "HeightAbsPctError",
        "ManualArea",
        "ProgramArea",
        "AreaAbsPctError",
        "MissingPeak",
        "ManualShape",
    )
    assert [row[2] for row in per_target_rows[1:]] == ["LargeError", "Missing"]
    assert per_target_rows[2][13] is True
    assert failure_rows[1:] == [
        ("canonical_targeted", "SampleA", "LargeError", "RT_MAX_DELTA", 0.25, "<=0.20"),
        ("canonical_targeted", "SampleA", "LargeError", "AREA_ERROR", 0.4, "<=0.20"),
        (
            "canonical_targeted",
            "SampleA",
            "Missing",
            "MISSING_PEAK",
            None,
            "manual peak must be detected",
        ),
    ]


def test_run_cases_executes_each_case_once_with_current_config(
    tmp_path: Path, monkeypatch
) -> None:
    import tools.diagnostics.targeted_manual_validation as module

    cases = [
        module.ManualValidationCase(
            name=name,
            sample_name=name,
            raw_path=tmp_path / f"{name}.raw",
            targets_path=tmp_path / f"{name}.csv",
        )
        for name in ("nosplit", "split")
    ]
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_run_case(
        case: module.ManualValidationCase,
        _output_dir: Path,
        execution_overrides: dict[str, str],
    ) -> list[module.ProgramPeakRow]:
        calls.append((case.name, execution_overrides))
        return []

    monkeypatch.setattr(module, "_run_case", fake_run_case)
    overrides = {"parallel_mode": "process", "parallel_workers": "4"}

    assert module._run_cases(cases, tmp_path, overrides) == []
    assert calls == [("nosplit", overrides), ("split", overrides)]


def test_main_writes_workbook_with_injected_runner(tmp_path: Path, monkeypatch) -> None:
    import tools.diagnostics.targeted_manual_validation as module

    manual_workbook = tmp_path / "manual.xlsx"
    _write_manual_workbook(manual_workbook)
    nosplit_raw = tmp_path / "NoSplit.raw"
    split_raw = tmp_path / "Split.raw"
    nosplit_raw.write_text("raw", encoding="utf-8")
    split_raw.write_text("raw", encoding="utf-8")
    nosplit_targets = tmp_path / "targets1.csv"
    split_targets = tmp_path / "targets2.csv"
    nosplit_targets.write_text("label\nTargetA\n", encoding="utf-8")
    split_targets.write_text("label\nTargetA\n", encoding="utf-8")

    def fake_runner(
        cases: list[module.ManualValidationCase],
        _output_dir: Path,
        _execution_overrides: dict[str, str],
    ) -> list[module.ProgramPeakRow]:
        return [
            module.ProgramPeakRow(
                case.sample_name,
                "TargetA",
                False,
                10.01,
                1000.0,
                12000.0,
                True,
            )
            for case in cases
        ]

    monkeypatch.setattr(module, "_run_cases", fake_runner)
    output_dir = tmp_path / "validation"

    exit_code = module.main(
        [
            "--manual-workbook",
            str(manual_workbook),
            "--nosplit-raw",
            str(nosplit_raw),
            "--split-raw",
            str(split_raw),
            "--nosplit-targets",
            str(nosplit_targets),
            "--split-targets",
            str(split_targets),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert (output_dir / "targeted_manual_validation.xlsx").exists()


def test_main_applies_parallel_overrides_to_one_current_run(
    tmp_path: Path, monkeypatch
) -> None:
    import tools.diagnostics.targeted_manual_validation as module

    manual_workbook = tmp_path / "manual.xlsx"
    _write_manual_workbook(manual_workbook)
    nosplit_raw = tmp_path / "NoSplit.raw"
    split_raw = tmp_path / "Split.raw"
    nosplit_raw.write_text("raw", encoding="utf-8")
    split_raw.write_text("raw", encoding="utf-8")
    nosplit_targets = tmp_path / "targets1.csv"
    split_targets = tmp_path / "targets2.csv"
    nosplit_targets.write_text("label\nTargetA\n", encoding="utf-8")
    split_targets.write_text("label\nTargetA\n", encoding="utf-8")
    seen_overrides: list[dict[str, str]] = []

    def fake_runner(
        cases: list[module.ManualValidationCase],
        _output_dir: Path,
        execution_overrides: dict[str, str],
    ) -> list[module.ProgramPeakRow]:
        seen_overrides.append(execution_overrides)
        return [
            module.ProgramPeakRow(
                case.sample_name,
                "TargetA",
                False,
                10.01,
                1000.0,
                10000.0,
                True,
            )
            for case in cases
        ]

    monkeypatch.setattr(module, "_run_cases", fake_runner)

    exit_code = module.main(
        [
            "--manual-workbook",
            str(manual_workbook),
            "--nosplit-raw",
            str(nosplit_raw),
            "--split-raw",
            str(split_raw),
            "--nosplit-targets",
            str(nosplit_targets),
            "--split-targets",
            str(split_targets),
            "--output-dir",
            str(tmp_path / "validation"),
            "--parallel-mode",
            "process",
            "--parallel-workers",
            "4",
        ]
    )

    assert exit_code == 0
    assert seen_overrides == [{"parallel_mode": "process", "parallel_workers": "4"}]


def test_script_file_exposes_cli_help() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "tools/diagnostics/targeted_manual_validation.py",
            "--help",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert (
        "Compare the current Targeted extractor with manual truth" in completed.stdout
    )
    assert "resolver" not in completed.stdout
    assert "grid" not in completed.stdout


def _write_manual_workbook(path: Path) -> None:
    wb = Workbook()
    header_1 = [
        "No.",
        "Name",
        "m/z",
        "NoSplit",
        None,
        None,
        None,
        None,
        "Split",
        None,
        None,
        None,
        None,
    ]
    header_2 = [
        None,
        None,
        None,
        "RT\n(min)",
        "Peak height",
        "Peak area",
        "Peak width\n(min)",
        "Shape",
        "RT\n(min)",
        "Peak height",
        "Peak area",
        "Peak width\n(min)",
        "Shape",
    ]
    ws = wb.active
    ws.title = "DNA"
    ws.append(header_1)
    ws.append(header_2)
    ws.append(
        [
            1,
            "TargetA",
            100.0,
            10.0,
            1000,
            10000,
            1.0,
            "正常",
            10.0,
            1000,
            10000,
            1.0,
            "正常",
        ]
    )
    ws_rna = wb.create_sheet("RNA")
    ws_rna.append(header_1)
    ws_rna.append(header_2)
    wb.save(path)
