"""Immutable value objects for execution-runtime terminal settlement."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.execution_runtime import AttemptRuntime, AttemptTerminalOutcome
from xic_extractor.execution_runtime_observability import RuntimeObservabilityLedger
from xic_extractor.processing_identity import ProcessingAttemptIdentity


class RuntimeFinalizationContractError(ValueError):
    """Raised when one attempt's mechanical terminal state cannot close."""


class RuntimeResourceOpenOutcome(str, Enum):
    """Exactly one observable result after a resource reservation."""

    OPENED = "opened"
    OPEN_FAILED = "open_failed"
    NOT_OPENED = "not_opened"
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

    def __post_init__(self) -> None:
        _require_attempt(self.attempt)
        if not isinstance(self.kind, RuntimeResourceKind):
            raise RuntimeFinalizationContractError("kind must be a RuntimeResourceKind")
        _required_text(self.resource_key, field="resource_key")
        _required_text(self.worker_key, field="worker_key")
        _required_text(self.source_reference, field="source_reference")
        _required_text(self.compatibility_token, field="compatibility_token")


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
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_attempt(value: object) -> ProcessingAttemptIdentity:
    if not isinstance(value, ProcessingAttemptIdentity):
        raise RuntimeFinalizationContractError(
            "attempt must be an owner-issued ProcessingAttemptIdentity"
        )
    return value


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RuntimeFinalizationContractError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


def _require_sha256(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RuntimeFinalizationContractError(
            "coordinator_record_sha256 must be a lower-case SHA-256 digest"
        )
    return value


def _require_tuple_of(value: object, item_type: type, *, field: str) -> None:
    if not isinstance(value, tuple) or any(
        not isinstance(item, item_type) for item in value
    ):
        raise RuntimeFinalizationContractError(
            f"{field} must be a tuple of {item_type.__name__} values"
        )


__all__ = (
    "RuntimeArtifactDisposition",
    "RuntimeFinalizationContractError",
    "RuntimeLifecycleEventKind",
    "RuntimeLifecycleFailureKind",
    "RuntimeResourceCleanupOutcome",
    "RuntimeResourceKind",
    "RuntimeResourceOpenOutcome",
    "SupervisorFinalizationEventKind",
)
