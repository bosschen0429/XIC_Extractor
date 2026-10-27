"""Owner-bound inputs for current Untargeted CID-NL acquisition and grouping."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

CID_NL_ALIGNMENT_CONFIG = AlignmentConfig(anchor_min_seed_events=1)


class UntargetedCidNlGoldenPathError(ValueError):
    """Raised when the bounded Untargeted path would lose or invent authority."""


@dataclass(frozen=True, slots=True)
class UntargetedCidNlAssessmentInput:
    """One sample's complete CID-NL method assessment."""

    sample: ResolvedCanonicalSampleIdentity
    assessment: CidNlMethodAssessmentRef


@dataclass(frozen=True, slots=True)
class UntargetedCidNlBackfillInput:
    """One eligible missing cell's complete owner-backed revisit assessment."""

    sample: ResolvedCanonicalSampleIdentity
    candidate_assessment: CidNlMethodAssessmentRef
