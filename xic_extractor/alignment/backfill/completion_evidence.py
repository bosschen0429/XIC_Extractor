"""Exact completed-empty Backfill query and assessment facts."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.acquisition_contract import TraceCoordinate
from xic_extractor.alignment.cross_sample.peak_groups import CrossSampleGroupIdentity
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.sample_metadata import SampleMetadata


@dataclass(frozen=True, slots=True)
class BackfillCompletionProjection:
    """Runtime-issued completion evidence; it makes no physical absence claim."""

    sample: SampleMetadata
    group_identity: CrossSampleGroupIdentity
    group_center_mz: float
    group_center_rt: float
    query_rt: float
    query_rt_basis: str
    backfill_assessment: CidNlMethodAssessmentRef
    trace_coordinate: TraceCoordinate
