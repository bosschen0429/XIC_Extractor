from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.runtime_adapter import (
    AlignmentRuntimeAdapterError,
    AlignmentRuntimeProcessResult,
    canonical_alignment_peak_group_result_bytes,
    execute_alignment_process_runtime,
    mint_alignment_process_result_artifact,
)
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
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
    RuntimeCancellationRequest,
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
    cross_sample_peak_groups: tuple[CrossSamplePeakGroupHypothesis, ...] | None = (
        None
    )
    upstream_artifacts: tuple[EvidenceArtifactRef, ...] = ()


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
        upstream_artifacts=job.upstream_artifacts,
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
        cross_sample_peak_groups=job.cross_sample_peak_groups,
        cross_sample_peak_group_upstream_artifact_ids=(
            None
            if job.cross_sample_peak_groups is None
            else tuple(
                upstream.evidence_artifact_id
                for upstream in job.upstream_artifacts
            )
        ),
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


def test_alignment_accepted_cancellation_skips_runner_without_factory(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="cancelled")
    marker = OwnerPathRuntimeMarker(
        tmp_path / "alignment-cancelled.runtime.json",
        "alignment-runtime-artifacts:cancelled",
    )
    runner_calls: list[str] = []
    job = _NoRawAlignmentJob(
        attempt,
        "sample:a",
        "process:sample:a",
        b"must-not-run",
    )

    def worker(observed_job: _NoRawAlignmentJob) -> AlignmentRuntimeProcessResult:
        runner_calls.append(observed_job.work_unit_key)
        return _run_no_raw_alignment_job(observed_job)

    result = execute_alignment_process_runtime(
        start_runtime_attempt(
            attempt=attempt,
            work_unit_keys=("sample:a",),
            owner_completeness_attestation="alignment-runtime-test:v1",
            cancellation_request=RuntimeCancellationRequest(attempt=attempt),
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
    assert result.finalization_record.terminal_outcome is (
        AttemptTerminalOutcome.CANCELLED
    )
    assert result.successful_observation_batches == ()
    assert result.successful_cross_sample_peak_groups == ()
    assert tuple(settlement.outcome for settlement in result.runtime.settlements) == (
        WorkUnitTerminalOutcome.CANCELLED,
    )
    resource = result.coordinator_record.state.resources[0]
    assert resource.open_outcome is RuntimeResourceOpenOutcome.NOT_OPENED
    assert resource.cleanup_outcome is RuntimeResourceCleanupOutcome.SUCCEEDED
    assert recovered.finalization_acknowledged is True
    assert recovered.terminal_outcome is AttemptTerminalOutcome.CANCELLED
    assert recovered.coordinator_record_bytes is not None
    recovered_payload = json.loads(recovered.coordinator_record_bytes)
    recovered_settlement = recovered_payload["work_settlements"][0]
    assert recovered_settlement["outcome"] == "cancelled"
    assert recovered_settlement["owner_result_artifact_id"] is None
    assert recovered_settlement["failure_detail"]["category"] == "cancellation"
    assert recovered_settlement["failure_detail"]["stage"] == (
        "runtime-owner-pre-dispatch"
    )


def test_alignment_scientific_carriers_roundtrip_in_manifest_order(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="carrier")
    groups_a = (_group("group:a", processing_request_id=attempt.processing_request_id),)
    groups_b = (_group("group:b", processing_request_id=attempt.processing_request_id),)
    upstream_a = mint_alignment_process_result_artifact(
        processing_attempt=attempt,
        work_unit_key="upstream:a",
        owner_result_payload=b"upstream-a",
    )
    upstream_b = mint_alignment_process_result_artifact(
        processing_attempt=attempt,
        work_unit_key="upstream:b",
        owner_result_payload=b"upstream-b",
    )
    jobs = (
        _NoRawAlignmentJob(
            attempt,
            "sample:b",
            "process:sample:b",
            canonical_alignment_peak_group_result_bytes(groups_b),
            cross_sample_peak_groups=groups_b,
            upstream_artifacts=(upstream_b,),
        ),
        _NoRawAlignmentJob(
            attempt,
            "sample:a",
            "process:sample:a",
            canonical_alignment_peak_group_result_bytes(groups_a),
            cross_sample_peak_groups=groups_a,
            upstream_artifacts=(upstream_a,),
        ),
    )

    result = execute_alignment_process_runtime(
        _runtime(attempt, ("sample:a", "sample:b")),
        jobs,
        worker=_run_no_raw_alignment_job,
        error_factory=_alignment_job_error,
        max_workers=2,
        resource_reservations=_reservations(attempt),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "carrier.runtime.json",
            "alignment-runtime-artifacts:carrier",
        ),
    )

    assert result.successful_cross_sample_peak_groups == (
        ("sample:a", groups_a),
        ("sample:b", groups_b),
    )


def test_alignment_scientific_carrier_rejects_payload_mismatch(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="mismatch")
    artifact = mint_alignment_process_result_artifact(
        processing_attempt=attempt,
        work_unit_key="sample:a",
        owner_result_payload=canonical_alignment_peak_group_result_bytes(
            (
                _group(
                    "group:artifact",
                    processing_request_id=attempt.processing_request_id,
                ),
            )
        ),
    )

    with pytest.raises(AlignmentRuntimeAdapterError, match="does not bind"):
        AlignmentRuntimeProcessResult(
            settlement=mint_work_unit_settlement(
                attempt=attempt,
                work_unit_key="sample:a",
                outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                owner_result_ref=artifact,
            ),
            resource_key="process:sample:a",
            open_outcome=RuntimeResourceOpenOutcome.OPENED,
            cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
            runtime_artifacts=(artifact,),
            cross_sample_peak_groups=(
                _group(
                    "group:carrier",
                    processing_request_id=attempt.processing_request_id,
                ),
            ),
            cross_sample_peak_group_upstream_artifact_ids=(),
        )

    matching_artifact = mint_alignment_process_result_artifact(
        processing_attempt=attempt,
        work_unit_key="sample:a",
        owner_result_payload=canonical_alignment_peak_group_result_bytes(
            (
                _group(
                    "group:carrier",
                    processing_request_id=attempt.processing_request_id,
                ),
            )
        ),
    )
    with pytest.raises(AlignmentRuntimeAdapterError, match="caller-provided"):
        AlignmentRuntimeProcessResult(
            settlement=mint_work_unit_settlement(
                attempt=attempt,
                work_unit_key="sample:a",
                outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                owner_result_ref=matching_artifact,
            ),
            resource_key="process:sample:a",
            open_outcome=RuntimeResourceOpenOutcome.OPENED,
            cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
            runtime_artifacts=(matching_artifact,),
            cross_sample_peak_groups=(
                _group(
                    "group:carrier",
                    processing_request_id=attempt.processing_request_id,
                ),
            ),
        )


@pytest.mark.parametrize(
    "outcome",
    [WorkUnitTerminalOutcome.FAILED, WorkUnitTerminalOutcome.CANCELLED],
)
def test_alignment_unsuccessful_work_cannot_carry_scientific_result(
    tmp_path: Path,
    outcome: WorkUnitTerminalOutcome,
) -> None:
    attempt = _attempt(tmp_path, stem=f"unsuccessful-{outcome.value}")

    with pytest.raises(AlignmentRuntimeAdapterError, match="only successful"):
        AlignmentRuntimeProcessResult(
            settlement=mint_work_unit_settlement(
                attempt=attempt,
                work_unit_key="sample:a",
                outcome=outcome,
                failure_detail=WorkUnitFailureDetail(
                    category=(
                        WorkUnitFailureCategory.CANCELLATION
                        if outcome is WorkUnitTerminalOutcome.CANCELLED
                        else WorkUnitFailureCategory.EXECUTION_ERROR
                    ),
                    stage="alignment-process-worker",
                    source_reference="no-raw:sample:a",
                    message="worker did not succeed",
                ),
            ),
            resource_key="process:sample:a",
            open_outcome=RuntimeResourceOpenOutcome.NOT_OPENED,
            cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
            cross_sample_peak_groups=(
                _group(
                    "group:forbidden",
                    processing_request_id=attempt.processing_request_id,
                ),
            ),
            cross_sample_peak_group_upstream_artifact_ids=(),
        )


def test_alignment_conflicting_replay_does_not_expose_scientific_carrier(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="conflict")
    first = (
        _group(
            "group:first",
            processing_request_id=attempt.processing_request_id,
        ),
    )
    conflicting = (
        _group(
            "group:conflicting",
            processing_request_id=attempt.processing_request_id,
        ),
    )
    jobs = (
        _NoRawAlignmentJob(
            attempt,
            "sample:a",
            "process:sample:a",
            canonical_alignment_peak_group_result_bytes(first),
            cross_sample_peak_groups=first,
        ),
        _NoRawAlignmentJob(
            attempt,
            "sample:a",
            "process:sample:a",
            canonical_alignment_peak_group_result_bytes(conflicting),
            cross_sample_peak_groups=conflicting,
        ),
    )

    result = execute_alignment_process_runtime(
        _runtime(attempt, ("sample:a",)),
        jobs,
        worker=_run_no_raw_alignment_job,
        error_factory=_alignment_job_error,
        max_workers=1,
        resource_reservations=(_reservations(attempt)[0],),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "conflict.runtime.json",
            "alignment-runtime-artifacts:conflict",
        ),
    )

    assert result.runtime.integrity_failure is not None
    assert result.successful_cross_sample_peak_groups == ()


def test_alignment_scientific_serializer_rejects_display_only_group() -> None:
    display_only = CrossSamplePeakGroupHypothesis(
        group_hypothesis_id="FAM000001",
        public_family_id="FAM000001",
        owner_ids=("owner:display",),
        event_ids=("event:display",),
        event_member_count=1,
    )

    with pytest.raises(AlignmentRuntimeAdapterError, match="canonical identity"):
        canonical_alignment_peak_group_result_bytes((display_only,))


def test_alignment_scientific_serializer_omits_private_targeted_istd_anchor(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.drift_evidence import (
        build_targeted_istd_identity_anchor,
    )
    from xic_extractor.configuration.models import Target
    from xic_extractor.configuration.target_identity import mint_target_list_snapshot

    attempt = _attempt(tmp_path, stem="targeted-istd")
    target = Target(
        label="ISTD",
        mz=500.0,
        rt_min=8.0,
        rt_max=9.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
    )
    anchor = build_targeted_istd_identity_anchor(
        mint_target_list_snapshot((target,), identity_namespace="runtime-istd"),
        observed_labels=(target.label,),
    )

    unanchored = _group(
        "group:targeted-istd",
        processing_request_id=attempt.processing_request_id,
    )
    payload = canonical_alignment_peak_group_result_bytes(
        (
            replace(
                unanchored,
                targeted_istd_anchor=anchor,
            ),
        )
    )
    rendered = json.loads(payload)
    unanchored_rendered = json.loads(
        canonical_alignment_peak_group_result_bytes((unanchored,))
    )

    assert "targeted_istd_anchor" not in rendered["groups"][0]
    assert "_mint_token" not in payload.decode("utf-8")
    assert rendered == unanchored_rendered


def _group(
    group_id: str,
    *,
    processing_request_id: str,
) -> CrossSamplePeakGroupHypothesis:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleGroupMemberSubject,
        mint_cross_sample_group_identity,
    )
    from xic_extractor.processing_identity import (
        canonical_identity_basis_bytes,
        typed_identity_digest,
    )

    subject_id = f"xic.peak-observation-content.v1:{group_id}"
    member_basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject_v1",
            "processing_request_id": processing_request_id,
            "canonical_sample_identity_id": f"xic.sample.v1:{group_id}",
            "subject": {"kind": "physical_observation", "id": subject_id},
        }
    )
    member = CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest(
            "xic.group-member-subject.v1:",
            member_basis,
        ),
        processing_request_id=processing_request_id,
        canonical_sample_identity_id=f"xic.sample.v1:{group_id}",
        subject_kind="physical_observation",
        subject_id=subject_id,
        physical_observation_ids=(subject_id,),
        basis_bytes=member_basis,
    )
    identity = mint_cross_sample_group_identity(
        processing_request_id=processing_request_id,
        member_subjects=(member,),
    )
    return CrossSamplePeakGroupHypothesis(
        group_hypothesis_id=identity.group_hypothesis_id,
        public_family_id=group_id,
        owner_ids=(f"owner:{group_id}",),
        event_ids=(f"event:{group_id}",),
        event_member_count=1,
        processing_request_id=identity.processing_request_id,
        member_subjects=identity.member_subjects,
        identity_basis_bytes=identity.basis_bytes,
        disposition=identity.disposition,
    )


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
