"""Qualify a selected peak against irregular background without reintegration."""
from __future__ import annotations

import numpy as np
from scipy.optimize import isotonic_regression


def is_background_like_peak(
    rt: np.ndarray,
    raw: np.ndarray,
    baseline: np.ndarray,
    *,
    apex_index: int,
    raw_noise_mad: float,
) -> bool:
    """Require deficient shape on both acquired and baseline-corrected data.

    Inputs are the unchanged selected interval and its relative apex index.
    The second view protects resolved peaks riding on a sloping background.
    Preserve negative residuals: clipping would manufacture peak-like shape.
    Thresholds belong to the versioned physical selection policy.
    """
    return _deficient_peak_shape(rt, raw, apex_index, raw_noise_mad) and (
        _deficient_peak_shape(rt, raw - baseline, apex_index)
    )


def is_unresolved_shoulder_peak(
    *,
    segment_class: str,
    apex_height: float,
    left_valley: float,
    right_valley: float,
) -> bool:
    """Qualify whether an overlapping shoulder candidate lacks baseline return."""
    if segment_class != "shoulder_candidate" or apex_height <= 0.0:
        return False
    floor_ratio = min(left_valley, right_valley) / apex_height
    prominence_ratio = (apex_height - max(left_valley, right_valley)) / apex_height
    return floor_ratio > 0.25 and prominence_ratio < 0.65


def has_deficient_monotone_run(
    raw: np.ndarray,
    *,
    apex_index: int,
    min_points: int = 6,
) -> bool:
    """Determine if a peak interval lacks consecutive monotonic trend steps.

    Authentic peaks have diffusion-driven continuity (typically >= 2 consecutive
    steps on at least one flank). High-frequency noise or baseline chatter
    alternates back-and-forth with max consecutive run <= 1.
    Sparse peaks (< min_points) are exempted to protect ultra-short analytes.
    """
    if len(raw) < min_points:
        return False
    apex = max(0, min(apex_index, len(raw) - 1))

    # Rising flank up to apex
    up_diffs = np.diff(raw[: apex + 1])
    max_up = 0
    cur_up = 0
    for d in up_diffs:
        if d > 0:
            cur_up += 1
            if cur_up > max_up:
                max_up = cur_up
        elif d < 0:
            cur_up = 0

    # Falling flank from apex
    down_diffs = np.diff(raw[apex:])
    max_down = 0
    cur_down = 0
    for d in down_diffs:
        if d < 0:
            cur_down += 1
            if cur_down > max_down:
                max_down = cur_down
        elif d > 0:
            cur_down = 0

    return max(max_up, max_down) <= 1


def has_unresolved_valley_floors(
    *,
    apex_height: float,
    left_valley: float,
    right_valley: float,
    max_floor_ratio: float = 0.20,
) -> bool:
    """Determine if a peak interval fails to return near baseline on both flanks.

    A resolved chromatographic peak must return near baseline (<= 20% of apex
    height) on at least one flank. When both flanks remain elevated above 20%
    of apex height on the baseline-corrected signal, the interval is an
    unresolved portion of a broader feature or elevated saddle without valley return.
    """
    if apex_height <= 0.0:
        return True
    floor_ratio = min(left_valley, right_valley) / apex_height
    return floor_ratio > max_floor_ratio



def is_provisional_peak_shape(
    rt: np.ndarray,
    raw: np.ndarray,
    *,
    apex_index: int,
    raw_noise_mad: float,
) -> bool:
    """Determine if a peak has marginal contrast (<1.5x) and low SNR (<5.0)."""
    if raw_noise_mad <= 0.0:
        return False
    edge_count = max(1, min(5, len(raw) // 6))
    edge = max(
        float(np.median(raw[:edge_count])),
        float(np.median(raw[-edge_count:])),
    )
    centre = float(np.median(raw[max(0, apex_index - 1) : apex_index + 2]))
    contrast = (centre / edge) if edge > 0.0 else float("inf")
    snr = (centre - edge) / raw_noise_mad
    return contrast < 1.5 and snr < 5.0


def _deficient_peak_shape(
    rt: np.ndarray,
    signal: np.ndarray,
    apex: int,
    noise_mad: float | None = None,
) -> bool:
    # Separate monotone flanks permit asymmetric peaks. Joining at the larger
    # fitted apex preserves monotonicity; this is a shape score, not an exact
    # joint constrained least-squares fit or a probability of peak identity.
    left = isotonic_regression(signal[: apex + 1], increasing=True).x
    right = isotonic_regression(signal[apex:], increasing=False).x
    fitted = np.r_[left[:-1], max(left[-1], right[0]), right[1:]]
    variance = float(np.sum((signal - np.mean(signal)) ** 2))
    r2 = (
        1.0 - float(np.sum((signal - fitted) ** 2)) / variance
        if variance > 0.0 else 0.0
    )
    if r2 < 0.65:
        return True
    edge_count = max(1, min(5, len(signal) // 6))
    edge = max(
        float(np.median(signal[:edge_count])),
        float(np.median(signal[-edge_count:])),
    )
    centre = float(np.median(signal[max(0, apex - 1) : apex + 2]))
    if edge > 0.0 and centre / edge < 1.2:
        return True
    if noise_mad is None:
        if len(signal) < 3:
            noise_mad = 0.0
        else:
            weight = (rt[2:] - rt[1:-1]) / (rt[2:] - rt[:-2])
            residual = (
                weight * signal[:-2] + (1.0 - weight) * signal[2:] - signal[1:-1]
            ) / np.sqrt(1.0 + weight**2 + (1.0 - weight)**2)
            noise_mad = float(np.median(np.abs(residual - np.median(residual))))
    # Zero MAD does not establish absence of a sparse peak.
    return noise_mad > 0.0 and (centre - edge) / noise_mad < 3.0
