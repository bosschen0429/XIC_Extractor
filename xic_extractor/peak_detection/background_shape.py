"""Describe provisional contrast quality without deciding peak availability."""

from __future__ import annotations

import numpy as np


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
