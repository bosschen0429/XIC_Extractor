"""Diagnostic injection chronology; no selection or quantitative authority."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PureWindowsPath
from typing import Sequence


@dataclass(frozen=True)
class InjectionHeader:
    raw_path: str
    acquisition_source: str
    creation_date: datetime
    start_time_min: float
    end_time_min: float
    instrument_method: str

    def __post_init__(self) -> None:
        if not self.acquisition_source.strip():
            raise ValueError("RAW acquisition source is missing")
        if (
            not math.isfinite(self.start_time_min)
            or not math.isfinite(self.end_time_min)
            or not 0 <= self.start_time_min < self.end_time_min
        ):
            raise ValueError("RAW run times must be finite and 0 <= start < end")

    @property
    def run_length_min(self) -> float:
        return self.end_time_min - self.start_time_min


@dataclass(frozen=True)
class PerturbationPolicy:
    max_previous_run_min: float = 20.0
    max_previous_run_fraction: float = 0.5
    max_idle_min: float = 5.0

    def __post_init__(self) -> None:
        values = (
            self.max_previous_run_min,
            self.max_previous_run_fraction,
            self.max_idle_min,
        )
        if not all(math.isfinite(v) and v >= 0 for v in values):
            raise ValueError("perturbation thresholds must be finite and nonnegative")
        if not 0 < self.max_previous_run_fraction < 1:
            raise ValueError("max_previous_run_fraction must be between 0 and 1")


@dataclass(frozen=True)
class Injection:
    injection_order: int
    raw_stem: str
    header: InjectionHeader
    copy_paths: tuple[str, ...]
    acquisition_start: datetime
    acquisition_end: datetime
    run_length_min: float
    previous_injection_order: int | None
    previous_raw_stem: str | None
    previous_run_length_min: float | None
    idle_min: float | None
    possibly_perturbed: bool
    timing_warning: str | None


def acquisition_key(source: str) -> str:
    """Vendor acquisition paths are Windows paths, independent of local copies."""
    return str(PureWindowsPath(source)).casefold()


def build_injection_timeline(
    headers: Sequence[InjectionHeader],
    policy: PerturbationPolicy,
) -> tuple[Injection, ...]:
    """Order actual injections across folders; preserve copies and overlap evidence.

    CreationDate is the acquisition clock origin. RunHeaderEx times are minute
    offsets from that origin. Missing header facts must be rejected by the reader.
    Duplicate identity is creation date plus original acquisition path, never
    local basename or filesystem timestamps.
    """
    groups: dict[tuple[datetime, str], list[InjectionHeader]] = {}
    for header in headers:
        groups.setdefault(
            (header.creation_date, acquisition_key(header.acquisition_source)), []
        ).append(header)
    rows: list[Injection] = []
    for key in sorted(groups):
        copies = sorted(groups[key], key=lambda h: (h.raw_path.casefold(), h.raw_path))
        header = copies[0]
        if any(
            (h.start_time_min, h.end_time_min, h.instrument_method)
            != (header.start_time_min, header.end_time_min, header.instrument_method)
            for h in copies
        ):
            raise ValueError(
                f"conflicting duplicate RAW headers: {header.acquisition_source}"
            )
        start = header.creation_date + timedelta(minutes=header.start_time_min)
        end = header.creation_date + timedelta(minutes=header.end_time_min)
        previous = rows[-1] if rows else None
        idle = (
            (start - previous.acquisition_end).total_seconds() / 60
            if previous
            else None
        )
        flagged = bool(
            previous
            and idle is not None
            and 0 <= idle <= policy.max_idle_min
            and previous.run_length_min <= policy.max_previous_run_min
            and previous.run_length_min
            <= header.run_length_min * policy.max_previous_run_fraction
        )
        rows.append(
            Injection(
                injection_order=len(rows) + 1,
                raw_stem=PureWindowsPath(header.acquisition_source).stem,
                header=header,
                copy_paths=tuple(sorted({h.raw_path for h in copies})),
                acquisition_start=start,
                acquisition_end=end,
                run_length_min=header.run_length_min,
                previous_injection_order=previous.injection_order if previous else None,
                previous_raw_stem=previous.raw_stem if previous else None,
                previous_run_length_min=previous.run_length_min if previous else None,
                idle_min=idle,
                possibly_perturbed=flagged,
                timing_warning="overlapping_acquisitions"
                if idle is not None and idle < 0
                else None,
            )
        )
    return tuple(rows)


@dataclass(frozen=True)
class ScheduledInjection:
    sequence_path: str
    row_number: int
    acquisition_source: str
    instrument_method: str


@dataclass(frozen=True)
class SequenceComparison:
    scheduled: ScheduledInjection
    status: str
    injection_orders: tuple[int, ...]


def compare_sequence(
    scheduled: Sequence[ScheduledInjection],
    timeline: Sequence[Injection],
) -> tuple[SequenceComparison, ...]:
    """Compare exact vendor filenames; ambiguous repeat acquisitions stay visible.

    Sequence segments remain separate provenance records and never determine
    chronology. No fuzzy basename matching across acquisition directories.
    """
    by_source: dict[str, list[int]] = {}
    for row in timeline:
        by_source.setdefault(acquisition_key(row.header.acquisition_source), []).append(
            row.injection_order
        )
    comparisons = []
    for entry in scheduled:
        orders = tuple(by_source.get(acquisition_key(entry.acquisition_source), ()))
        status = (
            "unconfigured"
            if not entry.acquisition_source
            else "missing_raw"
            if not orders
            else "matched"
            if len(orders) == 1
            else "ambiguous"
        )
        comparisons.append(SequenceComparison(entry, status, orders))
    return tuple(comparisons)
