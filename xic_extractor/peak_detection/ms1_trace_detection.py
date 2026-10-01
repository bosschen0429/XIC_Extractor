from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.models import PeakDetectionResult, PeakResult

Ms1TracePeakStatus = Literal[
    "detected",
    "not_detected",
    "empty_trace",
    "unassessable_trace",
]
Ms1PeakFinder = Callable[..., PeakDetectionResult]


@dataclass(frozen=True)
class Ms1TracePeakDetection:
    status: Ms1TracePeakStatus
    rt: NDArray[np.float64]
    intensity: NDArray[np.float64]
    result: PeakDetectionResult | None = None
    peak: PeakResult | None = None


def detect_ms1_trace_peak(
    rt: object,
    intensity: object,
    *,
    peak_config: ExtractionConfig,
    preferred_rt: float | None,
    peak_finder: Ms1PeakFinder,
    strict_preferred_rt: bool = False,
) -> Ms1TracePeakDetection:
    try:
        rt_array, intensity_array = validate_ms1_trace_arrays(rt, intensity)
    except ValueError:
        empty = np.asarray((), dtype=float)
        return Ms1TracePeakDetection(
            status="unassessable_trace",
            rt=empty,
            intensity=empty,
        )
    if rt_array.size == 0 or intensity_array.size == 0:
        return Ms1TracePeakDetection(
            status="empty_trace",
            rt=rt_array,
            intensity=intensity_array,
        )
    result = peak_finder(
        rt_array,
        intensity_array,
        peak_config,
        preferred_rt=preferred_rt,
        strict_preferred_rt=strict_preferred_rt,
    )
    if result.status != "OK" or result.peak is None:
        return Ms1TracePeakDetection(
            status="not_detected",
            rt=rt_array,
            intensity=intensity_array,
            result=result,
        )
    return Ms1TracePeakDetection(
        status="detected",
        rt=rt_array,
        intensity=intensity_array,
        result=result,
        peak=result.peak,
    )


def validate_ms1_trace_arrays(
    rt: object,
    intensity: object,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    rt_array = np.asarray(rt, dtype=float)
    intensity_array = np.asarray(intensity, dtype=float)
    if (
        rt_array.ndim != 1
        or intensity_array.ndim != 1
        or rt_array.shape != intensity_array.shape
        or not np.all(np.isfinite(rt_array))
        or not np.all(np.isfinite(intensity_array))
    ):
        raise ValueError("MS1 trace arrays must be finite 1D pairs")
    return rt_array, intensity_array
