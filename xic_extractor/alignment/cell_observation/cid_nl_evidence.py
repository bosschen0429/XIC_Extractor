"""Interpret CID-NL evidence before shared cell lineage and value decisions."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.backfill.cid_nl_evidence import (
    cid_nl_allows_physical_localization,
    cid_nl_backfill_block_reason,
)
from xic_extractor.alignment.cell_observation.group_context import (
    CellObservationContractError,
)
from xic_extractor.alignment.cell_observation.method_evidence import (
    FinalCellEvidenceState,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    is_ms1_only_cid_nl_backfill_assessment,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.observations import PeakObservationRef


def resolve_cid_nl_recovered_evidence(
    assessment: CidNlMethodAssessmentRef,
    hypothesis: PeakHypothesis | None,
) -> FinalCellEvidenceState:
    """A group-admitted revisit retains its own completed assessment meaning."""
    if hypothesis is not None:
        return "direct_observation"
    if assessment.content.method_result == "HOLD":
        return "unresolved" if assessment.candidate_peaks else "searched_no_observation"
    if assessment.content.method_result == "REJECT":
        return "rejected_observation"
    return "unavailable"


def cid_nl_supported_observation_ids(
    assessment: CidNlMethodAssessmentRef,
) -> frozenset[str]:
    """The exact SUPPORT subset, never the whole candidate universe."""
    if assessment.content.method_result != "SUPPORT":
        raise CellObservationContractError("direct member assessment must be SUPPORT")
    return frozenset(
        relation.content.peak_observation.peak_observation_content_id
        for relation in assessment.individual_support_refs
    )


def resolve_cid_nl_cell_evidence(
    assessment: CidNlMethodAssessmentRef,
    observation: PeakObservationRef | None,
    hypothesis: PeakHypothesis | None,
) -> FinalCellEvidenceState:
    """Resolve method meaning; callers still close exact group/sample lineage."""
    technical_state = assessment.content.technical_state
    method_result = assessment.content.method_result
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


@dataclass(frozen=True, slots=True)
class CidNlCellEvidence:
    """Bind CID-NL once at record admission, without changing persisted bytes.

    Local interpretation remains lazy: no-input, outside-group and disabled
    coverage must not pretend that an unexecuted search was evaluated.
    """

    assessment: CidNlMethodAssessmentRef

    @property
    def supported_observation_ids(self) -> frozenset[str]:
        return cid_nl_supported_observation_ids(self.assessment)

    @property
    def backfill_block_reason(self) -> str | None:
        return cid_nl_backfill_block_reason(self.assessment)

    @property
    def allows_physical_localization(self) -> bool:
        return cid_nl_allows_physical_localization(self.assessment)

    def local_state(
        self, observation: PeakObservationRef | None, hypothesis: PeakHypothesis | None
    ) -> FinalCellEvidenceState:
        return resolve_cid_nl_cell_evidence(self.assessment, observation, hypothesis)

    def recovered_state(
        self, hypothesis: PeakHypothesis | None
    ) -> FinalCellEvidenceState:
        return resolve_cid_nl_recovered_evidence(self.assessment, hypothesis)
