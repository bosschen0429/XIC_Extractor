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
    write_settings(settings, config_dir=tmp_path)
    assert "old_unknown" in (tmp_path / "settings.csv").read_text()


def test_method_summary_names_strategy_without_revision():
    from xic_workbench.targeted import method_summary

    assert method_summary({}) == {
        "label": "共用 Targeted 策略",
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
    before = (tmp_path / "workspace.json").read_bytes()
    with pytest.raises(ConfigError):
        write_targets([{**row, "rt_min": "1"}], config_dir=tmp_path)
    assert (tmp_path / "workspace.json").read_bytes() == before


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
            "source_note": "keep me",
        }
    ]


def test_target_import_entrypoint_uses_explicit_targets_sheet(tmp_path):
    from tests.test_targeted_method_files import rows
    from xic_extractor.configuration.targeted_method_files import save_target_table
    from xic_workbench.target_import import import_targets

    source = tmp_path / "targets.xlsx"
    save_target_table(rows(), source)
    imported = import_targets(source)
    assert imported[0]["role"] == "istd"
    assert imported[1]["istd_pair"] == "standard"
    assert imported[1]["expected_rt_min"] == "2"


def test_excel_target_import_without_target_rows_is_rejected(tmp_path: Path) -> None:
    import openpyxl

    from xic_workbench.target_import import import_targets

    source = tmp_path / "no_targets.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.title = "Summary"
    workbook.save(source)
    workbook.close()

    with pytest.raises(ConfigError, match="工作表必須"):
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
        "xic_extractor.applications.targeted.application."
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
        "xic_extractor.applications.targeted.application."
        "build_canonical_targeted_application_service",
        lambda _runner: Service(),
    )
    run = module.TargetedRun(
        config_dir,
        progress=lambda _current, _total, _name: None,
        should_cancel=lambda: True,
    )

    with pytest.raises(InterruptedError, match="樣本資訊讀取已停止"):
        run.execute()

    assert run.available_outputs == ()
    assert run.cancellation_reason == "樣本資訊讀取已停止。"


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

    cancelled = False

    class Service:
        def execute_application(self, _request):
            nonlocal cancelled
            cancelled = True
            return SimpleNamespace(active_output=output, published_paths=(published,))

    _substitute_prepared_inputs(monkeypatch, module, config_dir, config, [target])
    monkeypatch.setattr(
        "xic_extractor.applications.targeted.application."
        "build_canonical_targeted_application_service",
        lambda _runner: Service(),
    )
    run = module.TargetedRun(
        config_dir,
        progress=lambda _current, _total, _name: None,
        should_cancel=lambda: cancelled,
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
        "xic_extractor.applications.targeted.application."
        "build_canonical_targeted_application_service",
        lambda _runner: Service(),
    )
    run = module.TargetedRun(
        tmp_path / "config",
        progress=lambda _current, _total, _name: None,
        should_cancel=lambda: False,
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
    config.dll_dir = config_dir.parent / "dll"
    config.injection_order_source = None
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


@pytest.mark.parametrize(
    "kind, extension",
    [("method", "xlsx"), ("targets", "xlsx"), ("targets", "csv"), ("targets", "tsv")],
)
def test_uploaded_files_detect_content_and_preserve_batch(tmp_path, kind, extension):
    import base64
    import csv

    from tests.test_targeted_method_files import rows
    from xic_extractor.configuration import targeted_method_files as files
    from xic_workbench.target_import import preview_target_upload

    path = tmp_path / f"arbitrary-name.{extension}"
    if kind == "method":
        files.save_targeted_method(files.create_targeted_method({}, rows()), path)
    elif extension == "xlsx":
        files.save_target_table(rows(), path)
    else:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(dict.fromkeys(key for row in rows() for key in row)),
                delimiter="\t" if extension == "tsv" else ",",
            )
            writer.writeheader()
            writer.writerows(rows())
    settings = {"data_dir": "current-raw", "dll_dir": "current-dll"}
    result = preview_target_upload(
        path.name, base64.b64encode(path.read_bytes()).decode(), settings
    )
    assert result["kind"] == kind
    assert [row["label"] for row in result["targets"]] == ["standard", "analyte"]
    assert result["targets"][1]["istd_pair"] == "standard"
    assert settings == {"data_dir": "current-raw", "dll_dir": "current-dll"}
    if kind == "method":
        assert result["settings"]["data_dir"] == "current-raw"
        assert result["settings"]["dll_dir"] == "current-dll"
    else:
        assert "settings" not in result


def test_uploaded_invalid_method_is_not_downgraded_to_target_table(tmp_path):
    import base64

    import openpyxl

    from tests.test_targeted_method_files import rows
    from xic_extractor.configuration.targeted_method_files import save_target_table
    from xic_workbench.target_import import preview_target_upload

    path = tmp_path / "broken.xlsx"
    save_target_table(rows(), path)
    book = openpyxl.load_workbook(path)
    book.create_sheet("Method").append(["invalid"])
    book.save(path)
    book.close()
    with pytest.raises(ConfigError):
        preview_target_upload(
            path.name, base64.b64encode(path.read_bytes()).decode(), {}
        )


@pytest.mark.parametrize(
    "name, content",
    [
        ("file.xls", "AA=="),
        ("file.xlsx", "!"),
        ("file.xlsx", "AA=="),
        ("file.csv", "YQ==" * 400000),
    ],
    ids=["extension", "base64", "workbook", "oversized"],
)
def test_upload_rejects_invalid_or_oversized_input(name, content):
    from xic_workbench.target_import import preview_target_upload

    with pytest.raises(ConfigError):
        preview_target_upload(name, content, {})
