"""Immutable application-result closure over completed workflow projections."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
)
from xic_extractor.discovery.models import PeakStatus
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    UntargetedCidNlMethodSubjectBatch,
)
from xic_extractor.peak_detection.observations import PeakObservationContent
from xic_extractor.peak_detection.search_scope import (
    UntargetedSearchScope,
)
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.sample_metadata import SampleMetadata

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
    """One declared Discovery peak intentionally excluded from owner-build."""

    candidate_id: str
    method_subject_id: str
    sample_key: str
    peak_status: PeakStatus
    reason_code: Literal[
        "discovery_candidate_not_search_eligible",
        "composite_requirement_not_supported",
        "cid_nl_requirement_not_supported",
    ] = "discovery_candidate_not_search_eligible"
    composite_assessments: tuple[CidNlMethodAssessmentRef, ...] = ()
    method_assessments: tuple[CidNlMethodAssessmentRef, ...] = ()


@dataclass(frozen=True, slots=True)
class FinalizedTargetApplicationResult:
    scientific_core: FinalizedScientificAlignment
    completed_projection: TargetPostCellProjection

    @property
    def variant(self) -> Literal["targeted"]:
        return "targeted"

    @property
    def canonical_samples(
        self,
    ) -> tuple[SampleMetadata, ...]:
        return self.scientific_core.canonical_samples


@dataclass(frozen=True, slots=True)
class FinalizedUntargetApplicationResult:
    scientific_core: FinalizedScientificAlignment
    completed_projection: UntargetMatrixProjection

    @property
    def variant(self) -> Literal["untargeted"]:
        return "untargeted"

    @property
    def canonical_samples(
        self,
    ) -> tuple[SampleMetadata, ...]:
        return self.scientific_core.canonical_samples


@dataclass(frozen=True, slots=True)
class FinalizedUntargetBatchApplicationResult:
    """One immutable batch handoff over exact Untarget workset results.

    The batch result owns only complete child results.  It does not regroup
    rows, select values, or synthesize missing evidence; those decisions stay
    with the existing workset-local owners.
    """

    scope: UntargetedSearchScope
    processing_attempt_id: str
    acquisition_sources: tuple[AcquisitionSourceIdentity, ...]
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

    @property
    def canonical_samples(
        self,
    ) -> tuple[SampleMetadata, ...]:
        # Batch closure already checked every child's sample-identity population.
        # Sample metadata does not require restoring a stored scientific child.
        return tuple(sorted(self.scope.samples, key=_sample_sort_key))

    @property
    def method_subjects(self) -> UntargetedCidNlMethodSubjectBatch:
        return cast(UntargetedCidNlMethodSubjectBatch, self.scope.method_subjects)

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        subject_batch = self.method_subjects
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


def _require_batch_population(
    request: FinalizedUntargetBatchApplicationResult,
) -> None:
    """Close cross-child coverage once; children retain their owner's answers."""
    expected = Counter(request.scope.declared_candidate_ids)
    actual = Counter((item.candidate_id for item in request.excluded_candidates))
    memberships = dict(request.method_subjects.candidate_memberships)
    excluded_ids = set(actual)
    expected_subjects = {
        subject
        for candidate, subject in memberships.items()
        if candidate not in excluded_ids
    }
    actual_subjects: Counter[str] = Counter()
    seen_groups: set[str] = set()
    seen_physical: set[PeakObservationContent] = set()
    for child in request.children:
        core = child.scientific_core
        actual.update(core.scope.declared_candidate_ids)
        actual_subjects.update(
            provider.method_subject_id for provider in core.scope.evidence_providers
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


def _sample_sort_key(sample: SampleMetadata) -> str:
    return sample.sample_name


__all__ = (
    "ApplicationResultContractError",
    "FinalizedApplicationResult",
    "FinalizedTargetApplicationResult",
    "FinalizedUntargetApplicationResult",
    "FinalizedUntargetBatchApplicationResult",
    "UntargetedCandidateExclusion",
)
