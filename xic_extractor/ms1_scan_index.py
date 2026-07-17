from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from xic_extractor.xic_models import XICRequest, XICTrace

MS1IndexIntensityMode = Literal["max", "sum"]
MS1_INDEX_CACHE_SCHEMA_VERSION = "ms1_scan_index_npz_v1"


@dataclass(frozen=True)
class MS1Scan:
    scan_number: int
    rt: float
    masses: NDArray[np.float64]
    intensities: NDArray[np.float64]


@dataclass(frozen=True)
class MS1ScanIndexProvenance:
    """Identity of the sample and source artifact used to build an NPZ index."""

    sample_stem: str
    source_path: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.sample_stem.strip():
            raise ValueError("MS1 index provenance sample_stem must be non-empty")
        if not self.source_path.strip():
            raise ValueError("MS1 index provenance source_path must be non-empty")
        digest = self.source_sha256.strip()
        if len(digest) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in digest
        ):
            raise ValueError(
                "MS1 index provenance source_sha256 must be a sha256 hex digest"
            )


class MS1ScanReadError(RuntimeError):
    """A RAW scan could not be read into an auditable MS1 index."""


def build_ms1_scan_index(
    source: Any,
    *,
    first_scan_number: int | None = None,
    last_scan_number: int | None = None,
    fail_on_malformed: bool = False,
) -> tuple[MS1Scan, ...]:
    raw_file = _raw_file(source)
    if first_scan_number is None:
        first_scan = int(raw_file.RunHeaderEx.FirstSpectrum)
    else:
        first_scan = int(first_scan_number)
    if last_scan_number is None:
        last_scan = int(raw_file.RunHeaderEx.LastSpectrum)
    else:
        last_scan = int(last_scan_number)
    scans: list[MS1Scan] = []
    for scan_number in range(first_scan, last_scan + 1):
        try:
            filter_obj = raw_file.GetFilterForScanNumber(scan_number)
            if str(getattr(filter_obj, "MSOrder", "")) != "Ms":
                continue
            masses, intensities = _scan_arrays(raw_file, scan_number)
            rt = float(raw_file.RetentionTimeFromScanNumber(scan_number))
        except Exception as exc:
            raise MS1ScanReadError(
                f"failed to read MS1 scan {scan_number}: {exc}"
            ) from exc
        if (
            masses.ndim != 1
            or intensities.ndim != 1
            or masses.shape != intensities.shape
        ):
            if fail_on_malformed:
                raise MS1ScanReadError(
                    f"failed to read MS1 scan {scan_number}: "
                    "masses/intensities must be matching 1D arrays"
                )
            continue
        scans.append(
            MS1Scan(
                scan_number=scan_number,
                rt=rt,
                masses=masses,
                intensities=intensities,
            )
        )
    return tuple(scans)


def build_ms1_scan_index_for_rt_window(
    source: Any,
    *,
    rt_min: float,
    rt_max: float,
    fail_on_malformed: bool = False,
) -> tuple[MS1Scan, ...]:
    """Build an MS1 index for a bounded RT window without hiding scan errors."""

    if rt_min > rt_max:
        raise ValueError("rt_min must be <= rt_max")
    raw_file = _raw_file(source)
    try:
        first_scan = int(raw_file.ScanNumberFromRetentionTime(rt_min))
        last_scan = int(raw_file.ScanNumberFromRetentionTime(rt_max))
    except Exception as exc:
        raise MS1ScanReadError(
            f"failed to resolve MS1 scan window for RT {rt_min:g}-{rt_max:g}: {exc}"
        ) from exc
    return build_ms1_scan_index(
        source,
        first_scan_number=first_scan,
        last_scan_number=last_scan,
        fail_on_malformed=fail_on_malformed,
    )


def write_ms1_scan_index_npz(
    path: Path,
    index: tuple[MS1Scan, ...],
    *,
    provenance: MS1ScanIndexProvenance | None = None,
) -> Path:
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
        if (
            masses.ndim != 1
            or intensities.ndim != 1
            or masses.shape != intensities.shape
        ):
            raise ValueError("MS1 scan cache arrays must be matching 1D arrays")
        mass_arrays.append(masses)
        intensity_arrays.append(intensities)
        position += int(masses.size)
        offsets[scan_index + 1] = position
    masses_concat = (
        np.concatenate(mass_arrays)
        if mass_arrays
        else np.asarray([], dtype=np.float64)
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
        provenance_sample_stem=np.asarray(
            [provenance.sample_stem if provenance is not None else ""]
        ),
        provenance_source_path=np.asarray(
            [provenance.source_path if provenance is not None else ""]
        ),
        provenance_source_sha256=np.asarray(
            [provenance.source_sha256 if provenance is not None else ""]
        ),
    )
    return path


def read_ms1_scan_index_npz(path: Path) -> tuple[MS1Scan, ...]:
    with np.load(path, allow_pickle=False) as payload:
        schema_version = str(payload["schema_version"][0])
        if schema_version != MS1_INDEX_CACHE_SCHEMA_VERSION:
            raise ValueError(
                "unsupported MS1 index cache schema version: "
                f"{schema_version}",
            )
        scan_numbers = np.asarray(payload["scan_number"], dtype=np.int64)
        rts = np.asarray(payload["rt"], dtype=np.float64)
        offsets = np.asarray(payload["offsets"], dtype=np.int64)
        masses = np.asarray(payload["masses"], dtype=np.float64)
        intensities = np.asarray(payload["intensities"], dtype=np.float64)
    if scan_numbers.ndim != 1 or rts.ndim != 1 or scan_numbers.shape != rts.shape:
        raise ValueError("MS1 index cache scan metadata is malformed")
    if offsets.ndim != 1 or len(offsets) != len(scan_numbers) + 1:
        raise ValueError("MS1 index cache offsets are malformed")
    if masses.ndim != 1 or intensities.ndim != 1 or masses.shape != intensities.shape:
        raise ValueError("MS1 index cache intensity arrays are malformed")
    if len(offsets) and (offsets[0] != 0 or offsets[-1] != len(masses)):
        raise ValueError("MS1 index cache offsets do not match payload length")
    scans: list[MS1Scan] = []
    for index, scan_number in enumerate(scan_numbers):
        start = int(offsets[index])
        end = int(offsets[index + 1])
        if start > end:
            raise ValueError("MS1 index cache offsets are not monotonic")
        scans.append(
            MS1Scan(
                scan_number=int(scan_number),
                rt=float(rts[index]),
                masses=masses[start:end],
                intensities=intensities[start:end],
            ),
        )
    return tuple(scans)


def read_ms1_scan_index_provenance(path: Path) -> MS1ScanIndexProvenance | None:
    """Read optional source binding from an MS1 index cache.

    Legacy caches without these fields remain readable by the generic index
    reader, but callers producing source-attributed evidence must reject a
    missing result rather than infer identity from the filename.
    """

    fields = (
        "provenance_sample_stem",
        "provenance_source_path",
        "provenance_source_sha256",
    )
    with np.load(path, allow_pickle=False) as payload:
        present = tuple(field in payload.files for field in fields)
        if not any(present):
            return None
        if not all(present):
            raise ValueError("MS1 index cache source provenance is incomplete")
        if any(payload[field].shape != (1,) for field in fields):
            raise ValueError("MS1 index cache source provenance is incomplete")
        values = tuple(str(payload[field][0]).strip() for field in fields)
    if not any(values):
        return None
    if not all(values):
        raise ValueError("MS1 index cache source provenance is incomplete")
    return MS1ScanIndexProvenance(
        sample_stem=values[0],
        source_path=values[1],
        source_sha256=values[2],
    )


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


def _raw_file(source: Any) -> Any:
    return getattr(source, "_raw_file", source)


def _scan_arrays(
    raw_file: Any,
    scan_number: int,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    if hasattr(raw_file, "GetSegmentedScanFromScanNumber"):
        scan = raw_file.GetSegmentedScanFromScanNumber(scan_number, None)
        masses = np.asarray(getattr(scan, "Positions", []), dtype=np.float64)
    else:
        scan = raw_file.GetSimplifiedScan(scan_number)
        masses = np.asarray(getattr(scan, "Masses", []), dtype=np.float64)
    intensities = np.asarray(getattr(scan, "Intensities", []), dtype=np.float64)
    if (
        masses.ndim != 1
        or intensities.ndim != 1
        or masses.shape != intensities.shape
    ):
        return masses, intensities
    order = np.argsort(masses)
    return masses[order], intensities[order]


def _scan_window(source: Any, request: XICRequest) -> tuple[int, int]:
    resolver = getattr(source, "scan_window_for_request", None)
    if callable(resolver):
        start_scan, end_scan = resolver(request)
        return int(start_scan), int(end_scan)
    raw_file = _raw_file(source)
    return (
        int(raw_file.ScanNumberFromRetentionTime(request.rt_min)),
        int(raw_file.ScanNumberFromRetentionTime(request.rt_max)),
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
