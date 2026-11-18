"""Internal standards a curation never drops, matched by m/z and RT window."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from xic_extractor.matrix_curation.mass_rt_windows import MzRtWindow, mz_rt_window


def windows_from_targets(targets: Iterable[Any]) -> tuple[MzRtWindow, ...]:
    """Windows of the ``is_istd`` rows of a target list."""
    windows = (
        mz_rt_window(
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


def read_istd_file(path: Path) -> tuple[MzRtWindow, ...]:
    """Standards from a target list file (CSV/TSV/XLSX with an ISTD role)."""
    from xic_extractor.configuration.targets import read_istd_targets

    return windows_from_targets(read_istd_targets(path).targets)


def windows_from_target_snapshot(snapshot: object) -> tuple[MzRtWindow, ...]:
    """Standards the run declared, from its run metadata target snapshot.

    Empty when the run used no internal standard list.
    """
    if not isinstance(snapshot, str):
        return ()
    definitions = json.loads(snapshot).get("definitions") or ()
    windows = []
    for definition in definitions:
        target = definition.get("target") if isinstance(definition, Mapping) else None
        if not isinstance(target, Mapping) or target.get("is_istd") is not True:
            continue
        window = mz_rt_window(
            label=str(target.get("label") or definition.get("target_label") or ""),
            mz=target.get("mz"),
            rt_min=target.get("rt_min"),
            rt_max=target.get("rt_max"),
            expected_rt_min=target.get("expected_rt_min"),
        )
        if window is not None:
            windows.append(window)
    return tuple(windows)
