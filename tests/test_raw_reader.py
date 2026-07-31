import importlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


def test_importing_raw_reader_does_not_import_clr() -> None:
    module = importlib.import_module("xic_extractor.raw_reader")

    assert "clr" not in module.__dict__


def test_preflight_reports_missing_pythonnet(tmp_path: Path) -> None:
    from xic_extractor.raw_reader import preflight_raw_reader

    errors = preflight_raw_reader(tmp_path, import_module_func=_missing_pythonnet)

    assert any("pythonnet" in error and "Install" in error for error in errors)


def test_preflight_reports_missing_dotnet_runtime(tmp_path: Path) -> None:
    from xic_extractor.raw_reader import preflight_raw_reader

    tmp_path.mkdir(exist_ok=True)
    _write_expected_dlls(tmp_path)
    errors = preflight_raw_reader(
        tmp_path, import_module_func=_broken_pythonnet_runtime
    )

    assert any(".NET runtime" in error and "Install" in error for error in errors)


def test_preflight_reports_missing_dll_dir(tmp_path: Path) -> None:
    from xic_extractor.raw_reader import preflight_raw_reader

    missing = tmp_path / "missing"
    errors = preflight_raw_reader(missing, import_module_func=_working_imports())

    assert any(
        "Xcalibur DLL directory" in error and str(missing) in error for error in errors
    )


def test_preflight_reports_missing_expected_thermo_dll(tmp_path: Path) -> None:
    from xic_extractor.raw_reader import preflight_raw_reader

    tmp_path.mkdir(exist_ok=True)
    errors = preflight_raw_reader(tmp_path, import_module_func=_working_imports())

    assert any("ThermoFisher.CommonCore.Data.dll" in error for error in errors)
    assert any("open Settings" in error for error in errors)


def test_open_raw_raises_raw_reader_error_when_dll_load_fails(tmp_path: Path) -> None:
    from xic_extractor.raw_reader import RawReaderError, open_raw

    _write_expected_dlls(tmp_path)
    raw_path = tmp_path / "sample.raw"
    raw_path.write_text("", encoding="utf-8")

    with pytest.raises(RawReaderError, match="Failed to load Thermo DLL"):
        open_raw(
            raw_path,
            tmp_path,
            _import_module=_working_imports(add_reference_error=RuntimeError("boom")),
            _thermo=_fake_thermo(SimpleNamespace()),
        )


def test_open_raw_raises_raw_reader_error_when_preflight_fails(tmp_path: Path) -> None:
    from xic_extractor.raw_reader import RawReaderError, open_raw

    with pytest.raises(RawReaderError, match="Xcalibur DLL directory"):
        open_raw(
            tmp_path / "sample.raw",
            tmp_path / "missing",
            _import_module=_working_imports(),
        )


def test_open_raw_disposes_vendor_handle_when_select_instrument_fails(
    tmp_path: Path,
) -> None:
    from xic_extractor.raw_reader import (
        RawReaderOpenError,
        open_raw,
        reset_reader_state,
    )

    class _SelectFailRaw(_FakeRaw):
        def SelectInstrument(self, *_args) -> None:
            raise RuntimeError("select failed")

    reset_reader_state()
    _write_expected_dlls(tmp_path)
    raw = _SelectFailRaw()

    with pytest.raises(RawReaderOpenError, match="select failed") as caught:
        open_raw(
            tmp_path / "sample.raw",
            tmp_path,
            _import_module=_working_imports(),
            _thermo=_fake_thermo(raw),
        )

    assert caught.value.cleanup_failed is False
    assert raw.disposed is True


def test_open_raw_reports_cleanup_failure_after_vendor_open_error(
    tmp_path: Path,
) -> None:
    from xic_extractor.raw_reader import (
        RawReaderOpenError,
        open_raw,
        reset_reader_state,
    )

    class _CleanupFailRaw(_FakeRaw):
        def SelectInstrument(self, *_args) -> None:
            raise RuntimeError("select failed")

        def Dispose(self) -> None:
            raise RuntimeError("dispose failed")

    reset_reader_state()
    _write_expected_dlls(tmp_path)
    raw = _CleanupFailRaw()

    with pytest.raises(
        RawReaderOpenError,
        match="vendor cleanup failed: dispose failed",
    ) as caught:
        open_raw(
            tmp_path / "sample.raw",
            tmp_path,
            _import_module=_working_imports(),
            _thermo=_fake_thermo(raw),
        )

    assert caught.value.cleanup_failed is True


def test_open_raw_loads_only_expected_absolute_dll_paths(tmp_path: Path) -> None:
    from xic_extractor.raw_reader import (
        EXPECTED_THERMO_DLLS,
        open_raw,
        reset_reader_state,
    )

    reset_reader_state()
    _write_expected_dlls(tmp_path)
    raw = SimpleNamespace(SelectInstrument=lambda *_: None)
    calls: list[str] = []
    open_raw(
        tmp_path / "sample.raw",
        tmp_path,
        _import_module=_working_imports(add_reference_calls=calls),
        _thermo=_fake_thermo(raw),
    )

    assert [Path(call).name for call in calls] == list(EXPECTED_THERMO_DLLS)
    assert all(Path(call).is_absolute() for call in calls)
    assert all(Path(call).parent == tmp_path.resolve() for call in calls)


def test_open_raw_binds_only_the_verified_acquisition_source(tmp_path: Path) -> None:
    from xic_extractor.processing_identity import build_acquisition_source_identity
    from xic_extractor.raw_reader import open_raw, reset_reader_state

    reset_reader_state()
    _write_expected_dlls(tmp_path)
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"sample-a")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    raw = _FakeRaw()

    handle = open_raw(
        raw_path,
        tmp_path,
        expected_acquisition_source=source,
        _import_module=_working_imports(),
        _thermo=_fake_thermo(raw),
    )

    assert handle.acquisition_source == source


@pytest.mark.parametrize(
    ("acquisition_format", "observation_namespace"),
    (
        ("synthetic_no_raw", "thermo_raw_file_reader:Device.MS:1"),
        ("thermo_raw", "thermo_raw_file_reader:Device.MS:2"),
    ),
)
def test_open_raw_rejects_source_identity_for_wrong_adapter_semantics(
    tmp_path: Path,
    acquisition_format: str,
    observation_namespace: str,
) -> None:
    from xic_extractor.processing_identity import build_acquisition_source_identity
    from xic_extractor.raw_reader import RawReaderError, open_raw, reset_reader_state

    reset_reader_state()
    _write_expected_dlls(tmp_path)
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"sample-a")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=acquisition_format,
        observation_namespace=observation_namespace,
    )

    with pytest.raises(
        RawReaderError,
        match="does not describe the Thermo RAW MS-controller-1 adapter",
    ):
        open_raw(
            raw_path,
            tmp_path,
            expected_acquisition_source=source,
            _import_module=_working_imports(),
            _thermo=_fake_thermo(_FakeRaw()),
        )


def test_owner_bound_raw_handle_seals_payload_and_source(tmp_path: Path) -> None:
    from xic_extractor.processing_identity import build_acquisition_source_identity
    from xic_extractor.raw_reader import (
        open_raw,
        require_owner_bound_raw_handle,
        reset_reader_state,
    )

    reset_reader_state()
    _write_expected_dlls(tmp_path)
    raw_path = tmp_path / "sample.raw"
    other_path = tmp_path / "other.raw"
    raw_path.write_bytes(b"sample-a")
    other_path.write_bytes(b"sample-b")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    other_source = build_acquisition_source_identity(
        other_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    raw = _FakeRaw()
    thermo = _fake_thermo(raw)
    handle = open_raw(
        raw_path,
        tmp_path,
        expected_acquisition_source=source,
        _import_module=_working_imports(),
        _thermo=thermo,
    )

    for attribute, replacement in (
        ("_raw_file", _FakeRaw()),
        ("_thermo", _fake_thermo(_FakeRaw())),
        ("_bound_acquisition_source", other_source),
    ):
        with pytest.raises(AttributeError, match="is sealed"):
            setattr(handle, attribute, replacement)
        with pytest.raises(AttributeError, match="is sealed"):
            delattr(handle, attribute)

    assert handle.acquisition_source == source
    assert require_owner_bound_raw_handle(handle, source) is handle


def test_direct_raw_handle_cannot_claim_an_acquisition_source(tmp_path: Path) -> None:
    from xic_extractor.processing_identity import build_acquisition_source_identity
    from xic_extractor.raw_reader import (
        RawFileHandle,
        RawReaderError,
        require_owner_bound_raw_handle,
    )

    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"sample-a")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    raw = _FakeRaw()
    handle = RawFileHandle(raw, _fake_thermo(raw))

    with pytest.raises(
        RawReaderError,
        match="was not owner-bound by open_raw",
    ):
        require_owner_bound_raw_handle(handle, source)


def test_open_raw_rejects_path_content_from_another_source(tmp_path: Path) -> None:
    from xic_extractor.processing_identity import build_acquisition_source_identity
    from xic_extractor.raw_reader import RawReaderError, open_raw, reset_reader_state

    reset_reader_state()
    _write_expected_dlls(tmp_path)
    expected_path = tmp_path / "sample-a.raw"
    actual_path = tmp_path / "sample-b.raw"
    expected_path.write_bytes(b"sample-a")
    actual_path.write_bytes(b"sample-b")
    expected_source = build_acquisition_source_identity(
        expected_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )

    with pytest.raises(
        RawReaderError,
        match="does not match expected acquisition source",
    ):
        open_raw(
            actual_path,
            tmp_path,
            expected_acquisition_source=expected_source,
            _import_module=_working_imports(),
            _thermo=_fake_thermo(_FakeRaw()),
        )


def test_open_raw_rejects_source_path_replacement_during_vendor_open(
    tmp_path: Path,
) -> None:
    from xic_extractor.processing_identity import build_acquisition_source_identity
    from xic_extractor.raw_reader import RawReaderError, open_raw, reset_reader_state

    reset_reader_state()
    _write_expected_dlls(tmp_path)
    raw_path = tmp_path / "sample.raw"
    replacement_path = tmp_path / "replacement.raw"
    raw_path.write_bytes(b"sample-a")
    replacement_path.write_bytes(b"sample-b")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    raw = _FakeRaw()
    thermo = _fake_thermo(raw)

    def _replace_before_open(_path: str):
        replacement_path.replace(raw_path)
        return raw

    thermo.raw_file_reader_adapter.FileFactory = _replace_before_open

    with pytest.raises(
        RawReaderError,
        match="source content or path changed while the vendor handle was opening",
    ):
        open_raw(
            raw_path,
            tmp_path,
            expected_acquisition_source=source,
            _import_module=_working_imports(),
            _thermo=thermo,
        )

    assert raw.disposed


def test_open_raw_rechecks_content_after_vendor_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xic_extractor.raw_reader as raw_reader
    from xic_extractor.processing_identity import build_acquisition_source_identity

    raw_reader.reset_reader_state()
    _write_expected_dlls(tmp_path)
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"sample-a")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    raw = _FakeRaw()
    thermo = _fake_thermo(raw)

    def _mutate_after_precheck(_path: str):
        raw_path.write_bytes(b"sample-b")
        return raw

    thermo.raw_file_reader_adapter.FileFactory = _mutate_after_precheck
    monkeypatch.setattr(
        raw_reader,
        "_stat_identity",
        lambda _value: (1, 1, len(b"sample-a"), 1, 1),
    )

    with pytest.raises(
        raw_reader.RawReaderError,
        match="source content or path changed while the vendor handle was opening",
    ):
        raw_reader.open_raw(
            raw_path,
            tmp_path,
            expected_acquisition_source=source,
            _import_module=_working_imports(),
            _thermo=thermo,
        )

    assert raw.disposed


def test_raw_file_handle_exit_disposes_raw_object() -> None:
    from xic_extractor.raw_reader import RawFileHandle

    raw = _FakeRaw()
    with RawFileHandle(raw, _fake_thermo(raw)):
        pass

    assert raw.disposed is True


def test_extract_xic_returns_empty_arrays_when_chromatogram_is_empty() -> None:
    from xic_extractor.raw_reader import RawFileHandle

    raw = _FakeRaw(chromatogram=_FakeChromatogram([], []))
    handle = RawFileHandle(raw, _fake_thermo(raw))

    rt, intensity = handle.extract_xic(mz=258.0, rt_min=8.0, rt_max=10.0, ppm_tol=20.0)

    assert isinstance(rt, np.ndarray)
    assert isinstance(intensity, np.ndarray)
    assert len(rt) == 0
    assert len(intensity) == 0


def test_extract_xic_sets_trace_type_when_supported() -> None:
    from xic_extractor.raw_reader import RawFileHandle

    settings = SimpleNamespace(MassRanges=None, Filter=None, TraceType=None)
    thermo = _fake_thermo(_FakeRaw())
    thermo.trace_settings_factory = lambda: settings
    raw = _FakeRaw(chromatogram=_FakeChromatogram([8.1], [10.0]))
    handle = RawFileHandle(raw, thermo)

    handle.extract_xic(mz=258.0, rt_min=8.0, rt_max=10.0, ppm_tol=20.0)

    assert settings.TraceType == "MassRange"


def test_extract_xic_prefers_mass_range_trace_type_constructor() -> None:
    from xic_extractor.raw_reader import RawFileHandle

    calls: list[str] = []

    def _trace_settings(trace_type: str):
        calls.append(trace_type)
        return SimpleNamespace(MassRanges=None, Filter=None, TraceType=None)

    raw = _FakeRaw(chromatogram=_FakeChromatogram([8.1], [10.0]))
    thermo = _fake_thermo(raw)
    thermo.trace_settings_factory = _trace_settings
    handle = RawFileHandle(raw, thermo)

    handle.extract_xic(mz=258.0, rt_min=8.0, rt_max=10.0, ppm_tol=20.0)

    assert calls == ["MassRange"]


def test_extract_xic_returns_positions_and_intensities() -> None:
    from xic_extractor.raw_reader import RawFileHandle

    raw = _FakeRaw(chromatogram=_FakeChromatogram([8.1, 8.2], [10.0, 20.0]))
    handle = RawFileHandle(raw, _fake_thermo(raw))

    rt, intensity = handle.extract_xic(mz=258.0, rt_min=8.0, rt_max=10.0, ppm_tol=20.0)

    assert rt.tolist() == [8.1, 8.2]
    assert intensity.tolist() == [10.0, 20.0]


def test_extract_xic_many_batches_shared_scan_window() -> None:
    from xic_extractor.raw_reader import RawFileHandle
    from xic_extractor.xic_models import XICRequest

    raw = _FakeRaw(
        chromatogram=_FakeChromatogram(
            positions=[[8.1, 8.2], [8.1, 8.2]],
            intensities=[[10.0, 20.0], [30.0, 40.0]],
        )
    )
    handle = RawFileHandle(raw, _fake_thermo(raw))

    traces = handle.extract_xic_many(
        (
            XICRequest(mz=258.0, rt_min=8.0, rt_max=10.0, ppm_tol=20.0),
            XICRequest(mz=259.0, rt_min=8.0, rt_max=10.0, ppm_tol=20.0),
        )
    )

    assert len(traces) == 2
    assert traces[0].rt.tolist() == [8.1, 8.2]
    assert traces[0].intensity.tolist() == [10.0, 20.0]
    assert traces[1].rt.tolist() == [8.1, 8.2]
    assert traces[1].intensity.tolist() == [30.0, 40.0]
    assert raw.chromatogram_calls == [((1, 2), 2)]
    assert handle.raw_chromatogram_call_count == 1


def test_extract_xic_many_preserves_order_for_mixed_scan_windows() -> None:
    from xic_extractor.raw_reader import RawFileHandle
    from xic_extractor.xic_models import XICRequest

    raw = _FakeRaw(
        chromatogram_by_window={
            (1, 2): _FakeChromatogram([[8.1], [8.1]], [[10.0], [10.0]]),
            (2, 2): _FakeChromatogram([[8.2]], [[20.0]]),
        }
    )
    handle = RawFileHandle(raw, _fake_thermo(raw))

    traces = handle.extract_xic_many(
        (
            XICRequest(mz=258.0, rt_min=8.0, rt_max=10.0, ppm_tol=20.0),
            XICRequest(mz=259.0, rt_min=8.5, rt_max=10.0, ppm_tol=20.0),
            XICRequest(mz=260.0, rt_min=8.0, rt_max=10.0, ppm_tol=20.0),
        )
    )

    assert [trace.intensity.tolist() for trace in traces] == [[10.0], [20.0], [10.0]]
    assert raw.chromatogram_calls == [((1, 2), 2), ((2, 2), 1)]
    assert handle.raw_chromatogram_call_count == 2


def test_scan_window_for_request_uses_raw_scan_numbers() -> None:
    from xic_extractor.raw_reader import RawFileHandle
    from xic_extractor.xic_models import XICRequest

    raw = _FakeRaw()
    handle = RawFileHandle(raw, _fake_thermo(raw))

    window = handle.scan_window_for_request(
        XICRequest(mz=258.0, rt_min=8.0, rt_max=10.0, ppm_tol=20.0)
    )

    assert window == (1, 2)


def test_iter_ms2_scans_yields_parsed_scans() -> None:
    from xic_extractor.raw_reader import RawFileHandle

    raw = _FakeRaw(
        filters={1: _FakeFilter(is_ms2=True, precursor_mz=258.1)},
        scans={1: _FakeScan([126.0, 127.0], [10.0, 50.0])},
    )
    handle = RawFileHandle(raw, _fake_thermo(raw))

    events = list(handle.iter_ms2_scans(rt_min=8.0, rt_max=8.0))

    assert len(events) == 1
    assert events[0].parse_error is None
    assert events[0].scan is not None
    assert events[0].scan.scan_number == 1
    assert events[0].scan.precursor_mz == pytest.approx(258.1)
    assert events[0].scan.masses.tolist() == [126.0, 127.0]
    assert events[0].scan.intensities.tolist() == [10.0, 50.0]
    assert events[0].scan.base_peak == pytest.approx(50.0)


def test_iter_ms2_scans_skips_non_ms2_scans() -> None:
    from xic_extractor.raw_reader import RawFileHandle

    raw = _FakeRaw(filters={1: _FakeFilter(is_ms2=False, precursor_mz=258.1)})
    handle = RawFileHandle(raw, _fake_thermo(raw))

    assert list(handle.iter_ms2_scans(rt_min=8.0, rt_max=8.0)) == []


def test_iter_ms2_scans_reports_missing_precursor_reaction() -> None:
    from xic_extractor.raw_reader import RawFileHandle

    raw = _FakeRaw(filters={1: _FakeFilter(is_ms2=True, precursor_mz=258.1)})
    raw.filters[1].Filter.Reactions = []
    handle = RawFileHandle(raw, _fake_thermo(raw))

    events = list(handle.iter_ms2_scans(rt_min=8.0, rt_max=8.0))

    assert events[0].scan is None
    assert events[0].parse_error is not None
    assert "precursor reaction" in events[0].parse_error


def test_iter_ms2_scans_reads_precursor_from_scan_filter_get_reaction() -> None:
    from xic_extractor.raw_reader import RawFileHandle

    raw = _FakeRaw(
        filters={1: _FakeScanFilterWithReaction(precursor_mz=537.0779)},
        scans={1: _FakeScan([127.0], [50.0])},
    )
    handle = RawFileHandle(raw, _fake_thermo(raw))

    events = list(handle.iter_ms2_scans(rt_min=8.0, rt_max=8.0))

    assert events[0].parse_error is None
    assert events[0].scan is not None
    assert events[0].scan.precursor_mz == pytest.approx(537.0779)


def test_iter_ms2_scans_yields_parse_error_events() -> None:
    from xic_extractor.raw_reader import RawFileHandle

    raw = _FakeRaw(
        filters={1: _FakeFilter(is_ms2=True, precursor_mz=258.1)},
        scan_errors={1: RuntimeError("bad scan")},
    )
    handle = RawFileHandle(raw, _fake_thermo(raw))

    events = list(handle.iter_ms2_scans(rt_min=8.0, rt_max=8.0))

    assert len(events) == 1
    assert events[0].scan is None
    assert events[0].parse_error is not None
    assert "bad scan" in events[0].parse_error
    assert events[0].scan_number == 1


def _write_expected_dlls(path: Path) -> None:
    from xic_extractor.raw_reader import EXPECTED_THERMO_DLLS

    path.mkdir(parents=True, exist_ok=True)
    for dll_name in EXPECTED_THERMO_DLLS:
        (path / dll_name).write_text("", encoding="utf-8")


def _missing_pythonnet(name: str):
    if name == "pythonnet":
        raise ImportError("missing")
    return SimpleNamespace()


def _broken_pythonnet_runtime(name: str):
    if name == "pythonnet":
        return SimpleNamespace(
            get_runtime_info=lambda: (_ for _ in ()).throw(RuntimeError("no runtime"))
        )
    return SimpleNamespace(AddReference=lambda *_: None)


def _working_imports(
    *,
    add_reference_calls: list[str] | None = None,
    add_reference_error: Exception | None = None,
):
    calls = add_reference_calls if add_reference_calls is not None else []

    def _import(name: str):
        if name == "pythonnet":
            return SimpleNamespace(get_runtime_info=lambda: "ok")
        if name == "clr":

            def _add_reference(path: str) -> None:
                if add_reference_error is not None:
                    raise add_reference_error
                calls.append(path)

            return SimpleNamespace(AddReference=_add_reference)
        return SimpleNamespace()

    return _import


def _fake_thermo(raw):
    return SimpleNamespace(
        raw_file_reader_adapter=SimpleNamespace(FileFactory=lambda _: raw),
        device_ms="MS",
        mass_range_factory=lambda low, high: (low, high),
        trace_settings_factory=lambda: SimpleNamespace(MassRanges=None, Filter=None),
        mass_range_trace_type="MassRange",
        ms2_order="Ms2",
    )


class _FakeChromatogram:
    def __init__(self, positions, intensities) -> None:
        if positions and not isinstance(positions[0], list):
            positions = [positions]
        if intensities and not isinstance(intensities[0], list):
            intensities = [intensities]
        self.PositionsArray = positions
        self.IntensitiesArray = intensities


class _FakeRaw:
    def __init__(
        self,
        *,
        chromatogram: _FakeChromatogram | None = None,
        chromatogram_by_window: dict[tuple[int, int], _FakeChromatogram] | None = None,
        filters: dict[int, object] | None = None,
        scans: dict[int, object] | None = None,
        scan_errors: dict[int, Exception] | None = None,
    ) -> None:
        self.disposed = False
        self.chromatogram = chromatogram or _FakeChromatogram([8.1], [10.0])
        self.chromatogram_by_window = chromatogram_by_window or {}
        self.chromatogram_calls: list[tuple[tuple[int, int], int]] = []
        self.filters = filters or {}
        self.scans = scans or {}
        self.scan_errors = scan_errors or {}

    def Dispose(self) -> None:
        self.disposed = True

    def SelectInstrument(self, *_args) -> None:
        return None

    def ScanNumberFromRetentionTime(self, rt: float) -> int:
        return 1 if rt <= 8.0 else 2

    def RetentionTimeFromScanNumber(self, scan_number: int) -> float:
        return 8.0 + (scan_number - 1) * 0.1

    def GetChromatogramData(self, settings, start_scan, end_scan):
        self.chromatogram_calls.append(((start_scan, end_scan), len(settings)))
        return self.chromatogram_by_window.get(
            (start_scan, end_scan),
            self.chromatogram,
        )

    def GetFilterForScanNumber(self, scan_number: int):
        return self.filters[scan_number]

    def GetSimplifiedScan(self, scan_number: int):
        if scan_number in self.scan_errors:
            raise self.scan_errors[scan_number]
        return self.scans[scan_number]


class _FakeFilter:
    def __init__(self, *, is_ms2: bool, precursor_mz: float) -> None:
        self.MSOrder = "Ms2" if is_ms2 else "Ms1"
        self.Filter = SimpleNamespace(
            Reactions=[SimpleNamespace(PrecursorMass=precursor_mz)]
        )


class _FakeScanFilterWithReaction:
    def __init__(self, *, precursor_mz: float) -> None:
        self.MSOrder = "Ms2"
        self.reaction = SimpleNamespace(PrecursorMass=precursor_mz)

    def GetReaction(self, index: int):
        if index != 0:
            raise IndexError(index)
        return self.reaction


class _FakeScan:
    def __init__(self, masses, intensities) -> None:
        self.Masses = masses
        self.Intensities = intensities
