"""One bounded Backfill transaction that closes repaired observation evidence."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import InitVar, asdict, dataclass, field
from typing import TYPE_CHECKING, Literal, TypeAlias

from xic_extractor.alignment.backfill.evaluation_coverage import (
    OwnerBackfillAttemptTerminal,
    OwnerBackfillEvaluationCoverageBatch,
    OwnerBackfillPlanningPosition,
    validate_owner_backfill_conservative_no_request_position,
    validate_owner_backfill_evaluation_coverage,
)
from xic_extractor.alignment.backfill_transaction import OwnerBackfillSampleResult
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleSuccessorApplication,
    mint_cross_sample_group_identity,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillPeakFactObservation,
    validate_owner_backfill_peak_fact_observation,
)
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    selected_integration_authority_projection,
)
from xic_extractor.alignment.owner_backfill_request_plan import (
    OwnerBackfillLogicalRequestMember,
    OwnerBackfillRequestIdentity,
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

ObservationRepairOutcome: TypeAlias = Literal["no_repair", "repaired"]
ObservationRepairAuthorizationState: TypeAlias = Literal[
    "not_run", "repair", "multiple_eligible"
]

_SUBJECT_PREFIX = "xic.observation-repair-subject.v1:"
_RECEIPT_PREFIX = "xic.observation-repair-receipt.v1:"
_CANDIDATE_RECEIPT_PREFIX = "xic.observation-repair-receipt.v2:"
_AUTHORIZATION_PREFIX = "xic.observation-repair-authorization.v1:"
_CANDIDATE_PREFIX = "xic.observation-repair-candidate.v1:"
_CELL_PREFIX = "xic.observation-repair-cell.v1:"
_COVERAGE_SEMANTICS_PREFIX = "xic.observation-repair-coverage-semantics.v1:"
_NO_REPAIR_TERMINAL_PREFIX = "xic.observation-repair-no-repair-terminal.v1:"
_PHYSICAL_ANSWER_PREFIX = "physical-answer.v1:"
_SUBJECT_MINT_TOKEN = object()
_RECEIPT_MINT_TOKEN = object()
_AUTHORIZATION_MINT_TOKEN = object()
_CANDIDATE_MINT_TOKEN = object()
_NO_REPAIR_TERMINAL_MINT_TOKEN = object()
_TERMINAL_RESULT_MINT_TOKEN = object()
_TYPED_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*:[0-9a-f]{64}$")


class ObservationRepairContractError(ValueError):
    """Raised when ObservationRepair input cannot be closed exactly."""


@dataclass(frozen=True, slots=True)
class ObservationRepairSubject:
    """Owner-minted subject for exactly one repair epoch."""

    subject_id: str
    request_identity: OwnerBackfillRequestIdentity
    logical_request: OwnerBackfillLogicalRequestMember
    predecessor_group: CrossSampleGroupIdentity
    member_subject: CrossSampleGroupMemberSubject
    sample_identity: ResolvedCanonicalSampleIdentity
    successor_application: CrossSampleSuccessorApplication
    repair_epoch: int
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _SUBJECT_MINT_TOKEN:
            raise ObservationRepairContractError(
                "repair subject must be minted by Backfill"
            )
        _validate_subject(self)


@dataclass(frozen=True, slots=True)
class ObservationRepairCandidate:
    """One owner-selected, membership-neutral repair candidate.

    A candidate is minted only after Backfill has validated the complete
    existing-group by canonical-sample population.  It deliberately carries
    neither a group member subject nor a CrossSample successor application.
    """

    candidate_id: str
    authorization_id: str
    processing_owners: ProcessingOwnerReferences
    request_identity: OwnerBackfillRequestIdentity
    logical_request: OwnerBackfillLogicalRequestMember
    predecessor_group: CrossSampleGroupIdentity
    sample_identity: ResolvedCanonicalSampleIdentity
    cell: CellObservationRef
    coverage_position: OwnerBackfillPlanningPosition
    repair_epoch: int
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _CANDIDATE_MINT_TOKEN:
            raise ObservationRepairContractError(
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
        """Candidate does not mint a CrossSample successor application."""

        return None


@dataclass(frozen=True, slots=True)
class ObservationRepairNotRunAuthorization:
    """Complete-population authorization with no eligible repair cell."""

    authorization_id: str
    processing_owners: ProcessingOwnerReferences
    coverage_id: str
    population_cell_ids: tuple[str, ...]
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _AUTHORIZATION_MINT_TOKEN:
            raise ObservationRepairContractError(
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
    def state(self) -> ObservationRepairAuthorizationState:
        return self.variant

    @property
    def eligible_cell_ids(self) -> tuple[str, ...]:
        return ()


@dataclass(frozen=True, slots=True)
class ObservationRepairRepairAuthorization:
    """Complete-population authorization for exactly one repair candidate."""

    authorization_id: str
    processing_owners: ProcessingOwnerReferences
    coverage_id: str
    population_cell_ids: tuple[str, ...]
    eligible_cell_ids: tuple[str, ...]
    candidate: ObservationRepairCandidate
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _AUTHORIZATION_MINT_TOKEN:
            raise ObservationRepairContractError(
                "repair authorization must be minted by Backfill"
            )
        _validate_authorization_common(
            self,
            state="repair",
            eligible_cell_ids=self.eligible_cell_ids,
        )
        if type(self.candidate) is not ObservationRepairCandidate:
            raise ObservationRepairContractError(
                "repair authorization candidate must be owner-minted"
            )
        if self.candidate.processing_owners is not self.processing_owners:
            raise ObservationRepairContractError(
                "repair candidate crosses processing owners"
            )
        if self.candidate.authorization_id != self.authorization_id:
            raise ObservationRepairContractError(
                "repair candidate crosses its authorization"
            )
        if self.eligible_cell_ids != (self._candidate_cell_id(self.candidate),):
            raise ObservationRepairContractError(
                "repair authorization must carry exactly its candidate cell"
            )

    @staticmethod
    def _candidate_cell_id(candidate: ObservationRepairCandidate) -> str:
        return _cell_identity_id(candidate.cell)

    @property
    def variant(self) -> Literal["repair"]:
        return "repair"

    @property
    def state(self) -> ObservationRepairAuthorizationState:
        return self.variant


@dataclass(frozen=True, slots=True)
class ObservationRepairMultipleEligibleAuthorization:
    """Complete-population authorization blocked by ambiguous cardinality."""

    authorization_id: str
    processing_owners: ProcessingOwnerReferences
    coverage_id: str
    population_cell_ids: tuple[str, ...]
    eligible_cell_ids: tuple[str, ...]
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _AUTHORIZATION_MINT_TOKEN:
            raise ObservationRepairContractError(
                "repair authorization must be minted by Backfill"
            )
        _validate_authorization_common(
            self,
            state="multiple_eligible",
            eligible_cell_ids=self.eligible_cell_ids,
        )
        if len(self.eligible_cell_ids) < 2:
            raise ObservationRepairContractError(
                "multiple-eligible authorization requires at least two cells"
            )

    @property
    def variant(self) -> Literal["multiple_eligible"]:
        return "multiple_eligible"

    @property
    def state(self) -> ObservationRepairAuthorizationState:
        return self.variant


ObservationRepairAuthorization: TypeAlias = (
    ObservationRepairNotRunAuthorization
    | ObservationRepairRepairAuthorization
    | ObservationRepairMultipleEligibleAuthorization
)


@dataclass(frozen=True, slots=True)
class ObservationRepairEvidence:
    """Process-safe evidence facts; this record carries no selection authority."""

    logical_request_id: str
    request_identity_id: str
    group_hypothesis_id: str
    member_subject_id: str | None
    sample_stem: str
    work_unit_key: str
    candidate_index: int
    candidate_phase: str
    acquisition_source_id: str
    selection_subject_id: str
    physical_question_id: str
    physical_answer_id: str
    physical_segment_id: str
    fact_content_id: str
    selected_integration_authority_content_id: str
    rt_left_min: float
    rt_apex_min: float
    rt_right_min: float
    height: float
    scan_support_count: int
    area_counts_seconds: float
    area_source: str


@dataclass(frozen=True, slots=True)
class ObservationRepairNoRepairTerminal:
    """Exact physical terminal fact for a membership-neutral no-repair result."""

    terminal_id: str
    subject_id: str
    authorization_id: str
    candidate_id: str
    repair_epoch: int
    work_unit_key: str
    physical_answer_id: str
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _NO_REPAIR_TERMINAL_MINT_TOKEN:
            raise ObservationRepairContractError(
                "no-repair terminal must be minted by Backfill"
            )
        _validate_no_repair_terminal(self)


@dataclass(frozen=True, slots=True)
class ObservationRepairReceipt:
    """Terminal content-bound handoff from Backfill to its next owner."""

    receipt_id: str
    subject: ObservationRepairSubject | ObservationRepairCandidate
    subject_id: str
    repair_epoch: int
    outcome: ObservationRepairOutcome
    sample_stem: str
    evidence: ObservationRepairEvidence | None
    no_repair_terminal: ObservationRepairNoRepairTerminal | None = None
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RECEIPT_MINT_TOKEN:
            raise ObservationRepairContractError(
                "repair receipt must be minted by Backfill"
            )
        if type(self.subject) not in {
            ObservationRepairSubject,
            ObservationRepairCandidate,
        }:
            raise TypeError("repair receipt subject must be owner-minted")
        _validate_subject(self.subject)
        if self.subject_id != self.subject.subject_id:
            raise ObservationRepairContractError(
                "repair receipt crosses its exact subject"
            )
        if self.repair_epoch != 1:
            raise ObservationRepairContractError("repair receipt exceeds one epoch")
        if self.repair_epoch != self.subject.repair_epoch:
            raise ObservationRepairContractError("repair receipt crosses subject epoch")
        if self.sample_stem != self.subject.request_identity.sample_stem:
            raise ObservationRepairContractError(
                "repair receipt crosses subject sample"
            )
        expected_outcome = "repaired" if self.evidence is not None else "no_repair"
        if self.outcome != expected_outcome:
            raise ObservationRepairContractError(
                "repair receipt outcome crosses its terminal evidence"
            )
        if self.evidence is not None and self.no_repair_terminal is not None:
            raise ObservationRepairContractError(
                "repaired receipt cannot carry a no-repair terminal"
            )
        if (
            type(self.subject) is ObservationRepairSubject
            and self.no_repair_terminal is not None
        ):
            raise ObservationRepairContractError(
                "no-repair terminal is candidate-scoped"
            )
        if self.evidence is None:
            if (
                type(self.subject) is ObservationRepairCandidate
                and self.no_repair_terminal is None
            ):
                raise ObservationRepairContractError(
                    "candidate no-repair receipt requires an exact terminal"
                )
            if self.no_repair_terminal is not None:
                if (
                    type(self.no_repair_terminal)
                    is not ObservationRepairNoRepairTerminal
                ):
                    raise TypeError("no-repair terminal must be owner-minted")
                self.no_repair_terminal.__post_init__(_NO_REPAIR_TERMINAL_MINT_TOKEN)
                if (
                    self.no_repair_terminal.subject_id != self.subject_id
                    or self.no_repair_terminal.repair_epoch != self.repair_epoch
                    or self.no_repair_terminal.authorization_id != self.authorization_id
                    or self.no_repair_terminal.candidate_id != self.candidate_id
                ):
                    raise ObservationRepairContractError(
                        "no-repair terminal crosses exact subject or epoch"
                    )
        expected = _receipt_id(
            subject_id=self.subject_id,
            repair_epoch=self.repair_epoch,
            outcome=self.outcome,
            sample_stem=self.sample_stem,
            evidence=self.evidence,
            no_repair_terminal=self.no_repair_terminal,
            authorization_id=self.authorization_id,
            candidate_id=self.candidate_id,
        )
        if self.receipt_id != expected:
            raise ObservationRepairContractError(
                "repair receipt id is not bound to its complete content"
            )

    @property
    def evidence_authority(self) -> str:
        return "observation_evidence_only"

    @property
    def membership_authority(self) -> str:
        return "none"

    @property
    def value_authority(self) -> str:
        return "none"

    @property
    def matrix_authority(self) -> str:
        return "none"

    @property
    def activation_authority(self) -> str:
        return "none"

    @property
    def writer_authority(self) -> str:
        return "none"

    @property
    def authorization_id(self) -> str | None:
        """Expose the candidate authorization without granting new authority."""

        if type(self.subject) is ObservationRepairCandidate:
            return self.subject.authorization_id
        return None

    @property
    def candidate_id(self) -> str | None:
        """Expose the membership-neutral candidate identity when present."""

        if type(self.subject) is ObservationRepairCandidate:
            return self.subject.candidate_id
        return None


@dataclass(frozen=True, slots=True)
class ObservationRepairTerminalResult:
    """Closed owner result for one complete population authorization."""

    authorization: ObservationRepairAuthorization
    receipt: ObservationRepairReceipt | None
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _TERMINAL_RESULT_MINT_TOKEN:
            raise ObservationRepairContractError(
                "repair terminal result must be minted by Backfill"
            )
        if type(self.authorization) not in {
            ObservationRepairNotRunAuthorization,
            ObservationRepairRepairAuthorization,
            ObservationRepairMultipleEligibleAuthorization,
        }:
            raise TypeError("terminal result authorization must be owner-minted")
        self.authorization.__post_init__(_AUTHORIZATION_MINT_TOKEN)
        if type(self.authorization) is ObservationRepairRepairAuthorization:
            if type(self.receipt) is not ObservationRepairReceipt:
                raise ObservationRepairContractError(
                    "repair terminal result requires an exact receipt"
                )
            if (
                self.receipt.subject != self.authorization.candidate
                or self.receipt.authorization_id != self.authorization.authorization_id
                or self.receipt.candidate_id
                != self.authorization.candidate.candidate_id
            ):
                raise ObservationRepairContractError(
                    "repair terminal result receipt crosses authorization"
                )
        elif self.receipt is not None:
            raise ObservationRepairContractError(
                "blocked repair terminal result cannot carry a receipt"
            )

    @property
    def state(self) -> ObservationRepairAuthorizationState:
        return self.authorization.state

    @property
    def candidate(self) -> ObservationRepairCandidate | None:
        if type(self.authorization) is ObservationRepairRepairAuthorization:
            return self.authorization.candidate
        return None


def mint_observation_repair_subject(
    *,
    request_identity: OwnerBackfillRequestIdentity,
    logical_request: OwnerBackfillLogicalRequestMember,
    predecessor_group: CrossSampleGroupIdentity,
    member_subject: CrossSampleGroupMemberSubject,
    sample_identity: ResolvedCanonicalSampleIdentity,
    successor_application: CrossSampleSuccessorApplication,
    repair_epoch: int = 1,
) -> ObservationRepairSubject:
    """Mint the only repair subject Backfill is allowed to close."""

    if type(request_identity) is not OwnerBackfillRequestIdentity:
        raise TypeError("request identity must be owner-minted")
    if type(logical_request) is not OwnerBackfillLogicalRequestMember:
        raise TypeError("logical request must be owner-minted")
    if type(predecessor_group) is not CrossSampleGroupIdentity:
        raise TypeError("predecessor group must be owner-minted")
    if type(member_subject) is not CrossSampleGroupMemberSubject:
        raise TypeError("member subject must be owner-minted")
    if type(sample_identity) is not ResolvedCanonicalSampleIdentity:
        raise TypeError("sample identity must be owner-resolved")
    if type(successor_application) is not CrossSampleSuccessorApplication:
        raise TypeError("successor application must be CrossSample-minted")
    if type(repair_epoch) is not int or repair_epoch != 1:
        raise ObservationRepairContractError("repair epoch must be exactly 1")
    subject_id = _subject_id(
        request_identity=request_identity,
        logical_request=logical_request,
        predecessor_group=predecessor_group,
        member_subject=member_subject,
        sample_identity=sample_identity,
        successor_application=successor_application,
        repair_epoch=repair_epoch,
    )
    return ObservationRepairSubject(
        subject_id=subject_id,
        request_identity=request_identity,
        logical_request=logical_request,
        predecessor_group=predecessor_group,
        member_subject=member_subject,
        sample_identity=sample_identity,
        successor_application=successor_application,
        repair_epoch=repair_epoch,
        _mint_token=_SUBJECT_MINT_TOKEN,
    )


def authorize_observation_repair(
    *,
    processing_owners: ProcessingOwnerReferences,
    coverage: OwnerBackfillEvaluationCoverageBatch,
    cell_observations: tuple[CellObservationRef, ...],
) -> ObservationRepairAuthorization:
    """Derive a closed repair authorization from one complete owner population.

    The operation is intentionally the only place that reduces the active
    CID-NL population to ``not_run``, ``repair(candidate)``, or
    ``multiple_eligible``.  It does not consult producer selection flags and it
    never constructs CrossSample membership or successor authority.
    """

    if type(processing_owners) is not ProcessingOwnerReferences:
        raise TypeError("processing_owners must be owner-backed")
    if type(coverage) is not OwnerBackfillEvaluationCoverageBatch:
        raise TypeError("coverage must be owner-minted evaluation coverage")
    try:
        validate_owner_backfill_evaluation_coverage(coverage)
    except (TypeError, ValueError) as exc:
        raise ObservationRepairContractError(
            "evaluation coverage is not canonical"
        ) from exc
    if coverage.epoch.processing_owners is not processing_owners:
        raise ObservationRepairContractError(
            "evaluation coverage crosses processing owners"
        )
    if type(cell_observations) is not tuple:
        raise ObservationRepairContractError(
            "cell observations must be an immutable owner population tuple"
        )
    if not cell_observations:
        raise ObservationRepairContractError(
            "cell observation population cannot be empty"
        )

    positions = coverage.epoch.positions
    positions_by_key = {
        (
            position.group_identity.group_hypothesis_id,
            position.sample.identity.sample_identity_id,
        ): position
        for position in positions
    }
    if len(positions_by_key) != len(positions):
        raise ObservationRepairContractError(
            "evaluation coverage duplicates a group/sample position"
        )
    samples_by_id = {
        sample.identity.sample_identity_id: sample for sample in coverage.epoch.samples
    }
    seen_keys: set[tuple[str, str]] = set()
    rows: list[
        tuple[tuple[str, str], str, CellObservationRef, OwnerBackfillPlanningPosition]
    ] = []
    eligible: list[
        tuple[
            str,
            CellObservationRef,
            OwnerBackfillPlanningPosition,
            OwnerBackfillLogicalRequestMember,
            OwnerBackfillRequestIdentity,
        ]
    ] = []
    for cell in cell_observations:
        sample_id, group_id, position = _validate_population_cell(
            cell,
            processing_owners=processing_owners,
            positions_by_key=positions_by_key,
            samples_by_id=samples_by_id,
        )
        key = (group_id, sample_id)
        if key in seen_keys:
            raise ObservationRepairContractError(
                "cell observation population contains a duplicate position"
            )
        seen_keys.add(key)
        cell_id = _cell_identity_id(cell)
        rows.append((key, cell_id, cell, position))
        no_request_proof = validate_owner_backfill_conservative_no_request_position(
            position,
            source_cell=cell,
            source_population=cell_observations,
        )
        if (
            no_request_proof is None
            and cell.content.technical_state == "evaluated"
            and cell.content.method_result == "HOLD"
            # A completed composite requirement cannot become SUPPORT through
            # an MS1-only repair. Its acquisition owner evaluates every loss.
            and cell.method_assessment.content.method_subject.composition is None
            and not (
                cell.peak_observation is not None
                and position.planning_reason == "retained_local_owner"
            )
        ):
            logical_request, request_identity = _logical_binding_for_cell(
                cell,
                position=position,
            )
            eligible.append(
                (cell_id, cell, position, logical_request, request_identity)
            )

    expected_keys = set(positions_by_key)
    if seen_keys != expected_keys:
        missing = sorted(expected_keys - seen_keys)
        extra = sorted(seen_keys - expected_keys)
        raise ObservationRepairContractError(
            "cell observation population is incomplete or contains extra positions"
            f" (missing={missing!r}, extra={extra!r})"
        )
    rows.sort(key=lambda item: item[0])
    eligible.sort(
        key=lambda item: (
            item[2].group_identity.group_hypothesis_id,
            item[2].sample.identity.sample_identity_id,
        )
    )
    population_cell_ids = tuple(item[1] for item in rows)
    eligible_cell_ids = tuple(item[0] for item in eligible)
    coverage_identity = _coverage_semantics_id(coverage)
    if len(eligible) == 0:
        authorization_id = _authorization_id(
            processing_owners=processing_owners,
            coverage=coverage,
            population_cell_ids=population_cell_ids,
            eligible_cell_ids=(),
            state="not_run",
        )
        return ObservationRepairNotRunAuthorization(
            authorization_id=authorization_id,
            processing_owners=processing_owners,
            coverage_id=coverage_identity,
            population_cell_ids=population_cell_ids,
            _mint_token=_AUTHORIZATION_MINT_TOKEN,
        )
    if len(eligible) > 1:
        authorization_id = _authorization_id(
            processing_owners=processing_owners,
            coverage=coverage,
            population_cell_ids=population_cell_ids,
            eligible_cell_ids=eligible_cell_ids,
            state="multiple_eligible",
        )
        return ObservationRepairMultipleEligibleAuthorization(
            authorization_id=authorization_id,
            processing_owners=processing_owners,
            coverage_id=coverage_identity,
            population_cell_ids=population_cell_ids,
            eligible_cell_ids=eligible_cell_ids,
            _mint_token=_AUTHORIZATION_MINT_TOKEN,
        )

    cell_id, cell, position, logical_request, request_identity = eligible[0]
    authorization_id = _authorization_id(
        processing_owners=processing_owners,
        coverage=coverage,
        population_cell_ids=population_cell_ids,
        eligible_cell_ids=(cell_id,),
        state="repair",
    )
    candidate = ObservationRepairCandidate(
        candidate_id=_candidate_id(
            authorization_id=authorization_id,
            processing_request_id=processing_owners.request.processing_request_id,
            processing_attempt_id=processing_owners.attempt.processing_attempt_id,
            request_identity=request_identity,
            logical_request=logical_request,
            predecessor_group=position.group_identity,
            sample_identity=position.sample,
            cell=cell,
            coverage_position=position,
            repair_epoch=1,
        ),
        authorization_id=authorization_id,
        processing_owners=processing_owners,
        request_identity=request_identity,
        logical_request=logical_request,
        predecessor_group=position.group_identity,
        sample_identity=position.sample,
        cell=cell,
        coverage_position=position,
        repair_epoch=1,
        _mint_token=_CANDIDATE_MINT_TOKEN,
    )
    return ObservationRepairRepairAuthorization(
        authorization_id=authorization_id,
        processing_owners=processing_owners,
        coverage_id=coverage_identity,
        population_cell_ids=population_cell_ids,
        eligible_cell_ids=(cell_id,),
        candidate=candidate,
        _mint_token=_AUTHORIZATION_MINT_TOKEN,
    )


def close_observation_repair(
    subject: (
        ObservationRepairSubject
        | ObservationRepairCandidate
        | ObservationRepairRepairAuthorization
    ),
    result: OwnerBackfillSampleResult,
    *,
    authorization: ObservationRepairRepairAuthorization | None = None,
    coverage: OwnerBackfillEvaluationCoverageBatch | None = None,
    cell_observations: tuple[CellObservationRef, ...] | None = None,
) -> ObservationRepairReceipt:
    """Close one complete owner result as ``no_repair`` or ``repaired``.

    A membership-neutral candidate must be closed against the complete current
    population.  Re-authorizing at this boundary prevents an older candidate
    from being replayed after the population cardinality or candidate changes.
    Legacy member subjects retain their existing close-only contract.
    """

    if type(subject) is ObservationRepairRepairAuthorization:
        if authorization is not None and authorization is not subject:
            raise ObservationRepairContractError(
                "repair execution received two different authorizations"
            )
        authorization = subject
        authorization.__post_init__(_AUTHORIZATION_MINT_TOKEN)
        effective_subject: ObservationRepairSubject | ObservationRepairCandidate = (
            subject.candidate
        )
    elif type(subject) is ObservationRepairCandidate:
        if type(authorization) is not ObservationRepairRepairAuthorization:
            raise ObservationRepairContractError(
                "repair candidate requires its exact current authorization"
            )
        authorization.__post_init__(_AUTHORIZATION_MINT_TOKEN)
        if authorization.candidate is not subject:
            raise ObservationRepairContractError(
                "repair execution candidate is not the exact authorized candidate"
            )
        effective_subject = subject
    elif type(subject) is ObservationRepairSubject:
        if (
            authorization is not None
            or coverage is not None
            or cell_observations is not None
        ):
            raise ObservationRepairContractError(
                "member repair subject cannot carry population authorization"
            )
        effective_subject = subject
    else:
        raise TypeError(
            "subject must be an owner-minted ObservationRepairSubject, candidate, "
            "or repair authorization"
        )
    if type(effective_subject) is ObservationRepairCandidate:
        _require_current_repair_authorization(
            authorization,
            candidate=effective_subject,
            coverage=coverage,
            cell_observations=cell_observations,
        )
    return _close_observation_repair_after_authorization(
        effective_subject,
        result,
    )


def _close_observation_repair_after_authorization(
    subject: (ObservationRepairSubject | ObservationRepairCandidate),
    result: OwnerBackfillSampleResult,
) -> ObservationRepairReceipt:
    """Close one already-authorized owner result."""

    _validate_subject(subject)
    effective_subject = subject
    if type(result) is not OwnerBackfillSampleResult:
        raise TypeError("result must be an OwnerBackfillSampleResult")
    if (
        type(result.peak_fact_observations) is not tuple
        or type(result.prepared_peak_facts) is not tuple
    ):
        raise ObservationRepairContractError(
            "repair result evidence containers must be immutable tuples"
        )
    if (
        isinstance(result.sample_index, bool)
        or not isinstance(result.sample_index, int)
        or result.sample_index < 0
    ):
        raise ObservationRepairContractError(
            "repair result sample index must be non-negative"
        )
    if result.sample_stem != effective_subject.request_identity.sample_stem:
        raise ObservationRepairContractError("repair result crosses sample identity")

    if result.prepared_peak_facts:
        raise ObservationRepairContractError(
            "prepared Backfill facts lack exact logical request closure"
        )
    inputs = result.peak_fact_observations
    exact_work_units: set[str] = set()
    exact_live: list[OwnerBackfillPeakFactObservation] = []
    normalized_list: list[ObservationRepairEvidence] = []
    for item in inputs:
        normalized = _normalize_live_evidence(effective_subject, item)
        if (
            item.logical_request == effective_subject.logical_request
            and item.request_identity == effective_subject.request_identity
            and item.cell.group_hypothesis_id
            == effective_subject.predecessor_group.group_hypothesis_id
            and item.cell.sample_stem == effective_subject.request_identity.sample_stem
        ):
            if item.work_unit_key in exact_work_units:
                raise ObservationRepairContractError(
                    "repair result contains duplicate evidence"
                )
            exact_work_units.add(item.work_unit_key)
            exact_live.append(item)
        if normalized is not None:
            normalized_list.append(normalized)
    if not normalized_list:
        evidence = None
        if type(effective_subject) is ObservationRepairCandidate:
            if len(exact_live) != 1:
                raise ObservationRepairContractError(
                    "candidate no_repair requires exactly one exact live "
                    "evaluated observation"
                )
            no_repair_terminal = _mint_no_repair_terminal(
                effective_subject,
                exact_live[0],
            )
        else:
            if not exact_live:
                raise ObservationRepairContractError(
                    "no_repair requires an exact live evaluated observation"
                )
            no_repair_terminal = None
    elif len(normalized_list) != 1:
        content_ids = tuple(item.fact_content_id for item in normalized_list)
        if len(set(content_ids)) != len(content_ids):
            raise ObservationRepairContractError(
                "repair result contains duplicate evidence"
            )
        raise ObservationRepairContractError(
            "repair result must contain exactly one complete selected evidence"
        )
    else:
        evidence = normalized_list[0]
        no_repair_terminal = None

    outcome: ObservationRepairOutcome = (
        "repaired" if evidence is not None else "no_repair"
    )
    receipt_id = _receipt_id(
        subject_id=effective_subject.subject_id,
        repair_epoch=effective_subject.repair_epoch,
        outcome=outcome,
        sample_stem=result.sample_stem,
        evidence=evidence,
        no_repair_terminal=no_repair_terminal,
        authorization_id=(
            effective_subject.authorization_id
            if type(effective_subject) is ObservationRepairCandidate
            else None
        ),
        candidate_id=(
            effective_subject.candidate_id
            if type(effective_subject) is ObservationRepairCandidate
            else None
        ),
    )
    return ObservationRepairReceipt(
        receipt_id=receipt_id,
        subject=effective_subject,
        subject_id=effective_subject.subject_id,
        repair_epoch=effective_subject.repair_epoch,
        outcome=outcome,
        sample_stem=result.sample_stem,
        evidence=evidence,
        no_repair_terminal=no_repair_terminal,
        _mint_token=_RECEIPT_MINT_TOKEN,
    )


def execute_observation_repair(
    *,
    processing_owners: ProcessingOwnerReferences,
    coverage: OwnerBackfillEvaluationCoverageBatch,
    cell_observations: tuple[CellObservationRef, ...],
    execute: Callable[[ObservationRepairCandidate], OwnerBackfillSampleResult],
) -> ObservationRepairTerminalResult:
    """Authorize and execute at most one bounded repair transaction.

    The callback is invoked only for the single eligible candidate.  The
    ``not_run`` and ``multiple_eligible`` branches are terminal blocks and
    cannot produce a repair receipt.
    """

    authorization = authorize_observation_repair(
        processing_owners=processing_owners,
        coverage=coverage,
        cell_observations=cell_observations,
    )
    if type(authorization) is not ObservationRepairRepairAuthorization:
        return ObservationRepairTerminalResult(
            authorization=authorization,
            receipt=None,
            _mint_token=_TERMINAL_RESULT_MINT_TOKEN,
        )
    if not callable(execute):
        raise TypeError("execute must be a callable owner repair operation")
    result = execute(authorization.candidate)
    receipt = _close_observation_repair_after_authorization(
        authorization.candidate,
        result,
    )
    return ObservationRepairTerminalResult(
        authorization=authorization,
        receipt=receipt,
        _mint_token=_TERMINAL_RESULT_MINT_TOKEN,
    )


def _validate_population_cell(
    cell: CellObservationRef,
    *,
    processing_owners: ProcessingOwnerReferences,
    positions_by_key: dict[tuple[str, str], OwnerBackfillPlanningPosition],
    samples_by_id: dict[str, ResolvedCanonicalSampleIdentity],
) -> tuple[str, str, OwnerBackfillPlanningPosition]:
    cell_type = _cell_observation_ref_type()
    if type(cell) is not cell_type:
        raise ObservationRepairContractError(
            "cell population contains a non-owner CellObservationRef"
        )
    try:
        cell.group_context.__post_init__()
        cell.__post_init__()
    except (TypeError, ValueError) as exc:
        raise ObservationRepairContractError(
            "cell population contains a noncanonical CellObservationRef"
        ) from exc
    content = cell.content
    sample_id = content.canonical_sample_identity_id
    sample = samples_by_id.get(sample_id)
    if sample is None:
        raise ObservationRepairContractError(
            "cell population crosses the declared canonical sample universe"
        )
    group_id = content.group_hypothesis_id
    key = (group_id, sample_id)
    position = positions_by_key.get(key)
    if position is None:
        raise ObservationRepairContractError(
            "cell population contains an extra or crossed group/sample position"
        )
    try:
        group_identity = validate_cross_sample_peak_group_identity(
            cell.group_context.group,
            require_canonical=True,
        )
    except (TypeError, ValueError) as exc:
        raise ObservationRepairContractError(
            "cell population group identity is not canonical"
        ) from exc
    if group_identity != position.group_identity:
        raise ObservationRepairContractError(
            "cell population crosses its exact existing group identity"
        )
    context_attempt = cell.group_context.processing_attempt
    try:
        allowed_attempt_ids = {
            processing_owners.attempt.processing_attempt_id,
            *retry_predecessor_attempt_ids(processing_owners.attempt),
        }
    except (TypeError, ValueError) as exc:
        raise ObservationRepairContractError(
            "processing owners do not provide a valid retry lineage"
        ) from exc
    if (
        context_attempt.processing_request_id
        != processing_owners.request.processing_request_id
        or context_attempt.processing_attempt_id not in allowed_attempt_ids
    ):
        raise ObservationRepairContractError(
            "cell population contains a stale processing attempt"
        )
    if (
        cell.artifact.producer_processing_request_id
        != processing_owners.request.processing_request_id
        or cell.artifact.producer_processing_attempt_id not in allowed_attempt_ids
    ):
        raise ObservationRepairContractError(
            "cell population artifact is outside the current attempt or retry lineage"
        )
    assessment = cell.method_assessment
    if type(assessment) is not CidNlMethodAssessmentRef:
        raise ObservationRepairContractError(
            "cell population method assessment is not owner-issued"
        )
    if (
        assessment.content.provider_id != CID_NL_PROVIDER_ID
        or assessment.content.method_id != CID_NL_METHOD_ID
        or assessment.content.method_version != CID_NL_METHOD_VERSION
    ):
        raise ObservationRepairContractError(
            "cell population is outside the active CID-NL tracer"
        )
    method_subject = assessment.content.method_subject
    if type(method_subject) is not CidNlMethodSubject:
        raise ObservationRepairContractError(
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
        raise ObservationRepairContractError(
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
        raise ObservationRepairContractError(
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
            raise ObservationRepairContractError(
                "cell method subject crosses the owner target definition"
            )
        return target
    snapshot = processing_owners.target_snapshot
    if snapshot is None:
        raise ObservationRepairContractError(
            "targeted cell method subject lacks its owner target snapshot"
        )
    definitions = tuple(
        definition
        for definition in snapshot.definitions
        if definition.target_definition_id == method_subject.target_definition_id
        and definition.target_key.target_key_id == method_subject.target_key_id
    )
    if len(definitions) != 1:
        raise ObservationRepairContractError(
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
        raise ObservationRepairContractError(
            "targeted cell method subject is not owner-resolved"
        ) from exc


def _logical_binding_for_cell(
    cell: CellObservationRef,
    *,
    position: OwnerBackfillPlanningPosition,
) -> tuple[OwnerBackfillLogicalRequestMember, OwnerBackfillRequestIdentity]:
    if position.planning_state != "planned":
        raise ObservationRepairContractError(
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
        raise ObservationRepairContractError(
            "eligible cell does not bind exactly one owner Backfill request"
        )
    planned = matches[0]
    return planned.logical_request, planned.request_identity


def _validate_candidate(candidate: ObservationRepairCandidate) -> None:
    if type(candidate.processing_owners) is not ProcessingOwnerReferences:
        raise TypeError("candidate processing_owners must be owner-backed")
    if type(candidate.request_identity) is not OwnerBackfillRequestIdentity:
        raise TypeError("candidate request identity must be owner-minted")
    if type(candidate.logical_request) is not OwnerBackfillLogicalRequestMember:
        raise TypeError("candidate logical request must be owner-minted")
    if type(candidate.predecessor_group) is not CrossSampleGroupIdentity:
        raise TypeError("candidate group must be owner-minted")
    if type(candidate.sample_identity) is not ResolvedCanonicalSampleIdentity:
        raise TypeError("candidate sample must be owner-resolved")
    if type(candidate.coverage_position) is not OwnerBackfillPlanningPosition:
        raise TypeError("candidate coverage position must be owner-minted")
    if type(candidate.repair_epoch) is not int or candidate.repair_epoch != 1:
        raise ObservationRepairContractError("candidate repair epoch must be exactly 1")
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
        raise ObservationRepairContractError(
            "repair candidate crosses exact request identity"
        )
    if (
        candidate.logical_request.group_hypothesis_id
        != candidate.predecessor_group.group_hypothesis_id
    ):
        raise ObservationRepairContractError(
            "repair candidate crosses exact group identity"
        )
    position = candidate.coverage_position
    if position.group_identity != candidate.predecessor_group:
        raise ObservationRepairContractError(
            "repair candidate crosses coverage group identity"
        )
    if position.sample != candidate.sample_identity:
        raise ObservationRepairContractError(
            "repair candidate crosses coverage sample identity"
        )
    if position.planning_state != "planned":
        raise ObservationRepairContractError(
            "repair candidate requires a planned coverage position"
        )
    try:
        resolved_sample = resolve_canonical_sample_identity(
            candidate.sample_identity.snapshot,
            candidate.sample_identity.lookup_token,
            expected_identity=candidate.sample_identity.identity,
        )
    except (TypeError, ValueError) as exc:
        raise ObservationRepairContractError(
            "repair candidate sample resolution is not canonical"
        ) from exc
    if resolved_sample != candidate.sample_identity:
        raise ObservationRepairContractError(
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
        raise ObservationRepairContractError(
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
        raise ObservationRepairContractError(
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
        raise ObservationRepairContractError(
            "repair candidate id is not bound to its complete content"
        )


def _validate_authorization_common(
    authorization: object,
    *,
    state: ObservationRepairAuthorizationState,
    eligible_cell_ids: tuple[str, ...],
) -> None:
    processing_owners = getattr(authorization, "processing_owners", None)
    if type(processing_owners) is not ProcessingOwnerReferences:
        raise TypeError("authorization processing_owners must be owner-backed")
    coverage_id = getattr(authorization, "coverage_id", None)
    if type(coverage_id) is not str or not coverage_id:
        raise ObservationRepairContractError("authorization coverage_id is required")
    _validate_typed_id(
        coverage_id,
        _COVERAGE_SEMANTICS_PREFIX,
        "coverage_id",
    )
    population_cell_ids = getattr(authorization, "population_cell_ids", None)
    if type(population_cell_ids) is not tuple or not population_cell_ids:
        raise ObservationRepairContractError(
            "authorization requires a complete non-empty cell population"
        )
    if any(
        type(cell_id) is not str
        or not cell_id.startswith(_CELL_PREFIX)
        or not _TYPED_ID_PATTERN.fullmatch(cell_id)
        for cell_id in population_cell_ids
    ):
        raise ObservationRepairContractError(
            "authorization cell identities are not typed"
        )
    if len(set(population_cell_ids)) != len(population_cell_ids):
        raise ObservationRepairContractError(
            "authorization population duplicates a cell identity"
        )
    if type(eligible_cell_ids) is not tuple:
        raise TypeError("authorization eligible cell identities must be a tuple")
    if len(set(eligible_cell_ids)) != len(eligible_cell_ids):
        raise ObservationRepairContractError(
            "authorization eligible identities are duplicated"
        )
    if any(cell_id not in population_cell_ids for cell_id in eligible_cell_ids):
        raise ObservationRepairContractError(
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
        raise ObservationRepairContractError(
            "repair authorization id is not bound to its complete content"
        )


def _require_current_repair_authorization(
    authorization: ObservationRepairRepairAuthorization | None,
    *,
    candidate: ObservationRepairCandidate,
    coverage: OwnerBackfillEvaluationCoverageBatch | None,
    cell_observations: tuple[CellObservationRef, ...] | None,
) -> None:
    if type(authorization) is not ObservationRepairRepairAuthorization:
        raise ObservationRepairContractError(
            "repair candidate requires its exact current authorization"
        )
    if coverage is None or cell_observations is None:
        raise ObservationRepairContractError(
            "repair candidate close requires the complete current population"
        )
    current = authorize_observation_repair(
        processing_owners=authorization.processing_owners,
        coverage=coverage,
        cell_observations=cell_observations,
    )
    if type(current) is not ObservationRepairRepairAuthorization:
        raise ObservationRepairContractError(
            "repair authorization is no longer a current single candidate"
        )
    if current != authorization or current.candidate != candidate:
        raise ObservationRepairContractError(
            "repair authorization or candidate is not current"
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
        raise ObservationRepairContractError(f"{field} is not a typed identity")


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
    coverage: OwnerBackfillEvaluationCoverageBatch,
) -> str:
    """Bind coverage facts while deliberately dropping producer selection flags."""

    return typed_identity_digest(
        _COVERAGE_SEMANTICS_PREFIX,
        canonical_identity_basis_bytes(
            {
                "schema_version": "observation_repair_coverage_semantics_v1",
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
    terminal: OwnerBackfillAttemptTerminal,
) -> dict[str, object]:
    if type(terminal) is not OwnerBackfillAttemptTerminal:
        raise ObservationRepairContractError("coverage terminal is not owner-minted")
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
    coverage: OwnerBackfillEvaluationCoverageBatch | None = None,
    coverage_id: str | None = None,
    population_cell_ids: tuple[str, ...],
    eligible_cell_ids: tuple[str, ...],
    state: ObservationRepairAuthorizationState,
) -> str:
    if coverage is not None:
        coverage_id = _coverage_semantics_id(coverage)
    if coverage_id is None:
        raise ObservationRepairContractError("authorization requires coverage identity")
    return typed_identity_digest(
        _AUTHORIZATION_PREFIX,
        canonical_identity_basis_bytes(
            {
                "schema_version": "observation_repair_authorization_v1",
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
    request_identity: OwnerBackfillRequestIdentity,
    logical_request: OwnerBackfillLogicalRequestMember,
    predecessor_group: CrossSampleGroupIdentity,
    sample_identity: ResolvedCanonicalSampleIdentity,
    cell: CellObservationRef,
    coverage_position: OwnerBackfillPlanningPosition,
    repair_epoch: int,
) -> str:
    return typed_identity_digest(
        _CANDIDATE_PREFIX,
        canonical_identity_basis_bytes(
            {
                "schema_version": "observation_repair_candidate_v1",
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


def _validate_subject(
    subject: ObservationRepairSubject | ObservationRepairCandidate,
) -> None:
    if type(subject) is ObservationRepairCandidate:
        _validate_candidate(subject)
        return
    if type(subject) is not ObservationRepairSubject:
        raise TypeError("subject must be owner-minted")
    if type(subject.request_identity) is not OwnerBackfillRequestIdentity:
        raise TypeError("request identity must be owner-minted")
    if type(subject.logical_request) is not OwnerBackfillLogicalRequestMember:
        raise TypeError("logical request must be owner-minted")
    if type(subject.predecessor_group) is not CrossSampleGroupIdentity:
        raise TypeError("predecessor group must be owner-minted")
    if type(subject.member_subject) is not CrossSampleGroupMemberSubject:
        raise TypeError("member subject must be owner-minted")
    if type(subject.sample_identity) is not ResolvedCanonicalSampleIdentity:
        raise TypeError("sample identity must be owner-resolved")
    if type(subject.successor_application) is not CrossSampleSuccessorApplication:
        raise TypeError("successor application must be CrossSample-minted")
    replayed_application = CrossSampleSuccessorApplication(
        status=subject.successor_application.status,
        predecessors=subject.successor_application.predecessors,
        successors=subject.successor_application.successors,
        lineage=subject.successor_application.lineage,
        reason=subject.successor_application.reason,
    )
    if replayed_application != subject.successor_application:
        raise ObservationRepairContractError(
            "repair subject successor application is not canonical"
        )
    resolved_sample = resolve_canonical_sample_identity(
        subject.sample_identity.snapshot,
        subject.sample_identity.lookup_token,
        expected_identity=subject.sample_identity.identity,
    )
    if resolved_sample != subject.sample_identity:
        raise ObservationRepairContractError(
            "repair subject sample resolution is not canonical"
        )
    replayed_group = mint_cross_sample_group_identity(
        processing_request_id=subject.predecessor_group.processing_request_id,
        member_subjects=subject.predecessor_group.member_subjects,
        disposition=subject.predecessor_group.disposition,
    )
    if replayed_group != subject.predecessor_group:
        raise ObservationRepairContractError(
            "repair subject predecessor group is not canonical"
        )
    if type(subject.repair_epoch) is not int or subject.repair_epoch != 1:
        raise ObservationRepairContractError("repair epoch must be exactly 1")
    request_binding = (
        subject.logical_request.item.sample_stem,
        subject.logical_request.item.request,
    )
    if request_binding != (
        subject.request_identity.sample_stem,
        subject.request_identity.request,
    ):
        raise ObservationRepairContractError(
            "repair subject crosses exact request identity"
        )
    if (
        subject.logical_request.group_hypothesis_id
        != subject.predecessor_group.group_hypothesis_id
    ):
        raise ObservationRepairContractError(
            "repair subject crosses predecessor group identity"
        )
    if subject.member_subject not in subject.predecessor_group.member_subjects:
        raise ObservationRepairContractError(
            "repair subject member is absent from predecessor group"
        )
    if subject.predecessor_group not in subject.successor_application.successors:
        raise ObservationRepairContractError(
            "repair subject group is absent from successor application"
        )
    if (
        subject.sample_identity.identity.sample_identity_id
        != subject.member_subject.canonical_sample_identity_id
    ):
        raise ObservationRepairContractError(
            "repair subject member crosses canonical sample identity"
        )
    if (
        subject.sample_identity.metadata.raw_stem
        != subject.request_identity.sample_stem
    ):
        raise ObservationRepairContractError(
            "repair subject sample metadata crosses exact Backfill sample"
        )
    if (
        subject.member_subject.processing_request_id
        != subject.predecessor_group.processing_request_id
    ):
        raise ObservationRepairContractError(
            "repair subject member crosses processing request"
        )
    expected = _subject_id(
        request_identity=subject.request_identity,
        logical_request=subject.logical_request,
        predecessor_group=subject.predecessor_group,
        member_subject=subject.member_subject,
        sample_identity=subject.sample_identity,
        successor_application=subject.successor_application,
        repair_epoch=subject.repair_epoch,
    )
    if subject.subject_id != expected:
        raise ObservationRepairContractError(
            "repair subject id is not bound to its complete content"
        )


def _normalize_live_evidence(
    subject: ObservationRepairSubject | ObservationRepairCandidate,
    observation: OwnerBackfillPeakFactObservation,
) -> ObservationRepairEvidence | None:
    if observation.cell.sample_stem != subject.request_identity.sample_stem:
        raise ObservationRepairContractError("repair evidence crosses sample identity")
    if (
        observation.cell.group_hypothesis_id
        != subject.predecessor_group.group_hypothesis_id
    ):
        return None
    validate_owner_backfill_peak_fact_observation(observation)
    if observation.request_identity != subject.request_identity:
        raise ObservationRepairContractError("repair evidence crosses request identity")
    if observation.logical_request != subject.logical_request:
        return None
    if not observation.selected_for_output:
        if not observation.trace_evaluated or observation.physical_evaluation is None:
            raise ObservationRepairContractError(
                "unselected repair evidence is not a completed evaluation"
            )
        return None
    evaluation = observation.physical_evaluation
    receipt = observation.facts
    if evaluation is None:
        raise ObservationRepairContractError(
            "selected repair evidence is partial or unavailable"
        )
    if evaluation.facts is None and receipt is None:
        if _is_completed_no_signal_evaluation(observation):
            return None
        raise ObservationRepairContractError(
            "selected repair evidence is partial or unavailable"
        )
    if evaluation.facts is None or receipt is None:
        raise ObservationRepairContractError(
            "selected repair evidence is partial or unavailable"
        )
    facts = evaluation.facts
    integration_id = selected_integration_authority_projection(
        receipt.integration
    ).selected_integration_authority_content_id
    return _evidence(
        subject,
        request_identity_id=observation.request_identity.request_identity_id,
        group_hypothesis_id=observation.cell.group_hypothesis_id,
        sample_stem=observation.cell.sample_stem,
        work_unit_key=observation.work_unit_key,
        candidate_index=observation.candidate_index,
        candidate_phase=observation.candidate_phase,
        acquisition_source_id=(
            observation.request_identity.acquisition_source.acquisition_source_id
        ),
        selection_subject_id=evaluation.question.selection_subject_id,
        physical_question_id=facts.physical_question_id,
        physical_answer_id=facts.physical_answer_id,
        physical_segment_id=facts.physical_segment_id,
        fact_content_id=facts.fact_content_id,
        selected_integration_authority_content_id=integration_id,
        rt_left_min=facts.left_rt_min,
        rt_apex_min=facts.apex_rt_min,
        rt_right_min=facts.right_rt_min,
        height=facts.height,
        scan_support_count=facts.scan_support_count,
        area_counts_seconds=facts.area_counts_seconds,
        area_source=facts.area_source,
    )


def _is_completed_no_signal_evaluation(
    observation: OwnerBackfillPeakFactObservation,
) -> bool:
    evaluation = observation.physical_evaluation
    if evaluation is None:
        return False
    return (
        observation.trace_evaluated
        and evaluation.state == "unavailable"
        and evaluation.reason == "no_signal_above_baseline"
        and evaluation.prepared.state == "unavailable"
        and evaluation.prepared.reason == "no_signal_above_baseline"
        and evaluation.segment_universe.state == "unavailable"
        and evaluation.segment_universe.reason == "no_signal_above_baseline"
        and not evaluation.segment_universe.segments
        and evaluation.answer.state == "unavailable"
        and evaluation.answer.reason == "no_signal_above_baseline"
        and evaluation.answer.physical_segment_id is None
        and evaluation.operations == ("trace_evaluation",)
    )


def _mint_no_repair_terminal(
    subject: ObservationRepairCandidate,
    observation: OwnerBackfillPeakFactObservation,
) -> ObservationRepairNoRepairTerminal:
    """Project one exact live terminal without recomputing scientific facts."""

    validate_owner_backfill_peak_fact_observation(observation)
    evaluation = observation.physical_evaluation
    if not observation.trace_evaluated or evaluation is None:
        raise ObservationRepairContractError(
            "no_repair terminal requires a completed physical evaluation"
        )
    if observation.selected_for_output and not _is_completed_no_signal_evaluation(
        observation
    ):
        raise ObservationRepairContractError(
            "selected no_repair terminal is not a completed no-signal evaluation"
        )
    terminal_id = _no_repair_terminal_id(
        subject_id=subject.subject_id,
        authorization_id=subject.authorization_id,
        candidate_id=subject.candidate_id,
        repair_epoch=subject.repair_epoch,
        work_unit_key=observation.work_unit_key,
        physical_answer_id=evaluation.answer.physical_answer_id,
    )
    return ObservationRepairNoRepairTerminal(
        terminal_id=terminal_id,
        subject_id=subject.subject_id,
        authorization_id=subject.authorization_id,
        candidate_id=subject.candidate_id,
        repair_epoch=subject.repair_epoch,
        work_unit_key=observation.work_unit_key,
        physical_answer_id=evaluation.answer.physical_answer_id,
        _mint_token=_NO_REPAIR_TERMINAL_MINT_TOKEN,
    )


def _evidence(
    subject: ObservationRepairSubject | ObservationRepairCandidate,
    *,
    request_identity_id: str,
    group_hypothesis_id: str,
    sample_stem: str,
    work_unit_key: str,
    candidate_index: int,
    candidate_phase: str,
    acquisition_source_id: str,
    selection_subject_id: str,
    physical_question_id: str,
    physical_answer_id: str,
    physical_segment_id: str,
    fact_content_id: str,
    selected_integration_authority_content_id: str,
    rt_left_min: float,
    rt_apex_min: float,
    rt_right_min: float,
    height: float,
    scan_support_count: int,
    area_counts_seconds: float,
    area_source: str,
) -> ObservationRepairEvidence:
    binding = (
        request_identity_id,
        group_hypothesis_id,
        sample_stem,
        acquisition_source_id,
    )
    expected = (
        subject.request_identity.request_identity_id,
        subject.predecessor_group.group_hypothesis_id,
        subject.request_identity.sample_stem,
        subject.request_identity.acquisition_source.acquisition_source_id,
    )
    if binding != expected:
        if request_identity_id != expected[0] or acquisition_source_id != expected[3]:
            raise ObservationRepairContractError(
                "repair evidence crosses request identity"
            )
        if group_hypothesis_id != expected[1]:
            raise ObservationRepairContractError(
                "repair evidence crosses group identity"
            )
        raise ObservationRepairContractError("repair evidence crosses sample identity")
    return ObservationRepairEvidence(
        logical_request_id=subject.logical_request.logical_request_id,
        request_identity_id=request_identity_id,
        group_hypothesis_id=group_hypothesis_id,
        member_subject_id=(
            None
            if subject.member_subject is None
            else subject.member_subject.member_subject_id
        ),
        sample_stem=sample_stem,
        work_unit_key=work_unit_key,
        candidate_index=candidate_index,
        candidate_phase=candidate_phase,
        acquisition_source_id=acquisition_source_id,
        selection_subject_id=selection_subject_id,
        physical_question_id=physical_question_id,
        physical_answer_id=physical_answer_id,
        physical_segment_id=physical_segment_id,
        fact_content_id=fact_content_id,
        selected_integration_authority_content_id=(
            selected_integration_authority_content_id
        ),
        rt_left_min=rt_left_min,
        rt_apex_min=rt_apex_min,
        rt_right_min=rt_right_min,
        height=height,
        scan_support_count=scan_support_count,
        area_counts_seconds=area_counts_seconds,
        area_source=area_source,
    )


def _subject_id(
    *,
    request_identity: OwnerBackfillRequestIdentity,
    logical_request: OwnerBackfillLogicalRequestMember,
    predecessor_group: CrossSampleGroupIdentity,
    member_subject: CrossSampleGroupMemberSubject,
    sample_identity: ResolvedCanonicalSampleIdentity,
    successor_application: CrossSampleSuccessorApplication,
    repair_epoch: int,
) -> str:
    return typed_identity_digest(
        _SUBJECT_PREFIX,
        canonical_identity_basis_bytes(
            {
                "schema_version": "observation_repair_subject_v1",
                "request_identity_id": request_identity.request_identity_id,
                "logical_request_id": logical_request.logical_request_id,
                "predecessor_group_hypothesis_id": (
                    predecessor_group.group_hypothesis_id
                ),
                "member_subject_id": member_subject.member_subject_id,
                "canonical_sample_identity_id": (
                    sample_identity.identity.sample_identity_id
                ),
                "sample_metadata_snapshot_id": sample_identity.snapshot.snapshot_id,
                "sample_raw_stem": sample_identity.metadata.raw_stem,
                "successor_application": _successor_application_basis(
                    successor_application
                ),
                "repair_epoch": repair_epoch,
            }
        ),
    )


def _successor_application_basis(
    application: CrossSampleSuccessorApplication,
) -> dict[str, object]:
    return {
        "status": application.status,
        "reason": application.reason,
        "predecessors": [
            _group_identity_basis(group) for group in application.predecessors
        ],
        "successors": [
            _group_identity_basis(group) for group in application.successors
        ],
        "lineage": [
            {
                "lineage_edge_id": edge.lineage_edge_id,
                "predecessor_group_hypothesis_id": (
                    edge.predecessor_group_hypothesis_id
                ),
                "successor_group_hypothesis_id": edge.successor_group_hypothesis_id,
                "processing_request_id": edge.processing_request_id,
                "predecessor_identity_basis_hex": (
                    edge.predecessor_identity_basis_bytes.hex()
                ),
                "predecessor_group_hypothesis_ids": list(
                    edge.predecessor_group_hypothesis_ids
                ),
                "member_subject_ids": list(edge.member_subject_ids),
                "basis_hex": edge.basis_bytes.hex(),
            }
            for edge in application.lineage
        ],
    }


def _group_identity_basis(group: CrossSampleGroupIdentity) -> dict[str, object]:
    return {
        "group_hypothesis_id": group.group_hypothesis_id,
        "processing_request_id": group.processing_request_id,
        "member_subject_ids": [
            member.member_subject_id for member in group.member_subjects
        ],
        "basis_hex": group.basis_bytes.hex(),
        "disposition": group.disposition,
    }


def _no_repair_terminal_id(
    *,
    subject_id: str,
    authorization_id: str,
    candidate_id: str,
    repair_epoch: int,
    work_unit_key: str,
    physical_answer_id: str,
) -> str:
    return typed_identity_digest(
        _NO_REPAIR_TERMINAL_PREFIX,
        canonical_identity_basis_bytes(
            {
                "schema_version": "observation_repair_no_repair_terminal_v1",
                "subject_id": subject_id,
                "authorization_id": authorization_id,
                "candidate_id": candidate_id,
                "repair_epoch": repair_epoch,
                "work_unit_key": work_unit_key,
                "physical_answer_id": physical_answer_id,
            }
        ),
    )


def _validate_no_repair_terminal(
    terminal: ObservationRepairNoRepairTerminal,
) -> None:
    if terminal.authorization_id is None or terminal.candidate_id is None:
        raise ObservationRepairContractError("no-repair terminal is candidate-scoped")
    _validate_typed_id(
        terminal.terminal_id,
        _NO_REPAIR_TERMINAL_PREFIX,
        "no-repair terminal_id",
    )
    _validate_typed_id(
        terminal.authorization_id,
        _AUTHORIZATION_PREFIX,
        "no-repair authorization_id",
    )
    _validate_typed_id(
        terminal.candidate_id,
        _CANDIDATE_PREFIX,
        "no-repair candidate_id",
    )
    if terminal.subject_id != terminal.candidate_id:
        raise ObservationRepairContractError(
            "candidate no-repair terminal crosses its subject"
        )
    if terminal.repair_epoch != 1:
        raise ObservationRepairContractError(
            "no-repair terminal repair epoch must be exactly 1"
        )
    if type(terminal.work_unit_key) is not str or not terminal.work_unit_key:
        raise ObservationRepairContractError(
            "no-repair terminal work_unit_key is required"
        )
    _validate_typed_id(
        terminal.physical_answer_id,
        _PHYSICAL_ANSWER_PREFIX,
        "no-repair physical_answer_id",
    )
    expected = _no_repair_terminal_id(
        subject_id=terminal.subject_id,
        authorization_id=terminal.authorization_id,
        candidate_id=terminal.candidate_id,
        repair_epoch=terminal.repair_epoch,
        work_unit_key=terminal.work_unit_key,
        physical_answer_id=terminal.physical_answer_id,
    )
    if terminal.terminal_id != expected:
        raise ObservationRepairContractError(
            "no-repair terminal id is not bound to its complete content"
        )


def _receipt_id(
    *,
    subject_id: str,
    repair_epoch: int,
    outcome: ObservationRepairOutcome,
    sample_stem: str,
    evidence: ObservationRepairEvidence | None,
    no_repair_terminal: ObservationRepairNoRepairTerminal | None = None,
    authorization_id: str | None = None,
    candidate_id: str | None = None,
) -> str:
    if (authorization_id is None) != (candidate_id is None):
        raise ObservationRepairContractError(
            "candidate receipt identity requires authorization and candidate ids"
        )
    candidate_receipt = candidate_id is not None
    if not candidate_receipt and no_repair_terminal is not None:
        raise ObservationRepairContractError("no-repair terminal is candidate-scoped")
    evidence_basis: object = None
    if evidence is not None:
        evidence_basis = {
            name: getattr(evidence, name)
            for name in ObservationRepairEvidence.__dataclass_fields__
        }
    payload: dict[str, object] = {
        "schema_version": (
            "observation_repair_receipt_v2"
            if candidate_receipt
            else "observation_repair_receipt_v1"
        ),
        "subject_id": subject_id,
        "repair_epoch": repair_epoch,
        "outcome": outcome,
        "sample_stem": sample_stem,
        "evidence": evidence_basis,
    }
    if candidate_receipt:
        payload["authorization_id"] = authorization_id
        payload["candidate_id"] = candidate_id
    if no_repair_terminal is not None:
        payload["no_repair_terminal"] = {
            "terminal_id": no_repair_terminal.terminal_id,
            "subject_id": no_repair_terminal.subject_id,
            "authorization_id": no_repair_terminal.authorization_id,
            "candidate_id": no_repair_terminal.candidate_id,
            "repair_epoch": no_repair_terminal.repair_epoch,
            "work_unit_key": no_repair_terminal.work_unit_key,
            "physical_answer_id": no_repair_terminal.physical_answer_id,
        }
    return typed_identity_digest(
        _CANDIDATE_RECEIPT_PREFIX if candidate_receipt else _RECEIPT_PREFIX,
        canonical_identity_basis_bytes(payload),
    )


__all__ = (
    "ObservationRepairAuthorization",
    "ObservationRepairAuthorizationState",
    "ObservationRepairContractError",
    "ObservationRepairCandidate",
    "ObservationRepairEvidence",
    "ObservationRepairMultipleEligibleAuthorization",
    "ObservationRepairNoRepairTerminal",
    "ObservationRepairNotRunAuthorization",
    "ObservationRepairOutcome",
    "ObservationRepairRepairAuthorization",
    "ObservationRepairReceipt",
    "ObservationRepairSubject",
    "ObservationRepairTerminalResult",
    "authorize_observation_repair",
    "close_observation_repair",
    "execute_observation_repair",
    "mint_observation_repair_subject",
)
