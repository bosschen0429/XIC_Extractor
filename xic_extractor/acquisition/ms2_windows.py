"""Acquire only the MS2 events requested by a current RT-window question."""

from collections.abc import Iterator
from typing import Protocol

from xic_extractor.ms2_scan_models import Ms2ScanEvent, Ms2Window


class MS2ScanSource(Protocol):
    def iter_ms2_scans(
        self, rt_min: float, rt_max: float
    ) -> Iterator[Ms2ScanEvent]: ...


def acquire_ms2_window(
    raw: MS2ScanSource, *, sample_key: str, rt_min: float, rt_max: float
) -> Ms2Window:
    """Materialize one bounded reader request while its source is open."""
    return Ms2Window(
        sample_key=sample_key,
        rt_min=rt_min,
        rt_max=rt_max,
        events=tuple(raw.iter_ms2_scans(rt_min, rt_max)),
    )
