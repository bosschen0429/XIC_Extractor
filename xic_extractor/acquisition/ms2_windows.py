"""Acquire only the MS2 events requested by a current RT-window question."""

from collections.abc import Iterator
from typing import Protocol

from xic_extractor.ms2_scan_models import Ms2PrecursorScope, Ms2ScanEvent, Ms2Window


class MS2ScanSource(Protocol):
    def iter_ms2_scans(
        self, rt_min: float, rt_max: float
    ) -> Iterator[Ms2ScanEvent]: ...


def acquire_ms2_window(
    raw: MS2ScanSource,
    *,
    sample_key: str,
    rt_min: float,
    rt_max: float,
    precursor_scope: Ms2PrecursorScope | None = None,
) -> Ms2Window:
    """Materialize one bounded reader request while its source is open.

    With a precursor scope, only admitted events carry spectra; the others
    remain counted as scanned events of the window.
    """
    if precursor_scope is None:
        return Ms2Window(
            sample_key=sample_key,
            rt_min=rt_min,
            rt_max=rt_max,
            events=tuple(raw.iter_ms2_scans(rt_min, rt_max)),
        )
    read_window = getattr(raw, "read_ms2_window", None)
    if callable(read_window):
        events, unread = read_window(rt_min, rt_max, precursor_scope=precursor_scope)
    else:
        scanned = tuple(raw.iter_ms2_scans(rt_min, rt_max))
        events = tuple(
            event
            for event in scanned
            if event.scan is None
            or precursor_scope(event.scan.precursor_mz, event.scan.precursor_metadata)
        )
        unread = len(scanned) - len(events)
    return Ms2Window(
        sample_key=sample_key,
        rt_min=rt_min,
        rt_max=rt_max,
        events=events,
        unread_event_count=unread,
    )
