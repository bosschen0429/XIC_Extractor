"""Bounded Backfill-owned transactions."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .observation_repair import (
        ObservationRepairAuthorization,
        ObservationRepairAuthorizationState,
        ObservationRepairCandidate,
        ObservationRepairContractError,
        ObservationRepairEvidence,
        ObservationRepairMultipleEligibleAuthorization,
        ObservationRepairNoRepairTerminal,
        ObservationRepairNotRunAuthorization,
        ObservationRepairOutcome,
        ObservationRepairReceipt,
        ObservationRepairRepairAuthorization,
        ObservationRepairSubject,
    )

_OBSERVATION_REPAIR_EXPORTS = {
    "ObservationRepairAuthorization",
    "ObservationRepairAuthorizationState",
    "ObservationRepairCandidate",
    "ObservationRepairContractError",
    "ObservationRepairEvidence",
    "ObservationRepairMultipleEligibleAuthorization",
    "ObservationRepairNoRepairTerminal",
    "ObservationRepairNotRunAuthorization",
    "ObservationRepairOutcome",
    "ObservationRepairRepairAuthorization",
    "ObservationRepairReceipt",
    "ObservationRepairSubject",
}


def __getattr__(name: str) -> Any:
    if name not in _OBSERVATION_REPAIR_EXPORTS:
        raise AttributeError(name)
    from . import observation_repair

    return getattr(observation_repair, name)


__all__ = (
    "ObservationRepairAuthorization",
    "ObservationRepairAuthorizationState",
    "ObservationRepairCandidate",
    "ObservationRepairContractError",
    "ObservationRepairEvidence",
    "ObservationRepairMultipleEligibleAuthorization",
    "ObservationRepairNoRepairTerminal",
    "ObservationRepairNotRunAuthorization",
    "ObservationRepairOutcome",
    "ObservationRepairRepairAuthorization",
    "ObservationRepairReceipt",
    "ObservationRepairSubject",
)
