"""Shared scientific and application-result finalization contracts."""

from xic_extractor.alignment.result.application_result import (
    ApplicationResultContractError,
    ApplicationResultRequest,
    FinalizedApplicationResult,
    FinalizedTargetApplicationResult,
    FinalizedUntargetApplicationResult,
    TargetApplicationResultRequest,
    UntargetApplicationResultRequest,
    finalize_alignment_result,
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
    "ApplicationResultRequest",
    "FinalGroupCellObservation",
    "FinalizedApplicationResult",
    "FinalizedScientificAlignment",
    "FinalizedScientificAlignmentContractError",
    "FinalizedTargetApplicationResult",
    "FinalizedUntargetApplicationResult",
    "ObservedSearchSubject",
    "SearchedNoObservationSubject",
    "TargetApplicationResultRequest",
    "TerminalSearchObservation",
    "UntargetApplicationResultRequest",
    "finalize_alignment_result",
    "finalize_scientific_alignment",
)
