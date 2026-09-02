from __future__ import annotations

import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_targeted_production_workflow import production_case
from xic_extractor.extraction.targeted_output_boundary import (
    TargetedOutputBoundaryError,
    publish_targeted_outputs,
)
from xic_extractor.extraction.targeted_production_workflow import (
    execute_targeted_production_workflow,
)
from xic_extractor.extraction.targeted_runtime_adapter import (
    TargetedRuntimeAdapterError,
)
from xic_extractor.extraction.targeted_service import (
    TargetedApplicationService,
    build_targeted_application_request,
)


def test_complete_finalized_population_publishes_csv_excel_and_manifest(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
    excel_path = config.output_csv.with_name("xic_results.xlsx")
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        excel_output_path=excel_path,
    )
    finalized = execute_targeted_production_workflow(request, output)

    published = publish_targeted_outputs(request, finalized, output)

    assert set(published) == {
        config.output_csv,
        config.output_csv.with_name("xic_results_long.csv"),
        config.diagnostics_csv,
        config.output_csv.with_name("method_manifest.json"),
        excel_path,
    }
    assert all(path.is_file() for path in published)
    assert not list(config.output_csv.parent.glob(".xic-targeted-publish-*"))


def test_collision_leaves_existing_output_and_publishes_nothing_else(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)
    config.output_csv.parent.mkdir(parents=True, exist_ok=True)
    config.output_csv.write_bytes(b"existing")

    with pytest.raises(TargetedOutputBoundaryError, match="already exists"):
        publish_targeted_outputs(request, finalized, output)

    assert config.output_csv.read_bytes() == b"existing"
    assert sorted(path.name for path in config.output_csv.parent.iterdir()) == [
        config.output_csv.name
    ]


def test_render_failure_publishes_no_final_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_extractor.extraction.targeted_output_boundary as boundary

    config, target, output = production_case(tmp_path)
    excel_path = config.output_csv.with_name("xic_results.xlsx")
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        excel_output_path=excel_path,
    )
    finalized = execute_targeted_production_workflow(request, output)
    monkeypatch.setattr(
        boundary,
        "write_excel_from_run_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("render failed")),
    )

    with pytest.raises(TargetedOutputBoundaryError, match="render failed"):
        publish_targeted_outputs(request, finalized, output)

    assert not any(config.output_csv.parent.iterdir())


def test_commit_failure_rolls_back_all_final_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)
    original_replace = Path.replace
    commit_count = 0
    failed = False

    def fail_second_commit(source: Path, target_path: Path) -> Path:
        nonlocal commit_count, failed
        if source.parent.name.startswith(".xic-targeted-publish-"):
            commit_count += 1
            if commit_count == 2 and not failed:
                failed = True
                raise OSError("commit failed")
        return original_replace(source, target_path)

    monkeypatch.setattr(Path, "replace", fail_second_commit)

    with pytest.raises(TargetedOutputBoundaryError, match="rolled back"):
        publish_targeted_outputs(request, finalized, output)

    assert not any(config.output_csv.parent.iterdir())


def test_cancellation_after_typed_completion_never_invokes_publisher(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        should_stop=lambda: True,
    )

    service = TargetedApplicationService(
        lambda *_args, **_kwargs: output,
        finalized_workflow_runner=execute_targeted_production_workflow,
        output_publisher=lambda *_args: pytest.fail("cancelled run published output"),
    )

    with pytest.raises(TargetedRuntimeAdapterError, match="cancelled"):
        service.execute_application(request)


def test_cli_and_gui_route_through_acquisition_service_boundary() -> None:
    import gui.workers.pipeline_worker as gui_module
    import scripts.run_extraction as cli_module

    cli_source = inspect.getsource(cli_module)
    gui_source = inspect.getsource(gui_module)
    assert "extractor.run" not in cli_source
    assert "extractor.run" not in gui_source
    assert "extractor.acquire" in cli_source
    assert "extractor.acquire" in gui_source
    assert "write_excel_from_run_output" not in cli_source
    assert "write_excel_from_run_output" not in gui_source
