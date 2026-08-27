"""Shared scientific-result finalization contracts."""

from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    FinalizedScientificAlignmentContractError,
    ObservedSearchSubject,
    SearchedNoObservationSubject,
    TerminalSearchObservation,
    finalize_scientific_alignment,
)

__all__ = (
    "FinalizedScientificAlignment",
    "FinalizedScientificAlignmentContractError",
    "ObservedSearchSubject",
    "SearchedNoObservationSubject",
    "TerminalSearchObservation",
    "finalize_scientific_alignment",
)
