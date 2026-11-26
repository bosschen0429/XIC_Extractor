from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class XICRequest:
    mz: float
    rt_min: float
    rt_max: float
    ppm_tol: float


def validate_xic_window(rt_min: float, rt_max: float, ppm_tol: float) -> None:
    if rt_min > rt_max:
        raise ValueError("rt_min must be <= rt_max")
    if ppm_tol <= 0:
        raise ValueError("ppm_tol must be > 0")


@dataclass(frozen=True)
class XICTrace:
    rt: NDArray[np.float64]
    intensity: NDArray[np.float64]

    @classmethod
    def empty(cls) -> "XICTrace":
        return cls(np.array([], dtype=float), np.array([], dtype=float))

    @classmethod
    def from_arrays(cls, rt: object, intensity: object) -> "XICTrace":
        rt_array = np.asarray(rt, dtype=float)
        intensity_array = np.asarray(intensity, dtype=float)
        if (
            rt_array.ndim != 1
            or intensity_array.ndim != 1
            or rt_array.shape != intensity_array.shape
        ):
            raise ValueError("XIC trace arrays must be matching 1D arrays")
        return cls(rt_array, intensity_array)


def crop_xic_trace_by_rt(
    trace: XICTrace,
    rt_min: float,
    rt_max: float,
    *,
    assume_sorted_rt: bool = False,
) -> XICTrace:
    if rt_min > rt_max:
        rt_min, rt_max = rt_max, rt_min

    rt = trace.rt
    intensity = trace.intensity
    if assume_sorted_rt or _is_non_decreasing_finite_rt(rt):
        start = int(np.searchsorted(rt, rt_min, side="left"))
        stop = int(np.searchsorted(rt, rt_max, side="right"))
        return XICTrace.from_arrays(rt[start:stop], intensity[start:stop])

    mask = (rt >= rt_min) & (rt <= rt_max)
    return XICTrace.from_arrays(rt[mask], intensity[mask])


def _is_non_decreasing_finite_rt(rt: NDArray[np.float64]) -> bool:
    if rt.size == 0:
        return True
    if not np.isfinite(rt[0]) or not np.isfinite(rt[-1]):
        return False
    if rt.size == 1:
        return True
    return bool(np.all(rt[:-1] <= rt[1:]))
