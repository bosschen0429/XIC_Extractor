from pathlib import Path
from types import SimpleNamespace

import pytest

from xic_extractor import raw_reader_errors


def test_injection_header_reads_vendor_clock_source_and_method(monkeypatch):
    from contextlib import contextmanager
    from datetime import datetime

    from xic_extractor import raw_reader

    raw = SimpleNamespace(
        SampleInformation=SimpleNamespace(
            RawFileName=r"D:\original\sample.raw", InstrumentMethodFile="method.meth"
        ),
        RunHeaderEx=SimpleNamespace(StartTime=1.5, EndTime=61.5),
    )
    handle = SimpleNamespace(
        _raw_file=raw, sample_header=SimpleNamespace(creation_date=datetime(2026, 1, 1))
    )
    closed = []

    @contextmanager
    def opened(*args, **kwargs):
        try:
            yield handle
        finally:
            closed.append(True)

    monkeypatch.setattr(raw_reader, "open_raw", opened)
    header = raw_reader.read_raw_injection_header(Path("copy.raw"), Path("dll"))
    assert header.acquisition_source == r"D:\original\sample.raw"
    assert header.creation_date == datetime(2026, 1, 1)
    assert header.start_time_min == 1.5
    assert header.end_time_min == 61.5
    assert header.instrument_method == "method.meth"
    assert closed == [True]
    handle.sample_header.creation_date = None
    with pytest.raises(raw_reader_errors.RawReaderError, match="creation date"):
        raw_reader.read_raw_injection_header(Path("copy.raw"), Path("dll"))
    assert closed == [True, True]


def test_sld_reader_projects_sequence_rows_without_clock(monkeypatch):
    from xic_extractor import raw_reader, raw_sequence

    sequence = SimpleNamespace(
        IsError=False,
        IsOpen=True,
        Samples=[
            SimpleNamespace(
                RawFileName="one", Path=r"D:\original", InstrumentMethodFile="one.meth"
            ),
            SimpleNamespace(
                RawFileName=r"D:\original\two.raw",
                Path=r"D:\other",
                InstrumentMethodFile="two.meth",
            ),
            SimpleNamespace(
                RawFileName="", Path=r"D:\original", InstrumentMethodFile=""
            ),
        ],
    )
    business = SimpleNamespace(
        SequenceFileReaderFactory=SimpleNamespace(ReadFile=lambda path: sequence)
    )
    monkeypatch.setattr(raw_reader, "preflight_raw_reader", lambda dll: [])
    monkeypatch.setattr(raw_reader, "ensure_assemblies_loaded", lambda *args: None)
    monkeypatch.setattr(raw_reader.importlib, "import_module", lambda name: business)
    rows = raw_sequence.read_sld_injections(Path("sequence.sld"), Path("dll"))
    assert [row.row_number for row in rows] == [1, 2, 3]
    assert [row.acquisition_source for row in rows] == [
        r"D:\original\one.raw",
        r"D:\original\two.raw",
        "",
    ]
    assert [row.instrument_method for row in rows] == ["one.meth", "two.meth", ""]
    sequence.IsError = True
    with pytest.raises(raw_reader_errors.RawReaderError, match="Unable to read"):
        raw_sequence.read_sld_injections(Path("sequence.sld"), Path("dll"))
