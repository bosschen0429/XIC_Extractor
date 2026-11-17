"""Immutable application-result closure over completed workflow projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
)
from xic_extractor.discovery.models import PeakStatus
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    UntargetedCidNlMethodSubjectBatch,
)
from xic_extractor.peak_detection.search_scope import (
    UntargetedSearchScope,
)
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.sample_metadata import SampleMetadata

if TYPE_CHECKING:
    from xic_extractor.alignment.projection.target.post_cell import (
        TargetPostCellProjection,
    )
    from xic_extractor.alignment.projection.untarget.matrix import GroupCells


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
    composite_assessments: tuple[CidNlMethodAssessment, ...] = ()
    method_assessments: tuple[CidNlMethodAssessment, ...] = ()


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
    """The finalized groups, each with its cells in canonical sample order."""

    scientific_core: FinalizedScientificAlignment
    group_cells: tuple[GroupCells, ...]

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

    Each finalized child was projected into its published rows when it
    composed; ``child_outputs`` holds those projections in the batch's child
    order. The batch does not regroup rows, select values, or synthesize
    missing evidence; those decisions stay with the workset-local owners.
    """

    scope: UntargetedSearchScope
    processing_attempt_id: str
    acquisition_sources: tuple[AcquisitionSourceIdentity, ...]
    child_outputs: tuple[object, ...] | ImmutableSequence[object]
    excluded_candidates: (
        tuple[UntargetedCandidateExclusion, ...]
        | ImmutableSequence[UntargetedCandidateExclusion]
    ) = ()
    # Whether any child admitted a Backfill with a flagged membership risk.
    has_flagged_backfill_risk: bool = False

    @property
    def variant(self) -> Literal["untargeted_batch"]:
        return "untargeted_batch"

    @property
    def canonical_samples(
        self,
    ) -> tuple[SampleMetadata, ...]:
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


FinalizedApplicationResult: TypeAlias = (
    FinalizedTargetApplicationResult
    | FinalizedUntargetApplicationResult
    | FinalizedUntargetBatchApplicationResult
)


def _sample_sort_key(sample: SampleMetadata) -> str:
    return sample.sample_name


__all__ = (
    "FinalizedApplicationResult",
    "FinalizedTargetApplicationResult",
    "FinalizedUntargetApplicationResult",
    "FinalizedUntargetBatchApplicationResult",
    "UntargetedCandidateExclusion",
)
