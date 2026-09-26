"""Project prepared physical segments into candidates and bind a selection back."""

from __future__ import annotations

from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidatesResult,
    PeakResult,
    PeakStatus,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    qualified_physical_segments,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalSegment,
    PhysicalSegmentUniverse,
    PreparedSignalResult,
)


def physical_candidates_result(
    prepared_result: PreparedSignalResult,
    universe: PhysicalSegmentUniverse,
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
        for segment in qualified_physical_segments(prepared, universe.segments)
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
        proposal_sources=(f"physical_segment:{segment.physical_segment_id}",),
    )


def selected_physical_segment(
    candidate: PeakCandidate | None,
    universe: PhysicalSegmentUniverse,
) -> PhysicalSegment | None:
    if candidate is None:
        return None
    segment_ids = {
        source.removeprefix("physical_segment:")
        for source in candidate.proposal_sources
        if source.startswith("physical_segment:")
    }
    matches = tuple(
        segment
        for segment in universe.segments
        if segment.physical_segment_id in segment_ids
    )
    if len(matches) != 1:
        raise ValueError(
            "selected Targeted candidate must identify one physical segment"
        )
    return matches[0]
