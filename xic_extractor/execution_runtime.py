from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from xic_extractor.processing_identity import ProcessingAttemptIdentity


class AttemptLifecycleContractError(ValueError):
    """Raised when owner-backed attempt lifecycle facts do not close."""


class AttemptLifecycleEventKind(str, Enum):
    """Package 0 event kinds needed to freeze initial cancellation ordering."""

    ATTEMPT_QUEUED = "attempt_queued"
    ATTEMPT_STARTED = "attempt_started"
    CANCELLATION_ACCEPTED = "cancellation_accepted"
    MANIFEST_PREFLIGHT_FAILED = "manifest_preflight_failed"
    TERMINAL_PROPOSAL = "terminal_proposal"


class AttemptTerminalOutcome(str, Enum):
    """Canonical proposed outcomes; acknowledgement is a later contract."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AttemptLifecycleEvent:
    """One ordered runtime fact bound to an owner-issued processing attempt."""

    attempt: ProcessingAttemptIdentity
    sequence: int
    kind: AttemptLifecycleEventKind
    proposed_outcome: AttemptTerminalOutcome | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise AttemptLifecycleContractError(
                "attempt must be an owner-issued ProcessingAttemptIdentity"
            )
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence <= 0
        ):
            raise AttemptLifecycleContractError("sequence must be a positive integer")
        if not isinstance(self.kind, AttemptLifecycleEventKind):
            raise AttemptLifecycleContractError(
                "kind must be an AttemptLifecycleEventKind"
            )
        if self.kind is AttemptLifecycleEventKind.TERMINAL_PROPOSAL:
            if not isinstance(self.proposed_outcome, AttemptTerminalOutcome):
                raise AttemptLifecycleContractError(
                    "terminal_proposal requires an AttemptTerminalOutcome"
                )
        elif self.proposed_outcome is not None:
            raise AttemptLifecycleContractError(
                "only terminal_proposal may carry a proposed outcome"
            )


@dataclass(frozen=True, slots=True)
class AttemptLifecycleHistory:
    """Immutable prefix of one attempt's append-only lifecycle history."""

    attempt: ProcessingAttemptIdentity
    events: tuple[AttemptLifecycleEvent, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise AttemptLifecycleContractError(
                "attempt must be an owner-issued ProcessingAttemptIdentity"
            )
        if not isinstance(self.events, tuple):
            raise AttemptLifecycleContractError("events must be a tuple")
        if self.events:
            first_event = self.events[0]
            if not isinstance(first_event, AttemptLifecycleEvent):
                raise AttemptLifecycleContractError(
                    "events must contain AttemptLifecycleEvent values"
                )
            if first_event.kind is not AttemptLifecycleEventKind.ATTEMPT_QUEUED:
                raise AttemptLifecycleContractError(
                    "first event must be attempt_queued"
                )

        queued = False
        started = False
        cancellation_accepted = False
        manifest_preflight_failed = False
        terminal_proposed = False
        for expected_sequence, event in enumerate(self.events, start=1):
            if not isinstance(event, AttemptLifecycleEvent):
                raise AttemptLifecycleContractError(
                    "events must contain AttemptLifecycleEvent values"
                )
            if event.attempt != self.attempt:
                raise AttemptLifecycleContractError(
                    "events must reference the owner-issued attempt"
                )
            if event.sequence != expected_sequence:
                raise AttemptLifecycleContractError(
                    "event sequences must be contiguous starting at 1"
                )
            if terminal_proposed:
                raise AttemptLifecycleContractError(
                    "terminal proposal is final in the coordinator history"
                )

            if event.kind is AttemptLifecycleEventKind.ATTEMPT_QUEUED:
                if queued:
                    raise AttemptLifecycleContractError(
                        "attempt_queued may appear only as the first event"
                    )
                queued = True
                continue

            if not queued:
                raise AttemptLifecycleContractError(
                    "first event must be attempt_queued"
                )
            if event.kind is AttemptLifecycleEventKind.ATTEMPT_STARTED:
                if started:
                    raise AttemptLifecycleContractError(
                        "attempt_started may appear only once"
                    )
                if manifest_preflight_failed:
                    raise AttemptLifecycleContractError(
                        "attempt cannot start after manifest preflight failure"
                    )
                if cancellation_accepted:
                    raise AttemptLifecycleContractError(
                        "attempt cannot start after queued cancellation"
                    )
                started = True
                continue
            if event.kind is AttemptLifecycleEventKind.CANCELLATION_ACCEPTED:
                if cancellation_accepted:
                    raise AttemptLifecycleContractError(
                        "cancellation_accepted may appear only once"
                    )
                if manifest_preflight_failed:
                    raise AttemptLifecycleContractError(
                        "cancellation cannot be accepted after manifest "
                        "preflight failure"
                    )
                cancellation_accepted = True
                continue
            if event.kind is AttemptLifecycleEventKind.MANIFEST_PREFLIGHT_FAILED:
                if manifest_preflight_failed:
                    raise AttemptLifecycleContractError(
                        "manifest_preflight_failed may appear only once"
                    )
                if started:
                    raise AttemptLifecycleContractError(
                        "manifest preflight failure must occur before attempt_started"
                    )
                if cancellation_accepted:
                    raise AttemptLifecycleContractError(
                        "manifest preflight failure cannot follow queued cancellation"
                    )
                manifest_preflight_failed = True
                continue
            if event.kind is AttemptLifecycleEventKind.TERMINAL_PROPOSAL:
                if not manifest_preflight_failed:
                    raise AttemptLifecycleContractError(
                        "terminal proposal must follow manifest_preflight_failed"
                    )
                if event.proposed_outcome is not AttemptTerminalOutcome.FAILED:
                    raise AttemptLifecycleContractError(
                        "manifest preflight failure must propose failed"
                    )
                terminal_proposed = True
                continue
            raise AttemptLifecycleContractError(
                f"unsupported lifecycle event kind: {event.kind.value}"
            )

    @property
    def cancellation_accepted(self) -> bool:
        return any(
            event.kind is AttemptLifecycleEventKind.CANCELLATION_ACCEPTED
            for event in self.events
        )


def append_attempt_lifecycle_event(
    history: AttemptLifecycleHistory,
    kind: AttemptLifecycleEventKind,
    *,
    proposed_outcome: AttemptTerminalOutcome | None = None,
) -> AttemptLifecycleHistory:
    """Return a new validated history without mutating its previous prefix."""

    if not isinstance(history, AttemptLifecycleHistory):
        raise AttemptLifecycleContractError(
            "history must be an AttemptLifecycleHistory"
        )
    event = AttemptLifecycleEvent(
        attempt=history.attempt,
        sequence=len(history.events) + 1,
        kind=kind,
        proposed_outcome=proposed_outcome,
    )
    return AttemptLifecycleHistory(
        attempt=history.attempt,
        events=(*history.events, event),
    )


def propose_manifest_preflight_failure(
    history: AttemptLifecycleHistory,
) -> AttemptLifecycleHistory:
    """Append the canonical queued-to-failed proposal path atomically to a prefix."""

    failed = append_attempt_lifecycle_event(
        history,
        AttemptLifecycleEventKind.MANIFEST_PREFLIGHT_FAILED,
    )
    return append_attempt_lifecycle_event(
        failed,
        AttemptLifecycleEventKind.TERMINAL_PROPOSAL,
        proposed_outcome=AttemptTerminalOutcome.FAILED,
    )


__all__ = [
    "AttemptLifecycleContractError",
    "AttemptLifecycleEvent",
    "AttemptLifecycleEventKind",
    "AttemptLifecycleHistory",
    "AttemptTerminalOutcome",
    "append_attempt_lifecycle_event",
    "propose_manifest_preflight_failure",
]
