"""Immutable application-result closure over completed workflow projections."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
)
from xic_extractor.discovery.batch_contract import (
    candidate_sample_stems_from_request,
)
from xic_extractor.discovery.models import (
    DiscoveryCandidateState,
)
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    cid_nl_method_subject_batch_from_request,
)
from xic_extractor.peak_detection.observations import PeakObservationContent
from xic_extractor.peak_detection.search_scope import (
    UntargetedSearchScope,
)
from xic_extractor.processing_identity import (
    IdentityContractError,
    ProcessingAttemptIdentity,
    ProcessingRequestIdentity,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

if TYPE_CHECKING:
    from xic_extractor.alignment.projection.target.post_cell import (
        TargetPostCellProjection,
    )
    from xic_extractor.alignment.projection.untarget.matrix import (
        UntargetMatrixProjection,
    )


class ApplicationResultContractError(ValueError):
    """Raised when application-result owners cannot close exactly."""


@dataclass(frozen=True, slots=True)
class UntargetedCandidateExclusion:
    """One declared Discovery candidate intentionally excluded from owner-build."""

    candidate_id: str
    method_subject_id: str
    sample_key: str
    discovery_candidate_state: DiscoveryCandidateState
    reason_code: Literal[
        "discovery_candidate_not_search_eligible",
        "composite_requirement_not_supported",
        "cid_nl_requirement_not_supported",
    ] = "discovery_candidate_not_search_eligible"
    composite_assessments: tuple[CidNlMethodAssessmentRef, ...] = ()
    method_assessments: tuple[CidNlMethodAssessmentRef, ...] = ()


@dataclass(frozen=True, slots=True)
class TargetApplicationResultRequest:
    scientific_core: FinalizedScientificAlignment
    completed_projection: TargetPostCellProjection

    @property
    def variant(self) -> Literal["targeted"]:
        return "targeted"


@dataclass(frozen=True, slots=True)
class UntargetApplicationResultRequest:
    scientific_core: FinalizedScientificAlignment
    completed_projection: UntargetMatrixProjection

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
        _require_batch_population(self)

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


@dataclass(frozen=True, slots=True)
class FinalizedUntargetApplicationResult:
    scientific_core: FinalizedScientificAlignment
    completed_projection: UntargetMatrixProjection

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

    @property
    def variant(self) -> Literal["untargeted_batch"]:
        return "untargeted_batch"

    @property
    def canonical_samples(
        self,
    ) -> tuple[ResolvedCanonicalSampleIdentity, ...]:
        # Batch closure already checked every child's sample-identity population.
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
    def physical_peak_keys(self) -> tuple[str, ...]:
        return tuple(
            content.physical_peak_key
            for child in self.children
            for group in child.scientific_core.final_population
            for member in group.member_subjects
            for content in member.physical_peaks
        )

    @property
    def rows(self) -> tuple[object, ...]:
        return tuple(
            row for child in self.children for row in child.completed_projection.rows
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
        core = request.scientific_core
        return FinalizedTargetApplicationResult(
            scientific_core=core,
            completed_projection=request.completed_projection,
        )
    if type(request) is UntargetApplicationResultRequest:
        core = request.scientific_core
        result = FinalizedUntargetApplicationResult(
            scientific_core=core,
            completed_projection=request.completed_projection,
        )
        return result
    if type(request) is UntargetBatchApplicationResultRequest:
        # Construction already closes the batch; this handoff adds no new input.
        return FinalizedUntargetBatchApplicationResult(
            scope=request.scope,
            processing_request=request.processing_request,
            processing_attempt=request.processing_attempt,
            children=request.children,
            excluded_candidates=request.excluded_candidates,
        )
    raise ApplicationResultContractError(
        "application result requires an exact discriminated request variant"
    )


def _finalize_fresh_untarget_result(
    request: UntargetApplicationResultRequest,
) -> FinalizedUntargetApplicationResult:
    """Transfer owners just completed in the callback-free workflow closure."""

    result = FinalizedUntargetApplicationResult(
        scientific_core=request.scientific_core,
        completed_projection=request.completed_projection,
    )
    return result


def _require_batch_population(
    request: UntargetBatchApplicationResultRequest
    | FinalizedUntargetBatchApplicationResult,
) -> None:
    """Close cross-child coverage once; children retain their owner's answers."""
    expected = Counter(request.scope.declared_candidate_ids)
    actual = Counter((item.candidate_id for item in request.excluded_candidates))
    candidate_samples = _candidate_sample_ids_from_request(
        request.processing_request, samples=request.scope.samples
    )
    for item in request.excluded_candidates:
        if candidate_samples.get(item.candidate_id) != item.sample_key:
            raise ApplicationResultContractError(
                "Untarget batch exclusion crossed candidate, subject, or sample"
            )
    memberships = dict(
        cid_nl_method_subject_batch_from_request(
            request.processing_request
        ).candidate_memberships
    )
    excluded_ids = set(actual)
    expected_subjects = {
        subject
        for candidate, subject in memberships.items()
        if candidate not in excluded_ids
    }
    actual_subjects: Counter[str] = Counter()
    seen_groups: set[str] = set()
    seen_physical: set[PeakObservationContent] = set()
    sample_ids = set(_sample_ids(request.scope.samples))
    for child in request.children:
        core = child.scientific_core
        actual.update(core.scope.declared_candidate_ids)
        actual_subjects.update(
            provider.method_subject_id for provider in core.scope.evidence_providers
        )
        handoff = core.cell_observation_handoff
        assert handoff is not None
        child_owners = handoff.coverage.processing_owners
        if child_owners.attempt.processing_attempt_id != (
            request.processing_attempt.processing_attempt_id
        ):
            raise ApplicationResultContractError(
                "Untarget batch child crossed its processing attempt"
            )
        if core.processing_request.processing_request_id != (
            request.processing_request.processing_request_id
        ):
            raise ApplicationResultContractError(
                "Untarget batch child crossed its processing request"
            )
        if set(_sample_ids(core.canonical_samples)) != sample_ids:
            raise ApplicationResultContractError(
                "Untarget batch children must share one sample universe"
            )
        for row in child.completed_projection.rows:
            group_id = row.group.group_hypothesis_id
            if group_id in seen_groups:
                raise ApplicationResultContractError(
                    "Untarget batch row identity conflicts across children"
                )
            seen_groups.add(group_id)
        for group in core.final_population:
            for member in group.member_subjects:
                physical_ids = set(member.physical_peaks)
                if seen_physical.intersection(physical_ids):
                    raise ApplicationResultContractError(
                        "Untarget batch children share a physical observation"
                    )
                seen_physical.update(physical_ids)
    if actual != expected:
        raise ApplicationResultContractError(
            "Untarget batch child/exclusion candidate union is not exactly once"
        )
    if actual_subjects != Counter({subject: 1 for subject in expected_subjects}):
        raise ApplicationResultContractError(
            "Untarget batch result has missing or duplicate subjects across children"
        )


def _sample_ids(
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> tuple[str, ...]:
    return tuple(
        sample.identity.sample_key for sample in sorted(samples, key=_sample_sort_key)
    )


def _candidate_sample_ids_from_request(
    request: ProcessingRequestIdentity,
    *,
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> dict[str, str]:
    try:
        candidate_stems, feature_ids = candidate_sample_stems_from_request(
            request,
            expected_sample_stems=tuple(sample.metadata.raw_stem for sample in samples),
        )
    except IdentityContractError as exc:
        raise ApplicationResultContractError(
            "Untarget batch requires its owner-validated candidate/sample universe: "
            f"{exc}"
        ) from exc
    sample_id_by_stem = {
        sample.metadata.raw_stem: sample.identity.sample_key for sample in samples
    }
    if feature_ids is not None:
        subject_batch = cid_nl_method_subject_batch_from_request(request)
        if feature_ids != tuple(cid for cid, _ in subject_batch.candidate_memberships):
            raise ApplicationResultContractError(
                "Alignment feature selection crossed subject membership"
            )
    return {cid: sample_id_by_stem[stem] for cid, stem in candidate_stems.items()}


def _sample_sort_key(sample: ResolvedCanonicalSampleIdentity) -> str:
    return sample.identity.sample_key


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
