from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xic_extractor.configuration.models import ConfigError, Target


def test_config_round_trip_preserves_target_precision_and_hidden_metadata(
    tmp_path: Path,
) -> None:
    from xic_workbench.config_io import read_targets, write_targets

    rows = [
        {
            "label": "Analyte",
            "mz": "258.108500",
            "rt_min": "8.000",
            "rt_max": "10.000",
            "ppm_tol": "20",
            "neutral_loss_da": "116.047400",
            "nl_ppm_warn": "20",
            "nl_ppm_max": "50",
            "is_istd": "false",
            "istd_pair": "d3-Analyte",
            "paired_rt_relation": "istd_not_later_than_pair",
            "source_note": "preserve me",
        }
    ]

    write_targets(rows, config_dir=tmp_path)

    restored = read_targets(config_dir=tmp_path)
    assert len(restored) == 1
    assert restored[0]["mz"] == "258.108500"
    assert restored[0]["neutral_loss_da"] == "116.047400"
    assert restored[0]["paired_rt_relation"] == "istd_not_later_than_pair"
    assert restored[0]["source_note"] == "preserve me"


def test_settings_round_trip_preserves_hidden_canonical_values(tmp_path: Path) -> None:
    from xic_workbench.config_io import read_settings, write_settings

    settings = {
        "data_dir": "C:\\RAW",
        "dll_dir": "C:\\Thermo",
        "signal_processing_method": "gaussian",
        "model_selection_expected_diff_approval_registry": "C:\\approval.tsv",
        "targeted_ms1_shape_identity_support_tsv": "C:\\support.tsv",
        "targeted_ms1_shape_identity_activation_policy": "explicit_support_tsv",
        "parallel_mode": "serial",
        "parallel_workers": "2",
    }

    write_settings(settings, config_dir=tmp_path)

    restored = read_settings(config_dir=tmp_path)
    assert {key: restored[key] for key in settings} == settings
    assert restored["signal_processing_window_points"] == "15"


def test_read_settings_migrates_legacy_keys_at_the_workbench_boundary(
    tmp_path: Path,
) -> None:
    from xic_workbench.config_io import read_settings, write_settings

    write_settings(
        {
            "data_dir": "C:\\RAW",
            "dll_dir": "C:\\Thermo",
            "ms1_morphology_smoothing_window_points": "21",
        },
        config_dir=tmp_path,
    )

    restored = read_settings(config_dir=tmp_path)

    assert restored["signal_processing_window_points"] == "21"
    assert "ms1_morphology_smoothing_window_points" not in restored
    assert restored["parallel_mode"] == "process"


def test_method_summary_normalizes_canonical_numeric_boolean_and_legacy_values(
) -> None:
    from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS
    from xic_workbench.targeted import method_summary

    settings = dict(CANONICAL_SETTINGS_DEFAULTS)
    settings.pop("signal_processing_window_points")
    settings["ms1_morphology_smoothing_window_points"] = "015"
    settings["ms2_precursor_tol_da"] = "1.10"
    settings["nl_min_intensity_ratio"] = ".01"
    settings["count_no_ms2_as_detected"] = "FALSE"
    settings["data_dir"] = "C:\\another-batch"
    settings["dll_dir"] = "C:\\another-install"

    assert method_summary(settings) == {
        "label": "標準方法",
        "custom_keys": [],
    }


def test_method_summary_labels_visible_and_hidden_nondefaults_as_custom() -> None:
    from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS
    from xic_workbench.targeted import method_summary

    settings = {
        **CANONICAL_SETTINGS_DEFAULTS,
        "ms2_precursor_tol_da": "0.7",
        "targeted_ms1_shape_identity_support_tsv": "C:\\reviewed-support.tsv",
    }

    assert method_summary(settings) == {
        "label": "自訂方法 · 2 項調整",
        "custom_keys": [
            "ms2_precursor_tol_da",
            "targeted_ms1_shape_identity_support_tsv",
        ],
    }


def test_failed_target_import_leaves_the_callers_rows_unchanged(tmp_path: Path) -> None:
    from xic_workbench.target_import import import_targets

    source = tmp_path / "malformed.csv"
    source.write_text("label,mz\nBroken\n", encoding="utf-8")
    current_rows = [{"label": "Keep", "mz": "250.100000"}]

    with pytest.raises(ConfigError, match="fewer values than columns"):
        import_targets(source)

    assert current_rows == [{"label": "Keep", "mz": "250.100000"}]


def test_target_import_entrypoint_preserves_draft_precision_and_hidden_metadata(
    tmp_path: Path,
) -> None:
    from xic_workbench.target_import import import_targets

    source = tmp_path / "targets.tsv"
    source.write_text(
        "label\tmz\tpaired_rt_relation\tsource_note\n"
        "Target A\t250.100000\tistd_not_later_than_pair\tkeep me\n",
        encoding="utf-8",
    )

    assert import_targets(source) == [
        {
            "label": "Target A",
            "mz": "250.100000",
            "paired_rt_relation": "istd_not_later_than_pair",
            "source_note": "keep me",
        }
    ]


def test_target_import_entrypoint_uses_the_existing_dna_workbook_contract(
    tmp_path: Path,
) -> None:
    import openpyxl

    from xic_workbench.target_import import import_targets

    source = tmp_path / "rt_check.xlsx"
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "DNA"
    worksheet.append(["m/z", "Anticipated adducts", "RT"])
    worksheet.append([258.1085, "5-hmdC", 8.0])
    worksheet.append([261.1273, "d3-5-hmdC", 8.1])
    workbook.save(source)
    workbook.close()

    rows = import_targets(source, sheet="DNA")

    assert rows[0] == {
        "label": "5-hmdC",
        "mz": "258.1085",
        "rt_min": "6.5",
        "rt_max": "9.5",
        "ppm_tol": "20",
        "neutral_loss_da": "116.0474",
        "nl_ppm_warn": "20",
        "nl_ppm_max": "50",
        "is_istd": "false",
        "istd_pair": "d3-5-hmdC",
    }
    assert rows[1]["label"] == "d3-5-hmdC"
    assert rows[1]["is_istd"] == "true"
    assert rows[1]["istd_pair"] == ""


def test_targeted_form_exposes_only_existing_controls_and_preserves_hidden_values() -> (
    None
):
    from xic_workbench.targeted import targeted_form

    form = targeted_form()
    fields = {field["key"]: field for field in form["settings"]}

    assert fields["data_dir"] == {
        "key": "data_dir",
        "label": "資料目錄",
        "kind": "folder",
        "group": "資料與環境",
    }
    assert fields["parallel_mode"]["options"] == ["serial", "process"]
    assert fields["signal_processing_window_points"]["min"] == 3
    assert fields["signal_processing_window_points"]["step"] == 2
    assert form["defaults"]["parallel_mode"] == "process"
    assert all(isinstance(value, str) for value in form["defaults"].values())
    assert (
        "model_selection_expected_diff_approval_registry"
        in form["passthrough_settings"]
    )
    assert "ms1_morphology_smoothing_window_points" not in form["defaults"]


def test_targeted_run_uses_canonical_service_and_returns_published_two_sample_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xic_workbench.targeted as module

    config_dir = tmp_path / "config"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    excel_path = output_dir / "xic_results_20261002_1200.xlsx"
    report_path = output_dir / "review_report_20261002_1200.html"
    excel_path.write_text("workbook", encoding="utf-8")
    report_path.write_text("report", encoding="utf-8")
    config = SimpleNamespace(
        output_csv=output_dir / "xic_results.csv",
        count_no_ms2_as_detected=True,
    )
    targets = [
        _target("Analyte", istd_pair="ISTD"),
        _target("ISTD", is_istd=True),
    ]
    output = SimpleNamespace(
        file_results=[
            SimpleNamespace(
                results={
                    "Analyte": _result(area=10.0, nl_status="OK", detected=True),
                    "ISTD": _result(area=20.0, nl_status="OK", detected=True),
                }
            ),
            SimpleNamespace(
                results={
                    "Analyte": _result(area=30.0, nl_status="NO_MS2", detected=False),
                    "ISTD": _result(area=60.0, nl_status="NL_FAIL", detected=False),
                }
            ),
        ],
        diagnostics=[object()],
    )
    progress_events: list[tuple[int, int, str]] = []
    captured: dict[str, object] = {}

    class Service:
        def execute_application(self, request):
            captured["request"] = request
            assert request.progress_callback is not None
            request.progress_callback(1, 2, "Sample_A.raw")
            return SimpleNamespace(
                active_output=output,
                published_paths=(excel_path, report_path),
            )

    monkeypatch.setattr(
        module.config_loader, "load_config", lambda actual: (config, targets)
    )
    monkeypatch.setattr(
        module,
        "build_canonical_targeted_application_service",
        lambda _runner: Service(),
    )
    run = module.TargetedRun(
        config_dir,
        progress=lambda current, total, name: progress_events.append(
            (current, total, name)
        ),
        should_cancel=lambda: False,
    )

    summary = run.execute()

    request = captured["request"]
    assert request.method_manifest_context.entrypoint == "xic-extractor-html"
    assert request.excel_output_path.parent == output_dir
    assert progress_events == [(1, 2, "Sample_A.raw")]
    assert summary["published_paths"] == [str(excel_path), str(report_path)]
    assert summary["total_files"] == 2
    assert summary["diagnostics_count"] == 1
    assert summary["targets"][0] == {
        "label": "Analyte",
        "detected": 1,
        "total": 2,
        "nl_ok": 1,
        "nl_warn": 0,
        "nl_fail": 0,
        "nl_no_ms2": 1,
        "median_area": 20.0,
    }
    assert summary["istd_warnings"] == [{"label": "ISTD", "detected": 1, "total": 2}]
    assert run.available_outputs == (
        ("已發布 Targeted 結果", excel_path),
        ("已發布 Targeted 結果", report_path),
    )


def test_targeted_run_raises_cancellation_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xic_workbench.targeted as module

    config_dir = tmp_path / "config"
    config = SimpleNamespace(
        output_csv=tmp_path / "output" / "xic_results.csv",
        count_no_ms2_as_detected=False,
    )

    class Service:
        def execute_application(self, _request):
            return SimpleNamespace(
                active_output=SimpleNamespace(file_results=[], diagnostics=[]),
                published_paths=(),
            )

    monkeypatch.setattr(
        module.config_loader, "load_config", lambda _actual: (config, [])
    )
    monkeypatch.setattr(
        module,
        "build_canonical_targeted_application_service",
        lambda _runner: Service(),
    )
    run = module.TargetedRun(
        config_dir,
        progress=lambda _current, _total, _name: None,
        should_cancel=lambda: True,
    )

    with pytest.raises(InterruptedError, match="使用者要求停止"):
        run.execute()

    assert run.available_outputs == ()


def test_targeted_run_keeps_success_when_cancellation_arrives_after_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xic_workbench.targeted as module

    config_dir = tmp_path / "config"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    published = output_dir / "xic_results_done.xlsx"
    published.write_text("done", encoding="utf-8")
    config = SimpleNamespace(
        output_csv=output_dir / "xic_results.csv",
        count_no_ms2_as_detected=False,
    )
    target = _target("Analyte")
    output = SimpleNamespace(
        file_results=[
            SimpleNamespace(
                results={"Analyte": _result(area=10.0, nl_status="OK", detected=True)}
            )
        ],
        diagnostics=[],
    )

    class Service:
        def execute_application(self, _request):
            return SimpleNamespace(active_output=output, published_paths=(published,))

    monkeypatch.setattr(
        module.config_loader, "load_config", lambda _actual: (config, [target])
    )
    monkeypatch.setattr(
        module,
        "build_canonical_targeted_application_service",
        lambda _runner: Service(),
    )
    run = module.TargetedRun(
        config_dir,
        progress=lambda _current, _total, _name: None,
        should_cancel=lambda: True,
    )

    summary = run.execute()

    assert summary["published_paths"] == [str(published)]
    assert summary["total_files"] == 1


def test_targeted_run_preserves_the_canonical_cancellation_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xic_workbench.targeted as module

    config = SimpleNamespace(
        output_csv=tmp_path / "output" / "xic_results.csv",
        count_no_ms2_as_detected=False,
    )

    class Service:
        def execute_application(self, _request):
            raise InterruptedError("fingerprinting stopped at Sample_B.raw")

    monkeypatch.setattr(
        module.config_loader, "load_config", lambda _actual: (config, [])
    )
    monkeypatch.setattr(
        module,
        "build_canonical_targeted_application_service",
        lambda _runner: Service(),
    )
    run = module.TargetedRun(
        tmp_path / "config",
        progress=lambda _current, _total, _name: None,
        should_cancel=lambda: True,
    )

    with pytest.raises(InterruptedError, match="fingerprinting stopped"):
        run.execute()

    assert run.cancellation_reason == "fingerprinting stopped at Sample_B.raw"


def _target(label: str, *, is_istd: bool = False, istd_pair: str = "") -> Target:
    return Target(
        label=label,
        mz=250.0,
        rt_min=1.0,
        rt_max=2.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=is_istd,
        istd_pair=istd_pair,
    )


def _result(*, area: float, nl_status: str, detected: bool) -> SimpleNamespace:
    return SimpleNamespace(
        nl=SimpleNamespace(status=nl_status),
        targeted_product_projection=SimpleNamespace(counted_detection=detected),
        peak_result=SimpleNamespace(status="OK", peak=object()),
        reported_peak_area=area,
    )
