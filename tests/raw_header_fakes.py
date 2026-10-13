"""Stand-in RAW headers for tests whose RAW files are placeholders.

The analysis-set owner reads every RAW header to order injections. Tests that
fake spectra with placeholder files install this instead: each file's
modification time is its acquisition clock and its stem its sample name.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from xic_extractor.injection_timeline import InjectionHeader
from xic_extractor.sample_metadata import infer_sample_role

_CLOCK_ORIGIN = datetime(2026, 1, 1, 9)


def install_fake_raw_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    def analysis_header(path: Path, _dll_dir: Path):
        stat = path.stat()
        header = InjectionHeader(
            raw_path=str(path.resolve()),
            acquisition_source=f"D:/acquired/{path.name}",
            creation_date=datetime.fromtimestamp(stat.st_mtime),
            start_time_min=0.0,
            end_time_min=10.0,
            instrument_method="",
        )
        return header, infer_sample_role(None, sample_name=path.stem), path.stem

    monkeypatch.setattr("xic_extractor.raw_reader._analysis_header", analysis_header)


def install_raw_sample_headers(
    monkeypatch: pytest.MonkeyPatch,
    sample_header: Callable[[Path], object],
    clock_minutes: Callable[[Path], float],
) -> None:
    """Serve test sample headers through the real analysis-header reader.

    ``sample_header`` returns the vendor sample header (name, type, comment,
    method); ``clock_minutes`` places each RAW on the acquisition clock.
    """
    from xic_extractor import raw_reader

    monkeypatch.setattr(raw_reader, "_ANALYSIS_HEADER_CACHE", {})
    monkeypatch.setattr(
        raw_reader,
        "open_raw",
        lambda path, dll_dir: nullcontext(
            SimpleNamespace(sample_header=sample_header(Path(path)))
        ),
    )
    monkeypatch.setattr(
        raw_reader,
        "_injection_header",
        lambda _handle, path: InjectionHeader(
            raw_path=str(Path(path).resolve()),
            acquisition_source=f"D:/acquired/{Path(path).name}",
            creation_date=_CLOCK_ORIGIN + timedelta(minutes=clock_minutes(Path(path))),
            start_time_min=0.0,
            end_time_min=10.0,
            instrument_method="",
        ),
    )
