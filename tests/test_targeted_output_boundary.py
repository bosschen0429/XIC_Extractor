from __future__ import annotations

import csv
import inspect
import json
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import asdict, replace
from multiprocessing import get_context
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from openpyxl import load_workbook

from tests.test_targeted_production_workflow import production_case
from xic_extractor.extraction import targeted_output_boundary
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
from xic_extractor.extractor import ExtractionResult, FileResult, RunOutput
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.output.method_manifest import artifact_sha256, method_manifest_path
from xic_extractor.output.review_report import review_report_path_for_excel


def _commit_staged_output_in_process(
    staged: str,
    final: str,
    start_barrier: Any,
) -> tuple[str, str]:
    start_barrier.wait(timeout=30)
    try:
        paths = targeted_output_boundary._commit_staged_outputs(
            {Path(staged): Path(final)}
        )
    except TargetedOutputBoundaryError as exc:
        return ("error", str(exc))
    return ("success", str(paths[0]))


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

    assert set(published.paths) == {
        config.output_csv,
        config.output_csv.with_name("xic_results_long.csv"),
        config.diagnostics_csv,
        config.output_csv.with_name("method_manifest.json"),
        excel_path,
    }
    assert all(path.is_file() for path in published.paths)
    assert not list(config.output_csv.parent.glob(".xic-targeted-publish-*"))


def test_published_workbook_binds_the_published_method_manifest(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    excel_path = config.output_csv.with_name("xic_results_20260903.xlsx")
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        excel_output_path=excel_path,
    )
    finalized = execute_targeted_production_workflow(request, output)

    publish_targeted_outputs(request, finalized, output)

    workbook = load_workbook(excel_path, read_only=True, data_only=True)
    metadata = dict(workbook["Run Metadata"].iter_rows(values_only=True))
    workbook.close()
    manifest = method_manifest_path(config)
    assert metadata["method_manifest_path"] == manifest.name
    assert metadata["method_manifest_sha256"] == artifact_sha256(manifest)


def test_published_review_report_uses_the_public_workbook_name(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, emit_review_report=True)
    excel_path = config.output_csv.with_name("xic_results_20260903.xlsx")
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        excel_output_path=excel_path,
    )
    finalized = execute_targeted_production_workflow(request, output)

    publication = publish_targeted_outputs(request, finalized, output)

    report_path = review_report_path_for_excel(excel_path)
    assert report_path in publication.paths
    assert report_path.is_file()
    assert not excel_path.with_name("__targeted_output__.html").exists()


def test_duplicate_declared_output_destinations_publish_nothing(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
    duplicate_excel_path = (
        config.output_csv.parent
        / "equivalent-destination"
        / ".."
        / config.output_csv.name
    )
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        excel_output_path=duplicate_excel_path,
    )
    finalized = execute_targeted_production_workflow(request, output)

    with pytest.raises(TargetedOutputBoundaryError, match="duplicate destination"):
        publish_targeted_outputs(request, finalized, output)

    assert not any(config.output_csv.parent.iterdir())


def test_publication_returns_concrete_public_result_models(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)

    published = publish_targeted_outputs(request, finalized, output)

    assert type(published.output) is RunOutput
    assert type(published.output.file_results) is list
    assert type(published.output.file_results[0]) is FileResult
    extraction = published.output.file_results[0].results[target.label]
    assert type(extraction) is ExtractionResult
    assert replace(extraction, reason="consumer-copy").reason == "consumer-copy"
    assert asdict(published.output)["file_results"][0]["sample_name"] == "SampleA"


def test_publication_renders_the_finalized_numeric_hypothesis(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)
    finalized_value = finalized[0].completed_projection.decisions[0].value
    extraction = output.file_results[0].results[target.label]
    selected = extraction.selected_hypothesis
    assert selected is not None
    output.file_results[0].results[target.label] = replace(
        extraction,
        selected_hypothesis=replace(
            selected,
            integration=replace(
                selected.integration,
                area_ms1_morphology=None,
                area_raw_counts_seconds=selected.integration.area_raw_counts_seconds
                * 2,
            ),
        ),
    )

    published = publish_targeted_outputs(request, finalized, output)

    rendered = published.output.file_results[0].results[target.label]
    assert rendered.reported_peak_area == finalized_value


def test_manifest_does_not_claim_any_unstaged_stale_output(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(
        config,
        keep_intermediate_csv=False,
        emit_score_breakdown=False,
    )
    stale_outputs = {
        "output_csv": config.output_csv,
        "long_csv": config.output_csv.with_name("xic_results_long.csv"),
        "diagnostics_csv": config.diagnostics_csv,
        "score_breakdown_csv": config.output_csv.with_name("xic_score_breakdown.csv"),
    }
    config.output_csv.parent.mkdir(parents=True)
    for artifact_id, path in stale_outputs.items():
        path.write_bytes(f"stale {artifact_id}".encode())
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)

    publish_targeted_outputs(request, finalized, output)

    manifest = json.loads(
        config.output_csv.with_name("method_manifest.json").read_text(encoding="utf-8")
    )
    for artifact_id, path in stale_outputs.items():
        artifact = manifest["output_artifacts"][artifact_id]
        assert artifact["exists"] is False
        assert artifact["sha256"] is None
        assert path.read_bytes() == f"stale {artifact_id}".encode()


def test_neutral_loss_disabled_target_publishes_ms1_backfill(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
    target = replace(
        target,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
    )
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )

    finalized = execute_targeted_production_workflow(request, output)
    published = publish_targeted_outputs(request, finalized, output)

    assert config.output_csv in published.paths
    assert config.output_csv.is_file()


def test_render_diagnostics_remain_appendable_until_writers_finish(
    tmp_path: Path,
) -> None:
    from xic_extractor.target_pair_rt_calibration import (
        write_target_pair_rt_calibration_tsv,
    )

    config, target, output = production_case(tmp_path)
    calibration_path = tmp_path / "calibration.tsv"
    write_target_pair_rt_calibration_tsv(calibration_path, [])
    config = replace(
        config,
        keep_intermediate_csv=True,
        target_pair_rt_calibration_path=calibration_path,
        emit_peak_candidates=False,
    )
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)

    publication = publish_targeted_outputs(request, finalized, output)

    assert any(
        item.issue == "TARGET_PAIR_RT_AUTO_RESELECTION_SKIPPED"
        for item in publication.output.diagnostics
    )
    assert config.diagnostics_csv in publication.paths


def test_acquisition_baseline_projection_preserves_not_counted_cells(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
    extraction = output.file_results[0].results[target.label]
    projection = extraction.targeted_product_projection
    assert projection is not None
    output.file_results[0].results[target.label] = replace(
        extraction,
        targeted_product_projection=replace(
            projection,
            product_state="not_counted",
            counted_detection=False,
            projection_reason="baseline_nl_fail",
        ),
    )
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        output_projection="acquisition_baseline",
    )
    finalized = execute_targeted_production_workflow(request, output)

    publication = publish_targeted_outputs(request, finalized, output)

    with config.output_csv.with_name("xic_results_long.csv").open(
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        row = next(csv.DictReader(handle))
    assert row["Product State"] == "not_counted"
    assert row["Counted Detection"] == "FALSE"
    assert row["RT"] == "ND"
    assert row["Area"] == "ND"
    assert publication.output is not output


def test_gui_summary_consumes_the_finalized_publication_view(tmp_path: Path) -> None:
    from gui.workers.pipeline_worker import build_summary

    config, target, output = production_case(tmp_path)
    extraction = output.file_results[0].results[target.label]
    projection = extraction.targeted_product_projection
    assert projection is not None
    output.file_results[0].results[target.label] = replace(
        extraction,
        targeted_product_projection=replace(
            projection,
            product_state="not_counted",
            counted_detection=False,
            projection_reason="acquisition_nl_fail",
        ),
    )
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )
    service = TargetedApplicationService(
        lambda *_args, **_kwargs: output,
    )

    final_output = service.execute(request)
    final_projection = (
        final_output.file_results[0].results[target.label].targeted_product_projection
    )
    assert final_projection is not None
    assert final_projection.product_state == "detected_clean"
    assert final_projection.counted_detection is True
    summary = build_summary(
        config,
        [target],
        final_output,
        config.output_csv.with_name("xic_results.xlsx"),
    )

    assert final_output is not output
    assert summary["targets"][0]["detected"] == 1
    assert (
        summary["targets"][0]["median_area"]
        == final_output.file_results[0].results[target.label].reported_peak_area
    )


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


def test_concurrent_non_replacing_publications_are_collision_atomic(
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
    original_commit = targeted_output_boundary._commit_staged_outputs
    commit_starts = Barrier(2)

    def synchronize_commit(*args: object, **kwargs: object) -> tuple[Path, ...]:
        commit_starts.wait(timeout=10)
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(
        targeted_output_boundary,
        "_commit_staged_outputs",
        synchronize_commit,
    )

    def publish_with_marker(marker: str) -> tuple[str, object]:
        evidence = replace(
            output,
            diagnostics=[
                DiagnosticRecord(
                    sample_name="SampleA",
                    target_label=target.label,
                    issue="FILE_ERROR",
                    reason=marker,
                )
            ],
        )
        try:
            return ("success", publish_targeted_outputs(request, finalized, evidence))
        except Exception as exc:  # noqa: BLE001 - assert the public failure below
            return ("error", exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                publish_with_marker,
                ("concurrent-a", "concurrent-b"),
            )
        )

    successes = [value for status, value in outcomes if status == "success"]
    failures = [value for status, value in outcomes if status == "error"]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], TargetedOutputBoundaryError)

    winning = successes[0]
    assert isinstance(winning, targeted_output_boundary.TargetedPublication)
    expected_paths = {
        config.output_csv,
        config.output_csv.with_name("xic_results_long.csv"),
        config.diagnostics_csv,
        config.output_csv.with_name("method_manifest.json"),
    }
    assert set(winning.paths) == expected_paths
    assert all(path.is_file() for path in winning.paths)
    diagnostics_text = config.diagnostics_csv.read_text(encoding="utf-8-sig")
    assert (
        sum(marker in diagnostics_text for marker in ("concurrent-a", "concurrent-b"))
        == 1
    )
    assert not list(config.output_csv.parent.glob(".xic-targeted-publish-*"))
    assert not (config.output_csv.parent / ".xic-targeted-publish.lock").exists()


def test_concurrent_commits_lock_every_destination_parent(tmp_path: Path) -> None:
    commit_starts = Barrier(2)
    shared_final = tmp_path / "shared" / "results.xlsx"

    def commit(run_name: str) -> tuple[str, object]:
        stage_root = tmp_path / f"stage-{run_name}"
        unique_stage = stage_root / "unique.csv"
        shared_stage = stage_root / "shared.xlsx"
        unique_final = tmp_path / run_name / "unique.csv"
        stage_root.mkdir()
        unique_stage.write_text(run_name, encoding="utf-8")
        shared_stage.write_text(run_name, encoding="utf-8")
        commit_starts.wait(timeout=10)
        try:
            paths = targeted_output_boundary._commit_staged_outputs(
                {
                    unique_stage: unique_final,
                    shared_stage: shared_final,
                }
            )
            return ("success", paths)
        except Exception as exc:  # noqa: BLE001 - assert the public failure below
            return ("error", exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(commit, ("run-a", "run-b")))

    successes = [value for status, value in outcomes if status == "success"]
    failures = [value for status, value in outcomes if status == "error"]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], TargetedOutputBoundaryError)

    winner = shared_final.read_text(encoding="utf-8")
    assert winner in {"run-a", "run-b"}
    assert (tmp_path / winner / "unique.csv").read_text(encoding="utf-8") == winner
    loser = "run-b" if winner == "run-a" else "run-a"
    assert not (tmp_path / loser / "unique.csv").exists()
    assert not list(tmp_path.rglob(".xic-targeted-publish.lock"))


def test_separate_process_commits_are_collision_atomic(tmp_path: Path) -> None:
    shared_final = tmp_path / "shared" / "results.csv"
    staged_paths = (tmp_path / "a.csv", tmp_path / "b.csv")
    for staged, marker in zip(staged_paths, ("process-a", "process-b"), strict=True):
        staged.write_text(marker, encoding="utf-8")

    spawn_context = get_context("spawn")
    with spawn_context.Manager() as manager:
        start_barrier = manager.Barrier(2)
        with ProcessPoolExecutor(
            max_workers=2,
            mp_context=spawn_context,
        ) as executor:
            futures = tuple(
                executor.submit(
                    _commit_staged_output_in_process,
                    str(staged),
                    str(shared_final),
                    start_barrier,
                )
                for staged in staged_paths
            )
            outcomes = tuple(future.result(timeout=45) for future in futures)

    assert [status for status, _ in outcomes].count("success") == 1
    assert [status for status, _ in outcomes].count("error") == 1
    assert shared_final.read_text(encoding="utf-8") in {"process-a", "process-b"}
    assert not (shared_final.parent / ".xic-targeted-publish.lock").exists()


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


def test_commit_cancellation_after_first_move_rolls_back_all_final_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
    cancellation_requested = False
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        should_stop=lambda: cancellation_requested,
    )
    finalized = execute_targeted_production_workflow(request, output)
    original_replace = Path.replace

    def cancel_after_first_commit(source: Path, target_path: Path) -> Path:
        nonlocal cancellation_requested
        result = original_replace(source, target_path)
        if source.parent.name.startswith(".xic-targeted-publish-"):
            cancellation_requested = True
        return result

    monkeypatch.setattr(Path, "replace", cancel_after_first_commit)

    publication = publish_targeted_outputs(request, finalized, output)

    assert publication.paths == ()
    assert not any(config.output_csv.parent.iterdir())


def test_commit_cancellation_after_final_move_rolls_back_output(
    tmp_path: Path,
) -> None:
    staged = tmp_path / "staged.csv"
    final = tmp_path / "final.csv"
    staged.write_text("value\n", encoding="utf-8")
    cancellation_checks = iter((False, True))

    with pytest.raises(targeted_output_boundary._TargetedPublicationCancelled):
        targeted_output_boundary._commit_staged_outputs(
            {staged: final},
            should_cancel=lambda: next(cancellation_checks),
        )

    assert staged.is_file()
    assert not final.exists()


def test_replay_replaces_its_owned_output_set(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    config = replace(config, keep_intermediate_csv=True)
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
    )
    finalized = execute_targeted_production_workflow(request, output)
    original_paths = publish_targeted_outputs(request, finalized, output).paths
    original_paths[0].write_bytes(b"stale replay output")

    replay_request = replace(request, replace_existing_outputs=True)
    replay_paths = publish_targeted_outputs(replay_request, finalized, output).paths

    assert replay_paths == original_paths
    assert all(path.is_file() for path in replay_paths)
    assert all(path.read_bytes() != b"stale replay output" for path in replay_paths)


def test_replay_commit_failure_restores_previous_output_set(
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
    original_paths = publish_targeted_outputs(request, finalized, output).paths
    original_bytes = {path: path.read_bytes() for path in original_paths}
    original_replace = Path.replace
    commit_count = 0

    def fail_second_new_output(source: Path, target_path: Path) -> Path:
        nonlocal commit_count
        if source.parent.name.startswith(".xic-targeted-publish-"):
            commit_count += 1
            if commit_count == 2:
                raise OSError("replacement failed")
        return original_replace(source, target_path)

    monkeypatch.setattr(Path, "replace", fail_second_new_output)

    with pytest.raises(TargetedOutputBoundaryError, match="rolled back"):
        publish_targeted_outputs(
            replace(request, replace_existing_outputs=True),
            finalized,
            output,
        )

    assert {path: path.read_bytes() for path in original_paths} == original_bytes


def test_failed_rollback_preserves_recoverable_previous_outputs(
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
    original_paths = publish_targeted_outputs(request, finalized, output).paths
    original_bytes = {path.read_bytes() for path in original_paths}
    original_replace = Path.replace
    commit_count = 0
    restore_failed = False

    def fail_commit_and_one_restore(source: Path, target_path: Path) -> Path:
        nonlocal commit_count, restore_failed
        if source.parent.name.startswith(".xic-targeted-publish-"):
            commit_count += 1
            if commit_count == 2:
                raise OSError("replacement failed")
        if source.parent.name == ".existing" and not restore_failed:
            restore_failed = True
            raise OSError("restore blocked")
        return original_replace(source, target_path)

    monkeypatch.setattr(Path, "replace", fail_commit_and_one_restore)

    with pytest.raises(
        TargetedOutputBoundaryError,
        match="recoverable files retained",
    ):
        publish_targeted_outputs(
            replace(request, replace_existing_outputs=True),
            finalized,
            output,
        )

    recovery_roots = list(config.output_csv.parent.glob(".xic-targeted-publish-*"))
    assert len(recovery_roots) == 1
    recoverable = [
        path.read_bytes()
        for path in (recovery_roots[0] / ".existing").iterdir()
        if path.is_file()
    ]
    assert recoverable
    assert set(recoverable).issubset(original_bytes)


def test_service_rejects_finalized_results_from_another_acquisition(
    tmp_path: Path,
) -> None:
    current_root = tmp_path / "current"
    stale_root = tmp_path / "stale"
    current_root.mkdir()
    stale_root.mkdir()
    current_config, current_target, current_output = production_case(current_root)
    stale_config, stale_target, stale_output = production_case(stale_root)
    (stale_config.data_dir / "SampleA.raw").write_bytes(b"stale acquisition")
    stale_request = build_targeted_application_request(
        stale_config,
        [stale_target],
        injection_order={"SampleA": 1},
    )
    stale_finalized = execute_targeted_production_workflow(
        stale_request,
        stale_output,
    )
    current_request = build_targeted_application_request(
        current_config,
        [current_target],
        injection_order={"SampleA": 1},
    )
    service = TargetedApplicationService(
        lambda *_args, **_kwargs: current_output,
        finalized_workflow_runner=lambda *_args: stale_finalized,
        output_publisher=lambda *_args: pytest.fail("crossed result was published"),
    )

    with pytest.raises(
        TargetedRuntimeAdapterError,
        match="do not belong to the current acquisition",
    ):
        service.execute_application(current_request)


def test_cancellation_after_typed_completion_never_invokes_publisher(
    tmp_path: Path,
) -> None:
    config, target, output = production_case(tmp_path)
    stop_results = iter((False, True))
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        should_stop=lambda: next(stop_results),
    )

    service = TargetedApplicationService(
        lambda *_args, **_kwargs: output,
        finalized_workflow_runner=execute_targeted_production_workflow,
        output_publisher=lambda *_args: pytest.fail("cancelled run published output"),
    )

    execution = service.execute_application(request)

    assert execution.active_output is output
    assert execution.finalized_results
    assert execution.published_paths == ()


def test_cancellation_after_acquisition_skips_finalization(tmp_path: Path) -> None:
    config, target, output = production_case(tmp_path)
    request = build_targeted_application_request(
        config,
        [target],
        injection_order={"SampleA": 1},
        should_stop=lambda: True,
    )
    service = TargetedApplicationService(
        lambda *_args, **_kwargs: output,
        finalized_workflow_runner=lambda *_args: pytest.fail(
            "cancelled run invoked finalization"
        ),
        output_publisher=lambda *_args: pytest.fail("cancelled run published output"),
    )

    execution = service.execute_application(request)

    assert execution.active_output is output
    assert execution.finalized_results == ()
    assert execution.published_paths == ()


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
