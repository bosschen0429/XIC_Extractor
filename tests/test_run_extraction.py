import csv
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from xic_extractor.applications.targeted import limited_identity_workflow
from xic_extractor.applications.targeted.application import TargetedApplicationExecution
from xic_extractor.applications.targeted.output_boundary import (
    TargetedOutputBoundaryError,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
from xic_extractor.extraction.result_models import RunOutput
from xic_extractor.extraction.targeted_inputs import (
    resolve_model_selection_expected_diff_approvals,
)
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.peak_detection.model_selection import ExpectedDiffApprovalRecord
from xic_extractor.raw_reader import RawReaderError


@pytest.fixture(autouse=True)
def _isolate_cli_shell_from_production_workflow(monkeypatch):
    """Keep CLI option tests focused; production composition has dedicated tests."""

    module = _module()

    class _Service:
        def execute_application(self, request):
            approvals = resolve_model_selection_expected_diff_approvals(
                request.config,
                request.model_selection_expected_diff_approvals,
            )
            output = module.extractor.run(
                request.config,
                list(request.targets),
                progress_callback=request.progress_callback,
                should_stop=request.should_stop,
                injection_order=request.injection_order,
                model_selection_expected_diff_approvals=approvals,
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

    def _load_config(config_dir: Path) -> tuple[ExtractionConfig, list[Target]]:
        calls["config_dir"] = config_dir
        return config, targets

    monkeypatch.setattr(module, "load_config", _load_config)
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
    assert calls["run_config"].keep_intermediate_csv is False
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

    monkeypatch.setattr(module, "load_config", lambda _config_dir: (config, targets))
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

    def fake_load_config(_config_dir, *, settings_overrides=None):
        calls["settings_overrides"] = settings_overrides
        loaded_config = (
            replace(config, data_dir=Path(settings_overrides["data_dir"]))
            if settings_overrides
            else config
        )
        return loaded_config, targets

    monkeypatch.setattr(module, "load_config", fake_load_config)
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


def test_cli_accepts_example_defaults_when_runtime_config_is_missing(
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
    (config_dir / "settings.csv").rename(config_dir / "settings.example.csv")
    (config_dir / "targets.csv").rename(config_dir / "targets.example.csv")
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
    assert calls["run_targets"][0].label == "Analyte"
    assert not (config_dir / "settings.csv").exists()
    assert not (config_dir / "targets.csv").exists()
    assert capsys.readouterr().err == ""


def test_cli_accepts_parallel_execution_overrides(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    config = _config(tmp_path)
    targets = [_target("Analyte")]
    calls: dict[str, object] = {}

    monkeypatch.setattr(module, "load_config", lambda _config_dir: (config, targets))
    monkeypatch.setattr(module.extractor, "run", _fake_run(calls))
    monkeypatch.setattr(
        module,
        "write_excel_from_run_output",
        lambda *_args, **_kwargs: calls.setdefault("excel", True),
        raising=False,
    )

    exit_code = module.main(
        [
            "--base-dir",
            str(tmp_path),
            "--parallel-mode",
            "process",
            "--parallel-workers",
            "4",
        ]
    )

    assert exit_code == 0
    assert calls["run_config"].parallel_mode == "process"
    assert calls["run_config"].parallel_workers == 4
    assert calls["excel"] is True
    assert "Processed files: 2" in capsys.readouterr().out


def test_cli_rejects_invalid_parallel_mode(capsys) -> None:
    module = _module()

    with pytest.raises(SystemExit) as exc_info:
        module.main(["--parallel-mode", "thread"])

    assert exc_info.value.code == 2
    assert "invalid choice: 'thread'" in capsys.readouterr().err


@pytest.mark.parametrize("workers", ["0", "-1"])
def test_cli_rejects_invalid_parallel_workers(workers: str, capsys) -> None:
    module = _module()

    with pytest.raises(SystemExit) as exc_info:
        module.main(["--parallel-workers", workers])

    assert exc_info.value.code == 2
    assert "parallel-workers must be >= 1" in capsys.readouterr().err


def test_cli_preserves_loaded_parallel_settings_when_overrides_are_omitted(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    config = replace(_config(tmp_path), parallel_mode="process", parallel_workers=4)
    targets = [_target("Analyte")]
    calls: dict[str, object] = {}

    monkeypatch.setattr(module, "load_config", lambda _config_dir: (config, targets))
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


def test_cli_loads_model_selection_expected_diff_approval_registry(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _module()
    config = _config(tmp_path)
    targets = [_target("Analyte")]
    registry_path = tmp_path / "expected_diff_approvals.tsv"
    registry_path.write_text("stub\n", encoding="utf-8")
    approval = _expected_diff_approval()
    calls: dict[str, object] = {}

    def _load_approvals(path: Path):
        calls["approval_registry_path"] = path
        return {approval.stable_row_id: approval}

    def fake_load_config(_config_dir: Path, *, settings_overrides=None):
        calls["settings_overrides"] = settings_overrides
        loaded_config = replace(
            config,
            model_selection_expected_diff_approval_registry=Path(
                settings_overrides["model_selection_expected_diff_approval_registry"]
            ),
        )
        return loaded_config, targets

    monkeypatch.setattr(module, "load_config", fake_load_config)
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_inputs.load_expected_diff_approval_registry",
        _load_approvals,
    )
    monkeypatch.setattr(module.extractor, "run", _fake_run(calls))
    monkeypatch.setattr(
        module,
        "write_excel_from_run_output",
        lambda *_args, **_kwargs: calls.setdefault("excel", True),
        raising=False,
    )

    exit_code = module.main(
        [
            "--base-dir",
            str(tmp_path),
            "--model-selection-expected-diff-approvals",
            str(registry_path),
        ]
    )

    assert exit_code == 0
    assert calls["settings_overrides"] == {
        "model_selection_expected_diff_approval_registry": str(registry_path.resolve())
    }
    assert calls["approval_registry_path"] == registry_path.resolve()
    assert calls["model_selection_expected_diff_approvals"] == {
        approval.stable_row_id: approval
    }
    assert "Processed files: 2" in capsys.readouterr().out


def test_cli_passes_targeted_ms1_shape_identity_support_override(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _module()
    config = replace(
        _config(tmp_path),
        targeted_ms1_shape_identity_activation_policy="limited_5hmdc_5medc",
    )
    targets = [_target("Analyte")]
    support_path = tmp_path / "targeted_ms1_shape_identity.tsv"
    support_path.write_text("stub\n", encoding="utf-8")
    calls: dict[str, object] = {}

    def fake_load_config(_config_dir: Path, *, settings_overrides=None):
        calls["settings_overrides"] = settings_overrides
        loaded_config = replace(
            config,
            targeted_ms1_shape_identity_support_tsv=Path(
                settings_overrides["targeted_ms1_shape_identity_support_tsv"]
            ),
            targeted_ms1_shape_identity_activation_policy=settings_overrides[
                "targeted_ms1_shape_identity_activation_policy"
            ],
        )
        return loaded_config, targets

    monkeypatch.setattr(module, "load_config", fake_load_config)
    monkeypatch.setattr(module.extractor, "run", _fake_run(calls))
    monkeypatch.setattr(
        module,
        "write_excel_from_run_output",
        lambda *_args, **_kwargs: calls.setdefault("excel", True),
        raising=False,
    )

    exit_code = module.main(
        [
            "--base-dir",
            str(tmp_path),
            "--targeted-ms1-shape-identity-support-tsv",
            str(support_path),
        ]
    )

    assert exit_code == 0
    assert calls["settings_overrides"] == {
        "targeted_ms1_shape_identity_support_tsv": str(support_path.resolve()),
        "targeted_ms1_shape_identity_activation_policy": "explicit_support_tsv",
    }
    assert calls["run_config"].targeted_ms1_shape_identity_support_tsv == (
        support_path.resolve()
    )
    assert calls["run_config"].targeted_ms1_shape_identity_activation_policy == (
        "explicit_support_tsv"
    )
    assert "Processed files: 2" in capsys.readouterr().out


def test_cli_explicit_shape_identity_activation_policy_override_keeps_normal_path(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _module()
    config = _config(tmp_path)
    targets = [_target("Analyte")]
    calls: dict[str, object] = {}

    def fake_load_config(_config_dir: Path, *, settings_overrides=None):
        calls["settings_overrides"] = settings_overrides
        loaded_config = replace(
            config,
            targeted_ms1_shape_identity_activation_policy=settings_overrides[
                "targeted_ms1_shape_identity_activation_policy"
            ],
        )
        return loaded_config, targets

    monkeypatch.setattr(module, "load_config", fake_load_config)
    monkeypatch.setattr(module.extractor, "run", _fake_run(calls))
    monkeypatch.setattr(
        module,
        "write_excel_from_run_output",
        lambda *_args, **_kwargs: calls.setdefault("excel", True),
        raising=False,
    )

    exit_code = module.main(
        [
            "--base-dir",
            str(tmp_path),
            "--targeted-ms1-shape-identity-activation-policy",
            "explicit_support_tsv",
        ]
    )

    assert exit_code == 0
    assert calls["settings_overrides"] == {
        "targeted_ms1_shape_identity_activation_policy": ("explicit_support_tsv")
    }
    assert calls["run_config"].targeted_ms1_shape_identity_activation_policy == (
        "explicit_support_tsv"
    )
    assert "Processed files: 2" in capsys.readouterr().out


def test_cli_limited_policy_without_support_tsv_dispatches_auto_limited_default(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _module()
    config = replace(
        _config(tmp_path),
        targeted_ms1_shape_identity_activation_policy="limited_5hmdc_5medc",
    )
    targets = [_target("Analyte")]
    calls: dict[str, object] = {}

    monkeypatch.setattr(module, "load_config", lambda _config_dir: (config, targets))

    def fake_auto_workflow(run_config, run_targets, **kwargs):
        calls["auto_config"] = run_config
        calls["auto_targets"] = run_targets
        calls["auto_kwargs"] = kwargs
        return RunOutput(file_results=[], diagnostics=[])

    def fake_normal_run(*_args, **_kwargs):
        calls["normal_run_called"] = True
        return RunOutput(file_results=[], diagnostics=[])

    monkeypatch.setattr(
        module,
        "_run_targeted_ms1_shape_identity_auto_limited_default",
        fake_auto_workflow,
    )
    monkeypatch.setattr(module.extractor, "run", fake_normal_run)

    exit_code = module.main(["--base-dir", str(tmp_path)])

    assert exit_code == 0
    assert calls["auto_config"].targeted_ms1_shape_identity_support_tsv is None
    assert calls["auto_config"].targeted_ms1_shape_identity_activation_policy == (
        "limited_5hmdc_5medc"
    )
    assert calls["auto_targets"] == targets
    assert calls["auto_kwargs"]["auto_output_dir"] is None
    assert "normal_run_called" not in calls
    assert "Processed files: 0" in capsys.readouterr().out


def test_cli_auto_limited_default_gate_failure_is_clean_and_unpublished(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _module()
    config = _config(tmp_path)
    targets = [_target("Analyte")]
    auto_dir = tmp_path / "auto"

    monkeypatch.setattr(module, "load_config", lambda _config_dir: (config, targets))

    def fake_run(run_config, _run_targets, **_kwargs):
        run_config.output_csv.parent.mkdir(parents=True, exist_ok=True)
        run_config.output_csv.write_text("SampleName\n", encoding="utf-8")
        run_config.diagnostics_csv.write_text("SampleName\n", encoding="utf-8")
        return RunOutput(file_results=[], diagnostics=[])

    monkeypatch.setattr(module.extractor, "run", fake_run)
    monkeypatch.setattr(
        limited_identity_workflow,
        "run_build_targeted_ms1_shape_identity_supports",
        lambda **kwargs: SimpleNamespace(
            evidence_tsv=kwargs["output_tsv"],
            candidate_count=1,
            evidence_row_count=1,
            trace_request_count=2,
        ),
    )
    monkeypatch.setattr(
        limited_identity_workflow,
        "write_targeted_ms1_shape_identity_auto_diff_artifacts",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("support key set mismatch")),
    )

    exit_code = module.main(
        [
            "--base-dir",
            str(tmp_path),
            "--targeted-ms1-shape-identity-auto-limited-default",
            "--targeted-ms1-shape-identity-auto-output-dir",
            str(auto_dir),
        ]
    )

    assert exit_code == 2
    assert not (auto_dir / "final" / "output").exists()
    assert not auto_dir.exists()
    assert not list(auto_dir.parent.glob(".xic-limited-*"))
    stderr = capsys.readouterr().err
    assert "targeted MS1 shape identity auto-limited workflow failed" in stderr
    assert "verified final output was not published" in stderr
    assert "support key set mismatch" in stderr


def test_cli_auto_limited_default_rejects_manual_support_tsv(
    tmp_path: Path,
    capsys,
) -> None:
    module = _module()
    support_tsv = tmp_path / "targeted_ms1_shape_identity.tsv"
    support_tsv.write_text("stub\n", encoding="utf-8")

    exit_code = module.main(
        [
            "--base-dir",
            str(tmp_path),
            "--targeted-ms1-shape-identity-auto-limited-default",
            "--targeted-ms1-shape-identity-support-tsv",
            str(support_tsv),
        ]
    )

    assert exit_code == 2
    assert (
        "--targeted-ms1-shape-identity-auto-limited-default cannot be combined "
        "with --targeted-ms1-shape-identity-support-tsv"
    ) in capsys.readouterr().err


def test_cli_auto_output_dir_requires_auto_flag(
    tmp_path: Path,
    capsys,
) -> None:
    module = _module()

    exit_code = module.main(
        [
            "--base-dir",
            str(tmp_path),
            "--targeted-ms1-shape-identity-auto-output-dir",
            str(tmp_path / "auto"),
        ]
    )

    assert exit_code == 2
    assert (
        "--targeted-ms1-shape-identity-auto-output-dir requires "
        "--targeted-ms1-shape-identity-auto-limited-default"
    ) in capsys.readouterr().err


def test_cli_reports_missing_model_selection_expected_diff_approval_registry(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _module()
    config = _config(tmp_path)
    targets = [_target("Analyte")]
    missing_path = tmp_path / "missing_approvals.tsv"
    calls: dict[str, object] = {}

    def fake_load_config(_config_dir: Path, *, settings_overrides=None):
        loaded_config = replace(
            config,
            model_selection_expected_diff_approval_registry=Path(
                settings_overrides["model_selection_expected_diff_approval_registry"]
            ),
        )
        return loaded_config, targets

    monkeypatch.setattr(module, "load_config", fake_load_config)
    monkeypatch.setattr(module.extractor, "run", _fake_run(calls))

    exit_code = module.main(
        [
            "--base-dir",
            str(tmp_path),
            "--model-selection-expected-diff-approvals",
            str(missing_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "approval registry file not found" in captured.err
    assert "run_config" not in calls


def test_cli_reports_config_errors_without_traceback(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    monkeypatch.setattr(
        module,
        "load_config",
        lambda _config_dir: (_ for _ in ()).throw(ConfigError("settings.csv missing")),
    )

    exit_code = module.main(["--base-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "settings.csv missing" in captured.err
    assert "Traceback" not in captured.err


def test_cli_reports_publication_rejections_without_traceback(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = _module()
    config = _config(tmp_path)
    targets = [_target("Analyte")]
    monkeypatch.setattr(module, "load_config", lambda _config_dir: (config, targets))

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
    monkeypatch.setattr(module, "load_config", lambda _config_dir: (config, targets))
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
        model_selection_expected_diff_approvals=None,
    ):
        calls["run_config"] = config
        calls["run_targets"] = targets
        calls["model_selection_expected_diff_approvals"] = (
            model_selection_expected_diff_approvals
        )
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


def _expected_diff_approval() -> ExpectedDiffApprovalRecord:
    return ExpectedDiffApprovalRecord(
        stable_row_id="SampleA|Analyte|legacy|successor",
        sample_name="SampleA",
        target_label="Analyte",
        legacy_selected_candidate_id="legacy",
        successor_selected_candidate_id="successor",
        final_label="expected_diff",
        reviewer_verdict="approved",
        validation_tier="targeted_benchmark",
        public_outputs_touched=("peak_candidate_table",),
        matrix_value_impact="none",
        evidence_sources=("rt",),
        evidence_summary="reviewed",
        reviewer_role="domain_reviewer",
    )


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
    config_dir.mkdir(parents=True, exist_ok=True)
    settings = {
        "data_dir": str(data_dir),
        "dll_dir": str(dll_dir),
        "smooth_window": "15",
        "smooth_polyorder": "3",
        "peak_rel_height": "0.95",
        "peak_min_prominence_ratio": "0.10",
        "ms2_precursor_tol_da": "0.5",
        "nl_min_intensity_ratio": "0.01",
        "count_no_ms2_as_detected": "false",
        "targeted_ms1_shape_identity_activation_policy": "explicit_support_tsv",
    }
    with (config_dir / "settings.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=["key", "value", "description"])
        writer.writeheader()
        for key, value in settings.items():
            writer.writerow({"key": key, "value": value, "description": key})

    fields = [
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
        writer = csv.DictWriter(handle, fieldnames=fields)
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


def test_cli_reusable_method_freezes_inputs_only_until_run_finishes(
    tmp_path, monkeypatch
):
    from dataclasses import asdict

    from xic_extractor.configuration.hashing import compute_config_hash
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
    method_dir = tmp_path / "method"
    target_rows = [
        {key: str(value) for key, value in asdict(_target("MethodTarget")).items()}
    ]
    save_targeted_method(
        create_targeted_method(
            {
                "nl_min_intensity_ratio": "0.07",
                "targeted_ms1_shape_identity_activation_policy": "explicit_support_tsv",
            },
            target_rows,
        ),
        method_dir,
    )
    calls = {}
    fake_run = _fake_run(calls)

    prepare = module.prepare_targeted_config

    def record_frozen_inputs(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        calls["frozen_inputs"] = prepared.input_config_dir
        return prepared

    def run_and_change_active_sources(config, targets, **kwargs):
        (method_dir / "targets.csv").write_text("edited during run", encoding="utf-8")
        (tmp_path / "config" / "settings.csv").write_text(
            "edited during run", encoding="utf-8"
        )
        receipt = calls["frozen_inputs"]
        assert config.config_hash == compute_config_hash(
            receipt / "targets.csv", receipt / "settings.csv"
        )
        return fake_run(config, targets, **kwargs)

    monkeypatch.setattr(module, "prepare_targeted_config", record_frozen_inputs)
    monkeypatch.setattr(module.extractor, "run", run_and_change_active_sources)
    assert (
        module.main(
            [
                "--base-dir",
                str(tmp_path),
                "--method-dir",
                str(method_dir),
                "--data-dir",
                str(new_batch),
                "--parallel-mode",
                "serial",
                "--parallel-workers",
                "2",
            ]
        )
        == 0
    )
    config = calls["run_config"]
    assert config.data_dir == new_batch
    assert config.dll_dir == dll_dir
    assert config.nl_min_intensity_ratio == 0.07
    assert config.parallel_workers == 2
    assert [target.label for target in calls["run_targets"]] == ["MethodTarget"]
    assert not calls["frozen_inputs"].exists()
    assert not (tmp_path / "output" / ".run_inputs").exists()
