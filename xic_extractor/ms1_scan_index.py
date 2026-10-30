from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

import numpy as np
from numpy.typing import NDArray

from xic_extractor.validation_primitives import require_finite_real, require_integer
from xic_extractor.xic_models import XICRequest, XICTrace

MS1IndexIntensityMode = Literal["max", "sum"]
MS1_INDEX_CACHE_SCHEMA_VERSION = "ms1_scan_index_npz"


@dataclass(frozen=True)
class MS1Scan:
    scan_number: int
    rt: float
    masses: NDArray[np.float64]
    intensities: NDArray[np.float64]


class MS1ScanReadError(RuntimeError):
    """A RAW scan could not be read into an auditable MS1 index."""


def build_ms1_scan_index(
    source: Any,
    *,
    first_scan_number: int | None = None,
    last_scan_number: int | None = None,
    centroid: bool = False,
) -> tuple[MS1Scan, ...]:
    """Read MS1 scans; feature detection opts into vendor-centroided ions.

    The default retains stored scan samples for existing trace consumers.
    Profile samples must not be interpreted as independent mass peaks.
    """
    raw = raw_file(source)
    if first_scan_number is None:
        first_scan = int(raw.RunHeaderEx.FirstSpectrum)
    else:
        first_scan = require_integer(
            first_scan_number,
            name="first_scan_number",
        )
    if last_scan_number is None:
        last_scan = int(raw.RunHeaderEx.LastSpectrum)
    else:
        last_scan = require_integer(
            last_scan_number,
            name="last_scan_number",
        )
    scans: list[MS1Scan] = []
    for scan_number in range(first_scan, last_scan + 1):
        try:
            filter_obj = raw.GetFilterForScanNumber(scan_number)
            if str(getattr(filter_obj, "MSOrder", "")) != "Ms":
                continue
            masses, intensities = _scan_arrays(raw, scan_number, centroid=centroid)
            rt = float(raw.RetentionTimeFromScanNumber(scan_number))
        except Exception as exc:
            raise MS1ScanReadError(
                f"failed to read MS1 scan {scan_number}: {exc}"
            ) from exc
        scan = MS1Scan(
            scan_number=scan_number,
            rt=rt,
            masses=masses,
            intensities=intensities,
        )
        try:
            _validate_ms1_scan(scan, previous=scans[-1] if scans else None)
        except ValueError as exc:
            raise MS1ScanReadError(
                f"failed to read MS1 scan {scan_number}: {exc}"
            ) from exc
        scans.append(scan)
    result = tuple(scans)
    try:
        validate_ms1_scan_index(result, require_nonempty=True)
    except ValueError as exc:
        raise MS1ScanReadError(f"failed to build MS1 scan index: {exc}") from exc
    return result


def build_ms1_scan_index_for_rt_window(
    source: Any,
    *,
    rt_min: float,
    rt_max: float,
) -> tuple[MS1Scan, ...]:
    """Build an MS1 index for a bounded RT window without hiding scan errors."""

    parsed_rt_min = require_finite_real(rt_min, name="rt_min")
    parsed_rt_max = require_finite_real(rt_max, name="rt_max")
    if parsed_rt_min > parsed_rt_max:
        raise ValueError("rt_min must be <= rt_max")
    raw = raw_file(source)
    try:
        first_scan = int(raw.ScanNumberFromRetentionTime(rt_min))
        last_scan = int(raw.ScanNumberFromRetentionTime(rt_max))
    except Exception as exc:
        raise MS1ScanReadError(
            f"failed to resolve MS1 scan window for RT {rt_min:g}-{rt_max:g}: {exc}"
        ) from exc
    return build_ms1_scan_index(
        source,
        first_scan_number=first_scan,
        last_scan_number=last_scan,
    )


def write_ms1_scan_index_npz(
    path: Path,
    index: tuple[MS1Scan, ...],
) -> Path:
    validate_ms1_scan_index(index)
    path.parent.mkdir(parents=True, exist_ok=True)
    offsets: NDArray[np.int64] = np.zeros(len(index) + 1, dtype=np.int64)
    scan_numbers: NDArray[np.int64] = np.empty(len(index), dtype=np.int64)
    rts: NDArray[np.float64] = np.empty(len(index), dtype=np.float64)
    mass_arrays: list[NDArray[np.float64]] = []
    intensity_arrays: list[NDArray[np.float64]] = []
    position = 0
    for scan_index, scan in enumerate(index):
        scan_numbers[scan_index] = scan.scan_number
        rts[scan_index] = scan.rt
        masses = np.asarray(scan.masses, dtype=np.float64)
        intensities = np.asarray(scan.intensities, dtype=np.float64)
        mass_arrays.append(masses)
        intensity_arrays.append(intensities)
        position += int(masses.size)
        offsets[scan_index + 1] = position
    masses_concat = (
        np.concatenate(mass_arrays) if mass_arrays else np.asarray([], dtype=np.float64)
    )
    intensities_concat = (
        np.concatenate(intensity_arrays)
        if intensity_arrays
        else np.asarray([], dtype=np.float64)
    )
    np.savez(
        path,
        schema_version=np.asarray([MS1_INDEX_CACHE_SCHEMA_VERSION]),
        scan_number=scan_numbers,
        rt=rts,
        offsets=offsets,
        masses=masses_concat,
        intensities=intensities_concat,
    )
    return path


def read_ms1_scan_index_npz(path: Path) -> tuple[MS1Scan, ...]:
    try:
        with np.load(path, allow_pickle=False) as payload:
            schema_values = np.asarray(payload["schema_version"])
            if schema_values.shape != (1,):
                raise ValueError("MS1 index cache schema_version is malformed")
            schema_version = str(schema_values[0])
            scan_number_values = np.asarray(payload["scan_number"])
            rt_values = np.asarray(payload["rt"])
            offset_values = np.asarray(payload["offsets"])
            mass_values = np.asarray(payload["masses"])
            intensity_values = np.asarray(payload["intensities"])
    except KeyError as exc:
        missing = str(exc).split(" is not a file", maxsplit=1)[0].strip("'\"")
        raise ValueError(
            f"MS1 index cache is missing required field {missing!r}"
        ) from exc
    if schema_version != MS1_INDEX_CACHE_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported MS1 index cache schema version: {schema_version}",
        )
    _validate_integer_array(scan_number_values, name="scan_number")
    _validate_integer_array(offset_values, name="offsets")
    _validate_real_array(rt_values, name="rt")
    _validate_real_array(mass_values, name="masses")
    _validate_real_array(intensity_values, name="intensities")
    scan_numbers = scan_number_values.astype(np.int64, copy=False)
    offsets = offset_values.astype(np.int64, copy=False)
    rts = rt_values.astype(np.float64, copy=False)
    masses = mass_values.astype(np.float64, copy=False)
    intensities = intensity_values.astype(np.float64, copy=False)
    if scan_numbers.ndim != 1 or rts.ndim != 1 or scan_numbers.shape != rts.shape:
        raise ValueError("MS1 index cache scan metadata is malformed")
    if offsets.ndim != 1 or len(offsets) != len(scan_numbers) + 1:
        raise ValueError("MS1 index cache offsets are malformed")
    if masses.ndim != 1 or intensities.ndim != 1 or masses.shape != intensities.shape:
        raise ValueError("MS1 index cache intensity arrays are malformed")
    if len(offsets) and (
        offsets[0] != 0 or offsets[-1] != len(masses) or np.any(np.diff(offsets) < 0)
    ):
        raise ValueError("MS1 index cache offsets do not match payload length")
    scans: list[MS1Scan] = []
    for index, scan_number in enumerate(scan_numbers):
        start = int(offsets[index])
        end = int(offsets[index + 1])
        scans.append(
            MS1Scan(
                scan_number=int(scan_number),
                rt=float(rts[index]),
                masses=masses[start:end],
                intensities=intensities[start:end],
            ),
        )
    result = tuple(scans)
    validate_ms1_scan_index(result)
    return result


def extract_index_xic(
    source: Any,
    index: tuple[MS1Scan, ...],
    request: XICRequest,
    *,
    intensity_mode: MS1IndexIntensityMode = "max",
) -> XICTrace:
    start_scan, end_scan = _scan_window(source, request)
    tolerance = request.mz * request.ppm_tol / 1e6
    rt_values: list[float] = []
    intensity_values: list[float] = []
    for scan in index:
        if scan.scan_number < start_scan or scan.scan_number > end_scan:
            continue
        left = int(np.searchsorted(scan.masses, request.mz - tolerance, side="left"))
        right = int(np.searchsorted(scan.masses, request.mz + tolerance, side="right"))
        intensity = _window_intensity(
            scan.intensities[left:right],
            intensity_mode=intensity_mode,
        )
        rt_values.append(scan.rt)
        intensity_values.append(intensity)
    return XICTrace.from_arrays(rt_values, intensity_values)


def raw_file(source: Any) -> Any:
    return getattr(source, "_raw_file", source)


def _scan_arrays(
    raw_file: Any,
    scan_number: int,
    *,
    centroid: bool,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    if centroid:
        scan = raw_file.GetCentroidStream(scan_number, False)
        masses = np.asarray(scan.Masses, dtype=np.float64)
    elif hasattr(raw_file, "GetSegmentedScanFromScanNumber"):
        scan = raw_file.GetSegmentedScanFromScanNumber(scan_number, None)
        masses = np.asarray(getattr(scan, "Positions", []), dtype=np.float64)
    else:
        scan = raw_file.GetSimplifiedScan(scan_number)
        masses = np.asarray(getattr(scan, "Masses", []), dtype=np.float64)
    intensities = np.asarray(getattr(scan, "Intensities", []), dtype=np.float64)
    if masses.ndim != 1 or intensities.ndim != 1 or masses.shape != intensities.shape:
        return masses, intensities
    order = np.argsort(masses)
    return masses[order], intensities[order]


def validate_ms1_scan_index(
    index: Sequence[MS1Scan],
    *,
    require_nonempty: bool = False,
) -> None:
    """Validate ordering and numeric invariants for one reusable MS1 index."""

    if require_nonempty and not index:
        raise ValueError("MS1 scan index must contain at least one MS1 scan")
    previous: MS1Scan | None = None
    for scan in index:
        _validate_ms1_scan(scan, previous=previous)
        previous = scan


def _validate_ms1_scan(scan: MS1Scan, *, previous: MS1Scan | None) -> None:
    scan_number = require_integer(scan.scan_number, name="scan_number")
    if scan_number <= 0:
        raise ValueError("scan_number must be positive")
    require_finite_real(scan.rt, name="rt")
    raw_masses = np.asarray(scan.masses)
    raw_intensities = np.asarray(scan.intensities)
    _validate_real_array(raw_masses, name="masses")
    _validate_real_array(raw_intensities, name="intensities")
    masses = raw_masses.astype(np.float64, copy=False)
    intensities = raw_intensities.astype(np.float64, copy=False)
    if masses.ndim != 1 or intensities.ndim != 1 or masses.shape != intensities.shape:
        raise ValueError("masses/intensities must be matching 1D arrays")
    if not np.all(np.isfinite(masses)):
        raise ValueError("masses must be finite")
    if not np.all(np.isfinite(intensities)):
        raise ValueError("intensities must be finite")
    if masses.size > 1 and np.any(np.diff(masses) < 0):
        raise ValueError("masses must be sorted in nondecreasing order")
    if previous is not None:
        if scan.scan_number <= previous.scan_number:
            raise ValueError("scan_number must be strictly increasing")
        if scan.rt < previous.rt:
            raise ValueError("rt must be nondecreasing")


def _validate_integer_array(values: np.ndarray, *, name: str) -> None:
    if not np.issubdtype(values.dtype, np.integer):
        raise ValueError(f"MS1 index cache {name} must be an integer array")
    if values.size and np.any(values > np.iinfo(np.int64).max):
        raise ValueError(f"MS1 index cache {name} exceeds int64 range")


def _validate_real_array(values: np.ndarray, *, name: str) -> None:
    if not (
        np.issubdtype(values.dtype, np.integer)
        or np.issubdtype(values.dtype, np.floating)
    ):
        raise ValueError(f"MS1 index cache {name} must be a real numeric array")


def _scan_window(source: Any, request: XICRequest) -> tuple[int, int]:
    resolver = getattr(source, "scan_window_for_request", None)
    if callable(resolver):
        start_scan, end_scan = resolver(request)
        return int(start_scan), int(end_scan)
    raw = raw_file(source)
    return (
        int(raw.ScanNumberFromRetentionTime(request.rt_min)),
        int(raw.ScanNumberFromRetentionTime(request.rt_max)),
    )


def _window_intensity(
    intensities: NDArray[np.float64],
    *,
    intensity_mode: MS1IndexIntensityMode,
) -> float:
    if len(intensities) == 0:
        return 0.0
    if intensity_mode == "max":
        return float(np.max(intensities))
    if intensity_mode == "sum":
        return float(np.sum(intensities))
    raise ValueError(f"unsupported MS1 index intensity mode: {intensity_mode}")
