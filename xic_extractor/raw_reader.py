import importlib
import math
import os
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, final

import numpy as np

from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    IdentityContractError,
    fingerprint_file_content,
)
from xic_extractor.xic_models import XICRequest, XICTrace

EXPECTED_THERMO_DLLS = (
    "ThermoFisher.CommonCore.Data.dll",
    "ThermoFisher.CommonCore.RawFileReader.dll",
)

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
class Ms2Scan:
    scan_number: int
    rt: float
    precursor_mz: float
    masses: np.ndarray
    intensities: np.ndarray
    base_peak: float


@dataclass(frozen=True)
class Ms2ScanEvent:
    scan: Ms2Scan | None
    parse_error: str | None
    scan_number: int


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
    def acquisition_source(self) -> AcquisitionSourceIdentity | None:
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

    def iter_ms2_scans(self, rt_min: float, rt_max: float) -> Iterator[Ms2ScanEvent]:
        start_scan = self._raw_file.ScanNumberFromRetentionTime(rt_min)
        end_scan = self._raw_file.ScanNumberFromRetentionTime(rt_max)
        for scan_number in range(start_scan, end_scan + 1):
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
    Callable[[Any, _ThermoApi, AcquisitionSourceIdentity], RawFileHandle],
    Callable[[object, AcquisitionSourceIdentity], RawFileHandle],
]:
    mint_token = object()

    @final
    class OwnerBoundRawFileHandle(RawFileHandle):
        __slots__ = ("_bound_acquisition_source",)

        def __init__(
            self,
            raw_file: Any,
            thermo: _ThermoApi,
            source: AcquisitionSourceIdentity,
            *,
            _mint_token: object,
        ) -> None:
            if _mint_token is not mint_token:
                raise RawReaderError(
                    "owner-bound RAW handles must be minted by open_raw"
                )
            super().__init__(raw_file, thermo)
            self._bound_acquisition_source = source

        def __setattr__(self, name: str, value: object) -> None:
            if name in {
                "_raw_file",
                "_thermo",
                "_bound_acquisition_source",
            } and hasattr(self, name):
                raise AttributeError(f"{name} is sealed on owner-bound RAW handles")
            super().__setattr__(name, value)

        def __delattr__(self, name: str) -> None:
            if name in {
                "_raw_file",
                "_thermo",
                "_bound_acquisition_source",
            }:
                raise AttributeError(f"{name} is sealed on owner-bound RAW handles")
            super().__delattr__(name)

        def __init_subclass__(cls, **kwargs: object) -> None:
            raise TypeError("owner-bound RAW handles are final")

        @property
        def acquisition_source(self) -> AcquisitionSourceIdentity:
            return self._bound_acquisition_source

    def mint(
        raw_file: Any,
        thermo: _ThermoApi,
        source: AcquisitionSourceIdentity,
    ) -> RawFileHandle:
        return OwnerBoundRawFileHandle(
            raw_file,
            thermo,
            source,
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
    _import_module: Callable[[str], Any] = importlib.import_module,
    _thermo: _ThermoApi | None = None,
) -> RawFileHandle:
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
    source_binding = _verify_expected_acquisition_source(
        path,
        expected_acquisition_source,
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
        _verify_source_path_still_bound(path, source_binding)
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
    return _mint_owner_bound_raw_handle(
        raw_file,
        thermo,
        source_binding.source,
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


def _verify_source_path_still_bound(
    path: Path,
    binding: _VerifiedAcquisitionSourceBinding | None,
) -> None:
    if binding is None:
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


def _positive_float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0.0:
        return None
    return parsed
