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


@dataclass(frozen=True, slots=True)
class AttemptLifecycleEvent:
    """One ordered runtime fact bound to an owner-issued processing attempt."""

    attempt: ProcessingAttemptIdentity
    sequence: int
    kind: AttemptLifecycleEventKind

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
                cancellation_accepted = True
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
    )
    return AttemptLifecycleHistory(
        attempt=history.attempt,
        events=(*history.events, event),
    )


__all__ = [
    "AttemptLifecycleContractError",
    "AttemptLifecycleEvent",
    "AttemptLifecycleEventKind",
    "AttemptLifecycleHistory",
    "append_attempt_lifecycle_event",
]
