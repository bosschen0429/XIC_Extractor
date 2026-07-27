from pathlib import Path

from gui.workers.pipeline_worker import PipelineWorker
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_diagnostic_method import _method_request
from xic_extractor.extraction.targeted_diagnostic_application import (
    targeted_diagnostic_request_provider,
)
from xic_extractor.extraction.targeted_diagnostic_method import (
    targeted_attempt_namespace_component,
)


def test_cli_and_gui_use_production_composition_for_spawned_method(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import gui.workers.pipeline_worker as gui_module
    import scripts.run_extraction as cli_module

    (tmp_path / "cli").mkdir()
    (tmp_path / "gui").mkdir()
    cli_request, _cli_fixture = _method_request(tmp_path / "cli")
    gui_request, _gui_fixture = _method_request(tmp_path / "gui")
    requests_by_output = {
        cli_request.peak_config.output_csv: cli_request,
        gui_request.peak_config.output_csv: gui_request,
    }
    calls = {"legacy": 0, "provider": 0}

    def legacy_runner(actual_config, actual_targets, **_kwargs):
        request = requests_by_output[actual_config.output_csv]
        assert actual_targets == list(request.targets)
        calls["legacy"] += 1
        return request.authoritative_legacy_output

    def request_provider(application_request, actual_output):
        request = requests_by_output[application_request.config.output_csv]
        assert application_request.targets == request.targets
        assert actual_output is request.authoritative_legacy_output
        calls["provider"] += 1
        return request

    monkeypatch.setattr(cli_module.extractor, "run", legacy_runner)
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda _config_dir: (
            cli_request.peak_config,
            list(cli_request.targets),
        ),
    )
    monkeypatch.setattr(cli_module, "write_method_manifest", lambda *_a, **_k: None)
    monkeypatch.setattr(
        gui_module.config_module,
        "load_config",
        lambda _config_dir: (
            gui_request.peak_config,
            list(gui_request.targets),
        ),
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

    with targeted_diagnostic_request_provider(
        request_provider,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
    ):
        assert cli_module.main(
            ["--base-dir", str(tmp_path / "cli"), "--skip-excel"]
        ) == 0
        worker = PipelineWorker(tmp_path / "gui" / "config")
        errors: list[str] = []
        worker.error.connect(errors.append)
        worker.run()

    assert errors == []
    assert calls == {"legacy": 2, "provider": 2}
    for request in (cli_request, gui_request):
        attempt_dir = (
            request.peak_config.output_csv.parent
            / "successor_attempts"
            / targeted_attempt_namespace_component(
                request.processing_attempt.processing_attempt_id
            )
        )
        assert (attempt_dir / "alignment.runtime.json").is_file()
        assert (attempt_dir / "targeted.runtime.json").is_file()
