"""Acquire only the MS2 events requested by a current RT-window question."""

from collections.abc import Iterator
from typing import Protocol

from xic_extractor.ms2_scan_models import Ms2PrecursorScope, Ms2ScanEvent, Ms2Window


class MS2ScanSource(Protocol):
    def iter_ms2_scans(
        self, rt_min: float, rt_max: float
    ) -> Iterator[Ms2ScanEvent]: ...


class MS2WindowSource(MS2ScanSource, Protocol):
    def read_ms2_window(
        self, rt_min: float, rt_max: float, *, precursor_scope: Ms2PrecursorScope
    ) -> tuple[Ms2ScanEvent, ...]:
        """Admitted MS2 events and acquisition errors; spectra only when admitted."""


def acquire_ms2_window(
    raw: MS2WindowSource,
    *,
    sample_key: str,
    rt_min: float,
    rt_max: float,
    precursor_scope: Ms2PrecursorScope | None = None,
) -> Ms2Window:
    """Materialize one bounded reader request while its source is open.

    With a precursor scope, only admitted events carry spectra.
    """
    events = (
        tuple(raw.iter_ms2_scans(rt_min, rt_max))
        if precursor_scope is None
        else raw.read_ms2_window(rt_min, rt_max, precursor_scope=precursor_scope)
    )
    return Ms2Window(
        sample_key=sample_key,
        rt_min=rt_min,
        rt_max=rt_max,
        events=events,
    )
