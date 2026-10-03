import importlib
import math
import os
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from typing import Any, final

import numpy as np

from xic_extractor.acquisition_contract import (
    RawAcquisitionSessionCapability,
    RawAcquisitionSessionReceipt,
    _mint_raw_acquisition_session_receipt,
)
from xic_extractor.analysis_set import (
    AnalysisSet,
    discover_raw_files,
    select_analysis_set,
)
from xic_extractor.injection_timeline import (
    InjectionHeader,
    PerturbationPolicy,
    ScheduledInjection,
    build_injection_timeline,
)
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata, Ms2Scan, Ms2ScanEvent
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    IdentityContractError,
    fingerprint_file_content,
)
from xic_extractor.sample_metadata import (
    SampleMetadata,
    SampleRole,
    infer_sample_role,
)
from xic_extractor.xic_models import XICRequest, XICTrace

EXPECTED_THERMO_DLLS = (
    "ThermoFisher.CommonCore.Data.dll",
    "ThermoFisher.CommonCore.RawFileReader.dll",
)
THERMO_RAW_ACQUISITION_FORMAT = "thermo_raw"
THERMO_RAW_OBSERVATION_NAMESPACE = "thermo_raw_file_reader:Device.MS:1"

_ASSEMBLIES_LOADED = False


class RawReaderError(Exception):
    """Raised when Thermo raw reader setup or file opening fails."""


class RawReaderOpenError(RawReaderError):
    """Raised after a vendor open attempt with explicit cleanup truth."""

    def __init__(self, message: str, *, cleanup_failed: bool) -> None:
        super().__init__(message)
        if not isinstance(cleanup_failed, bool):
            raise TypeError("cleanup_failed must be a bool")
        self.cleanup_failed = cleanup_failed


@dataclass(frozen=True)
class _ThermoApi:
    raw_file_reader_adapter: Any
    device_ms: Any
    mass_range_factory: Callable[[float, float], Any]
    trace_settings_factory: Callable[..., Any]
    mass_range_trace_type: Any
    ms2_order: Any


@dataclass(frozen=True)
class _VerifiedAcquisitionSourceBinding:
    source: AcquisitionSourceIdentity
    path_identity: tuple[int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class RawSampleHeader:
    raw_path: str
    raw_stem: str
    sample_name: str
    sample_id: str
    row_number: int | None
    sample_type: str
    creation_date: datetime | None
    vial: str
    comment: str


def _to_python_datetime(dt: Any) -> datetime | None:
    if dt is None:
        return None
    try:
        return datetime(
            int(dt.Year),
            int(dt.Month),
            int(dt.Day),
            int(dt.Hour),
            int(dt.Minute),
            int(dt.Second),
            int(getattr(dt, "Millisecond", 0)) * 1000,
        )
    except (AttributeError, TypeError, ValueError):
        return None


class RawFileHandle:
    __slots__ = ("_raw_file", "_thermo", "_raw_chromatogram_call_count")

    def __init__(self, raw_file: Any, thermo: _ThermoApi) -> None:
        self._raw_file = raw_file
        self._thermo = thermo
        self._raw_chromatogram_call_count = 0

    def __enter__(self) -> "RawFileHandle":
        return self

    def __exit__(self, *_args: object) -> None:
        self._raw_file.Dispose()

    @property
    def raw_chromatogram_call_count(self) -> int:
        return self._raw_chromatogram_call_count

    @property
    def sample_header(self) -> RawSampleHeader:
        raw = self._raw_file
        info = getattr(raw, "SampleInformation", None)
        sample_name = str(getattr(info, "SampleName", "") or "").strip()
        sample_id = str(getattr(info, "SampleId", "") or "").strip()
        row_number_raw = getattr(info, "RowNumber", None)
        row_number = None
        if row_number_raw is not None:
            try:
                row_val = int(row_number_raw)
                if row_val > 0:
                    row_number = row_val
            except (TypeError, ValueError):
                pass
        sample_type = str(getattr(info, "SampleType", "") or "Unknown").strip()
        vial = str(getattr(info, "Vial", "") or "").strip()
        comment = str(getattr(info, "Comment", "") or "").strip()
        creation_date = _to_python_datetime(getattr(raw, "CreationDate", None))
        file_name = str(getattr(raw, "FileName", "") or "").strip()
        raw_stem = Path(file_name).stem if file_name else ""
        return RawSampleHeader(
            raw_path=file_name,
            raw_stem=raw_stem,
            sample_name=sample_name or raw_stem,
            sample_id=sample_id,
            row_number=row_number,
            sample_type=sample_type,
            creation_date=creation_date,
            vial=vial,
            comment=comment,
        )

    @property
    def acquisition_source(self) -> AcquisitionSourceIdentity | None:
        return None

    @property
    def raw_open_receipt(self) -> RawAcquisitionSessionReceipt | None:
        return None

    def extract_xic(
        self, mz: float, rt_min: float, rt_max: float, ppm_tol: float
    ) -> tuple[np.ndarray, np.ndarray]:
        trace = self.extract_xic_many(
            (XICRequest(mz=mz, rt_min=rt_min, rt_max=rt_max, ppm_tol=ppm_tol),)
        )[0]
        return trace.rt, trace.intensity

    def extract_xic_many(
        self,
        requests: Sequence[XICRequest],
    ) -> tuple[XICTrace, ...]:
        if not requests:
            return ()
        grouped: dict[tuple[int, int], list[tuple[int, Any]]] = defaultdict(list)
        for index, request in enumerate(requests):
            start_scan, end_scan = self.scan_window_for_request(request)
            settings = self._build_chromatogram_settings(request.mz, request.ppm_tol)
            grouped[(start_scan, end_scan)].append((index, settings))

        traces: list[XICTrace | None] = [None] * len(requests)
        for (start_scan, end_scan), indexed_settings in grouped.items():
            settings = [item[1] for item in indexed_settings]
            self._raw_chromatogram_call_count += 1
            data = self._raw_file.GetChromatogramData(settings, start_scan, end_scan)
            for offset, (original_index, _settings) in enumerate(indexed_settings):
                positions = _item_or_empty(data.PositionsArray, offset)
                intensities = _item_or_empty(data.IntensitiesArray, offset)
                if len(intensities) == 0:
                    traces[original_index] = XICTrace.empty()
                else:
                    traces[original_index] = XICTrace.from_arrays(
                        positions,
                        intensities,
                    )
        if any(trace is None for trace in traces):
            raise RawReaderError(
                "Thermo RAW batch extraction returned incomplete traces",
            )
        return tuple(trace for trace in traces if trace is not None)

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        return (
            int(self._raw_file.ScanNumberFromRetentionTime(request.rt_min)),
            int(self._raw_file.ScanNumberFromRetentionTime(request.rt_max)),
        )

    def retention_time_for_scan(self, scan_number: int) -> float:
        return float(self._raw_file.RetentionTimeFromScanNumber(scan_number))

    def ms1_centroid_mz(self, rt: float, mz_min: float, mz_max: float) -> float | None:
        """Most intense MS1 centroid within [mz_min, mz_max] at the scan at ``rt``."""
        raw = self._raw_file
        scan_number = int(raw.ScanNumberFromRetentionTime(rt))
        if (
            getattr(raw.GetFilterForScanNumber(scan_number), "MSOrder", None)
            == self._thermo.ms2_order
        ):
            return None
        centroids = raw.GetCentroidStream(scan_number, False)
        masses = np.asarray(getattr(centroids, "Masses", None) or [], dtype=float)
        intensities = np.asarray(
            getattr(centroids, "Intensities", None) or [], dtype=float
        )
        inside = (masses >= mz_min) & (masses <= mz_max)
        if not inside.any():
            return None
        return float(masses[inside][np.argmax(intensities[inside])])

    def iter_ms2_scans(self, rt_min: float, rt_max: float) -> Iterator[Ms2ScanEvent]:
        yield from self._iter_ms2_scans(rt_min, rt_max, scan_numbers=None)

    def iter_ms2_scan_subset(
        self, rt_min: float, rt_max: float, scan_numbers: frozenset[int]
    ) -> Iterator[Ms2ScanEvent]:
        """Read declared scans within the same native RT-to-scan window."""
        yield from self._iter_ms2_scans(rt_min, rt_max, scan_numbers=scan_numbers)

    def _iter_ms2_scans(
        self, rt_min: float, rt_max: float, *, scan_numbers: frozenset[int] | None
    ) -> Iterator[Ms2ScanEvent]:
        start_scan = self._raw_file.ScanNumberFromRetentionTime(rt_min)
        end_scan = self._raw_file.ScanNumberFromRetentionTime(rt_max)
        selected = (
            range(start_scan, end_scan + 1)
            if scan_numbers is None
            else sorted(
                number for number in scan_numbers if start_scan <= number <= end_scan
            )
        )
        for scan_number in selected:
            try:
                filter_obj = self._raw_file.GetFilterForScanNumber(scan_number)
                if getattr(filter_obj, "MSOrder", None) != self._thermo.ms2_order:
                    continue
                precursor_mz = _extract_precursor_mz(filter_obj)
                scan_rt = float(self._raw_file.RetentionTimeFromScanNumber(scan_number))
                scan_data = self._raw_file.GetSimplifiedScan(scan_number)
                masses = np.asarray(getattr(scan_data, "Masses", []), dtype=float)
                intensities = np.asarray(
                    getattr(scan_data, "Intensities", []), dtype=float
                )
                base_peak = float(np.max(intensities)) if len(intensities) else 0.0
                yield Ms2ScanEvent(
                    scan=Ms2Scan(
                        scan_number=scan_number,
                        rt=scan_rt,
                        precursor_mz=precursor_mz,
                        masses=masses,
                        intensities=intensities,
                        base_peak=base_peak,
                        precursor_metadata=_read_precursor_metadata(
                            self._raw_file, filter_obj, scan_number
                        ),
                    ),
                    parse_error=None,
                    scan_number=scan_number,
                )
            except Exception as exc:
                yield Ms2ScanEvent(
                    scan=None,
                    parse_error=str(exc),
                    scan_number=scan_number,
                )

    def _build_chromatogram_settings(self, mz: float, ppm_tol: float) -> Any:
        tolerance = mz * ppm_tol / 1e6
        settings = self._create_mass_range_trace_settings()
        settings.MassRanges = [
            self._thermo.mass_range_factory(mz - tolerance, mz + tolerance)
        ]
        settings.Filter = "ms"
        if hasattr(settings, "TraceType"):
            settings.TraceType = self._thermo.mass_range_trace_type
        return settings

    def _create_mass_range_trace_settings(self) -> Any:
        try:
            return self._thermo.trace_settings_factory(
                self._thermo.mass_range_trace_type
            )
        except TypeError:
            return self._thermo.trace_settings_factory()


def _owner_bound_raw_handle_support() -> tuple[
    Callable[
        [
            Any,
            _ThermoApi,
            AcquisitionSourceIdentity,
            RawAcquisitionSessionReceipt | None,
        ],
        RawFileHandle,
    ],
    Callable[[object, AcquisitionSourceIdentity], RawFileHandle],
]:
    mint_token = object()

    @final
    class OwnerBoundRawFileHandle(RawFileHandle):
        __slots__ = ("_bound_acquisition_source", "_raw_open_receipt")

        def __init__(
            self,
            raw_file: Any,
            thermo: _ThermoApi,
            source: AcquisitionSourceIdentity,
            raw_open_receipt: RawAcquisitionSessionReceipt | None,
            *,
            _mint_token: object,
        ) -> None:
            if _mint_token is not mint_token:
                raise RawReaderError(
                    "owner-bound RAW handles must be minted by open_raw"
                )
            super().__init__(raw_file, thermo)
            self._bound_acquisition_source = source
            self._raw_open_receipt = raw_open_receipt

        def __setattr__(self, name: str, value: object) -> None:
            if name in {
                "_raw_file",
                "_thermo",
                "_bound_acquisition_source",
                "_raw_open_receipt",
            } and hasattr(self, name):
                raise AttributeError(f"{name} is sealed on owner-bound RAW handles")
            super().__setattr__(name, value)

        def __delattr__(self, name: str) -> None:
            if name in {
                "_raw_file",
                "_thermo",
                "_bound_acquisition_source",
                "_raw_open_receipt",
            }:
                raise AttributeError(f"{name} is sealed on owner-bound RAW handles")
            super().__delattr__(name)

        def __init_subclass__(cls, **kwargs: object) -> None:
            raise TypeError("owner-bound RAW handles are final")

        @property
        def acquisition_source(self) -> AcquisitionSourceIdentity:
            return self._bound_acquisition_source

        @property
        def raw_open_receipt(self) -> RawAcquisitionSessionReceipt | None:
            return self._raw_open_receipt

    def mint(
        raw_file: Any,
        thermo: _ThermoApi,
        source: AcquisitionSourceIdentity,
        raw_open_receipt: RawAcquisitionSessionReceipt | None,
    ) -> RawFileHandle:
        return OwnerBoundRawFileHandle(
            raw_file,
            thermo,
            source,
            raw_open_receipt,
            _mint_token=mint_token,
        )

    def require(
        raw: object,
        expected: AcquisitionSourceIdentity,
    ) -> RawFileHandle:
        if not isinstance(raw, OwnerBoundRawFileHandle):
            raise RawReaderError("RAW handle was not owner-bound by open_raw")
        if raw.acquisition_source != expected:
            raise RawReaderError(
                "RAW handle is bound to a different acquisition source"
            )
        return raw

    return mint, require


_mint_owner_bound_raw_handle, require_owner_bound_raw_handle = (
    _owner_bound_raw_handle_support()
)


def open_raw(
    path: Path,
    dll_dir: Path,
    *,
    expected_acquisition_source: AcquisitionSourceIdentity | None = None,
    _expected_path_identity: tuple[int, int, int, int, int] | None = None,
    _raw_session_capability: RawAcquisitionSessionCapability | None = None,
    _import_module: Callable[[str], Any] = importlib.import_module,
    _thermo: _ThermoApi | None = None,
) -> RawFileHandle:
    _validate_raw_session_capability(
        _raw_session_capability,
        expected_acquisition_source=expected_acquisition_source,
    )
    errors = preflight_raw_reader(dll_dir, import_module_func=_import_module)
    if errors:
        raise RawReaderError(" ".join(errors))

    resolved_dll_dir = dll_dir.resolve()
    try:
        _ensure_assemblies_loaded(resolved_dll_dir, _import_module)
        thermo = _thermo or _load_thermo_api(_import_module)
    except Exception as exc:
        raise RawReaderError(
            f"Failed to load Thermo DLL or open raw file: {exc}"
        ) from exc
    source_binding = (
        _verify_expected_acquisition_source(path, expected_acquisition_source)
        if _expected_path_identity is None
        else _bind_expected_path_identity(
            path,
            expected_acquisition_source,
            _expected_path_identity,
        )
    )
    raw_file = None
    try:
        raw_file = thermo.raw_file_reader_adapter.FileFactory(str(path))
        raw_file.SelectInstrument(thermo.device_ms, 1)
    except Exception as exc:
        cleanup_error = _dispose_after_failed_open(raw_file)
        message = f"Failed to load Thermo DLL or open raw file: {exc}"
        if cleanup_error is not None:
            message = f"{message}; vendor cleanup failed: {cleanup_error}"
        raise RawReaderOpenError(
            message,
            cleanup_failed=cleanup_error is not None,
        ) from exc
    try:
        _verify_source_path_still_bound(
            path,
            source_binding,
            verify_content=_expected_path_identity is None,
        )
    except RawReaderError as exc:
        cleanup_error = _dispose_after_failed_open(raw_file)
        if cleanup_error is not None:
            raise RawReaderOpenError(
                f"{exc}; vendor cleanup failed: {cleanup_error}",
                cleanup_failed=True,
            ) from exc
        raise RawReaderOpenError(str(exc), cleanup_failed=False) from exc
    if source_binding is None:
        return RawFileHandle(raw_file, thermo)
    raw_open_receipt = (
        None
        if _raw_session_capability is None
        else _mint_raw_acquisition_session_receipt(_raw_session_capability)
    )
    return _mint_owner_bound_raw_handle(
        raw_file,
        thermo,
        source_binding.source,
        raw_open_receipt,
    )


def read_raw_sample_header(path: Path, dll_dir: Path) -> RawSampleHeader:
    with open_raw(path, dll_dir=dll_dir) as handle:
        header = handle.sample_header
        if not header.raw_path:
            header = replace(
                header,
                raw_path=str(path.resolve()),
                raw_stem=path.stem,
                sample_name=header.sample_name or path.stem,
            )
        return header


def read_raw_injection_header(path: Path, dll_dir: Path) -> InjectionHeader:
    """Read chronology facts only, without spectra or content hashing."""
    with open_raw(path, dll_dir=dll_dir) as handle:
        return _injection_header(handle, path)


def _injection_header(handle: RawFileHandle, path: Path) -> InjectionHeader:
    raw = handle._raw_file
    creation = handle.sample_header.creation_date
    if creation is None:
        raise RawReaderError(f"RAW creation date is unavailable: {path}")
    info = raw.SampleInformation
    return InjectionHeader(
        raw_path=str(path.resolve()),
        acquisition_source=str(info.RawFileName or "").strip(),
        creation_date=creation,
        start_time_min=float(raw.RunHeaderEx.StartTime),
        end_time_min=float(raw.RunHeaderEx.EndTime),
        instrument_method=str(info.InstrumentMethodFile or "").strip(),
    )


def read_analysis_set(
    raw_root: Path,
    dll_dir: Path,
    sample_metadata: Sequence[SampleMetadata] | None = None,
) -> AnalysisSet:
    """Resolve the analysis set from every RAW below root, one open per RAW."""
    headers: list[InjectionHeader] = []
    roles: dict[str, SampleRole] = {}
    unread: list[str] = []
    for path in discover_raw_files(raw_root):
        try:
            header, role = _analysis_header(path, dll_dir)
        except (RawReaderError, ValueError):
            key = str(path.resolve())
            unread.append(key)
            roles[key] = infer_sample_role(None, sample_name=path.stem)
            continue
        headers.append(header)
        roles[header.raw_path] = role
    timeline = build_injection_timeline(headers, PerturbationPolicy())
    return select_analysis_set(timeline, roles, sample_metadata, unread_paths=unread)


_ANALYSIS_HEADER_CACHE: dict[
    tuple[str, int, int, str], tuple[InjectionHeader, SampleRole]
] = {}


def _analysis_header(path: Path, dll_dir: Path) -> tuple[InjectionHeader, SampleRole]:
    """Header facts are immutable for an unchanged file; reuse across resolutions."""
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, str(dll_dir))
    cached = _ANALYSIS_HEADER_CACHE.get(key)
    if cached is not None:
        return cached
    with open_raw(path, dll_dir=dll_dir) as handle:
        header = _injection_header(handle, path)
        sample = handle.sample_header
    # Header SampleName is free text; the RAW filename and method name
    # carry the lab's role vocabulary (Blank, SDOLEK, STD, wash).
    role = infer_sample_role(
        sample.sample_type,
        sample_name=path.stem,
        comment=" ".join(
            (
                sample.sample_name,
                PureWindowsPath(header.instrument_method).stem,
                sample.comment,
            )
        ),
    )
    _ANALYSIS_HEADER_CACHE[key] = (header, role)
    return header, role


def read_sld_injections(path: Path, dll_dir: Path) -> tuple[ScheduledInjection, ...]:
    """Read an optional Xcalibur sequence; it supplies no acquisition clock."""
    errors = preflight_raw_reader(dll_dir)
    if errors:
        raise RawReaderError("; ".join(errors))
    _ensure_assemblies_loaded(dll_dir.resolve(), importlib.import_module)
    business = importlib.import_module("ThermoFisher.CommonCore.Data.Business")
    sequence = business.SequenceFileReaderFactory.ReadFile(str(path.resolve()))
    if sequence is None or sequence.IsError or not sequence.IsOpen:
        raise RawReaderError(f"Unable to read Xcalibur sequence: {path}")
    entries = []
    for index, sample in enumerate(sequence.Samples, start=1):
        filename = str(sample.RawFileName or "").strip()
        if filename and not filename.lower().endswith(".raw"):
            filename += ".raw"
        source = PureWindowsPath(str(sample.Path or "")) / filename
        entries.append(
            ScheduledInjection(
                sequence_path=str(path.resolve()),
                row_number=index,
                acquisition_source=str(source) if filename else "",
                instrument_method=str(sample.InstrumentMethodFile or "").strip(),
            )
        )
    return tuple(entries)


def derive_sample_metadata_from_raw_files(
    raw_paths: Sequence[Path],
    dll_dir: Path,
    *,
    batch_id: str | None = None,
) -> tuple[SampleMetadata, ...]:
    """Derive canonical SampleMetadata rows directly from Thermo RAW file headers.

    ``batch_id`` names one batch for every RAW; without it each parent folder
    is a batch with its own injection order.
    """
    if not raw_paths:
        return ()

    batches: dict[str, list[tuple[Path, Any]]] = {}
    for path in raw_paths:
        header = read_raw_sample_header(path, dll_dir=dll_dir)
        batches.setdefault(
            path.parent.name if batch_id is None else batch_id, []
        ).append((path, header))

    metadata_rows: list[SampleMetadata] = []
    seen_sample_names: set[str] = set()

    for batch_key, items in batches.items():
        row_numbers = [h.row_number for _, h in items]
        has_valid_row_numbers = all(
            isinstance(rn, int) and rn > 0 for rn in row_numbers
        ) and len(set(row_numbers)) == len(items)

        if has_valid_row_numbers:
            sorted_items = sorted(
                items,
                key=lambda x: (x[1].row_number, x[1].raw_stem or x[0].stem),
            )
            use_row_number = True
        else:
            min_dt = datetime.min
            sorted_items = sorted(
                items,
                key=lambda x: (
                    x[1].creation_date or min_dt,
                    x[1].raw_stem or x[0].stem,
                ),
            )
            use_row_number = False

        for order_idx, (path, header) in enumerate(sorted_items, start=1):
            injection_order = header.row_number if use_row_number else order_idx
            raw_stem = header.raw_stem or path.stem

            raw_name = header.sample_name or raw_stem
            if (
                not raw_name
                or raw_name.lower() in {"unknown", "sample"}
                or raw_name in seen_sample_names
            ):
                sample_name = raw_stem
            else:
                sample_name = raw_name

            if sample_name in seen_sample_names:
                sample_name = raw_stem
            seen_sample_names.add(sample_name)

            role = infer_sample_role(
                header.sample_type,
                sample_name=sample_name,
                comment=header.comment,
            )
            metadata_rows.append(
                SampleMetadata(
                    sample_name=sample_name,
                    raw_stem=raw_stem,
                    injection_order=injection_order,
                    sample_role=role,
                    batch_id=batch_key,
                    prep_batch_id="",
                    matrix_type="",
                    group="",
                    excluded=False,
                    exclusion_reason="",
                )
            )

    return tuple(metadata_rows)


def _validate_raw_session_capability(
    capability: RawAcquisitionSessionCapability | None,
    *,
    expected_acquisition_source: AcquisitionSourceIdentity | None,
) -> None:
    if capability is None:
        return
    if not isinstance(capability, RawAcquisitionSessionCapability):
        raise RawReaderError(
            "_raw_session_capability must be an owner-minted capability"
        )
    if not isinstance(expected_acquisition_source, AcquisitionSourceIdentity):
        raise RawReaderError(
            "RAW session capability requires expected_acquisition_source"
        )
    if capability.acquisition_source != expected_acquisition_source:
        raise RawReaderError(
            "RAW session capability is bound to a different acquisition source"
        )


def _dispose_after_failed_open(raw_file: Any | None) -> Exception | None:
    if raw_file is None:
        return None
    try:
        raw_file.Dispose()
    except Exception as exc:
        return exc
    return None


def _verify_expected_acquisition_source(
    path: Path,
    expected: AcquisitionSourceIdentity | None,
) -> _VerifiedAcquisitionSourceBinding | None:
    if expected is None:
        return None
    if not isinstance(expected, AcquisitionSourceIdentity):
        raise RawReaderError(
            "expected_acquisition_source must be an AcquisitionSourceIdentity"
        )
    if (
        expected.acquisition_format != THERMO_RAW_ACQUISITION_FORMAT
        or expected.observation_namespace != THERMO_RAW_OBSERVATION_NAMESPACE
    ):
        raise RawReaderError(
            "expected acquisition source does not describe the Thermo RAW "
            "MS-controller-1 adapter"
        )
    try:
        before = path.stat()
        fingerprint = fingerprint_file_content(path)
        after = path.stat()
    except IdentityContractError as exc:
        raise RawReaderError(
            "unable to verify the expected acquisition source"
        ) from exc
    except OSError as exc:
        raise RawReaderError(
            "unable to verify the expected acquisition source"
        ) from exc
    if not os.path.samestat(before, after) or _stat_identity(before) != _stat_identity(
        after
    ):
        raise RawReaderError("RAW source path changed while it was being verified")
    if (
        fingerprint.content_sha256 != expected.content_sha256
        or fingerprint.byte_size != expected.byte_size
    ):
        raise RawReaderError(
            "RAW path content does not match expected acquisition source"
        )
    return _VerifiedAcquisitionSourceBinding(
        source=expected,
        path_identity=_stat_identity(after),
    )


def _bind_expected_path_identity(
    path: Path,
    expected: AcquisitionSourceIdentity | None,
    expected_path_identity: tuple[int, int, int, int, int],
) -> _VerifiedAcquisitionSourceBinding:
    if not isinstance(expected, AcquisitionSourceIdentity):
        raise RawReaderError(
            "expected path identity requires an AcquisitionSourceIdentity"
        )
    if (
        expected.acquisition_format != THERMO_RAW_ACQUISITION_FORMAT
        or expected.observation_namespace != THERMO_RAW_OBSERVATION_NAMESPACE
    ):
        raise RawReaderError(
            "expected acquisition source does not describe the Thermo RAW "
            "MS-controller-1 adapter"
        )
    try:
        current = _stat_identity(path.stat())
    except OSError as exc:
        raise RawReaderError("unable to verify the expected RAW path state") from exc
    if current != expected_path_identity or current[2] != expected.byte_size:
        raise RawReaderError(
            "RAW path state does not match expected acquisition source"
        )
    return _VerifiedAcquisitionSourceBinding(
        source=expected,
        path_identity=current,
    )


def _verify_source_path_still_bound(
    path: Path,
    binding: _VerifiedAcquisitionSourceBinding | None,
    *,
    verify_content: bool = True,
) -> None:
    if binding is None:
        return
    if not verify_content:
        try:
            current_path_identity = _stat_identity(path.stat())
        except OSError as exc:
            raise RawReaderError(
                "RAW source path changed while the vendor handle was opening"
            ) from exc
        if current_path_identity != binding.path_identity:
            raise RawReaderError(
                "RAW source path changed while the vendor handle was opening"
            )
        return
    try:
        current_binding = _verify_expected_acquisition_source(
            path,
            binding.source,
        )
    except RawReaderError as exc:
        raise RawReaderError(
            "RAW source content or path changed while the vendor handle was opening"
        ) from exc
    if (
        current_binding is None
        or current_binding.path_identity != binding.path_identity
    ):
        raise RawReaderError(
            "RAW source content or path changed while the vendor handle was opening"
        )


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def preflight_raw_reader(
    dll_dir: Path,
    *,
    import_module_func: Callable[[str], Any] = importlib.import_module,
) -> list[str]:
    errors: list[str] = []
    try:
        pythonnet = import_module_func("pythonnet")
        _check_dotnet_runtime(pythonnet)
    except ImportError:
        errors.append(
            "pythonnet is not installed; Install project dependencies with `uv sync`."
        )
    except Exception as exc:
        errors.append(
            f".NET runtime is not available ({exc}); Install .NET 6+ runtime."
        )

    try:
        resolved = dll_dir.resolve(strict=True)
    except OSError:
        errors.append(
            f"Xcalibur DLL directory was not found at `{dll_dir}`; "
            "open Settings and correct DLL directory."
        )
        return errors

    for dll_name in EXPECTED_THERMO_DLLS:
        dll_path = resolved / dll_name
        if not dll_path.is_file():
            errors.append(
                f"Xcalibur DLL `{dll_name}` was not found in `{resolved}`; "
                "open Settings and correct DLL directory."
            )
    return errors


def reset_reader_state() -> None:
    global _ASSEMBLIES_LOADED
    _ASSEMBLIES_LOADED = False


def _check_dotnet_runtime(pythonnet: Any) -> None:
    get_runtime_info = getattr(pythonnet, "get_runtime_info", None)
    if callable(get_runtime_info):
        get_runtime_info()


def _ensure_assemblies_loaded(
    dll_dir: Path, import_module_func: Callable[[str], Any]
) -> None:
    global _ASSEMBLIES_LOADED
    if _ASSEMBLIES_LOADED:
        return
    clr = import_module_func("clr")
    for dll_name in EXPECTED_THERMO_DLLS:
        dll_path = dll_dir / dll_name
        try:
            clr.AddReference(str(dll_path))
        except Exception as exc:
            raise RawReaderError(
                f"Failed to load Thermo DLL `{dll_path}`: {exc}"
            ) from exc
    _ASSEMBLIES_LOADED = True


def _load_thermo_api(import_module_func: Callable[[str], Any]) -> _ThermoApi:
    raw_module = import_module_func("ThermoFisher.CommonCore.RawFileReader")
    business = import_module_func("ThermoFisher.CommonCore.Data.Business")
    filter_enums = import_module_func("ThermoFisher.CommonCore.Data.FilterEnums")
    return _ThermoApi(
        raw_file_reader_adapter=raw_module.RawFileReaderAdapter,
        device_ms=business.Device.MS,
        mass_range_factory=business.Range,
        trace_settings_factory=business.ChromatogramTraceSettings,
        mass_range_trace_type=business.TraceType.MassRange,
        ms2_order=filter_enums.MSOrderType.Ms2,
    )


def _first_or_empty(values: Any) -> Any:
    if values is None or len(values) == 0:
        return []
    return values[0]


def _item_or_empty(values: Any, index: int) -> Any:
    if values is None or len(values) <= index:
        return []
    return values[index]


def _extract_precursor_mz(filter_obj: Any) -> float:
    get_reaction = getattr(filter_obj, "GetReaction", None)
    if callable(get_reaction):
        try:
            precursor = _positive_float_or_none(get_reaction(0).PrecursorMass)
        except (AttributeError, IndexError, TypeError, ValueError):
            precursor = None
        if precursor is not None:
            return precursor

    reactions = getattr(
        getattr(filter_obj, "Filter", SimpleNamespace()), "Reactions", []
    )
    if reactions is None or len(reactions) == 0:
        raise RawReaderError("MS2 filter has no precursor reaction")
    precursor = _positive_float_or_none(reactions[0].PrecursorMass)
    if precursor is None:
        raise RawReaderError("MS2 filter has no precursor reaction")
    return precursor


def _read_precursor_metadata(
    raw: Any, filter_obj: Any, scan_number: int
) -> Ms2PrecursorMetadata:
    trailer_reader = getattr(raw, "GetTrailerExtraInformation", None)
    trailer = trailer_reader(scan_number) if callable(trailer_reader) else None
    values = {
        str(label).strip().rstrip(":").casefold(): value
        for label, value in zip(
            getattr(trailer, "Labels", ()), getattr(trailer, "Values", ())
        )
    }
    get_reaction = getattr(filter_obj, "GetReaction", None)
    reaction = None
    if callable(get_reaction):
        try:
            reaction = get_reaction(0)
        except (AttributeError, IndexError, TypeError, ValueError):
            pass
    if reaction is None:
        reactions = getattr(getattr(filter_obj, "Filter", None), "Reactions", ())
        reaction = reactions[0] if reactions else None
    width = _positive_float_or_none(getattr(reaction, "IsolationWidth", None))
    if width is None:
        width = _positive_float_or_none(values.get("ms2 isolation width"))
    offset = _finite_float_or_none(getattr(reaction, "IsolationWidthOffset", None))
    return Ms2PrecursorMetadata(
        monoisotopic_mz=_positive_float_or_none(values.get("monoisotopic m/z")),
        charge=_positive_integer_or_none(values.get("charge state")),
        isolation_width_da=width,
        isolation_offset_da=offset,
        master_scan_number=_positive_integer_or_none(values.get("master scan number")),
    )


def _positive_integer_or_none(value: Any) -> int | None:
    parsed = _positive_float_or_none(value)
    return int(parsed) if parsed is not None and parsed.is_integer() else None


def _finite_float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _positive_float_or_none(value: Any) -> float | None:
    parsed = _finite_float_or_none(value)
    return parsed if parsed is not None and parsed > 0.0 else None
