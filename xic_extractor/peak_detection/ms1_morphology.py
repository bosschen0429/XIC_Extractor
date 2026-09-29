from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
from scipy.signal import peak_widths

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)

MS1_MORPHOLOGY_TRACE_METHOD = "gaussian_15"
MS1_MORPHOLOGY_AREA_SOURCE = "gaussian15_positive_asls_residual"
DEFAULT_GAUSSIAN15_WINDOW_POINTS = 15


def compute_shape_metrics(
    rt: np.ndarray,
    intensity: np.ndarray,
    apex_index: int,
) -> tuple[float, float | None]:
    """Return the RT half-width ratio and half-prominence width in minutes."""
    rt_values = np.asarray(rt, dtype=float)
    values = np.asarray(intensity, dtype=float)
    if len(values) == 0 or apex_index < 0 or apex_index >= len(values):
        return 1.0, None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        widths, _, left_ips, right_ips = peak_widths(
            values,
            [apex_index],
            rel_height=0.5,
        )
    if len(widths) == 0:
        return 1.0, None
    left_rt, right_rt = np.interp(
        [left_ips[0], right_ips[0]],
        np.arange(len(values)),
        rt_values,
    )
    width_min = float(right_rt - left_rt)
    if not np.isfinite(width_min) or width_min <= 0:
        return 1.0, None
    left = float(rt_values[apex_index] - left_rt)
    right = float(right_rt - rt_values[apex_index])
    if left <= 0 or right <= 0:
        return 1.0, width_min
    return float(left / right), width_min


@dataclass(frozen=True)
class Ms1MorphologyMetrics:
    trace_method: str
    trace_window_points: int
    trace_effective_points: int
    area_positive_asls_residual: float | None
    area_source: str


def configured_morphology_window_points(config: object) -> int:
    return int(
        getattr(
            config,
            "ms1_morphology_smoothing_window_points",
            DEFAULT_GAUSSIAN15_WINDOW_POINTS,
        )
    )


def gaussian15_morphology_trace(
    residual: np.ndarray,
    *,
    window_points: int = DEFAULT_GAUSSIAN15_WINDOW_POINTS,
) -> np.ndarray:
    """Return the Xcalibur-like Gaussian15 morphology trace."""
    values = np.asarray(residual, dtype=float)
    if values.ndim != 1:
        raise ValueError("residual must be one-dimensional")
    window = morphology_trace_effective_points(
        len(values),
        method=MS1_MORPHOLOGY_TRACE_METHOD,
        window_points=window_points,
    )
    if window <= 1:
        return values.copy()
    kernel = _gaussian_kernel(window)
    return np.convolve(values, kernel, mode="same")


def positive_residual_area(
    rt_values: np.ndarray,
    residual_values: np.ndarray,
    left_index: int,
    right_index: int,
) -> float:
    rt = np.asarray(rt_values, dtype=float)
    residual = np.asarray(residual_values, dtype=float)
    if rt.ndim != 1 or residual.ndim != 1:
        raise ValueError("rt_values and residual_values must be one-dimensional")
    if len(rt) != len(residual):
        raise ValueError("rt_values and residual_values must have the same length")
    left, right = _bounded_interval(left_index, right_index, len(rt))
    segment = np.maximum(residual[left:right], 0.0)
    segment_rt = rt[left:right]
    return float(np.trapezoid(segment, segment_rt)) * 60.0


def gaussian15_positive_asls_residual_metrics(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    baseline_values: np.ndarray,
    left_index: int,
    right_index: int,
    *,
    window_points: int = DEFAULT_GAUSSIAN15_WINDOW_POINTS,
) -> Ms1MorphologyMetrics:
    rt = np.asarray(rt_values, dtype=float)
    intensity = np.asarray(intensity_values, dtype=float)
    baseline = np.asarray(baseline_values, dtype=float)
    if rt.ndim != 1 or intensity.ndim != 1 or baseline.ndim != 1:
        raise ValueError("rt_values, intensity_values, and baseline_values must be 1-D")
    if len(rt) != len(intensity) or len(rt) != len(baseline):
        raise ValueError("trace arrays must have the same length")
    if len(rt) < 2:
        return Ms1MorphologyMetrics(
            trace_method=MS1_MORPHOLOGY_TRACE_METHOD,
            trace_window_points=window_points,
            trace_effective_points=0,
            area_positive_asls_residual=None,
            area_source="",
        )
    if not (
        np.all(np.isfinite(rt))
        and np.all(np.isfinite(intensity))
        and np.all(np.isfinite(baseline))
    ):
        raise ValueError("trace arrays must contain only finite values")
    residual = intensity - baseline
    smoothed = gaussian15_morphology_trace(residual, window_points=window_points)
    area = positive_residual_area(rt, smoothed, left_index, right_index)
    return Ms1MorphologyMetrics(
        trace_method=MS1_MORPHOLOGY_TRACE_METHOD,
        trace_window_points=window_points,
        trace_effective_points=morphology_trace_effective_points(
            len(residual),
            method=MS1_MORPHOLOGY_TRACE_METHOD,
            window_points=window_points,
        ),
        area_positive_asls_residual=area,
        area_source=MS1_MORPHOLOGY_AREA_SOURCE,
    )


def gaussian15_positive_asls_residual_trace(
    intensity_values: np.ndarray,
    baseline_values: np.ndarray,
    *,
    window_points: int = DEFAULT_GAUSSIAN15_WINDOW_POINTS,
) -> np.ndarray:
    intensity = np.asarray(intensity_values, dtype=float)
    baseline = np.asarray(baseline_values, dtype=float)
    if intensity.ndim != 1 or baseline.ndim != 1:
        raise ValueError("intensity_values and baseline_values must be one-dimensional")
    if len(intensity) != len(baseline):
        raise ValueError("intensity_values and baseline_values must match")
    residual = intensity - baseline
    return np.maximum(
        gaussian15_morphology_trace(residual, window_points=window_points),
        0.0,
    )


def gaussian_raw_minus_asls_raw_positive_trace(
    intensity_values: np.ndarray,
    baseline_values: np.ndarray,
    *,
    profile: SignalProcessingProfile,
) -> np.ndarray:
    """Subtract a nonnegative AsLS baseline from Gaussian(raw), clipped at zero."""

    _smoothed, positive = canonical_gaussian_signal_traces(
        intensity_values,
        baseline_values,
        profile=profile,
    )
    return positive


def canonical_gaussian_signal_traces(
    intensity_values: np.ndarray,
    baseline_values: np.ndarray,
    *,
    profile: SignalProcessingProfile,
) -> tuple[np.ndarray, np.ndarray]:
    """Return Gaussian(raw) and its positive residual over nonnegative AsLS."""

    if not isinstance(profile, SignalProcessingProfile):
        raise TypeError("profile must be a SignalProcessingProfile")
    intensity = np.asarray(intensity_values, dtype=float)
    baseline = np.asarray(baseline_values, dtype=float)
    if intensity.ndim != 1 or baseline.ndim != 1:
        raise ValueError("intensity_values and baseline_values must be one-dimensional")
    if len(intensity) != len(baseline):
        raise ValueError("intensity_values and baseline_values must match")
    if len(intensity) < profile.window_points:
        raise ValueError("insufficient_points_for_profile")
    if not np.all(np.isfinite(intensity)) or not np.all(np.isfinite(baseline)):
        raise ValueError("intensity_values and baseline_values must be finite")
    smoothed_raw = gaussian15_morphology_trace(
        intensity,
        window_points=profile.window_points,
    )
    return smoothed_raw, np.maximum(smoothed_raw - np.maximum(baseline, 0.0), 0.0)


def morphology_trace_effective_points(
    trace_length: int,
    *,
    method: str,
    window_points: int,
) -> int:
    if trace_length <= 0:
        return 0
    if method not in {MS1_MORPHOLOGY_TRACE_METHOD, "smooth_15"}:
        return 1
    if window_points <= 1:
        return 1
    if trace_length < window_points:
        return 1
    return _odd_window_points(trace_length, window_points)


def _gaussian_kernel(window_points: int) -> np.ndarray:
    if window_points <= 1:
        return np.ones(1, dtype=float)
    half_width = (window_points - 1) / 2.0
    sigma = max(window_points / 6.0, 1e-6)
    positions = np.arange(window_points, dtype=float) - half_width
    kernel = np.exp(-0.5 * (positions / sigma) ** 2)
    kernel_sum = float(np.sum(kernel))
    if kernel_sum <= 0:
        return np.ones(window_points, dtype=float) / float(window_points)
    return kernel / kernel_sum


def _odd_window_points(trace_length: int, requested: int) -> int:
    if trace_length <= 0:
        return 0
    window = max(1, min(int(requested), int(trace_length)))
    if window % 2 == 0:
        window -= 1
    return max(1, window)


def _bounded_interval(
    left_index: int,
    right_index: int,
    trace_length: int,
) -> tuple[int, int]:
    left = max(0, min(int(left_index), trace_length))
    right = max(left, min(int(right_index), trace_length))
    if right - left < 2 and trace_length >= 2:
        right = min(trace_length, left + 2)
        left = max(0, right - 2)
    return left, right
