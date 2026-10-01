from typing import Literal

import numpy as np
from scipy.signal import find_peaks

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.integration import (
    integrate_area_counts_seconds,
    raw_apex_index,
)
from xic_extractor.peak_detection.models import (
    LocalMinimumRegionQuality,
    PeakCandidate,
    PeakCandidatesResult,
    PeakResult,
)
from xic_extractor.peak_detection.trace_quality import (
    local_minimum_region_quality,
    passes_local_peak_height_filters,
)


def find_peak_candidates_local_minimum(
    rt: np.ndarray,
    intensity: np.ndarray,
    config: ExtractionConfig,
) -> PeakCandidatesResult:
    rt_values, intensity_values = _as_matching_arrays(rt, intensity)
    n_points = len(intensity_values)
    if n_points == 0:
        return _candidate_failure("NO_SIGNAL", n_points, None)
    if n_points < max(config.resolver_min_scans, 3):
        return _candidate_failure("WINDOW_TOO_SHORT", n_points, None)

    max_intensity = float(np.max(intensity_values))
    if max_intensity <= 0:
        return _candidate_failure("NO_SIGNAL", n_points, max_intensity)

    threshold = _local_minimum_threshold(
        intensity_values,
        config.resolver_chrom_threshold,
    )
    peak_indices = _local_peak_indices(intensity_values)
    accepted_peaks = [
        peak_idx
        for peak_idx in peak_indices
        if passes_local_peak_height_filters(
            intensity_values[peak_idx],
            max_intensity,
            config,
        )
    ]
    if not accepted_peaks:
        return _candidate_failure("PEAK_NOT_FOUND", n_points, max_intensity)

    regions = _local_minimum_regions(
        rt_values,
        intensity_values,
        accepted_peaks,
        threshold,
        config,
    )
    candidates: list[PeakCandidate] = []
    for left, right in regions:
        quality = local_minimum_region_quality(
            rt_values,
            intensity_values,
            left=left,
            right=right,
            max_intensity=max_intensity,
            config=config,
        )
        if quality is None:
            continue
        candidates.append(
            _build_local_minimum_candidate(
                rt_values,
                intensity_values,
                left=left,
                right=right,
                quality=quality,
                source_apex_rank=len(candidates) + 1,
            )
        )
    candidates_result = tuple(candidates)
    if not candidates_result:
        return _candidate_failure("PEAK_NOT_FOUND", n_points, max_intensity)

    return PeakCandidatesResult(
        status="OK",
        candidates=candidates_result,
        n_points=n_points,
        max_smoothed=max_intensity,
        n_prominent_peaks=len(candidates_result),
    )


def _as_matching_arrays(
    rt: np.ndarray, intensity: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    rt_values = np.asarray(rt, dtype=float)
    intensity_values = np.asarray(intensity, dtype=float)
    if len(rt_values) != len(intensity_values):
        raise ValueError("rt and intensity must have the same length")
    return rt_values, intensity_values


def _candidate_failure(
    status: Literal["NO_SIGNAL", "WINDOW_TOO_SHORT", "PEAK_NOT_FOUND"],
    n_points: int,
    max_smoothed: float | None,
) -> PeakCandidatesResult:
    return PeakCandidatesResult(
        status=status,
        candidates=(),
        n_points=n_points,
        max_smoothed=max_smoothed,
        n_prominent_peaks=0,
    )


def _build_local_minimum_candidate(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    *,
    left: int,
    right: int,
    quality: LocalMinimumRegionQuality,
    source_apex_rank: int,
) -> PeakCandidate:
    raw_apex_idx = raw_apex_index(intensity_values, left, right)
    apex_intensity = float(intensity_values[raw_apex_idx])
    edge_height = max(float(intensity_values[left]), float(intensity_values[right - 1]))
    area = integrate_area_counts_seconds(intensity_values, rt_values, left, right)

    peak = PeakResult(
        rt=float(rt_values[raw_apex_idx]),
        intensity=apex_intensity,
        intensity_smoothed=apex_intensity,
        area=area,
        peak_start=float(rt_values[left]),
        peak_end=float(rt_values[right - 1]),
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=peak.rt,
        selection_apex_intensity=apex_intensity,
        selection_apex_index=raw_apex_idx,
        raw_apex_rt=peak.rt,
        raw_apex_intensity=apex_intensity,
        raw_apex_index=raw_apex_idx,
        prominence=max(0.0, apex_intensity - edge_height),
        quality_flags=quality.flags,
        region_scan_count=quality.scan_count,
        region_duration_min=quality.duration_min,
        region_edge_ratio=quality.edge_ratio,
        region_trace_continuity=quality.trace_continuity,
        proposal_sources=("local_minimum",),
        source_apex_rank=source_apex_rank,
    )


def _local_minimum_threshold(
    intensity_values: np.ndarray, chrom_threshold: float
) -> float:
    baseline = float(np.min(intensity_values))
    apex = float(np.max(intensity_values))
    return baseline + (apex - baseline) * chrom_threshold


def _local_peak_indices(intensity_values: np.ndarray) -> list[int]:
    peak_indices = [int(index) for index in find_peaks(intensity_values)[0]]
    if len(intensity_values) == 1:
        return [0]
    if intensity_values[0] > intensity_values[1]:
        peak_indices.insert(0, 0)
    if intensity_values[-1] > intensity_values[-2]:
        peak_indices.append(len(intensity_values) - 1)
    if not peak_indices:
        peak_indices.append(int(np.argmax(intensity_values)))
    return sorted(set(peak_indices))


def _local_minimum_regions(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    peak_indices: list[int],
    threshold: float,
    config: ExtractionConfig,
) -> list[tuple[int, int]]:
    if len(peak_indices) == 1:
        peak_idx = peak_indices[0]
        return [
            (
                _left_threshold_boundary(intensity_values, peak_idx, threshold),
                _right_threshold_boundary(intensity_values, peak_idx, threshold),
            )
        ]

    regions: list[tuple[int, int]] = []
    region_left = _left_threshold_boundary(intensity_values, peak_indices[0], threshold)
    last_peak = peak_indices[0]
    for peak_idx in peak_indices[1:]:
        valley_idx = _valley_index(intensity_values, last_peak, peak_idx)
        if _should_split_local_region(
            rt_values,
            intensity_values,
            left_peak=last_peak,
            right_peak=peak_idx,
            valley_idx=valley_idx,
            config=config,
        ):
            regions.append((region_left, valley_idx + 1))
            region_left = min(valley_idx + 1, peak_idx)
        last_peak = peak_idx
    region_right = _right_threshold_boundary(
        intensity_values,
        peak_indices[-1],
        threshold,
    )
    regions.append((region_left, region_right))
    return regions


def _left_threshold_boundary(
    intensity_values: np.ndarray, peak_idx: int, threshold: float
) -> int:
    left = peak_idx
    while left > 0 and intensity_values[left - 1] > threshold:
        left -= 1
    return left


def _right_threshold_boundary(
    intensity_values: np.ndarray, peak_idx: int, threshold: float
) -> int:
    right = peak_idx + 1
    while right < len(intensity_values) and intensity_values[right] > threshold:
        right += 1
    return right


def _valley_index(intensity_values: np.ndarray, left_peak: int, right_peak: int) -> int:
    valley_slice = intensity_values[left_peak : right_peak + 1]
    return left_peak + int(np.argmin(valley_slice))


def _should_split_local_region(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    *,
    left_peak: int,
    right_peak: int,
    valley_idx: int,
    config: ExtractionConfig,
) -> bool:
    if (
        rt_values[right_peak] - rt_values[left_peak]
        < config.resolver_min_search_range_min
    ):
        return False
    valley_height = float(intensity_values[valley_idx])
    if valley_height <= 0:
        return True
    left_ratio = float(intensity_values[left_peak]) / valley_height
    right_ratio = float(intensity_values[right_peak]) / valley_height
    return (
        left_ratio >= config.resolver_min_ratio_top_edge
        and right_ratio >= config.resolver_min_ratio_top_edge
    )
