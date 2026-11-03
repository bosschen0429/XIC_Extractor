"""Internal standards a curation never drops, matched by m/z and RT window."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ISTD_MZ_PPM = 10.0
# Half-width around expected_rt_min when a standard declares no RT range.
ISTD_EXPECTED_RT_HALF_WIDTH_MIN = 0.75


@dataclass(frozen=True)
class IstdWindow:
    label: str
    mz: float
    rt_low_min: float
    rt_high_min: float


def istd_window(
    *,
    label: str,
    mz: object,
    rt_min: object,
    rt_max: object,
    expected_rt_min: object,
) -> IstdWindow | None:
    """The RT window of one standard: its range, or expected RT ±0.75 min.

    A range whose maximum is not above its minimum is no range. A standard with
    neither cannot be located and protects nothing.
    """
    center_mz, low, high, expected = (
        _number(value) for value in (mz, rt_min, rt_max, expected_rt_min)
    )
    if center_mz is None or center_mz <= 0:
        return None
    if low is not None and high is not None and high > low:
        return IstdWindow(label, center_mz, low, high)
    if expected is not None:
        return IstdWindow(
            label,
            center_mz,
            expected - ISTD_EXPECTED_RT_HALF_WIDTH_MIN,
            expected + ISTD_EXPECTED_RT_HALF_WIDTH_MIN,
        )
    return None


def windows_from_targets(targets: Iterable[Any]) -> tuple[IstdWindow, ...]:
    """Windows of the ``is_istd`` rows of a target list."""
    windows = (
        istd_window(
            label=target.label,
            mz=target.mz,
            rt_min=target.rt_min,
            rt_max=target.rt_max,
            expected_rt_min=target.expected_rt_min,
        )
        for target in targets
        if target.is_istd
    )
    return tuple(window for window in windows if window is not None)


def read_istd_file(path: Path) -> tuple[IstdWindow, ...]:
    """Standards from a target list file (CSV/TSV/XLSX with an ISTD role)."""
    from xic_extractor.configuration.targets import read_istd_targets

    return windows_from_targets(read_istd_targets(path).targets)


def windows_from_explanation(summary: Mapping[str, Any]) -> tuple[IstdWindow, ...]:
    """Standards the run declared, from its explanation completion summary.

    Empty when the run used no internal standard list.
    """
    reference = summary.get("known_istd_reference")
    if not isinstance(reference, Mapping):
        return ()
    snapshot = reference.get("target_snapshot")
    if not isinstance(snapshot, str):
        return ()
    definitions = json.loads(snapshot).get("definitions") or ()
    windows = []
    for definition in definitions:
        target = definition.get("target") if isinstance(definition, Mapping) else None
        if not isinstance(target, Mapping) or target.get("is_istd") is not True:
            continue
        window = istd_window(
            label=str(target.get("label") or definition.get("target_label") or ""),
            mz=target.get("mz"),
            rt_min=target.get("rt_min"),
            rt_max=target.get("rt_max"),
            expected_rt_min=target.get("expected_rt_min"),
        )
        if window is not None:
            windows.append(window)
    return tuple(windows)


def match_istd(mz: float, rt_min: float, windows: Sequence[IstdWindow]) -> str:
    """Labels of the standards whose m/z (±10 ppm) and RT window hold the row."""
    labels = sorted(
        {
            window.label
            for window in windows
            if abs(mz - window.mz) / window.mz * 1e6 <= ISTD_MZ_PPM
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
