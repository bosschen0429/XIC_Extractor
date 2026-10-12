from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

from xic_extractor.peak_detection.integration import integrate_area_counts_seconds
from xic_extractor.peak_detection.models import PeakCandidate

BoundarySource = Literal[
    "candidate_interval",
    "half_height",
    "baseline_return",
    "derivative_zero_crossing",
    "cwt_width",
]


@dataclass(frozen=True)
class BoundaryHypothesis:
    boundary_id: str
    sources: tuple[str, ...]
    left_index: int
    right_index: int
    rt_left_min: float
    rt_apex_min: float
    rt_right_min: float
    width_min: float
    area_raw_counts_seconds: float
    scan_count: int


@dataclass(frozen=True)
class BoundaryCandidateContext:
    selection_apex_rt: float
    rt_left_min: float
    rt_right_min: float
    cwt_best_scale: float | None = None
    proposal_sources: tuple[str, ...] = ()


def boundary_candidate_from_peak_candidate(
    candidate: PeakCandidate,
) -> BoundaryCandidateContext:
    return BoundaryCandidateContext(
        selection_apex_rt=candidate.selection_apex_rt,
        rt_left_min=candidate.peak.peak_start,
        rt_right_min=candidate.peak.peak_end,
        cwt_best_scale=candidate.cwt_best_scale,
        proposal_sources=candidate.proposal_sources,
    )


def enumerate_boundary_hypotheses(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    candidate: PeakCandidate | BoundaryCandidateContext,
    *,
    candidate_id: str | None = None,
    sources: tuple[BoundarySource, ...] = (
        "candidate_interval",
        "half_height",
        "baseline_return",
        "derivative_zero_crossing",
        "cwt_width",
    ),
) -> tuple[BoundaryHypothesis, ...]:
    rt = np.asarray(rt_values, dtype=float)
    intensity = np.asarray(intensity_values, dtype=float)
    _validate_trace_arrays(rt, intensity)
    candidate_context = _boundary_candidate_context(candidate)

    apex_index = _nearest_index(rt, candidate_context.selection_apex_rt)
    intervals: list[tuple[tuple[str, ...], int, int]] = []
    for source in sources:
        interval = _interval_for_source(
            source,
            rt,
            intensity,
            candidate_context,
            apex_index,
        )
        if interval is not None:
            intervals.append(((source,), *interval))

    merged = _merge_duplicate_intervals(intervals)
    return tuple(
        _build_boundary_hypothesis(
            rt,
            intensity,
            candidate_context,
            candidate_id=candidate_id
            or _candidate_context_boundary_base_id(candidate_context),
            sources=boundary_sources,
            left_index=left_index,
            right_index=right_index,
        )
        for boundary_sources, left_index, right_index in merged
    )


def _boundary_candidate_context(
    candidate: PeakCandidate | BoundaryCandidateContext,
) -> BoundaryCandidateContext:
    if isinstance(candidate, BoundaryCandidateContext):
        return candidate
    return boundary_candidate_from_peak_candidate(candidate)


def boundary_audit_id(*, candidate_id: str, boundary: BoundaryHypothesis) -> str:
    return "|".join(
        (
            candidate_id,
            _join(boundary.sources),
            _format_float(boundary.rt_left_min, digits=5),
            _format_float(boundary.rt_right_min, digits=5),
        )
    )


def _validate_trace_arrays(rt: np.ndarray, intensity: np.ndarray) -> None:
    if rt.ndim != 1 or intensity.ndim != 1:
        raise ValueError("rt_values and intensity_values must be one-dimensional")
    if len(rt) != len(intensity):
        raise ValueError("rt_values and intensity_values must have the same length")
    if len(rt) < 2:
        raise ValueError("rt_values and intensity_values must contain at least 2 scans")


def _interval_for_source(
    source: BoundarySource,
    rt: np.ndarray,
    intensity: np.ndarray,
    candidate: BoundaryCandidateContext,
    apex_index: int,
) -> tuple[int, int] | None:
    if source == "candidate_interval":
        return _candidate_interval(rt, candidate)
    if source == "half_height":
        return _threshold_interval(rt, intensity, candidate, apex_index, fraction=0.5)
    if source == "baseline_return":
        return _threshold_interval(rt, intensity, candidate, apex_index, fraction=0.05)
    if source == "derivative_zero_crossing":
        return _derivative_zero_crossing_interval(rt, intensity, apex_index)
    if source == "cwt_width":
        return _cwt_width_interval(rt, candidate, apex_index)
    raise ValueError(f"Unsupported boundary source: {source}")


def _candidate_interval(
    rt: np.ndarray,
    candidate: BoundaryCandidateContext,
) -> tuple[int, int]:
    left_index = _nearest_index(rt, candidate.rt_left_min)
    right_inclusive = _nearest_index(rt, candidate.rt_right_min)
    return _valid_interval(left_index, right_inclusive + 1, len(rt))


def _threshold_interval(
    rt: np.ndarray,
    intensity: np.ndarray,
    candidate: BoundaryCandidateContext,
    apex_index: int,
    *,
    fraction: float,
) -> tuple[int, int] | None:
    candidate_left, candidate_right = _candidate_interval(rt, candidate)
    apex_intensity = float(intensity[apex_index])
    edge_intensity = max(
        float(intensity[candidate_left]),
        float(intensity[candidate_right - 1]),
    )
    dynamic_range = apex_intensity - edge_intensity
    if dynamic_range <= 0:
        return None
    threshold = edge_intensity + dynamic_range * fraction

    left_index = apex_index
    while left_index > 0 and float(intensity[left_index]) > threshold:
        left_index -= 1
    right_index = apex_index
    while (
        right_index < len(intensity) - 1
        and float(intensity[right_index]) > threshold
    ):
        right_index += 1
    return _valid_interval(left_index, right_index + 1, len(rt))


def _derivative_zero_crossing_interval(
    rt: np.ndarray,
    intensity: np.ndarray,
    apex_index: int,
) -> tuple[int, int]:
    left_index = apex_index
    while (
        left_index > 0
        and float(intensity[left_index]) > float(intensity[left_index - 1])
    ):
        left_index -= 1

    right_inclusive = apex_index
    while (
        right_inclusive < len(intensity) - 1
        and float(intensity[right_inclusive]) > float(intensity[right_inclusive + 1])
    ):
        right_inclusive += 1
    return _valid_interval(left_index, right_inclusive + 1, len(rt))


def _cwt_width_interval(
    rt: np.ndarray,
    candidate: BoundaryCandidateContext,
    apex_index: int,
) -> tuple[int, int] | None:
    if (
        candidate.cwt_best_scale is None
        or not math.isfinite(candidate.cwt_best_scale)
        or candidate.cwt_best_scale <= 0
    ):
        return None
    width_scans = max(2, int(round(candidate.cwt_best_scale)))
    left_index = apex_index - width_scans // 2
    right_index = left_index + width_scans
    return _valid_interval(left_index, right_index, len(rt))


def _valid_interval(
    left_index: int,
    right_index: int,
    n_points: int,
) -> tuple[int, int]:
    bounded_left = max(0, min(left_index, n_points - 2))
    bounded_right = max(bounded_left + 2, min(right_index, n_points))
    return bounded_left, bounded_right


def _nearest_index(rt: np.ndarray, value: float) -> int:
    return int(np.argmin(np.abs(rt - value)))


def _merge_duplicate_intervals(
    intervals: list[tuple[tuple[str, ...], int, int]],
) -> tuple[tuple[tuple[str, ...], int, int], ...]:
    by_bounds: dict[tuple[int, int], tuple[str, ...]] = {}
    order: list[tuple[int, int]] = []
    for sources, left_index, right_index in intervals:
        bounds = (left_index, right_index)
        if bounds not in by_bounds:
            by_bounds[bounds] = ()
            order.append(bounds)
        by_bounds[bounds] = _combine_sources(by_bounds[bounds], sources)
    return tuple((by_bounds[bounds], *bounds) for bounds in order)


def _combine_sources(
    first: tuple[str, ...],
    second: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*first, *second)))


def _build_boundary_hypothesis(
    rt: np.ndarray,
    intensity: np.ndarray,
    candidate: BoundaryCandidateContext,
    *,
    candidate_id: str,
    sources: tuple[str, ...],
    left_index: int,
    right_index: int,
) -> BoundaryHypothesis:
    rt_left = float(rt[left_index])
    rt_right = float(rt[right_index - 1])
    boundary = BoundaryHypothesis(
        boundary_id="",
        sources=sources,
        left_index=left_index,
        right_index=right_index,
        rt_left_min=rt_left,
        rt_apex_min=candidate.selection_apex_rt,
        rt_right_min=rt_right,
        width_min=rt_right - rt_left,
        area_raw_counts_seconds=integrate_area_counts_seconds(
            intensity,
            rt,
            left_index,
            right_index,
        ),
        scan_count=right_index - left_index,
    )
    return replace(
        boundary,
        boundary_id=boundary_audit_id(
            candidate_id=candidate_id,
            boundary=boundary,
        ),
    )


def _candidate_context_boundary_base_id(
    candidate: BoundaryCandidateContext,
) -> str:
    return "|".join(
        (
            _join(candidate.proposal_sources),
            _format_float(candidate.selection_apex_rt, digits=5),
        )
    )


def _format_float(value: float, *, digits: int = 5) -> str:
    return f"{value:.{digits}f}"


def _join(values: tuple[str, ...]) -> str:
    return ";".join(values)
