from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

SUPERVISOR_FINALIZATION_CLASSIFICATION = "runtime_recovery_metadata"


class RuntimeContractError(ValueError):
    """Raised when an attempt history violates the no-RAW runtime contract."""


class AttemptOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RuntimeEventKind(str, Enum):
    ATTEMPT_QUEUED = "attempt_queued"
    MANIFEST_PREFLIGHT_FAILED = "manifest_preflight_failed"
    ATTEMPT_STARTED = "attempt_started"
    CANCELLATION_REQUESTED = "cancellation_requested"
    WORK_SUCCEEDED = "work_succeeded"
    WORK_FAILED = "work_failed"
    WORK_CANCELLED = "work_cancelled"
    RESOURCE_RESERVED = "resource_reserved"
    RESOURCE_OPENED = "resource_opened"
    RESOURCE_ACCESSED = "resource_accessed"
    RESOURCE_OPEN_FAILED = "resource_open_failed"
    RESOURCE_NOT_OPENED = "resource_not_opened"
    RESOURCE_UNKNOWN_AFTER_WORKER_LOSS = "resource_unknown_after_worker_loss"
    CLEANUP_SUCCEEDED = "cleanup_succeeded"
    CLEANUP_FAILED = "cleanup_failed"
    CLEANUP_UNKNOWN_AFTER_WORKER_LOSS = "cleanup_unknown_after_worker_loss"
    ARTIFACT_REGISTERED = "artifact_registered"
    ARTIFACT_OFFERED_TO_OWNER = "artifact_offered_to_owner"
    ARTIFACT_ACKNOWLEDGED = "artifact_acknowledged"
    ARTIFACT_QUARANTINED = "artifact_quarantined_diagnostic"
    ATTEMPT_TERMINAL = "attempt_terminal"


@dataclass(frozen=True)
class RequiredWorkManifest:
    work_unit_keys: tuple[str, ...]
    owner_attests_complete: bool

    def __post_init__(self) -> None:
        if not self.owner_attests_complete:
            raise RuntimeContractError(
                "required-work manifest needs owner completeness attestation"
            )
        if not self.work_unit_keys:
            raise RuntimeContractError("required-work manifest must not be empty")
        if any(not key.strip() for key in self.work_unit_keys):
            raise RuntimeContractError("work-unit keys cannot be blank")
        if len(self.work_unit_keys) != len(set(self.work_unit_keys)):
            raise RuntimeContractError("required-work manifest keys must be unique")


@dataclass(frozen=True)
class RuntimeEvent:
    sequence: int
    attempt_id: str
    kind: RuntimeEventKind
    subject_id: str = ""
    outcome: AttemptOutcome | None = None


@dataclass(frozen=True)
class ValidatedAttemptHistory:
    attempt_id: str
    outcome: AttemptOutcome
    settled_work: tuple[str, ...]
    acknowledged_artifacts: tuple[str, ...]
    quarantined_artifacts: tuple[str, ...]


def validate_attempt_history(
    manifest: RequiredWorkManifest,
    events: tuple[RuntimeEvent, ...],
) -> ValidatedAttemptHistory:
    """Validate an ordered history; this is a contract checker, not a scheduler."""

    if not events:
        raise RuntimeContractError("attempt history must not be empty")
    attempt_ids = {event.attempt_id for event in events}
    if len(attempt_ids) != 1 or "" in attempt_ids:
        raise RuntimeContractError("events must reference exactly one attempt")
    expected_sequences = tuple(range(1, len(events) + 1))
    if tuple(event.sequence for event in events) != expected_sequences:
        raise RuntimeContractError("runtime event sequence must be contiguous")
    if events[0].kind is not RuntimeEventKind.ATTEMPT_QUEUED:
        raise RuntimeContractError("attempt history must start with attempt_queued")

    phase = "queued"
    cancellation_requested = False
    preflight_failed = False
    attempt_started = False
    work_states: dict[str, str] = {}
    resources: dict[str, str] = {}
    cleanup_states: dict[str, str] = {}
    artifacts: dict[str, str] = {}
    terminal_event: RuntimeEvent | None = None

    for event in events[1:]:
        if terminal_event is not None:
            raise RuntimeContractError("events cannot follow attempt_terminal")
        kind = event.kind
        if kind is not RuntimeEventKind.ATTEMPT_TERMINAL and event.outcome is not None:
            raise RuntimeContractError(
                f"{kind.value} cannot declare a terminal outcome"
            )
        if kind is RuntimeEventKind.MANIFEST_PREFLIGHT_FAILED:
            _require_phase(phase, "queued", kind)
            if preflight_failed:
                raise RuntimeContractError(
                    "manifest_preflight_failed cannot be emitted more than once"
                )
            preflight_failed = True
        elif kind is RuntimeEventKind.ATTEMPT_STARTED:
            _require_phase(phase, "queued", kind)
            if preflight_failed:
                raise RuntimeContractError(
                    "preflight-failed attempt cannot emit attempt_started"
                )
            attempt_started = True
            phase = "running"
        elif kind is RuntimeEventKind.CANCELLATION_REQUESTED:
            if phase not in {"queued", "running"}:
                raise RuntimeContractError(
                    "cancellation requires queued or running phase"
                )
            if cancellation_requested:
                raise RuntimeContractError(
                    "cancellation_requested cannot be emitted more than once"
                )
            cancellation_requested = True
        elif kind in {
            RuntimeEventKind.WORK_SUCCEEDED,
            RuntimeEventKind.WORK_FAILED,
            RuntimeEventKind.WORK_CANCELLED,
        }:
            _require_phase(phase, "running", kind)
            _require_subject(event)
            if event.subject_id not in manifest.work_unit_keys:
                raise RuntimeContractError(
                    f"work unit {event.subject_id!r} is not in the closed manifest"
                )
            if event.subject_id in work_states:
                raise RuntimeContractError(
                    f"work unit {event.subject_id!r} settled more than once"
                )
            if (
                kind is RuntimeEventKind.WORK_CANCELLED
                and not cancellation_requested
            ):
                raise RuntimeContractError(
                    "work_cancelled requires a cancellation request"
                )
            work_states[event.subject_id] = {
                RuntimeEventKind.WORK_SUCCEEDED: "succeeded",
                RuntimeEventKind.WORK_FAILED: "failed",
                RuntimeEventKind.WORK_CANCELLED: "cancelled",
            }[kind]
        elif kind is RuntimeEventKind.RESOURCE_RESERVED:
            _require_phase(phase, "running", kind)
            _require_subject(event)
            if event.subject_id in resources:
                raise RuntimeContractError(
                    f"resource {event.subject_id!r} reserved more than once"
                )
            resources[event.subject_id] = "reserved"
        elif kind in {
            RuntimeEventKind.RESOURCE_OPENED,
            RuntimeEventKind.RESOURCE_OPEN_FAILED,
            RuntimeEventKind.RESOURCE_NOT_OPENED,
            RuntimeEventKind.RESOURCE_UNKNOWN_AFTER_WORKER_LOSS,
        }:
            _require_phase(phase, "running", kind)
            _transition_resource(resources, event, expected="reserved")
        elif kind is RuntimeEventKind.RESOURCE_ACCESSED:
            _require_phase(phase, "running", kind)
            _require_subject(event)
            if (
                resources.get(event.subject_id)
                != RuntimeEventKind.RESOURCE_OPENED.value
            ):
                raise RuntimeContractError(
                    f"resource {event.subject_id!r} cannot be accessed unless open"
                )
            if event.subject_id in cleanup_states:
                raise RuntimeContractError(
                    f"resource {event.subject_id!r} cannot be accessed after cleanup"
                )
        elif kind in {
            RuntimeEventKind.CLEANUP_SUCCEEDED,
            RuntimeEventKind.CLEANUP_FAILED,
            RuntimeEventKind.CLEANUP_UNKNOWN_AFTER_WORKER_LOSS,
        }:
            _require_phase(phase, "running", kind)
            _require_subject(event)
            if event.subject_id not in resources:
                raise RuntimeContractError(
                    f"cleanup references unknown resource {event.subject_id!r}"
                )
            if resources[event.subject_id] == "reserved":
                raise RuntimeContractError(
                    f"resource {event.subject_id!r} has no observable open outcome"
                )
            if event.subject_id in cleanup_states:
                raise RuntimeContractError(
                    f"resource {event.subject_id!r} cleaned more than once"
                )
            cleanup_states[event.subject_id] = kind.value
        elif kind is RuntimeEventKind.ARTIFACT_REGISTERED:
            _require_phase(phase, "running", kind)
            _require_subject(event)
            if event.subject_id in artifacts:
                raise RuntimeContractError(
                    f"artifact {event.subject_id!r} registered more than once"
                )
            artifacts[event.subject_id] = "registered"
        elif kind in {
            RuntimeEventKind.ARTIFACT_OFFERED_TO_OWNER,
            RuntimeEventKind.ARTIFACT_QUARANTINED,
        }:
            _require_phase(phase, "running", kind)
            expected_states = (
                {"registered", RuntimeEventKind.ARTIFACT_OFFERED_TO_OWNER.value}
                if kind is RuntimeEventKind.ARTIFACT_QUARANTINED
                else {"registered"}
            )
            _transition_artifact(artifacts, event, expected=expected_states)
        elif kind is RuntimeEventKind.ARTIFACT_ACKNOWLEDGED:
            _require_phase(phase, "running", kind)
            _transition_artifact(
                artifacts,
                event,
                expected={RuntimeEventKind.ARTIFACT_OFFERED_TO_OWNER.value},
            )
        elif kind is RuntimeEventKind.ATTEMPT_TERMINAL:
            if event.outcome is None:
                raise RuntimeContractError("attempt_terminal requires an outcome")
            terminal_event = event
            phase = "terminal"
        else:  # pragma: no cover - Enum exhaustiveness guard
            raise RuntimeContractError(f"unsupported runtime event {kind!r}")

    if terminal_event is None:
        raise RuntimeContractError("attempt history must end with attempt_terminal")
    if set(resources) != set(cleanup_states):
        raise RuntimeContractError(
            "every reserved resource must be cleaned exactly once"
        )
    undisposed = [
        artifact_id
        for artifact_id, state in artifacts.items()
        if state
        not in {
            RuntimeEventKind.ARTIFACT_ACKNOWLEDGED.value,
            RuntimeEventKind.ARTIFACT_QUARANTINED.value,
        }
    ]
    if undisposed:
        raise RuntimeContractError(
            "every artifact must be acknowledged or quarantined before terminal"
        )

    expected_outcome = _expected_outcome(
        manifest=manifest,
        preflight_failed=preflight_failed,
        attempt_started=attempt_started,
        cancellation_requested=cancellation_requested,
        work_states=work_states,
        cleanup_states=cleanup_states,
        resources=resources,
    )
    if terminal_event.outcome is not expected_outcome:
        raise RuntimeContractError(
            f"terminal outcome must be {expected_outcome.value!r}"
        )
    _validate_artifact_disposition(artifacts, expected_outcome)
    return ValidatedAttemptHistory(
        attempt_id=terminal_event.attempt_id,
        outcome=expected_outcome,
        settled_work=tuple(sorted(work_states)),
        acknowledged_artifacts=tuple(
            sorted(
                artifact_id
                for artifact_id, state in artifacts.items()
                if state == RuntimeEventKind.ARTIFACT_ACKNOWLEDGED.value
            )
        ),
        quarantined_artifacts=tuple(
            sorted(
                artifact_id
                for artifact_id, state in artifacts.items()
                if state == RuntimeEventKind.ARTIFACT_QUARANTINED.value
            )
        ),
    )


def _expected_outcome(
    *,
    manifest: RequiredWorkManifest,
    preflight_failed: bool,
    attempt_started: bool,
    cancellation_requested: bool,
    work_states: dict[str, str],
    cleanup_states: dict[str, str],
    resources: dict[str, str],
) -> AttemptOutcome:
    if not attempt_started:
        if preflight_failed:
            return AttemptOutcome.FAILED
        if cancellation_requested:
            return AttemptOutcome.CANCELLED
        raise RuntimeContractError(
            "attempt must start before it can reach a terminal outcome"
        )
    if attempt_started and set(work_states) != set(manifest.work_unit_keys):
        raise RuntimeContractError(
            "started attempt must settle every manifest work unit"
        )
    failed = (
        preflight_failed
        or "failed" in work_states.values()
        or RuntimeEventKind.CLEANUP_FAILED.value in cleanup_states.values()
        or RuntimeEventKind.CLEANUP_UNKNOWN_AFTER_WORKER_LOSS.value
        in cleanup_states.values()
        or RuntimeEventKind.RESOURCE_UNKNOWN_AFTER_WORKER_LOSS.value
        in resources.values()
        or RuntimeEventKind.RESOURCE_OPEN_FAILED.value in resources.values()
        or (
            RuntimeEventKind.RESOURCE_NOT_OPENED.value in resources.values()
            and not cancellation_requested
        )
    )
    if failed:
        return AttemptOutcome.FAILED
    if cancellation_requested:
        return AttemptOutcome.CANCELLED
    if any(state != "succeeded" for state in work_states.values()):
        raise RuntimeContractError(
            "successful attempt must settle every manifest work unit"
        )
    return AttemptOutcome.SUCCEEDED


def _validate_artifact_disposition(
    artifacts: dict[str, str],
    outcome: AttemptOutcome,
) -> None:
    expected_state = (
        RuntimeEventKind.ARTIFACT_ACKNOWLEDGED.value
        if outcome is AttemptOutcome.SUCCEEDED
        else RuntimeEventKind.ARTIFACT_QUARANTINED.value
    )
    invalid = sorted(
        artifact_id
        for artifact_id, state in artifacts.items()
        if state != expected_state
    )
    if invalid:
        disposition = (
            "acknowledged"
            if outcome is AttemptOutcome.SUCCEEDED
            else "quarantined"
        )
        raise RuntimeContractError(
            f"{outcome.value} attempt requires all artifacts to be "
            f"{disposition}: {', '.join(invalid)}"
        )


def _require_phase(actual: str, expected: str, kind: RuntimeEventKind) -> None:
    if actual != expected:
        raise RuntimeContractError(
            f"{kind.value} requires {expected} phase, got {actual}"
        )


def _require_subject(event: RuntimeEvent) -> None:
    if not event.subject_id.strip():
        raise RuntimeContractError(f"{event.kind.value} requires subject_id")


def _transition_resource(
    resources: dict[str, str],
    event: RuntimeEvent,
    *,
    expected: str,
) -> None:
    _require_subject(event)
    if resources.get(event.subject_id) != expected:
        raise RuntimeContractError(
            f"resource {event.subject_id!r} cannot transition via {event.kind.value}"
        )
    resources[event.subject_id] = event.kind.value


def _transition_artifact(
    artifacts: dict[str, str],
    event: RuntimeEvent,
    *,
    expected: set[str],
) -> None:
    _require_subject(event)
    if artifacts.get(event.subject_id) not in expected:
        raise RuntimeContractError(
            f"artifact {event.subject_id!r} cannot transition via {event.kind.value}"
        )
    artifacts[event.subject_id] = event.kind.value
