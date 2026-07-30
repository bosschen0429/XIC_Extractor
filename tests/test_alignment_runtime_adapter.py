from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from xic_extractor.alignment.runtime_adapter import (
    AlignmentRuntimeProcessResult,
    execute_alignment_process_runtime,
    mint_alignment_process_result_artifact,
)
from xic_extractor.execution_runtime import (
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeArtifactDisposition,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    canonical_coordinator_terminal_record_bytes,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    mint_work_unit_settlement,
    start_runtime_attempt,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    ProcessingAttemptIdentity,
    build_acquisition_source_identity,
    build_processing_request_identity,
    issue_processing_attempt,
)


@dataclass(frozen=True)
class _NoRawAlignmentJob:
    attempt: ProcessingAttemptIdentity
    work_unit_key: str
    resource_key: str
    payload: bytes
    delay_sec: float = 0.0
    fail: bool = False


def _run_no_raw_alignment_job(
    job: _NoRawAlignmentJob,
) -> AlignmentRuntimeProcessResult:
    if job.delay_sec:
        time.sleep(job.delay_sec)
    if job.fail:
        raise RuntimeError(f"spawned failure:{job.work_unit_key}")
    artifact = mint_alignment_process_result_artifact(
        processing_attempt=job.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=job.payload,
    )
    return AlignmentRuntimeProcessResult(
        settlement=mint_work_unit_settlement(
            attempt=job.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=artifact,
        ),
        resource_key=job.resource_key,
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
        runtime_artifacts=(artifact,),
    )


def _alignment_job_error(
    job: _NoRawAlignmentJob,
    error: Exception,
) -> AlignmentRuntimeProcessResult:
    return AlignmentRuntimeProcessResult(
        settlement=mint_work_unit_settlement(
            attempt=job.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.FAILED,
            failure_detail=WorkUnitFailureDetail(
                category=WorkUnitFailureCategory.EXECUTION_ERROR,
                stage="alignment-process-worker",
                source_reference=f"no-raw:{job.work_unit_key}",
                message=str(error),
            ),
        ),
        resource_key=job.resource_key,
        open_outcome=RuntimeResourceOpenOutcome.NOT_OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )


def test_alignment_spawn_runner_is_canonical_across_serial_and_reordered_results(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime = _runtime(attempt, ("sample:a", "sample:b"))
    reservations = _reservations(attempt)
    jobs = (
        _NoRawAlignmentJob(
            attempt,
            "sample:a",
            "process:sample:a",
            b"alignment-a",
            delay_sec=0.05,
        ),
        _NoRawAlignmentJob(
            attempt,
            "sample:b",
            "process:sample:b",
            b"alignment-b",
        ),
    )
    serial = execute_alignment_process_runtime(
        runtime,
        jobs,
        worker=_run_no_raw_alignment_job,
        error_factory=_alignment_job_error,
        max_workers=1,
        resource_reservations=reservations,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "serial.runtime.json",
            "alignment-runtime-artifacts:test",
        ),
    )
    reordered = execute_alignment_process_runtime(
        runtime,
        tuple(reversed(jobs)),
        worker=_run_no_raw_alignment_job,
        error_factory=_alignment_job_error,
        max_workers=2,
        resource_reservations=reservations,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "reordered.runtime.json",
            "alignment-runtime-artifacts:test",
        ),
    )

    assert serial.runtime.settlements == reordered.runtime.settlements
    assert canonical_coordinator_terminal_record_bytes(
        serial.coordinator_record
    ) == canonical_coordinator_terminal_record_bytes(reordered.coordinator_record)
    assert serial.finalization_record == reordered.finalization_record
    assert serial.finalization_record.terminal_outcome is (
        AttemptTerminalOutcome.SUCCEEDED
    )
    assert tuple(
        disposition
        for _artifact_id, disposition in (
            serial.finalization_record.artifact_dispositions
        )
    ) == (
        RuntimeArtifactDisposition.OFFERED_TO_OWNER,
        RuntimeArtifactDisposition.OFFERED_TO_OWNER,
    )


def test_alignment_spawn_failure_closes_resource_and_durable_terminal(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="failure")
    runtime = _runtime(attempt, ("sample:a",))
    marker = OwnerPathRuntimeMarker(
        tmp_path / "failed.runtime.json",
        "alignment-runtime-artifacts:failed",
    )
    jobs = (
        _NoRawAlignmentJob(
            attempt,
            "sample:a",
            "process:sample:a",
            b"unused",
            fail=True,
        ),
    )
    result = execute_alignment_process_runtime(
        runtime,
        jobs,
        worker=_run_no_raw_alignment_job,
        error_factory=_alignment_job_error,
        max_workers=1,
        resource_reservations=(_reservations(attempt)[0],),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=marker,
    )

    recovered = marker.recover_after_coordinator_loss(attempt)
    assert result.runtime.must_fail is True
    assert result.finalization_record.terminal_outcome is AttemptTerminalOutcome.FAILED
    assert recovered.finalization_acknowledged is True
    assert recovered.terminal_outcome is AttemptTerminalOutcome.FAILED
    failure_detail = result.runtime.settlements[0].failure_detail
    assert failure_detail is not None
    assert failure_detail.message == "spawned failure:sample:a"
    assert recovered.coordinator_record_bytes is not None
    recovered_payload = json.loads(recovered.coordinator_record_bytes)
    assert recovered_payload["work_settlements"][0]["failure_detail"]["message"] == (
        "spawned failure:sample:a"
    )
    resource = result.coordinator_record.state.resources[0]
    assert resource.open_outcome is RuntimeResourceOpenOutcome.NOT_OPENED
    assert resource.cleanup_outcome is RuntimeResourceCleanupOutcome.SUCCEEDED


def test_alignment_preflight_failure_skips_runner_and_finalizes_marker(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="preflight")
    marker = OwnerPathRuntimeMarker(
        tmp_path / "alignment-preflight.runtime.json",
        "alignment-runtime-artifacts:preflight",
    )
    runner_calls: list[str] = []
    job = _NoRawAlignmentJob(
        attempt,
        "sample:undeclared",
        "process:sample:undeclared",
        b"must-not-run",
    )

    def worker(observed_job: _NoRawAlignmentJob) -> AlignmentRuntimeProcessResult:
        runner_calls.append(observed_job.work_unit_key)
        return _run_no_raw_alignment_job(observed_job)

    result = execute_alignment_process_runtime(
        start_runtime_attempt(
            attempt=attempt,
            work_unit_keys=(),
            owner_completeness_attestation="alignment-runtime-test:v1",
        ),
        (job,),
        worker=worker,
        error_factory=_alignment_job_error,
        max_workers=1,
        resource_reservations=(_reservations(attempt)[0],),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=marker,
    )

    recovered = marker.recover_after_coordinator_loss(attempt)
    assert runner_calls == []
    assert result.runtime.manifest_preflight_failed is True
    assert result.coordinator_record.state.resources == ()
    assert result.finalization_record.terminal_outcome is AttemptTerminalOutcome.FAILED
    assert recovered.finalization_acknowledged is True
    assert recovered.terminal_outcome is AttemptTerminalOutcome.FAILED


def _runtime(
    attempt: ProcessingAttemptIdentity,
    work_unit_keys: tuple[str, ...],
) -> AttemptRuntime:
    return start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=work_unit_keys,
        owner_completeness_attestation="alignment-runtime-test:v1",
    )


def _reservations(
    attempt: ProcessingAttemptIdentity,
) -> tuple[RuntimeResourceReservation, ...]:
    return tuple(
        RuntimeResourceReservation(
            attempt=attempt,
            kind=RuntimeResourceKind.PROCESS_WORKER,
            resource_key=f"process:sample:{suffix}",
            worker_key=f"worker:{suffix}",
            source_reference=f"no-raw:sample:{suffix}",
            compatibility_token="alignment:no-raw:v1",
        )
        for suffix in ("a", "b")
    )


def _attempt(
    tmp_path: Path,
    *,
    stem: str = "sample",
) -> ProcessingAttemptIdentity:
    raw_path = tmp_path / f"{stem}.raw"
    raw_path.write_bytes(f"synthetic-no-raw:{stem}".encode())
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo.raw.ms_controller_1",
    )
    request = build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key=f"{stem}.raw",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="alignment_runtime_test_v1",
        processing_algorithm_version="alignment_runtime_test_v1",
        processing_contract_version="processing_identity_v1",
        scientific_inputs={"method": "cid_nl", "neutral_loss_da": 116.0},
    )
    return issue_processing_attempt(request)
