from pathlib import Path
from threading import Event

import pytest

from gui.workers.pipeline_worker import PipelineWorker
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_diagnostic_method import _method_request
from xic_extractor.extraction.targeted_diagnostic_application import (
    build_canonical_targeted_application_service,
    targeted_diagnostic_request_provider,
)
from xic_extractor.extraction.targeted_diagnostic_delivery import (
    rollback_targeted_diagnostic_delivery_candidate,
)
from xic_extractor.extraction.targeted_diagnostic_method import (
    targeted_attempt_namespace_component,
)
from xic_extractor.extraction.targeted_service import (
    build_targeted_application_request,
)
from xic_extractor.output.csv_writers import write_wide_csv


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
        if actual_config.keep_intermediate_csv:
            write_wide_csv(
                actual_config,
                actual_targets,
                request.authoritative_legacy_output.file_results,
            )
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
        worker.start()
        assert worker.wait(30_000)

    assert calls == {"legacy": 2, "provider": 2}
    assert cli_request.peak_config.output_csv.is_file()
    assert not gui_request.peak_config.output_csv.exists()
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
        assert (attempt_dir / "xic_results.csv").is_file()


def test_gui_thread_cancellation_is_accepted_before_successor_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import gui.workers.pipeline_worker as gui_module

    tmp_path.mkdir(exist_ok=True)
    request, _fixture = _method_request(tmp_path)
    legacy_entered = Event()
    release_legacy = Event()

    def legacy_runner(_config, _targets, **_kwargs):
        legacy_entered.set()
        assert release_legacy.wait(timeout=10)
        return request.authoritative_legacy_output

    def request_provider(application_request, actual_output):
        assert application_request.targets == request.targets
        assert actual_output is request.authoritative_legacy_output
        return request

    monkeypatch.setattr(gui_module.extractor, "run", legacy_runner)
    monkeypatch.setattr(
        gui_module.config_module,
        "load_config",
        lambda _config_dir: (request.peak_config, list(request.targets)),
    )
    monkeypatch.setattr(
        gui_module,
        "write_excel_from_run_output",
        lambda *_args, **_kwargs: pytest.fail("cancelled GUI wrote XLSX"),
    )

    with targeted_diagnostic_request_provider(
        request_provider,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
    ):
        worker = PipelineWorker(tmp_path / "config")
        worker.start()
        assert legacy_entered.wait(timeout=10)
        worker.stop()
        release_legacy.set()
        assert worker.wait(30_000)

    attempt_dir = (
        request.peak_config.output_csv.parent
        / "successor_attempts"
        / targeted_attempt_namespace_component(
            request.processing_attempt.processing_attempt_id
        )
    )
    assert (attempt_dir / "alignment.runtime.json").is_file()
    assert not (attempt_dir / "targeted.runtime.json").exists()
    assert not (attempt_dir / "xic_results.csv").exists()


def test_production_composition_exposes_delivery_and_whole_attempt_rollback(
    tmp_path: Path,
) -> None:
    request, _fixture = _method_request(tmp_path)

    def legacy_runner(_actual_config, _actual_targets, **_kwargs):
        return request.authoritative_legacy_output

    def request_provider(_application_request, _actual_output):
        return request

    with targeted_diagnostic_request_provider(
        request_provider,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
    ):
        execution = build_canonical_targeted_application_service(
            legacy_runner
        ).execute_application(
            build_targeted_application_request(
                request.peak_config,
                list(request.targets),
            )
        )

    outcome = execution.diagnostic_outcome
    assert outcome is not None
    assert outcome.status == "staged_candidate"
    assert outcome.method_result is not None
    assert outcome.delivery_candidate is not None
    rolled_back = rollback_targeted_diagnostic_delivery_candidate(
        outcome.delivery_candidate
    )
    assert rolled_back.status == "rolled_back"
    assert rolled_back.prior_current_delivery_id == request.prior_current_delivery_id
    assert rolled_back.active_output == request.authoritative_legacy_output
    assert request.peak_config.keep_intermediate_csv is False
    assert not request.peak_config.output_csv.exists()


def test_production_composition_ignores_stale_unretained_legacy_csv(
    tmp_path: Path,
) -> None:
    request, _fixture = _method_request(tmp_path)
    stale_bytes = b"stale output from an earlier run\r\n"
    request.peak_config.output_csv.write_bytes(stale_bytes)

    with targeted_diagnostic_request_provider(
        lambda _application_request, _actual_output: request,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
    ):
        execution = build_canonical_targeted_application_service(
            lambda _config, _targets, **_kwargs: request.authoritative_legacy_output
        ).execute_application(
            build_targeted_application_request(
                request.peak_config,
                list(request.targets),
            )
        )

    outcome = execution.diagnostic_outcome
    assert outcome is not None
    assert outcome.status == "staged_candidate"
    assert request.peak_config.output_csv.read_bytes() == stale_bytes


def test_production_composition_preserves_delivery_quarantine(
    tmp_path: Path,
) -> None:
    request, _fixture = _method_request(tmp_path)
    request.authoritative_legacy_output.file_results.reverse()

    def legacy_runner(actual_config, actual_targets, **_kwargs):
        write_wide_csv(
            actual_config,
            actual_targets,
            request.authoritative_legacy_output.file_results,
        )
        return request.authoritative_legacy_output

    with targeted_diagnostic_request_provider(
        lambda _application_request, _actual_output: request,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
    ):
        execution = build_canonical_targeted_application_service(
            legacy_runner
        ).execute_application(
            build_targeted_application_request(
                request.peak_config,
                list(request.targets),
            )
        )

    outcome = execution.diagnostic_outcome
    assert execution.active_output is request.authoritative_legacy_output
    assert outcome is not None
    assert outcome.status == "quarantined"
    assert outcome.method_result is not None
    assert outcome.delivery_candidate is not None
    assert outcome.delivery_candidate.status == "quarantined"
    assert outcome.quarantine_reason == "diagnostic CSV hard-exact conflict"


def test_cancellation_after_targeted_settlement_prevents_delivery_stage(
    tmp_path: Path,
) -> None:
    request, _fixture = _method_request(tmp_path)
    stop_checks = iter((False, False, True))

    def legacy_runner(actual_config, actual_targets, **_kwargs):
        write_wide_csv(
            actual_config,
            actual_targets,
            request.authoritative_legacy_output.file_results,
        )
        return request.authoritative_legacy_output

    with targeted_diagnostic_request_provider(
        lambda _application_request, _actual_output: request,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
    ):
        execution = build_canonical_targeted_application_service(
            legacy_runner
        ).execute_application(
            build_targeted_application_request(
                request.peak_config,
                list(request.targets),
                should_stop=lambda: next(stop_checks),
            )
        )

    outcome = execution.diagnostic_outcome
    assert outcome is not None
    assert outcome.status == "quarantined"
    assert outcome.method_result is not None
    assert outcome.delivery_candidate is None
    attempt_dir = (
        request.peak_config.output_csv.parent
        / "successor_attempts"
        / targeted_attempt_namespace_component(
            request.processing_attempt.processing_attempt_id
        )
    )
    assert (attempt_dir / "alignment.runtime.json").is_file()
    assert (attempt_dir / "targeted.runtime.json").is_file()
    assert not (attempt_dir / "xic_results.csv").exists()
