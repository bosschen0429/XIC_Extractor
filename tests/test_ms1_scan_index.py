from __future__ import annotations

from pathlib import Path

import numpy as np

from xic_extractor.ms1_scan_index import (
    MS1Scan,
    build_ms1_scan_index,
    extract_index_xic,
    read_ms1_scan_index_npz,
    write_ms1_scan_index_npz,
)
from xic_extractor.xic_models import XICRequest


class _Source:
    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        return 1, 3


def test_ms1_scan_index_npz_round_trip(tmp_path: Path) -> None:
    index = (
        MS1Scan(
            scan_number=1,
            rt=1.0,
            masses=np.asarray([100.0, 101.0], dtype=np.float64),
            intensities=np.asarray([10.0, 20.0], dtype=np.float64),
        ),
        MS1Scan(
            scan_number=2,
            rt=1.1,
            masses=np.asarray([100.0], dtype=np.float64),
            intensities=np.asarray([30.0], dtype=np.float64),
        ),
    )
    path = tmp_path / "index.npz"

    write_ms1_scan_index_npz(path, index)
    loaded = read_ms1_scan_index_npz(path)

    assert [scan.scan_number for scan in loaded] == [1, 2]
    assert [scan.rt for scan in loaded] == [1.0, 1.1]
    np.testing.assert_allclose(loaded[0].masses, [100.0, 101.0])
    np.testing.assert_allclose(loaded[1].intensities, [30.0])


def test_extract_index_xic_uses_ppm_window_and_max_intensity() -> None:
    index = (
        MS1Scan(
            scan_number=1,
            rt=1.0,
            masses=np.asarray([99.99, 100.0005, 100.0015], dtype=np.float64),
            intensities=np.asarray([5.0, 10.0, 20.0], dtype=np.float64),
        ),
        MS1Scan(
            scan_number=2,
            rt=1.1,
            masses=np.asarray([100.0002, 100.0030], dtype=np.float64),
            intensities=np.asarray([30.0, 7.0], dtype=np.float64),
        ),
    )
    request = XICRequest(mz=100.0, rt_min=0.9, rt_max=1.2, ppm_tol=20.0)

    trace = extract_index_xic(_Source(), index, request)

    np.testing.assert_allclose(trace.rt, [1.0, 1.1])
    np.testing.assert_allclose(trace.intensity, [20.0, 30.0])


def test_build_ms1_scan_index_prefers_segmented_scan_api() -> None:
    class _Header:
        FirstSpectrum = 1
        LastSpectrum = 1

    class _Filter:
        MSOrder = "Ms"

    class _Segmented:
        Positions = [100.0, 99.0]
        Intensities = [10.0, 5.0]

    class _Raw:
        RunHeaderEx = _Header()

        def GetFilterForScanNumber(self, _scan_number: int) -> _Filter:
            return _Filter()

        def GetSegmentedScanFromScanNumber(self, scan_number: int, _stats):
            assert scan_number == 1
            return _Segmented()

        def GetSimplifiedScan(self, _scan_number: int):  # pragma: no cover
            raise AssertionError("segmented API should be preferred")

        def RetentionTimeFromScanNumber(self, _scan_number: int) -> float:
            return 2.5

    index = build_ms1_scan_index(_Raw())

    assert len(index) == 1
    assert index[0].scan_number == 1
    assert index[0].rt == 2.5
    np.testing.assert_allclose(index[0].masses, [99.0, 100.0])
    np.testing.assert_allclose(index[0].intensities, [5.0, 10.0])
