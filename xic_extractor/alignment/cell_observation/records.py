"""CID-NL cell predecessor records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from xic_extractor.alignment.cell_observation.cid_nl_evidence import (
    cid_nl_supported_peaks,
    resolve_cid_nl_cell_evidence,
    resolve_cid_nl_recovered_evidence,
)
from xic_extractor.alignment.cell_observation.group_context import (
    CellObservationContractError,
    CidNlCellGroupContext,
    TerminalObservationVariant,
)
from xic_extractor.alignment.cell_observation.method_evidence import (
    FinalCellEvidenceState,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupMemberSubject,
)
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
    CidNlMethodResult,
    CidNlTechnicalState,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.observations import PeakObservation
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

    Equality uses the cell's orthogonal axes only; the group context,
    assessment, peak and hypothesis it closes over travel with it.  It carries
    no value, writer, fill, or product authority.
    """

    sample_key: str
    group_hypothesis_id: str
    group_owner_ids: tuple[str, ...]
    group_method_subject_id: str
    group_target_label: str | None
    technical_state: CidNlTechnicalState
    method_result: CidNlMethodResult | None
    physical_peak_key: str | None
    peak_hypothesis_id: str | None
    reason_codes: tuple[str, ...]
    group_context: CidNlCellGroupContext = field(compare=False)
    method_assessment: CidNlMethodAssessment = field(compare=False)
    peak_observation: PeakObservation | None = field(compare=False)
    peak_hypothesis: PeakHypothesis | None = field(compare=False)
    outside_group: OutsideGroupCellRelation | None = None
    unassigned_support: bool = False
    outside_group_context: CidNlCellGroupContext | None = field(
        default=None, compare=False
    )

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


def _direct_member_integration(
    *,
    member: CrossSampleGroupMemberSubject | None,
    canonical_sample: SampleMetadata,
    terminal_variant: TerminalObservationVariant,
    terminal_hypothesis: PeakHypothesis | None,
    method_assessment: CidNlMethodAssessment | None,
    peak_observation: PeakObservation | None,
    peak_hypothesis: PeakHypothesis | None,
) -> IntegrationResult | None:
    if member is None:
        return None
    sample_key = canonical_sample.sample_name
    direct_hypothesis = peak_hypothesis
    if direct_hypothesis is None and (
        terminal_variant == "observed"
        and terminal_hypothesis is not None
        and member.subject_kind == "peak_hypothesis"
        and member.subject_id == terminal_hypothesis.hypothesis_id
    ):
        direct_hypothesis = terminal_hypothesis
    if direct_hypothesis is not None:
        if type(direct_hypothesis) is not PeakHypothesis or not (
            direct_hypothesis.is_owner_backed
        ):
            raise CellObservationContractError(
                "direct member hypothesis is not owner-backed"
            )
        if (
            member.subject_kind != "peak_hypothesis"
            or member.subject_id != direct_hypothesis.hypothesis_id
            or any(
                observation.sample_key != sample_key
                for observation in direct_hypothesis.peak_observation_refs
            )
        ):
            raise CellObservationContractError(
                "direct hypothesis does not mint the exact final member"
            )
        if method_assessment is not None:
            if not set(member.physical_peaks).issubset(
                cid_nl_supported_peaks(method_assessment)
            ):
                raise CellObservationContractError(
                    "direct member hypothesis is outside the exact SUPPORT subset"
                )
        return direct_hypothesis.integration
    if peak_observation is not None:
        # Final members bind owner-backed hypotheses; an observation alone
        # cannot mint the exact member.
        raise CellObservationContractError(
            "direct observation does not mint the exact final member"
        )
    return None


def _outside_group_relation(
    *,
    group_context: CidNlCellGroupContext,
    method_assessment: CidNlMethodAssessment,
    outside_group_context: CidNlCellGroupContext | None,
) -> OutsideGroupCellRelation | None:
    if outside_group_context is None:
        return None
    if type(outside_group_context) is not CidNlCellGroupContext:
        raise CellObservationContractError(
            "outside-group context has the wrong owner type"
        )
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
        and support.sample.sample_name == method_assessment.sample_key
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
    target: ResolvedTargetDefinition | None = None,
    group_context: CidNlCellGroupContext,
    method_assessment: CidNlMethodAssessment,
    peak_observation: PeakObservation | None = None,
    peak_hypothesis: PeakHypothesis | None = None,
    outside_group_context: CidNlCellGroupContext | None = None,
    unassigned_support: bool = False,
) -> CellObservation:
    """Attach a completed assessment to an existing group without creating it."""

    group = group_context.group
    method_result = method_assessment.method_result
    if method_assessment.sample_key != (sample.sample_name):
        raise CellObservationContractError("assessment references a different sample")
    support_binding = any(
        support.assessment == method_assessment
        and support.sample.sample_name == sample.sample_name
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
        sample_key=sample.sample_name,
        group_hypothesis_id=group.group_hypothesis_id,
        group_owner_ids=group_context.sorted_owner_ids,
        group_method_subject_id=group_context.method_subject_id,
        group_target_label=group_context.target_label,
        technical_state=method_assessment.technical_state,
        method_result=method_result,
        physical_peak_key=(
            None if peak_observation is None else peak_observation.physical_peak_key
        ),
        peak_hypothesis_id=(
            None if peak_hypothesis is None else peak_hypothesis.hypothesis_id
        ),
        reason_codes=method_assessment.reason_codes,
        group_context=group_context,
        method_assessment=method_assessment,
        peak_observation=peak_observation,
        peak_hypothesis=peak_hypothesis,
        outside_group=outside_group,
        unassigned_support=unassigned_support,
        outside_group_context=outside_group_context,
    )
