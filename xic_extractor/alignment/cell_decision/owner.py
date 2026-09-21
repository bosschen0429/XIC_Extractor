"""Exact Target/Group subject to production-cell decision owner."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field, fields
from typing import Literal, TypeAlias, cast

from xic_extractor.alignment.cell_observations import (
    CellObservationContractError,
    FinalGroupCellObservation,
    validate_final_group_cell_observation_population,
)
from xic_extractor.alignment.cross_sample.association import (
    CrossSampleAssociationLineageHandoff,
)
from xic_extractor.alignment.primary_matrix_area import (
    primary_matrix_area_from_integration,
)
from xic_extractor.alignment.projection.target.pre_cell import (
    TargetCellSubject,
    TargetPreCellProjection,
    TargetRowBinding,
    validate_target_row_binding,
)
from xic_extractor.alignment.projection.untarget.pre_cell import (
    GroupCellSubject,
    UntargetPreCellProjection,
    project_untarget_pre_cell,
)
from xic_extractor.alignment.result import (
    FinalizedScientificAlignment,
    ObservedSearchSubject,
    SearchedNoObservationSubject,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    mint_typed_identity,
    typed_identity_digest,
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
_DECISION_MINT_TOKEN = object()
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
_GROUP_VALUE_STATES = frozenset({"direct_observation", "repaired_observation"})
_GROUP_TERMINAL_COMPANION_STATES = frozenset(
    {"not_searched", "searched_no_observation", "unavailable"}
)


@dataclass(frozen=True, slots=True)
class NumericCellDecision:
    """A finite positive value selected by the existing Matrix-area helper."""

    value: float
    source: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, (int, float))
            or not math.isfinite(self.value)
            or self.value <= 0
        ):
            raise CellDecisionContractError(
                "numeric cell decision requires a finite positive value"
            )
        if not isinstance(self.source, str) or not self.source.strip():
            raise CellDecisionContractError(
                "numeric cell decision requires a named area source"
            )
        object.__setattr__(self, "value", float(self.value))

    @property
    def variant(self) -> Literal["numeric"]:
        return "numeric"


@dataclass(frozen=True, slots=True)
class NoWriteCellDecision:
    """One closed reason that forbids a numeric write."""

    variant: NoWriteState

    def __post_init__(self) -> None:
        if self.variant not in _NO_WRITE_STATES:
            raise CellDecisionContractError(
                "no-write cell decision uses an unsupported closed variant"
            )


CellDecisionPayload: TypeAlias = NumericCellDecision | NoWriteCellDecision


@dataclass(frozen=True, slots=True)
class ProductionCellDecision:
    """One owner-sealed decision bound to one exact subject and evidence closure."""

    decision_id: str
    subject: CellSubject
    evidence: CellEvidence
    payload: CellDecisionPayload
    basis_bytes: bytes
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _DECISION_MINT_TOKEN:
            raise CellDecisionContractError(
                "production cell decision must be minted by cell_decision"
            )

        # The sealed factory derives the basis and ID from these exact inputs.
        # Explicit validation remains available at an external trust boundary.

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
    subject: CellSubject,
    *,
    target_projection: TargetPreCellProjection | None = None,
    group_handoff: CrossSampleAssociationLineageHandoff | None = None,
    group_observations: Sequence[FinalGroupCellObservation] | None = None,
) -> ProductionCellDecision:
    """Decide one explicit subject through its complete owner population."""

    if type(subject) is TargetCellSubject:
        if (
            target_projection is None
            or group_handoff is not None
            or group_observations is not None
        ):
            raise CellDecisionContractError(
                "Target decision requires only its exact Target projection"
            )
        target_owner_subjects = _validate_target_projection(target_projection)
        if not any(subject is item for item in target_owner_subjects):
            raise CellDecisionContractError(
                "explicit Target subject is absent from its owner population"
            )
        return _decide_sealed_subject(
            subject,
            evidence=subject.terminal_subject,
        )
    elif type(subject) is GroupCellSubject:
        if target_projection is not None or group_handoff is None:
            raise CellDecisionContractError(
                "Group decision requires only its exact #305 handoff population"
            )
        if group_observations is None:
            raise CellDecisionContractError(
                "Group decision requires the complete #305 observation population"
            )
        try:
            owner_observations = validate_final_group_cell_observation_population(
                handoff=group_handoff,
                cell_observations=group_observations,
            )
        except (CellObservationContractError, TypeError, ValueError) as exc:
            raise CellDecisionContractError(str(exc)) from exc
        observation_by_key = {
            _group_observation_key(item): item for item in owner_observations
        }
        key = _group_subject_key(subject)
        evidence = observation_by_key.get(key)
        if evidence is None:
            raise CellDecisionContractError(
                "explicit Group subject is absent from the #305 owner population"
            )
        _validate_group_binding(subject, evidence)
        return _decide_sealed_subject(
            subject,
            evidence=evidence,
        )
    else:
        raise CellDecisionContractError(
            "cell decision requires an exact TargetCellSubject or GroupCellSubject"
        )


def decide_target_cell_population(
    projection: TargetPreCellProjection,
    *,
    subjects: Sequence[TargetCellSubject] | None = None,
) -> tuple[ProductionCellDecision, ...]:
    """Decide the complete Target subject population in canonical identity order."""

    owner_subjects = _validate_target_projection(projection)
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
    projection: UntargetPreCellProjection,
    finalized_alignment: FinalizedScientificAlignment,
    *,
    subjects: Sequence[GroupCellSubject] | None = None,
    observations: Sequence[FinalGroupCellObservation] | None = None,
) -> tuple[ProductionCellDecision, ...]:
    """Decide the complete Group x sample population from the exact #305 closure."""

    ordered_subjects, observation_by_key = _validate_group_population_inputs(
        projection,
        finalized_alignment,
        subjects=subjects,
        observations=observations,
    )
    return _decide_validated_group_cell_population(
        ordered_subjects,
        observation_by_key,
    )


def _decide_fresh_group_cell_population(
    projection: UntargetPreCellProjection,
    finalized_alignment: FinalizedScientificAlignment,
) -> tuple[ProductionCellDecision, ...]:
    """Decide owners completed immediately upstream by the shared workflow."""

    ordered_subjects, observation_by_key = _validate_group_population_inputs(
        projection,
        finalized_alignment,
        fresh=True,
    )
    return _decide_validated_group_cell_population(
        ordered_subjects,
        observation_by_key,
    )


def _decide_validated_group_cell_population(
    ordered_subjects: tuple[GroupCellSubject, ...],
    observation_by_key: dict[tuple[str, str], FinalGroupCellObservation],
) -> tuple[ProductionCellDecision, ...]:
    decisions = tuple(
        _decide_sealed_subject(
            subject,
            evidence=observation_by_key[_group_subject_key(subject)],
        )
        for subject in ordered_subjects
    )
    return tuple(sorted(decisions, key=_decision_sort_key))


def _validate_group_population_inputs(
    projection: UntargetPreCellProjection,
    finalized_alignment: FinalizedScientificAlignment,
    *,
    subjects: Sequence[GroupCellSubject] | None = None,
    observations: Sequence[FinalGroupCellObservation] | None = None,
    fresh: bool = False,
) -> tuple[
    tuple[GroupCellSubject, ...],
    dict[tuple[str, str], FinalGroupCellObservation],
]:
    if type(projection) is not UntargetPreCellProjection:
        raise CellDecisionContractError(
            "Group population requires an exact UntargetPreCellProjection"
        )
    if type(finalized_alignment) is not FinalizedScientificAlignment:
        raise CellDecisionContractError(
            "Group population requires an exact FinalizedScientificAlignment"
        )
    if projection.finalized_alignment is not finalized_alignment:
        raise CellDecisionContractError(
            "Group projection crossed its exact finalized alignment"
        )
    if fresh:
        if projection.processing_request is not finalized_alignment.processing_request:
            raise CellDecisionContractError(
                "Group projection crossed its exact processing request"
            )
        if (
            type(projection.cell_subjects) is not tuple
            or type(finalized_alignment.cell_observations) is not tuple
        ):
            raise CellDecisionContractError(
                "fresh Group populations require exact immutable tuples"
            )
    else:
        expected_projection = project_untarget_pre_cell(finalized_alignment)
        if projection != expected_projection:
            raise CellDecisionContractError(
                "Group projection is partial, crossed, reordered, or substituted"
            )
    owner_subjects = projection.cell_subjects
    requested_subjects = owner_subjects if subjects is None else tuple(subjects)
    ordered_subjects = cast(
        tuple[GroupCellSubject, ...],
        _bind_complete_subject_population(
            requested_subjects,
            owner_subjects=owner_subjects,
            expected_type=GroupCellSubject,
            label="Group",
        ),
    )
    handoff = finalized_alignment.cell_observation_handoff
    if handoff is None:
        raise CellDecisionContractError(
            "Group population requires its Backfill association handoff"
        )
    if fresh:
        owner_observations = finalized_alignment.cell_observations
    else:
        try:
            owner_observations = validate_final_group_cell_observation_population(
                handoff=handoff,
                cell_observations=finalized_alignment.cell_observations,
            )
        except (CellObservationContractError, TypeError, ValueError) as exc:
            raise CellDecisionContractError(str(exc)) from exc
    requested_observations = (
        owner_observations if observations is None else tuple(observations)
    )
    ordered_observations = _bind_complete_observation_population(
        requested_observations,
        owner_observations=owner_observations,
    )
    if fresh:
        subject_keys = tuple(_group_subject_key(item) for item in ordered_subjects)
        observation_keys = tuple(
            _group_observation_key(item) for item in ordered_observations
        )
        if len(subject_keys) != len(set(subject_keys)):
            raise CellDecisionContractError(
                "fresh Group subject population contains a duplicate position"
            )
        if len(observation_keys) != len(set(observation_keys)):
            raise CellDecisionContractError(
                "fresh Group observation population contains a duplicate position"
            )
        if set(subject_keys) != set(observation_keys):
            raise CellDecisionContractError(
                "fresh Group subject and observation populations do not match"
            )
    observation_by_key = {
        _group_observation_key(item): item for item in ordered_observations
    }
    return ordered_subjects, observation_by_key


def validate_production_cell_decision(decision: ProductionCellDecision) -> None:
    """Revalidate one sealed decision against its retained exact owner inputs."""

    if type(decision) is not ProductionCellDecision:
        raise CellDecisionContractError(
            "decision must be an exact ProductionCellDecision"
        )
    _validate_decision_binding(decision)
    expected = _decide_sealed_subject(
        decision.subject,
        evidence=decision.evidence,
    )
    if decision != expected:
        raise CellDecisionContractError(
            "production cell decision payload was substituted"
        )


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
        payload = _decide_target(subject)
    elif type(subject) is GroupCellSubject:
        if type(evidence) is not FinalGroupCellObservation:
            raise CellDecisionContractError(
                "Group decision requires an exact final cell observation"
            )
        _validate_group_binding(subject, evidence)
        payload = _decide_group(subject, evidence)
    else:
        raise CellDecisionContractError(
            "cell decision requires an exact TargetCellSubject or GroupCellSubject"
        )
    return _mint_decision(subject=subject, evidence=evidence, payload=payload)


def _decide_target(subject: TargetCellSubject) -> CellDecisionPayload:
    terminal = subject.terminal_subject
    sample_id = subject.canonical_sample.identity.sample_identity_id
    if terminal.canonical_sample_identity_id != sample_id:
        raise CellDecisionContractError(
            "Target terminal crossed the exact canonical sample"
        )
    if type(terminal) is SearchedNoObservationSubject:
        return NoWriteCellDecision("searched_no_observation")
    if type(terminal) is not ObservedSearchSubject:
        raise CellDecisionContractError(
            "Target terminal uses an unsupported or partial variant"
        )
    hypothesis = terminal.hypothesis
    if not hypothesis.is_owner_backed:
        raise CellDecisionContractError(
            "Target observed terminal requires an owner-backed PeakHypothesis"
        )
    return _payload_from_integration(hypothesis.integration)


def _decide_group(
    subject: GroupCellSubject,
    observation: FinalGroupCellObservation,
) -> CellDecisionPayload:
    state = observation.evidence_state
    states = observation.evidence_states
    value_state_count = sum(item in _GROUP_VALUE_STATES for item in states)
    if subject.member_subject is not None:
        allowed_states = _GROUP_VALUE_STATES | _GROUP_TERMINAL_COMPANION_STATES
        if value_state_count == 1 and all(item in allowed_states for item in states):
            integrations = observation.integrations
            if len(integrations) != 1:
                raise CellDecisionContractError(
                    "Group numeric decision requires exactly one integration"
                )
            return _payload_from_integration(integrations[0])
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
    if state == "mixed":
        raise CellDecisionContractError(
            "Group closure contains unsupported mixed terminal states"
        )
    if state not in _NO_WRITE_STATES - {"not_quantifiable"}:
        raise CellDecisionContractError(
            "Group closure uses an unsupported or partial evidence state"
        )
    return NoWriteCellDecision(cast(NoWriteState, state))


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


def _validate_target_projection(
    projection: TargetPreCellProjection,
) -> tuple[TargetCellSubject, ...]:
    if type(projection) is not TargetPreCellProjection:
        raise CellDecisionContractError(
            "Target population requires an exact TargetPreCellProjection"
        )
    if type(projection.row_binding) is not TargetRowBinding:
        raise CellDecisionContractError(
            "Target projection crossed its exact row binding"
        )
    binding = projection.row_binding
    try:
        validate_target_row_binding(binding)
    except (TypeError, ValueError) as exc:
        raise CellDecisionContractError(str(exc)) from exc
    groups = binding.eligible_groups
    group_ids = tuple(group.group_hypothesis_id for group in groups)
    if group_ids != tuple(sorted(set(group_ids))):
        raise CellDecisionContractError(
            "Target row binding groups are duplicate or non-canonical"
        )
    if any(
        group.processing_request_id != projection.scope.request.processing_request_id
        or group.disposition not in {"established", "provisional"}
        for group in groups
    ):
        raise CellDecisionContractError(
            "Target row binding contains a crossed or ineligible group"
        )
    owner_subjects = tuple(projection.cell_subjects)
    expected_samples = tuple(
        sorted(
            projection.scope.samples,
            key=lambda sample: sample.identity.sample_identity_id,
        )
    )
    actual_samples = tuple(subject.canonical_sample for subject in owner_subjects)
    if actual_samples != expected_samples:
        raise CellDecisionContractError(
            "Target projection does not retain the complete canonical sample population"
        )
    for subject in owner_subjects:
        if (
            type(subject) is not TargetCellSubject
            or subject.target is not projection.scope.target
            or subject.processing_request is not projection.scope.request
            or subject.row_binding is not projection.row_binding
        ):
            raise CellDecisionContractError(
                "Target projection contains a crossed or substituted subject"
            )
        terminal = subject.terminal_subject
        matching_bindings = tuple(
            binding
            for binding in projection.scope.capture_bindings
            if binding.candidate_id == terminal.candidate_id
            and binding.sample_identity_id
            == subject.canonical_sample.identity.sample_identity_id
        )
        if len(matching_bindings) != 1:
            raise CellDecisionContractError(
                "Target terminal candidate crossed its exact scope binding"
            )
    return owner_subjects


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


def _bind_complete_observation_population(
    observations: Sequence[FinalGroupCellObservation],
    *,
    owner_observations: tuple[FinalGroupCellObservation, ...],
) -> tuple[FinalGroupCellObservation, ...]:
    if isinstance(observations, (str, bytes)):
        raise CellDecisionContractError("Group observations must be a finite sequence")
    requested = tuple(observations)
    if any(type(item) is not FinalGroupCellObservation for item in requested):
        raise CellDecisionContractError(
            "Group observation population contains a non-owner closure"
        )
    requested_ids = tuple(id(item) for item in requested)
    owner_ids = tuple(id(item) for item in owner_observations)
    if len(requested_ids) != len(set(requested_ids)):
        raise CellDecisionContractError(
            "Group observation population contains a duplicate closure"
        )
    if set(requested_ids) != set(owner_ids):
        raise CellDecisionContractError(
            "Group observation population is missing, extra, or substituted"
        )
    return requested


def _validate_group_binding(
    subject: GroupCellSubject,
    observation: FinalGroupCellObservation,
) -> None:
    if observation.group is not subject.group:
        raise CellDecisionContractError("Group decision crossed the exact final group")
    if observation.canonical_sample is not subject.canonical_sample:
        raise CellDecisionContractError(
            "Group decision crossed the exact canonical sample"
        )
    if observation.processing_request_id != subject.processing_request_id:
        raise CellDecisionContractError(
            "Group decision crossed the exact processing request"
        )
    member = subject.member_subject
    if member is not None:
        if not any(member is item for item in subject.group.member_subjects):
            raise CellDecisionContractError(
                "Group decision crossed the exact member subject"
            )
        if (
            member.processing_request_id != subject.processing_request_id
            or member.canonical_sample_identity_id
            != subject.canonical_sample.identity.sample_identity_id
        ):
            raise CellDecisionContractError(
                "Group decision member crossed its request or canonical sample"
            )


def _mint_decision(
    *,
    subject: CellSubject,
    evidence: CellEvidence,
    payload: CellDecisionPayload,
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
        _mint_token=_DECISION_MINT_TOKEN,
    )


def _validate_decision_binding(decision: ProductionCellDecision) -> None:
    if type(decision.payload) not in {NumericCellDecision, NoWriteCellDecision}:
        raise CellDecisionContractError(
            "production cell decision contains an unsupported payload"
        )
    expected_basis = canonical_identity_basis_bytes(
        _decision_basis(
            subject=decision.subject,
            evidence=decision.evidence,
            payload=decision.payload,
        )
    )
    if decision.basis_bytes != expected_basis:
        raise CellDecisionContractError(
            "production cell decision mismatches its exact basis"
        )
    if decision.decision_id != typed_identity_digest(_DECISION_PREFIX, expected_basis):
        raise CellDecisionContractError(
            "production cell decision ID mismatches its exact basis"
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
            "target_definition_id": subject.target.definition.target_definition_id,
            "target_key_id": subject.target.definition.target_key.target_key_id,
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
        sample_id = subject.canonical_sample.identity.sample_identity_id
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
            "basis_sha256": hashlib.sha256(evidence.basis_bytes).hexdigest(),
            "evidence_states": evidence.evidence_states,
            "position_ids": evidence.position_ids,
            "source_cell_artifact_ids": evidence.source_cell_artifact_ids,
            "coverage_terminal_ids": tuple(
                item.terminal_id for item in evidence.coverage_terminals
            ),
            "repair_receipt_ids": tuple(
                item.receipt_id for item in evidence.repair_receipts
            ),
            "repaired_observation_keys": tuple(
                item.work_unit_key for item in evidence.repaired_observations
            ),
            "integrations": tuple(
                _integration_identity(item) for item in evidence.integrations
            ),
        }
        request_id = subject.processing_request_id
        sample_id = subject.canonical_sample.identity.sample_identity_id
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
        "canonical_sample_identity_id": sample_id,
        "subject": subject_identity,
        "evidence": evidence_identity,
        "payload": payload_identity,
    }


def _group_subject_key(subject: GroupCellSubject) -> tuple[str, str]:
    return (
        subject.group_hypothesis_id,
        subject.canonical_sample.identity.sample_identity_id,
    )


def _integration_identity(integration: IntegrationResult) -> dict[str, object]:
    if type(integration) is not IntegrationResult:
        raise CellDecisionContractError(
            "decision basis requires an exact IntegrationResult"
        )
    return {item.name: getattr(integration, item.name) for item in fields(integration)}


def _group_observation_key(
    observation: FinalGroupCellObservation,
) -> tuple[str, str]:
    return (
        observation.group_hypothesis_id,
        observation.canonical_sample_identity_id,
    )


def _decision_sort_key(decision: ProductionCellDecision) -> tuple[str, ...]:
    subject = decision.subject
    if type(subject) is TargetCellSubject:
        return (
            "target",
            subject.target.definition.target_definition_id,
            subject.canonical_sample.identity.sample_identity_id,
        )
    if type(subject) is GroupCellSubject:
        return (
            "untarget",
            subject.group_hypothesis_id,
            subject.canonical_sample.identity.sample_identity_id,
        )
    raise CellDecisionContractError("decision contains an unsupported subject")
