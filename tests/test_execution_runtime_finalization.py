from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.execution_runtime import (
    AttemptLifecycleEventKind,
    AttemptLifecycleHistory,
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitTerminalOutcome,
    append_attempt_lifecycle_event,
    preflight_required_work_manifest,
    settle_work_unit,
    start_attempt_runtime,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeArtifactDisposition,
    RuntimeFinalizationContractError,
    RuntimeLifecycleEventKind,
    RuntimeLifecycleFailureKind,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    SupervisorFinalizationEventKind,
    accept_supervisor_finalization,
    begin_attempt_finalization,
    canonical_coordinator_terminal_record_bytes,
    mark_worker_lost,
    mint_supervisor_finalization,
    propose_attempt_terminal,
    record_runtime_resource_cleanup,
    record_runtime_resource_open,
    register_runtime_artifact,
    reserve_runtime_resource,
    settle_runtime_resource,
)
from xic_extractor.extraction.targeted_runtime_adapter import (
    mint_targeted_process_result_ref,
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
    submitted = mint_supervisor_finalization(coordinator)
    accepted = accept_supervisor_finalization(coordinator, submitted)
    replay = accept_supervisor_finalization(
        coordinator,
        submitted,
        existing=accepted,
    )

    assert coordinator.proposed_outcome is AttemptTerminalOutcome.SUCCEEDED
    assert coordinator.state.artifacts[0].proposed_disposition is (
        RuntimeArtifactDisposition.OFFERED_TO_OWNER
    )
    assert accepted is submitted
    assert replay is accepted
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
    finalization = mint_supervisor_finalization(coordinator)

    assert coordinator.proposed_outcome is AttemptTerminalOutcome.FAILED
    assert finalization.artifact_dispositions == (
        (
            artifact.evidence_artifact_id,
            RuntimeArtifactDisposition.QUARANTINED_DIAGNOSTIC,
        ),
    )
    assert finalization.events[-1].terminal_outcome is AttemptTerminalOutcome.FAILED


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

    lost = mark_worker_lost(state, worker_key="worker:1")
    replay = mark_worker_lost(lost, worker_key="worker:1")
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
    finalization = mint_supervisor_finalization(coordinator)
    conflicting = replace(
        finalization,
        coordinator_record_sha256="0" * 64,
    )

    with pytest.raises(RuntimeFinalizationContractError, match="conflicts"):
        accept_supervisor_finalization(coordinator, conflicting)
    with pytest.raises(RuntimeFinalizationContractError, match="cannot change"):
        register_runtime_artifact(
            coordinator.state,
            work_unit_key="raw:a",
            artifact=artifact,
        )


def test_runtime_finalization_modules_stay_inside_mechanical_boundary() -> None:
    expected = {
        "xic_extractor/execution_runtime_finalization.py": (
            "xic_extractor.evidence_artifacts",
            "xic_extractor.execution_runtime",
            "xic_extractor.execution_runtime_finalization_models",
        ),
        "xic_extractor/execution_runtime_finalization_models.py": (
            "xic_extractor.evidence_artifacts",
            "xic_extractor.execution_runtime",
            "xic_extractor.processing_identity",
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


def _runtime(attempt: ProcessingAttemptIdentity) -> AttemptRuntime:
    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    return start_attempt_runtime(
        preflight_required_work_manifest(
            queued,
            attempt=attempt,
            work_unit_keys=("raw:a",),
            owner_completeness_attestation="runtime-finalization-test:v1",
        )
    )


def _reservation(attempt: ProcessingAttemptIdentity) -> RuntimeResourceReservation:
    return RuntimeResourceReservation(
        attempt=attempt,
        resource_key="session:a",
        worker_key="worker:1",
        source_reference="source:a",
        compatibility_token="targeted:test:v1",
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
        scientific_input_schema_version="runtime_finalization_test_v1",
        processing_algorithm_version="runtime_finalization_test_v1",
        processing_contract_version="processing_identity_v1",
        scientific_inputs={"method": "cid_nl", "neutral_loss_da": 116.0},
    )
    return issue_processing_attempt(request)
