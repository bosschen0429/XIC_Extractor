"""Exact completed-empty Backfill query and assessment facts."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
)
from xic_extractor.sample_metadata import SampleMetadata


@dataclass(frozen=True, slots=True)
class BackfillCompletionProjection:
    """Runtime-issued completion evidence; it makes no physical absence claim."""

    sample: SampleMetadata
    group: CrossSamplePeakGroupHypothesis
    backfill_assessment: CidNlMethodAssessment
