"""Bounded Backfill-owned transactions."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .observation_revisit import (
        ObservationRevisitAuthorization,
        ObservationRevisitAuthorizationState,
        ObservationRevisitCandidate,
        ObservationRevisitContractError,
        ObservationRevisitEligibleAuthorization,
        ObservationRevisitEvidence,
        ObservationRevisitMultipleEligibleAuthorization,
        ObservationRevisitNoResultTerminal,
        ObservationRevisitNotRunAuthorization,
        ObservationRevisitOutcome,
        ObservationRevisitReceipt,
        ObservationRevisitSubject,
    )

_OBSERVATION_REPAIR_EXPORTS = {
    "ObservationRevisitAuthorization",
    "ObservationRevisitAuthorizationState",
    "ObservationRevisitCandidate",
    "ObservationRevisitContractError",
    "ObservationRevisitEvidence",
    "ObservationRevisitMultipleEligibleAuthorization",
    "ObservationRevisitNoResultTerminal",
    "ObservationRevisitNotRunAuthorization",
    "ObservationRevisitOutcome",
    "ObservationRevisitEligibleAuthorization",
    "ObservationRevisitReceipt",
    "ObservationRevisitSubject",
}


def __getattr__(name: str) -> Any:
    if name not in _OBSERVATION_REPAIR_EXPORTS:
        raise AttributeError(name)
    from . import observation_revisit

    return getattr(observation_revisit, name)


__all__ = (
    "ObservationRevisitAuthorization",
    "ObservationRevisitAuthorizationState",
    "ObservationRevisitCandidate",
    "ObservationRevisitContractError",
    "ObservationRevisitEvidence",
    "ObservationRevisitMultipleEligibleAuthorization",
    "ObservationRevisitNoResultTerminal",
    "ObservationRevisitNotRunAuthorization",
    "ObservationRevisitOutcome",
    "ObservationRevisitEligibleAuthorization",
    "ObservationRevisitReceipt",
    "ObservationRevisitSubject",
)
