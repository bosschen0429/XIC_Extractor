from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xic_extractor.configuration.models import ConfigError, Target


def test_config_round_trip_preserves_target_precision_and_hidden_metadata(tmp_path):
    from xic_workbench.config_io import read_targets, write_targets

    rows = [
        dict(
            label="standard",
            mz="258.108500",
            expected_rt_min="8.000",
            cid_nl_tag="dR",
            role="istd",
            istd_pair="",
            source_note="preserve me",
        )
    ]
    write_targets(rows, config_dir=tmp_path)
    restored = read_targets(config_dir=tmp_path)
    assert restored[0]["mz"] == "258.108500"
    assert restored[0]["expected_rt_min"] == "8.000"
    assert restored[0]["source_note"] == "preserve me"


def test_settings_round_trip_rejects_hidden_science_without_migrating(tmp_path):
    from xic_workbench.config_io import read_settings, write_settings

    settings = {
        "data_dir": "C:/RAW",
        "dll_dir": "C:/Thermo",
        "injection_order_source": "",
        "parallel_workers": "",
    }
    write_settings(settings, config_dir=tmp_path)
    with pytest.raises(ConfigError, match="dirty_matrix_mode"):
        write_settings({**settings, "dirty_matrix_mode": "true"}, config_dir=tmp_path)
    assert read_settings(config_dir=tmp_path) == settings
    (tmp_path / "settings.csv").write_text(
        "key,value\nold_unknown,keep-not-effective\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="old_unknown"):
        write_settings(settings, config_dir=tmp_path)
    assert "old_unknown" in (tmp_path / "settings.csv").read_text()


def test_method_summary_names_strategy_without_revision():
    from xic_workbench.targeted import method_summary

    assert method_summary({}) == {
        "label": "共用 CID-NL 策略",
        "custom_keys": [],
        "differences": [],
    }


def test_invalid_targets_leave_saved_input_unchanged(tmp_path):
    from xic_workbench.config_io import write_targets

    row = dict(
        label="standard",
        mz="258",
        expected_rt_min="8",
        cid_nl_tag="dR",
        role="istd",
        istd_pair="",
    )
    write_targets([row], config_dir=tmp_path)
    before = (tmp_path / "targets.csv").read_bytes()
    with pytest.raises(ConfigError):
        write_targets([{**row, "rt_min": "1"}], config_dir=tmp_path)
    assert (tmp_path / "targets.csv").read_bytes() == before


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
        "label\tmz\tsource_note\nTarget A\t250.100000\tkeep me\n",
        encoding="utf-8",
    )

    assert import_targets(source) == [
        {
            "label": "Target A",
            "mz": "250.100000",
            "role": "analyte",
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
        "expected_rt_min": "8.0",
        "cid_nl_tag": "dR",
        "role": "analyte",
        "istd_pair": "d3-5-hmdC",
    }
    assert rows[1]["label"] == "d3-5-hmdC"
    assert rows[1]["role"] == "istd"
    assert rows[1]["istd_pair"] == ""


def test_excel_target_import_without_target_rows_is_rejected(tmp_path: Path) -> None:
    import openpyxl

    from xic_workbench.target_import import import_targets

    source = tmp_path / "no_targets.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.title = "Summary"
    workbook.save(source)
    workbook.close()

    with pytest.raises(ConfigError, match="no target rows"):
        import_targets(source)


def test_targeted_fields_are_batch_and_machine_only():
    from xic_workbench.targeted import setting_fields

    fields = {field["key"]: field for field in setting_fields()}
    assert set(fields) == {
        "data_dir",
        "dll_dir",
        "injection_order_source",
        "parallel_workers",
    }
    assert fields["data_dir"]["scope"] == "batch"
    assert fields["dll_dir"]["scope"] == "environment"
    assert fields["parallel_workers"]["scope"] == "runtime"
    assert fields["parallel_workers"]["default"] == ""
    assert fields["parallel_workers"]["min"] == 1


def test_targeted_run_uses_canonical_service_and_returns_published_two_sample_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xic_workbench.targeted as module

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.csv").write_text("settings", encoding="utf-8")
    (config_dir / "targets.csv").write_text("targets", encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    excel_path = output_dir / "xic_results_20261002_1200.xlsx"
    report_path = output_dir / "review_report_20261002_1200.html"
    excel_path.write_text("workbook", encoding="utf-8")
    report_path.write_text("report", encoding="utf-8")
    config = SimpleNamespace(
        output_csv=output_dir / "xic_results.csv",
        count_no_ms2_as_detected=True,
        targeted_ms1_shape_identity_activation_policy="explicit_support_tsv",
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

    _substitute_prepared_inputs(monkeypatch, module, config_dir, config, targets)
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
    config_dir.mkdir()
    (config_dir / "settings.csv").write_text("settings", encoding="utf-8")
    (config_dir / "targets.csv").write_text("targets", encoding="utf-8")
    config = SimpleNamespace(
        output_csv=tmp_path / "output" / "xic_results.csv",
        count_no_ms2_as_detected=False,
        targeted_ms1_shape_identity_activation_policy="explicit_support_tsv",
    )

    class Service:
        def execute_application(self, _request):
            return SimpleNamespace(
                active_output=SimpleNamespace(file_results=[], diagnostics=[]),
                published_paths=(),
            )

    _substitute_prepared_inputs(monkeypatch, module, config_dir, config, [])
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
    config_dir.mkdir()
    (config_dir / "settings.csv").write_text("settings", encoding="utf-8")
    (config_dir / "targets.csv").write_text("targets", encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    published = output_dir / "xic_results_done.xlsx"
    published.write_text("done", encoding="utf-8")
    config = SimpleNamespace(
        output_csv=output_dir / "xic_results.csv",
        count_no_ms2_as_detected=False,
        targeted_ms1_shape_identity_activation_policy="explicit_support_tsv",
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

    _substitute_prepared_inputs(monkeypatch, module, config_dir, config, [target])
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

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.csv").write_text("settings", encoding="utf-8")
    (config_dir / "targets.csv").write_text("targets", encoding="utf-8")
    config = SimpleNamespace(
        output_csv=tmp_path / "output" / "xic_results.csv",
        count_no_ms2_as_detected=False,
        targeted_ms1_shape_identity_activation_policy="explicit_support_tsv",
    )

    class Service:
        def execute_application(self, _request):
            raise InterruptedError("fingerprinting stopped at Sample_B.raw")

    _substitute_prepared_inputs(monkeypatch, module, config_dir, config, [])
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


def _substitute_prepared_inputs(monkeypatch, module, config_dir, config, targets):
    """Substitute the admission interface, not parsing internals.

    These adapter tests start with admitted inputs. The neighboring snapshot
    tests use real CSV preparation and verify frozen calculation/manifest bytes.
    RAW membership still crosses the real discovery/check interface here.
    """
    from xic_extractor.configuration.loader import PreparedTargetedConfig

    config.data_dir = config_dir.parent / "raw"
    config.data_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(
        module.config_loader,
        "prepare_targeted_config",
        lambda *_args, **_kwargs: PreparedTargetedConfig(
            config, tuple(targets), config_dir
        ),
    )


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
