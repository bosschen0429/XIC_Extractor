import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from xic_extractor.applications.targeted.application import TargetedApplicationExecution
from xic_extractor.applications.targeted.output_boundary import (
    TargetedOutputBoundaryError,
)
from xic_extractor.configuration.loader import PreparedTargetedConfig
from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
from xic_extractor.extraction.result_models import RunOutput
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.raw_reader_errors import RawReaderError


@pytest.fixture(autouse=True)
def _isolate_cli_shell_from_production_workflow(monkeypatch):
    """Keep CLI option tests focused; production composition has dedicated tests."""

    module = _module()

    class _Service:
        def execute_application(self, request):
            output = module.extractor.run(
                request.config,
                list(request.targets),
                progress_callback=request.progress_callback,
                should_stop=request.should_stop,
                injection_order=request.injection_order,
            )
            if request.excel_output_path is not None:
                module.write_excel_from_run_output(
                    request.config,
                    list(request.targets),
                    output,
                    output_path=request.excel_output_path,
                )
            return TargetedApplicationExecution(active_output=output)

    monkeypatch.setattr(
        module,
        "write_excel_from_run_output",
        lambda *_args, output_path, **_kwargs: output_path,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "build_canonical_targeted_application_service",
        lambda _runner: _Service(),
    )


def test_cli_runs_extraction_with_base_dir_and_workbook(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    config = _config(tmp_path)
    targets = [_target("Analyte")]
    calls: dict[str, object] = {}

    def _load_config(config_dir: Path, **kwargs):
        calls["config_dir"] = config_dir
        return PreparedTargetedConfig(config, tuple(targets), config_dir)

    monkeypatch.setattr(module, "prepare_targeted_config", _load_config)
    monkeypatch.setattr(module.extractor, "run", _fake_run(calls))
    monkeypatch.setattr(
        module,
        "write_excel_from_run_output",
        lambda *_args, **_kwargs: calls.setdefault("excel", True),
        raising=False,
    )

    exit_code = module.main(["--base-dir", str(tmp_path)])

    assert exit_code == 0
    assert calls["config_dir"] == tmp_path / "config"
    assert calls["run_targets"] == targets
    assert calls["excel"] is True
    stdout = capsys.readouterr().out
    assert "Processed files: 2" in stdout
    assert "Diagnostics: 1" in stdout
    assert "1/2 SampleA.raw" in stdout


def test_cli_runs_excel_conversion_by_default(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import scripts.csv_to_excel as csv_to_excel

    module = _module()
    config = _config(tmp_path)
    targets = [_target("Analyte")]
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        module,
        "prepare_targeted_config",
        lambda config_dir, **kwargs: PreparedTargetedConfig(
            config, tuple(targets), config_dir
        ),
    )
    monkeypatch.setattr(module.extractor, "run", _fake_run(calls))
    monkeypatch.setattr(
        csv_to_excel,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("csv_to_excel.run should not be called")
        ),
    )
    monkeypatch.setattr(
        module,
        "write_excel_from_run_output",
        lambda excel_config, excel_targets, run_output, *, output_path: calls.update(
            {
                "excel_config": excel_config,
                "excel_targets": excel_targets,
                "excel_run_output": run_output,
                "excel_output_path": output_path,
            }
        ),
        raising=False,
    )

    exit_code = module.main(["--base-dir", str(tmp_path)])

    assert exit_code == 0
    assert calls["excel_config"] is config
    assert calls["excel_targets"] == targets
    assert calls["excel_run_output"] is calls["run_output"]
    output_path = calls["excel_output_path"]
    assert isinstance(output_path, Path)
    assert output_path.parent == tmp_path / "output"
    assert re.fullmatch(r"xic_results_\d{8}_\d{4}\.xlsx", output_path.name)
    assert "Excel skipped" not in capsys.readouterr().out


def test_cli_accepts_data_dir_override_for_validation_subset(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    config = _config(tmp_path)
    targets = [_target("Analyte")]
    validation_dir = tmp_path / "validation"
    validation_dir.mkdir()
    calls: dict[str, object] = {}

    def fake_load_config(_config_dir, *, settings_overrides=None, **kwargs):
        calls["settings_overrides"] = settings_overrides
        loaded_config = (
            replace(config, data_dir=Path(settings_overrides["data_dir"]))
            if settings_overrides
            else config
        )
        return PreparedTargetedConfig(loaded_config, tuple(targets), _config_dir)

    monkeypatch.setattr(module, "prepare_targeted_config", fake_load_config)
    monkeypatch.setattr(module.extractor, "run", _fake_run(calls))
    monkeypatch.setattr(
        module,
        "write_excel_from_run_output",
        lambda excel_config, excel_targets, run_output, *, output_path: calls.update(
            {
                "excel_config": excel_config,
                "excel_targets": excel_targets,
                "excel_run_output": run_output,
                "excel_output_path": output_path,
            }
        ),
        raising=False,
    )

    exit_code = module.main(
        ["--base-dir", str(tmp_path), "--data-dir", str(validation_dir)]
    )

    assert exit_code == 0
    assert calls["run_config"].data_dir == validation_dir.resolve()
    assert calls["run_config"].output_csv == config.output_csv
    assert calls["excel_config"] is calls["run_config"]
    assert calls["settings_overrides"] == {"data_dir": str(validation_dir.resolve())}
    assert "Excel skipped" not in capsys.readouterr().out


def test_cli_applies_data_dir_override_before_real_config_validation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    config_dir = tmp_path / "config"
    validation_dir = tmp_path / "validation"
    dll_dir = tmp_path / "dll"
    validation_dir.mkdir()
    dll_dir.mkdir()
    _write_cli_config(
        config_dir,
        data_dir=tmp_path / "placeholder_missing",
        dll_dir=dll_dir,
    )
    calls: dict[str, object] = {}

    monkeypatch.setattr(module.extractor, "run", _fake_run(calls))
    monkeypatch.setattr(
        module,
        "write_excel_from_run_output",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    exit_code = module.main(
        ["--base-dir", str(tmp_path), "--data-dir", str(validation_dir)]
    )

    assert exit_code == 0
    assert calls["run_config"].data_dir == validation_dir.resolve()
    assert "settings.csv" not in capsys.readouterr().err


def test_cli_does_not_migrate_old_csv_inputs(tmp_path, capsys):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "targets.csv"
    path.write_text("legacy input left untouched", encoding="utf-8")
    assert _module().main(["--base-dir", str(tmp_path)]) == 2
    assert "at least one target row" in capsys.readouterr().err
    assert path.read_text() == "legacy input left untouched"
    assert not (config_dir / "workspace.json").exists()


@pytest.mark.parametrize(
    "args", [["--parallel-mode", "process"], ["--parallel-workers", "2"]]
)
def test_cli_rejects_retired_execution_overrides(args, capsys):
    with pytest.raises(SystemExit) as exc:
        _module().main(args)
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_cli_preserves_loaded_parallel_settings_when_overrides_are_omitted(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    config = replace(_config(tmp_path), parallel_mode="process", parallel_workers=4)
    targets = [_target("Analyte")]
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        module,
        "prepare_targeted_config",
        lambda config_dir, **kwargs: PreparedTargetedConfig(
            config, tuple(targets), config_dir
        ),
    )
    monkeypatch.setattr(module.extractor, "run", _fake_run(calls))
    monkeypatch.setattr(
        module,
        "write_excel_from_run_output",
        lambda *_args, **_kwargs: calls.setdefault("excel", True),
        raising=False,
    )

    exit_code = module.main(["--base-dir", str(tmp_path)])

    assert exit_code == 0
    assert calls["run_config"].parallel_mode == "process"
    assert calls["run_config"].parallel_workers == 4
    assert "Processed files: 2" in capsys.readouterr().out


def test_cli_reports_config_errors_without_traceback(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    monkeypatch.setattr(
        module,
        "prepare_targeted_config",
        lambda _config_dir, **kwargs: (_ for _ in ()).throw(
            ConfigError("workspace.json invalid")
        ),
    )

    exit_code = module.main(["--base-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "workspace.json invalid" in captured.err
    assert "Traceback" not in captured.err


def test_cli_reports_publication_rejections_without_traceback(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    config = _config(tmp_path)
    targets = [_target("Analyte")]
    monkeypatch.setattr(
        module,
        "prepare_targeted_config",
        lambda config_dir, **kwargs: PreparedTargetedConfig(
            config, tuple(targets), config_dir
        ),
    )

    class _RejectingService:
        def execute_application(self, _request):
            raise TargetedOutputBoundaryError("output already exists")

    monkeypatch.setattr(
        module,
        "build_canonical_targeted_application_service",
        lambda _runner: _RejectingService(),
    )

    exit_code = module.main(["--base-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "output already exists" in captured.err
    assert "Traceback" not in captured.err


def test_cli_reports_reader_setup_errors_without_traceback(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    config = _config(tmp_path)
    targets = [_target("Analyte")]
    monkeypatch.setattr(
        module,
        "prepare_targeted_config",
        lambda config_dir, **kwargs: PreparedTargetedConfig(
            config, tuple(targets), config_dir
        ),
    )
    monkeypatch.setattr(
        module.extractor,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RawReaderError("pythonnet is not installed")
        ),
    )

    exit_code = module.main(["--base-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "pythonnet is not installed" in captured.err
    assert "Traceback" not in captured.err


def _module():
    import scripts.run_extraction as module

    return module


def _fake_run(calls: dict[str, object]):
    def _run(
        config,
        targets,
        progress_callback=None,
        should_stop=None,
        injection_order=None,
    ):
        calls["run_config"] = config
        calls["run_targets"] = targets
        calls["should_stop"] = should_stop
        calls["injection_order"] = injection_order

        if progress_callback is not None:
            progress_callback(1, 2, "SampleA.raw")
        output = RunOutput(
            file_results=[
                SimpleNamespace(sample_name="SampleA"),
                SimpleNamespace(sample_name="SampleB"),
            ],
            diagnostics=[
                DiagnosticRecord(
                    sample_name="SampleB",
                    target_label="Analyte",
                    issue="PEAK_NOT_FOUND",
                    reason="No peak",
                )
            ],
        )
        calls["run_output"] = output
        return output

    return _run


def _config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_csv=tmp_path / "output" / "xic_results.csv",
        diagnostics_csv=tmp_path / "output" / "xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _target(label: str) -> Target:
    return Target(
        label=label,
        mz=258.1085,
        rt_min=8.0,
        rt_max=10.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=False,
        istd_pair="",
    )


def _write_cli_config(config_dir: Path, *, data_dir: Path, dll_dir: Path) -> None:
    from xic_extractor.configuration.workspace_state import save_workspace

    save_workspace(
        config_dir,
        settings={"data_dir": str(data_dir), "dll_dir": str(dll_dir)},
        targets=[
            dict(
                label="Analyte",
                mz="258.1085",
                expected_rt_min="9.0",
                cid_nl_tag="dR",
                role="istd",
                istd_pair="",
            )
        ],
    )


def test_cli_reusable_method_freezes_inputs_only_until_run_finishes(
    tmp_path, monkeypatch
):
    from xic_extractor.configuration.targeted_method_files import (
        create_targeted_method,
        save_targeted_method,
    )

    module = _module()
    raw_dir = tmp_path / "raw"
    new_batch = tmp_path / "new-batch"
    dll_dir = tmp_path / "dll"
    for folder in (raw_dir, new_batch, dll_dir):
        folder.mkdir()
    _write_cli_config(tmp_path / "config", data_dir=raw_dir, dll_dir=dll_dir)
    method_dir = tmp_path / "method.xlsx"
    target_rows = [
        dict(
            label="MethodTarget",
            mz="258.1085",
            expected_rt_min="9.0",
            cid_nl_tag="dR",
            role="istd",
            istd_pair="",
        )
    ]
    save_targeted_method(create_targeted_method({}, target_rows), method_dir)
    calls = {}
    fake_run = _fake_run(calls)

    prepare = module.prepare_targeted_config

    def record_frozen_inputs(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        calls["frozen_inputs"] = prepared.input_config_dir
        return prepared

    def run_and_change_active_sources(config, targets, **kwargs):
        method_dir.write_text("edited during run", encoding="utf-8")
        (tmp_path / "config" / "workspace.json").write_text(
            "edited during run", encoding="utf-8"
        )
        return fake_run(config, targets, **kwargs)

    monkeypatch.setattr(module, "prepare_targeted_config", record_frozen_inputs)
    monkeypatch.setattr(module.extractor, "run", run_and_change_active_sources)
    assert (
        module.main(
            [
                "--base-dir",
                str(tmp_path),
                "--method",
                str(method_dir),
                "--data-dir",
                str(new_batch),
            ]
        )
        == 0
    )
    config = calls["run_config"]
    assert config.data_dir == new_batch
    assert config.dll_dir == dll_dir
    assert config.nl_min_intensity_ratio == 0.01
    assert config.parallel_workers >= 1
    assert [target.label for target in calls["run_targets"]] == ["MethodTarget"]
    assert not calls["frozen_inputs"].exists()
    assert not (tmp_path / "output" / ".run_inputs").exists()
