from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

import numpy as np
from numpy.typing import NDArray

from xic_extractor.validation_primitives import require_finite_real, require_integer

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
    raw: Any,
    *,
    first_scan_number: int | None = None,
    last_scan_number: int | None = None,
    centroid: bool = False,
) -> tuple[MS1Scan, ...]:
    """Read MS1 scans; feature detection opts into vendor-centroided ions.

    The default retains stored scan samples for existing trace consumers.
    Profile samples must not be interpreted as independent mass peaks.
    ``raw`` is the vendor RAW file object owned by ``RawFileHandle``.
    """
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


def _validate_real_array(values: np.ndarray, *, name: str) -> None:
    if not (
        np.issubdtype(values.dtype, np.integer)
        or np.issubdtype(values.dtype, np.floating)
    ):
        raise ValueError(f"MS1 index cache {name} must be a real numeric array")
