"""Bounded Backfill-owned transactions."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .observation_repair import (
        ObservationRepairContractError,
        ObservationRepairEvidence,
        ObservationRepairOutcome,
        ObservationRepairReceipt,
        ObservationRepairSubject,
        close_observation_repair,
        mint_observation_repair_subject,
    )

_OBSERVATION_REPAIR_EXPORTS = {
    "ObservationRepairContractError",
    "ObservationRepairEvidence",
    "ObservationRepairOutcome",
    "ObservationRepairReceipt",
    "ObservationRepairSubject",
    "close_observation_repair",
    "mint_observation_repair_subject",
}


def __getattr__(name: str) -> Any:
    if name not in _OBSERVATION_REPAIR_EXPORTS:
        raise AttributeError(name)
    from . import observation_repair

    return getattr(observation_repair, name)

__all__ = (
    "ObservationRepairContractError",
    "ObservationRepairEvidence",
    "ObservationRepairOutcome",
    "ObservationRepairReceipt",
    "ObservationRepairSubject",
    "close_observation_repair",
    "mint_observation_repair_subject",
)
