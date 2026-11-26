"""MS1 landmarks for correspondence, independent of peak integration.

A landmark is a supported local maximum, not a selected physical segment or
method-backed observation. In particular, maxima within one envelope can
describe a pattern without becoming separate quantitation results.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks, peak_widths

from xic_extractor.peak_detection.physical_peak_evaluation import (
    enumerate_physical_segment_universe,
    unique_physical_segment_at_rt,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalSegment,
    PhysicalSegmentUniverse,
    PreparedSignalResult,
)

LANDMARK_MIN_PROMINENCE_FRACTION = 0.04
"""A landmark's prominence relative to the trace maximum."""


@dataclass(frozen=True, slots=True)
class ElutionLandmark:
    apex_rt: float
    height: float
    width: float
    physical_segment: PhysicalSegment | None = None


@dataclass(frozen=True, slots=True)
class SampleElutionContext:
    sample_stem: str
    mz: float
    rt_start: float
    rt_end: float
    landmarks: tuple[ElutionLandmark, ...]
    rt: tuple[float, ...] = ()
    raw: tuple[float, ...] = ()


def project_elution_context(
    prepared_result: PreparedSignalResult,
    *,
    sample_stem: str,
    mz: float,
    universe: PhysicalSegmentUniverse | None = None,
) -> SampleElutionContext | None:
    """Retain interior maxima without changing the integration universe.

    The smoothing kernel must have acquired support on both sides of an apex.
    Edge fragments are not landmarks; an omitted landmark is not an absence
    assertion. Widths describe the matching uncertainty, never integration
    bounds or an area.
    """
    prepared = prepared_result.prepared
    if prepared is None:
        return None
    rt = np.asarray(prepared.rt)
    signal = np.asarray(prepared.positive_signal)
    peaks, _ = find_peaks(
        signal,
        prominence=float(np.max(signal)) * LANDMARK_MIN_PROMINENCE_FRACTION,
    )
    margin = prepared.profile.window_points // 2
    peaks = peaks[(peaks >= margin) & (peaks < len(rt) - margin)]
    if not len(peaks):
        return None
    _, _, left, right = peak_widths(signal, peaks)
    positions = np.arange(len(rt))
    widths = np.interp(right, positions, rt) - np.interp(left, positions, rt)
    if universe is None:
        universe = enumerate_physical_segment_universe(prepared_result)
    landmarks = tuple(
        ElutionLandmark(
            float(rt[index]),
            float(signal[index]),
            float(width),
            unique_physical_segment_at_rt(universe, float(rt[index])),
        )
        for index, width in zip(peaks, widths, strict=True)
        if width > 0.0
    )
    return SampleElutionContext(
        sample_stem=sample_stem,
        mz=mz,
        rt_start=float(rt[0]),
        rt_end=float(rt[-1]),
        landmarks=landmarks,
        rt=prepared.rt,
        raw=prepared.raw,
    )
