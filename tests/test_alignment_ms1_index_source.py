from types import SimpleNamespace

import numpy as np
import pytest

from xic_extractor.alignment.ms1_index_source import (
    MS1IndexedRawSource,
    build_ms1_scan_index,
    extract_index_xic,
    read_ms1_scan_index_npz,
    write_ms1_scan_index_npz,
)
from xic_extractor.ms1_scan_index import MS1ScanReadError
from xic_extractor.xic_models import XICRequest


def test_ms1_indexed_raw_source_extracts_mass_window_maxima() -> None:
    source = MS1IndexedRawSource(FakeRawHandle())

    traces = source.extract_xic_many(
        (
            XICRequest(mz=100.0, rt_min=1.0, rt_max=3.0, ppm_tol=10000.0),
            XICRequest(mz=101.0, rt_min=1.0, rt_max=1.0, ppm_tol=1.0),
        )
    )

    assert [trace.rt.tolist() for trace in traces] == [[1.0, 3.0], [1.0]]
    assert [trace.intensity.tolist() for trace in traces] == [[20.0, 40.0], [10.0]]


def test_ms1_indexed_raw_source_delegates_scan_window_lookup() -> None:
    source = MS1IndexedRawSource(FakeRawHandle())

    assert source.scan_window_for_request(
        XICRequest(mz=100.0, rt_min=1.0, rt_max=3.0, ppm_tol=10000.0)
    ) == (1, 3)


def test_alignment_ms1_index_source_fails_closed_on_malformed_ms1_scan() -> None:
    class _MalformedRaw(FakeRawHandle):
        def GetSegmentedScanFromScanNumber(
            self,
            scan_number: int,
            _scan_filter,
        ) -> SimpleNamespace:
            if scan_number == 1:
                return SimpleNamespace(
                    Positions=np.asarray([99.0, 100.0], dtype=float),
                    Intensities=np.asarray([10.0], dtype=float),
                )
            return super().GetSegmentedScanFromScanNumber(scan_number, _scan_filter)

    source = MS1IndexedRawSource(_MalformedRaw())

    with pytest.raises(MS1ScanReadError, match="scan 1.*matching 1D arrays"):
        source.extract_xic_many(
            (XICRequest(mz=100.0, rt_min=1.0, rt_max=3.0, ppm_tol=10.0),)
        )


def test_alignment_ms1_index_source_rejects_raw_without_ms1_scans() -> None:
    class _NoMs1Raw(FakeRawHandle):
        def GetFilterForScanNumber(self, scan_number: int) -> SimpleNamespace:
            return SimpleNamespace(MSOrder="Ms2")

    source = MS1IndexedRawSource(_NoMs1Raw())

    with pytest.raises(MS1ScanReadError, match="at least one MS1 scan"):
        source.extract_xic_many(
            (XICRequest(mz=100.0, rt_min=1.0, rt_max=3.0, ppm_tol=10.0),)
        )


def test_extract_index_xic_can_sum_mass_window_intensities() -> None:
    raw = FakeRawHandle()
    index = build_ms1_scan_index(raw)

    trace = extract_index_xic(
        raw,
        index,
        XICRequest(mz=100.0, rt_min=1.0, rt_max=3.0, ppm_tol=10000.0),
        intensity_mode="sum",
    )

    assert trace.intensity.tolist() == [35.0, 85.0]


def test_ms1_scan_index_npz_roundtrips_without_pickle(tmp_path) -> None:
    raw = FakeRawHandle()
    index = build_ms1_scan_index(raw)
    cache_path = write_ms1_scan_index_npz(tmp_path / "sample.ms1_index.npz", index)

    loaded = read_ms1_scan_index_npz(cache_path)
    trace = extract_index_xic(
        raw,
        loaded,
        XICRequest(mz=100.0, rt_min=1.0, rt_max=3.0, ppm_tol=10000.0),
        intensity_mode="sum",
    )

    assert len(loaded) == len(index)
    assert [scan.scan_number for scan in loaded] == [1, 3]
    assert [scan.rt for scan in loaded] == [1.0, 3.0]
    assert trace.intensity.tolist() == [35.0, 85.0]


class FakeRawHandle:
    def __init__(self) -> None:
        self._raw_file = self
        self.RunHeaderEx = SimpleNamespace(FirstSpectrum=1, LastSpectrum=3)

    @property
    def raw_chromatogram_call_count(self) -> int:
        return 0

    def GetFilterForScanNumber(self, scan_number: int) -> SimpleNamespace:
        return SimpleNamespace(MSOrder="Ms2" if scan_number == 2 else "Ms")

    def GetSegmentedScanFromScanNumber(
        self,
        scan_number: int,
        _scan_filter,
    ) -> SimpleNamespace:
        arrays = {
            1: ([99.0, 100.0, 101.0], [5.0, 20.0, 10.0]),
            2: ([100.0], [999.0]),
            3: ([99.0, 100.0, 101.0], [15.0, 40.0, 30.0]),
        }
        masses, intensities = arrays[scan_number]
        return SimpleNamespace(
            Positions=np.asarray(masses, dtype=float),
            Intensities=np.asarray(intensities, dtype=float),
        )

    def RetentionTimeFromScanNumber(self, scan_number: int) -> float:
        return float(scan_number)

    def ScanNumberFromRetentionTime(self, rt: float) -> int:
        return int(round(rt))
