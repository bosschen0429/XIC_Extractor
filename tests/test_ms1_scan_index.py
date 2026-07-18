from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from xic_extractor.ms1_scan_index import (
    MS1Scan,
    MS1ScanIndexProvenance,
    MS1ScanReadError,
    build_ms1_scan_index,
    extract_index_xic,
    read_ms1_scan_index_npz,
    read_ms1_scan_index_provenance,
    validate_ms1_scan_index,
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


def test_ms1_scan_index_npz_round_trips_source_provenance(tmp_path: Path) -> None:
    path = tmp_path / "index.npz"
    provenance = MS1ScanIndexProvenance(
        sample_stem="SampleA",
        source_path="C:/raw/SampleA.raw",
        source_sha256="a" * 64,
    )

    write_ms1_scan_index_npz(path, (), provenance=provenance)

    assert read_ms1_scan_index_provenance(path) == provenance
    assert read_ms1_scan_index_npz(path) == ()


def test_ms1_scan_index_npz_rejects_empty_provenance_array(tmp_path: Path) -> None:
    path = tmp_path / "index.npz"
    np.savez_compressed(
        path,
        provenance_sample_stem=np.asarray([], dtype=str),
        provenance_source_path=np.asarray(["C:/raw/SampleA.raw"]),
        provenance_source_sha256=np.asarray(["a" * 64]),
    )

    with pytest.raises(ValueError, match="source provenance is incomplete"):
        read_ms1_scan_index_provenance(path)


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


@pytest.mark.parametrize(
    "index",
    [
        (
            MS1Scan(
                scan_number=1,
                rt=float("nan"),
                masses=np.asarray([100.0]),
                intensities=np.asarray([10.0]),
            ),
        ),
        (
            MS1Scan(
                scan_number=1,
                rt=True,  # type: ignore[arg-type]
                masses=np.asarray([100.0]),
                intensities=np.asarray([10.0]),
            ),
        ),
        (
            MS1Scan(
                scan_number=1,
                rt=1.0,
                masses=np.asarray([True]),
                intensities=np.asarray([10.0]),
            ),
        ),
        (
            MS1Scan(
                scan_number=1,
                rt=1.0,
                masses=np.asarray([101.0, 100.0]),
                intensities=np.asarray([10.0, 20.0]),
            ),
        ),
        (
            MS1Scan(
                scan_number=1,
                rt=1.0,
                masses=np.asarray([100.0, float("inf")]),
                intensities=np.asarray([10.0, 20.0]),
            ),
        ),
        (
            MS1Scan(
                scan_number=1,
                rt=1.0,
                masses=np.asarray([100.0]),
                intensities=np.asarray([float("nan")]),
            ),
        ),
    ],
)
def test_ms1_scan_index_writer_rejects_invalid_scan_payloads(
    tmp_path: Path,
    index: tuple[MS1Scan, ...],
) -> None:
    with pytest.raises(ValueError):
        write_ms1_scan_index_npz(tmp_path / "invalid.npz", index)


def test_ms1_scan_index_reader_rejects_missing_required_array(tmp_path: Path) -> None:
    path = tmp_path / "missing.npz"
    np.savez(
        path,
        schema_version=np.asarray(["ms1_scan_index_npz_v1"]),
        scan_number=np.asarray([1]),
        rt=np.asarray([1.0]),
        offsets=np.asarray([0, 1]),
        masses=np.asarray([100.0]),
    )

    with pytest.raises(ValueError, match="missing required field 'intensities'"):
        read_ms1_scan_index_npz(path)


def test_ms1_scan_index_reader_rejects_unordered_scan_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unordered.npz"
    np.savez(
        path,
        schema_version=np.asarray(["ms1_scan_index_npz_v1"]),
        scan_number=np.asarray([2, 1]),
        rt=np.asarray([1.1, 1.0]),
        offsets=np.asarray([0, 1, 2]),
        masses=np.asarray([100.0, 101.0]),
        intensities=np.asarray([10.0, 20.0]),
    )

    with pytest.raises(ValueError, match="scan_number must be strictly increasing"):
        read_ms1_scan_index_npz(path)


@pytest.mark.parametrize("scan_number", [True, 1.5])
def test_ms1_scan_index_writer_rejects_non_integer_scan_numbers(
    tmp_path: Path,
    scan_number: object,
) -> None:
    index = (
        MS1Scan(
            scan_number=scan_number,  # type: ignore[arg-type]
            rt=1.0,
            masses=np.asarray([100.0]),
            intensities=np.asarray([10.0]),
        ),
    )

    with pytest.raises(ValueError, match="scan_number must be an integer"):
        write_ms1_scan_index_npz(tmp_path / "invalid-scan.npz", index)


@pytest.mark.parametrize(
    "field_values",
    [
        {"scan_number": np.asarray([1.5]), "offsets": np.asarray([0, 1])},
        {"scan_number": np.asarray([True]), "offsets": np.asarray([0, 1])},
        {"scan_number": np.asarray([1]), "offsets": np.asarray([0.0, 1.5])},
        {"scan_number": np.asarray([1]), "offsets": np.asarray([False, True])},
    ],
)
def test_ms1_scan_index_reader_rejects_non_integer_structure_arrays(
    tmp_path: Path,
    field_values: dict[str, np.ndarray],
) -> None:
    path = tmp_path / "invalid-integers.npz"
    np.savez(
        path,
        schema_version=np.asarray(["ms1_scan_index_npz_v1"]),
        scan_number=field_values["scan_number"],
        rt=np.asarray([1.0]),
        offsets=field_values["offsets"],
        masses=np.asarray([100.0]),
        intensities=np.asarray([10.0]),
    )

    with pytest.raises(ValueError, match="integer array"):
        read_ms1_scan_index_npz(path)


@pytest.mark.parametrize("rt_values", [np.asarray([True]), np.asarray(["1.0"])])
def test_ms1_scan_index_reader_rejects_non_real_rt_arrays(
    tmp_path: Path,
    rt_values: np.ndarray,
) -> None:
    path = tmp_path / "invalid-rt.npz"
    np.savez(
        path,
        schema_version=np.asarray(["ms1_scan_index_npz_v1"]),
        scan_number=np.asarray([1]),
        rt=rt_values,
        offsets=np.asarray([0, 1]),
        masses=np.asarray([100.0]),
        intensities=np.asarray([10.0]),
    )

    with pytest.raises(ValueError, match="rt must be a real numeric array"):
        read_ms1_scan_index_npz(path)


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
