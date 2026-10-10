"""MS1 landmarks for correspondence, independent of peak integration.

A landmark is a supported local maximum, not a selected physical segment or
method-backed observation. In particular, shoulders can describe a pattern
without becoming separate quantitation results.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks, peak_widths

from xic_extractor.peak_detection.physical_peak_models import PreparedSignalResult


@dataclass(frozen=True, slots=True)
class ElutionLandmark:
    apex_rt: float
    height: float
    width: float

    def __post_init__(self) -> None:
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in (self.apex_rt, self.height, self.width)
            )
            or self.apex_rt < 0.0
            or self.height <= 0.0
            or self.width <= 0.0
        ):
            raise ValueError(
                "elution landmark requires finite RT, positive height and width"
            )


@dataclass(frozen=True, slots=True)
class SampleElutionContext:
    sample_stem: str
    mz: float
    rt_start: float
    rt_end: float
    landmarks: tuple[ElutionLandmark, ...]
    trace_content_id: str = ""
    profile_id: str = ""

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sample_stem, str)
            or not self.sample_stem.strip()
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in (self.mz, self.rt_start, self.rt_end)
            )
            or self.mz <= 0.0
            or self.rt_start < 0.0
            or self.rt_end <= self.rt_start
            or type(self.landmarks) is not tuple
            or any(not isinstance(peak, ElutionLandmark) for peak in self.landmarks)
        ):
            raise ValueError(
                "elution context requires a sample, mass, bounds and landmarks"
            )
        apices = tuple(peak.apex_rt for peak in self.landmarks)
        if tuple(sorted(set(apices))) != apices or any(
            not self.rt_start <= apex <= self.rt_end for apex in apices
        ):
            raise ValueError(
                "elution landmarks must be ordered within acquired context"
            )


def project_elution_context(
    prepared_result: PreparedSignalResult,
    *,
    sample_stem: str,
    mz: float,
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
        prominence=float(np.max(signal))
        * prepared.profile.segment_min_prominence_fraction,
    )
    margin = prepared.profile.window_points // 2
    peaks = peaks[(peaks >= margin) & (peaks < len(rt) - margin)]
    if not len(peaks):
        return None
    _, _, left, right = peak_widths(signal, peaks)
    positions = np.arange(len(rt))
    widths = np.interp(right, positions, rt) - np.interp(left, positions, rt)
    landmarks = tuple(
        ElutionLandmark(float(rt[index]), float(signal[index]), float(width))
        for index, width in zip(peaks, widths, strict=True)
        if width > 0.0
    )
    return SampleElutionContext(
        sample_stem=sample_stem,
        mz=mz,
        rt_start=float(rt[0]),
        rt_end=float(rt[-1]),
        landmarks=landmarks,
        trace_content_id=prepared_result.projection.trace_content_id,
        profile_id=prepared_result.projection.profile_id,
    )
