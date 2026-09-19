import csv
import subprocess
import sys
from pathlib import Path

from openpyxl import Workbook, load_workbook

from scripts.local_minimum_param_sweep import (
    ManualTruthRow,
    ParameterSet,
    ProgramPeakRow,
    build_parameter_sets,
    read_manual_truth,
    run_sweep,
    score_parameter_set,
    write_sweep_workbook,
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


def test_score_parameter_set_ranks_by_area_mape_and_tracks_guardrails() -> None:
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

    score = score_parameter_set("candidate", {}, truth, peaks)

    assert score.area_median_abs_pct_error == 0.2
    assert score.area_within_10pct == 1
    assert score.area_within_20pct == 1
    assert score.missing_manual_peaks == 1
    assert score.istd_misses == 0
    assert score.rt_median_abs_delta_min == 0.06
    assert score.rt_max_abs_delta_min == 0.10
    assert score.large_area_misses == 1


def test_build_parameter_sets_includes_legacy_current_and_candidate_grid() -> None:
    parameter_sets = build_parameter_sets(grid="quick")

    names = [item.name for item in parameter_sets]
    assert names[0] == "legacy_savgol"
    assert names[1] == "local_minimum_current"
    assert any(name.startswith("local_minimum_grid_") for name in names)
    assert parameter_sets[0].settings_overrides["resolver_mode"] == "legacy_savgol"
    assert parameter_sets[1].settings_overrides["resolver_mode"] == "local_minimum"


def test_calibration_grid_focuses_duration_and_search() -> None:
    parameter_sets = build_parameter_sets(grid="calibration-duration-search")

    by_name = {item.name: item.settings_overrides for item in parameter_sets}

    assert list(by_name)[:2] == ["legacy_savgol", "local_minimum_current"]
    assert by_name["local_minimum_duration_2p0"]["resolver_peak_duration_max"] == "2.0"
    assert (
        by_name["local_minimum_search_0p05_duration_2p0"][
            "resolver_min_search_range_min"
        ]
        == "0.05"
    )
    assert (
        by_name["local_minimum_search_0p05"]["resolver_peak_duration_max"]
        == "2.0"
    )
    assert (
        by_name["local_minimum_search_0p04_duration_2p0"][
            "resolver_min_search_range_min"
        ]
        == "0.04"
    )
    assert all(
        settings.get("resolver_mode") in {"legacy_savgol", "local_minimum"}
        for settings in by_name.values()
    )
    assert len(parameter_sets) <= 12


def test_calibration_grid_focuses_min_duration_and_height() -> None:
    parameter_sets = build_parameter_sets(grid="calibration-duration-height")

    by_name = {item.name: item.settings_overrides for item in parameter_sets}

    assert list(by_name)[:2] == ["legacy_savgol", "local_minimum_current"]
    assert (
        by_name["local_minimum_current"]["resolver_min_relative_height"]
        == "0.02"
    )
    assert (
        by_name["local_minimum_rel_height_0p00"][
            "resolver_min_relative_height"
        ]
        == "0.0"
    )
    assert (
        by_name["local_minimum_min_duration_0p02"][
            "resolver_peak_duration_min"
        ]
        == "0.02"
    )
    assert (
        by_name["local_minimum_rel_height_0p01"][
            "resolver_min_relative_height"
        ]
        == "0.01"
    )
    assert (
        by_name["local_minimum_min_duration_0p02_rel_height_0p01"][
            "resolver_peak_duration_min"
        ]
        == "0.02"
    )
    assert (
        by_name["local_minimum_min_duration_0p02_rel_height_0p01"][
            "resolver_min_relative_height"
        ]
        == "0.01"
    )
    assert all(
        settings.get("resolver_peak_duration_max") == "2.0"
        for name, settings in by_name.items()
        if name.startswith("local_minimum_") and name != "local_minimum_current"
    )
    assert len(parameter_sets) <= 12


def test_run_sweep_scores_each_parameter_set_with_injected_runner() -> None:
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
    parameter_sets = [
        ParameterSet("legacy_savgol", {"resolver_mode": "legacy_savgol"}),
        ParameterSet("local_minimum_current", {"resolver_mode": "local_minimum"}),
    ]

    def fake_runner(parameter_set: ParameterSet) -> list[ProgramPeakRow]:
        area = 10000.0 if parameter_set.name == "legacy_savgol" else 12000.0
        return [
            ProgramPeakRow(
                "SampleA",
                "TargetA",
                False,
                10.01,
                1000.0,
                area,
                True,
            )
        ]

    result = run_sweep(truth, parameter_sets, fake_runner)

    assert [score.name for score in result.scores] == [
        "legacy_savgol",
        "local_minimum_current",
    ]
    assert result.scores[0].area_median_abs_pct_error == 0.0
    assert result.scores[1].area_median_abs_pct_error == 0.2


def test_write_sweep_workbook_contains_required_sheets(tmp_path: Path) -> None:
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
    peaks = [
        ProgramPeakRow("SampleA", "TargetA", False, 10.01, 1000.0, 10000.0, True)
    ]
    score = score_parameter_set(
        "legacy_savgol",
        {"resolver_mode": "legacy_savgol"},
        truth,
        peaks,
    )
    output = tmp_path / "summary.xlsx"

    write_sweep_workbook(output, [score])

    wb = load_workbook(output, read_only=True, data_only=True)
    assert wb.sheetnames == ["Summary", "PerTarget", "Failures", "RunConfig"]
    assert wb["Summary"]["A1"].value == "Rank"


def test_write_settings_csv_preserves_staging_config_contract(tmp_path: Path) -> None:
    import scripts.local_minimum_param_sweep as module

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


def test_write_sweep_workbook_lists_per_target_failures(tmp_path: Path) -> None:
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
    score = score_parameter_set(
        "candidate",
        {"resolver_mode": "local_minimum"},
        truth,
        peaks,
    )
    output = tmp_path / "summary.xlsx"

    write_sweep_workbook(output, [score])

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
        ("candidate", "SampleA", "LargeError", "RT_MAX_DELTA", 0.25, "<=0.20"),
        ("candidate", "SampleA", "LargeError", "AREA_ERROR", 0.4, "<=0.20"),
        (
            "candidate",
            "SampleA",
            "Missing",
            "MISSING_PEAK",
            None,
            "manual peak must be detected",
        ),
    ]


def test_main_writes_workbook_with_injected_runner(
    tmp_path: Path, monkeypatch
) -> None:
    import scripts.local_minimum_param_sweep as module

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
        parameter_set: module.ParameterSet,
        cases: list[module.SweepCase],
        _output_dir: Path,
    ) -> list[module.ProgramPeakRow]:
        area = 10000.0 if parameter_set.name == "legacy_savgol" else 12000.0
        return [
            module.ProgramPeakRow(
                case.sample_name,
                "TargetA",
                False,
                10.01,
                1000.0,
                area,
                True,
            )
            for case in cases
        ]

    monkeypatch.setattr(module, "_run_parameter_set", fake_runner)
    output_dir = tmp_path / "sweep"

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
            "--grid",
            "quick",
        ]
    )

    assert exit_code == 0
    assert (output_dir / "local_minimum_param_sweep_summary.xlsx").exists()


def test_main_applies_parallel_overrides_to_every_parameter_set(
    tmp_path: Path, monkeypatch
) -> None:
    import scripts.local_minimum_param_sweep as module

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
        parameter_set: module.ParameterSet,
        cases: list[module.SweepCase],
        _output_dir: Path,
    ) -> list[module.ProgramPeakRow]:
        seen_overrides.append(parameter_set.settings_overrides)
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

    monkeypatch.setattr(module, "_run_parameter_set", fake_runner)

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
            str(tmp_path / "sweep"),
            "--grid",
            "quick",
            "--parallel-mode",
            "process",
            "--parallel-workers",
            "4",
        ]
    )

    assert exit_code == 0
    assert seen_overrides
    assert all(settings["parallel_mode"] == "process" for settings in seen_overrides)
    assert all(settings["parallel_workers"] == "4" for settings in seen_overrides)


def test_script_file_exposes_cli_help() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/local_minimum_param_sweep.py",
            "--help",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Sweep local_minimum resolver parameters" in completed.stdout


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
