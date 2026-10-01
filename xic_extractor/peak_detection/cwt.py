from __future__ import annotations

import math
import warnings
from dataclasses import replace
from typing import Literal

import numpy as np
from scipy.signal import find_peaks_cwt

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.integration import (
    integrate_area_counts_seconds,
    peak_bounds,
    raw_apex_index,
)
from xic_extractor.peak_detection.models import (
    LocalMinimumRegionQuality,
    PeakCandidate,
    PeakCandidatesResult,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.trace_quality import (
    local_minimum_region_quality,
    passes_local_peak_height_filters,
)

_CWT_PROPOSAL_SOURCE = "centwave_cwt"
_APEX_MERGE_TOLERANCE_MIN = 0.08


def find_peak_candidates_centwave_cwt(
    rt: object,
    intensity: object,
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

    widths = _cwt_widths(rt_values, config)
    if len(widths) == 0:
        return _candidate_failure("WINDOW_TOO_SHORT", n_points, max_intensity)

    min_length = max(1, min(len(widths), config.resolver_min_scans) // 2)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        cwt_peak_indices = find_peaks_cwt(
            intensity_values,
            widths,
            min_length=min_length,
            min_snr=1,
        )
    peak_indices = tuple(
        sorted(
            {int(index) for index in cwt_peak_indices if 0 <= int(index) < n_points}
        )
    )
    accepted_peaks = tuple(
        peak_idx
        for peak_idx in peak_indices
        if passes_local_peak_height_filters(
            float(intensity_values[peak_idx]),
            max_intensity,
            config,
        )
    )
    if not accepted_peaks:
        return _candidate_failure("PEAK_NOT_FOUND", n_points, max_intensity)

    candidates: list[PeakCandidate] = []
    for peak_idx in accepted_peaks:
        candidate = _build_cwt_candidate(
            rt_values,
            intensity_values,
            peak_idx=peak_idx,
            widths=widths,
            min_length=min_length,
            max_intensity=max_intensity,
            source_apex_rank=len(candidates) + 1,
            config=config,
        )
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        return _candidate_failure("PEAK_NOT_FOUND", n_points, max_intensity)

    return PeakCandidatesResult(
        status="OK",
        candidates=tuple(candidates),
        n_points=n_points,
        max_smoothed=max_intensity,
        n_prominent_peaks=len(candidates),
    )


def add_cwt_proposals_for_audit(
    peak_result: PeakDetectionResult,
    rt: object,
    intensity: object,
    config: ExtractionConfig,
) -> PeakDetectionResult:
    cwt_result = find_peak_candidates_centwave_cwt(rt, intensity, config)
    if cwt_result.status != "OK" or not cwt_result.candidates:
        return peak_result

    merged = list(peak_result.candidates)
    for proposal in cwt_result.candidates:
        match_index = _matching_apex_index(
            merged,
            proposal,
            selected_peak=peak_result.peak,
        )
        if match_index is None:
            merged.append(proposal)
            continue
        merged[match_index] = _merge_cwt_proposal(merged[match_index], proposal)
    return replace(peak_result, candidates=tuple(merged))


def _build_cwt_candidate(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    *,
    peak_idx: int,
    widths: np.ndarray,
    min_length: int,
    max_intensity: float,
    source_apex_rank: int,
    config: ExtractionConfig,
) -> PeakCandidate | None:
    n_points = len(intensity_values)
    left, right = peak_bounds(
        intensity_values,
        peak_idx,
        config.peak_rel_height,
        n_points,
    )
    if right <= left:
        return None
    quality = local_minimum_region_quality(
        rt_values,
        intensity_values,
        left=left,
        right=right,
        max_intensity=max_intensity,
        config=config,
    )
    if quality is None:
        return None
    return _candidate_from_bounds(
        rt_values,
        intensity_values,
        left=left,
        right=right,
        quality=quality,
        widths=widths,
        min_length=min_length,
        source_apex_rank=source_apex_rank,
    )


def _candidate_from_bounds(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    *,
    left: int,
    right: int,
    quality: LocalMinimumRegionQuality,
    widths: np.ndarray,
    min_length: int,
    source_apex_rank: int,
) -> PeakCandidate:
    raw_idx = raw_apex_index(intensity_values, left, right)
    apex_intensity = float(intensity_values[raw_idx])
    edge_height = max(float(intensity_values[left]), float(intensity_values[right - 1]))
    area = integrate_area_counts_seconds(intensity_values, rt_values, left, right)
    peak = PeakResult(
        rt=float(rt_values[raw_idx]),
        intensity=apex_intensity,
        intensity_smoothed=apex_intensity,
        area=area,
        peak_start=float(rt_values[left]),
        peak_end=float(rt_values[right - 1]),
    )
    best_scale = _best_scale_for_width(widths, right - left)
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=peak.rt,
        selection_apex_intensity=apex_intensity,
        selection_apex_index=raw_idx,
        raw_apex_rt=peak.rt,
        raw_apex_intensity=apex_intensity,
        raw_apex_index=raw_idx,
        prominence=max(0.0, apex_intensity - edge_height),
        quality_flags=quality.flags,
        region_scan_count=quality.scan_count,
        region_duration_min=quality.duration_min,
        region_edge_ratio=quality.edge_ratio,
        region_trace_continuity=quality.trace_continuity,
        cwt_best_scale=best_scale,
        cwt_ridge_persistence=min(1.0, min_length / max(1, len(widths))),
        proposal_sources=(_CWT_PROPOSAL_SOURCE,),
        source_apex_rank=source_apex_rank,
    )


def _cwt_widths(rt_values: np.ndarray, config: ExtractionConfig) -> np.ndarray:
    rt_step = _median_rt_step(rt_values)
    if rt_step is None or rt_step <= 0:
        return np.array([], dtype=float)
    min_width = max(1, int(math.floor(max(1, config.resolver_min_scans) / 2)))
    max_width_by_duration = max(
        min_width,
        int(math.ceil(config.resolver_peak_duration_max / rt_step)),
    )
    max_width = min(max_width_by_duration, max(min_width, len(rt_values) // 2))
    return np.arange(min_width, max_width + 1, dtype=float)


def _median_rt_step(rt_values: np.ndarray) -> float | None:
    if len(rt_values) < 2:
        return None
    diffs = np.diff(rt_values)
    positive = diffs[diffs > 0]
    if len(positive) == 0:
        return None
    return float(np.median(positive))


def _best_scale_for_width(widths: np.ndarray, width_scans: int) -> float | None:
    if len(widths) == 0:
        return None
    index = int(np.argmin(np.abs(widths - float(width_scans))))
    return float(widths[index])


def _matching_apex_index(
    candidates: list[PeakCandidate],
    proposal: PeakCandidate,
    *,
    selected_peak: PeakResult | None,
) -> int | None:
    matches: list[int] = []
    for index, candidate in enumerate(candidates):
        if (
            abs(candidate.selection_apex_rt - proposal.selection_apex_rt)
            <= _APEX_MERGE_TOLERANCE_MIN
        ):
            matches.append(index)
    if not matches:
        return None
    if selected_peak is not None:
        for index in matches:
            if candidates[index].peak == selected_peak:
                return index
    return matches[0]


def _merge_cwt_proposal(
    existing: PeakCandidate,
    proposal: PeakCandidate,
) -> PeakCandidate:
    return replace(
        existing,
        proposal_sources=_append_source(
            existing.proposal_sources,
            _CWT_PROPOSAL_SOURCE,
        ),
        cwt_best_scale=proposal.cwt_best_scale,
        cwt_ridge_persistence=proposal.cwt_ridge_persistence,
        merge_note=_merge_note(existing.merge_note),
    )


def _append_source(values: tuple[str, ...], source: str) -> tuple[str, ...]:
    if source in values:
        return values
    return (*values, source)


def _merge_note(existing: str) -> str:
    if not existing:
        return "same_apex_cwt_audit"
    if "same_apex_cwt_audit" in existing:
        return existing
    return f"{existing};same_apex_cwt_audit"


def _as_matching_arrays(
    rt: object,
    intensity: object,
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
