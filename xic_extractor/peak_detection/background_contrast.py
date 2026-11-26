"""How far a peak stands above the other bumps of its channel nearby.

A person rejects a bump that is as tall as the dozens of noise bumps around it,
however peak-like it looks once scaled to its own height. The contrast is the
peak's height over the median height of the other local maxima within
``CONTRAST_HALF_WINDOW_MIN`` of its bounds, all on the canonical signal of that
wider trace. A median over many bumps is barely moved by a few neighbouring
real peaks; with fewer than ``MIN_CONTRAST_BUMPS`` bumps the median is not
robust, so the surroundings count as quiet and the contrast is infinite. The
window and bump floor are product policy chosen on the 8RAW rows reviewed in
#1283.
"""

from __future__ import annotations

import math

import numpy as np

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)

CONTRAST_HALF_WINDOW_MIN = 4.0
MIN_CONTRAST_BUMPS = 10


def background_contrast(
    rt: np.ndarray,
    intensity: np.ndarray,
    *,
    left_rt_min: float,
    right_rt_min: float,
    profile: SignalProcessingProfile,
) -> float | None:
    """Peak height over the median nearby bump; ``None`` without a peak signal."""
    keep = (rt >= left_rt_min - CONTRAST_HALF_WINDOW_MIN) & (
        rt <= right_rt_min + CONTRAST_HALF_WINDOW_MIN
    )
    prepared = prepare_physical_signal(rt[keep], intensity[keep], profile=profile)
    if prepared.prepared is None:
        return None
    times = np.asarray(prepared.prepared.rt, dtype=float)
    signal = np.asarray(prepared.prepared.positive_signal, dtype=float)
    inside = (times >= left_rt_min) & (times <= right_rt_min)
    if not inside.any():
        return None
    height = float(signal[inside].max())
    interior = np.flatnonzero(
        (signal[1:-1] > signal[:-2]) & (signal[1:-1] >= signal[2:])
    )
    maxima = interior + 1
    bumps = signal[maxima[~inside[maxima]]]
    bumps = bumps[bumps > 0.0]
    if height <= 0.0:
        return None
    if len(bumps) < MIN_CONTRAST_BUMPS:
        return math.inf
    return height / float(np.median(bumps))
