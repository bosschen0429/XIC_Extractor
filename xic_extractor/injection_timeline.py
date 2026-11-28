"""RAW acquisition chronology and the injection coordinates of sample populations."""

from __future__ import annotations

from collections.abc import Callable, Container, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cached_property
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from xic_extractor.sample_metadata import SampleMetadata

_Row = TypeVar("_Row")


@dataclass(frozen=True, slots=True)
class PreviousInjection:
    """Header facts about the acquisition immediately before this one."""

    instrument_method: str
    idle_min: float


@dataclass(frozen=True)
class InjectionSequence:
    """A sample population on the RAW timeline, with its own dense positions.

    Ordinals include injections outside the selected population. Positions and
    the before/after window count only members of this population (or its view).
    """

    _orders: Mapping[str, int] = field(default_factory=dict)
    _previous: Mapping[str, PreviousInjection] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_orders", dict(self._orders))
        object.__setattr__(self, "_previous", dict(self._previous))

    @classmethod
    def from_samples(
        cls,
        samples: Iterable[SampleMetadata],
        *,
        previous: Mapping[str, PreviousInjection] | None = None,
    ) -> InjectionSequence:
        return cls(
            {
                sample.raw_stem: sample.injection_order
                for sample in samples
                if sample.injection_order is not None
            },
            {} if previous is None else previous,
        )

    @cached_property
    def stems(self) -> tuple[str, ...]:
        return tuple(sorted(self._orders, key=lambda stem: (self._orders[stem], stem)))

    @cached_property
    def _positions(self) -> dict[str, int]:
        return {stem: position for position, stem in enumerate(self.stems)}

    def __contains__(self, stem: str) -> bool:
        return stem in self._orders

    def __len__(self) -> int:
        return len(self._orders)

    def order(self, stem: str) -> int | None:
        return self._orders.get(stem)

    def required_order(self, stem: str) -> int:
        return self._orders[stem]

    def position(self, stem: str) -> int | None:
        return self._positions.get(stem)

    def position_gap(self, left: str, right: str) -> int | None:
        return _gap(self.position(left), self.position(right))

    def previous(self, stem: str) -> PreviousInjection | None:
        """Previous acquisition, including context; absent in order-only snapshots."""
        return self._previous.get(stem)

    @property
    def has_unique_orders(self) -> bool:
        return len(set(self._orders.values())) == len(self)

    def with_orders(self, orders: Mapping[str, int]) -> InjectionSequence:
        """Use explicit analysis ordinals without changing the header facts."""
        return InjectionSequence(orders, self._previous)

    def restrict(self, stems: Container[str]) -> InjectionSequence:
        if all(stem in stems for stem in self._orders):
            return self
        return InjectionSequence(
            {stem: order for stem, order in self._orders.items() if stem in stems},
            {
                stem: previous
                for stem, previous in self._previous.items()
                if stem in stems
            },
        )

    def around(self, stem: str, k: int) -> tuple[str, ...]:
        position = self.position(stem)
        if position is None:
            return ()
        return (
            self.stems[max(0, position - k) : position]
            + self.stems[position + 1 : position + k + 1]
        )

    def nearest_by_order(
        self, stem: str, candidates: Iterable[str], k: int
    ) -> tuple[str, ...]:
        return self.nearest_rows_by_order(
            stem,
            (candidate for candidate in candidates if candidate != stem),
            k,
            stem=lambda candidate: candidate,
            within_sample=lambda candidate: (candidate,),
        )

    def nearest_rows_by_order(
        self,
        query: str,
        rows: Iterable[_Row],
        k: int,
        *,
        stem: Callable[[_Row], str],
        within_sample: Callable[[_Row], tuple[Any, ...]],
    ) -> tuple[_Row, ...]:
        order = self.order(query)
        if order is None:
            return ()

        def key(row: _Row) -> tuple[Any, ...]:
            candidate_order = self.required_order(stem(row))
            return abs(candidate_order - order), candidate_order, *within_sample(row)

        return tuple(sorted((row for row in rows if stem(row) in self), key=key)[:k])

    def ordered_rows(
        self,
        rows: Iterable[_Row],
        *,
        stem: Callable[[_Row], str],
        within_sample: Callable[[_Row], tuple[Any, ...]],
        by_position: bool = False,
    ) -> list[_Row]:
        """Sort measured rows along the sequence; unknown samples come last."""

        def key(row: _Row) -> tuple[Any, ...]:
            coordinate = self.position if by_position else self.order
            order = coordinate(stem(row))
            return (order is None, order, *within_sample(row))

        return sorted(rows, key=key)

    def snapshot_orders(self) -> dict[str, int]:
        """Keep the persisted order field a plain stem-to-ordinal mapping."""
        return dict(self._orders)


def _gap(left: int | None, right: int | None) -> int | None:
    return abs(right - left) if left is not None and right is not None else None


@dataclass(frozen=True)
class InjectionHeader:
    raw_path: str
    acquisition_source: str
    creation_date: datetime
    start_time_min: float
    end_time_min: float
    instrument_method: str

    @property
    def run_length_min(self) -> float:
        return self.end_time_min - self.start_time_min


@dataclass(frozen=True)
class Injection:
    injection_order: int
    raw_stem: str
    acquisition_stem: str
    renamed_after_acquisition: bool
    header: InjectionHeader
    copy_paths: tuple[str, ...]
    acquisition_start: datetime
    acquisition_end: datetime
    run_length_min: float
    previous_injection_order: int | None
    previous_raw_stem: str | None
    previous_run_length_min: float | None
    idle_min: float | None
    previous_instrument_method: str | None
    timing_warning: str | None

    @property
    def previous(self) -> PreviousInjection | None:
        if self.previous_instrument_method is None or self.idle_min is None:
            return None
        return PreviousInjection(self.previous_instrument_method, self.idle_min)


def local_raw_stem(copy_paths: Sequence[str], acquisition_stem: str) -> str:
    """Sample identity uses the local filename, which may differ from acquisition.

    Prefer a copy still named as acquired; otherwise the shallowest copy path.
    """
    stems = [PureWindowsPath(path).stem for path in copy_paths]
    folded = {stem.casefold(): stem for stem in stems}
    if acquisition_stem.casefold() in folded:
        return folded[acquisition_stem.casefold()]
    shallowest = min(
        copy_paths,
        key=lambda path: (len(PureWindowsPath(path).parts), path.casefold(), path),
    )
    return PureWindowsPath(shallowest).stem


def acquisition_key(source: str) -> str:
    """Vendor acquisition paths are Windows paths, independent of local copies."""
    return str(PureWindowsPath(source)).casefold()


def build_injection_timeline(
    headers: Sequence[InjectionHeader],
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
        copy_paths = tuple(sorted({h.raw_path for h in copies}))
        acquisition_stem = PureWindowsPath(header.acquisition_source).stem
        raw_stem = local_raw_stem(copy_paths, acquisition_stem)
        rows.append(
            Injection(
                injection_order=len(rows) + 1,
                raw_stem=raw_stem,
                acquisition_stem=acquisition_stem,
                renamed_after_acquisition=(
                    raw_stem.casefold() != acquisition_stem.casefold()
                ),
                header=header,
                copy_paths=copy_paths,
                acquisition_start=start,
                acquisition_end=end,
                run_length_min=header.run_length_min,
                previous_injection_order=previous.injection_order if previous else None,
                previous_raw_stem=previous.raw_stem if previous else None,
                previous_run_length_min=previous.run_length_min if previous else None,
                idle_min=idle,
                previous_instrument_method=previous.header.instrument_method
                if previous
                else None,
                timing_warning="overlapping_acquisitions"
                if idle is not None and idle < 0
                else None,
            )
        )
    return tuple(rows)
