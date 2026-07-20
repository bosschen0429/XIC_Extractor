from types import SimpleNamespace

import numpy as np
import pytest

from xic_extractor.alignment.ms1_index_source import (
    MS1IndexedRawSource,
    RawSuperWindowSource,
    build_ms1_scan_index,
    extract_index_xic,
    read_ms1_scan_index_npz,
    source_for_owner_build_backend,
    write_ms1_scan_index_npz,
)
from xic_extractor.ms1_scan_index import MS1ScanReadError
from xic_extractor.xic_models import XICRequest, XICTrace


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


def test_source_for_owner_build_backend_is_opt_in() -> None:
    raw = FakeRawHandle()

    assert source_for_owner_build_backend(raw, "raw") is raw
    superwindow = source_for_owner_build_backend(raw, "raw_superwindow")
    assert isinstance(superwindow, RawSuperWindowSource)
    indexed = source_for_owner_build_backend(raw, "ms1_index")

    assert isinstance(indexed, MS1IndexedRawSource)


def test_raw_superwindow_source_merges_overlaps_and_crops_to_request_order() -> None:
    raw = FakeSuperWindowRawHandle()
    source = RawSuperWindowSource(raw, superwindow_span_factor=2)

    traces = source.extract_xic_many(
        (
            XICRequest(mz=200.0, rt_min=2.0, rt_max=4.0, ppm_tol=10.0),
            XICRequest(mz=100.0, rt_min=1.0, rt_max=3.0, ppm_tol=10.0),
        )
    )

    assert raw.call_count == 1
    assert [
        (request.mz, request.rt_min, request.rt_max) for request in raw.batches[0]
    ] == [
        (100.0, 1.0, 4.0),
        (200.0, 1.0, 4.0),
    ]
    assert traces[0].rt.tolist() == [2.0, 3.0, 4.0]
    assert traces[0].intensity.tolist() == [2002.0, 2003.0, 2004.0]
    assert traces[1].rt.tolist() == [1.0, 2.0, 3.0]
    assert traces[1].intensity.tolist() == [1001.0, 1002.0, 1003.0]


def test_raw_superwindow_source_respects_span_limit() -> None:
    raw = FakeSuperWindowRawHandle()
    source = RawSuperWindowSource(raw, superwindow_span_factor=1)

    source.extract_xic_many(
        (
            XICRequest(mz=100.0, rt_min=1.0, rt_max=3.0, ppm_tol=10.0),
            XICRequest(mz=200.0, rt_min=2.0, rt_max=4.0, ppm_tol=10.0),
        )
    )

    assert raw.call_count == 2


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


class FakeSuperWindowRawHandle:
    def __init__(self) -> None:
        self.call_count = 0
        self.batches: list[tuple[XICRequest, ...]] = []

    @property
    def raw_chromatogram_call_count(self) -> int:
        return self.call_count

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        return int(round(request.rt_min)), int(round(request.rt_max))

    def retention_time_for_scan(self, scan_number: int) -> float:
        return float(scan_number)

    def extract_xic_many(
        self,
        requests: tuple[XICRequest, ...],
    ):
        self.call_count += 1
        self.batches.append(requests)
        traces = []
        for request in requests:
            rt = np.arange(
                int(round(request.rt_min)),
                int(round(request.rt_max)) + 1,
                dtype=float,
            )
            traces.append(
                XICTrace.from_arrays(
                    rt,
                    request.mz * 10.0 + rt,
                )
            )
        return tuple(traces)
