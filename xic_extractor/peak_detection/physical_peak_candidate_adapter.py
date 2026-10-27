"""Project prepared physical segments into candidates and bind a selection back."""

from __future__ import annotations

from collections.abc import Mapping

from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidatesResult,
    PeakResult,
    PeakStatus,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    answer_physical_peak_question,
    mint_physical_peak_question,
    qualified_physical_segments,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PHYSICAL_PEAK_NO_SELECTION_POLICY,
    PHYSICAL_PEAK_POINT_POLICY,
    PhysicalPeakEvaluation,
    PhysicalSegment,
    PhysicalSegmentUniverse,
    PreparedSignalResult,
)


def answer_selected_physical_candidate(
    prepared_result: PreparedSignalResult,
    universe: PhysicalSegmentUniverse,
    candidate: PeakCandidate | None,
    *,
    acquisition_rt_window: tuple[float, float],
    selection_rt_window: tuple[float, float],
    candidate_evaluations: Mapping[PeakCandidate, PhysicalPeakEvaluation] | None = None,
    selection_policy_version: str = PHYSICAL_PEAK_POINT_POLICY,
) -> PhysicalPeakEvaluation:
    """Bind a selected candidate and return its completed physical answer.

    A supplied evaluation map belongs to these candidates, signal and universe;
    selection reuses that answer without minting or evaluating another question.
    With no candidate, unavailable acquisition uses the acquired window's centre;
    an available universe uses the selection window's centre and no-selection
    policy, so segments outside that window cannot acquire selection authority.
    """
    if candidate is not None and candidate_evaluations is not None:
        return candidate_evaluations[candidate]
    segment = candidate.physical_segment if candidate is not None else None
    if candidate is not None and (segment is None or segment not in universe.segments):
        raise ValueError("selected Targeted candidate must carry its physical segment")
    if segment is not None:
        query_rt_min = segment.apex_rt_min
    else:
        window = (
            acquisition_rt_window
            if universe.state != "available"
            else selection_rt_window
        )
        query_rt_min = (window[0] + window[1]) / 2
    question = mint_physical_peak_question(
        prepared_result.projection,
        universe,
        query_rt_min=query_rt_min,
        selection_policy_version=(
            selection_policy_version
            if segment is not None
            else PHYSICAL_PEAK_NO_SELECTION_POLICY
        ),
    )
    return answer_physical_peak_question(prepared_result, universe, question)


def physical_candidates_result(
    prepared_result: PreparedSignalResult,
    universe: PhysicalSegmentUniverse,
    *,
    selection_policy_version: str = PHYSICAL_PEAK_POINT_POLICY,
) -> PeakCandidatesResult:
    prepared = prepared_result.prepared
    if universe.state != "available" or prepared is None:
        status: PeakStatus = (
            "NO_SIGNAL"
            if universe.reason == "no_signal_above_baseline"
            else "PEAK_NOT_FOUND"
        )
        return PeakCandidatesResult(
            status=status,
            candidates=(),
            n_points=prepared_result.projection.trace_point_count,
            max_smoothed=None,
            n_prominent_peaks=0,
        )
    candidates = tuple(
        _physical_segment_candidate(prepared_result, segment)
        for segment in qualified_physical_segments(
            prepared,
            universe.segments,
            selection_policy_version=selection_policy_version,
        )
    )
    if not candidates:
        return PeakCandidatesResult(
            status="PEAK_NOT_FOUND",
            candidates=(),
            n_points=len(prepared.rt),
            max_smoothed=max(prepared.smoothed, default=None),
            n_prominent_peaks=0,
        )
    return PeakCandidatesResult(
        status="OK",
        candidates=candidates,
        n_points=len(prepared.rt),
        max_smoothed=max(prepared.smoothed, default=None),
        n_prominent_peaks=len(candidates),
    )


def _physical_segment_candidate(
    prepared_result: PreparedSignalResult,
    segment: PhysicalSegment,
) -> PeakCandidate:
    prepared = prepared_result.prepared
    assert prepared is not None
    raw_apex = float(prepared.raw[segment.apex_index])
    smoothed_apex = float(prepared.smoothed[segment.apex_index])
    duration = segment.right_rt_min - segment.left_rt_min
    edge_signal = max(
        float(prepared.positive_signal[segment.start_index]),
        float(prepared.positive_signal[segment.end_index - 1]),
    )
    edge_ratio = edge_signal / segment.height if segment.height > 0 else None
    return PeakCandidate(
        peak=PeakResult(
            rt=segment.apex_rt_min,
            intensity=raw_apex,
            intensity_smoothed=smoothed_apex,
            area=segment.area_counts_seconds,
            peak_start=segment.left_rt_min,
            peak_end=segment.right_rt_min,
        ),
        selection_apex_rt=segment.apex_rt_min,
        selection_apex_intensity=smoothed_apex,
        selection_apex_index=segment.apex_index,
        raw_apex_rt=segment.apex_rt_min,
        raw_apex_intensity=raw_apex,
        raw_apex_index=segment.apex_index,
        prominence=segment.height,
        region_scan_count=segment.scan_support_count,
        region_duration_min=duration,
        region_edge_ratio=edge_ratio,
        region_trace_continuity=1.0,
        proposal_sources=("physical_segment",),
        physical_segment=segment,
    )
