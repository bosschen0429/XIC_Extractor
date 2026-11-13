"""Measure one XIC's peak population and answer point queries against it.

Discovery measures every peak of an XIC this way and reports each peak's XIC
window and query RT. Alignment repeats the same measurement from RAW with
those capture conditions; equal inputs give equal evaluations.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    answer_physical_peak_question,
    discovery_raw_peak_components,
    earliest_physical_segment_at_rt,
    enumerate_physical_segment_universe,
    mint_physical_peak_question,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import (
    DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    PhysicalPeakAnswer,
    PhysicalPeakEvaluation,
    PhysicalPeakOperation,
    PhysicalPeakQuestion,
    PhysicalSegment,
    PhysicalSegmentUniverse,
    PreparedSignalResult,
    RawPeakComponent,
)


@dataclass(frozen=True, slots=True)
class PhysicalPeakPopulation:
    """The prepared signal and segment universe of one XIC window."""

    prepared: PreparedSignalResult
    universe: PhysicalSegmentUniverse
    context_rt_start_min: float
    context_rt_end_min: float

    def segment_at(self, query_rt_min: float) -> PhysicalSegment | None:
        return earliest_physical_segment_at_rt(
            self.universe,
            query_rt_min,
            selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
        )

    def raw_apex_rt(self, segment: PhysicalSegment) -> float:
        """The segment's raw apex inside the window; its measurement's query RT."""
        component = segment.raw_component
        query_rt = (
            self.prepared.projection.rt[component.apex_index]
            if component is not None
            else segment.apex_rt_min
        )
        return min(max(query_rt, self.context_rt_start_min), self.context_rt_end_min)

    def peak_centred_context(
        self, segment: PhysicalSegment, *, half_width_peak_widths: float
    ) -> tuple[float, float, float]:
        """Raw apex and the context of ``half_width_peak_widths`` peak widths.

        The peak width is the segment's measured boundary width in this window.
        RT never starts before zero.
        """
        apex_rt = self.raw_apex_rt(segment)
        half_width = half_width_peak_widths * (
            segment.right_rt_min - segment.left_rt_min
        )
        return apex_rt, max(0.0, apex_rt - half_width), apex_rt + half_width

    def measure_segment(self, segment: PhysicalSegment) -> PhysicalPeakEvaluation:
        """Measure one segment, queried at its raw apex inside the window."""
        question = PhysicalPeakQuestion(
            "physical_peak_question",
            self.universe,
            self.raw_apex_rt(segment),
            (segment,),
            DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
        )
        return answer_physical_peak_question(self.prepared, self.universe, question)

    def unselected(self, query_rt_min: float) -> PhysicalPeakEvaluation:
        """The explicit no-selection answer of a query no segment owns."""
        universe = self.universe
        question = mint_physical_peak_question(
            universe.prepared,
            universe,
            query_rt_min=query_rt_min,
            selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
        )
        state = universe.state if universe.state != "available" else "unavailable"
        reason = universe.reason or (
            "ambiguous_segment_candidates"
            if question.admissible_segments
            else "no_selection"
        )
        answer = PhysicalPeakAnswer(
            "physical_peak_answer", state, reason, question, None
        )
        operations: tuple[PhysicalPeakOperation, ...] = (
            "trace_evaluation",
            "asls_baseline",
            "gaussian_smoothing",
            "segment_enumeration",
        )
        if universe.prepared.state != "available":
            operations = ("trace_evaluation",)
        return PhysicalPeakEvaluation(
            state,
            reason,
            universe.prepared,
            universe,
            question,
            answer,
            None,
            None,
            operations,
        )

    def evaluate(self, query_rt_min: float) -> PhysicalPeakEvaluation:
        """The measurement of the peak a query RT selects."""
        segment = self.segment_at(query_rt_min)
        if segment is None:
            return self.unselected(query_rt_min)
        return self.measure_segment(segment)


def measure_physical_peak_population(
    rt: np.ndarray,
    intensity: np.ndarray,
    *,
    context_rt_start_min: float,
    context_rt_end_min: float,
    profile: SignalProcessingProfile,
    components: tuple[RawPeakComponent, ...] | None = None,
) -> PhysicalPeakPopulation:
    """Prepare one XIC window and enumerate its peaks; no segment is measured."""
    prepared = prepare_physical_signal(rt, intensity, profile=profile)
    if components is None:
        components = discovery_raw_peak_components(rt, intensity, profile)
    universe = enumerate_physical_segment_universe(
        prepared,
        request_context_rt_start_min=context_rt_start_min,
        request_context_rt_end_min=context_rt_end_min,
        selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
        discovery_components=components,
    )
    return PhysicalPeakPopulation(
        prepared, universe, context_rt_start_min, context_rt_end_min
    )


__all__ = ("PhysicalPeakPopulation", "measure_physical_peak_population")
