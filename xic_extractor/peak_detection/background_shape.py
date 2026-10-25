"""Describe provisional contrast quality without deciding peak availability."""

from __future__ import annotations

import numpy as np

from xic_extractor.peak_detection.signal_quality import (
    PROVISIONAL_MAX_SNR,
    SignalQuality,
)


def is_provisional_peak_shape(
    raw: np.ndarray,
    *,
    apex_index: int,
    signal_quality: SignalQuality | None,
) -> bool:
    """Describe marginal contrast using the peak owner's completed S/N fact."""
    if signal_quality is None or signal_quality.signal_to_noise is None:
        return False
    edge_count = max(1, min(5, len(raw) // 6))
    edge = max(
        float(np.median(raw[:edge_count])),
        float(np.median(raw[-edge_count:])),
    )
    centre = float(np.median(raw[max(0, apex_index - 1) : apex_index + 2]))
    contrast = (centre / edge) if edge > 0.0 else float("inf")
    return contrast < 1.5 and signal_quality.signal_to_noise < PROVISIONAL_MAX_SNR
