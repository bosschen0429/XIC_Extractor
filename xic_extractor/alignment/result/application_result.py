"""Immutable application-result closure over completed workflow projections."""

from __future__ import annotations

import json
from dataclasses import InitVar, dataclass, field
from typing import TYPE_CHECKING, Literal, TypeAlias

from xic_extractor.alignment.grouping_result import (
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    FinalizedScientificAlignmentContractError,
    _validate_finalized_scientific_alignment_completion_receipt,
)
from xic_extractor.discovery.batch_contract import (
    ALIGNMENT_FEATURE_INPUT_KEY,
    CANDIDATE_UNIVERSE_INPUT_KEY,
    validate_candidate_universe_payload,
)
from xic_extractor.discovery.models import (
    DISCOVERY_CANDIDATE_STATE_VALUES,
    MS1_FEATURE_BACKED_STATES,
    DiscoveryCandidateState,
)
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    is_unsearched_cid_nl_discovery_assessment,
    validate_cid_nl_method_assessment_binding,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubjectContractError,
    cid_nl_alignment_subject_worksets,
    cid_nl_method_subject_batch_from_request,
    validate_cid_nl_alignment_subject_compatibility,
)
from xic_extractor.peak_detection.search_scope import (
    TargetSearchScope,
    UntargetedSearchScope,
)
from xic_extractor.processing_identity import (
    IdentityContractError,
    ProcessingAttemptIdentity,
    ProcessingOwnerReferences,
    ProcessingRequestIdentity,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

if TYPE_CHECKING:
    from xic_extractor.alignment.projection.target.post_cell import (
        TargetPostCellProjection,
    )
    from xic_extractor.alignment.projection.untarget.post_cell import (
        UntargetPostCellProjection,
    )


class ApplicationResultContractError(ValueError):
    """Raised when application-result owners cannot close exactly."""


_APPLICATION_RESULT_MINT_TOKEN = object()


def _is_evaluated_unsupported_assessment(ref: CidNlMethodAssessmentRef) -> bool:
    return ref.content.technical_state == "evaluated" and ref.content.method_result in {
        "HOLD",
        "REJECT",
    }


def _is_method_exclusion_coverage(ref: CidNlMethodAssessmentRef) -> bool:
    return _is_evaluated_unsupported_assessment(
        ref
    ) or is_unsearched_cid_nl_discovery_assessment(ref)


@dataclass(frozen=True, slots=True)
class UntargetedCandidateExclusion:
    """One declared Discovery candidate intentionally excluded from owner-build."""

    candidate_id: str
    method_subject_id: str
    canonical_sample_identity_id: str
    discovery_candidate_state: DiscoveryCandidateState
    reason_code: Literal[
        "discovery_candidate_not_search_eligible",
        "composite_requirement_not_supported",
        "cid_nl_requirement_not_supported",
    ] = "discovery_candidate_not_search_eligible"
    composite_assessments: tuple[CidNlMethodAssessmentRef, ...] = ()
    method_assessments: tuple[CidNlMethodAssessmentRef, ...] = ()

    def __post_init__(self) -> None:
        for field_name, value in (
            ("candidate_id", self.candidate_id),
            ("method_subject_id", self.method_subject_id),
            ("canonical_sample_identity_id", self.canonical_sample_identity_id),
        ):
            if type(value) is not str or not value or value != value.strip():
                raise ApplicationResultContractError(
                    f"Untargeted candidate exclusion {field_name} is invalid"
                )
        if self.discovery_candidate_state not in DISCOVERY_CANDIDATE_STATE_VALUES:
            raise ApplicationResultContractError("Invalid excluded Discovery state")
        if (
            type(self.composite_assessments) is not tuple
            or type(self.method_assessments) is not tuple
        ):
            raise ApplicationResultContractError(
                "Exclusion assessment proofs must be immutable tuples"
            )
        if self.reason_code == "composite_requirement_not_supported":
            if self.method_assessments:
                raise ApplicationResultContractError(
                    "Composite exclusion cannot carry method assessments"
                )
            refs = self.composite_assessments
            if (
                type(refs) is not tuple
                or not refs
                or any(
                    type(ref) is not CidNlMethodAssessmentRef
                    or ref.content.method_subject.composition is None
                    or ref.content.method_subject.method_subject_id
                    != self.method_subject_id
                    or not _is_method_exclusion_coverage(ref)
                    for ref in refs
                )
                or not any(_is_evaluated_unsupported_assessment(ref) for ref in refs)
                or (
                    self.discovery_candidate_state in MS1_FEATURE_BACKED_STATES
                    and not any(
                        ref.content.canonical_sample_identity_id
                        == self.canonical_sample_identity_id
                        and _is_evaluated_unsupported_assessment(ref)
                        for ref in refs
                    )
                )
            ):
                raise ApplicationResultContractError(
                    "Composite exclusion requires evaluated unsupported assessments, "
                    "with exact no-input assessments allowed only as coverage"
                )
            return
        if self.reason_code == "cid_nl_requirement_not_supported":
            refs = self.method_assessments
            if (
                self.composite_assessments
                or type(refs) is not tuple
                or not refs
                or any(
                    type(ref) is not CidNlMethodAssessmentRef
                    or ref.content.method_subject.composition is not None
                    or ref.content.method_subject.subject_kind != "untargeted_precursor"
                    or ref.content.method_subject.method_subject_id
                    != self.method_subject_id
                    or not _is_method_exclusion_coverage(ref)
                    for ref in refs
                )
                or not any(_is_evaluated_unsupported_assessment(ref) for ref in refs)
                or (
                    self.discovery_candidate_state in MS1_FEATURE_BACKED_STATES
                    and not any(
                        ref.content.canonical_sample_identity_id
                        == self.canonical_sample_identity_id
                        and _is_evaluated_unsupported_assessment(ref)
                        for ref in refs
                    )
                )
            ):
                raise ApplicationResultContractError(
                    "CID-NL exclusion requires evaluated unsupported single-method "
                    "assessments, with exact no-input assessments allowed only as "
                    "coverage, and no composite proof"
                )
            return
        if self.discovery_candidate_state in MS1_FEATURE_BACKED_STATES:
            raise ApplicationResultContractError(
                "Untargeted candidate exclusion requires a non-feature-backed "
                "Discovery state"
            )
        if (
            self.reason_code != "discovery_candidate_not_search_eligible"
            or self.composite_assessments
            or self.method_assessments
        ):
            raise ApplicationResultContractError(
                "Untargeted candidate exclusion reason is unsupported"
            )


@dataclass(frozen=True, slots=True)
class TargetApplicationResultRequest:
    scientific_core: FinalizedScientificAlignment
    completed_projection: TargetPostCellProjection

    def __post_init__(self) -> None:
        _validate_target_request(self)

    @property
    def variant(self) -> Literal["targeted"]:
        return "targeted"


@dataclass(frozen=True, slots=True)
class UntargetApplicationResultRequest:
    scientific_core: FinalizedScientificAlignment
    completed_projection: UntargetPostCellProjection

    def __post_init__(self) -> None:
        _validate_untarget_request(self)

    @property
    def variant(self) -> Literal["untargeted"]:
        return "untargeted"


@dataclass(frozen=True, slots=True)
class UntargetBatchApplicationResultRequest:
    """Request to seal one complete multi-subject Untarget result."""

    scope: UntargetedSearchScope
    processing_request: ProcessingRequestIdentity
    processing_attempt: ProcessingAttemptIdentity
    children: (
        tuple[FinalizedUntargetApplicationResult, ...]
        | ImmutableSequence[FinalizedUntargetApplicationResult]
    )
    excluded_candidates: (
        tuple[UntargetedCandidateExclusion, ...]
        | ImmutableSequence[UntargetedCandidateExclusion]
    ) = ()

    def __post_init__(self) -> None:
        _validate_untarget_batch_request(self)

    @property
    def variant(self) -> Literal["untargeted_batch"]:
        return "untargeted_batch"


ApplicationResultRequest: TypeAlias = (
    TargetApplicationResultRequest
    | UntargetApplicationResultRequest
    | UntargetBatchApplicationResultRequest
)


@dataclass(frozen=True, slots=True)
class FinalizedTargetApplicationResult:
    scientific_core: FinalizedScientificAlignment
    completed_projection: TargetPostCellProjection
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _require_result_mint(_mint_token)
        _validate_target_result(self)

    @property
    def variant(self) -> Literal["targeted"]:
        return "targeted"

    @property
    def processing_request(self) -> ProcessingRequestIdentity:
        return self.scientific_core.processing_request

    @property
    def canonical_samples(
        self,
    ) -> tuple[ResolvedCanonicalSampleIdentity, ...]:
        return self.scientific_core.canonical_samples

    @property
    def completion_receipts(
        self,
    ) -> tuple[CrossSampleGroupingResult, TargetPostCellProjection]:
        return (
            self.scientific_core.grouping_result,
            self.completed_projection,
        )


@dataclass(frozen=True, slots=True)
class FinalizedUntargetApplicationResult:
    scientific_core: FinalizedScientificAlignment
    completed_projection: UntargetPostCellProjection
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        # Application owner entry points close the result before returning it.
        _require_result_mint(_mint_token)

    @property
    def variant(self) -> Literal["untargeted"]:
        return "untargeted"

    @property
    def processing_request(self) -> ProcessingRequestIdentity:
        return self.scientific_core.processing_request

    @property
    def canonical_samples(
        self,
    ) -> tuple[ResolvedCanonicalSampleIdentity, ...]:
        return self.scientific_core.canonical_samples

    @property
    def completion_receipts(
        self,
    ) -> tuple[CrossSampleGroupingResult, UntargetPostCellProjection]:
        return (
            self.scientific_core.grouping_result,
            self.completed_projection,
        )


@dataclass(frozen=True, slots=True)
class FinalizedUntargetBatchApplicationResult:
    """One immutable batch handoff over exact Untarget workset results.

    The batch result owns only complete child results.  It does not regroup
    rows, select values, or synthesize missing evidence; those decisions stay
    with the existing workset-local owners.
    """

    scope: UntargetedSearchScope
    processing_request: ProcessingRequestIdentity
    processing_attempt: ProcessingAttemptIdentity
    children: (
        tuple[FinalizedUntargetApplicationResult, ...]
        | ImmutableSequence[FinalizedUntargetApplicationResult]
    )
    excluded_candidates: (
        tuple[UntargetedCandidateExclusion, ...]
        | ImmutableSequence[UntargetedCandidateExclusion]
    )
    _mint_token: InitVar[object]

    def __post_init__(self, _mint_token: object) -> None:
        # The owner transfers the validated request's exact immutable fields.
        _require_result_mint(_mint_token)

    @property
    def variant(self) -> Literal["untargeted_batch"]:
        return "untargeted_batch"

    @property
    def application_results(
        self,
    ) -> (
        tuple[FinalizedUntargetApplicationResult, ...]
        | ImmutableSequence[FinalizedUntargetApplicationResult]
    ):
        return self.children

    @property
    def canonical_samples(
        self,
    ) -> tuple[ResolvedCanonicalSampleIdentity, ...]:
        # Batch closure already checked every child's exact sample universe.
        # Sample metadata does not require restoring a stored scientific child.
        return tuple(sorted(self.scope.samples, key=_sample_sort_key))

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        subject_batch = cid_nl_method_subject_batch_from_request(
            self.processing_request
        )
        return tuple(
            candidate_id
            for provider in self.scope.evidence_providers
            for candidate_id, method_subject_id in subject_batch.candidate_memberships
            if method_subject_id == provider.method_subject_id
        )

    @property
    def subject_ids(self) -> tuple[str, ...]:
        return tuple(
            provider.method_subject_id for provider in self.scope.evidence_providers
        )

    @property
    def physical_observation_ids(self) -> tuple[str, ...]:
        return tuple(
            physical_id
            for child in self.children
            for group in child.scientific_core.final_population
            for member in group.member_subjects
            for physical_id in member.physical_observation_ids
        )

    @property
    def rows(self) -> tuple[object, ...]:
        return tuple(
            row for child in self.children for row in child.completed_projection.rows
        )

    @property
    def included_rows(self) -> tuple[object, ...]:
        return tuple(
            row
            for child in self.children
            for row in child.completed_projection.included_rows
        )

    @property
    def archive_rows(self) -> tuple[object, ...]:
        return tuple(
            row
            for child in self.children
            for row in child.completed_projection.archive_rows
        )

    @property
    def decisions(self) -> tuple[object, ...]:
        return tuple(
            decision
            for child in self.children
            for decision in child.completed_projection.decisions
        )

    @property
    def lineage(self) -> tuple[object, ...]:
        return tuple(
            edge for child in self.children for edge in child.scientific_core.lineage
        )


FinalizedApplicationResult: TypeAlias = (
    FinalizedTargetApplicationResult
    | FinalizedUntargetApplicationResult
    | FinalizedUntargetBatchApplicationResult
)


def finalize_alignment_result(
    request: ApplicationResultRequest,
) -> FinalizedApplicationResult:
    """Seal one scientific core and one matching completed projection."""

    if type(request) is TargetApplicationResultRequest:
        _validate_target_request(request)
        core = request.scientific_core
        return FinalizedTargetApplicationResult(
            scientific_core=core,
            completed_projection=request.completed_projection,
            _mint_token=_APPLICATION_RESULT_MINT_TOKEN,
        )
    if type(request) is UntargetApplicationResultRequest:
        _validate_untarget_request(request)
        core = request.scientific_core
        result = FinalizedUntargetApplicationResult(
            scientific_core=core,
            completed_projection=request.completed_projection,
            _mint_token=_APPLICATION_RESULT_MINT_TOKEN,
        )
        _validate_untarget_result(result)
        return result
    if type(request) is UntargetBatchApplicationResultRequest:
        # Construction already closes the batch; this handoff adds no new input.
        return FinalizedUntargetBatchApplicationResult(
            scope=request.scope,
            processing_request=request.processing_request,
            processing_attempt=request.processing_attempt,
            children=request.children,
            excluded_candidates=request.excluded_candidates,
            _mint_token=_APPLICATION_RESULT_MINT_TOKEN,
        )
    raise ApplicationResultContractError(
        "application result requires an exact discriminated request variant"
    )


def _finalize_fresh_untarget_result(
    request: UntargetApplicationResultRequest,
) -> FinalizedUntargetApplicationResult:
    """Transfer owners just completed in the callback-free workflow closure."""

    _validate_untarget_request(request)
    result = FinalizedUntargetApplicationResult(
        scientific_core=request.scientific_core,
        completed_projection=request.completed_projection,
        _mint_token=_APPLICATION_RESULT_MINT_TOKEN,
    )
    _validate_untarget_result(result, _reuse_fresh_completion=True)
    return result


def _validate_target_request(request: TargetApplicationResultRequest) -> None:
    from xic_extractor.alignment.projection.target.post_cell import (
        TargetPostCellProjection,
    )

    if type(request.scientific_core) is not FinalizedScientificAlignment:
        raise ApplicationResultContractError(
            "Target application-result request requires an exact scientific core"
        )
    if type(request.completed_projection) is not TargetPostCellProjection:
        raise ApplicationResultContractError(
            "Target application-result request requires an exact Target projection"
        )


def _validate_untarget_request(request: UntargetApplicationResultRequest) -> None:
    from xic_extractor.alignment.projection.untarget.post_cell import (
        UntargetPostCellProjection,
    )

    if type(request.scientific_core) is not FinalizedScientificAlignment:
        raise ApplicationResultContractError(
            "Untarget application-result request requires an exact scientific core"
        )
    if type(request.completed_projection) is not UntargetPostCellProjection:
        raise ApplicationResultContractError(
            "Untarget application-result request requires an exact Untarget projection"
        )


def _validate_common_result(
    *,
    scientific_core: FinalizedScientificAlignment,
    _reuse_fresh_completion: bool = False,
) -> None:
    if not _reuse_fresh_completion:
        try:
            _validate_finalized_scientific_alignment_completion_receipt(scientific_core)
        except FinalizedScientificAlignmentContractError as exc:
            raise ApplicationResultContractError(str(exc)) from exc
    if scientific_core.processing_request is not scientific_core.scope.request:
        raise ApplicationResultContractError(
            "application result scientific core substituted its processing request"
        )
    if len(scientific_core.scope.samples) != len(scientific_core.canonical_samples) or {
        id(sample) for sample in scientific_core.scope.samples
    } != {id(sample) for sample in scientific_core.canonical_samples}:
        raise ApplicationResultContractError(
            "application result scientific core substituted its canonical "
            "sample universe"
        )


def _validate_target_result(result: FinalizedTargetApplicationResult) -> None:
    from xic_extractor.alignment.projection.target.post_cell import (
        TargetPostCellProjection,
    )

    _validate_common_result(
        scientific_core=result.scientific_core,
    )
    if type(result.scientific_core.scope) is not TargetSearchScope:
        raise ApplicationResultContractError(
            "Target application result requires a Target scientific core"
        )
    if type(result.completed_projection) is not TargetPostCellProjection:
        raise ApplicationResultContractError(
            "Target application result requires an exact completed Target projection"
        )
    projection = result.completed_projection
    pre_cell = projection.pre_cell_projection
    core = result.scientific_core
    if pre_cell.finalized_alignment is not core:
        raise ApplicationResultContractError(
            "Target completed projection crossed or substituted its scientific core"
        )
    if pre_cell.scope is not core.scope:
        raise ApplicationResultContractError(
            "Target completed projection crossed or substituted its scientific core"
        )
    if len(projection.cell_subjects) != len(core.canonical_samples) or any(
        subject.canonical_sample is not sample
        for subject, sample in zip(
            projection.cell_subjects,
            core.canonical_samples,
            strict=True,
        )
    ):
        raise ApplicationResultContractError(
            "Target completed projection crossed the canonical sample universe"
        )
    if any(
        not any(
            subject.terminal_subject is terminal for terminal in core.terminal_subjects
        )
        for subject in projection.cell_subjects
    ):
        raise ApplicationResultContractError(
            "Target completed projection substituted a terminal scientific owner"
        )
    if any(
        not any(group is owner_group for owner_group in core.final_population)
        for group in projection.row_binding.eligible_groups
    ):
        raise ApplicationResultContractError(
            "Target completed projection substituted a finalized group owner"
        )


def _validate_untarget_result(
    result: FinalizedUntargetApplicationResult,
    *,
    _reuse_fresh_completion: bool = False,
) -> None:
    from xic_extractor.alignment.projection.untarget.post_cell import (
        UntargetPostCellProjection,
        validate_untarget_post_cell_completion_receipt,
    )
    from xic_extractor.alignment.projection.untarget.pre_cell import (
        UntargetProjectionContractError,
    )

    _validate_common_result(
        scientific_core=result.scientific_core,
        _reuse_fresh_completion=_reuse_fresh_completion,
    )
    if type(result.scientific_core.scope) is not UntargetedSearchScope:
        raise ApplicationResultContractError(
            "Untarget application result requires an Untargeted scientific core"
        )
    if type(result.completed_projection) is not UntargetPostCellProjection:
        raise ApplicationResultContractError(
            "Untarget application result requires an exact completed "
            "Untarget projection"
        )
    if not _reuse_fresh_completion:
        try:
            validate_untarget_post_cell_completion_receipt(result.completed_projection)
        except UntargetProjectionContractError as exc:
            raise ApplicationResultContractError(str(exc)) from exc
    projection = result.completed_projection
    pre_cell = projection.pre_cell_projection
    if pre_cell.finalized_alignment is not result.scientific_core:
        raise ApplicationResultContractError(
            "Untarget completed projection crossed or substituted its scientific core"
        )
    if pre_cell.processing_request is not result.scientific_core.processing_request:
        raise ApplicationResultContractError(
            "Untarget completed projection substituted its processing request"
        )
    _validate_exact_owner_population(
        actual=pre_cell.canonical_samples,
        expected=result.scientific_core.canonical_samples,
        label="canonical sample universe",
    )
    _validate_exact_owner_population(
        actual=pre_cell.final_population,
        expected=result.scientific_core.final_population,
        label="final population",
    )
    _validate_exact_owner_population(
        actual=pre_cell.lineage,
        expected=result.scientific_core.lineage,
        label="lineage",
    )


def _validate_untarget_batch_request(
    request: UntargetBatchApplicationResultRequest
    | FinalizedUntargetBatchApplicationResult,
) -> None:
    if type(request.scope) is not UntargetedSearchScope:
        raise ApplicationResultContractError(
            "Untarget batch result requires an exact UntargetedSearchScope"
        )
    if type(request.processing_request) is not ProcessingRequestIdentity:
        raise ApplicationResultContractError(
            "Untarget batch result requires an exact processing request"
        )
    if type(request.processing_attempt) is not ProcessingAttemptIdentity:
        raise ApplicationResultContractError(
            "Untarget batch result requires an exact processing attempt"
        )
    if request.scope.request is not request.processing_request:
        raise ApplicationResultContractError(
            "Untarget batch result crossed its processing request"
        )
    if (
        request.processing_attempt.processing_request_id
        != request.processing_request.processing_request_id
    ):
        raise ApplicationResultContractError(
            "Untarget batch result attempt crossed its processing request"
        )
    children = request.children
    if type(children) not in (tuple, ImmutableSequence):
        raise ApplicationResultContractError(
            "Untarget batch result requires an immutable child population"
        )
    exclusions = request.excluded_candidates
    if type(exclusions) not in (tuple, ImmutableSequence):
        raise ApplicationResultContractError(
            "Untarget batch exclusions must be an immutable typed sequence"
        )
    excluded_ids: list[str] = []
    excluded_id_set: set[str] = set()

    parent_candidates = tuple(request.scope.declared_candidate_ids)
    if not parent_candidates or len(parent_candidates) != len(set(parent_candidates)):
        raise ApplicationResultContractError(
            "Untarget batch result requires one complete candidate universe"
        )
    parent_samples = tuple(request.scope.samples)
    parent_sample_ids = _sample_ids(parent_samples)
    candidate_sample_ids = _candidate_sample_ids_from_request(
        request.processing_request,
        samples=parent_samples,
    )
    if candidate_sample_ids and set(candidate_sample_ids) != set(parent_candidates):
        raise ApplicationResultContractError(
            "Untarget batch candidate/sample universe is incomplete"
        )
    parent_subject_ids = tuple(
        provider.method_subject_id for provider in request.scope.evidence_providers
    )
    if not parent_subject_ids or len(parent_subject_ids) != len(
        set(parent_subject_ids)
    ):
        raise ApplicationResultContractError(
            "Untarget batch result parent subjects are not unique"
        )
    try:
        subject_batch = cid_nl_method_subject_batch_from_request(
            request.processing_request
        )
    except CidNlMethodSubjectContractError as exc:
        raise ApplicationResultContractError(
            "Untarget batch result is not pinned to a CID-NL subject batch"
        ) from exc
    parent_memberships = dict(subject_batch.candidate_memberships)
    if parent_candidates != tuple(
        candidate_id
        for candidate_id, _method_subject_id in subject_batch.candidate_memberships
    ) or parent_subject_ids != tuple(
        subject.method_subject_id for subject in subject_batch.subjects
    ):
        raise ApplicationResultContractError(
            "Untarget batch result parent scope is not the complete request universe"
        )
    parent_sample_ids_set = set(parent_sample_ids)
    subject_by_id = {
        subject.method_subject_id: subject for subject in subject_batch.subjects
    }
    samples_by_id = {
        sample.identity.sample_identity_id: sample for sample in parent_samples
    }
    input_sample_ids_by_subject: dict[str, set[str]] = {}
    for candidate_id, subject_id in parent_memberships.items():
        sample_id = candidate_sample_ids.get(candidate_id)
        if sample_id is not None:
            input_sample_ids_by_subject.setdefault(subject_id, set()).add(sample_id)
    # Rebuilding this shared binding would reparse the full request per candidate.
    exclusion_owners: ProcessingOwnerReferences | None = None
    for exclusion in exclusions:
        if type(exclusion) is not UntargetedCandidateExclusion:
            raise ApplicationResultContractError(
                "Untarget batch exclusions must be an immutable typed sequence"
            )
        if exclusion.candidate_id in excluded_id_set:
            raise ApplicationResultContractError(
                "Untarget batch exclusions contain duplicate candidates"
            )
        excluded_ids.append(exclusion.candidate_id)
        excluded_id_set.add(exclusion.candidate_id)
        exclusion.__post_init__()
        expected_sample_id = candidate_sample_ids.get(exclusion.candidate_id)
        if (
            parent_memberships.get(exclusion.candidate_id)
            != exclusion.method_subject_id
            or exclusion.canonical_sample_identity_id not in parent_sample_ids_set
            or (
                expected_sample_id is not None
                and exclusion.canonical_sample_identity_id != expected_sample_id
            )
        ):
            raise ApplicationResultContractError(
                "Untarget batch exclusion crossed candidate, subject, or sample"
            )
        if exclusion.reason_code in {
            "composite_requirement_not_supported",
            "cid_nl_requirement_not_supported",
        }:
            assessments = (
                exclusion.composite_assessments
                if exclusion.reason_code == "composite_requirement_not_supported"
                else exclusion.method_assessments
            )
            assessment_sample_ids = tuple(
                ref.content.canonical_sample_identity_id for ref in assessments
            )
            required_sample_ids = input_sample_ids_by_subject.get(
                exclusion.method_subject_id, parent_sample_ids_set
            )
            # Exclusion closes the subject's actual Discovery inputs. Samples
            # without an input need no synthetic no-search assessment; multiple
            # distinct candidate peaks in one sample may have distinct answers.
            if (
                assessment_sample_ids != tuple(sorted(assessment_sample_ids))
                or not required_sample_ids.issubset(assessment_sample_ids)
                or not set(assessment_sample_ids).issubset(parent_sample_ids_set)
                or len({ref.artifact.evidence_artifact_id for ref in assessments})
                != len(assessments)
            ):
                raise ApplicationResultContractError(
                    "Method exclusion requires exact input sample coverage"
                )
            if exclusion_owners is None:
                exclusion_owners = ProcessingOwnerReferences(
                    request=request.processing_request,
                    attempt=request.processing_attempt,
                    sample_snapshot=parent_samples[0].snapshot,
                    sample_resolutions=parent_samples,
                )
            for assessment in assessments:
                sample = samples_by_id[assessment.content.canonical_sample_identity_id]
                try:
                    validate_cid_nl_method_assessment_binding(
                        assessment,
                        processing_owners=exclusion_owners,
                        sample=sample,
                        method_subject=subject_by_id[exclusion.method_subject_id],
                    )
                except ValueError as exc:
                    raise ApplicationResultContractError(
                        "Method exclusion crossed its assessment owner binding"
                    ) from exc

    if tuple(excluded_ids) != tuple(
        candidate_id
        for candidate_id in parent_candidates
        if candidate_id in excluded_id_set
    ):
        raise ApplicationResultContractError(
            "Untarget batch exclusions are not in canonical candidate order"
        )

    included_subject_ids = {
        method_subject_id
        for candidate_id, method_subject_id in parent_memberships.items()
        if candidate_id not in excluded_id_set
    }
    included_subjects = tuple(
        subject
        for subject in subject_batch.subjects
        if subject.method_subject_id in included_subject_ids
    )
    try:
        expected_child_worksets = tuple(
            tuple(subject.method_subject_id for subject in workset)
            for workset in cid_nl_alignment_subject_worksets(included_subjects)
        )
    except CidNlMethodSubjectContractError as exc:
        raise ApplicationResultContractError(
            "Untarget batch included subjects cannot form alignment worksets"
        ) from exc

    child_candidates: set[str] = set()
    seen_subject_ids: set[str] = set()
    child_worksets: list[tuple[str, ...]] = []
    row_ids: set[str] = set()
    group_ids: set[str] = set()
    physical_ids: set[str] = set()
    for child in children:
        if type(child) is not FinalizedUntargetApplicationResult:
            raise ApplicationResultContractError(
                "Untarget batch result requires exact finalized Untarget children"
            )
        # The application owner already closed each immutable child. Batch
        # assembly checks parent bindings and cross-child coverage below; it
        # does not reopen the child's scientific or post-cell computation.
        core = child.scientific_core
        if core.processing_request is not request.processing_request:
            raise ApplicationResultContractError(
                "Untarget batch child crossed its processing request"
            )
        handoff = core.cell_observation_handoff
        if handoff is None:
            raise ApplicationResultContractError(
                "Untarget batch child lost its Backfill association handoff"
            )
        child_attempt = handoff.coverage.epoch.processing_owners.attempt
        if child_attempt is not request.processing_attempt:
            raise ApplicationResultContractError(
                "Untarget batch child crossed its processing attempt"
            )
        scope = core.scope
        if type(scope) is not UntargetedSearchScope:
            raise ApplicationResultContractError(
                "Untarget batch child requires an exact UntargetedSearchScope"
            )
        if scope.request is not request.processing_request:
            raise ApplicationResultContractError(
                "Untarget batch child scope crossed its processing request"
            )
        samples = tuple(scope.samples)
        if _sample_ids(samples) != parent_sample_ids or any(
            sample is not parent
            for sample, parent in zip(
                sorted(samples, key=_sample_sort_key),
                sorted(parent_samples, key=_sample_sort_key),
                strict=True,
            )
        ):
            raise ApplicationResultContractError(
                "Untarget batch children must share one exact sample universe"
            )
        memberships = _scope_memberships(scope)
        candidate_ids = tuple(scope.declared_candidate_ids)
        if set(memberships) != set(candidate_ids):
            raise ApplicationResultContractError(
                "Untarget batch child subject membership is incomplete"
            )
        if any(
            parent_memberships.get(candidate_id) != method_subject_id
            for candidate_id, method_subject_id in memberships.items()
        ):
            raise ApplicationResultContractError(
                "Untarget batch child subject membership crossed its parent"
            )
        child_subject_id_set = set(memberships.values())
        child_subject_ids = tuple(
            subject_id
            for subject_id in parent_subject_ids
            if subject_id in child_subject_id_set
        )
        if not child_subject_ids or len(child_subject_ids) != len(child_subject_id_set):
            raise ApplicationResultContractError(
                "Untarget batch child subject workset crossed its parent"
            )
        if (
            tuple(provider.method_subject_id for provider in scope.evidence_providers)
            != child_subject_ids
        ):
            raise ApplicationResultContractError(
                "Untarget batch child providers do not match its subject workset"
            )
        try:
            validate_cid_nl_alignment_subject_compatibility(
                tuple(subject_by_id[subject_id] for subject_id in child_subject_ids)
            )
        except (CidNlMethodSubjectContractError, KeyError) as exc:
            raise ApplicationResultContractError(
                "Untarget batch child must carry one compatible alignment workset"
            ) from exc
        if seen_subject_ids.intersection(child_subject_id_set):
            raise ApplicationResultContractError(
                "Untarget batch result has duplicate subjects across children"
            )
        seen_subject_ids.update(child_subject_id_set)
        child_worksets.append(child_subject_ids)
        for candidate_id in candidate_ids:
            if candidate_id in child_candidates or candidate_id in excluded_id_set:
                raise ApplicationResultContractError(
                    "Untarget batch child candidate union is not exactly once"
                )
            child_candidates.add(candidate_id)
        if len(child_candidates) > len(parent_candidates):
            raise ApplicationResultContractError(
                "Untarget batch child candidate union is not exactly once"
            )

        for row in child.completed_projection.rows:
            row_key = getattr(row, "row_key", None)
            row_id = getattr(row_key, "untarget_row_key_id", None)
            group = getattr(row_key, "group", None)
            group_id = getattr(group, "group_hypothesis_id", None)
            if not isinstance(row_id, str) or not isinstance(group_id, str):
                raise ApplicationResultContractError(
                    "Untarget batch row identity is incomplete"
                )
            if row_id in row_ids:
                raise ApplicationResultContractError(
                    "Untarget batch row identity conflicts across children"
                )
            if group_id in group_ids:
                raise ApplicationResultContractError(
                    "Untarget batch group identity conflicts across children"
                )
            row_ids.add(row_id)
            group_ids.add(group_id)

        for group in core.final_population:
            for member in group.member_subjects:
                member_physical_ids = tuple(member.physical_observation_ids)
                if physical_ids.intersection(member_physical_ids):
                    raise ApplicationResultContractError(
                        "Untarget batch children share a physical observation"
                    )
                physical_ids.update(member_physical_ids)

    if child_candidates | excluded_id_set != set(parent_candidates):
        raise ApplicationResultContractError(
            "Untarget batch child/exclusion candidate union is not exactly once"
        )
    child_by_subject = {
        subject_id: index
        for index, workset in enumerate(child_worksets)
        for subject_id in workset
    }
    subject_order = {
        subject.method_subject_id: index
        for index, subject in enumerate(included_subjects)
    }
    if any(
        len({child_by_subject[subject_id] for subject_id in workset}) != 1
        for workset in expected_child_worksets
    ) or child_worksets != sorted(
        child_worksets, key=lambda workset: min(subject_order[item] for item in workset)
    ):
        raise ApplicationResultContractError(
            "Untarget batch child worksets split a comparison component or "
            "are not in canonical order"
        )


def _sample_ids(
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> tuple[str, ...]:
    return tuple(
        sample.identity.sample_identity_id
        for sample in sorted(samples, key=_sample_sort_key)
    )


def _candidate_sample_ids_from_request(
    request: ProcessingRequestIdentity,
    *,
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> dict[str, str]:
    request_inputs = json.loads(request.normalized_scientific_inputs_json)
    try:
        entries = validate_candidate_universe_payload(
            request_inputs[CANDIDATE_UNIVERSE_INPUT_KEY],
            expected_sample_stems=tuple(sample.metadata.raw_stem for sample in samples),
        )
    except (IdentityContractError, KeyError) as exc:
        raise ApplicationResultContractError(
            "Untarget batch requires its owner-validated candidate/sample universe"
        ) from exc
    sample_id_by_stem = {
        sample.metadata.raw_stem: sample.identity.sample_identity_id
        for sample in samples
    }
    candidate_sample_ids: dict[str, str] = {}
    for entry in entries:
        sample_stem = entry["sample_stem"]
        candidate_ids = entry["candidate_ids"]
        assert isinstance(sample_stem, str)
        assert isinstance(candidate_ids, list)
        sample_id = sample_id_by_stem[sample_stem]
        for candidate_id in candidate_ids:
            assert isinstance(candidate_id, str)
            if candidate_id in candidate_sample_ids:
                raise ApplicationResultContractError(
                    "Untarget batch candidate/sample universe is not unique"
                )
            candidate_sample_ids[candidate_id] = sample_id
    feature_ids = request_inputs.get(ALIGNMENT_FEATURE_INPUT_KEY)
    if feature_ids is not None:
        if (
            not isinstance(feature_ids, list)
            or any(not isinstance(cid, str) for cid in feature_ids)
            or feature_ids != sorted(set(feature_ids))
            or not set(feature_ids).issubset(candidate_sample_ids)
        ):
            raise ApplicationResultContractError(
                "Alignment feature selection is invalid"
            )
        subject_batch = cid_nl_method_subject_batch_from_request(request)
        if tuple(feature_ids) != tuple(
            cid for cid, _ in subject_batch.candidate_memberships
        ):
            raise ApplicationResultContractError(
                "Alignment feature selection crossed subject membership"
            )
        return {cid: candidate_sample_ids[cid] for cid in feature_ids}
    return candidate_sample_ids


def _sample_sort_key(sample: ResolvedCanonicalSampleIdentity) -> str:
    return sample.identity.sample_identity_id


def _scope_memberships(scope: UntargetedSearchScope) -> dict[str, str]:
    memberships: dict[str, str] = {}
    for binding in scope.capture_bindings:
        candidate_id = getattr(binding, "candidate_id", None)
        method_subject_id = getattr(binding, "method_subject_id", None)
        if not isinstance(candidate_id, str) or not isinstance(method_subject_id, str):
            raise ApplicationResultContractError(
                "Untarget batch capture membership is incomplete"
            )
        if candidate_id in memberships:
            raise ApplicationResultContractError(
                "Untarget batch capture membership contains duplicate candidates"
            )
        memberships[candidate_id] = method_subject_id
    return memberships


def _validate_exact_owner_population(
    *,
    actual: tuple[object, ...],
    expected: tuple[object, ...],
    label: str,
) -> None:
    if len(actual) != len(expected) or any(
        actual_owner is not expected_owner
        for actual_owner, expected_owner in zip(actual, expected, strict=True)
    ):
        raise ApplicationResultContractError(
            f"Untarget completed projection substituted its {label}"
        )


def _require_result_mint(value: object) -> None:
    if value is not _APPLICATION_RESULT_MINT_TOKEN:
        raise ApplicationResultContractError(
            "finalized application result must be minted by its owner finalizer"
        )


__all__ = (
    "ApplicationResultContractError",
    "ApplicationResultRequest",
    "FinalizedApplicationResult",
    "FinalizedTargetApplicationResult",
    "FinalizedUntargetApplicationResult",
    "FinalizedUntargetBatchApplicationResult",
    "TargetApplicationResultRequest",
    "UntargetApplicationResultRequest",
    "UntargetBatchApplicationResultRequest",
    "UntargetedCandidateExclusion",
    "finalize_alignment_result",
)
