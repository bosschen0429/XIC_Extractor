"""Mechanical resource, artifact, and terminal-finalization reducer.

This focused companion remains part of the single execution_runtime owner.
It contains no storage, acquisition, scientific, delivery, or writer policy.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.execution_runtime import (
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitSettlement,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization_models import (
    AttemptFinalizationState,
    CoordinatorTerminalRecord,
    RuntimeArtifactDisposition,
    RuntimeArtifactRecord,
    RuntimeFinalizationContractError,
    RuntimeLifecycleEvent,
    RuntimeLifecycleEventKind,
    RuntimeLifecycleFailure,
    RuntimeLifecycleFailureKind,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
    RuntimeResourceRecord,
    RuntimeResourceReservation,
    SupervisorFinalizationEvent,
    SupervisorFinalizationEventKind,
    SupervisorFinalizationRecord,
    _coordinator_event_digest,
)
from xic_extractor.execution_runtime_observability import RuntimeObservabilityLedger


def begin_attempt_finalization(runtime: AttemptRuntime) -> AttemptFinalizationState:
    """Start the focused lifecycle reducer from one existing work runtime."""

    return AttemptFinalizationState(runtime=runtime)


def reserve_runtime_resource(
    state: AttemptFinalizationState,
    reservation: RuntimeResourceReservation,
) -> AttemptFinalizationState:
    """Record a reservation before the adapter allocates or dispatches it."""

    _require_mutable_state(state)
    if not isinstance(reservation, RuntimeResourceReservation):
        raise RuntimeFinalizationContractError(
            "reservation must be a RuntimeResourceReservation"
        )
    if reservation.attempt != state.runtime.manifest.attempt:
        raise RuntimeFinalizationContractError(
            "resource reservation must bind the runtime attempt"
        )
    existing = _resource(state, reservation.resource_key)
    if existing is not None:
        if existing.reservation == reservation:
            return state
        return _record_failure(
            state,
            RuntimeLifecycleFailureKind.CONFLICTING_RESOURCE_RESERVATION,
            reservation.resource_key,
            "resource key was reserved with different owner content",
        )
    return replace(
        state,
        resources=(*state.resources, RuntimeResourceRecord(reservation)),
        events=(
            *state.events,
            _event(
                state,
                RuntimeLifecycleEventKind.RESOURCE_RESERVED,
                reservation.resource_key,
            ),
        ),
    )


def settle_runtime_resource(
    state: AttemptFinalizationState,
    *,
    resource_key: str,
    open_outcome: RuntimeResourceOpenOutcome,
    cleanup_outcome: RuntimeResourceCleanupOutcome,
) -> AttemptFinalizationState:
    """Accept one complete resource report; identical replay is idempotent."""

    opened = record_runtime_resource_open(
        state,
        resource_key=resource_key,
        outcome=open_outcome,
    )
    return record_runtime_resource_cleanup(
        opened,
        resource_key=resource_key,
        outcome=cleanup_outcome,
    )


def record_runtime_resource_open(
    state: AttemptFinalizationState,
    *,
    resource_key: str,
    outcome: RuntimeResourceOpenOutcome,
) -> AttemptFinalizationState:
    """Accept the first open outcome; identical replay is idempotent."""

    _require_mutable_state(state)
    key = _required_text(resource_key, field="resource_key")
    if not isinstance(outcome, RuntimeResourceOpenOutcome):
        raise RuntimeFinalizationContractError(
            "outcome must be a RuntimeResourceOpenOutcome"
        )
    existing = _resource(state, key)
    if existing is None:
        return _record_failure(
            state,
            RuntimeLifecycleFailureKind.UNREGISTERED_RESOURCE,
            key,
            "resource open report arrived without a reservation",
        )
    if existing.open_outcome is not None:
        if existing.open_outcome is outcome:
            return state
        return _record_failure(
            state,
            RuntimeLifecycleFailureKind.CONFLICTING_RESOURCE_REPORT,
            key,
            "late open report conflicts with the accepted resource record",
        )
    updated = replace(existing, open_outcome=outcome)
    return replace(
        state,
        resources=tuple(
            updated if item is existing else item for item in state.resources
        ),
        events=(*state.events, _event(state, _open_event_kind(outcome), key)),
    )


def record_runtime_resource_cleanup(
    state: AttemptFinalizationState,
    *,
    resource_key: str,
    outcome: RuntimeResourceCleanupOutcome,
) -> AttemptFinalizationState:
    """Accept the first terminal cleanup; identical replay is idempotent."""

    _require_mutable_state(state)
    key = _required_text(resource_key, field="resource_key")
    if not isinstance(outcome, RuntimeResourceCleanupOutcome):
        raise RuntimeFinalizationContractError(
            "outcome must be a RuntimeResourceCleanupOutcome"
        )
    existing = _resource(state, key)
    if existing is None:
        return _record_failure(
            state,
            RuntimeLifecycleFailureKind.UNREGISTERED_RESOURCE,
            key,
            "resource cleanup report arrived without a reservation",
        )
    if existing.open_outcome is None:
        raise RuntimeFinalizationContractError(
            "resource cleanup requires an accepted open outcome"
        )
    if existing.cleanup_outcome is not None:
        if existing.cleanup_outcome is outcome:
            return state
        return _record_failure(
            state,
            RuntimeLifecycleFailureKind.CONFLICTING_RESOURCE_REPORT,
            key,
            "late cleanup report conflicts with the terminal resource record",
        )
    updated = replace(existing, cleanup_outcome=outcome)
    return replace(
        state,
        resources=tuple(
            updated if item is existing else item for item in state.resources
        ),
        events=(*state.events, _event(state, _cleanup_event_kind(outcome), key)),
    )


def mark_worker_lost(
    state: AttemptFinalizationState,
    *,
    worker_key: str,
) -> AttemptFinalizationState:
    """Close every non-terminal resource for one lost worker conservatively."""

    _require_mutable_state(state)
    key = _required_text(worker_key, field="worker_key")
    current = state
    for record in state.resources:
        if record.reservation.worker_key == key and record.cleanup_outcome is None:
            open_outcome = record.open_outcome or (
                RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS
            )
            current = record_runtime_resource_open(
                current,
                resource_key=record.reservation.resource_key,
                outcome=open_outcome,
            )
            current = record_runtime_resource_cleanup(
                current,
                resource_key=record.reservation.resource_key,
                outcome=RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS,
            )
    return current


def register_runtime_artifact(
    state: AttemptFinalizationState,
    *,
    work_unit_key: str,
    artifact: EvidenceArtifactRef,
) -> AttemptFinalizationState:
    """Register a current-attempt artifact while it is runtime_temporary."""

    _require_mutable_state(state)
    record = RuntimeArtifactRecord(work_unit_key=work_unit_key, artifact=artifact)
    if (
        artifact.producer_processing_request_id
        != state.runtime.manifest.attempt.processing_request_id
        or artifact.producer_processing_attempt_id
        != state.runtime.manifest.attempt.processing_attempt_id
    ):
        raise RuntimeFinalizationContractError(
            "runtime artifact must bind the runtime request and attempt"
        )
    if record.work_unit_key not in state.runtime.manifest.work_unit_keys:
        raise RuntimeFinalizationContractError(
            "runtime artifact must bind a declared manifest work unit"
        )
    existing = next(
        (
            item
            for item in state.artifacts
            if item.artifact.evidence_artifact_id == artifact.evidence_artifact_id
        ),
        None,
    )
    if existing is not None:
        if existing == record:
            return state
        return _record_failure(
            state,
            RuntimeLifecycleFailureKind.CONFLICTING_ARTIFACT_REGISTRATION,
            artifact.evidence_artifact_id,
            "artifact was registered against a different work unit",
        )
    return replace(
        state,
        artifacts=(*state.artifacts, record),
        events=(
            *state.events,
            _event(
                state,
                RuntimeLifecycleEventKind.ARTIFACT_REGISTERED,
                artifact.evidence_artifact_id,
            ),
        ),
    )


def propose_attempt_terminal(
    state: AttemptFinalizationState,
) -> CoordinatorTerminalRecord:
    """Freeze a terminal proposal only after all mechanical inputs close."""

    _require_mutable_state(state)
    if not state.runtime.all_manifest_work_settled:
        raise RuntimeFinalizationContractError(
            "terminal proposal requires every manifest work unit to settle"
        )
    incomplete_resources = tuple(
        record.reservation.resource_key
        for record in state.resources
        if record.cleanup_outcome is None
    )
    if incomplete_resources:
        raise RuntimeFinalizationContractError(
            f"terminal proposal requires resource cleanup: {incomplete_resources!r}"
        )
    registered_artifacts = {
        (record.work_unit_key, record.artifact.evidence_artifact_id)
        for record in state.artifacts
    }
    missing_artifacts = tuple(
        (settlement.work_unit_key, settlement.owner_result_ref.evidence_artifact_id)
        for settlement in state.runtime.settlements
        if settlement.outcome is WorkUnitTerminalOutcome.SUCCEEDED
        and settlement.owner_result_ref is not None
        and (
            settlement.work_unit_key,
            settlement.owner_result_ref.evidence_artifact_id,
        )
        not in registered_artifacts
    )
    if missing_artifacts:
        raise RuntimeFinalizationContractError(
            "successful result artifacts must be registered to the same work unit: "
            f"{missing_artifacts!r}"
        )
    outcome = _proposed_outcome(state)
    disposition = (
        RuntimeArtifactDisposition.OFFERED_TO_OWNER
        if outcome is AttemptTerminalOutcome.SUCCEEDED
        else RuntimeArtifactDisposition.QUARANTINED_DIAGNOSTIC
    )
    artifacts = tuple(
        replace(record, proposed_disposition=disposition) for record in state.artifacts
    )
    proposed = replace(
        state,
        artifacts=artifacts,
        events=(
            *state.events,
            _event(
                state,
                RuntimeLifecycleEventKind.TERMINAL_PROPOSAL,
                proposed_outcome=outcome,
            ),
        ),
    )
    return CoordinatorTerminalRecord(
        state=proposed,
        proposed_outcome=outcome,
        coordinator_event_digest=_coordinator_event_digest(proposed),
    )


def mint_supervisor_finalization(
    coordinator: CoordinatorTerminalRecord,
    *,
    observability_ledger: RuntimeObservabilityLedger,
) -> SupervisorFinalizationRecord:
    """Mint the exact atomic acknowledgement batch for a coordinator record."""

    if not isinstance(coordinator, CoordinatorTerminalRecord):
        raise RuntimeFinalizationContractError(
            "coordinator must be a CoordinatorTerminalRecord"
        )
    if not isinstance(observability_ledger, RuntimeObservabilityLedger):
        raise RuntimeFinalizationContractError(
            "observability_ledger must be a RuntimeObservabilityLedger"
        )
    attempt = coordinator.state.runtime.manifest.attempt
    if observability_ledger.attempt != attempt:
        raise RuntimeFinalizationContractError(
            "observability ledger must bind the coordinator attempt"
        )
    if (
        observability_ledger.work_unit_keys
        != coordinator.state.runtime.manifest.work_unit_keys
    ):
        raise RuntimeFinalizationContractError(
            "observability ledger must bind the closed manifest work units"
        )
    start = coordinator.state.events[-1].sequence + 1
    dispositions = tuple(
        (record.artifact.evidence_artifact_id, record.proposed_disposition)
        for record in coordinator.state.artifacts
    )
    events: list[SupervisorFinalizationEvent] = [
        SupervisorFinalizationEvent(
            sequence=start,
            kind=SupervisorFinalizationEventKind.TERMINAL_ACKNOWLEDGED,
        )
    ]
    for artifact_id, disposition in dispositions:
        events.append(
            SupervisorFinalizationEvent(
                sequence=start + len(events),
                kind=(
                    SupervisorFinalizationEventKind.ARTIFACT_OFFERED_TO_OWNER
                    if disposition is RuntimeArtifactDisposition.OFFERED_TO_OWNER
                    else SupervisorFinalizationEventKind.ARTIFACT_QUARANTINED_DIAGNOSTIC
                ),
                artifact_id=artifact_id,
            )
        )
    events.append(
        SupervisorFinalizationEvent(
            sequence=start + len(events),
            kind=SupervisorFinalizationEventKind.TERMINAL_OUTCOME,
            terminal_outcome=coordinator.proposed_outcome,
        )
    )
    return SupervisorFinalizationRecord(
        attempt=attempt,
        coordinator_record_sha256=hashlib.sha256(
            canonical_coordinator_terminal_record_bytes(coordinator)
        ).hexdigest(),
        terminal_outcome=coordinator.proposed_outcome,
        artifact_dispositions=dispositions,
        events=tuple(events),
        observability_ledger=observability_ledger,
    )


def accept_supervisor_finalization(
    coordinator: CoordinatorTerminalRecord,
    submitted: SupervisorFinalizationRecord,
    *,
    observability_ledger: RuntimeObservabilityLedger,
    existing: SupervisorFinalizationRecord | None = None,
) -> SupervisorFinalizationRecord:
    """Accept canonical acknowledgement once; identical replay is idempotent."""

    if not isinstance(submitted, SupervisorFinalizationRecord):
        raise RuntimeFinalizationContractError(
            "supervisor finalization conflicts with the coordinator proposal"
        )
    expected = mint_supervisor_finalization(
        coordinator,
        observability_ledger=observability_ledger,
    )
    if submitted != expected:
        raise RuntimeFinalizationContractError(
            "supervisor finalization conflicts with the coordinator proposal"
        )
    if existing is None:
        return submitted
    if not isinstance(existing, SupervisorFinalizationRecord) or existing != submitted:
        raise RuntimeFinalizationContractError(
            "conflicting finalization already exists for this attempt"
        )
    return existing


def canonical_coordinator_terminal_record_bytes(
    coordinator: CoordinatorTerminalRecord,
) -> bytes:
    """Return deterministic recovery-record bytes without product payloads."""

    state = coordinator.state
    integrity_failure = state.runtime.integrity_failure
    payload = {
        "schema_version": "execution_runtime_coordinator_terminal_v2",
        "processing_request_id": state.runtime.manifest.attempt.processing_request_id,
        "processing_attempt_id": state.runtime.manifest.attempt.processing_attempt_id,
        "manifest_work_unit_keys": state.runtime.manifest.work_unit_keys,
        "work_settlements": [
            _work_unit_settlement_payload(item) for item in state.runtime.settlements
        ],
        "settlement_integrity_failure": (
            None
            if integrity_failure is None
            else {
                "work_unit_key": integrity_failure.work_unit_key,
                "kind": integrity_failure.kind.value,
                "submitted_settlement": _work_unit_settlement_payload(
                    integrity_failure.submitted_settlement
                ),
                "accepted_settlement": (
                    None
                    if integrity_failure.accepted_settlement is None
                    else _work_unit_settlement_payload(
                        integrity_failure.accepted_settlement
                    )
                ),
            }
        ),
        "resources": [
            {
                "resource_key": item.reservation.resource_key,
                "worker_key": item.reservation.worker_key,
                "source_reference": item.reservation.source_reference,
                "compatibility_token": item.reservation.compatibility_token,
                "open_outcome": item.open_outcome.value if item.open_outcome else None,
                "cleanup_outcome": (
                    item.cleanup_outcome.value if item.cleanup_outcome else None
                ),
            }
            for item in state.resources
        ],
        "artifacts": [
            {
                "work_unit_key": item.work_unit_key,
                "evidence_artifact_id": item.artifact.evidence_artifact_id,
                "proposed_disposition": item.proposed_disposition.value,
            }
            for item in state.artifacts
        ],
        "failures": [
            {
                "kind": item.kind.value,
                "subject_key": item.subject_key,
                "message": item.message,
            }
            for item in state.failures
        ],
        "proposed_outcome": coordinator.proposed_outcome.value,
        "coordinator_event_digest": coordinator.coordinator_event_digest,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _work_unit_settlement_payload(
    settlement: WorkUnitSettlement,
) -> dict[str, object]:
    failure_detail = settlement.failure_detail
    return {
        "failure_detail": (
            None
            if failure_detail is None
            else {
                "category": failure_detail.category.value,
                "message": failure_detail.message,
                "source_reference": failure_detail.source_reference,
                "stage": failure_detail.stage,
            }
        ),
        "work_unit_key": settlement.work_unit_key,
        "outcome": settlement.outcome.value,
        "owner_result_artifact_id": (
            None
            if settlement.owner_result_ref is None
            else settlement.owner_result_ref.evidence_artifact_id
        ),
    }


def _proposed_outcome(state: AttemptFinalizationState) -> AttemptTerminalOutcome:
    resource_open_failed = any(
        record.open_outcome
        in {
            RuntimeResourceOpenOutcome.OPEN_FAILED,
            RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS,
        }
        for record in state.resources
    )
    cleanup_failed = any(
        record.cleanup_outcome
        in {
            RuntimeResourceCleanupOutcome.FAILED,
            RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS,
        }
        for record in state.resources
    )
    if (
        state.runtime.must_fail
        or state.failures
        or resource_open_failed
        or cleanup_failed
    ):
        return AttemptTerminalOutcome.FAILED
    if state.runtime.history.cancellation_accepted:
        return AttemptTerminalOutcome.CANCELLED
    return AttemptTerminalOutcome.SUCCEEDED


def _resource(
    state: AttemptFinalizationState,
    resource_key: str,
) -> RuntimeResourceRecord | None:
    return next(
        (
            item
            for item in state.resources
            if item.reservation.resource_key == resource_key
        ),
        None,
    )


def _record_failure(
    state: AttemptFinalizationState,
    kind: RuntimeLifecycleFailureKind,
    subject_key: str,
    message: str,
) -> AttemptFinalizationState:
    failure = RuntimeLifecycleFailure(kind, subject_key, message)
    if failure in state.failures:
        return state
    return replace(state, failures=(*state.failures, failure))


def _event(
    state: AttemptFinalizationState,
    kind: RuntimeLifecycleEventKind,
    subject_key: str | None = None,
    *,
    proposed_outcome: AttemptTerminalOutcome | None = None,
    offset: int = 0,
) -> RuntimeLifecycleEvent:
    return RuntimeLifecycleEvent(
        sequence=len(state.runtime.history.events) + len(state.events) + 1 + offset,
        kind=kind,
        subject_key=subject_key,
        proposed_outcome=proposed_outcome,
    )


def _open_event_kind(
    outcome: RuntimeResourceOpenOutcome,
) -> RuntimeLifecycleEventKind:
    return {
        RuntimeResourceOpenOutcome.OPENED: RuntimeLifecycleEventKind.RESOURCE_OPENED,
        RuntimeResourceOpenOutcome.OPEN_FAILED: (
            RuntimeLifecycleEventKind.RESOURCE_OPEN_FAILED
        ),
        RuntimeResourceOpenOutcome.NOT_OPENED: (
            RuntimeLifecycleEventKind.RESOURCE_NOT_OPENED
        ),
        RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS: (
            RuntimeLifecycleEventKind.RESOURCE_OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS
        ),
    }[outcome]


def _cleanup_event_kind(
    outcome: RuntimeResourceCleanupOutcome,
) -> RuntimeLifecycleEventKind:
    return {
        RuntimeResourceCleanupOutcome.SUCCEEDED: (
            RuntimeLifecycleEventKind.RESOURCE_CLEANUP_SUCCEEDED
        ),
        RuntimeResourceCleanupOutcome.FAILED: (
            RuntimeLifecycleEventKind.RESOURCE_CLEANUP_FAILED
        ),
        RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS: (
            RuntimeLifecycleEventKind.RESOURCE_CLEANUP_UNKNOWN_AFTER_WORKER_LOSS
        ),
    }[outcome]


def _require_mutable_state(state: AttemptFinalizationState) -> None:
    if not isinstance(state, AttemptFinalizationState):
        raise RuntimeFinalizationContractError(
            "state must be an AttemptFinalizationState"
        )
    if state.terminal_proposed:
        raise RuntimeFinalizationContractError(
            "coordinator state cannot change after terminal proposal"
        )


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RuntimeFinalizationContractError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


__all__ = [
    "AttemptFinalizationState",
    "CoordinatorTerminalRecord",
    "RuntimeArtifactDisposition",
    "RuntimeArtifactRecord",
    "RuntimeFinalizationContractError",
    "RuntimeLifecycleEvent",
    "RuntimeLifecycleEventKind",
    "RuntimeLifecycleFailure",
    "RuntimeLifecycleFailureKind",
    "RuntimeResourceCleanupOutcome",
    "RuntimeResourceOpenOutcome",
    "RuntimeResourceRecord",
    "RuntimeResourceReservation",
    "SupervisorFinalizationEvent",
    "SupervisorFinalizationEventKind",
    "SupervisorFinalizationRecord",
    "accept_supervisor_finalization",
    "begin_attempt_finalization",
    "canonical_coordinator_terminal_record_bytes",
    "mark_worker_lost",
    "mint_supervisor_finalization",
    "propose_attempt_terminal",
    "record_runtime_resource_cleanup",
    "record_runtime_resource_open",
    "register_runtime_artifact",
    "reserve_runtime_resource",
    "settle_runtime_resource",
]
