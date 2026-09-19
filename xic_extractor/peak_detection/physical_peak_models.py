"""Immutable models and policy literals for physical peak evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)

PhysicalPeakState = Literal["available", "unavailable", "failed"]
LEGACY_PHYSICAL_PEAK_SELECTION_POLICY = "closed_segment_contains_query_v1"
LEGACY_PHYSICAL_PEAK_POINT_POLICY = "baseline_peak_contains_query_v2"
LEGACY_PHYSICAL_PEAK_WINDOW_POLICY = "unique_baseline_peak_in_context_v1"
LEGACY_ORIGINAL_SUPPORT_POINT_POLICY = "baseline_peak_contains_query_v3"
LEGACY_ORIGINAL_SUPPORT_WINDOW_POLICY = "unique_baseline_peak_in_context_v2"
LEGACY_ORIGINAL_CONTINUITY_POINT_POLICY = "baseline_peak_contains_query_v4"
LEGACY_ORIGINAL_CONTINUITY_WINDOW_POLICY = "unique_baseline_peak_in_context_v3"
LEGACY_ORIGINAL_EDGE_RETURN_POINT_POLICY = "baseline_peak_contains_query_v5"
LEGACY_ORIGINAL_EDGE_RETURN_WINDOW_POLICY = "unique_baseline_peak_in_context_v4"
LEGACY_ORIGINAL_FRAGMENTATION_POINT_POLICY = "baseline_peak_contains_query_v6"
LEGACY_ORIGINAL_FRAGMENTATION_WINDOW_POLICY = "unique_baseline_peak_in_context_v5"
PHYSICAL_PEAK_POINT_POLICY = "baseline_peak_contains_query_v7"
PHYSICAL_PEAK_WINDOW_POLICY = "unique_baseline_peak_in_context_v6"
DISCOVERY_PHYSICAL_PEAK_POINT_POLICY = (
    "background_qualified_peak_contains_query_v1"
)
_PHYSICAL_PEAK_SELECTION_POLICIES = frozenset(
    {
        LEGACY_PHYSICAL_PEAK_SELECTION_POLICY,
        LEGACY_PHYSICAL_PEAK_POINT_POLICY,
        LEGACY_PHYSICAL_PEAK_WINDOW_POLICY,
        LEGACY_ORIGINAL_SUPPORT_POINT_POLICY,
        LEGACY_ORIGINAL_SUPPORT_WINDOW_POLICY,
        LEGACY_ORIGINAL_CONTINUITY_POINT_POLICY,
        LEGACY_ORIGINAL_CONTINUITY_WINDOW_POLICY,
        LEGACY_ORIGINAL_EDGE_RETURN_POINT_POLICY,
        LEGACY_ORIGINAL_EDGE_RETURN_WINDOW_POLICY,
        LEGACY_ORIGINAL_FRAGMENTATION_POINT_POLICY,
        LEGACY_ORIGINAL_FRAGMENTATION_WINDOW_POLICY,
        PHYSICAL_PEAK_POINT_POLICY,
        PHYSICAL_PEAK_WINDOW_POLICY,
        DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    }
)
_PHYSICAL_EDGE_RETURN_FRACTION = 0.001
_PHYSICAL_LOCAL_CONTRAST_MIN_MAD = 2.0
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
