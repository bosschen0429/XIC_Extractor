"""Choose the rows of one Targeted Mass Exclusion list.

A row may never stop a target from triggering MS2: the m/z window the method
excludes around a row and the window the method includes around a target must
not overlap while both are active. Named exclusions come first; background ions
fill the remaining entries, most often among a scan's 20 most intense first.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from xic_extractor.exclusion_list import ExclusionListError
from xic_extractor.exclusion_list.background import BackgroundWindow
from xic_extractor.mass_accuracy import mz_window, within_ppm

MIN_WINDOW_MIN = 0.5
WHOLE_RUN = (0.0, math.inf)
MZ_DIGITS = 6
RT_DIGITS = 3
_RT_STEP = Decimal(1).scaleb(-RT_DIGITS)


@dataclass(frozen=True, slots=True)
class Target:
    """An ion a method must still fragment; ``start``/``stop`` in minutes."""

    name: str
    mz: float
    start: float
    stop: float
    source: str


@dataclass(frozen=True, slots=True)
class NamedExclusion:
    name: str
    mz: float


@dataclass(frozen=True, slots=True)
class MethodSettings:
    """Method values shared by every list of one recipe."""

    intensity_threshold: float
    inclusion_tolerance_ppm: float
    max_entries: int


@dataclass(frozen=True, slots=True)
class ListSpec:
    """One exclusion list: the methods that load it share these values."""

    name: str
    scan_range: tuple[float, float]
    exclusion_tolerance_ppm: float
    rt: bool

    @property
    def csv_name(self) -> str:
        return f"{self.name}.csv"


@dataclass(frozen=True, slots=True)
class ExclusionRow:
    compound: str
    mz: float
    start: float
    stop: float


@dataclass(frozen=True, slots=True)
class TargetConflict:
    """A candidate row removed, or cut in RT, to keep targets triggerable."""

    candidate: str
    targets: tuple[Target, ...]
    kept: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class ExclusionList:
    spec: ListSpec
    rows: tuple[ExclusionRow, ...]
    named_rows: int
    conflicts: tuple[TargetConflict, ...]
    named_outside_range: tuple[NamedExclusion, ...]
    background_apex: tuple[float, ...]


def select_exclusion_list(
    spec: ListSpec,
    settings: MethodSettings,
    named: Sequence[NamedExclusion],
    targets: Sequence[Target],
    windows: Sequence[BackgroundWindow],
    top_n: Sequence[int],
    run_end: float,
) -> ExclusionList:
    """Return the rows of ``spec``; ``top_n`` pairs with ``windows``.

    Without RT every row spans the run, so a target anywhere in the run removes
    the row instead of cutting it.
    """
    low, high = spec.scan_range
    tolerance = spec.exclusion_tolerance_ppm
    conflicts: list[TargetConflict] = []
    outside: list[NamedExclusion] = []

    def protect(
        candidate: str, mz: float, start: float, stop: float
    ) -> tuple[tuple[float, float], ...]:
        free, hits = _free_windows(mz, start, stop, targets, spec, settings)
        if hits:
            conflicts.append(TargetConflict(candidate, hits, free))
        return free

    named_rows: list[ExclusionRow] = []

    def covered_by_named(mz: float, start: float, stop: float) -> bool:
        return any(
            _covers(row.mz, row.start, row.stop, mz, start, stop, tolerance)
            for row in named_rows
        )

    for index, entry in enumerate(named, 1):
        mz = round(entry.mz, MZ_DIGITS)
        if not low <= mz <= high:
            outside.append(entry)
            continue
        label = f"EX{index:03d}"
        free = protect(f"{label} {entry.name}", mz, 0.0, run_end)
        for part, (start, stop) in enumerate(free, 1):
            if covered_by_named(mz, start, stop):
                continue
            compound = (
                f"{label}_R{part}_{_compound_text(entry.name)}"
                if spec.rt
                else f"{label}_{_compound_text(entry.name)}"
            )
            named_rows.append(ExclusionRow(compound, mz, start, stop))

    room = settings.max_entries - len(named_rows)
    if room < 0:
        raise ExclusionListError(
            f"{spec.name}: {len(named_rows)} named rows after target protection "
            f"exceed max_entries={settings.max_entries}"
        )
    ranked = sorted(
        (
            (count, replace(window, mz=mz))
            for count, window in zip(top_n, windows, strict=True)
            if low <= (mz := round(window.mz, MZ_DIGITS)) <= high
            and window.apex >= settings.intensity_threshold
        ),
        key=lambda item: (-item[0], -item[1].area, item[1].mz, item[1].start),
    )
    kept: list[tuple[BackgroundWindow, float, float]] = []
    for _, window in ranked:
        if len(kept) >= room:
            break
        candidate = f"m/z {window.mz:.4f} {_span(window.start, window.stop, run_end)}"
        for start, stop in protect(candidate, window.mz, window.start, window.stop):
            if covered_by_named(window.mz, start, stop) or any(
                _covers(other.mz, a, b, window.mz, start, stop, tolerance)
                for other, a, b in kept
            ):
                continue
            kept = [
                (other, a, b)
                for other, a, b in kept
                if not _covers(window.mz, start, stop, other.mz, a, b, tolerance)
            ]
            kept.append((window, start, stop))
            if len(kept) >= room:
                break

    return ExclusionList(
        spec=spec,
        rows=tuple(named_rows) + _background_rows(kept, spec.rt),
        named_rows=len(named_rows),
        conflicts=tuple(conflicts),
        named_outside_range=tuple(outside),
        background_apex=tuple(window.apex for window, _, _ in kept),
    )


def _free_windows(
    mz: float,
    start: float,
    stop: float,
    targets: Sequence[Target],
    spec: ListSpec,
    settings: MethodSettings,
) -> tuple[tuple[tuple[float, float], ...], tuple[Target, ...]]:
    excluded_low, excluded_high = mz_window(mz, spec.exclusion_tolerance_ppm)
    # Test the exact bounds the CSV will carry, including for uncut rows.
    free = [(round(start, RT_DIGITS), round(stop, RT_DIGITS))]
    hits: list[Target] = []
    for target in targets:
        included_low, included_high = mz_window(
            target.mz, settings.inclusion_tolerance_ppm
        )
        if excluded_high < included_low or included_high < excluded_low:
            continue
        if not spec.rt:
            hits.append(target)
            continue
        remaining = []
        for a, b in free:
            if target.stop <= a or target.start >= b:
                remaining.append((a, b))
                continue
            if target not in hits:
                hits.append(target)
            if target.start > a:
                remaining.append((a, _cut_rt(target.start, ROUND_FLOOR)))
            if target.stop < b:
                remaining.append((_cut_rt(target.stop, ROUND_CEILING), b))
        free = remaining
    if hits and not spec.rt:
        return (), tuple(hits)
    return tuple(w for w in free if w[1] - w[0] >= MIN_WINDOW_MIN), tuple(hits)


def _cut_rt(value: float, rounding: str) -> float:
    """Round away from the protected interval on the CSV's decimal RT grid."""
    return float(Decimal(str(value)).quantize(_RT_STEP, rounding=rounding))


def _covers(
    mz: float,
    start: float,
    stop: float,
    other_mz: float,
    other_start: float,
    other_stop: float,
    tolerance_ppm: float,
) -> bool:
    """Whether a row at ``mz`` already excludes the other row everywhere."""
    return (
        within_ppm(other_mz, mz, tolerance_ppm)
        and start <= other_start
        and other_stop <= stop
    )


def _background_rows(
    kept: Sequence[tuple[BackgroundWindow, float, float]], rt: bool
) -> tuple[ExclusionRow, ...]:
    rows: list[ExclusionRow] = []
    ions: dict[int, int] = {}
    parts: dict[int, int] = {}
    for window, start, stop in sorted(kept, key=lambda item: (item[0].mz, item[1])):
        number = ions.setdefault(window.ion, len(ions) + 1)
        parts[window.ion] = parts.get(window.ion, 0) + 1
        compound = f"BG{number:04d}" + (f"_R{parts[window.ion]}" if rt else "")
        rows.append(ExclusionRow(compound, window.mz, start, stop))
    return tuple(rows)


def _compound_text(name: str) -> str:
    return re.sub(r"\s+", "", name).replace(",", ";")


def _span(start: float, stop: float, run_end: float) -> str:
    if start <= 0 and stop >= run_end:
        return "whole run"
    return f"{start:.2f}-{stop:.2f} min"
