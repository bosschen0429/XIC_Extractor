import ast
from dataclasses import replace
from pathlib import Path

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction.targeted_inputs import resolve_targeted_inputs
from xic_extractor.extraction.targeted_service import (
    TargetedApplicationService,
    build_targeted_application_request,
)
from xic_extractor.extractor import RunOutput


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


def test_headless_service_preserves_inputs_and_callbacks() -> None:
    config = _config()
    targets = [_target()]
    calls: dict[str, object] = {}
    output = RunOutput(file_results=[], diagnostics=[])

    def runner(actual_config, actual_targets, **kwargs):
        calls["config"] = actual_config
        calls["targets"] = actual_targets
        calls.update(kwargs)
        return output

    def progress(_done: int, _total: int, _name: str) -> None:
        return None

    def should_stop() -> bool:
        return False
    request = build_targeted_application_request(
        config,
        targets,
        progress_callback=progress,
        should_stop=should_stop,
    )

    assert TargetedApplicationService(runner).execute(request) is output
    assert calls["config"] is config
    assert calls["targets"] == targets
    assert calls["progress_callback"] is progress
    assert calls["should_stop"] is should_stop


def test_service_resolves_approval_registry_at_adapter_boundary(monkeypatch) -> None:
    resolved_approvals = {"row-1": object()}
    calls: dict[str, object] = {}

    def resolve_approvals(actual_config, approvals):
        assert actual_config is config
        assert approvals is None
        return resolved_approvals

    def runner(_config, _targets, **kwargs):
        calls.update(kwargs)
        return RunOutput(file_results=[], diagnostics=[])

    config = _config()
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_service."
        "resolve_model_selection_expected_diff_approvals",
        resolve_approvals,
    )

    TargetedApplicationService(runner).execute(
        build_targeted_application_request(config, [_target()])
    )

    assert calls["model_selection_expected_diff_approvals"] is resolved_approvals


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
        "xic_extractor.extraction.targeted_inputs."
        "load_expected_diff_approval_registry",
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
    for path in (Path("xic_extractor/extraction/targeted_service.py"),):
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
