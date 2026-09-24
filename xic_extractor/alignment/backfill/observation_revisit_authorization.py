"""Typed repair candidate and authorization receipt validation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import InitVar, asdict, dataclass, field
from typing import TYPE_CHECKING, Literal, TypeAlias

from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillAttemptTerminal,
    BackfillEvaluationCoverageBatch,
    BackfillPlanningPosition,
)
from xic_extractor.alignment.backfill_request_plan import (
    BackfillLogicalRequestMember,
    BackfillRequestIdentity,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    resolve_target_definition,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CID_NL_METHOD_ID,
    CID_NL_METHOD_VERSION,
    CID_NL_PROVIDER_ID,
    CidNlMethodAssessmentRef,
    validate_cid_nl_method_assessment_binding,
)
from xic_extractor.peak_detection.cid_nl_method_subject import CidNlMethodSubject
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    canonical_identity_basis_bytes,
    retry_predecessor_attempt_ids,
    typed_identity_digest,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    resolve_canonical_sample_identity,
)

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_observations import CellObservationRef

ObservationRevisitAuthorizationState: TypeAlias = Literal[
    "not_run", "repair", "multiple_eligible"
]

_AUTHORIZATION_PREFIX = "xic.observation-repair-authorization:"
_CANDIDATE_PREFIX = "xic.observation-repair-candidate:"
_CELL_PREFIX = "xic.observation-repair-cell:"
_COVERAGE_SEMANTICS_PREFIX = "xic.observation-repair-coverage-semantics:"
_AUTHORIZATION_MINT_TOKEN = object()
_CANDIDATE_MINT_TOKEN = object()
_TYPED_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*:[0-9a-f]{64}$")


class ObservationRevisitContractError(ValueError):
    """Raised when ObservationRevisit input cannot be closed exactly."""


@dataclass(frozen=True, slots=True)
class ObservationRevisitCandidate:
    """One owner-selected, membership-neutral repair candidate.

    A candidate is minted only after Backfill has validated the complete
    existing-group by canonical-sample population.  It deliberately carries
    neither a group member subject nor a CrossSample partition transition.
    """

    candidate_id: str
    authorization_id: str
    processing_owners: ProcessingOwnerReferences
    request_identity: BackfillRequestIdentity
    logical_request: BackfillLogicalRequestMember
    predecessor_group: CrossSampleGroupIdentity
    sample_identity: ResolvedCanonicalSampleIdentity
    cell: CellObservationRef
    coverage_position: BackfillPlanningPosition
    repair_epoch: int
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _CANDIDATE_MINT_TOKEN:
            raise ObservationRevisitContractError(
                "repair candidate must be minted by Backfill"
            )
        _validate_candidate(self)

    @property
    def subject_id(self) -> str:
        """Compatibility projection used by the existing receipt path."""

        return self.candidate_id

    @property
    def member_subject(self) -> None:
        """Candidate membership is intentionally not represented."""

        return None

    @property
    def successor_application(self) -> None:
        """Candidate does not mint a CrossSample partition transition."""

        return None


@dataclass(frozen=True, slots=True)
class ObservationRevisitNotRunAuthorization:
    """Complete-population authorization with no eligible repair cell."""

    authorization_id: str
    processing_owners: ProcessingOwnerReferences
    coverage_id: str
    population_cell_ids: tuple[str, ...]
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _AUTHORIZATION_MINT_TOKEN:
            raise ObservationRevisitContractError(
                "repair authorization must be minted by Backfill"
            )
        _validate_authorization_common(
            self,
            state="not_run",
            eligible_cell_ids=(),
        )

    @property
    def variant(self) -> Literal["not_run"]:
        return "not_run"

    @property
    def state(self) -> ObservationRevisitAuthorizationState:
        return self.variant

    @property
    def eligible_cell_ids(self) -> tuple[str, ...]:
        return ()


@dataclass(frozen=True, slots=True)
class ObservationRevisitEligibleAuthorization:
    """Complete-population authorization for exactly one repair candidate."""

    authorization_id: str
    processing_owners: ProcessingOwnerReferences
    coverage_id: str
    population_cell_ids: tuple[str, ...]
    eligible_cell_ids: tuple[str, ...]
    candidate: ObservationRevisitCandidate
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _AUTHORIZATION_MINT_TOKEN:
            raise ObservationRevisitContractError(
                "repair authorization must be minted by Backfill"
            )
        _validate_authorization_common(
            self,
            state="repair",
            eligible_cell_ids=self.eligible_cell_ids,
        )
        if type(self.candidate) is not ObservationRevisitCandidate:
            raise ObservationRevisitContractError(
                "repair authorization candidate must be owner-minted"
            )
        if self.candidate.processing_owners is not self.processing_owners:
            raise ObservationRevisitContractError(
                "repair candidate crosses processing owners"
            )
        if self.candidate.authorization_id != self.authorization_id:
            raise ObservationRevisitContractError(
                "repair candidate crosses its authorization"
            )
        if self.eligible_cell_ids != (self._candidate_cell_id(self.candidate),):
            raise ObservationRevisitContractError(
                "repair authorization must carry exactly its candidate cell"
            )

    @staticmethod
    def _candidate_cell_id(candidate: ObservationRevisitCandidate) -> str:
        return _cell_identity_id(candidate.cell)

    @property
    def variant(self) -> Literal["repair"]:
        return "repair"

    @property
    def state(self) -> ObservationRevisitAuthorizationState:
        return self.variant


@dataclass(frozen=True, slots=True)
class ObservationRevisitMultipleEligibleAuthorization:
    """Complete-population authorization blocked by ambiguous cardinality."""

    authorization_id: str
    processing_owners: ProcessingOwnerReferences
    coverage_id: str
    population_cell_ids: tuple[str, ...]
    eligible_cell_ids: tuple[str, ...]
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _AUTHORIZATION_MINT_TOKEN:
            raise ObservationRevisitContractError(
                "repair authorization must be minted by Backfill"
            )
        _validate_authorization_common(
            self,
            state="multiple_eligible",
            eligible_cell_ids=self.eligible_cell_ids,
        )
        if len(self.eligible_cell_ids) < 2:
            raise ObservationRevisitContractError(
                "multiple-eligible authorization requires at least two cells"
            )

    @property
    def variant(self) -> Literal["multiple_eligible"]:
        return "multiple_eligible"

    @property
    def state(self) -> ObservationRevisitAuthorizationState:
        return self.variant


ObservationRevisitAuthorization: TypeAlias = (
    ObservationRevisitNotRunAuthorization
    | ObservationRevisitEligibleAuthorization
    | ObservationRevisitMultipleEligibleAuthorization
)


def _validate_population_cell(
    cell: CellObservationRef,
    *,
    processing_owners: ProcessingOwnerReferences,
    positions_by_key: dict[tuple[str, str], BackfillPlanningPosition],
    samples_by_id: dict[str, ResolvedCanonicalSampleIdentity],
) -> tuple[str, str, BackfillPlanningPosition]:
    cell_type = _cell_observation_ref_type()
    if type(cell) is not cell_type:
        raise ObservationRevisitContractError(
            "cell population contains a non-owner CellObservationRef"
        )
    try:
        cell.group_context.__post_init__()
        cell.__post_init__()
    except (TypeError, ValueError) as exc:
        raise ObservationRevisitContractError(
            "cell population contains a noncanonical CellObservationRef"
        ) from exc
    content = cell.content
    sample_id = content.canonical_sample_identity_id
    sample = samples_by_id.get(sample_id)
    if sample is None:
        raise ObservationRevisitContractError(
            "cell population crosses the declared canonical sample universe"
        )
    group_id = content.group_hypothesis_id
    key = (group_id, sample_id)
    position = positions_by_key.get(key)
    if position is None:
        raise ObservationRevisitContractError(
            "cell population contains an extra or crossed group/sample position"
        )
    try:
        group_identity = validate_cross_sample_peak_group_identity(
            cell.group_context.group,
            require_canonical=True,
        )
    except (TypeError, ValueError) as exc:
        raise ObservationRevisitContractError(
            "cell population group identity is not canonical"
        ) from exc
    if group_identity != position.group_identity:
        raise ObservationRevisitContractError(
            "cell population crosses its exact existing group identity"
        )
    context_attempt = cell.group_context.processing_attempt
    try:
        allowed_attempt_ids = {
            processing_owners.attempt.processing_attempt_id,
            *retry_predecessor_attempt_ids(processing_owners.attempt),
        }
    except (TypeError, ValueError) as exc:
        raise ObservationRevisitContractError(
            "processing owners do not provide a valid retry lineage"
        ) from exc
    if (
        context_attempt.processing_request_id
        != processing_owners.request.processing_request_id
        or context_attempt.processing_attempt_id not in allowed_attempt_ids
    ):
        raise ObservationRevisitContractError(
            "cell population contains a stale processing attempt"
        )
    if (
        cell.artifact.producer_processing_request_id
        != processing_owners.request.processing_request_id
        or cell.artifact.producer_processing_attempt_id not in allowed_attempt_ids
    ):
        raise ObservationRevisitContractError(
            "cell population artifact is outside the current attempt or retry lineage"
        )
    assessment = cell.method_assessment
    if type(assessment) is not CidNlMethodAssessmentRef:
        raise ObservationRevisitContractError(
            "cell population method assessment is not owner-issued"
        )
    if (
        assessment.content.provider_id != CID_NL_PROVIDER_ID
        or assessment.content.method_id != CID_NL_METHOD_ID
        or assessment.content.method_version != CID_NL_METHOD_VERSION
    ):
        raise ObservationRevisitContractError(
            "cell population is outside the active CID-NL tracer"
        )
    method_subject = assessment.content.method_subject
    if type(method_subject) is not CidNlMethodSubject:
        raise ObservationRevisitContractError(
            "cell population method subject is not owner-issued"
        )
    if method_subject.subject_kind == "targeted_definition":
        target = _resolve_target_for_assessment(
            processing_owners,
            method_subject,
        )
        bound_subject = None
    elif method_subject.subject_kind == "untargeted_precursor":
        target = None
        bound_subject = method_subject
    else:
        raise ObservationRevisitContractError(
            "cell population method subject kind is not closed"
        )
    try:
        validate_cid_nl_method_assessment_binding(
            assessment,
            processing_owners=processing_owners,
            sample=sample,
            target=target,
            method_subject=bound_subject,
        )
    except (TypeError, ValueError) as exc:
        raise ObservationRevisitContractError(
            "cell population method assessment crosses owner request"
        ) from exc
    return sample_id, group_id, position


def _resolve_target_for_assessment(
    processing_owners: ProcessingOwnerReferences,
    method_subject: CidNlMethodSubject,
) -> ResolvedTargetDefinition:
    if processing_owners.review_target is not None:
        target = processing_owners.review_target
        if (
            target.snapshot.snapshot_id != method_subject.target_snapshot_id
            or target.definition.target_key.target_key_id
            != method_subject.target_key_id
            or target.definition.target_definition_id
            != method_subject.target_definition_id
        ):
            raise ObservationRevisitContractError(
                "cell method subject crosses the owner target definition"
            )
        return target
    snapshot = processing_owners.target_snapshot
    if snapshot is None:
        raise ObservationRevisitContractError(
            "targeted cell method subject lacks its owner target snapshot"
        )
    definitions = tuple(
        definition
        for definition in snapshot.definitions
        if definition.target_definition_id == method_subject.target_definition_id
        and definition.target_key.target_key_id == method_subject.target_key_id
    )
    if len(definitions) != 1:
        raise ObservationRevisitContractError(
            "targeted cell method subject does not resolve uniquely"
        )
    definition = definitions[0]
    try:
        return resolve_target_definition(
            snapshot,
            definition.target.label,
            expected_definition=definition,
        )
    except (TypeError, ValueError) as exc:
        raise ObservationRevisitContractError(
            "targeted cell method subject is not owner-resolved"
        ) from exc


def _logical_binding_for_cell(
    cell: CellObservationRef,
    *,
    position: BackfillPlanningPosition,
) -> tuple[BackfillLogicalRequestMember, BackfillRequestIdentity]:
    if position.planning_state != "planned":
        raise ObservationRevisitContractError(
            "eligible cell has no planned owner Backfill request"
        )
    subject = cell.method_assessment.content.method_subject
    matches = tuple(
        planned
        for planned in position.primary_logical_requests
        if planned.logical_request.item.request.mz == subject.precursor_mz
        and planned.logical_request.item.request.ppm_tol
        == subject.precursor_tolerance_ppm
    )
    if len(matches) != 1:
        raise ObservationRevisitContractError(
            "eligible cell does not bind exactly one owner Backfill request"
        )
    planned = matches[0]
    return planned.logical_request, planned.request_identity


def _validate_candidate(candidate: ObservationRevisitCandidate) -> None:
    if type(candidate.processing_owners) is not ProcessingOwnerReferences:
        raise TypeError("candidate processing_owners must be owner-backed")
    if type(candidate.request_identity) is not BackfillRequestIdentity:
        raise TypeError("candidate request identity must be owner-minted")
    if type(candidate.logical_request) is not BackfillLogicalRequestMember:
        raise TypeError("candidate logical request must be owner-minted")
    if type(candidate.predecessor_group) is not CrossSampleGroupIdentity:
        raise TypeError("candidate group must be owner-minted")
    if type(candidate.sample_identity) is not ResolvedCanonicalSampleIdentity:
        raise TypeError("candidate sample must be owner-resolved")
    if type(candidate.coverage_position) is not BackfillPlanningPosition:
        raise TypeError("candidate coverage position must be owner-minted")
    if type(candidate.repair_epoch) is not int or candidate.repair_epoch != 1:
        raise ObservationRevisitContractError(
            "candidate repair epoch must be exactly 1"
        )
    _validate_typed_id(
        candidate.authorization_id, _AUTHORIZATION_PREFIX, "authorization_id"
    )
    _validate_typed_id(candidate.candidate_id, _CANDIDATE_PREFIX, "candidate_id")
    request_binding = (
        candidate.logical_request.item.sample_stem,
        candidate.logical_request.item.request,
    )
    if request_binding != (
        candidate.request_identity.sample_stem,
        candidate.request_identity.request,
    ):
        raise ObservationRevisitContractError(
            "repair candidate crosses exact request identity"
        )
    if (
        candidate.logical_request.group_hypothesis_id
        != candidate.predecessor_group.group_hypothesis_id
    ):
        raise ObservationRevisitContractError(
            "repair candidate crosses exact group identity"
        )
    position = candidate.coverage_position
    if position.group_identity != candidate.predecessor_group:
        raise ObservationRevisitContractError(
            "repair candidate crosses coverage group identity"
        )
    if position.sample != candidate.sample_identity:
        raise ObservationRevisitContractError(
            "repair candidate crosses coverage sample identity"
        )
    if position.planning_state != "planned":
        raise ObservationRevisitContractError(
            "repair candidate requires a planned coverage position"
        )
    try:
        resolved_sample = resolve_canonical_sample_identity(
            candidate.sample_identity.snapshot,
            candidate.sample_identity.lookup_token,
            expected_identity=candidate.sample_identity.identity,
        )
    except (TypeError, ValueError) as exc:
        raise ObservationRevisitContractError(
            "repair candidate sample resolution is not canonical"
        ) from exc
    if resolved_sample != candidate.sample_identity:
        raise ObservationRevisitContractError(
            "repair candidate sample resolution is not canonical"
        )
    _validate_population_cell(
        candidate.cell,
        processing_owners=candidate.processing_owners,
        positions_by_key={
            (
                position.group_identity.group_hypothesis_id,
                position.sample.identity.sample_identity_id,
            ): position
        },
        samples_by_id={
            candidate.sample_identity.identity.sample_identity_id: (
                candidate.sample_identity
            )
        },
    )
    if (
        candidate.cell.content.technical_state != "evaluated"
        or candidate.cell.content.method_result != "HOLD"
    ):
        raise ObservationRevisitContractError(
            "repair candidate requires an evaluated HOLD cell"
        )
    logical_request, request_identity = _logical_binding_for_cell(
        candidate.cell,
        position=position,
    )
    if (
        logical_request != candidate.logical_request
        or request_identity != candidate.request_identity
    ):
        raise ObservationRevisitContractError(
            "repair candidate does not bind the exact planned request"
        )
    expected = _candidate_id(
        authorization_id=candidate.authorization_id,
        processing_request_id=(
            candidate.processing_owners.request.processing_request_id
        ),
        processing_attempt_id=(
            candidate.processing_owners.attempt.processing_attempt_id
        ),
        request_identity=candidate.request_identity,
        logical_request=candidate.logical_request,
        predecessor_group=candidate.predecessor_group,
        sample_identity=candidate.sample_identity,
        cell=candidate.cell,
        coverage_position=candidate.coverage_position,
        repair_epoch=candidate.repair_epoch,
    )
    if candidate.candidate_id != expected:
        raise ObservationRevisitContractError(
            "repair candidate id is not bound to its complete content"
        )


def _validate_authorization_common(
    authorization: object,
    *,
    state: ObservationRevisitAuthorizationState,
    eligible_cell_ids: tuple[str, ...],
) -> None:
    processing_owners = getattr(authorization, "processing_owners", None)
    if type(processing_owners) is not ProcessingOwnerReferences:
        raise TypeError("authorization processing_owners must be owner-backed")
    coverage_id = getattr(authorization, "coverage_id", None)
    if type(coverage_id) is not str or not coverage_id:
        raise ObservationRevisitContractError("authorization coverage_id is required")
    _validate_typed_id(
        coverage_id,
        _COVERAGE_SEMANTICS_PREFIX,
        "coverage_id",
    )
    population_cell_ids = getattr(authorization, "population_cell_ids", None)
    if type(population_cell_ids) is not tuple or not population_cell_ids:
        raise ObservationRevisitContractError(
            "authorization requires a complete non-empty cell population"
        )
    if any(
        type(cell_id) is not str
        or not cell_id.startswith(_CELL_PREFIX)
        or not _TYPED_ID_PATTERN.fullmatch(cell_id)
        for cell_id in population_cell_ids
    ):
        raise ObservationRevisitContractError(
            "authorization cell identities are not typed"
        )
    if len(set(population_cell_ids)) != len(population_cell_ids):
        raise ObservationRevisitContractError(
            "authorization population duplicates a cell identity"
        )
    if type(eligible_cell_ids) is not tuple:
        raise TypeError("authorization eligible cell identities must be a tuple")
    if len(set(eligible_cell_ids)) != len(eligible_cell_ids):
        raise ObservationRevisitContractError(
            "authorization eligible identities are duplicated"
        )
    if any(cell_id not in population_cell_ids for cell_id in eligible_cell_ids):
        raise ObservationRevisitContractError(
            "authorization eligible cell is outside its population"
        )
    expected = _authorization_id(
        processing_owners=processing_owners,
        coverage_id=coverage_id,
        population_cell_ids=population_cell_ids,
        eligible_cell_ids=eligible_cell_ids,
        state=state,
    )
    authorization_id = getattr(authorization, "authorization_id", None)
    if authorization_id != expected:
        raise ObservationRevisitContractError(
            "repair authorization id is not bound to its complete content"
        )


def _cell_observation_ref_type() -> type[object]:
    from xic_extractor.alignment.cell_observations import CellObservationRef

    return CellObservationRef


def _validate_typed_id(value: object, prefix: str, field: str) -> None:
    if (
        type(value) is not str
        or not value.startswith(prefix)
        or not _TYPED_ID_PATTERN.fullmatch(value)
    ):
        raise ObservationRevisitContractError(f"{field} is not a typed identity")


def _cell_identity_id(cell: CellObservationRef) -> str:
    from xic_extractor.alignment.cell_observations import (
        canonical_cell_observation_bytes,
    )

    content_digest = hashlib.sha256(
        canonical_cell_observation_bytes(cell.content)
    ).hexdigest()
    return typed_identity_digest(
        _CELL_PREFIX,
        canonical_identity_basis_bytes(
            {
                "group_hypothesis_id": cell.content.group_hypothesis_id,
                "canonical_sample_identity_id": (
                    cell.content.canonical_sample_identity_id
                ),
                "artifact_id": cell.artifact.evidence_artifact_id,
                "content_sha256": content_digest,
            }
        ),
    )


def _coverage_semantics_id(
    coverage: BackfillEvaluationCoverageBatch,
) -> str:
    """Bind coverage facts while deliberately dropping producer selection flags."""

    return typed_identity_digest(
        _COVERAGE_SEMANTICS_PREFIX,
        canonical_identity_basis_bytes(
            {
                "schema_version": "observation_repair_coverage_semantics",
                "epoch_id": coverage.epoch.epoch_id,
                "processing_request_id": (
                    coverage.epoch.processing_owners.request.processing_request_id
                ),
                "processing_attempt_id": (
                    coverage.epoch.processing_owners.attempt.processing_attempt_id
                ),
                "terminals": [
                    _terminal_nonselection_basis(terminal)
                    for terminal in coverage.terminals
                ],
            }
        ),
    )


def _terminal_nonselection_basis(
    terminal: BackfillAttemptTerminal,
) -> dict[str, object]:
    if type(terminal) is not BackfillAttemptTerminal:
        raise ObservationRevisitContractError("coverage terminal is not owner-minted")
    physical_evaluation = getattr(terminal, "physical_evaluation", None)
    return {
        "terminal_id": terminal.terminal_id,
        "position_id": terminal.position_id,
        "logical_request_id": terminal.logical_request.logical_request_id,
        "request_identity_id": terminal.request_identity.request_identity_id,
        "candidate_phase": terminal.candidate_phase,
        "candidate_index": terminal.candidate_index,
        "trace_evaluated": terminal.trace_evaluated,
        "terminal_reason": terminal.terminal_reason,
        "physical_evaluation": (
            None if physical_evaluation is None else asdict(physical_evaluation)
        ),
    }


def _authorization_id(
    *,
    processing_owners: ProcessingOwnerReferences,
    coverage: BackfillEvaluationCoverageBatch | None = None,
    coverage_id: str | None = None,
    population_cell_ids: tuple[str, ...],
    eligible_cell_ids: tuple[str, ...],
    state: ObservationRevisitAuthorizationState,
) -> str:
    if coverage is not None:
        coverage_id = _coverage_semantics_id(coverage)
    if coverage_id is None:
        raise ObservationRevisitContractError(
            "authorization requires coverage identity"
        )
    return typed_identity_digest(
        _AUTHORIZATION_PREFIX,
        canonical_identity_basis_bytes(
            {
                "schema_version": "observation_repair_authorization",
                "processing_request_id": (
                    processing_owners.request.processing_request_id
                ),
                "processing_attempt_id": (
                    processing_owners.attempt.processing_attempt_id
                ),
                "coverage_id": coverage_id,
                "population_cell_ids": list(population_cell_ids),
                "eligible_cell_ids": list(eligible_cell_ids),
                "state": state,
                "repair_epoch": 1,
            }
        ),
    )


def _candidate_id(
    *,
    authorization_id: str,
    processing_request_id: str,
    processing_attempt_id: str,
    request_identity: BackfillRequestIdentity,
    logical_request: BackfillLogicalRequestMember,
    predecessor_group: CrossSampleGroupIdentity,
    sample_identity: ResolvedCanonicalSampleIdentity,
    cell: CellObservationRef,
    coverage_position: BackfillPlanningPosition,
    repair_epoch: int,
) -> str:
    return typed_identity_digest(
        _CANDIDATE_PREFIX,
        canonical_identity_basis_bytes(
            {
                "schema_version": "observation_repair_candidate",
                "authorization_id": authorization_id,
                "processing_request_id": processing_request_id,
                "processing_attempt_id": processing_attempt_id,
                "request_identity_id": request_identity.request_identity_id,
                "logical_request_id": logical_request.logical_request_id,
                "group_hypothesis_id": predecessor_group.group_hypothesis_id,
                "sample_identity_id": sample_identity.identity.sample_identity_id,
                "coverage_position_id": coverage_position.position_id,
                "cell_identity_id": _cell_identity_id(cell),
                "repair_epoch": repair_epoch,
            }
        ),
    )


__all__ = [
    "ObservationRevisitAuthorization",
    "ObservationRevisitAuthorizationState",
    "ObservationRevisitCandidate",
    "ObservationRevisitContractError",
    "ObservationRevisitMultipleEligibleAuthorization",
    "ObservationRevisitNotRunAuthorization",
    "ObservationRevisitEligibleAuthorization",
    "_AUTHORIZATION_MINT_TOKEN",
    "_AUTHORIZATION_PREFIX",
    "_CANDIDATE_MINT_TOKEN",
    "_CANDIDATE_PREFIX",
    "_CELL_PREFIX",
    "_COVERAGE_SEMANTICS_PREFIX",
    "_TYPED_ID_PATTERN",
    "_authorization_id",
    "_candidate_id",
    "_cell_identity_id",
    "_cell_observation_ref_type",
    "_coverage_semantics_id",
    "_logical_binding_for_cell",
    "_resolve_target_for_assessment",
    "_terminal_nonselection_basis",
    "_validate_authorization_common",
    "_validate_candidate",
    "_validate_population_cell",
    "_validate_typed_id",
]
