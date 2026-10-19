from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.runtime_observability_fixtures import (
    build_runtime_observability_ledger,
    record_runtime_resource_cleanup,
    record_runtime_resource_open,
    register_runtime_artifact,
    reserve_runtime_resource,
    settle_runtime_resource,
    settle_work_unit,
)
from tests.runtime_telemetry_fixtures import (
    mint_test_process_result_ref as mint_targeted_process_result_ref,
)
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.execution._finalization import (
    RuntimeArtifactDisposition,
    RuntimeFinalizationContractError,
    RuntimeLifecycleEventKind,
    RuntimeLifecycleFailureKind,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    SupervisorFinalizationEventKind,
    begin_attempt_finalization,
    canonical_coordinator_terminal_record_bytes,
    mint_supervisor_finalization,
    propose_attempt_terminal,
)
from xic_extractor.execution._lifecycle import (
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
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    ProcessingAttemptIdentity,
    build_acquisition_source_identity,
    build_processing_request_identity,
    issue_processing_attempt,
)


def test_success_closes_resource_artifact_and_atomic_acknowledgement(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime, artifact = _successful_runtime(attempt)
    state = begin_attempt_finalization(runtime)
    state = reserve_runtime_resource(state, _reservation(attempt))
    state = register_runtime_artifact(
        state,
        work_unit_key="raw:a",
        artifact=artifact,
    )
    state = settle_runtime_resource(
        state,
        resource_key="session:a",
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )

    coordinator = propose_attempt_terminal(state)
    submitted = mint_supervisor_finalization(
        coordinator,
        observability_ledger=_ledger(attempt, coordinator),
    )
    accepted = submitted

    assert coordinator.proposed_outcome is AttemptTerminalOutcome.SUCCEEDED
    assert coordinator.state.artifacts[0].proposed_disposition is (
        RuntimeArtifactDisposition.OFFERED_TO_OWNER
    )
    assert accepted is submitted
    assert accepted.artifact_dispositions == (
        (artifact.evidence_artifact_id, RuntimeArtifactDisposition.OFFERED_TO_OWNER),
    )
    assert [event.kind for event in accepted.events] == [
        SupervisorFinalizationEventKind.TERMINAL_ACKNOWLEDGED,
        SupervisorFinalizationEventKind.ARTIFACT_OFFERED_TO_OWNER,
        SupervisorFinalizationEventKind.TERMINAL_OUTCOME,
    ]
    assert tuple(event.sequence for event in accepted.events) == tuple(
        range(
            coordinator.state.events[-1].sequence + 1,
            coordinator.state.events[-1].sequence + 4,
        )
    )
    assert canonical_coordinator_terminal_record_bytes(coordinator) == (
        canonical_coordinator_terminal_record_bytes(coordinator)
    )


def test_failed_cleanup_terminalizes_and_quarantines_whole_attempt(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime, artifact = _successful_runtime(attempt)
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        _reservation(attempt),
    )
    state = register_runtime_artifact(
        state,
        work_unit_key="raw:a",
        artifact=artifact,
    )
    state = settle_runtime_resource(
        state,
        resource_key="session:a",
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.FAILED,
    )

    coordinator = propose_attempt_terminal(state)
    finalization = mint_supervisor_finalization(
        coordinator,
        observability_ledger=_ledger(attempt, coordinator),
    )

    assert coordinator.proposed_outcome is AttemptTerminalOutcome.FAILED
    assert finalization.artifact_dispositions == (
        (
            artifact.evidence_artifact_id,
            RuntimeArtifactDisposition.QUARANTINED_DIAGNOSTIC,
        ),
    )
    assert finalization.events[-1].terminal_outcome is AttemptTerminalOutcome.FAILED


@pytest.mark.parametrize(
    "open_outcome",
    (
        RuntimeResourceOpenOutcome.OPEN_FAILED,
        RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS,
    ),
)
def test_failed_or_unknown_resource_open_prevents_successful_terminalization(
    tmp_path: Path,
    open_outcome: RuntimeResourceOpenOutcome,
) -> None:
    attempt = _attempt(tmp_path)
    runtime, artifact = _successful_runtime(attempt)
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        _reservation(attempt),
    )
    state = register_runtime_artifact(
        state,
        work_unit_key="raw:a",
        artifact=artifact,
    )
    state = settle_runtime_resource(
        state,
        resource_key="session:a",
        open_outcome=open_outcome,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )

    coordinator = propose_attempt_terminal(state)

    assert coordinator.proposed_outcome is AttemptTerminalOutcome.FAILED
    assert coordinator.state.artifacts[0].proposed_disposition is (
        RuntimeArtifactDisposition.QUARANTINED_DIAGNOSTIC
    )


def test_not_opened_resource_without_accepted_cancellation_fails_attempt(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime, artifact = _successful_runtime(attempt)
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        _reservation(attempt),
    )
    state = register_runtime_artifact(
        state,
        work_unit_key="raw:a",
        artifact=artifact,
    )
    state = settle_runtime_resource(
        state,
        resource_key="session:a",
        open_outcome=RuntimeResourceOpenOutcome.NOT_OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )

    coordinator = propose_attempt_terminal(state)

    assert coordinator.proposed_outcome is AttemptTerminalOutcome.FAILED
    assert coordinator.state.artifacts[0].proposed_disposition is (
        RuntimeArtifactDisposition.QUARANTINED_DIAGNOSTIC
    )


def test_condition_not_activated_keeps_success_without_opening_resource(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime, artifact = _successful_runtime(attempt)
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        _reservation(
            attempt,
            owner_conditional=True,
        ),
    )
    state = register_runtime_artifact(
        state,
        work_unit_key="raw:a",
        artifact=artifact,
    )
    state = settle_runtime_resource(
        state,
        resource_key="session:a",
        open_outcome=RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )

    coordinator = propose_attempt_terminal(state)
    payload = json.loads(canonical_coordinator_terminal_record_bytes(coordinator))

    assert coordinator.proposed_outcome is AttemptTerminalOutcome.SUCCEEDED
    assert coordinator.state.artifacts[0].proposed_disposition is (
        RuntimeArtifactDisposition.OFFERED_TO_OWNER
    )
    assert payload["schema_version"] == "execution_runtime_coordinator_terminal"
    assert payload["resources"][0]["owner_conditional"] is True
    assert payload["resources"][0]["open_outcome"] == "condition_not_activated"


def test_required_resource_cannot_claim_condition_not_activated(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime, artifact = _successful_runtime(attempt)
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        _reservation(attempt),
    )
    state = register_runtime_artifact(
        state,
        work_unit_key="raw:a",
        artifact=artifact,
    )
    state = settle_runtime_resource(
        state,
        resource_key="session:a",
        open_outcome=RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )

    coordinator = propose_attempt_terminal(state)

    assert coordinator.proposed_outcome is AttemptTerminalOutcome.FAILED
    assert len(coordinator.state.failures) == 1
    failure = coordinator.state.failures[0]
    assert failure.kind is RuntimeLifecycleFailureKind.CONFLICTING_RESOURCE_REPORT
    assert failure.subject_key == "session:a"
    assert failure.message == (
        "required resource cannot close as condition_not_activated"
    )
    assert coordinator.state.artifacts[0].proposed_disposition is (
        RuntimeArtifactDisposition.QUARANTINED_DIAGNOSTIC
    )


def test_cancelled_attempt_quarantines_artifacts_without_becoming_failed(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime = _runtime(attempt)
    cancelled_history = append_attempt_lifecycle_event(
        runtime.history,
        AttemptLifecycleEventKind.CANCELLATION_ACCEPTED,
    )
    runtime = replace(runtime, history=cancelled_history)
    runtime = settle_work_unit(
        runtime,
        work_unit_key="raw:a",
        outcome=WorkUnitTerminalOutcome.CANCELLED,
        failure_detail=_failure_detail(
            "raw:a",
            category=WorkUnitFailureCategory.CANCELLATION,
            message="owner accepted cancellation",
        ),
    )
    artifact = mint_targeted_process_result_ref(
        processing_attempt=attempt,
        work_unit_key="raw:a",
        owner_result_payload=b"partial-before-cancel",
    ).artifact
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        _reservation(attempt),
    )
    state = register_runtime_artifact(
        state,
        work_unit_key="raw:a",
        artifact=artifact,
    )
    state = settle_runtime_resource(
        state,
        resource_key="session:a",
        open_outcome=RuntimeResourceOpenOutcome.NOT_OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )

    coordinator = propose_attempt_terminal(state)

    assert coordinator.proposed_outcome is AttemptTerminalOutcome.CANCELLED
    assert coordinator.state.artifacts[0].proposed_disposition is (
        RuntimeArtifactDisposition.QUARANTINED_DIAGNOSTIC
    )


def test_worker_loss_preserves_open_fact_and_closes_cleanup_unknown(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime = settle_work_unit(
        _runtime(attempt),
        work_unit_key="raw:a",
        outcome=WorkUnitTerminalOutcome.FAILED,
        failure_detail=_failure_detail("raw:a", message="worker lost"),
    )
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        _reservation(attempt),
    )
    state = record_runtime_resource_open(
        state,
        resource_key="session:a",
        outcome=RuntimeResourceOpenOutcome.OPENED,
    )

    lost = record_runtime_resource_cleanup(
        state, resource_key="session:a",
        outcome=RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS,
    )
    replay = record_runtime_resource_cleanup(
        lost, resource_key="session:a",
        outcome=RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS,
    )
    coordinator = propose_attempt_terminal(lost)

    assert replay is lost
    assert lost.resources[0].open_outcome is RuntimeResourceOpenOutcome.OPENED
    assert lost.resources[0].cleanup_outcome is (
        RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS
    )
    assert coordinator.proposed_outcome is AttemptTerminalOutcome.FAILED
    assert [event.kind for event in lost.events][-1] is (
        RuntimeLifecycleEventKind.RESOURCE_CLEANUP_UNKNOWN_AFTER_WORKER_LOSS
    )


def test_late_resource_report_is_idempotent_or_fails_closed(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    runtime = settle_work_unit(
        _runtime(attempt),
        work_unit_key="raw:a",
        outcome=WorkUnitTerminalOutcome.FAILED,
        failure_detail=_failure_detail("raw:a", message="late resource report"),
    )
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        _reservation(attempt),
    )
    state = settle_runtime_resource(
        state,
        resource_key="session:a",
        open_outcome=RuntimeResourceOpenOutcome.OPEN_FAILED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )

    identical = settle_runtime_resource(
        state,
        resource_key="session:a",
        open_outcome=RuntimeResourceOpenOutcome.OPEN_FAILED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )
    conflict = record_runtime_resource_cleanup(
        state,
        resource_key="session:a",
        outcome=RuntimeResourceCleanupOutcome.FAILED,
    )

    assert identical is state
    assert conflict.resources == state.resources
    assert conflict.failures[-1].kind is (
        RuntimeLifecycleFailureKind.CONFLICTING_RESOURCE_REPORT
    )
    assert propose_attempt_terminal(conflict).proposed_outcome is (
        AttemptTerminalOutcome.FAILED
    )


def test_terminal_and_acknowledgement_fail_closed_on_missing_or_conflict(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime, artifact = _successful_runtime(attempt)
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        _reservation(attempt),
    )

    with pytest.raises(RuntimeFinalizationContractError, match="resource cleanup"):
        propose_attempt_terminal(state)

    state = settle_runtime_resource(
        state,
        resource_key="session:a",
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )
    with pytest.raises(RuntimeFinalizationContractError, match="must be registered"):
        propose_attempt_terminal(state)

    state = register_runtime_artifact(
        state,
        work_unit_key="raw:a",
        artifact=artifact,
    )
    coordinator = propose_attempt_terminal(state)
    with pytest.raises(RuntimeFinalizationContractError, match="cannot change"):
        register_runtime_artifact(
            coordinator.state,
            work_unit_key="raw:a",
            artifact=artifact,
        )


def test_coordinator_receipt_binds_settlement_integrity_failure(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime, accepted_artifact = _successful_runtime(attempt)
    failed_submission = settle_work_unit(
        runtime,
        work_unit_key="raw:a",
        outcome=WorkUnitTerminalOutcome.FAILED,
        failure_detail=_failure_detail("raw:a", message="conflicting settlement"),
    )
    alternate_artifact = mint_targeted_process_result_ref(
        processing_attempt=attempt,
        work_unit_key="raw:a",
        owner_result_payload=b"alternate-owner-result",
    ).artifact
    alternate_submission = settle_work_unit(
        runtime,
        work_unit_key="raw:a",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=alternate_artifact,
    )

    def close(conflicted: AttemptRuntime):
        state = register_runtime_artifact(
            begin_attempt_finalization(conflicted),
            work_unit_key="raw:a",
            artifact=accepted_artifact,
        )
        return propose_attempt_terminal(state)

    failed_coordinator = close(failed_submission)
    alternate_coordinator = close(alternate_submission)
    failed_bytes = canonical_coordinator_terminal_record_bytes(failed_coordinator)
    alternate_bytes = canonical_coordinator_terminal_record_bytes(alternate_coordinator)
    failure_payload = json.loads(failed_bytes)["settlement_integrity_failure"]

    assert failed_bytes != alternate_bytes
    assert failure_payload == {
        "accepted_settlement": {
            "failure_detail": None,
            "outcome": "succeeded",
            "owner_result_artifact_id": accepted_artifact.evidence_artifact_id,
            "work_unit_key": "raw:a",
        },
        "kind": "conflicting_settlement",
        "submitted_settlement": {
            "failure_detail": {
                "category": "execution_error",
                "message": "conflicting settlement",
                "source_reference": "raw:a",
                "stage": "runtime-finalization-test",
            },
            "outcome": "failed",
            "owner_result_artifact_id": None,
            "work_unit_key": "raw:a",
        },
        "work_unit_key": "raw:a",
    }
    assert json.loads(failed_bytes)["schema_version"] == (
        "execution_runtime_coordinator_terminal"
    )
    assert (
        mint_supervisor_finalization(
            failed_coordinator,
            observability_ledger=_ledger(attempt, failed_coordinator),
        ).coordinator_record_sha256
        != mint_supervisor_finalization(
            alternate_coordinator,
            observability_ledger=_ledger(attempt, alternate_coordinator),
        ).coordinator_record_sha256
    )


def test_supervisor_receipt_binds_validated_runtime_observability(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime, artifact = _successful_runtime(attempt)
    state = register_runtime_artifact(
        begin_attempt_finalization(runtime),
        work_unit_key="raw:a",
        artifact=artifact,
    )
    coordinator = propose_attempt_terminal(state)
    first_ledger = _ledger(
        attempt,
        coordinator,
        logical_xic_request_count=1,
    )
    second_ledger = _ledger(
        attempt,
        coordinator,
        logical_xic_request_count=2,
    )

    first = mint_supervisor_finalization(
        coordinator,
        observability_ledger=first_ledger,
    )
    second = mint_supervisor_finalization(
        coordinator,
        observability_ledger=second_ledger,
    )

    assert first.observability_ledger == first_ledger
    assert second.observability_ledger == second_ledger
    assert first != second


def test_supervisor_rejects_observability_that_conflicts_with_finalized_facts(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime, artifact = _successful_runtime(attempt)
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        _reservation(attempt),
    )
    state = register_runtime_artifact(
        state,
        work_unit_key="raw:a",
        artifact=artifact,
    )
    state = settle_runtime_resource(
        state,
        resource_key="session:a",
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )
    coordinator = propose_attempt_terminal(state)

    with pytest.raises(RuntimeFinalizationContractError, match="observability ledger"):
        mint_supervisor_finalization(
            coordinator,
            observability_ledger=_ledger(attempt),
        )


def test_supervisor_rejects_raw_open_count_that_conflicts_with_resources(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime, artifact = _successful_runtime(attempt)
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        _reservation(attempt),
    )
    state = settle_runtime_resource(
        state,
        resource_key="session:a",
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )
    state = register_runtime_artifact(
        state,
        work_unit_key="raw:a",
        artifact=artifact,
    )
    coordinator = propose_attempt_terminal(state)
    inconsistent = _ledger(
        attempt,
        coordinator,
        raw_open_count=0,
    )

    with pytest.raises(RuntimeFinalizationContractError, match="raw_open_count"):
        mint_supervisor_finalization(
            coordinator,
            observability_ledger=inconsistent,
        )


def test_process_worker_open_does_not_increment_raw_open_count(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime, artifact = _successful_runtime(attempt)
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        replace(
            _reservation(attempt),
            kind=RuntimeResourceKind.PROCESS_WORKER,
            resource_key="process:a",
        ),
    )
    state = settle_runtime_resource(
        state,
        resource_key="process:a",
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )
    state = register_runtime_artifact(
        state,
        work_unit_key="raw:a",
        artifact=artifact,
    )
    coordinator = propose_attempt_terminal(state)
    ledger = _ledger(attempt, coordinator)

    mint_supervisor_finalization(
        coordinator,
        observability_ledger=ledger,
    )

    assert (
        next(
            metric.value for metric in ledger.metrics if metric.name == "raw_open_count"
        )
        == 0
    )


def test_undeclared_artifact_is_rejected_by_registration(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime, accepted_artifact = _successful_runtime(attempt)
    rogue_artifact = mint_targeted_process_result_ref(
        processing_attempt=attempt,
        work_unit_key="raw:rogue",
        owner_result_payload=b"rogue-owner-result",
    ).artifact
    state = register_runtime_artifact(
        begin_attempt_finalization(runtime),
        work_unit_key="raw:a",
        artifact=accepted_artifact,
    )

    with pytest.raises(RuntimeFinalizationContractError, match="declared manifest"):
        register_runtime_artifact(
            state,
            work_unit_key="raw:rogue",
            artifact=rogue_artifact,
        )
    coordinator = propose_attempt_terminal(state)

    assert tuple(item.work_unit_key for item in state.artifacts) == ("raw:a",)
    assert coordinator.proposed_outcome is AttemptTerminalOutcome.SUCCEEDED
    assert coordinator.state.artifacts[0].proposed_disposition is (
        RuntimeArtifactDisposition.OFFERED_TO_OWNER
    )


def test_successful_settlement_requires_artifact_registered_for_same_work_unit(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    artifact = mint_targeted_process_result_ref(
        processing_attempt=attempt,
        work_unit_key="raw:a",
        owner_result_payload=b"shared-owner-result",
    ).artifact
    runtime = settle_work_unit(
        _runtime(attempt, work_unit_keys=("raw:a", "raw:b")),
        work_unit_key="raw:a",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=artifact,
    )
    runtime = settle_work_unit(
        runtime,
        work_unit_key="raw:b",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=artifact,
    )
    state = register_runtime_artifact(
        begin_attempt_finalization(runtime),
        work_unit_key="raw:a",
        artifact=artifact,
    )

    with pytest.raises(RuntimeFinalizationContractError, match="same work unit"):
        propose_attempt_terminal(state)


def test_runtime_finalization_modules_stay_inside_mechanical_boundary() -> None:
    expected = {
        "xic_extractor/execution/_finalization.py": (
            "xic_extractor.evidence_artifacts",
            "xic_extractor.execution._lifecycle",
            "xic_extractor.execution._observability",
            "xic_extractor.processing_identity",
            "xic_extractor.validation_primitives",
        ),
    }
    for filename, expected_imports in expected.items():
        path = Path(filename)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=filename)
        imports = tuple(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("xic_extractor")
        )
        assert imports == expected_imports


def _successful_runtime(
    attempt: ProcessingAttemptIdentity,
) -> tuple[AttemptRuntime, EvidenceArtifactRef]:
    reference = mint_targeted_process_result_ref(
        processing_attempt=attempt,
        work_unit_key="raw:a",
        owner_result_payload=b"owner-result",
    )
    runtime = settle_work_unit(
        _runtime(attempt),
        work_unit_key="raw:a",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=reference.artifact,
    )
    return runtime, reference.artifact


def _runtime(
    attempt: ProcessingAttemptIdentity,
    *,
    work_unit_keys: tuple[str, ...] = ("raw:a",),
) -> AttemptRuntime:
    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    return start_attempt_runtime(
        preflight_required_work_manifest(
            queued,
            attempt=attempt,
            work_unit_keys=work_unit_keys,
            owner_completeness_attestation="runtime-finalization-test",
        )
    )


def _reservation(
    attempt: ProcessingAttemptIdentity,
    *,
    owner_conditional: bool = False,
) -> RuntimeResourceReservation:
    return RuntimeResourceReservation(
        attempt=attempt,
        kind=RuntimeResourceKind.ACQUISITION_SESSION,
        resource_key="session:a",
        worker_key="worker:1",
        source_reference="source:a",
        compatibility_token="targeted:test",
        owner_conditional=owner_conditional,
    )


def _failure_detail(
    source_reference: str,
    *,
    category: WorkUnitFailureCategory = WorkUnitFailureCategory.EXECUTION_ERROR,
    message: str,
) -> WorkUnitFailureDetail:
    return WorkUnitFailureDetail(
        category=category,
        stage="runtime-finalization-test",
        source_reference=source_reference,
        message=message,
    )


def _ledger(
    attempt: ProcessingAttemptIdentity,
    coordinator=None,
    *,
    raw_open_count: int | None = None,
    logical_xic_request_count: int = 0,
):
    resource_lifecycles = ()
    artifact_dispositions = ()
    if coordinator is not None:
        resource_lifecycles = tuple(
            (
                record.reservation.resource_key,
                record.reservation.kind,
                f"resource_{record.open_outcome.value}",
                f"resource_cleanup_{record.cleanup_outcome.value}",
            )
            for record in coordinator.state.resources
        )
        artifact_dispositions = tuple(
            (
                record.artifact.evidence_artifact_id,
                f"artifact_{record.proposed_disposition.value}",
            )
            for record in coordinator.state.artifacts
        )
    return build_runtime_observability_ledger(
        attempt,
        work_unit_keys=("raw:a",),
        worker_keys=("worker:1",),
        raw_open_count=raw_open_count,
        logical_xic_request_count=logical_xic_request_count,
        resource_lifecycles=resource_lifecycles,
        artifact_dispositions=artifact_dispositions,
    )


def _attempt(tmp_path: Path) -> ProcessingAttemptIdentity:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"synthetic-no-raw-content")
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
                adapter_input_key="sample.raw",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="runtime_finalization_test",
        processing_algorithm_version="runtime_finalization_test",
        processing_contract_version="processing_identity",
        scientific_inputs={"method": "cid_nl", "neutral_loss_da": 116.0},
    )
    return issue_processing_attempt(request)
