from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from xic_extractor.raw_reader import (
    RawFileHandle,
    RawSampleHeader,
    _ThermoApi,
    _to_python_datetime,
    preflight_raw_reader,
    read_raw_sample_header,
)


def test_to_python_datetime() -> None:
    assert _to_python_datetime(None) is None
    assert _to_python_datetime("invalid") is None

    fake_dt = SimpleNamespace(
        Year=2026,
        Month=1,
        Day=9,
        Hour=18,
        Minute=6,
        Second=51,
        Millisecond=960,
    )
    py_dt = _to_python_datetime(fake_dt)
    assert py_dt == datetime(2026, 1, 9, 18, 6, 51, 960000)


def test_raw_file_handle_sample_header() -> None:
    fake_info = SimpleNamespace(
        SampleName="Sample_A",
        SampleId="ID_123",
        RowNumber=5,
        SampleType="Unknown",
        Vial="A:01",
        Comment="Test comment",
        InstrumentMethodFile=r"C:\methods\wash.meth",
    )
    fake_raw = SimpleNamespace(
        SampleInformation=fake_info,
        CreationDate=SimpleNamespace(
            Year=2026, Month=3, Day=15, Hour=10, Minute=30, Second=0, Millisecond=0
        ),
        FileName=r"C:\data\sample_a.raw",
        Dispose=lambda: None,
    )
    fake_thermo = _ThermoApi(
        raw_file_reader_adapter=None,
        device_ms=None,
        mass_range_factory=None,
        trace_settings_factory=None,
        mass_range_trace_type=None,
        ms2_order=None,
    )
    handle = RawFileHandle(fake_raw, fake_thermo)
    header = handle.sample_header

    assert isinstance(header, RawSampleHeader)
    assert header.raw_path == r"C:\data\sample_a.raw"
    assert header.raw_stem == "sample_a"
    assert header.sample_name == "Sample_A"
    assert header.sample_id == "ID_123"
    assert header.row_number == 5
    assert header.sample_type == "Unknown"
    assert header.creation_date == datetime(2026, 3, 15, 10, 30, 0)
    assert header.vial == "A:01"
    assert header.comment == "Test comment"
    assert header.instrument_method == r"C:\methods\wash.meth"


def test_real_raw_sample_header_if_available() -> None:
    raw_dir = Path(r"C:\Xcalibur\data\20260106_CSMU_NAA_Tissue_R")
    raw_path = raw_dir / "BenignfatBC0979_DNA.raw"
    dll_dir = Path(r"C:\Xcalibur\system\programs")
    if not raw_path.is_file() or not dll_dir.is_dir():
        pytest.skip("Local Thermo RAW and DLL environment not available")

    environment_errors = preflight_raw_reader(dll_dir)
    if environment_errors:
        pytest.skip("Thermo environment unavailable: " + "; ".join(environment_errors))

    header = read_raw_sample_header(raw_path, dll_dir)

    assert header.raw_stem == "BenignfatBC0979_DNA"
    assert header.row_number == 42
    assert header.sample_type == "Unknown"
    assert header.creation_date is not None
    assert header.creation_date.year == 2026
    assert header.vial == "B:E8"
