"""Immutable models and policy literals for physical peak evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult

PhysicalPeakState = Literal["available", "unavailable", "failed"]
PHYSICAL_SEGMENT_UNIVERSE_SCHEMA = "physical_segment_universe"
PHYSICAL_PEAK_POINT_POLICY = "baseline_peak_contains_query"
PHYSICAL_PEAK_NO_SELECTION_POLICY = "no_selected_physical_segment"
PHYSICAL_PEAK_WINDOW_POLICY = "unique_baseline_peak_in_context"
# The group RT resolves several peaks in the context, and a tail that settles
# near the baseline closes the peak before the acquired edge.
PHYSICAL_PEAK_GROUP_WINDOW_POLICY = "group_rt_resolved_peak_in_context"
PHYSICAL_PEAK_BACKFILL_POLICY = "sample_local_background_qualified_peak_in_context"
PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY = "backfill_localization_unreliable"
DISCOVERY_PHYSICAL_PEAK_POINT_POLICY = "background_qualified_peak_contains_query"
_PHYSICAL_PEAK_SELECTION_POLICIES = frozenset(
    {
        PHYSICAL_PEAK_POINT_POLICY,
        PHYSICAL_PEAK_NO_SELECTION_POLICY,
        PHYSICAL_PEAK_WINDOW_POLICY,
        PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
        PHYSICAL_PEAK_BACKFILL_POLICY,
        PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY,
        DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    }
)
_PHYSICAL_LOCAL_CONTRAST_MIN_MAD = 4.0
PhysicalPeakOperation = Literal[
    "trace_evaluation",
    "asls_baseline",
    "gaussian_smoothing",
    "segment_enumeration",
    "background_qualification",
    "integration",
    "normalization",
    "raw_descriptor_projection",
    "region_audit_projection",
]
_PHYSICAL_PEAK_OPERATION_ORDER: tuple[PhysicalPeakOperation, ...] = (
    "trace_evaluation",
    "asls_baseline",
    "gaussian_smoothing",
    "segment_enumeration",
    "background_qualification",
    "integration",
    "normalization",
    "raw_descriptor_projection",
    "region_audit_projection",
)


@dataclass(frozen=True, slots=True)
class PreparedSignalProjection:
    schema_version: str
    trace_content_id: str
    profile_id: str
    prepared_signal_id: str
    trace_point_count: int
    state: PhysicalPeakState
    reason: str


@dataclass(frozen=True, slots=True)
class PreparedSignal:
    projection: PreparedSignalProjection
    profile: SignalProcessingProfile
    rt: tuple[float, ...]
    raw: tuple[float, ...]
    baseline: tuple[float, ...]
    smoothed: tuple[float, ...]
    positive_signal: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class PreparedSignalResult:
    projection: PreparedSignalProjection
    prepared: PreparedSignal | None
    operations: tuple[PhysicalPeakOperation, ...]
    trace_context_rt_start_min: float | None = None
    trace_context_rt_end_min: float | None = None
    # Live AsLS intermediate; excluded from prepared-signal persistence and identity.
    baseline_unclipped: tuple[float, ...] | None = None


@dataclass(frozen=True, slots=True)
class PhysicalSegment:
    physical_segment_id: str
    start_index: int
    end_index: int
    apex_index: int
    left_rt_min: float
    right_rt_min: float
    apex_rt_min: float
    height: float
    scan_support_count: int
    area_counts_seconds: float
    segment_class: str
    boundary_stop_reason: str
    evidence_sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PhysicalSegmentUniverse:
    schema_version: str
    prepared_signal_id: str
    context_rt_start_min: float
    context_rt_end_min: float
    state: PhysicalPeakState
    reason: str
    segments: tuple[PhysicalSegment, ...]
    segment_universe_id: str

    @property
    def segment_ids(self) -> tuple[str, ...]:
        return tuple(segment.physical_segment_id for segment in self.segments)


@dataclass(frozen=True, slots=True)
class PhysicalPeakQuestion:
    schema_version: str
    trace_content_id: str
    prepared_signal_id: str
    segment_universe_id: str
    segment_ids: tuple[str, ...]
    selection_subject_id: str
    query_rt_min: float
    context_rt_start_min: float
    context_rt_end_min: float
    admissible_segment_ids: tuple[str, ...]
    selection_policy_version: str
    physical_question_id: str
    # Candidate apex eligibility, independent of the measured signal context.
    search_rt_window: tuple[float, float] | None = None


@dataclass(frozen=True, slots=True)
class PhysicalPeakAnswer:
    schema_version: str
    state: PhysicalPeakState
    reason: str
    prepared_signal_id: str
    physical_question_id: str
    physical_segment_id: str | None
    physical_answer_id: str


@dataclass(frozen=True, slots=True)
class PhysicalPeakFacts:
    schema_version: str
    prepared_signal_id: str
    physical_question_id: str
    physical_answer_id: str
    physical_segment_id: str
    apex_rt_min: float
    left_rt_min: float
    right_rt_min: float
    height: float
    scan_support_count: int
    area_counts_seconds: float
    area_source: str
    relative_shape_schema_version: str
    relative_shape_point_count: int
    relative_shape_content_hash: str
    fact_content_id: str


@dataclass(frozen=True, slots=True)
class PhysicalPeakEvaluation:
    state: PhysicalPeakState
    reason: str
    prepared: PreparedSignalProjection
    segment_universe: PhysicalSegmentUniverse
    question: PhysicalPeakQuestion
    answer: PhysicalPeakAnswer
    facts: PhysicalPeakFacts | None
    integration: IntegrationResult | None
    operations: tuple[PhysicalPeakOperation, ...]
    # Diagnostic evidence only: never part of scientific equality or identity.
    retained_prepared_signal: PreparedSignal | None = field(default=None, compare=False)

    @property
    def requires_overlap_review(self) -> bool:
        selected_id = self.answer.physical_segment_id
        return selected_id is not None and any(
            segment.physical_segment_id == selected_id
            and (
                segment.segment_class == "shoulder_candidate"
                or segment.boundary_stop_reason == "shoulder_overlap_review"
                or "shoulder_overlap" in segment.evidence_sources
            )
            for segment in self.segment_universe.segments
        )
