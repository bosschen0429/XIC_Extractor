"""Interpret CID-NL evidence before shared cell lineage and value decisions."""

from __future__ import annotations

from xic_extractor.alignment.cell_observation.group_context import (
    CellObservationContractError,
)
from xic_extractor.alignment.cell_observation.method_evidence import (
    FinalCellEvidenceState,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
    is_ms1_only_cid_nl_backfill_assessment,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import MeasuredPeakHypothesis


def resolve_cid_nl_recovered_evidence(
    assessment: CidNlMethodAssessment,
    hypothesis: MeasuredPeakHypothesis | None,
) -> FinalCellEvidenceState:
    """A group-admitted revisit retains its own completed assessment meaning."""
    if hypothesis is not None:
        return "direct_observation"
    if assessment.method_result == "HOLD":
        return "unresolved" if assessment.candidate_peaks else "searched_no_observation"
    if assessment.method_result == "REJECT":
        return "rejected_observation"
    return "unavailable"


def cid_nl_supported_peaks(
    assessment: CidNlMethodAssessment,
) -> frozenset[PeakObservation]:
    """The exact SUPPORT subset, never the whole candidate universe."""
    if assessment.method_result != "SUPPORT":
        raise CellObservationContractError("direct member assessment must be SUPPORT")
    return frozenset(
        relation.peak_observation.physical_identity
        for relation in assessment.individual_support_refs
    )


def resolve_cid_nl_cell_evidence(
    assessment: CidNlMethodAssessment,
    observation: PeakObservation | None,
    hypothesis: MeasuredPeakHypothesis | None,
) -> FinalCellEvidenceState:
    """Resolve method meaning; callers still close exact group/sample lineage."""
    technical_state = assessment.technical_state
    method_result = assessment.method_result
    if technical_state in {"unsupported", "failed"}:
        if observation is not None or hypothesis is not None:
            raise CellObservationContractError(
                "unavailable lane cannot carry direct observation evidence"
            )
        return "unavailable"
    if technical_state == "not_evaluated":
        raise CellObservationContractError(
            "retained local owner contradicts a not-evaluated source cell"
        )
    if method_result == "SUPPORT":
        if observation is None or hypothesis is None:
            raise CellObservationContractError(
                "SUPPORT source cell lacks its exact observation and hypothesis"
            )
        return "direct_observation"
    if method_result == "REJECT":
        if hypothesis is not None:
            raise CellObservationContractError(
                "REJECT lane cannot carry a SUPPORT hypothesis"
            )
        return "rejected_observation"
    if method_result != "HOLD":
        raise CellObservationContractError(
            "evaluated source cell requires SUPPORT, HOLD, or REJECT"
        )
    if hypothesis is not None:
        raise CellObservationContractError(
            "HOLD lane cannot carry a SUPPORT hypothesis"
        )
    if not assessment.candidate_peaks and observation is None:
        return "searched_no_observation"
    if observation is not None and observation not in assessment.candidate_peaks:
        raise CellObservationContractError(
            "unresolved source evidence is outside the exact candidate universe"
        )
    if observation is not None and is_ms1_only_cid_nl_backfill_assessment(assessment):
        return "ms1_only_backfill_observation"
    return "unresolved"
