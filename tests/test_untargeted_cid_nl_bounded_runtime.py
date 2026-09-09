"""End-to-end bounded cohort closure through the retained scientific owners."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import Workbook

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_real_data_adapter import _peak_config, _settings
from tests.test_untargeted_cid_nl_batch_runtime import _nearby_subject_candidates
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _current_candidates,
    _discovery_receipts,
    _install_runtime_raws,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import read_known_istd_reference
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.alignment.workflow import CompletedUntargetBatchWorkflowResult
from xic_extractor.execution_runtime import AttemptTerminalOutcome
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    compose_untargeted_cid_nl_production_result,
    execute_untargeted_cid_nl_production_batch,
    materialize_untargeted_cid_nl_partition,
    prepare_untargeted_cid_nl_production_plan,
)
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.sample_metadata import SampleMetadata


def _plan(tmp_path, monkeypatch, *, scenario="positive"):
    paths = tuple(tmp_path / f"S{i}.synthetic" for i in range(4))
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)
    raws = {}
    candidates = {}
    for path in paths:
        path.write_bytes(path.stem.encode())
        raw, sample_candidates = _nearby_subject_candidates(
            path, settings=settings, peak_config=peak_config
        )
        if scenario == "unsupported":
            raw.intensity[:] = 0.0
            sample_candidates = _current_candidates(
                raw, raw_file=path, settings=settings, peak_config=peak_config
            )
        raws[path] = raw
        candidates[path.stem] = (
            () if scenario == "recovery" and path == paths[-1] else sample_candidates
        )
    plan = prepare_untargeted_cid_nl_production_plan(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=path.stem,
                raw_stem=path.stem,
                injection_order=index + 1,
                sample_role="study_sample",
            )
            for index, path in enumerate(paths)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:bounded-runtime",
    )
    _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    reference = None
    if scenario == "istd":
        workbook = Workbook()
        targets = workbook.active
        targets.title = "Targets"
        targets.append(
            [
                "Label",
                "Role",
                "m/z",
                "RT min",
                "RT max",
                "ppm tol",
                "NL (Da)",
                "NL ppm warn",
                "NL ppm max",
                "ISTD Pair",
            ]
        )
        targets.append(["known", "ISTD", 500, 7, 10, 20, 116.0474, 20, 50, ""])
        results = workbook.create_sheet("XIC Results")
        results.append(["SampleName", "Target", "Role", "RT", "Area"])
        for path in paths:
            results.append([path.stem, "known", "ISTD", 7.98, -999])
        reference_path = tmp_path / "reference.xlsx"
        workbook.save(reference_path)
        reference = read_known_istd_reference(reference_path)
    assert len(plan.subject_batch.subjects) == 2
    return plan, reference


@pytest.mark.parametrize("scenario", ["positive", "recovery", "unsupported", "istd"])
def test_bounded_runtime_matches_eager_after_disk_owner_round_trip(
    tmp_path: Path, monkeypatch, scenario: str
):
    from xic_extractor.extraction.untargeted_cid_nl_bounded_runtime import (
        execute_bounded_untargeted_cid_nl,
    )

    plan, reference = _plan(tmp_path, monkeypatch, scenario=scenario)
    config = AlignmentConfig(anchor_min_seed_events=1)
    batch = materialize_untargeted_cid_nl_partition(plan, plan.subject_batch)
    eager_runtime = execute_untargeted_cid_nl_production_batch(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "eager.json", "eager"),
    )
    eager = compose_untargeted_cid_nl_production_result(
        batch, eager_runtime, alignment_config=config, known_istd_reference=reference
    )
    marker = OwnerPathRuntimeMarker(tmp_path / "bounded.json", "bounded")
    progress_events = []
    bounded = execute_bounded_untargeted_cid_nl(
        plan,
        output_dir=tmp_path / "bounded-output",
        alignment_config=config,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=marker,
        known_istd_reference=reference,
        progress_callback=progress_events.append,
        max_partition_jobs=len(plan.context.samples),
    )
    assert type(bounded) is CompletedUntargetBatchWorkflowResult
    assert type(bounded.application_result.children) is ImmutableSequence
    assert project_completed_untarget_batch_writer_inputs(bounded).matrix == (
        project_completed_untarget_batch_writer_inputs(eager).matrix
    )
    actual = bounded.application_result
    expected = eager.application_result
    assert actual.candidate_ids == expected.candidate_ids
    assert actual.physical_observation_ids == expected.physical_observation_ids
    assert tuple(actual.excluded_candidates) == expected.excluded_candidates
    owners = plan.context.processing_owners
    assert actual.processing_request is owners.request
    assert actual.processing_attempt is owners.attempt
    assert bounded.runtime_receipt.supervisor.attempt is owners.attempt
    assert all(
        left is right
        for left, right in zip(
            actual.canonical_samples, expected.canonical_samples, strict=True
        )
    )
    for child in actual.children:
        assert child.processing_request is owners.request
        assert (
            child.scientific_core.cell_observation_handoff.coverage.epoch.processing_owners
            is owners
        )
        assert all(
            left is right
            for left, right in zip(
                child.canonical_samples, expected.canonical_samples, strict=True
            )
        )
    assert sorted(
        item.artifact.evidence_artifact_id for item in bounded.method_assessments
    ) == sorted(item.artifact.evidence_artifact_id for item in eager.method_assessments)
    _assert_explanation_parity(tmp_path, plan, config, eager, bounded)
    partition_stages = [
        (event["stage"], event["completed"])
        for event in progress_events
        if event.get("event") == "partition_progress"
        and event["stage"]
        in {
            "partition_start",
            "partition_compose",
            "partition_released",
            "global_closure",
            "batch_completed",
        }
    ]
    assert partition_stages == [
        ("partition_start", 0),
        ("partition_compose", 0),
        ("partition_released", 1),
        ("partition_start", 1),
        ("partition_compose", 1),
        ("partition_released", 2),
        ("global_closure", 2),
        ("batch_completed", 2),
    ]
    eager_manifest = eager_runtime.finalization.coordinator.state.runtime.manifest
    assert len(eager_manifest.work_unit_keys) == len(batch.jobs)
    snapshot = marker.read()
    assert snapshot.terminal_outcome is AttemptTerminalOutcome.SUCCEEDED
    assert len(snapshot.manifest_work_unit_keys) == (
        len(plan.subject_batch.subjects) * len(plan.context.samples) * 2
    )
    if scenario == "unsupported":
        assert not actual.children
        assert len(actual.excluded_candidates) == len(actual.candidate_ids)
    else:
        assert actual.children
    if scenario == "recovery":
        assert any(result.recovery_attempts for result in eager_runtime.sample_results)


def _assert_explanation_parity(tmp_path, plan, config, eager, bounded):
    from xic_extractor.alignment.tsv_writer import write_alignment_matrix_tsv
    from xic_extractor.diagnostics.cid_nl_explanation import read_cid_nl_explanation
    from xic_extractor.diagnostics.cid_nl_explanation_projection import (
        write_cid_nl_explanation,
    )

    candidate_id = plan.subject_batch.candidate_memberships[0][0]
    records = []
    for name, completed in (("eager", eager), ("bounded", bounded)):
        writer = project_completed_untarget_batch_writer_inputs(completed)
        matrix = tmp_path / f"{name}.tsv"
        write_alignment_matrix_tsv(
            matrix,
            writer.matrix,
            alignment_config=config,
            production_decisions=writer.production_decisions,
        )
        report = write_cid_nl_explanation(
            tmp_path / f"{name}.sqlite",
            completed=completed,
            writer_inputs=writer,
            candidates_by_sample=plan.context.candidates_by_sample,
            matrix_path=matrix,
            alignment_config=config,
        )
        detail = read_cid_nl_explanation(report, candidate_id=candidate_id)
        records.append(detail["records"])
    assert records[0] == records[1]


@pytest.mark.parametrize("failure_seam", ["later_partition", "later_spool"])
def test_bounded_runtime_failure_never_publishes_completed_batch(
    tmp_path: Path, monkeypatch, failure_seam: str
):
    import xic_extractor.extraction.untargeted_cid_nl_bounded_runtime as bounded_owner

    plan, _ = _plan(tmp_path, monkeypatch)
    marker = OwnerPathRuntimeMarker(tmp_path / "failed.json", "failed")
    original_error = OSError("second partition storage unavailable")
    calls = []
    if failure_seam == "later_partition":
        seam_owner = bounded_owner
        seam_name = "execute_untargeted_cid_nl_runtime_partition"
    else:
        seam_owner = bounded_owner.PartitionResultStore
        seam_name = "append"
    original = getattr(seam_owner, seam_name)

    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise original_error
        return original(*args, **kwargs)

    monkeypatch.setattr(seam_owner, seam_name, fail_second)

    def forbid_completed(*args, **kwargs):
        pytest.fail("An incomplete partition must not publish a completed result")

    monkeypatch.setattr(
        bounded_owner, "CompletedUntargetBatchWorkflowResult", forbid_completed
    )
    with pytest.raises(RuntimeError, match="failed before batch publication") as error:
        bounded_owner.execute_bounded_untargeted_cid_nl(
            plan,
            output_dir=tmp_path / "failed-output",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            max_partition_jobs=len(plan.context.samples),
        )
    assert len(calls) == 2
    assert error.value.__cause__ is original_error
    snapshot = marker.read()
    assert snapshot.terminal_outcome is AttemptTerminalOutcome.FAILED
    assert snapshot.processing_attempt_id == (
        plan.context.processing_owners.attempt.processing_attempt_id
    )
