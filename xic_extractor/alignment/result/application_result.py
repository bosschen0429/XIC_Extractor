"""Immutable application-result closure over completed workflow projections."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import TYPE_CHECKING, Literal, TypeAlias

from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    FinalizedScientificAlignmentContractError,
    _validate_finalized_scientific_alignment_completion_receipt,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubjectContractError,
    cid_nl_method_subject_batch_from_request,
)
from xic_extractor.peak_detection.search_scope import (
    TargetSearchScope,
    UntargetedSearchScope,
)
from xic_extractor.processing_identity import (
    ProcessingAttemptIdentity,
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
    children: tuple[FinalizedUntargetApplicationResult, ...]

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
    ) -> tuple[CrossSampleFinalReceipt, TargetPostCellProjection]:
        return (
            self.scientific_core.final_cross_sample_receipt,
            self.completed_projection,
        )


@dataclass(frozen=True, slots=True)
class FinalizedUntargetApplicationResult:
    scientific_core: FinalizedScientificAlignment
    completed_projection: UntargetPostCellProjection
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _require_result_mint(_mint_token)
        _validate_untarget_result(self)

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
    ) -> tuple[CrossSampleFinalReceipt, UntargetPostCellProjection]:
        return (
            self.scientific_core.final_cross_sample_receipt,
            self.completed_projection,
        )


@dataclass(frozen=True, slots=True)
class FinalizedUntargetBatchApplicationResult:
    """One immutable batch handoff over subject-local Untarget results.

    The batch result owns only complete child results.  It does not regroup
    rows, select values, or synthesize missing evidence; those decisions stay
    with the existing subject-local owners.
    """

    scope: UntargetedSearchScope
    processing_request: ProcessingRequestIdentity
    processing_attempt: ProcessingAttemptIdentity
    children: tuple[FinalizedUntargetApplicationResult, ...]
    _mint_token: InitVar[object]

    def __post_init__(self, _mint_token: object) -> None:
        _require_result_mint(_mint_token)
        _validate_untarget_batch_request(self)

    @property
    def variant(self) -> Literal["untargeted_batch"]:
        return "untargeted_batch"

    @property
    def application_results(
        self,
    ) -> tuple[FinalizedUntargetApplicationResult, ...]:
        return self.children

    @property
    def canonical_samples(
        self,
    ) -> tuple[ResolvedCanonicalSampleIdentity, ...]:
        return self.children[0].canonical_samples

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(
            candidate_id
            for child in self.children
            for candidate_id in child.scientific_core.scope.declared_candidate_ids
        )

    @property
    def subject_ids(self) -> tuple[str, ...]:
        return tuple(
            provider.method_subject_id
            for child in self.children
            for provider in child.scientific_core.scope.evidence_providers
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
            row
            for child in self.children
            for row in child.completed_projection.rows
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
            edge
            for child in self.children
            for edge in child.scientific_core.lineage
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
        return FinalizedUntargetApplicationResult(
            scientific_core=core,
            completed_projection=request.completed_projection,
            _mint_token=_APPLICATION_RESULT_MINT_TOKEN,
        )
    if type(request) is UntargetBatchApplicationResultRequest:
        _validate_untarget_batch_request(request)
        return FinalizedUntargetBatchApplicationResult(
            scope=request.scope,
            processing_request=request.processing_request,
            processing_attempt=request.processing_attempt,
            children=request.children,
            _mint_token=_APPLICATION_RESULT_MINT_TOKEN,
        )
    raise ApplicationResultContractError(
        "application result requires an exact discriminated request variant"
    )


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
) -> None:
    try:
        _validate_finalized_scientific_alignment_completion_receipt(scientific_core)
    except FinalizedScientificAlignmentContractError as exc:
        raise ApplicationResultContractError(str(exc)) from exc
    if scientific_core.processing_request is not scientific_core.scope.request:
        raise ApplicationResultContractError(
            "application result scientific core substituted its processing request"
        )
    if len(scientific_core.scope.samples) != len(
        scientific_core.canonical_samples
    ) or {id(sample) for sample in scientific_core.scope.samples} != {
        id(sample) for sample in scientific_core.canonical_samples
    }:
        raise ApplicationResultContractError(
            "application result scientific core substituted its canonical "
            "sample universe"
        )


def _validate_target_result(result: FinalizedTargetApplicationResult) -> None:
    from xic_extractor.alignment.projection.target.post_cell import (
        TargetPostCellProjection,
        validate_target_post_cell_completion_receipt,
    )
    from xic_extractor.alignment.projection.target.pre_cell import (
        TargetProjectionContractError,
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
    try:
        validate_target_post_cell_completion_receipt(result.completed_projection)
    except TargetProjectionContractError as exc:
        raise ApplicationResultContractError(str(exc)) from exc
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
            subject.terminal_subject is terminal
            for terminal in core.terminal_subjects
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


def _validate_untarget_result(result: FinalizedUntargetApplicationResult) -> None:
    from xic_extractor.alignment.projection.untarget.post_cell import (
        UntargetPostCellProjection,
        validate_untarget_post_cell_completion_receipt,
    )
    from xic_extractor.alignment.projection.untarget.pre_cell import (
        UntargetProjectionContractError,
    )

    _validate_common_result(
        scientific_core=result.scientific_core,
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
    if type(children) is not tuple or not children:
        raise ApplicationResultContractError(
            "Untarget batch result requires a non-empty child population"
        )
    if any(type(child) is not FinalizedUntargetApplicationResult for child in children):
        raise ApplicationResultContractError(
            "Untarget batch result requires exact finalized Untarget children"
        )

    parent_candidates = tuple(request.scope.declared_candidate_ids)
    if not parent_candidates or len(parent_candidates) != len(set(parent_candidates)):
        raise ApplicationResultContractError(
            "Untarget batch result requires one complete candidate universe"
        )
    parent_samples = tuple(request.scope.samples)
    parent_sample_ids = _sample_ids(parent_samples)
    parent_memberships = _scope_memberships(request.scope)
    if set(parent_memberships) != set(parent_candidates):
        raise ApplicationResultContractError(
            "Untarget batch result parent subject membership is incomplete"
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
    if parent_candidates != tuple(
        candidate_id
        for candidate_id, _method_subject_id in subject_batch.candidate_memberships
    ) or parent_subject_ids != tuple(
        subject.method_subject_id for subject in subject_batch.subjects
    ):
        raise ApplicationResultContractError(
            "Untarget batch result parent scope is not the complete request universe"
        )

    child_candidates: list[str] = []
    child_subject_ids: list[str] = []
    row_ids: set[str] = set()
    group_ids: set[str] = set()
    physical_ids: set[str] = set()
    for child in children:
        try:
            _validate_untarget_result(child)
        except ApplicationResultContractError:
            raise
        except (TypeError, ValueError) as exc:
            raise ApplicationResultContractError(
                "Untarget batch child is not a complete finalized result"
            ) from exc
        core = child.scientific_core
        if core.processing_request is not request.processing_request:
            raise ApplicationResultContractError(
                "Untarget batch child crossed its processing request"
            )
        child_attempt = (
            core.cell_observation_handoff.coverage.epoch.processing_owners.attempt
        )
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
        subjects = tuple(memberships.values())
        if not subjects or len(set(subjects)) != 1:
            raise ApplicationResultContractError(
                "Untarget batch child must be subject-local"
            )
        if subjects[0] in child_subject_ids:
            raise ApplicationResultContractError(
                "Untarget batch result has duplicate subject-local children"
            )
        child_candidates.extend(candidate_ids)
        child_subject_ids.append(subjects[0])
        if len(child_candidates) != len(set(child_candidates)) or len(
            child_candidates
        ) > len(parent_candidates):
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

    if sorted(child_candidates) != sorted(parent_candidates):
        raise ApplicationResultContractError(
            "Untarget batch child candidate union is not exactly once"
        )
    if tuple(child_subject_ids) != parent_subject_ids:
        raise ApplicationResultContractError(
            "Untarget batch child subjects are not in canonical order"
        )


def _sample_ids(
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> tuple[str, ...]:
    return tuple(
        sample.identity.sample_identity_id
        for sample in sorted(samples, key=_sample_sort_key)
    )


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
    "finalize_alignment_result",
)
