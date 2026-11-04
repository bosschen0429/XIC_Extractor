from __future__ import annotations

import numpy as np
import pytest

from xic_extractor.ms1_scan_index import (
    MS1ScanReadError,
    build_ms1_scan_index,
    validate_ms1_scan_index,
)
from xic_extractor.xic_models import XICRequest


class _Source:
    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        return 1, 3


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


def test_build_ms1_scan_index_can_fail_on_malformed_scan_arrays() -> None:
    class _Header:
        FirstSpectrum = 1
        LastSpectrum = 1

    class _Filter:
        MSOrder = "Ms"

    class _Malformed:
        Positions = [100.0, 101.0]
        Intensities = [10.0]

    class _Raw:
        RunHeaderEx = _Header()

        def GetFilterForScanNumber(self, _scan_number: int) -> _Filter:
            return _Filter()

        def GetSegmentedScanFromScanNumber(self, _scan_number: int, _stats):
            return _Malformed()

        def RetentionTimeFromScanNumber(self, _scan_number: int) -> float:
            return 2.5

    with pytest.raises(MS1ScanReadError, match="matching 1D arrays"):
        build_ms1_scan_index(_Raw())


def test_ms1_scan_index_validator_can_require_nonempty() -> None:
    with pytest.raises(ValueError, match="at least one MS1 scan"):
        validate_ms1_scan_index((), require_nonempty=True)


def test_build_ms1_scan_index_rejects_raw_without_ms1_scans() -> None:
    class _Header:
        FirstSpectrum = 1
        LastSpectrum = 1

    class _Filter:
        MSOrder = "Ms2"

    class _Raw:
        RunHeaderEx = _Header()

        def GetFilterForScanNumber(self, _scan_number: int) -> _Filter:
            return _Filter()

    with pytest.raises(MS1ScanReadError, match="at least one MS1 scan"):
        build_ms1_scan_index(_Raw())
