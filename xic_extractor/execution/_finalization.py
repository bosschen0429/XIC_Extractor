"""Mechanical terminal/resource models and their finalization reducer."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import (
    Collection,
    Iterable,
)
from dataclasses import (
    dataclass,
    replace,
)
from enum import Enum

from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.execution._lifecycle import (
    AttemptLifecycleEventKind,
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitSettlement,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution._observability import (
    RuntimeMetricAvailability,
    RuntimeObservabilityLedger,
    RuntimeObservationSubject,
)
from xic_extractor.processing_identity import ProcessingAttemptIdentity
from xic_extractor.validation_primitives import (
    canonical_json_bytes,
    require_lower_sha256,
    require_owner_normalized_text,
    require_tuple_of,
    sha256_hexdigest,
)


class RuntimeFinalizationContractError(ValueError):
    """Raised when one attempt's mechanical terminal state cannot close."""


class RuntimeResourceOpenOutcome(str, Enum):
    """Exactly one observable result after a resource reservation."""

    OPENED = "opened"
    OPEN_FAILED = "open_failed"
    NOT_OPENED = "not_opened"
    CONDITION_NOT_ACTIVATED = "condition_not_activated"
    OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS = "open_state_unknown_after_worker_loss"


class RuntimeResourceCleanupOutcome(str, Enum):
    """Exactly one logical terminal cleanup result for a reserved resource."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN_AFTER_WORKER_LOSS = "unknown_after_worker_loss"


class RuntimeResourceKind(str, Enum):
    """Mechanical resource role used by owner-backed runtime metrics."""

    ACQUISITION_SESSION = "acquisition_session"
    PROCESS_WORKER = "process_worker"


class RuntimeArtifactDisposition(str, Enum):
    """Runtime-only artifact states; none grants delivery or writer authority."""

    RUNTIME_TEMPORARY = "runtime_temporary"
    QUARANTINED_DIAGNOSTIC = "quarantined_diagnostic"
    OFFERED_TO_OWNER = "offered_to_owner"


class RuntimeLifecycleFailureKind(str, Enum):
    """Fail-closed integrity violations retained at the attempt boundary."""

    UNREGISTERED_RESOURCE = "unregistered_resource"
    CONFLICTING_RESOURCE_RESERVATION = "conflicting_resource_reservation"
    CONFLICTING_RESOURCE_REPORT = "conflicting_resource_report"
    CONFLICTING_ARTIFACT_REGISTRATION = "conflicting_artifact_registration"


class RuntimeLifecycleEventKind(str, Enum):
    """Coordinator facts added after the existing attempt/work prefix."""

    RESOURCE_RESERVED = "resource_reserved"
    RESOURCE_OPENED = "resource_opened"
    RESOURCE_OPEN_FAILED = "resource_open_failed"
    RESOURCE_NOT_OPENED = "resource_not_opened"
    RESOURCE_CONDITION_NOT_ACTIVATED = "resource_condition_not_activated"
    RESOURCE_OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS = (
        "resource_open_state_unknown_after_worker_loss"
    )
    RESOURCE_CLEANUP_SUCCEEDED = "resource_cleanup_succeeded"
    RESOURCE_CLEANUP_FAILED = "resource_cleanup_failed"
    RESOURCE_CLEANUP_UNKNOWN_AFTER_WORKER_LOSS = (
        "resource_cleanup_unknown_after_worker_loss"
    )
    ARTIFACT_REGISTERED = "artifact_registered"
    TERMINAL_PROPOSAL = "terminal_proposal"


class SupervisorFinalizationEventKind(str, Enum):
    """Atomic supervisor event batch following a coordinator proposal."""

    TERMINAL_ACKNOWLEDGED = "terminal_acknowledged"
    ARTIFACT_OFFERED_TO_OWNER = "artifact_offered_to_owner"
    ARTIFACT_QUARANTINED_DIAGNOSTIC = "artifact_quarantined_diagnostic"
    TERMINAL_OUTCOME = "terminal_outcome"


@dataclass(frozen=True, slots=True)
class RuntimeResourceReservation:
    """Owner-supplied opaque reservation recorded before allocation/dispatch."""

    attempt: ProcessingAttemptIdentity
    kind: RuntimeResourceKind
    resource_key: str
    worker_key: str
    source_reference: str
    compatibility_token: str
    owner_conditional: bool = False

    def __post_init__(self) -> None:
        _require_attempt(self.attempt)
        if not isinstance(self.kind, RuntimeResourceKind):
            raise RuntimeFinalizationContractError("kind must be a RuntimeResourceKind")
        _required_text(self.resource_key, field="resource_key")
        _required_text(self.worker_key, field="worker_key")
        _required_text(self.source_reference, field="source_reference")
        _required_text(self.compatibility_token, field="compatibility_token")
        if not isinstance(self.owner_conditional, bool):
            raise RuntimeFinalizationContractError("owner_conditional must be boolean")


@dataclass(frozen=True, slots=True)
class RuntimeResourceRecord:
    """One reservation and its at-most-once open/cleanup terminal report."""

    reservation: RuntimeResourceReservation
    open_outcome: RuntimeResourceOpenOutcome | None = None
    cleanup_outcome: RuntimeResourceCleanupOutcome | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reservation, RuntimeResourceReservation):
            raise RuntimeFinalizationContractError(
                "reservation must be a RuntimeResourceReservation"
            )
        if self.open_outcome is not None and not isinstance(
            self.open_outcome, RuntimeResourceOpenOutcome
        ):
            raise RuntimeFinalizationContractError(
                "open_outcome must be a RuntimeResourceOpenOutcome"
            )
        if self.cleanup_outcome is not None and not isinstance(
            self.cleanup_outcome, RuntimeResourceCleanupOutcome
        ):
            raise RuntimeFinalizationContractError(
                "cleanup_outcome must be a RuntimeResourceCleanupOutcome"
            )
        if self.cleanup_outcome is not None and self.open_outcome is None:
            raise RuntimeFinalizationContractError(
                "resource cleanup requires an open outcome"
            )


@dataclass(frozen=True, slots=True)
class RuntimeArtifactRecord:
    """One current-attempt artifact registered before terminal disposition."""

    work_unit_key: str
    artifact: EvidenceArtifactRef
    proposed_disposition: RuntimeArtifactDisposition = (
        RuntimeArtifactDisposition.RUNTIME_TEMPORARY
    )

    def __post_init__(self) -> None:
        _required_text(self.work_unit_key, field="work_unit_key")
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise RuntimeFinalizationContractError(
                "artifact must be an owner-minted EvidenceArtifactRef"
            )
        if not isinstance(self.proposed_disposition, RuntimeArtifactDisposition):
            raise RuntimeFinalizationContractError(
                "proposed_disposition must be a RuntimeArtifactDisposition"
            )


@dataclass(frozen=True, slots=True)
class RuntimeLifecycleFailure:
    """First-class mechanical failure; readable text is not its identity."""

    kind: RuntimeLifecycleFailureKind
    subject_key: str
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RuntimeLifecycleFailureKind):
            raise RuntimeFinalizationContractError(
                "kind must be a RuntimeLifecycleFailureKind"
            )
        _required_text(self.subject_key, field="subject_key")
        _required_text(self.message, field="message")


@dataclass(frozen=True, slots=True)
class RuntimeLifecycleEvent:
    """One ordered coordinator lifecycle fact."""

    sequence: int
    kind: RuntimeLifecycleEventKind
    subject_key: str | None = None
    proposed_outcome: AttemptTerminalOutcome | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence <= 0
        ):
            raise RuntimeFinalizationContractError(
                "event sequence must be a positive integer"
            )
        if not isinstance(self.kind, RuntimeLifecycleEventKind):
            raise RuntimeFinalizationContractError(
                "kind must be a RuntimeLifecycleEventKind"
            )
        if self.subject_key is not None:
            _required_text(self.subject_key, field="subject_key")
        if self.kind is RuntimeLifecycleEventKind.TERMINAL_PROPOSAL:
            if not isinstance(self.proposed_outcome, AttemptTerminalOutcome):
                raise RuntimeFinalizationContractError(
                    "terminal_proposal requires an AttemptTerminalOutcome"
                )
            if self.subject_key is not None:
                raise RuntimeFinalizationContractError(
                    "terminal_proposal cannot carry subject_key"
                )
        elif self.proposed_outcome is not None:
            raise RuntimeFinalizationContractError(
                "only terminal_proposal may carry proposed_outcome"
            )


@dataclass(frozen=True, slots=True)
class AttemptFinalizationState:
    """Pure coordinator state after work settlement and before acknowledgement."""

    runtime: AttemptRuntime
    resources: tuple[RuntimeResourceRecord, ...] = ()
    artifacts: tuple[RuntimeArtifactRecord, ...] = ()
    events: tuple[RuntimeLifecycleEvent, ...] = ()
    failures: tuple[RuntimeLifecycleFailure, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, AttemptRuntime):
            raise RuntimeFinalizationContractError("runtime must be an AttemptRuntime")
        _require_tuple_of(self.resources, RuntimeResourceRecord, field="resources")
        _require_tuple_of(self.artifacts, RuntimeArtifactRecord, field="artifacts")
        _require_tuple_of(self.events, RuntimeLifecycleEvent, field="events")
        _require_tuple_of(self.failures, RuntimeLifecycleFailure, field="failures")
        attempt = self.runtime.manifest.attempt
        resource_keys = tuple(
            record.reservation.resource_key for record in self.resources
        )
        if len(resource_keys) != len(set(resource_keys)):
            raise RuntimeFinalizationContractError("resource keys must be unique")
        if any(record.reservation.attempt != attempt for record in self.resources):
            raise RuntimeFinalizationContractError(
                "resource reservations must bind the runtime attempt"
            )
        artifact_ids = tuple(
            record.artifact.evidence_artifact_id for record in self.artifacts
        )
        if len(artifact_ids) != len(set(artifact_ids)):
            raise RuntimeFinalizationContractError("artifact ids must be unique")
        if any(
            record.artifact.producer_processing_request_id
            != attempt.processing_request_id
            or record.artifact.producer_processing_attempt_id
            != attempt.processing_attempt_id
            for record in self.artifacts
        ):
            raise RuntimeFinalizationContractError(
                "runtime artifacts must bind the runtime request and attempt"
            )
        manifest_keys = set(self.runtime.manifest.work_unit_keys)
        if any(record.work_unit_key not in manifest_keys for record in self.artifacts):
            raise RuntimeFinalizationContractError(
                "runtime artifacts must bind declared manifest work units"
            )
        first_sequence = len(self.runtime.history.events) + 1
        if tuple(event.sequence for event in self.events) != tuple(
            range(first_sequence, first_sequence + len(self.events))
        ):
            raise RuntimeFinalizationContractError(
                "lifecycle event sequences must continue the attempt history"
            )
        terminal_indexes = tuple(
            index
            for index, event in enumerate(self.events)
            if event.kind is RuntimeLifecycleEventKind.TERMINAL_PROPOSAL
        )
        if terminal_indexes and terminal_indexes != (len(self.events) - 1,):
            raise RuntimeFinalizationContractError(
                "terminal proposal must be the final coordinator event"
            )

    @property
    def terminal_proposed(self) -> bool:
        return bool(
            (
                self.events
                and self.events[-1].kind is RuntimeLifecycleEventKind.TERMINAL_PROPOSAL
            )
            or self.runtime.manifest_preflight_failed
        )


@dataclass(frozen=True, slots=True)
class CoordinatorTerminalRecord:
    """Immutable coordinator proposal through work/resource/artifact closure."""

    state: AttemptFinalizationState
    proposed_outcome: AttemptTerminalOutcome
    coordinator_event_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.state, AttemptFinalizationState):
            raise RuntimeFinalizationContractError(
                "state must be an AttemptFinalizationState"
            )
        if not self.state.terminal_proposed:
            raise RuntimeFinalizationContractError(
                "coordinator record requires a terminal proposal"
            )
        if not isinstance(self.proposed_outcome, AttemptTerminalOutcome):
            raise RuntimeFinalizationContractError(
                "proposed_outcome must be an AttemptTerminalOutcome"
            )
        terminal_event = (
            self.state.events[-1]
            if self.state.events
            else self.state.runtime.history.events[-1]
        )
        if terminal_event.proposed_outcome is not self.proposed_outcome:
            raise RuntimeFinalizationContractError(
                "coordinator outcome must match the terminal event"
            )
        if self.coordinator_event_digest != _coordinator_event_digest(self.state):
            raise RuntimeFinalizationContractError(
                "coordinator_event_digest does not bind the event ledger"
            )


@dataclass(frozen=True, slots=True)
class SupervisorFinalizationEvent:
    """One fact in the supervisor's atomic terminal batch."""

    sequence: int
    kind: SupervisorFinalizationEventKind
    artifact_id: str | None = None
    terminal_outcome: AttemptTerminalOutcome | None = None


@dataclass(frozen=True, slots=True)
class SupervisorFinalizationRecord:
    """Content-bound acknowledgement candidate for owner-supplied persistence."""

    attempt: ProcessingAttemptIdentity
    coordinator_record_sha256: str
    terminal_outcome: AttemptTerminalOutcome
    artifact_dispositions: tuple[tuple[str, RuntimeArtifactDisposition], ...]
    events: tuple[SupervisorFinalizationEvent, ...]
    observability_ledger: RuntimeObservabilityLedger

    def __post_init__(self) -> None:
        _require_attempt(self.attempt)
        _require_sha256(self.coordinator_record_sha256)
        if not isinstance(self.terminal_outcome, AttemptTerminalOutcome):
            raise RuntimeFinalizationContractError(
                "terminal_outcome must be an AttemptTerminalOutcome"
            )
        if not isinstance(self.artifact_dispositions, tuple):
            raise RuntimeFinalizationContractError(
                "artifact_dispositions must be a tuple"
            )
        if not isinstance(self.events, tuple) or any(
            not isinstance(event, SupervisorFinalizationEvent) for event in self.events
        ):
            raise RuntimeFinalizationContractError(
                "events must contain SupervisorFinalizationEvent values"
            )
        if not isinstance(self.observability_ledger, RuntimeObservabilityLedger):
            raise RuntimeFinalizationContractError(
                "observability_ledger must be a RuntimeObservabilityLedger"
            )
        if self.observability_ledger.attempt != self.attempt:
            raise RuntimeFinalizationContractError(
                "observability ledger must bind the finalization attempt"
            )


def _coordinator_event_digest(state: AttemptFinalizationState) -> str:
    payload = {
        "attempt_events": [
            {
                "sequence": item.sequence,
                "kind": item.kind.value,
                "proposed_outcome": (
                    item.proposed_outcome.value if item.proposed_outcome else None
                ),
            }
            for item in state.runtime.history.events
        ],
        "lifecycle_events": [
            {
                "sequence": item.sequence,
                "kind": item.kind.value,
                "subject_key": item.subject_key,
                "proposed_outcome": (
                    item.proposed_outcome.value if item.proposed_outcome else None
                ),
            }
            for item in state.events
        ],
    }
    return sha256_hexdigest(canonical_json_bytes(payload))


def _require_attempt(value: object) -> ProcessingAttemptIdentity:
    if not isinstance(value, ProcessingAttemptIdentity):
        raise RuntimeFinalizationContractError(
            "attempt must be an owner-issued ProcessingAttemptIdentity"
        )
    return value


def _required_text(value: object, *, field: str) -> str:
    return require_owner_normalized_text(
        value,
        field=field,
        error_factory=RuntimeFinalizationContractError,
    )


def _require_sha256(value: object) -> str:
    return require_lower_sha256(
        value,
        field="coordinator_record_sha256",
        error_factory=lambda _: RuntimeFinalizationContractError(
            "coordinator_record_sha256 must be a lower-case SHA-256 digest"
        ),
    )


def _require_tuple_of(value: object, item_type: type, *, field: str) -> None:
    require_tuple_of(
        value,
        item_type,
        field=field,
        error_factory=RuntimeFinalizationContractError,
    )


_OWNER_OBSERVABILITY_EVENT_KINDS = {
    "manifest_closed",
    *(kind.value for kind in AttemptLifecycleEventKind),
    *(kind.value for kind in RuntimeLifecycleEventKind),
    *(kind.value for kind in SupervisorFinalizationEventKind),
}


def begin_attempt_finalization(runtime: AttemptRuntime) -> AttemptFinalizationState:
    """Start the focused lifecycle reducer from one existing work runtime."""

    return AttemptFinalizationState(runtime=runtime)


def reserve_runtime_resource(
    state: AttemptFinalizationState,
    reservation: RuntimeResourceReservation,
) -> AttemptFinalizationState:
    """Record a reservation before the adapter allocates or dispatches it."""

    return _reserve_runtime_resources(state, (reservation,))


def _reserve_runtime_resources(
    state: AttemptFinalizationState,
    reservations: Iterable[RuntimeResourceReservation],
) -> AttemptFinalizationState:
    """Apply ordered reservations without rebuilding the ledger per resource."""

    _require_mutable_state(state)
    resources = {item.reservation.resource_key: item for item in state.resources}
    events: list[RuntimeLifecycleEvent] = []
    failures = dict.fromkeys(state.failures)
    for reservation in reservations:
        if not isinstance(reservation, RuntimeResourceReservation):
            raise RuntimeFinalizationContractError(
                "reservation must be a RuntimeResourceReservation"
            )
        if reservation.attempt != state.runtime.manifest.attempt:
            raise RuntimeFinalizationContractError(
                "resource reservation must bind the runtime attempt"
            )
        key = reservation.resource_key
        existing = resources.get(key)
        if existing is not None:
            if existing.reservation != reservation:
                failures.setdefault(
                    RuntimeLifecycleFailure(
                        RuntimeLifecycleFailureKind.CONFLICTING_RESOURCE_RESERVATION,
                        key,
                        "resource key was reserved with different owner content",
                    )
                )
            continue
        resources[key] = RuntimeResourceRecord(reservation)
        events.append(
            _event(
                state,
                RuntimeLifecycleEventKind.RESOURCE_RESERVED,
                key,
                offset=len(events),
            )
        )
    if not events and len(failures) == len(state.failures):
        return state
    return replace(
        state,
        resources=tuple(resources.values()),
        events=(*state.events, *events),
        failures=tuple(failures),
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

    return _record_runtime_resource_opens(state, ((resource_key, outcome),))


def _record_runtime_resource_opens(
    state: AttemptFinalizationState,
    reports: Iterable[tuple[str, RuntimeResourceOpenOutcome]],
) -> AttemptFinalizationState:
    """Apply ordered open reports with one indexed, validated state update."""

    _require_mutable_state(state)
    resources = {item.reservation.resource_key: item for item in state.resources}
    events: list[RuntimeLifecycleEvent] = []
    failures = dict.fromkeys(state.failures)
    for resource_key, outcome in reports:
        key = _required_text(resource_key, field="resource_key")
        if not isinstance(outcome, RuntimeResourceOpenOutcome):
            raise RuntimeFinalizationContractError(
                "outcome must be a RuntimeResourceOpenOutcome"
            )
        existing = resources.get(key)
        if existing is None:
            failures.setdefault(
                RuntimeLifecycleFailure(
                    RuntimeLifecycleFailureKind.UNREGISTERED_RESOURCE,
                    key,
                    "resource open report arrived without a reservation",
                )
            )
            continue
        if existing.open_outcome is not None:
            if existing.open_outcome is not outcome:
                failures.setdefault(
                    RuntimeLifecycleFailure(
                        RuntimeLifecycleFailureKind.CONFLICTING_RESOURCE_REPORT,
                        key,
                        "late open report conflicts with the accepted resource record",
                    )
                )
            continue
        if (
            outcome is RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED
            and not existing.reservation.owner_conditional
        ):
            failures.setdefault(
                RuntimeLifecycleFailure(
                    RuntimeLifecycleFailureKind.CONFLICTING_RESOURCE_REPORT,
                    key,
                    "required resource cannot close as condition_not_activated",
                )
            )
        resources[key] = replace(existing, open_outcome=outcome)
        events.append(_event(state, _open_event_kind(outcome), key, offset=len(events)))
    if not events and len(failures) == len(state.failures):
        return state
    return replace(
        state,
        resources=tuple(resources.values()),
        events=(*state.events, *events),
        failures=tuple(failures),
    )


def record_runtime_resource_cleanup(
    state: AttemptFinalizationState,
    *,
    resource_key: str,
    outcome: RuntimeResourceCleanupOutcome,
) -> AttemptFinalizationState:
    """Accept the first terminal cleanup; identical replay is idempotent."""

    return _record_runtime_resource_cleanups(state, ((resource_key, outcome),))


def _record_runtime_resource_cleanups(
    state: AttemptFinalizationState,
    reports: Iterable[tuple[str, RuntimeResourceCleanupOutcome]],
) -> AttemptFinalizationState:
    """Apply ordered cleanup reports with one indexed, validated state update."""

    _require_mutable_state(state)
    resources = {item.reservation.resource_key: item for item in state.resources}
    events: list[RuntimeLifecycleEvent] = []
    failures = dict.fromkeys(state.failures)
    for resource_key, outcome in reports:
        key = _required_text(resource_key, field="resource_key")
        if not isinstance(outcome, RuntimeResourceCleanupOutcome):
            raise RuntimeFinalizationContractError(
                "outcome must be a RuntimeResourceCleanupOutcome"
            )
        existing = resources.get(key)
        if existing is None:
            failures.setdefault(
                RuntimeLifecycleFailure(
                    RuntimeLifecycleFailureKind.UNREGISTERED_RESOURCE,
                    key,
                    "resource cleanup report arrived without a reservation",
                )
            )
            continue
        if existing.open_outcome is None:
            raise RuntimeFinalizationContractError(
                "resource cleanup requires an accepted open outcome"
            )
        if existing.cleanup_outcome is not None:
            if existing.cleanup_outcome is not outcome:
                failures.setdefault(
                    RuntimeLifecycleFailure(
                        RuntimeLifecycleFailureKind.CONFLICTING_RESOURCE_REPORT,
                        key,
                        "late cleanup report conflicts with the terminal "
                        "resource record",
                    )
                )
            continue
        resources[key] = replace(existing, cleanup_outcome=outcome)
        events.append(
            _event(state, _cleanup_event_kind(outcome), key, offset=len(events))
        )
    if not events and len(failures) == len(state.failures):
        return state
    return replace(
        state,
        resources=tuple(resources.values()),
        events=(*state.events, *events),
        failures=tuple(failures),
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
    record = _runtime_artifact_registration(
        state,
        work_unit_key=work_unit_key,
        artifact=artifact,
        manifest_keys=state.runtime.manifest.work_unit_keys,
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


def _register_runtime_artifacts(
    state: AttemptFinalizationState,
    registrations: Iterable[tuple[str, EvidenceArtifactRef]],
) -> AttemptFinalizationState:
    """Register ordered artifacts with one complete, strictly validated state."""

    artifacts_by_id = {
        item.artifact.evidence_artifact_id: item for item in state.artifacts
    }
    manifest_keys = set(state.runtime.manifest.work_unit_keys)
    artifacts = list(state.artifacts)
    events: list[RuntimeLifecycleEvent] = []
    failures = list(state.failures)
    accepted_failures = set(state.failures)
    for work_unit_key, artifact in registrations:
        record = _runtime_artifact_registration(
            state,
            work_unit_key=work_unit_key,
            artifact=artifact,
            manifest_keys=manifest_keys,
        )
        artifact_id = artifact.evidence_artifact_id
        existing = artifacts_by_id.get(artifact_id)
        if existing is not None:
            if existing != record:
                failure = RuntimeLifecycleFailure(
                    RuntimeLifecycleFailureKind.CONFLICTING_ARTIFACT_REGISTRATION,
                    artifact_id,
                    "artifact was registered against a different work unit",
                )
                if failure not in accepted_failures:
                    failures.append(failure)
                    accepted_failures.add(failure)
            continue
        artifacts_by_id[artifact_id] = record
        artifacts.append(record)
        events.append(
            _event(
                state,
                RuntimeLifecycleEventKind.ARTIFACT_REGISTERED,
                artifact_id,
                offset=len(events),
            )
        )
    if not events and len(failures) == len(state.failures):
        return state
    return replace(
        state,
        artifacts=tuple(artifacts),
        events=(*state.events, *events),
        failures=tuple(failures),
    )


def _runtime_artifact_registration(
    state: AttemptFinalizationState,
    *,
    work_unit_key: str,
    artifact: EvidenceArtifactRef,
    manifest_keys: Collection[str],
) -> RuntimeArtifactRecord:
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
    if record.work_unit_key not in manifest_keys:
        raise RuntimeFinalizationContractError(
            "runtime artifact must bind a declared manifest work unit"
        )
    return record


def propose_attempt_terminal(
    state: AttemptFinalizationState,
) -> CoordinatorTerminalRecord:
    """Freeze a terminal proposal only after all mechanical inputs close."""

    if not isinstance(state, AttemptFinalizationState):
        raise RuntimeFinalizationContractError(
            "state must be an AttemptFinalizationState"
        )
    if state.runtime.manifest_preflight_failed:
        if state.resources or state.artifacts or state.events or state.failures:
            raise RuntimeFinalizationContractError(
                "preflight-terminalized attempt must have empty runtime registries"
            )
        return CoordinatorTerminalRecord(
            state=state,
            proposed_outcome=AttemptTerminalOutcome.FAILED,
            coordinator_event_digest=_coordinator_event_digest(state),
        )
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
    _validate_observability_against_coordinator(coordinator, observability_ledger)
    start = (
        len(coordinator.state.runtime.history.events)
        + len(coordinator.state.events)
        + 1
    )
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


def _validate_observability_against_coordinator(
    coordinator: CoordinatorTerminalRecord,
    ledger: RuntimeObservabilityLedger,
) -> None:
    state = coordinator.state
    expected: Counter[tuple[str, RuntimeObservationSubject, str]] = Counter()
    for history_event in state.runtime.history.events:
        expected[
            (
                history_event.kind.value,
                RuntimeObservationSubject.ATTEMPT,
                "attempt",
            )
        ] += 1
    if not state.runtime.manifest_preflight_failed:
        expected[
            (
                "manifest_closed",
                RuntimeObservationSubject.ATTEMPT,
                "manifest",
            )
        ] += 1
    for lifecycle_event in state.events:
        subject_key: str | None
        if lifecycle_event.kind is RuntimeLifecycleEventKind.TERMINAL_PROPOSAL:
            subject = RuntimeObservationSubject.ATTEMPT
            subject_key = "attempt"
        elif lifecycle_event.kind is RuntimeLifecycleEventKind.ARTIFACT_REGISTERED:
            subject = RuntimeObservationSubject.ARTIFACT
            subject_key = lifecycle_event.subject_key
        else:
            subject = RuntimeObservationSubject.RESOURCE
            subject_key = lifecycle_event.subject_key
        if subject_key is None:
            raise RuntimeFinalizationContractError(
                "coordinator lifecycle event is missing observability correlation"
            )
        expected[(lifecycle_event.kind.value, subject, subject_key)] += 1
    expected[
        (
            "terminal_acknowledged",
            RuntimeObservationSubject.ATTEMPT,
            "attempt",
        )
    ] += 1
    for artifact in state.artifacts:
        disposition_kind = (
            "artifact_offered_to_owner"
            if artifact.proposed_disposition
            is RuntimeArtifactDisposition.OFFERED_TO_OWNER
            else "artifact_quarantined_diagnostic"
        )
        expected[
            (
                disposition_kind,
                RuntimeObservationSubject.ARTIFACT,
                artifact.artifact.evidence_artifact_id,
            )
        ] += 1
    expected[
        (
            "terminal_outcome",
            RuntimeObservationSubject.ATTEMPT,
            "attempt",
        )
    ] += 1

    observed = Counter(
        (event.kind, event.subject, event.subject_key)
        for event in ledger.events
        if event.kind in _OWNER_OBSERVABILITY_EVENT_KINDS
    )
    if observed != expected:
        raise RuntimeFinalizationContractError(
            "observability ledger lifecycle conflicts with finalized runtime facts"
        )

    raw_open_records = tuple(
        metric for metric in ledger.metrics if metric.name == "raw_open_count"
    )
    expected_raw_open_count = sum(
        record.reservation.kind is RuntimeResourceKind.ACQUISITION_SESSION
        and record.open_outcome is RuntimeResourceOpenOutcome.OPENED
        for record in state.resources
    )
    if (
        len(raw_open_records) != 1
        or raw_open_records[0].availability is not RuntimeMetricAvailability.MEASURED
        or raw_open_records[0].value != expected_raw_open_count
    ):
        raise RuntimeFinalizationContractError(
            "observability ledger raw_open_count conflicts with finalized "
            "runtime resources"
        )


def canonical_coordinator_terminal_record_bytes(
    coordinator: CoordinatorTerminalRecord,
) -> bytes:
    """Return deterministic recovery-record bytes without product payloads."""

    state = coordinator.state
    integrity_failure = state.runtime.integrity_failure
    payload = {
        "schema_version": "execution_runtime_coordinator_terminal",
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
                "resource_kind": item.reservation.kind.value,
                "resource_key": item.reservation.resource_key,
                "worker_key": item.reservation.worker_key,
                "source_reference": item.reservation.source_reference,
                "compatibility_token": item.reservation.compatibility_token,
                "owner_conditional": item.reservation.owner_conditional,
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
    resource_not_opened_without_cancellation = (
        not state.runtime.history.cancellation_accepted
        and any(
            record.open_outcome is RuntimeResourceOpenOutcome.NOT_OPENED
            for record in state.resources
        )
    )
    if (
        state.runtime.must_fail
        or state.failures
        or resource_open_failed
        or cleanup_failed
        or resource_not_opened_without_cancellation
    ):
        return AttemptTerminalOutcome.FAILED
    if state.runtime.history.cancellation_accepted:
        return AttemptTerminalOutcome.CANCELLED
    return AttemptTerminalOutcome.SUCCEEDED


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
        RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED: (
            RuntimeLifecycleEventKind.RESOURCE_CONDITION_NOT_ACTIVATED
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


__all__ = (
    "RuntimeArtifactDisposition",
    "RuntimeFinalizationContractError",
    "RuntimeLifecycleEventKind",
    "RuntimeLifecycleFailureKind",
    "RuntimeResourceCleanupOutcome",
    "RuntimeResourceKind",
    "RuntimeResourceOpenOutcome",
    "RuntimeResourceReservation",
    "SupervisorFinalizationEventKind",
    "canonical_coordinator_terminal_record_bytes",
)
