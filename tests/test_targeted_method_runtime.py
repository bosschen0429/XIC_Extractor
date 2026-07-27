from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_diagnostic_method import _method_request
from xic_extractor.execution_runtime import (
    AttemptLifecycleEventKind,
    AttemptLifecycleHistory,
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitTerminalOutcome,
    append_attempt_lifecycle_event,
    preflight_required_work_manifest,
    start_attempt_runtime,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeArtifactDisposition,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.extraction.targeted_diagnostic_method import (
    TargetedDiagnosticMethodRequest,
    canonical_targeted_diagnostic_method_bytes,
)
from xic_extractor.extraction.targeted_method_runtime import (
    TargetedMethodAlignmentJob,
    TargetedMethodCompletionJob,
    TargetedMethodCompletionSpec,
    TargetedMethodRuntimeError,
    alignment_upstream_artifacts_for_request,
    build_targeted_method_completion_jobs,
    execute_targeted_method_alignment_runtime,
    execute_targeted_method_completion_runtime,
)
from xic_extractor.extraction.targeted_runtime_adapter import (
    TargetedProcessResourceReport,
    TargetedProcessWorkerResultEnvelope,
    TargetedRuntimeAdapterError,
    TargetedRuntimeWorkerBatch,
    mint_targeted_process_result_ref,
    resolve_targeted_application_method_result,
)
from xic_extractor.processing_identity import ProcessingAttemptIdentity


class _ExplodingBackfillSource:
    def extract_xic(self, *_args):
        raise RuntimeError("simulated spawned worker loss")


class _ForbiddenExecutor:
    def __init__(self, **_kwargs) -> None:
        raise AssertionError("cancelled runtime must not dispatch a process")


def _partial_worker_loss_batch(
    job: TargetedMethodCompletionJob,
    error: Exception,
) -> TargetedRuntimeWorkerBatch:
    partial = mint_targeted_process_result_ref(
        processing_attempt=job.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=b"partial-before-worker-loss",
        upstream_artifacts=(job.alignment_result_artifact,),
    )
    return TargetedRuntimeWorkerBatch(
        result_envelopes=(
            TargetedProcessWorkerResultEnvelope(
                attempt=job.attempt,
                work_unit_key=job.work_unit_key,
                outcome=WorkUnitTerminalOutcome.FAILED,
                failure_detail=WorkUnitFailureDetail(
                    category=WorkUnitFailureCategory.WORKER_LOSS,
                    stage="targeted-process-worker",
                    source_reference=f"no-raw:{job.work_unit_key}",
                    message=f"{type(error).__name__}: {error}",
                ),
            ),
        ),
        resource_reports=(
            TargetedProcessResourceReport(
                attempt=job.attempt,
                resource_key=job.resource_key,
                open_outcome=(
                    RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS
                ),
                cleanup_outcome=(
                    RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS
                ),
            ),
        ),
        runtime_artifact_refs=(partial,),
    )


def test_true_spawned_alignment_to_targeted_is_canonical_when_reordered(
    tmp_path: Path,
) -> None:
    request, fixture = _method_request(tmp_path)
    serial_alignment_jobs = (
        _alignment_job(
            replace(
                request,
                alignment_inputs=tuple(reversed(request.alignment_inputs)),
            ),
            fixture.attempt,
            "alignment:a",
        ),
        _alignment_job(request, fixture.attempt, "alignment:b"),
    )
    manifest = ("alignment:a", "alignment:b")
    serial_alignment = execute_targeted_method_alignment_runtime(
        _runtime(fixture.attempt, manifest),
        serial_alignment_jobs,
        max_workers=1,
        resource_reservations=_reservations(serial_alignment_jobs),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path, "alignment-serial"),
    )
    alignment = execute_targeted_method_alignment_runtime(
        _runtime(fixture.attempt, manifest),
        tuple(reversed(serial_alignment_jobs)),
        max_workers=2,
        resource_reservations=_reservations(serial_alignment_jobs),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path, "alignment-reordered"),
    )

    assert serial_alignment.successful_cross_sample_peak_groups == (
        alignment.successful_cross_sample_peak_groups
    )
    assert alignment.finalization_record.terminal_outcome is (
        AttemptTerminalOutcome.SUCCEEDED
    )
    assert tuple(
        key for key, _groups in alignment.successful_cross_sample_peak_groups
    ) == ("alignment:a", "alignment:b")
    assert alignment.successful_cross_sample_peak_groups[0][1] == (
        alignment.successful_cross_sample_peak_groups[1][1]
    )

    serial_specs = (
        TargetedMethodCompletionSpec(
            alignment_work_unit_key="alignment:a",
            targeted_work_unit_key="targeted:a",
            resource_key="process:targeted:a",
            request=request,
        ),
        TargetedMethodCompletionSpec(
            alignment_work_unit_key="alignment:b",
            targeted_work_unit_key="targeted:b",
            resource_key="process:targeted:b",
            request=request,
        ),
    )
    serial_jobs = build_targeted_method_completion_jobs(
        serial_alignment,
        serial_specs,
    )
    serial_targeted = execute_targeted_method_completion_runtime(
        _runtime(fixture.attempt, ("targeted:a", "targeted:b")),
        serial_jobs,
        max_workers=1,
        resource_reservations=_reservations(serial_jobs),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path, "targeted-serial"),
    )
    jobs = build_targeted_method_completion_jobs(
        alignment,
        tuple(reversed(serial_specs)),
    )
    targeted = execute_targeted_method_completion_runtime(
        _runtime(fixture.attempt, ("targeted:a", "targeted:b")),
        jobs,
        max_workers=2,
        resource_reservations=_reservations(serial_jobs),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path, "targeted-reordered"),
    )

    results = targeted.settlement.successful_targeted_method_results
    serial_results = serial_targeted.settlement.successful_targeted_method_results
    assert tuple(
        (key, canonical_targeted_diagnostic_method_bytes(result))
        for key, result in serial_results
    ) == tuple(
        (key, canonical_targeted_diagnostic_method_bytes(result))
        for key, result in results
    )
    assert targeted.finalization_record.terminal_outcome is (
        AttemptTerminalOutcome.SUCCEEDED
    )
    assert tuple(key for key, _result in results) == ("targeted:a", "targeted:b")
    assert canonical_targeted_diagnostic_method_bytes(results[0][1]) == (
        canonical_targeted_diagnostic_method_bytes(results[1][1])
    )
    completion_groups = {job.work_unit_key: job.alignment_groups for job in jobs}
    assert all(
        result.groups == completion_groups[key] for key, result in results
    )
    result = results[0][1]
    backfilled_index = next(
        index for index, cell in enumerate(result.cells) if cell.status == "backfilled"
    )
    backfilled = result.cells[backfilled_index]
    assert backfilled.selected_integration is not None
    tampered_cells = list(result.cells)
    tampered_cells[backfilled_index] = replace(
        backfilled,
        selected_integration=replace(
            backfilled.selected_integration,
            area_raw_counts_seconds=(
                backfilled.selected_integration.area_raw_counts_seconds + 1.0
            ),
        ),
    )
    tampered = replace(result, cells=tuple(tampered_cells))
    with pytest.raises(
        TargetedRuntimeAdapterError,
        match="exactly one matching method result",
    ):
        resolve_targeted_application_method_result(targeted.settlement, tampered)


def test_failed_alignment_carrier_cannot_enter_targeted_stage(tmp_path: Path) -> None:
    request, fixture = _method_request(tmp_path)
    invalid_request = replace(
        request,
        alignment_inputs=(request.alignment_inputs[0], request.alignment_inputs[0]),
    )
    job = _alignment_job(invalid_request, fixture.attempt, "alignment:failed")
    alignment = execute_targeted_method_alignment_runtime(
        _runtime(fixture.attempt, ("alignment:failed",)),
        (job,),
        max_workers=1,
        resource_reservations=_reservations((job,)),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path, "alignment-failed"),
    )

    assert alignment.finalization_record.terminal_outcome is (
        AttemptTerminalOutcome.FAILED
    )
    assert alignment.successful_cross_sample_peak_groups == ()
    with pytest.raises(TargetedMethodRuntimeError, match="only an integrity-clean"):
        build_targeted_method_completion_jobs(
            alignment,
            (
                TargetedMethodCompletionSpec(
                    alignment_work_unit_key="alignment:failed",
                    targeted_work_unit_key="targeted:forbidden",
                    resource_key="process:targeted:forbidden",
                    request=request,
                ),
            ),
        )


def test_cancellation_stops_before_targeted_spawn(tmp_path: Path) -> None:
    request, fixture = _method_request(tmp_path)
    alignment = _successful_alignment(tmp_path, request, fixture.attempt)
    jobs = build_targeted_method_completion_jobs(
        alignment,
        (_completion_spec(request),),
    )
    targeted = execute_targeted_method_completion_runtime(
        _runtime(fixture.attempt, ("targeted:method",), cancelled=True),
        jobs,
        max_workers=1,
        resource_reservations=_reservations(jobs),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path, "targeted-cancelled"),
        executor_factory=_ForbiddenExecutor,
    )

    assert targeted.finalization_record.terminal_outcome is (
        AttemptTerminalOutcome.CANCELLED
    )
    assert targeted.settlement.successful_targeted_method_results == ()
    resource = targeted.coordinator_record.state.resources[0]
    assert resource.open_outcome is RuntimeResourceOpenOutcome.NOT_OPENED
    assert resource.cleanup_outcome is RuntimeResourceCleanupOutcome.SUCCEEDED


def test_worker_loss_and_partial_artifact_fail_closed(tmp_path: Path) -> None:
    request, fixture = _method_request(tmp_path)
    alignment = _successful_alignment(tmp_path, request, fixture.attempt)
    exploding = replace(
        request,
        backfill_sources={
            **request.backfill_sources,
            "eligible-backfill": _ExplodingBackfillSource(),
        },
    )
    jobs = build_targeted_method_completion_jobs(
        alignment,
        (_completion_spec(exploding),),
    )

    worker_loss = execute_targeted_method_completion_runtime(
        _runtime(fixture.attempt, ("targeted:method",)),
        jobs,
        max_workers=1,
        resource_reservations=_reservations(jobs),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path, "targeted-worker-loss"),
    )
    assert worker_loss.finalization_record.terminal_outcome is (
        AttemptTerminalOutcome.FAILED
    )
    assert worker_loss.settlement.successful_targeted_method_results == ()
    resource = worker_loss.coordinator_record.state.resources[0]
    assert resource.open_outcome is (
        RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS
    )

    partial = execute_targeted_method_completion_runtime(
        _runtime(fixture.attempt, ("targeted:method",)),
        jobs,
        max_workers=1,
        resource_reservations=_reservations(jobs),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path, "targeted-partial"),
        error_factory=_partial_worker_loss_batch,
    )
    assert partial.settlement.successful_targeted_method_results == ()
    assert tuple(
        disposition
        for _artifact_id, disposition in (
            partial.finalization_record.artifact_dispositions
        )
    ) == (RuntimeArtifactDisposition.QUARANTINED_DIAGNOSTIC,)


def test_conflicting_targeted_replay_clears_scientific_carrier(
    tmp_path: Path,
) -> None:
    request, fixture = _method_request(tmp_path)
    alignment_jobs = (
        _alignment_job(request, fixture.attempt, "alignment:a"),
        _alignment_job(request, fixture.attempt, "alignment:b"),
    )
    alignment = execute_targeted_method_alignment_runtime(
        _runtime(fixture.attempt, ("alignment:a", "alignment:b")),
        alignment_jobs,
        max_workers=2,
        resource_reservations=_reservations(alignment_jobs),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path, "alignment-conflict-source"),
    )
    jobs = build_targeted_method_completion_jobs(
        alignment,
        (
            TargetedMethodCompletionSpec(
                alignment_work_unit_key="alignment:a",
                targeted_work_unit_key="targeted:shared",
                resource_key="process:targeted:a",
                request=request,
            ),
            TargetedMethodCompletionSpec(
                alignment_work_unit_key="alignment:b",
                targeted_work_unit_key="targeted:shared",
                resource_key="process:targeted:b",
                request=request,
            ),
        ),
    )
    targeted = execute_targeted_method_completion_runtime(
        _runtime(fixture.attempt, ("targeted:shared",)),
        jobs,
        max_workers=2,
        resource_reservations=_reservations(jobs),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path, "targeted-conflict"),
    )

    assert targeted.settlement.runtime.integrity_failure is not None
    assert targeted.finalization_record.terminal_outcome is (
        AttemptTerminalOutcome.FAILED
    )
    assert targeted.settlement.successful_targeted_method_results == ()


def _successful_alignment(
    tmp_path: Path,
    request: TargetedDiagnosticMethodRequest,
    attempt: ProcessingAttemptIdentity,
):
    job = _alignment_job(request, attempt, "alignment:method")
    return execute_targeted_method_alignment_runtime(
        _runtime(attempt, ("alignment:method",)),
        (job,),
        max_workers=1,
        resource_reservations=_reservations((job,)),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path, "alignment-success"),
    )


def _alignment_job(
    request: TargetedDiagnosticMethodRequest,
    attempt: ProcessingAttemptIdentity,
    work_unit_key: str,
) -> TargetedMethodAlignmentJob:
    return TargetedMethodAlignmentJob(
        attempt=attempt,
        work_unit_key=work_unit_key,
        resource_key=f"process:{work_unit_key}",
        alignment_inputs=request.alignment_inputs,
        target=request.alignment_inputs[0].target,
        config=request.alignment_config,
        upstream_artifacts=alignment_upstream_artifacts_for_request(request),
    )


def _completion_spec(
    request: TargetedDiagnosticMethodRequest,
) -> TargetedMethodCompletionSpec:
    return TargetedMethodCompletionSpec(
        alignment_work_unit_key="alignment:method",
        targeted_work_unit_key="targeted:method",
        resource_key="process:targeted:method",
        request=request,
    )


def _runtime(
    attempt: ProcessingAttemptIdentity,
    work_unit_keys: tuple[str, ...],
    *,
    cancelled: bool = False,
) -> AttemptRuntime:
    history = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    runtime = start_attempt_runtime(
        preflight_required_work_manifest(
            history,
            attempt=attempt,
            work_unit_keys=work_unit_keys,
            owner_completeness_attestation="targeted-method-runtime-test:v1",
        )
    )
    if cancelled:
        runtime = replace(
            runtime,
            history=append_attempt_lifecycle_event(
                runtime.history,
                AttemptLifecycleEventKind.CANCELLATION_ACCEPTED,
            ),
        )
    return runtime


def _reservations(jobs) -> tuple[RuntimeResourceReservation, ...]:
    return tuple(
        RuntimeResourceReservation(
            attempt=job.attempt,
            kind=RuntimeResourceKind.PROCESS_WORKER,
            resource_key=job.resource_key,
            worker_key=f"worker:{index}",
            source_reference=f"no-raw:{job.work_unit_key}:{index}",
            compatibility_token="targeted-method-runtime-test:v1",
        )
        for index, job in enumerate(jobs)
    )


def _marker(tmp_path: Path, stem: str) -> OwnerPathRuntimeMarker:
    return OwnerPathRuntimeMarker(
        tmp_path / f"{stem}.runtime.json",
        f"targeted-method-runtime:{stem}",
    )
