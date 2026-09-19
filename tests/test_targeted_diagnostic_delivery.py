from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_diagnostic_method import _method_request
from tests.test_targeted_method_runtime import (
    _alignment_job,
    _completion_spec,
    _marker,
    _reservations,
    _runtime,
)
from xic_extractor.alignment.runtime_adapter import (
    AlignmentRuntimeApplicationResult,
    mint_alignment_process_result_artifact,
)
from xic_extractor.evidence_artifacts import verify_evidence_artifact_payload
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
)
from xic_extractor.execution_runtime_owner import (
    RuntimeArtifactRegistrationReceipt,
    RuntimeCancellationRequest,
    RuntimeWorkerBatchReceipt,
    execute_runtime_attempt,
    mint_work_unit_settlement,
    start_runtime_attempt,
)
from xic_extractor.extraction.targeted_diagnostic_delivery import (
    TargetedDiagnosticDeliveryError,
    rollback_targeted_diagnostic_delivery_candidate,
    settle_targeted_diagnostic_delivery_candidate,
)
from xic_extractor.extraction.targeted_diagnostic_method import (
    TargetedDiagnosticMethodResult,
    run_targeted_diagnostic_method,
    targeted_attempt_namespace_component,
)
from xic_extractor.extraction.targeted_method_runtime import (
    alignment_upstream_artifacts_for_request,
    build_targeted_method_completion_jobs,
    execute_targeted_method_alignment_runtime,
    execute_targeted_method_completion_runtime,
)
from xic_extractor.extraction.targeted_runtime_adapter import (
    TargetedProcessResourceReport,
    TargetedProcessWorkerResultEnvelope,
    TargetedRuntimeLifecycleApplicationResult,
    TargetedRuntimeWorkerBatch,
    execute_targeted_runtime_lifecycle,
    mint_targeted_process_result_ref,
)
from xic_extractor.output.csv_writers import write_wide_csv
from xic_extractor.processing_identity import ProcessingAttemptIdentity


def test_true_spawned_runtime_writes_and_stages_exact_candidate(
    tmp_path: Path,
) -> None:
    request, fixture = _method_request(tmp_path)
    alignment, targeted, result = _true_runtime_path(
        tmp_path,
        request,
        fixture.attempt,
    )
    legacy_bytes = _legacy_csv_bytes(tmp_path, request)

    delivery = settle_targeted_diagnostic_delivery_candidate(
        result,
        request=request,
        alignment_runtime=alignment,
        targeted_runtime=targeted,
        prior_current_delivery_id="legacy-seal:prior-current",
    )

    candidate_path = (
        request.peak_config.output_csv.parent
        / "successor_attempts"
        / targeted_attempt_namespace_component(result.processing_attempt_id)
        / "xic_results.csv"
    )
    assert delivery.status == "staged_candidate"
    assert candidate_path.read_bytes() == legacy_bytes
    assert delivery.diagnostic_csv_artifact is not None
    verify_evidence_artifact_payload(delivery.diagnostic_csv_artifact, legacy_bytes)
    assert delivery.stage_candidate is not None
    assert delivery.seal_candidate is not None
    assert delivery.seal_candidate.stage == delivery.stage_candidate
    assert len(delivery.revisions) == len(request.alignment_inputs)
    rolled_back = rollback_targeted_diagnostic_delivery_candidate(delivery)
    assert rolled_back.status == "rolled_back"
    assert rolled_back.active_output == request.authoritative_legacy_output
    assert rolled_back.prior_current_delivery_id == "legacy-seal:prior-current"
    assert rolled_back.lineage_artifacts == delivery.lineage_artifacts
    assert not tuple(tmp_path.rglob("*.xlsx"))
    assert not tuple(tmp_path.rglob("*matrix*"))


def test_arbitrary_targeted_owner_payload_cannot_stage(tmp_path: Path) -> None:
    request, fixture = _method_request(tmp_path)
    alignment, _targeted, result = _true_runtime_path(
        tmp_path,
        request,
        fixture.attempt,
    )
    alignment_artifact = alignment.runtime.settlements[0].owner_result_ref
    assert alignment_artifact is not None
    arbitrary = _arbitrary_targeted_runtime(
        fixture.attempt,
        alignment_artifact=alignment_artifact,
    )

    with pytest.raises(
        TargetedDiagnosticDeliveryError,
        match="exactly one matching method result",
    ):
        settle_targeted_diagnostic_delivery_candidate(
            result,
            request=request,
            alignment_runtime=alignment,
            targeted_runtime=arbitrary,
            prior_current_delivery_id="legacy-seal:prior-current",
        )


def test_arbitrary_alignment_owner_payload_cannot_stage(tmp_path: Path) -> None:
    request, fixture = _method_request(tmp_path)
    alignment, _targeted, result = _true_runtime_path(
        tmp_path,
        request,
        fixture.attempt,
    )
    arbitrary = _arbitrary_alignment_runtime(
        fixture.attempt,
        upstream_artifacts=alignment_upstream_artifacts_for_request(request),
    )
    arbitrary_artifact = arbitrary.runtime.settlements[0].owner_result_ref
    assert arbitrary_artifact is not None
    targeted = _arbitrary_targeted_runtime(
        fixture.attempt,
        alignment_artifact=arbitrary_artifact,
    )

    with pytest.raises(
        TargetedDiagnosticDeliveryError,
        match="exactly one matching group result",
    ):
        settle_targeted_diagnostic_delivery_candidate(
            result,
            request=request,
            alignment_runtime=arbitrary,
            targeted_runtime=targeted,
            prior_current_delivery_id="legacy-seal:prior-current",
        )
    assert alignment.finalization_record.terminal_outcome is (
        AttemptTerminalOutcome.SUCCEEDED
    )


def test_mismatched_alignment_groups_cannot_stage(tmp_path: Path) -> None:
    request, fixture = _method_request(tmp_path)
    alignment, targeted, result = _true_runtime_path(
        tmp_path,
        request,
        fixture.attempt,
    )
    mismatched = replace(result, groups=())

    with pytest.raises(
        TargetedDiagnosticDeliveryError,
        match="exactly one matching group result",
    ):
        settle_targeted_diagnostic_delivery_candidate(
            mismatched,
            request=request,
            alignment_runtime=alignment,
            targeted_runtime=targeted,
            prior_current_delivery_id="legacy-seal:prior-current",
        )


@pytest.mark.parametrize(
    "scenario",
    ["failed", "cancelled", "worker_loss", "partial", "conflict"],
)
def test_unsuccessful_runtime_evidence_is_quarantined_without_writer(
    tmp_path: Path,
    scenario: str,
) -> None:
    request, fixture = _method_request(tmp_path)
    result = run_targeted_diagnostic_method(request)
    alignment_job = _alignment_job(request, fixture.attempt, "alignment:method")
    alignment = execute_targeted_method_alignment_runtime(
        _runtime(fixture.attempt, ("alignment:method",)),
        (alignment_job,),
        max_workers=1,
        resource_reservations=_reservations((alignment_job,)),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path, f"delivery-alignment-{scenario}"),
    )
    alignment_artifact = alignment.runtime.settlements[0].owner_result_ref
    assert alignment_artifact is not None
    targeted = _unsuccessful_targeted_runtime(
        fixture.attempt,
        alignment_artifact=alignment_artifact,
        scenario=scenario,
    )

    delivery = settle_targeted_diagnostic_delivery_candidate(
        result,
        request=request,
        alignment_runtime=alignment,
        targeted_runtime=targeted,
        prior_current_delivery_id="legacy-seal:prior-current",
    )

    assert delivery.status == "quarantined"
    assert delivery.stage_candidate is None
    assert delivery.seal_candidate is None
    assert delivery.diagnostic_csv_artifact is None
    assert delivery.active_output is request.authoritative_legacy_output
    assert not (request.peak_config.output_csv.parent / "successor_attempts").exists()
    if scenario == "partial":
        assert any(
            artifact.semantic_contract.owner_namespace
            == "xic_extractor.extraction.targeted_runtime_adapter"
            for artifact in delivery.lineage_artifacts
        )


def test_internal_writer_conflict_quarantines_and_preserves_prior_current(
    tmp_path: Path,
) -> None:
    request, fixture = _method_request(tmp_path)
    alignment, targeted, result = _true_runtime_path(
        tmp_path,
        request,
        fixture.attempt,
    )
    expected_successor_bytes = _legacy_csv_bytes(tmp_path, request)
    for output in (
        request.authoritative_legacy_output,
        result.authoritative_legacy_output,
    ):
        legacy_observed = next(
            item for item in output.file_results if item.sample_name == "observed-a"
        )
        legacy_observed.results["Analyte"] = replace(
            legacy_observed.results["Analyte"],
            reported_rt=999.0,
            nl_token="tampered-legacy-token",
        )
    delivery = settle_targeted_diagnostic_delivery_candidate(
        result,
        request=request,
        alignment_runtime=alignment,
        targeted_runtime=targeted,
        prior_current_delivery_id="legacy-seal:prior-current",
    )

    candidate_path = (
        request.peak_config.output_csv.parent
        / "successor_attempts"
        / targeted_attempt_namespace_component(result.processing_attempt_id)
        / "xic_results.csv"
    )
    assert delivery.status == "quarantined"
    assert delivery.method_result_artifact is not None
    assert delivery.diagnostic_csv_artifact is None
    assert delivery.prior_current_delivery_id == "legacy-seal:prior-current"
    assert candidate_path.read_bytes() == expected_successor_bytes


def _true_runtime_path(
    tmp_path: Path,
    request,
    attempt: ProcessingAttemptIdentity,
) -> tuple[
    AlignmentRuntimeApplicationResult,
    TargetedRuntimeLifecycleApplicationResult,
    TargetedDiagnosticMethodResult,
]:
    alignment_job = _alignment_job(request, attempt, "alignment:method")
    alignment = execute_targeted_method_alignment_runtime(
        _runtime(attempt, ("alignment:method",)),
        (alignment_job,),
        max_workers=1,
        resource_reservations=_reservations((alignment_job,)),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path, "delivery-alignment"),
    )
    completion_jobs = build_targeted_method_completion_jobs(
        alignment,
        (_completion_spec(request),),
    )
    targeted = execute_targeted_method_completion_runtime(
        _runtime(attempt, ("targeted:method",)),
        completion_jobs,
        max_workers=1,
        resource_reservations=_reservations(completion_jobs),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path, "delivery-targeted"),
    )
    result = targeted.settlement.successful_targeted_method_results[0][1]
    return alignment, targeted, result


def _legacy_csv_bytes(tmp_path: Path, request) -> bytes:
    output_config = replace(
        request.peak_config,
        output_csv=tmp_path / "legacy" / "xic_results.csv",
    )
    write_wide_csv(
        output_config,
        request.targets,
        request.authoritative_legacy_output.file_results,
    )
    return output_config.output_csv.read_bytes()


def _arbitrary_alignment_runtime(
    attempt: ProcessingAttemptIdentity,
    *,
    upstream_artifacts,
) -> AlignmentRuntimeApplicationResult:
    work_unit_key = "alignment:method"
    artifact = mint_alignment_process_result_artifact(
        processing_attempt=attempt,
        work_unit_key=work_unit_key,
        owner_result_payload=b"runtime-owner-result",
        upstream_artifacts=upstream_artifacts,
    )
    receipt = execute_runtime_attempt(
        _base_runtime(attempt, work_unit_key),
        (),
        worker_batch_provider=lambda _aggregate: RuntimeWorkerBatchReceipt(
            settlements=(
                mint_work_unit_settlement(
                    attempt=attempt,
                    work_unit_key=work_unit_key,
                    outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                    owner_result_ref=artifact,
                ),
            ),
            artifact_registrations=(
                RuntimeArtifactRegistrationReceipt(
                    work_unit_key=work_unit_key,
                    artifact=artifact,
                ),
            ),
        ),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=lambda proposed: proposed,
    )
    return AlignmentRuntimeApplicationResult(
        runtime=receipt.coordinator.state.runtime,
        finalization=receipt,
    )


def _arbitrary_targeted_runtime(
    attempt: ProcessingAttemptIdentity,
    *,
    alignment_artifact,
) -> TargetedRuntimeLifecycleApplicationResult:
    work_unit_key = "targeted:method"
    reference = mint_targeted_process_result_ref(
        processing_attempt=attempt,
        work_unit_key=work_unit_key,
        owner_result_payload=b"runtime-owner-result",
        upstream_artifacts=(alignment_artifact,),
    )
    batch = TargetedRuntimeWorkerBatch(
        result_envelopes=(
            TargetedProcessWorkerResultEnvelope(
                attempt=attempt,
                work_unit_key=work_unit_key,
                outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                owner_result_ref=reference,
            ),
        ),
        resource_reports=(),
        runtime_artifact_refs=(reference,),
    )
    return execute_targeted_runtime_lifecycle(
        _base_runtime(attempt, work_unit_key),
        (),
        worker_batch_provider=lambda _aggregate: batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        finalization_persister=lambda proposed: proposed,
    )


def _unsuccessful_targeted_runtime(
    attempt: ProcessingAttemptIdentity,
    *,
    alignment_artifact,
    scenario: str,
) -> TargetedRuntimeLifecycleApplicationResult:
    work_unit_key = "targeted:method"
    runtime = _base_runtime(
        attempt,
        work_unit_key,
        cancelled=scenario == "cancelled",
    )
    envelopes: list[TargetedProcessWorkerResultEnvelope] = []
    registered = []
    reservations: tuple[RuntimeResourceReservation, ...] = ()
    resource_reports: tuple[TargetedProcessResourceReport, ...] = ()
    if scenario == "cancelled":
        envelopes.append(
            TargetedProcessWorkerResultEnvelope(
                attempt=attempt,
                work_unit_key=work_unit_key,
                outcome=WorkUnitTerminalOutcome.CANCELLED,
                failure_detail=WorkUnitFailureDetail(
                    category=WorkUnitFailureCategory.CANCELLATION,
                    stage="targeted-process-worker",
                    source_reference="no-raw:targeted:method",
                    message="cancellation accepted before dispatch",
                ),
            )
        )
    elif scenario == "conflict":
        first = mint_targeted_process_result_ref(
            processing_attempt=attempt,
            work_unit_key=work_unit_key,
            owner_result_payload=b"runtime-owner-result:first",
            upstream_artifacts=(alignment_artifact,),
        )
        second = mint_targeted_process_result_ref(
            processing_attempt=attempt,
            work_unit_key=work_unit_key,
            owner_result_payload=b"runtime-owner-result:conflict",
            upstream_artifacts=(alignment_artifact,),
        )
        envelopes.append(
            TargetedProcessWorkerResultEnvelope(
                attempt=attempt,
                work_unit_key=work_unit_key,
                outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                owner_result_ref=first,
            )
        )
        envelopes.append(
            TargetedProcessWorkerResultEnvelope(
                attempt=attempt,
                work_unit_key=work_unit_key,
                outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                owner_result_ref=second,
            )
        )
        registered.extend((first, second))
    else:
        category = (
            WorkUnitFailureCategory.WORKER_LOSS
            if scenario in {"worker_loss", "partial"}
            else WorkUnitFailureCategory.EXECUTION_ERROR
        )
        envelopes.append(
            TargetedProcessWorkerResultEnvelope(
                attempt=attempt,
                work_unit_key=work_unit_key,
                outcome=WorkUnitTerminalOutcome.FAILED,
                failure_detail=WorkUnitFailureDetail(
                    category=category,
                    stage="targeted-process-worker",
                    source_reference="no-raw:targeted:method",
                    message=f"simulated {scenario}",
                ),
            )
        )
        if scenario == "partial":
            partial = mint_targeted_process_result_ref(
                processing_attempt=attempt,
                work_unit_key=work_unit_key,
                owner_result_payload=b"partial-before-worker-loss",
                upstream_artifacts=(alignment_artifact,),
            )
            registered.append(partial)
    if scenario == "worker_loss":
        reservation = RuntimeResourceReservation(
            attempt=attempt,
            kind=RuntimeResourceKind.PROCESS_WORKER,
            resource_key="process:targeted:method",
            worker_key="worker:targeted:method",
            source_reference="no-raw:targeted:method",
            compatibility_token="targeted-delivery-test",
        )
        reservations = (reservation,)
        resource_reports = (
            TargetedProcessResourceReport(
                attempt=attempt,
                resource_key=reservation.resource_key,
                open_outcome=(
                    RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS
                ),
                cleanup_outcome=(
                    RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS
                ),
            ),
        )
    batch = TargetedRuntimeWorkerBatch(
        result_envelopes=tuple(envelopes),
        resource_reports=resource_reports,
        runtime_artifact_refs=tuple(registered),
    )
    lifecycle = execute_targeted_runtime_lifecycle(
        runtime,
        reservations,
        worker_batch_provider=lambda _aggregate: batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        finalization_persister=lambda proposed: proposed,
    )
    if scenario == "partial":
        assert tuple(
            disposition
            for _artifact_id, disposition in (
                lifecycle.finalization_record.artifact_dispositions
            )
        ) == (RuntimeArtifactDisposition.QUARANTINED_DIAGNOSTIC,)
    return lifecycle


def _base_runtime(
    attempt: ProcessingAttemptIdentity,
    work_unit_key: str,
    *,
    cancelled: bool = False,
) -> AttemptRuntime:
    return start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=(work_unit_key,),
        owner_completeness_attestation="targeted-delivery-test",
        cancellation_request=(
            RuntimeCancellationRequest(attempt=attempt) if cancelled else None
        ),
    )
