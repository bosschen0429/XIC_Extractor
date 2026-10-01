"""Owner-bound inputs for current Untargeted CID-NL acquisition and grouping."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.discovery.cid_nl_evidence_projection import (
    UntargetedCidNlProjectionRef,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

CID_NL_ALIGNMENT_CONFIG = AlignmentConfig(anchor_min_seed_events=1)


class UntargetedCidNlGoldenPathError(ValueError):
    """Raised when the bounded Untargeted path would lose or invent authority."""


@dataclass(frozen=True, slots=True)
class UntargetedCidNlAssessmentInput:
    """One sample assessment plus exact Discovery projections for its evidence."""

    sample: ResolvedCanonicalSampleIdentity
    assessment: CidNlMethodAssessmentRef
    discovery_projections: tuple[UntargetedCidNlProjectionRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.sample, ResolvedCanonicalSampleIdentity):
            raise UntargetedCidNlGoldenPathError(
                "sample input requires an owner-resolved canonical sample"
            )
        if not isinstance(self.assessment, CidNlMethodAssessmentRef):
            raise UntargetedCidNlGoldenPathError(
                "sample input requires an owner-backed method assessment"
            )
        if not isinstance(self.discovery_projections, tuple) or any(
            not isinstance(item, UntargetedCidNlProjectionRef)
            for item in self.discovery_projections
        ):
            raise UntargetedCidNlGoldenPathError(
                "discovery_projections must contain exact Untargeted projection refs"
            )
        sample_id = self.sample.identity.sample_identity_id
        if self.assessment.content.canonical_sample_identity_id != sample_id:
            raise UntargetedCidNlGoldenPathError(
                "sample and assessment reference different canonical samples"
            )
        projection_sample_ids = {
            item.canonical_sample_identity_id for item in self.discovery_projections
        }
        if projection_sample_ids.difference({sample_id}):
            raise UntargetedCidNlGoldenPathError(
                "Discovery projection references a different canonical sample"
            )
        evidence_artifact_ids = tuple(
            sorted(
                evidence.artifact.evidence_artifact_id
                for evidence in self.assessment.evidence_refs
            )
        )
        projected_artifact_ids = tuple(
            sorted(
                projection.canonical_evidence_artifact_id
                for projection in self.discovery_projections
            )
        )
        if projected_artifact_ids != evidence_artifact_ids:
            raise UntargetedCidNlGoldenPathError(
                "Discovery projections must cover every assessment evidence ref "
                "exactly once"
            )
        object.__setattr__(
            self,
            "discovery_projections",
            tuple(
                sorted(
                    self.discovery_projections,
                    key=lambda projection: projection.artifact.evidence_artifact_id,
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class UntargetedCidNlBackfillInput:
    """One eligible missing cell's complete owner-backed revisit assessment."""

    sample: ResolvedCanonicalSampleIdentity
    candidate_assessment: CidNlMethodAssessmentRef

    def __post_init__(self) -> None:
        if not isinstance(self.sample, ResolvedCanonicalSampleIdentity):
            raise UntargetedCidNlGoldenPathError(
                "backfill input requires an owner-resolved canonical sample"
            )
        if not isinstance(self.candidate_assessment, CidNlMethodAssessmentRef):
            raise UntargetedCidNlGoldenPathError(
                "backfill input requires an owner-backed assessment"
            )
        if self.candidate_assessment.content.canonical_sample_identity_id != (
            self.sample.identity.sample_identity_id
        ):
            raise UntargetedCidNlGoldenPathError(
                "backfill assessment references a different sample"
            )
        if self.candidate_assessment.content.method_result != "HOLD":
            raise UntargetedCidNlGoldenPathError(
                "backfill input must be a completed HOLD assessment"
            )
        if not self.candidate_assessment.candidate_peaks:
            raise UntargetedCidNlGoldenPathError(
                "backfill input must retain its complete non-empty candidate universe"
            )
