from dataclasses import replace
from pathlib import Path

from gui.workers.pipeline_worker import PipelineWorker
from tests.test_targeted_diagnostic_method import _method_request
from tests.test_targeted_method_runtime import (
    _alignment_job,
    _completion_spec,
    _marker,
    _reservations,
    _runtime,
)
from xic_extractor.extraction.targeted_method_runtime import (
    build_targeted_method_completion_jobs,
    execute_targeted_method_alignment_runtime,
    execute_targeted_method_completion_runtime,
)
from xic_extractor.extraction.targeted_service import TargetedApplicationService


def test_cli_and_gui_execute_same_diagnostic_method_service_once_each(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import gui.workers.pipeline_worker as gui_module
    import scripts.run_extraction as cli_module

    method_request, fixture = _method_request(tmp_path)
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
        invocation = calls["diagnostic"]
        alignment_job = _alignment_job(
            actual_request,
            fixture.attempt,
            "alignment:method",
        )
        alignment = execute_targeted_method_alignment_runtime(
            _runtime(fixture.attempt, ("alignment:method",)),
            (alignment_job,),
            max_workers=1,
            resource_reservations=_reservations((alignment_job,)),
            recovery_marker=_marker(
                tmp_path,
                f"adapter-{invocation}-alignment",
            ),
        )
        jobs = build_targeted_method_completion_jobs(
            alignment,
            (_completion_spec(actual_request),),
        )
        targeted = execute_targeted_method_completion_runtime(
            _runtime(fixture.attempt, ("targeted:method",)),
            jobs,
            max_workers=1,
            resource_reservations=_reservations(jobs),
            recovery_marker=_marker(
                tmp_path,
                f"adapter-{invocation}-targeted",
            ),
        )
        results = targeted.settlement.successful_targeted_method_results
        assert len(results) == 1
        return results[0][1]

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
