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
PHYSICAL_PEAK_REFERENCE_POLICY = "reference_localized_contour_peak"
PHYSICAL_PEAK_ENVELOPE_POLICY = "localized_total_envelope"
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
        PHYSICAL_PEAK_REFERENCE_POLICY,
        PHYSICAL_PEAK_ENVELOPE_POLICY,
        PHYSICAL_PEAK_NO_SELECTION_POLICY,
        PHYSICAL_PEAK_WINDOW_POLICY,
        PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
        PHYSICAL_PEAK_BACKFILL_POLICY,
        PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY,
        DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    }
)
PhysicalPeakOperation = Literal[
    "trace_evaluation",
    "asls_baseline",
    "gaussian_smoothing",
    "segment_enumeration",
    "background_qualification",
    "integration",
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
    "raw_descriptor_projection",
    "region_audit_projection",
)


@dataclass(frozen=True, slots=True)
class PreparedSignalProjection:
    schema_version: str
    profile: SignalProcessingProfile
    rt: tuple[float, ...]
    raw: tuple[float, ...]
    state: PhysicalPeakState
    reason: str

    @property
    def trace_point_count(self) -> int:
        return len(self.rt)


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
    # Worker-local assignment made when this raw component is measured.
    # Completed readers decode retained answers and never rebuild this live map.
    raw_component: RawPeakComponent | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class RawPeakComponent:
    """Acquired valley basin and its observed raw apex."""

    start_index: int
    end_index: int
    apex_index: int
    clips_left_edge: bool
    clips_right_edge: bool
    # Raw segmentation's return extent is for bounded acquisition planning only.
    left_return_index: int | None = None
    right_return_index: int | None = None


@dataclass(frozen=True, slots=True)
class PhysicalSegmentUniverse:
    schema_version: str
    prepared: PreparedSignalProjection
    context_rt_start_min: float
    context_rt_end_min: float
    state: PhysicalPeakState
    reason: str
    segments: tuple[PhysicalSegment, ...]


@dataclass(frozen=True, slots=True)
class PhysicalPeakQuestion:
    schema_version: str
    universe: PhysicalSegmentUniverse
    query_rt_min: float
    admissible_segments: tuple[PhysicalSegment, ...]
    selection_policy_version: str
    # Candidate apex eligibility, independent of the measured signal context.
    search_rt_window: tuple[float, float] | None = None

    @property
    def context_rt_start_min(self) -> float:
        return self.universe.context_rt_start_min

    @property
    def context_rt_end_min(self) -> float:
        return self.universe.context_rt_end_min


@dataclass(frozen=True, slots=True)
class PhysicalPeakAnswer:
    schema_version: str
    state: PhysicalPeakState
    reason: str
    question: PhysicalPeakQuestion
    selected_segment: PhysicalSegment | None


@dataclass(frozen=True, slots=True)
class PhysicalPeakFacts:
    schema_version: str
    apex_rt_min: float
    left_rt_min: float
    right_rt_min: float
    height: float
    scan_support_count: int
    area_counts_seconds: float
    area_source: str


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
    # Live owner intermediates for projection; not scientific equality or identity.
    retained_prepared_signal: PreparedSignal | None = field(default=None, compare=False)

    def __deepcopy__(self, memo: dict[int, object]) -> PhysicalPeakEvaluation:
        # The complete answer graph is frozen dataclasses, tuples and scalars.
        # Copies of mutable run containers can share it without walking signals.
        return self
