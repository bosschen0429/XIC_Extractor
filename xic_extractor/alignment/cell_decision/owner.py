"""Target and completed Feature x Sample value decisions and output packaging."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, fields
from typing import Literal, TypeAlias, cast

from xic_extractor.alignment.cell_observation.observations import (
    FinalGroupCellObservation,
)
from xic_extractor.alignment.cross_sample.peak_groups import CrossSampleGroupIdentity
from xic_extractor.alignment.primary_matrix_area import (
    primary_matrix_area_from_integration,
)
from xic_extractor.alignment.projection.target.pre_cell import (
    TargetCellSubject,
    TargetPreCellProjection,
)
from xic_extractor.alignment.projection.untarget.identity import (
    GroupCellSubject,
)
from xic_extractor.alignment.result import (
    FinalizedScientificAlignment,
    ObservedSearchSubject,
    SearchedNoObservationSubject,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult


class CellDecisionContractError(ValueError):
    """Raised when exact owner evidence cannot produce one closed decision."""


NoWriteState: TypeAlias = Literal[
    "not_searched",
    "searched_no_observation",
    "unresolved",
    "rejected_observation",
    "not_quantifiable",
    "unavailable",
]
CellSubject: TypeAlias = TargetCellSubject | GroupCellSubject
CellEvidence: TypeAlias = (
    ObservedSearchSubject | SearchedNoObservationSubject | FinalGroupCellObservation
)

_NO_WRITE_STATES = frozenset(
    {
        "not_searched",
        "searched_no_observation",
        "unresolved",
        "rejected_observation",
        "not_quantifiable",
        "unavailable",
    }
)
_GROUP_VALUE_STATES = frozenset({"direct_observation"})
_GROUP_TERMINAL_COMPANION_STATES = frozenset(
    {"not_searched", "searched_no_observation", "unavailable"}
)


@dataclass(frozen=True, slots=True)
class NumericCellDecision:
    """A finite positive value selected by the existing Matrix-area helper."""

    value: float
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", float(self.value))

    @property
    def variant(self) -> Literal["numeric"]:
        return "numeric"


@dataclass(frozen=True, slots=True)
class NoWriteCellDecision:
    """One closed reason that forbids a numeric write."""

    variant: NoWriteState


CellDecisionPayload: TypeAlias = NumericCellDecision | NoWriteCellDecision


@dataclass(frozen=True, slots=True)
class ProductionCellDecision:
    """One owner-sealed decision bound to one exact subject and evidence closure."""

    subject: CellSubject
    evidence: CellEvidence
    payload: CellDecisionPayload
    # Retain the chosen observation even when its area is not quantifiable.
    selected_integration: IntegrationResult | None

    @property
    def variant(self) -> Literal["numeric"] | NoWriteState:
        return self.payload.variant

    @property
    def value(self) -> float | None:
        if type(self.payload) is NumericCellDecision:
            return self.payload.value
        return None

    @property
    def source(self) -> str:
        if type(self.payload) is NumericCellDecision:
            return self.payload.source
        return ""


def decide_production_cell(
    subject: TargetCellSubject,
    *,
    target_projection: TargetPreCellProjection | None = None,
) -> ProductionCellDecision:
    """Decide one explicit subject through its complete owner population."""

    if type(subject) is TargetCellSubject:
        if target_projection is None:
            raise CellDecisionContractError(
                "Target decision requires only its exact Target projection"
            )
        target_owner_subjects = target_projection.cell_subjects
        if not any(subject is item for item in target_owner_subjects):
            raise CellDecisionContractError(
                "explicit Target subject is absent from its owner population"
            )
        return _decide_sealed_subject(
            subject,
            evidence=subject.terminal_subject,
        )
    else:
        raise CellDecisionContractError(
            "explicit subject decision requires an exact TargetCellSubject"
        )


def decide_target_cell_population(
    projection: TargetPreCellProjection,
    *,
    subjects: Sequence[TargetCellSubject] | None = None,
) -> tuple[ProductionCellDecision, ...]:
    """Decide the complete Target subject population in canonical identity order."""

    owner_subjects = projection.cell_subjects
    requested = owner_subjects if subjects is None else tuple(subjects)
    ordered = cast(
        tuple[TargetCellSubject, ...],
        _bind_complete_subject_population(
            requested,
            owner_subjects=owner_subjects,
            expected_type=TargetCellSubject,
            label="Target",
        ),
    )
    decisions = tuple(
        _decide_sealed_subject(subject, evidence=subject.terminal_subject)
        for subject in ordered
    )
    return tuple(sorted(decisions, key=_decision_sort_key))


def decide_group_cell_population(
    finalized_alignment: FinalizedScientificAlignment,
) -> dict[
    tuple[CrossSampleGroupIdentity, str],
    tuple[CellDecisionPayload, IntegrationResult | None],
]:
    """Complete values/blank reasons keyed by the authoritative cell observation.

    Scientific completion already closed the group/sample population and its
    provenance. Row identities are not inputs to this decision. The existing
    payload and selected integration pass unchanged to output packaging.
    """

    member_samples = {
        group.group_hypothesis_id: {
            member.sample_key for member in group.member_subjects
        }
        for group in finalized_alignment.final_population
    }
    return {
        observation.key: _decide_group(
            observation,
            is_member=observation.sample_key
            in member_samples[observation.group_hypothesis_id],
        )
        for observation in finalized_alignment.cell_observations
    }


def _decide_sealed_subject(
    subject: CellSubject,
    *,
    evidence: CellEvidence,
) -> ProductionCellDecision:
    if type(subject) is TargetCellSubject:
        if evidence is not subject.terminal_subject:
            raise CellDecisionContractError(
                "Target decision crossed its exact terminal subject"
            )
        payload, integration = _decide_target(subject)
    elif type(subject) is GroupCellSubject:
        if type(evidence) is not FinalGroupCellObservation:
            raise CellDecisionContractError(
                "Group decision requires an exact final cell observation"
            )
        payload, integration = _decide_group(
            evidence,
            is_member=any(
                member.sample_key == evidence.sample_key
                for member in evidence.group.member_subjects
            ),
        )
    else:
        raise CellDecisionContractError(
            "cell decision requires an exact TargetCellSubject or GroupCellSubject"
        )
    return ProductionCellDecision(
        subject=subject,
        evidence=evidence,
        payload=payload,
        selected_integration=integration,
    )


def _decide_target(
    subject: TargetCellSubject,
) -> tuple[CellDecisionPayload, IntegrationResult | None]:
    terminal = subject.terminal_subject
    sample_id = subject.canonical_sample.identity.sample_key
    if terminal.sample_key != sample_id:
        raise CellDecisionContractError(
            "Target terminal crossed the exact canonical sample"
        )
    if type(terminal) is SearchedNoObservationSubject:
        return NoWriteCellDecision("searched_no_observation"), None
    if type(terminal) is not ObservedSearchSubject:
        raise CellDecisionContractError(
            "Target terminal uses an unsupported or partial variant"
        )
    hypothesis = terminal.hypothesis
    if not hypothesis.is_owner_backed:
        raise CellDecisionContractError(
            "Target observed terminal requires an owner-backed PeakHypothesis"
        )
    integration = hypothesis.integration
    if any(
        observation.physical_evaluation is not None
        and observation.physical_evaluation.quantitation_failure
        for observation in hypothesis.peak_observation_refs
    ):
        return NoWriteCellDecision("not_quantifiable"), integration
    return _payload_from_integration(integration), integration


def _decide_group(
    observation: FinalGroupCellObservation,
    *,
    is_member: bool,
) -> tuple[CellDecisionPayload, IntegrationResult | None]:
    state = observation.evidence_state
    states = observation.evidence_states
    value_state_count = sum(item in _GROUP_VALUE_STATES for item in states)
    if is_member:
        allowed_states = _GROUP_VALUE_STATES | _GROUP_TERMINAL_COMPANION_STATES
        if value_state_count == 1 and all(item in allowed_states for item in states):
            integrations = observation.integrations
            if len(integrations) != 1:
                raise CellDecisionContractError(
                    "Group numeric decision requires exactly one integration"
                )
            integration = integrations[0]
            return _payload_from_integration(integration), integration
        if value_state_count > 1:
            raise CellDecisionContractError(
                "Group closure contains competing observation evidence"
            )
        raise CellDecisionContractError(
            "Group member requires direct or repaired observation evidence"
        )
    if value_state_count:
        raise CellDecisionContractError(
            "Group nonmember cannot use observation evidence"
        )
    if "ms1_only_backfill_observation" in states:
        # A nonmember value never counts as a detection. Any competing lane
        # leaves the single MS1 peak as unresolved.
        integrations = observation.integrations
        if state == "ms1_only_backfill_observation" and len(integrations) == 1:
            integration = integrations[0]
            return _payload_from_integration(integration), integration
        return NoWriteCellDecision("unresolved"), None
    if state == "mixed":
        raise CellDecisionContractError(
            "Group closure contains unsupported mixed terminal states"
        )
    if state not in _NO_WRITE_STATES - {"not_quantifiable"}:
        raise CellDecisionContractError(
            "Group closure uses an unsupported or partial evidence state"
        )
    return NoWriteCellDecision(cast(NoWriteState, state)), None


def _payload_from_integration(integration: IntegrationResult) -> CellDecisionPayload:
    if type(integration) is not IntegrationResult:
        raise CellDecisionContractError(
            "numeric decision requires an exact IntegrationResult"
        )
    _reject_non_finite_integration(integration)
    selected = primary_matrix_area_from_integration(integration)
    if selected.value is None:
        return NoWriteCellDecision("not_quantifiable")
    return NumericCellDecision(value=selected.value, source=selected.source)


def _reject_non_finite_integration(integration: IntegrationResult) -> None:
    for item in fields(integration):
        value = getattr(integration, item.name)
        if isinstance(value, float) and not math.isfinite(value):
            raise CellDecisionContractError(
                f"IntegrationResult.{item.name} must be finite"
            )


def _bind_complete_subject_population(
    subjects: Sequence[CellSubject],
    *,
    owner_subjects: tuple[CellSubject, ...],
    expected_type: type[TargetCellSubject] | type[GroupCellSubject],
    label: str,
) -> tuple[CellSubject, ...]:
    if isinstance(subjects, (str, bytes)):
        raise CellDecisionContractError(f"{label} subjects must be a finite sequence")
    requested = tuple(subjects)
    if any(type(subject) is not expected_type for subject in requested):
        raise CellDecisionContractError(
            f"{label} population contains a non-owner subject"
        )
    requested_ids = tuple(id(subject) for subject in requested)
    owner_ids = tuple(id(subject) for subject in owner_subjects)
    if len(requested_ids) != len(set(requested_ids)):
        raise CellDecisionContractError(
            f"{label} population contains a duplicate subject"
        )
    if set(requested_ids) != set(owner_ids):
        raise CellDecisionContractError(
            f"{label} population is missing, extra, or substituted"
        )
    return requested


def _decision_sort_key(decision: ProductionCellDecision) -> tuple[str, ...]:
    subject = decision.subject
    if type(subject) is TargetCellSubject:
        return (
            "target",
            subject.target.lookup_label,
            subject.canonical_sample.identity.sample_key,
        )
    if type(subject) is GroupCellSubject:
        return (
            "untarget",
            subject.group_hypothesis_id,
            subject.canonical_sample.identity.sample_key,
        )
    raise CellDecisionContractError("decision contains an unsupported subject")
