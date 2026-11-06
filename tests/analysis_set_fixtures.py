"""Helpers shared by test_analysis_set and other test modules."""

from datetime import datetime, timedelta
from pathlib import Path

from xic_extractor.injection_timeline import (
    InjectionHeader,
)

BASE = datetime(2026, 1, 1, 12)


def header(path: str, minute: float, *, source: str | None = None) -> InjectionHeader:
    return InjectionHeader(
        raw_path=path,
        acquisition_source=source or f"D:/acquired/{Path(path).name}",
        creation_date=BASE + timedelta(minutes=minute),
        start_time_min=0,
        end_time_min=10,
        instrument_method="m.meth",
    )
