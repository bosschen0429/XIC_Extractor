from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.diagnostics.targeted_validation_run import run_targeted_case
from xic_extractor.applications.targeted.application import TargetedApplicationExecution
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.extraction.result_models import RunOutput


@pytest.fixture
def application_shell(monkeypatch):
    from tools.diagnostics import targeted_validation_run as runs

    def factory(acquire):
        def execute(request):
            output = acquire(request.config, list(request.targets))
            return TargetedApplicationExecution(active_output=output)

        return SimpleNamespace(execute_application=execute)

    monkeypatch.setattr(runs, "build_canonical_targeted_application_service", factory)


def test_validation_uses_finalized_targeted_application(tmp_path, monkeypatch):
    import tools.diagnostics.targeted_validation_run as runs

    data_dir, dll_dir = tmp_path / "raw", tmp_path / "dll"
    data_dir.mkdir()
    dll_dir.mkdir()
    _write_validation_settings(tmp_path / "config", data_dir=data_dir, dll_dir=dll_dir)
    _write_validation_targets(tmp_path / "config")
    acquired = RunOutput(file_results=[], diagnostics=[])
    requests = []

    def acquire(*_args, **_kwargs):
        return acquired

    def application(runner):
        assert runner is acquire

        def execute(request):
            requests.append(request)
            return TargetedApplicationExecution(
                active_output=acquired,
                published_paths=(request.excel_output_path,),
            )

        return SimpleNamespace(execute_application=execute)

    monkeypatch.setattr(runs.extractor, "acquire", acquire)
    monkeypatch.setattr(
        runs,
        "build_canonical_targeted_application_service",
        application,
    )
    workbook = runs.run_targeted_case(
        base_dir=tmp_path,
        data_dir=data_dir,
        mode="process",
        workers=4,
        output_dir=tmp_path / "out",
    )
    assert len(requests) == 1
    assert requests[0].excel_output_path == workbook
    assert requests[0].config.parallel_mode == "process"
    assert requests[0].config.parallel_workers == 4


def test_targeted_validation_run_applies_data_dir_override_before_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, application_shell
) -> None:
    config_dir = tmp_path / "config"
    validation_dir = tmp_path / "validation"
    dll_dir = tmp_path / "dll"
    validation_dir.mkdir()
    dll_dir.mkdir()
    _write_validation_settings(
        config_dir,
        data_dir=tmp_path / "placeholder_missing",
        dll_dir=dll_dir,
    )
    _write_validation_targets(config_dir)
    captured_configs: list[ExtractionConfig] = []
    captured: dict[str, object] = {}

    def fake_run(config, targets, *, model_selection_expected_diff_approvals=None):
        captured_configs.append(config)
        captured["targets"] = targets
        captured["approvals"] = model_selection_expected_diff_approvals
        return object()

    monkeypatch.setattr(
        "tools.diagnostics.targeted_validation_run.extractor.acquire", fake_run
    )

    workbook_path = run_targeted_case(
        base_dir=tmp_path,
        data_dir=validation_dir,
        mode="serial",
        workers=1,
        output_dir=tmp_path / "out",
    )

    assert captured_configs[0].data_dir == validation_dir
    assert captured["approvals"] is None
    assert workbook_path == tmp_path / "out" / "xic_results_serial_w1.xlsx"


def test_targeted_validation_run_applies_additional_settings_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, application_shell
) -> None:
    config_dir = tmp_path / "config"
    validation_dir = tmp_path / "validation"
    dll_dir = tmp_path / "dll"
    validation_dir.mkdir()
    dll_dir.mkdir()
    _write_validation_settings(
        config_dir,
        data_dir=tmp_path / "placeholder_missing",
        dll_dir=dll_dir,
    )
    _write_validation_targets(config_dir)
    captured_configs: list[ExtractionConfig] = []
    captured: dict[str, object] = {}

    def fake_run(config, targets, *, model_selection_expected_diff_approvals=None):
        captured_configs.append(config)
        captured["order"] = config.injection_order_source.read_bytes()
        captured["targets"] = targets
        captured["approvals"] = model_selection_expected_diff_approvals
        return object()

    monkeypatch.setattr(
        "tools.diagnostics.targeted_validation_run.extractor.acquire", fake_run
    )

    order = b"Sample_Name,Injection_Order\nSampleA,1\n"
    (tmp_path / "order.csv").write_bytes(order)
    run_targeted_case(
        base_dir=tmp_path,
        data_dir=validation_dir,
        mode="process",
        workers=4,
        output_dir=tmp_path / "out",
        settings_overrides={"injection_order_source": str(tmp_path / "order.csv")},
    )

    assert captured_configs[0].data_dir == validation_dir
    assert captured["order"] == order
    assert not captured_configs[0].injection_order_source.exists()
    assert captured_configs[0].parallel_mode == "process"
    assert captured_configs[0].parallel_workers == 4
    assert captured["approvals"] is None


def _write_validation_settings(
    config_dir: Path, *, data_dir: Path, dll_dir: Path
) -> None:
    from xic_extractor.configuration.workspace_state import save_workspace

    save_workspace(
        config_dir, settings={"data_dir": str(data_dir), "dll_dir": str(dll_dir)}
    )


def _write_validation_targets(config_dir: Path) -> None:
    from xic_extractor.configuration.workspace_state import save_workspace

    save_workspace(
        config_dir,
        targets=[
            dict(
                label="ISTD",
                mz="258.1085",
                expected_rt_min="9",
                cid_nl_loss_da="116.0474",
                role="istd",
                istd_pair="",
            )
        ],
    )
