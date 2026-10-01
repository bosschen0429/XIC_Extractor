from typing import Literal

import numpy as np
from scipy.signal import find_peaks, savgol_filter

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.integration import (
    integrate_area_counts_seconds,
    peak_bounds,
    raw_apex_index,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidatesResult,
    PeakResult,
)


def find_peak_candidates_savgol(
    rt: np.ndarray,
    intensity: np.ndarray,
    config: ExtractionConfig,
    *,
    peak_min_prominence_ratio: float | None = None,
) -> PeakCandidatesResult:
    rt_values, intensity_values = _as_matching_arrays(rt, intensity)
    n_points = len(intensity_values)
    if n_points == 0:
        return _candidate_failure("NO_SIGNAL", n_points, None)
    if n_points < config.smooth_window:
        return _candidate_failure("WINDOW_TOO_SHORT", n_points, None)

    smoothed = savgol_filter(
        intensity_values, config.smooth_window, config.smooth_polyorder
    )
    max_smoothed = float(np.max(smoothed))
    if max_smoothed <= 0:
        return _candidate_failure("NO_SIGNAL", n_points, max_smoothed)

    prominence = _prominence_threshold(
        intensity_values,
        smoothed,
        max_smoothed,
        (
            config.peak_min_prominence_ratio
            if peak_min_prominence_ratio is None
            else peak_min_prominence_ratio
        ),
    )
    peaks, properties = find_peaks(smoothed, prominence=prominence)
    if len(peaks) == 0:
        return _candidate_failure("PEAK_NOT_FOUND", n_points, max_smoothed)

    prominences = properties.get("prominences", np.zeros(len(peaks), dtype=float))
    candidates = tuple(
        _build_candidate(
            rt_values,
            intensity_values,
            smoothed,
            selection_apex_idx=int(peak_idx),
            prominence=float(prominences[index]),
            peak_rel_height=config.peak_rel_height,
            source_apex_rank=index + 1,
        )
        for index, peak_idx in enumerate(peaks)
    )

    return PeakCandidatesResult(
        status="OK",
        candidates=candidates,
        n_points=n_points,
        max_smoothed=max_smoothed,
        n_prominent_peaks=len(peaks),
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


def _build_candidate(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    smoothed: np.ndarray,
    *,
    selection_apex_idx: int,
    prominence: float,
    peak_rel_height: float,
    source_apex_rank: int,
) -> PeakCandidate:
    n_points = len(intensity_values)
    left, right = peak_bounds(smoothed, selection_apex_idx, peak_rel_height, n_points)
    raw_apex_idx = raw_apex_index(intensity_values, left, right)
    area = integrate_area_counts_seconds(intensity_values, rt_values, left, right)

    peak = PeakResult(
        rt=float(rt_values[selection_apex_idx]),
        intensity=float(intensity_values[raw_apex_idx]),
        intensity_smoothed=float(smoothed[selection_apex_idx]),
        area=area,
        peak_start=float(rt_values[left]),
        peak_end=float(rt_values[right - 1]),
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=float(rt_values[selection_apex_idx]),
        selection_apex_intensity=float(smoothed[selection_apex_idx]),
        selection_apex_index=selection_apex_idx,
        raw_apex_rt=float(rt_values[raw_apex_idx]),
        raw_apex_intensity=peak.intensity,
        raw_apex_index=raw_apex_idx,
        prominence=prominence,
        proposal_sources=("legacy_savgol",),
        source_apex_rank=source_apex_rank,
    )


def _prominence_threshold(
    intensity: np.ndarray,
    smoothed: np.ndarray,
    max_smoothed: float,
    peak_min_prominence_ratio: float,
) -> float:
    residual = intensity - smoothed
    median = float(np.median(residual))
    mad = float(np.median(np.abs(residual - median)))
    noise_floor = 3.0 * 1.4826 * mad
    return max(max_smoothed * peak_min_prominence_ratio, noise_floor)
