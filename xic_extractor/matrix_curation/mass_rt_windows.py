"""Labeled reference windows shared by Matrix Curation steps and ISTD protection."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from xic_extractor.mass_accuracy import absolute_ppm_error

WINDOW_MZ_PPM = 10.0
EXPECTED_RT_HALF_WIDTH_MIN = 0.75


@dataclass(frozen=True)
class MzRtWindow:
    label: str
    mz: float
    rt_low_min: float
    rt_high_min: float


def mz_rt_window(
    *,
    label: str,
    mz: object,
    rt_min: object,
    rt_max: object,
    expected_rt_min: object,
) -> MzRtWindow | None:
    """The RT window of one reference: its range, or expected RT ±0.75 min.

    A range whose maximum is not above its minimum is no range. A reference with
    neither cannot be located and has no window.
    """
    center_mz, low, high, expected = (
        _number(value) for value in (mz, rt_min, rt_max, expected_rt_min)
    )
    if center_mz is None or center_mz <= 0:
        return None
    if low is not None and high is not None and high > low:
        return MzRtWindow(label, center_mz, low, high)
    if expected is not None:
        return MzRtWindow(
            label,
            center_mz,
            expected - EXPECTED_RT_HALF_WIDTH_MIN,
            expected + EXPECTED_RT_HALF_WIDTH_MIN,
        )
    return None


def match_labels(mz: float, rt_min: float, windows: Sequence[MzRtWindow]) -> str:
    """Labels of windows whose m/z (±10 ppm) and RT window hold the row."""
    labels = sorted(
        {
            window.label
            for window in windows
            if absolute_ppm_error(mz, window.mz) <= WINDOW_MZ_PPM
            and window.rt_low_min <= rt_min <= window.rt_high_min
        }
    )
    return ";".join(labels)


def _number(value: object) -> float | None:
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
    ):
        return float(value)
    return None
