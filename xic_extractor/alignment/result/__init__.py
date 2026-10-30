"""Shared scientific and application-result finalization contracts."""

from xic_extractor.alignment.result.application_result import (
    ApplicationResultContractError,
    FinalizedApplicationResult,
    FinalizedTargetApplicationResult,
    FinalizedUntargetApplicationResult,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalGroupCellObservation,
    FinalizedScientificAlignment,
    FinalizedScientificAlignmentContractError,
    ObservedSearchSubject,
    SearchedNoObservationSubject,
    TerminalSearchObservation,
    finalize_scientific_alignment,
)

__all__ = (
    "ApplicationResultContractError",
    "FinalGroupCellObservation",
    "FinalizedApplicationResult",
    "FinalizedScientificAlignment",
    "FinalizedScientificAlignmentContractError",
    "FinalizedTargetApplicationResult",
    "FinalizedUntargetApplicationResult",
    "ObservedSearchSubject",
    "SearchedNoObservationSubject",
    "FinalizedTargetApplicationResult",
    "TerminalSearchObservation",
    "FinalizedUntargetApplicationResult",
    "finalize_scientific_alignment",
)
