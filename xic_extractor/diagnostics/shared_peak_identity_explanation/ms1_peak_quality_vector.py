from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

PEAK_QUALITY_VECTOR_BASIS = "family_ms1_overlay_raw_trace_vector"
_MIN_TRACE_VECTOR_POINTS = 5
_MIN_BOUNDARY_VECTOR_POINTS = 3
_SUPPORTIVE_SIGNAL_TO_NOISE = 3.0
_MAX_SUPPORTIVE_ZIGZAG_SCORE = 0.60
_MIN_SUPPORTIVE_SHARPNESS_SCORE = 0.10


@dataclass(frozen=True)
class PeakQualityVector:
    status: str
    basis: str
    trace_point_count: int
    boundary_point_count: int
    signal_to_noise_proxy: float | None
    fwhm_sec: float | None
    sharpness_score: float | None
    zigzag_score: float | None
    tailing_ratio: float | None
    boundary_margin_ratio: float | None
    feature_count: int
    reason: str


def build_peak_quality_vector(
    *,
    trace_rt: Sequence[float],
    trace_intensity: Sequence[float],
    cell_start_rt: float | None,
    cell_end_rt: float | None,
) -> PeakQualityVector:
    pairs = _finite_trace_pairs(trace_rt, trace_intensity)
    if not pairs:
        return _empty_peak_quality_vector("no_raw_trace_vector")
    if len(pairs) < _MIN_TRACE_VECTOR_POINTS:
        return _empty_peak_quality_vector(
            "insufficient_raw_trace_vector_points",
            trace_point_count=len(pairs),
        )
    if cell_start_rt is None or cell_end_rt is None:
        return _empty_peak_quality_vector(
            "cell_boundary_missing_for_peak_quality_vector",
            trace_point_count=len(pairs),
        )
    if cell_end_rt <= cell_start_rt:
        return _empty_peak_quality_vector(
            "cell_boundary_invalid_for_peak_quality_vector",
            trace_point_count=len(pairs),
        )

    boundary_pairs = tuple(
        pair for pair in pairs if cell_start_rt <= pair[0] <= cell_end_rt
    )
    if len(boundary_pairs) < _MIN_BOUNDARY_VECTOR_POINTS:
        return _empty_peak_quality_vector(
            "insufficient_boundary_vector_points",
            trace_point_count=len(pairs),
            boundary_point_count=len(boundary_pairs),
        )

    boundary_indices = tuple(
        index
        for index, pair in enumerate(pairs)
        if cell_start_rt <= pair[0] <= cell_end_rt
    )
    apex_index = max(boundary_indices, key=lambda index: pairs[index][1])
    baseline, noise = _baseline_and_noise(pairs, boundary_indices)
    apex_intensity = pairs[apex_index][1]
    signal = max(0.0, apex_intensity - baseline)
    signal_to_noise = signal / noise if noise is not None and noise > 0 else None
    fwhm_sec, tailing_ratio = _half_height_metrics(
        pairs,
        apex_index=apex_index,
        baseline=baseline,
    )
    sharpness_score = _sharpness_score(boundary_pairs, apex_intensity)
    zigzag_score = _zigzag_score(boundary_pairs)
    boundary_margin_ratio = _boundary_margin_ratio(pairs, boundary_indices)
    features = (
        signal_to_noise,
        fwhm_sec,
        sharpness_score,
        zigzag_score,
        tailing_ratio,
        boundary_margin_ratio,
    )
    feature_count = sum(value is not None for value in features)
    status, reason = _peak_quality_status_and_reason(
        signal_to_noise=signal_to_noise,
        fwhm_sec=fwhm_sec,
        sharpness_score=sharpness_score,
        zigzag_score=zigzag_score,
        feature_count=feature_count,
    )
    return PeakQualityVector(
        status=status,
        basis=PEAK_QUALITY_VECTOR_BASIS,
        trace_point_count=len(pairs),
        boundary_point_count=len(boundary_pairs),
        signal_to_noise_proxy=signal_to_noise,
        fwhm_sec=fwhm_sec,
        sharpness_score=sharpness_score,
        zigzag_score=zigzag_score,
        tailing_ratio=tailing_ratio,
        boundary_margin_ratio=boundary_margin_ratio,
        feature_count=feature_count,
        reason=reason,
    )


def _empty_peak_quality_vector(
    reason: str,
    *,
    trace_point_count: int = 0,
    boundary_point_count: int = 0,
) -> PeakQualityVector:
    return PeakQualityVector(
        status="not_available",
        basis="",
        trace_point_count=trace_point_count,
        boundary_point_count=boundary_point_count,
        signal_to_noise_proxy=None,
        fwhm_sec=None,
        sharpness_score=None,
        zigzag_score=None,
        tailing_ratio=None,
        boundary_margin_ratio=None,
        feature_count=0,
        reason=reason,
    )


def _finite_trace_pairs(
    rt_values: Sequence[float],
    intensity_values: Sequence[float],
) -> tuple[tuple[float, float], ...]:
    pairs = [
        (rt, intensity)
        for rt, intensity in zip(rt_values, intensity_values, strict=False)
        if math.isfinite(rt) and math.isfinite(intensity)
    ]
    return tuple(sorted(pairs, key=lambda pair: pair[0]))


def _baseline_and_noise(
    pairs: Sequence[tuple[float, float]],
    boundary_indices: Sequence[int],
) -> tuple[float, float | None]:
    boundary_index_set = set(boundary_indices)
    outside_values = [
        intensity
        for index, (_rt, intensity) in enumerate(pairs)
        if index not in boundary_index_set
    ]
    baseline_values = outside_values if len(outside_values) >= 3 else [
        intensity for _rt, intensity in pairs
    ]
    baseline = statistics.median(baseline_values)
    noise = _median_abs_deviation(baseline_values, baseline)
    if noise is None or noise <= 0:
        noise = _median_abs_diff(tuple(intensity for _rt, intensity in pairs))
    return baseline, noise


def _median_abs_deviation(
    values: Sequence[float],
    center: float,
) -> float | None:
    if not values:
        return None
    return statistics.median(abs(value - center) for value in values) * 1.4826


def _median_abs_diff(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    diffs = [abs(right - left) for left, right in zip(values, values[1:])]
    if not diffs:
        return None
    return statistics.median(diffs) * 0.7413


def _half_height_metrics(
    pairs: Sequence[tuple[float, float]],
    *,
    apex_index: int,
    baseline: float,
) -> tuple[float | None, float | None]:
    apex_rt, apex_intensity = pairs[apex_index]
    if apex_intensity <= baseline:
        return None, None
    half_height = baseline + (apex_intensity - baseline) / 2.0
    left_rt = _half_height_crossing(
        pairs,
        apex_index=apex_index,
        half_height=half_height,
        step=-1,
    )
    right_rt = _half_height_crossing(
        pairs,
        apex_index=apex_index,
        half_height=half_height,
        step=1,
    )
    if left_rt is None or right_rt is None or right_rt <= left_rt:
        return None, None
    left_width = max(0.0, apex_rt - left_rt)
    right_width = max(0.0, right_rt - apex_rt)
    tailing_ratio = right_width / left_width if left_width > 0 else None
    return (right_rt - left_rt) * 60.0, tailing_ratio


def _half_height_crossing(
    pairs: Sequence[tuple[float, float]],
    *,
    apex_index: int,
    half_height: float,
    step: int,
) -> float | None:
    previous_rt, previous_intensity = pairs[apex_index]
    index = apex_index + step
    while 0 <= index < len(pairs):
        current_rt, current_intensity = pairs[index]
        if current_intensity <= half_height:
            delta = previous_intensity - current_intensity
            if delta == 0:
                return current_rt
            fraction = (half_height - current_intensity) / delta
            return current_rt + (previous_rt - current_rt) * fraction
        previous_rt, previous_intensity = current_rt, current_intensity
        index += step
    return None


def _sharpness_score(
    boundary_pairs: Sequence[tuple[float, float]],
    apex_intensity: float,
) -> float | None:
    if apex_intensity <= 0 or len(boundary_pairs) < 3:
        return None
    edge_intensity = max(boundary_pairs[0][1], boundary_pairs[-1][1])
    return max(0.0, min(1.0, (apex_intensity - edge_intensity) / apex_intensity))


def _zigzag_score(boundary_pairs: Sequence[tuple[float, float]]) -> float | None:
    if len(boundary_pairs) < 4:
        return None
    intensities = [intensity for _rt, intensity in boundary_pairs]
    deltas = [
        right - left
        for left, right in zip(intensities, intensities[1:])
        if right != left
    ]
    if len(deltas) < 2:
        return 0.0
    sign_changes = sum(
        (left_delta > 0) != (right_delta > 0)
        for left_delta, right_delta in zip(deltas, deltas[1:])
    )
    return sign_changes / (len(deltas) - 1)


def _boundary_margin_ratio(
    pairs: Sequence[tuple[float, float]],
    boundary_indices: Sequence[int],
) -> float | None:
    boundary_index_set = set(boundary_indices)
    inside_max = max(pairs[index][1] for index in boundary_indices)
    outside_values = [
        intensity
        for index, (_rt, intensity) in enumerate(pairs)
        if index not in boundary_index_set
    ]
    if not outside_values:
        return None
    outside_max = max(outside_values)
    if outside_max <= 0:
        return None
    return inside_max / outside_max


def _peak_quality_status_and_reason(
    *,
    signal_to_noise: float | None,
    fwhm_sec: float | None,
    sharpness_score: float | None,
    zigzag_score: float | None,
    feature_count: int,
) -> tuple[str, str]:
    if feature_count < 4:
        return "inconclusive", "raw_trace_peak_quality_vector_incomplete"
    reasons: list[str] = []
    if signal_to_noise is not None and signal_to_noise < _SUPPORTIVE_SIGNAL_TO_NOISE:
        reasons.append("low_signal_to_noise_proxy")
    if fwhm_sec is None:
        reasons.append("fwhm_unavailable")
    if (
        sharpness_score is not None
        and sharpness_score < _MIN_SUPPORTIVE_SHARPNESS_SCORE
    ):
        reasons.append("low_peak_sharpness")
    if zigzag_score is not None and zigzag_score > _MAX_SUPPORTIVE_ZIGZAG_SCORE:
        reasons.append("high_local_zigzag")
    if reasons:
        return "partial_support", "raw_trace_peak_quality_vector_partial_" + (
            "_".join(reasons)
        )
    return "supportive", "raw_trace_peak_quality_vector_supportive"
