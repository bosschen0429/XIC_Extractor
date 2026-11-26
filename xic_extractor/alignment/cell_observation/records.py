"""CID-NL cell predecessor records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from xic_extractor.alignment.cell_observation.cid_nl_evidence import (
    resolve_cid_nl_cell_evidence,
    resolve_cid_nl_recovered_evidence,
)
from xic_extractor.alignment.cell_observation.group_context import (
    CellObservationContractError,
    CidNlCellGroupContext,
)
from xic_extractor.alignment.cell_observation.method_evidence import (
    FinalCellEvidenceState,
)
from xic_extractor.configuration.models import Target
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
    CidNlMethodResult,
    CidNlTechnicalState,
)
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.peak_detection.peak_hypothesis_identity import MeasuredPeakHypothesis
from xic_extractor.sample_metadata import SampleMetadata


@dataclass(frozen=True, slots=True)
class OutsideGroupCellRelation:
    """Reference support in one exact sibling without a group-local observation."""

    supporting_group_hypothesis_id: str
    supporting_peak_hypothesis_id: str
    relation: Literal["outside_group"] = "outside_group"


@dataclass(frozen=True, slots=True)
class CellObservation:
    """One exact cell closure under an already-existing provisional group.

    The group context, assessment, peak and hypothesis own their facts. It carries
    no value, writer, fill, or product authority.
    """

    group_context: CidNlCellGroupContext
    method_assessment: CidNlMethodAssessment
    peak_observation: PeakObservation | None
    peak_hypothesis: MeasuredPeakHypothesis | None
    outside_group: OutsideGroupCellRelation | None = None
    unassigned_support: bool = False
    outside_group_context: CidNlCellGroupContext | None = field(
        default=None, compare=False
    )

    @property
    def raw_stem(self) -> str:
        return self.method_assessment.raw_stem

    @property
    def group_hypothesis_id(self) -> str:
        return self.group_context.group.group_hypothesis_id

    @property
    def technical_state(self) -> CidNlTechnicalState:
        return self.method_assessment.technical_state

    @property
    def method_result(self) -> CidNlMethodResult | None:
        return self.method_assessment.method_result

    @property
    def physical_peak_key(self) -> str | None:
        return (
            None
            if self.peak_observation is None
            else self.peak_observation.physical_peak_key
        )

    @property
    def peak_hypothesis_id(self) -> str | None:
        return (
            None if self.peak_hypothesis is None else self.peak_hypothesis.hypothesis_id
        )

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return self.method_assessment.reason_codes

    def evidence_state(self) -> FinalCellEvidenceState:
        """The cell's own method meaning; lineage is closed by its caller."""
        return resolve_cid_nl_cell_evidence(
            self.method_assessment, self.peak_observation, self.peak_hypothesis
        )

    def recovered_evidence_state(self) -> FinalCellEvidenceState:
        """A group-admitted revisit keeps its own completed assessment meaning."""
        return resolve_cid_nl_recovered_evidence(
            self.method_assessment, self.peak_hypothesis
        )


def _outside_group_relation(
    *,
    group_context: CidNlCellGroupContext,
    method_assessment: CidNlMethodAssessment,
    outside_group_context: CidNlCellGroupContext | None,
) -> OutsideGroupCellRelation | None:
    if outside_group_context is None:
        return None
    if (
        method_assessment.method_result != "SUPPORT"
        or outside_group_context.config != group_context.config
        or outside_group_context.group.group_hypothesis_id
        == group_context.group.group_hypothesis_id
    ):
        raise CellObservationContractError(
            "outside-group context crossed its sibling owner boundary"
        )
    supports = tuple(
        support
        for support in outside_group_context.supports
        if support.assessment is method_assessment
        and support.sample.raw_stem == method_assessment.raw_stem
    )
    if len(supports) != 1:
        raise CellObservationContractError(
            "outside-group context lost its exact sample SUPPORT"
        )
    support = supports[0]
    if support.hypothesis.hypothesis_id in group_context.group.owner_ids:
        raise CellObservationContractError(
            "outside-group hypothesis belongs to the local group"
        )
    return OutsideGroupCellRelation(
        supporting_group_hypothesis_id=outside_group_context.group.group_hypothesis_id,
        supporting_peak_hypothesis_id=support.hypothesis.hypothesis_id,
    )


def mint_cell_observation(
    *,
    sample: SampleMetadata,
    target: Target | None = None,
    group_context: CidNlCellGroupContext,
    method_assessment: CidNlMethodAssessment,
    peak_observation: PeakObservation | None = None,
    peak_hypothesis: MeasuredPeakHypothesis | None = None,
    outside_group_context: CidNlCellGroupContext | None = None,
    unassigned_support: bool = False,
) -> CellObservation:
    """Attach a completed assessment to an existing group without creating it."""

    if method_assessment.raw_stem != (sample.raw_stem):
        raise CellObservationContractError("assessment references a different sample")
    support_binding = any(
        support.assessment == method_assessment
        and support.sample.raw_stem == sample.raw_stem
        for support in group_context.supports
    )
    if method_assessment.method_subject != group_context.method_subject and (
        not support_binding
    ):
        raise CellObservationContractError(
            "assessment differs from the group recovery subject"
        )
    if peak_hypothesis is not None and not any(
        support.hypothesis.hypothesis_id == peak_hypothesis.hypothesis_id
        and support.assessment == method_assessment
        for support in group_context.supports
    ):
        raise CellObservationContractError(
            "assessment relations do not bind the group SUPPORT hypothesis"
        )
    outside_group = _outside_group_relation(
        group_context=group_context,
        method_assessment=method_assessment,
        outside_group_context=outside_group_context,
    )
    return CellObservation(
        group_context=group_context,
        method_assessment=method_assessment,
        peak_observation=peak_observation,
        peak_hypothesis=peak_hypothesis,
        outside_group=outside_group,
        unassigned_support=unassigned_support,
        outside_group_context=outside_group_context,
    )
