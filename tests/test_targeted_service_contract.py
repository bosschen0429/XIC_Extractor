import ast
from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor import extractor
from xic_extractor.applications.targeted.application import (
    TargetedApplicationService,
    build_targeted_application_request,
)
from xic_extractor.config import ConfigError, ExtractionConfig, Target
from xic_extractor.extraction.targeted_inputs import resolve_targeted_inputs
from xic_extractor.extractor import FileResult, RunOutput


def test_cli_and_gui_use_same_targeted_application_service() -> None:
    for path in (
        Path("scripts/run_extraction.py"),
        Path("gui/workers/pipeline_worker.py"),
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "extractor"
        ]
        assert calls == [], f"{path} bypasses TargetedApplicationService"


def test_public_python_run_uses_canonical_targeted_application_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    targets = [_target()]
    output = RunOutput(file_results=[], diagnostics=[])
    captured: dict[str, object] = {}

    class _Service:
        def execute(self, request):
            captured["request"] = request
            return output

    def build_service(runner):
        captured["runner"] = runner
        return _Service()

    monkeypatch.setattr(
        "xic_extractor.applications.targeted.application."
        "build_canonical_targeted_application_service",
        build_service,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.run_pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("public run bypassed finalization")
        ),
    )

    actual = extractor.run(config, targets, injection_order={"SampleA": 1})

    assert actual is output
    assert captured["runner"] is extractor.acquire
    request = captured["request"]
    assert request.config is config
    assert request.targets == tuple(targets)
    assert request.injection_order == {"SampleA": 1}


def test_headless_service_preserves_inputs_and_callbacks(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "SampleA.raw").write_bytes(b"sample")
    config = replace(_config(), data_dir=data_dir)
    targets = [_target()]
    calls: dict[str, object] = {}
    output = RunOutput(file_results=[], diagnostics=[])
    stopped = False

    def runner(actual_config, actual_targets, **kwargs):
        nonlocal stopped
        calls["config"] = actual_config
        calls["targets"] = actual_targets
        calls.update(kwargs)
        stopped = True
        return output

    def progress(_done: int, _total: int, _name: str) -> None:
        return None

    def should_stop() -> bool:
        return stopped

    request = build_targeted_application_request(
        config,
        targets,
        progress_callback=progress,
        should_stop=should_stop,
    )

    assert TargetedApplicationService(runner).execute(request) is output
    assert calls["config"] == config
    assert calls["targets"] == targets
    assert calls["progress_callback"] is progress
    assert calls["should_stop"] is should_stop
    assert calls["publish_outputs"] is False
    acquisition_sources = calls["acquisition_sources_by_sample"]
    assert set(acquisition_sources) == {"SampleA"}
    assert acquisition_sources["SampleA"].source.byte_size == len(b"sample")


def test_service_isolates_runner_inputs_from_callback_mutation(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "SampleA.raw").write_bytes(b"sample")
    config = replace(_config(), data_dir=data_dir)
    caller_injection_order = {"SampleA": 1}
    runner_injection_order: dict[str, int] = {}
    stopped = False

    def progress(_done: int, _total: int, _name: str) -> None:
        caller_injection_order.clear()

    def runner(_config, _targets, **kwargs):
        nonlocal stopped
        progress_callback = kwargs["progress_callback"]
        progress_callback(0, 1, "SampleA.raw")
        runner_injection_order.update(kwargs["injection_order"])
        stopped = True
        return RunOutput(file_results=[], diagnostics=[])

    request = build_targeted_application_request(
        config,
        [_target()],
        injection_order=caller_injection_order,
        progress_callback=progress,
        should_stop=lambda: stopped,
    )

    TargetedApplicationService(runner).execute(request)

    assert caller_injection_order == {}
    assert runner_injection_order == {"SampleA": 1}


def test_service_retains_noncopyable_bound_lifecycle_callbacks(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "SampleA.raw").write_bytes(b"sample")
    config = replace(_config(), data_dir=data_dir)

    class LifecycleAdapter:
        def __init__(self) -> None:
            self.stopped = False
            self.progress_calls: list[tuple[int, int, str]] = []

        def __deepcopy__(self, _memo):
            raise AssertionError("lifecycle callback receiver must not be copied")

        def progress(self, done: int, total: int, name: str) -> None:
            self.progress_calls.append((done, total, name))

        def should_stop(self) -> bool:
            return self.stopped

    adapter = LifecycleAdapter()
    progress_callback = adapter.progress
    should_stop = adapter.should_stop

    def runner(_config, _targets, **kwargs):
        assert kwargs["progress_callback"] is progress_callback
        assert kwargs["should_stop"] is should_stop
        kwargs["progress_callback"](1, 1, "SampleA.raw")
        adapter.stopped = True
        return RunOutput(file_results=[], diagnostics=[])

    request = build_targeted_application_request(
        config,
        [_target()],
        progress_callback=progress_callback,
        should_stop=should_stop,
    )

    TargetedApplicationService(runner).execute(request)

    assert adapter.progress_calls == [(1, 1, "SampleA.raw")]


def test_service_resolves_approval_registry_at_adapter_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    resolved_approvals: dict[str, object] = {}
    calls: dict[str, object] = {}
    stopped = False

    def resolve_approvals(actual_config, approvals):
        assert actual_config is config
        assert approvals is None
        return resolved_approvals

    def runner(_config, _targets, **kwargs):
        nonlocal stopped
        calls.update(kwargs)
        stopped = True
        return RunOutput(file_results=[], diagnostics=[])

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "SampleA.raw").write_bytes(b"sample")
    config = replace(_config(), data_dir=data_dir)
    monkeypatch.setattr(
        "xic_extractor.applications.targeted.application."
        "resolve_model_selection_expected_diff_approvals",
        resolve_approvals,
    )

    TargetedApplicationService(runner).execute(
        build_targeted_application_request(
            config,
            [_target()],
            should_stop=lambda: stopped,
        )
    )

    assert calls["model_selection_expected_diff_approvals"] == resolved_approvals


def test_typed_workflow_failure_never_reaches_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "SampleA.raw").write_bytes(b"sample")
    config = replace(_config(), data_dir=data_dir)
    acquisition = RunOutput(
        file_results=[FileResult(sample_name="SampleA", results={})],
        diagnostics=[],
    )

    def acquire(*_args, **_kwargs):
        calls.append("acquire")
        return acquisition

    def fail_workflow(_request, _prepared):
        calls.append("workflow")
        raise RuntimeError("typed workflow failed")

    def publish(*_args, **_kwargs):
        calls.append("publish")

    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_production_workflow."
        "complete_prepared_targeted_production_workflow",
        fail_workflow,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_output_boundary.publish_targeted_outputs",
        publish,
    )

    with pytest.raises(RuntimeError, match="typed workflow failed"):
        TargetedApplicationService(acquire).execute_application(
            build_targeted_application_request(config, [_target()])
        )

    assert calls == ["acquire", "workflow"]


def test_failed_raw_acquisition_preserves_reason_and_never_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "SampleA.raw").write_bytes(b"sample")
    config = replace(_config(), data_dir=data_dir)
    acquisition = RunOutput(
        file_results=[
            FileResult(sample_name="SampleA", results={}, error="file locked")
        ],
        diagnostics=[],
    )

    def acquire(*_args, **_kwargs):
        calls.append("acquire")
        return acquisition

    def forbid_runtime(*_args, **_kwargs):
        calls.append("runtime")
        raise AssertionError("failed acquisition reached runtime success recording")

    def forbid_publication(*_args, **_kwargs):
        calls.append("publish")
        raise AssertionError("failed acquisition reached publication")

    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_production_workflow.start_runtime_attempt",
        forbid_runtime,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_output_boundary.publish_targeted_outputs",
        forbid_publication,
    )

    with pytest.raises(RuntimeError, match="SampleA.*file locked"):
        TargetedApplicationService(acquire).execute_application(
            build_targeted_application_request(config, [_target()])
        )

    assert calls == ["acquire"]


def test_cancellation_after_partial_acquisition_skips_handoff_and_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for name in ("SampleA", "SampleB"):
        (data_dir / f"{name}.raw").write_bytes(name.encode("utf-8"))
    config = replace(_config(), data_dir=data_dir)
    acquisition = RunOutput(
        file_results=[FileResult(sample_name="SampleA", results={})],
        diagnostics=[],
    )
    cancelled = False

    def acquire(*_args, **kwargs):
        nonlocal cancelled
        assert kwargs["publish_outputs"] is False
        calls.append("acquire")
        cancelled = True
        return acquisition

    def forbid_handoff(*_args, **_kwargs):
        raise AssertionError("cancelled acquisition reached the retained handoff")

    def forbid_publication(*_args, **_kwargs):
        raise AssertionError("cancelled acquisition reached publication")

    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_production_workflow."
        "prepare_targeted_production_workflow",
        forbid_handoff,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_output_boundary.publish_targeted_outputs",
        forbid_publication,
    )

    execution = TargetedApplicationService(acquire).execute_application(
        build_targeted_application_request(
            config, [_target()], should_stop=lambda: cancelled
        )
    )

    assert execution.active_output is acquisition
    assert execution.finalized_results == ()
    assert execution.published_paths == ()
    assert calls == ["acquire"]


def test_empty_acquisition_reports_no_input_before_finalization(tmp_path: Path) -> None:
    calls: list[str] = []
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "SampleA.raw").write_bytes(b"sample")
    config = replace(_config(), data_dir=data_dir)
    acquisition = RunOutput(file_results=[], diagnostics=[])

    def acquire(*_args, **_kwargs):
        calls.append("acquire")
        return acquisition

    with pytest.raises(ConfigError, match="No RAW files were acquired"):
        TargetedApplicationService(acquire).execute_application(
            build_targeted_application_request(config, [_target()])
        )

    assert calls == ["acquire"]


def test_pipeline_input_resolution_keeps_approvals_explicit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = replace(
        _config(),
        data_dir=tmp_path,
        model_selection_expected_diff_approval_registry=tmp_path / "registry.tsv",
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_inputs.load_expected_diff_approval_registry",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("pipeline resolver must not load adapter policy")
        ),
    )

    resolved = resolve_targeted_inputs(
        config,
        raw_paths=[],
        injection_order={},
        rt_prior_library={},
        model_selection_expected_diff_approvals=None,
    )

    assert resolved.model_selection_expected_diff_approvals is None


def test_contract_modules_do_not_import_gui_writer_or_raw_adapter() -> None:
    forbidden_prefixes = (
        "gui",
        "scripts",
        "xic_extractor.output",
        "xic_extractor.raw_reader",
    )
    for path in (
        Path("xic_extractor/acquisition_contract.py"),
        Path("xic_extractor/delivery_lineage.py"),
        Path("xic_extractor/execution/_observability.py"),
        Path("xic_extractor/applications/targeted/application.py"),
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = _imports(tree)
        assert not any(
            imported.startswith(forbidden_prefixes) for imported in imports
        ), f"{path}: forbidden dependency {imports}"


def _imports(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def _config() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("data"),
        dll_dir=Path("dll"),
        output_csv=Path("output/xic_results.csv"),
        diagnostics_csv=Path("output/xic_diagnostics.csv"),
        smooth_window=9,
        smooth_polyorder=2,
        peak_rel_height=0.02,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _target() -> Target:
    return Target(
        label="Analyte",
        mz=250.0,
        rt_min=1.0,
        rt_max=2.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0,
        nl_ppm_warn=20.0,
        nl_ppm_max=40.0,
        is_istd=False,
        istd_pair="",
    )
