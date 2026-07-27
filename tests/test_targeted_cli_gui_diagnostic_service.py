from dataclasses import replace
from pathlib import Path

from gui.workers.pipeline_worker import PipelineWorker
from tests.test_targeted_diagnostic_method import _method_request
from xic_extractor.extraction.targeted_diagnostic_method import (
    run_targeted_diagnostic_method,
)
from xic_extractor.extraction.targeted_service import TargetedApplicationService


def test_cli_and_gui_execute_same_diagnostic_method_service_once_each(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import gui.workers.pipeline_worker as gui_module
    import scripts.run_extraction as cli_module

    method_request, _fixture = _method_request(tmp_path)
    active_output = method_request.authoritative_legacy_output
    calls = {"legacy": 0, "factory": 0, "diagnostic": 0}

    def legacy_runner(actual_config, actual_targets, **_kwargs):
        assert actual_config.data_dir == method_request.peak_config.data_dir
        assert actual_targets == list(method_request.targets)
        calls["legacy"] += 1
        return active_output

    def request_factory(application_request, actual_output):
        assert application_request.config.data_dir == (
            method_request.peak_config.data_dir
        )
        assert application_request.targets == method_request.targets
        assert actual_output is active_output
        calls["factory"] += 1
        return replace(method_request, peak_config=application_request.config)

    def diagnostic_runner(actual_request):
        calls["diagnostic"] += 1
        return run_targeted_diagnostic_method(actual_request)

    service = TargetedApplicationService(
        legacy_runner,
        diagnostic_method_runner=diagnostic_runner,
        diagnostic_method_request_factory=request_factory,
    )
    def service_factory(_runner):
        return service
    config_and_targets = (
        method_request.peak_config,
        list(method_request.targets),
    )

    monkeypatch.setattr(cli_module, "TargetedApplicationService", service_factory)
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda _config_dir: config_and_targets,
    )
    monkeypatch.setattr(cli_module, "write_method_manifest", lambda *_a, **_k: None)
    assert cli_module.main(
        ["--base-dir", str(tmp_path), "--skip-excel"]
    ) == 0

    monkeypatch.setattr(gui_module, "TargetedApplicationService", service_factory)
    monkeypatch.setattr(
        gui_module.config_module,
        "load_config",
        lambda _config_dir: config_and_targets,
    )
    monkeypatch.setattr(
        gui_module,
        "write_excel_from_run_output",
        lambda config, _targets, _output, *, output_path: output_path,
    )
    monkeypatch.setattr(
        gui_module,
        "build_summary",
        lambda *_args: {"diagnostic_adapter": "gui"},
    )
    worker = PipelineWorker(tmp_path / "config")
    errors: list[str] = []
    worker.error.connect(errors.append)
    worker.run()

    assert errors == []
    assert calls == {"legacy": 2, "factory": 2, "diagnostic": 2}
