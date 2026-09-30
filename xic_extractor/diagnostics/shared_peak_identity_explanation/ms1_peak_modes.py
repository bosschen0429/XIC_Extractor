from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from xic_extractor.peak_detection.ms1_morphology import (
    DEFAULT_GAUSSIAN15_WINDOW_POINTS,
    gaussian15_morphology_trace,
)
from xic_extractor.tabular_io import text_value

GAUSSIAN15_MODE_GAP_MIN = 0.35
GAUSSIAN15_MODE_MIN_CLUSTER_SIZE = 2
GAUSSIAN15_MODE_OUTER_MARGIN_MIN = 0.20
GAUSSIAN15_TRACE_PEAK_GAP_MIN = 0.25
GAUSSIAN15_TRACE_EDGE_MARGIN_MIN = 0.20
GAUSSIAN15_TRACE_PEAK_HEIGHT_FRACTION_MIN = 0.20
GAUSSIAN15_TRACE_PEAK_PROMINENCE_FRACTION_MIN = 0.08


@dataclass(frozen=True)
class Gaussian15PeakObservation:
    sample_stem: str
    apex_rt: float
    height: float
    detected_seed: bool


@dataclass(frozen=True)
class Gaussian15PeakModeWindow:
    mode_id: str
    start_rt: float
    end_rt: float
    apex_rt: float
    trace_peak_count: int
    detected_seed_count: int
    reason: str = "gaussian15_trace_multipeak_mode_window"


def infer_gaussian15_peak_mode_windows(
    trace_rows: Sequence[Mapping[str, object]],
    *,
    rt_min: float | None = None,
    rt_max: float | None = None,
    window_points: int = DEFAULT_GAUSSIAN15_WINDOW_POINTS,
) -> tuple[Gaussian15PeakModeWindow, ...]:
    observations_by_sample: dict[str, tuple[Gaussian15PeakObservation, ...]] = {}
    for trace_row in trace_rows:
        peaks = gaussian15_peak_observations(
            trace_row,
            window_points=window_points,
        )
        if peaks:
            observations_by_sample[peaks[0].sample_stem] = peaks
    if max((len(peaks) for peaks in observations_by_sample.values()), default=0) <= 1:
        return ()
    observations = tuple(
        observation
        for peaks in observations_by_sample.values()
        for observation in peaks
    )
    if len(observations) < GAUSSIAN15_MODE_MIN_CLUSTER_SIZE * 2:
        return ()
    clusters = _cluster_peak_observations(observations)
    clusters = [
        cluster
        for cluster in clusters
        if len(cluster) >= GAUSSIAN15_MODE_MIN_CLUSTER_SIZE
    ]
    if len(clusters) <= 1:
        return ()

    medians = [_median_apex(cluster) for cluster in clusters]
    windows: list[Gaussian15PeakModeWindow] = []
    for index, cluster in enumerate(clusters):
        median_rt = medians[index]
        if index == 0:
            start_rt = min(item.apex_rt for item in cluster) - (
                GAUSSIAN15_MODE_OUTER_MARGIN_MIN
            )
            if rt_min is not None:
                start_rt = max(rt_min, start_rt)
        else:
            start_rt = (medians[index - 1] + median_rt) / 2.0

        if index == len(clusters) - 1:
            end_rt = max(item.apex_rt for item in cluster) + (
                GAUSSIAN15_MODE_OUTER_MARGIN_MIN
            )
            if rt_max is not None:
                end_rt = min(rt_max, end_rt)
        else:
            end_rt = (median_rt + medians[index + 1]) / 2.0

        if end_rt <= start_rt:
            continue
        windows.append(
            Gaussian15PeakModeWindow(
                mode_id=f"gaussian15_mode_{index + 1}_{median_rt:.2f}min",
                start_rt=start_rt,
                end_rt=end_rt,
                apex_rt=median_rt,
                trace_peak_count=len(cluster),
                detected_seed_count=len(
                    {
                        item.sample_stem
                        for item in cluster
                        if item.detected_seed
                    }
                ),
            )
        )
    return tuple(windows)


def gaussian15_peak_observations(
    trace_row: Mapping[str, object],
    *,
    window_points: int = DEFAULT_GAUSSIAN15_WINDOW_POINTS,
) -> tuple[Gaussian15PeakObservation, ...]:
    sample_stem = text_value(trace_row.get("sample_stem"))
    if not sample_stem:
        return ()
    rt_values = _float_values(trace_row.get("rt") or trace_row.get("raw_rt"))
    intensity_values = _float_values(
        trace_row.get("intensity") or trace_row.get("raw_intensity")
    )
    limit = min(len(rt_values), len(intensity_values))
    if limit < 3:
        return ()
    rt = np.asarray(rt_values[:limit], dtype=float)
    intensity = np.maximum(np.asarray(intensity_values[:limit], dtype=float), 0.0)
    finite = np.isfinite(rt) & np.isfinite(intensity)
    if int(np.sum(finite)) < 3:
        return ()
    rt = rt[finite]
    intensity = intensity[finite]
    if rt.size < 3:
        return ()
    smoothed = gaussian15_morphology_trace(intensity, window_points=window_points)
    max_height = float(np.max(smoothed)) if smoothed.size else 0.0
    if max_height <= 0:
        return ()

    candidates: list[Gaussian15PeakObservation] = []
    for index in range(1, smoothed.size - 1):
        height = float(smoothed[index])
        if height < max_height * GAUSSIAN15_TRACE_PEAK_HEIGHT_FRACTION_MIN:
            continue
        is_local_maximum = (
            height >= float(smoothed[index - 1])
            and height > float(smoothed[index + 1])
        )
        if not is_local_maximum:
            continue
        left_min = float(np.min(smoothed[: index + 1]))
        right_min = float(np.min(smoothed[index:]))
        prominence = height - max(left_min, right_min)
        if prominence < max_height * GAUSSIAN15_TRACE_PEAK_PROMINENCE_FRACTION_MIN:
            continue
        apex_rt = float(rt[index])
        if (
            apex_rt - float(np.min(rt)) < GAUSSIAN15_TRACE_EDGE_MARGIN_MIN
            or float(np.max(rt)) - apex_rt < GAUSSIAN15_TRACE_EDGE_MARGIN_MIN
        ):
            continue
        candidates.append(
            Gaussian15PeakObservation(
                sample_stem=sample_stem,
                apex_rt=apex_rt,
                height=height,
                detected_seed=_trace_is_detected_seed(trace_row),
            )
        )
    return _non_overlapping_peaks(candidates)


def trace_has_gaussian15_peak_in_window(
    trace_row: Mapping[str, object],
    *,
    start_rt: float,
    end_rt: float,
    window_points: int = DEFAULT_GAUSSIAN15_WINDOW_POINTS,
) -> bool:
    return any(
        start_rt <= observation.apex_rt <= end_rt
        for observation in gaussian15_peak_observations(
            trace_row,
            window_points=window_points,
        )
    )


def detected_seed_has_gaussian15_peak_in_window(
    trace_rows: Sequence[Mapping[str, object]],
    *,
    start_rt: float,
    end_rt: float,
    window_points: int = DEFAULT_GAUSSIAN15_WINDOW_POINTS,
) -> bool:
    return any(
        observation.detected_seed and start_rt <= observation.apex_rt <= end_rt
        for trace_row in trace_rows
        for observation in gaussian15_peak_observations(
            trace_row,
            window_points=window_points,
        )
    )


def _cluster_peak_observations(
    observations: Sequence[Gaussian15PeakObservation],
) -> list[list[Gaussian15PeakObservation]]:
    ordered = sorted(observations, key=lambda item: item.apex_rt)
    clusters: list[list[Gaussian15PeakObservation]] = []
    current: list[Gaussian15PeakObservation] = []
    for observation in ordered:
        if (
            current
            and observation.apex_rt - current[-1].apex_rt > GAUSSIAN15_MODE_GAP_MIN
        ):
            clusters.append(current)
            current = []
        current.append(observation)
    if current:
        clusters.append(current)
    return clusters


def _median_apex(cluster: Sequence[Gaussian15PeakObservation]) -> float:
    values = sorted(item.apex_rt for item in cluster)
    return values[len(values) // 2]


def _non_overlapping_peaks(
    candidates: Sequence[Gaussian15PeakObservation],
) -> tuple[Gaussian15PeakObservation, ...]:
    selected: list[Gaussian15PeakObservation] = []
    for candidate in sorted(candidates, key=lambda item: item.height, reverse=True):
        if all(
            abs(candidate.apex_rt - existing.apex_rt) >= GAUSSIAN15_TRACE_PEAK_GAP_MIN
            for existing in selected
        ):
            selected.append(candidate)
    return tuple(sorted(selected, key=lambda item: item.apex_rt))


def _trace_is_detected_seed(trace_row: Mapping[str, object]) -> bool:
    return (
        text_value(trace_row.get("status")) == "detected"
        or text_value(trace_row.get("group")) == "detected_seed"
    )


def _float_values(value: object) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    values: list[float] = []
    for item in value:
        try:
            values.append(float(item))
        except (TypeError, ValueError):
            continue
    return tuple(values)
