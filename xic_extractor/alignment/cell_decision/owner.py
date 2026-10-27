"""Target and completed Feature x Sample value decisions and output packaging."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from typing import Literal, TypeAlias, cast

from xic_extractor.alignment.cell_observation.observations import (
    FinalGroupCellObservation,
)
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
from xic_extractor.processing_identity import (
    mint_typed_identity,
)


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

_DECISION_PREFIX = "xic.production-cell-decision:"
_DECISION_SCHEMA_VERSION = "production_cell_decision"
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

    decision_id: str
    subject: CellSubject
    evidence: CellEvidence
    payload: CellDecisionPayload
    # The factory derives the basis and ID from these exact inputs.
    basis_bytes: bytes
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
) -> dict[str, tuple[CellDecisionPayload, IntegrationResult | None]]:
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
        observation.cell_observation_id: _decide_group(
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
    return _mint_decision(
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


def _mint_decision(
    *,
    subject: CellSubject,
    evidence: CellEvidence,
    payload: CellDecisionPayload,
    selected_integration: IntegrationResult | None,
) -> ProductionCellDecision:
    identity, basis = mint_typed_identity(
        _DECISION_PREFIX,
        _decision_basis(subject=subject, evidence=evidence, payload=payload),
    )
    return ProductionCellDecision(
        decision_id=identity,
        subject=subject,
        evidence=evidence,
        payload=payload,
        basis_bytes=basis,
        selected_integration=selected_integration,
    )


def _decision_basis(
    *,
    subject: CellSubject,
    evidence: CellEvidence,
    payload: CellDecisionPayload,
) -> dict[str, object]:
    if type(subject) is TargetCellSubject:
        evidence_identity: dict[str, object]
        subject_kind = "target"
        subject_identity: dict[str, object] = {
            "target": asdict(subject.target.definition.target),
            "target_label": subject.target.definition.target_key.target_label,
            "row_binding_variant": subject.row_binding.variant,
            "eligible_group_ids": tuple(
                group.group_hypothesis_id
                for group in subject.row_binding.eligible_groups
            ),
        }
        if type(evidence) is ObservedSearchSubject:
            evidence_identity = {
                "variant": evidence.variant,
                "candidate_id": evidence.candidate_id,
                "trace_group_id": evidence.trace_group_id,
                "hypothesis_id": evidence.hypothesis.hypothesis_id,
                "integration": _integration_identity(evidence.hypothesis.integration),
            }
        elif type(evidence) is SearchedNoObservationSubject:
            evidence_identity = {
                "variant": evidence.variant,
                "candidate_id": evidence.candidate_id,
                "trace_group_id": evidence.trace_group_id,
                "detection_status": evidence.detection_status,
            }
        else:
            raise CellDecisionContractError(
                "Target decision basis requires its exact terminal subject"
            )
        request_id = subject.processing_request.processing_request_id
        sample_id = subject.canonical_sample.identity.sample_key
    elif type(subject) is GroupCellSubject:
        if type(evidence) is not FinalGroupCellObservation:
            raise CellDecisionContractError(
                "Group decision basis requires its exact final cell observation"
            )
        subject_kind = "group"
        subject_identity = {
            "group_cell_subject_id": subject.subject_id,
            "untarget_row_key_id": subject.row_key.untarget_row_key_id,
            "group_hypothesis_id": subject.group_hypothesis_id,
            "member_subject_id": (
                None
                if subject.member_subject is None
                else subject.member_subject.member_subject_id
            ),
        }
        evidence_identity = {
            "cell_observation_id": evidence.cell_observation_id,
            "evidence_states": evidence.evidence_states,
            "position_keys": evidence.position_keys,
            "source_cell_artifact_ids": evidence.source_cell_artifact_ids,
            "integrations": tuple(
                _integration_identity(item) for item in evidence.integrations
            ),
        }
        request_id = subject.processing_request_id
        sample_id = subject.canonical_sample.identity.sample_key
    else:
        raise CellDecisionContractError(
            "decision basis requires an exact owner subject"
        )
    payload_identity: dict[str, object] = {"variant": payload.variant}
    if type(payload) is NumericCellDecision:
        payload_identity.update(value=payload.value, source=payload.source)
    return {
        "schema_version": _DECISION_SCHEMA_VERSION,
        "subject_kind": subject_kind,
        "processing_request_id": request_id,
        "sample_key": sample_id,
        "subject": subject_identity,
        "evidence": evidence_identity,
        "payload": payload_identity,
    }


def _integration_identity(integration: IntegrationResult) -> dict[str, object]:
    if type(integration) is not IntegrationResult:
        raise CellDecisionContractError(
            "decision basis requires an exact IntegrationResult"
        )
    # Advisory measurement quality does not mint a different quantitative decision.
    return {
        item.name: getattr(integration, item.name)
        for item in fields(integration)
        if item.name != "signal_quality"
    }


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
