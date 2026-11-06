"""Thermo DLL loading and native RAW/SLD I/O adapter."""

import importlib
import math
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath
from threading import Lock
from types import SimpleNamespace
from typing import Any

import numpy as np

from xic_extractor.acquisition_contract import (
    RawAcquisitionSessionReceipt,
    _mint_raw_acquisition_session_receipt,
)
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    MassRecalibrationResult,
    resolve_mass_recalibration,
)
from xic_extractor.injection_timeline import (
    InjectionHeader,
)
from xic_extractor.ms1_scan_index import MS1Scan, build_ms1_scan_index
from xic_extractor.ms2_scan_models import (
    Ms2PrecursorMetadata,
    Ms2PrecursorScope,
    Ms2Scan,
    Ms2ScanEvent,
)
from xic_extractor.precursor_evidence import Ms1PrecursorObservation
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
)
from xic_extractor.raw_mass_recalibration import (
    PreparedRawMassRecalibration,
    is_recorded_precursor,
    mass_restoration_scale,
    matches_parent_centroid,
    resolve_mass_restoration,
    restore_ms1_spectrum,
    restore_ms2_precursor,
    restored_xic,
)
from xic_extractor.raw_reader_errors import RawReaderError, RawReaderOpenError
from xic_extractor.raw_source_binding import (
    _verify_expected_acquisition_source,
    _verify_source_path_still_bound,
    bind_expected_path_identity,
)
from xic_extractor.raw_superwindow_source import RawSuperWindowSource
from xic_extractor.xic_models import XICRequest, XICTrace

# RawFileReader 8 package set; OpenMcdf is its only runtime dependency.
EXPECTED_THERMO_DLLS = (
    "OpenMcdf.dll",
    "ThermoFisher.CommonCore.Data.dll",
    "ThermoFisher.CommonCore.RawFileReader.dll",
)
THERMO_READER_INSTALL_COMMAND = "xic-install-thermo-reader"
DOTNET_INSTALL_COMMAND = "winget install Microsoft.DotNet.Runtime.10"
# Any installed .NET 8 or later major; .NET Framework cannot host RawFileReader 8.
_DOTNET_RUNTIME_CONFIG = Path(__file__).parent.joinpath(
    "configuration", "data", "thermo_dotnet.runtimeconfig.json"
)
_LM_CORRECTION_TRAILER = "lm correction (ppm)"
_LM_FOUND_TRAILER = "number of lm found"

_ASSEMBLIES_LOADED = False
_ASSEMBLY_LOAD_LOCK = Lock()


@dataclass(frozen=True)
class _ThermoApi:
    raw_file_reader_adapter: Any
    device_ms: Any
    mass_range_factory: Callable[[float, float], Any]
    trace_settings_factory: Callable[..., Any]
    mass_range_trace_type: Any
    ms2_order: Any
    ms1_order: Any = None
    header_errors: tuple[type[Exception], ...] = ()


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
    instrument_method: str = ""


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
    except AttributeError, TypeError, ValueError:
        return None


class RawFileHandle:
    __slots__ = (
        "_raw_file",
        "_thermo",
        "_raw_chromatogram_call_count",
        "_precursor_trailer_indices",
        "_acquisition_rt_bounds",
        "_mass_recalibration",
        "_lm_trailer_index",
        "_lm_found_trailer_index",
        "_mass_recalibration_result",
        "_mass_scale_by_scan",
        "_recalibrated_ms1",
    )

    def __init__(
        self,
        raw_file: Any,
        thermo: _ThermoApi,
        mass_recalibration: MassRecalibration = MassRecalibration.NONE,
    ) -> None:
        self._raw_file = raw_file
        self._thermo = thermo
        self._raw_chromatogram_call_count = 0
        self._precursor_trailer_indices: dict[str, int] | None = None
        self._acquisition_rt_bounds: tuple[float, float] | None = None
        self._mass_recalibration = resolve_mass_recalibration(mass_recalibration)
        self._lm_trailer_index: int | None = None
        self._lm_found_trailer_index: int | None = None
        self._mass_recalibration_result: MassRecalibrationResult | None = None
        self._mass_scale_by_scan: dict[int, float] = {}
        # Per MS1 scan: RT, recalibrated sorted masses and their intensities.
        self._recalibrated_ms1: dict[
            int, tuple[float, np.ndarray, np.ndarray] | None
        ] = {}

    def __enter__(self) -> "RawFileHandle":
        return self

    def __exit__(self, *_args: object) -> None:
        self._raw_file.Dispose()

    @property
    def mass_recalibration(self) -> MassRecalibration:
        return self._mass_recalibration

    @property
    def mass_recalibration_result(self) -> MassRecalibrationResult:
        if self._mass_recalibration_result is None:
            self._mass_recalibration_result = (
                MassRecalibrationResult(outcome="none")
                if self._mass_recalibration is MassRecalibration.NONE
                else self._prepare_mass_restoration()
            )
        return self._mass_recalibration_result

    def _prepare_mass_restoration(self) -> MassRecalibrationResult:
        """Restore positive-only RAWs from MS1 finds; never correct negative mode."""
        if self._mass_recalibration is MassRecalibration.EASY_IC_PROBE:
            raise RawReaderError(
                "easy_ic_probe is replay-only; use a saved result, "
                "easy_ic_restore or none"
            )
        self._load_lm_trailer_index()
        result = MassRecalibrationResult("unavailable", policy=self._mass_recalibration)
        lm_index, found_index = self._lm_trailer_index, self._lm_found_trailer_index
        if self._thermo.ms1_order is None:
            raise RawReaderError("mass restoration requires the vendor MS1 order")
        raw = self._raw_file
        anchors: list[float] = []
        polarities: set[str] = set()
        for scan in range(
            raw.RunHeaderEx.FirstSpectrum, raw.RunHeaderEx.LastSpectrum + 1
        ):
            scan_filter = raw.GetFilterForScanNumber(scan)
            polarity = str(scan_filter.Polarity)
            polarities.add(polarity)
            if (
                polarity != "Positive"
                or scan_filter.MSOrder != self._thermo.ms1_order
                or lm_index is None
                or found_index is None
            ):
                continue
            values = raw.GetTrailerExtraInformation(scan).Values
            if max(lm_index, found_index) >= len(values):
                continue
            lm = _finite_float_or_none(values[lm_index])
            found = _finite_float_or_none(values[found_index])
            if lm is not None and found is not None and found > 0:
                anchors.append(lm)
        return resolve_mass_restoration(
            result,
            polarities,
            anchors,
            has_lock_mass_trailers=lm_index is not None and found_index is not None,
        )

    def use_mass_recalibration_result(self, result: MassRecalibrationResult) -> None:
        """Initialize a fresh handle from its run's source-bound mass result."""
        self._mass_recalibration_result = result
        if result.outcome == "applied":
            self._load_lm_trailer_index()

    def _load_lm_trailer_index(self) -> None:
        labels = [
            str(item.Label).strip().rstrip(":").casefold()
            for item in self._raw_file.GetTrailerExtraHeaderInformation()
        ]
        self._lm_trailer_index = (
            labels.index(_LM_CORRECTION_TRAILER)
            if _LM_CORRECTION_TRAILER in labels
            else None
        )
        self._lm_found_trailer_index = (
            labels.index(_LM_FOUND_TRAILER) if _LM_FOUND_TRAILER in labels else None
        )

    def _scan_lm(self, scan_number: int) -> float | None:
        values = getattr(
            self._raw_file.GetTrailerExtraInformation(scan_number), "Values", ()
        )
        index = self._lm_trailer_index
        return (
            _finite_float_or_none(values[index])
            if index is not None and index < len(values)
            else None
        )

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
            except TypeError, ValueError:
                pass
        sample_type = str(getattr(info, "SampleType", "") or "Unknown").strip()
        vial = str(getattr(info, "Vial", "") or "").strip()
        comment = str(getattr(info, "Comment", "") or "").strip()
        creation_date = _to_python_datetime(getattr(raw, "CreationDate", None))
        file_name = str(getattr(raw, "FileName", "") or "").strip()
        raw_stem = PureWindowsPath(file_name).stem if file_name else ""
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
            instrument_method=str(
                getattr(info, "InstrumentMethodFile", "") or ""
            ).strip(),
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
        if self.mass_recalibration_result.outcome == "applied":
            return tuple(self._recalibrated_xic(request) for request in requests)
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

    def read_ms1_scan_index(self, *, centroid: bool = False) -> tuple[MS1Scan, ...]:
        return build_ms1_scan_index(self._raw_file, centroid=centroid)

    def batch_xic_source(self) -> RawSuperWindowSource:
        """Overlapping scan windows share one native chromatogram read."""
        return RawSuperWindowSource(self)

    def ms1_centroid_mz(self, rt: float, mz_min: float, mz_max: float) -> float | None:
        """Most intense MS1 centroid within [mz_min, mz_max] at the scan at ``rt``."""
        raw = self._raw_file
        scan_number = int(raw.ScanNumberFromRetentionTime(rt))
        if (
            getattr(raw.GetFilterForScanNumber(scan_number), "MSOrder", None)
            == self._thermo.ms2_order
        ):
            return None
        if self.mass_recalibration_result.outcome == "applied":
            spectrum = self._recalibrated_ms1_spectrum(scan_number)
            if spectrum is None:
                return None
            _, masses, intensities = spectrum
        else:
            centroids = raw.GetCentroidStream(scan_number, False)
            masses = np.asarray(getattr(centroids, "Masses", None) or [], dtype=float)
            intensities = np.asarray(
                getattr(centroids, "Intensities", None) or [], dtype=float
            )
        inside = (masses >= mz_min) & (masses <= mz_max)
        if not inside.any():
            return None
        return float(masses[inside][np.argmax(intensities[inside])])

    def read_ms1_mass_windows(
        self, requests: Sequence[tuple[int, float, float]]
    ) -> tuple[Ms1PrecursorObservation | None, ...]:
        """Read all positive centroids, once per exact MS1 scan in this batch."""
        by_scan: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
        for index, (scan_number, low, high) in enumerate(requests):
            by_scan[scan_number].append((index, low, high))
        results: list[Ms1PrecursorObservation | None] = [None] * len(requests)
        raw = self._raw_file
        for scan_number, windows in by_scan.items():
            if self.mass_recalibration_result.outcome == "applied":
                recalibrated = self._recalibrated_ms1_spectrum(scan_number)
                if recalibrated is None:
                    continue
                rt, masses, _ = recalibrated
                for index, low, high in windows:
                    inside = (masses >= low) & (masses <= high)
                    results[index] = Ms1PrecursorObservation(
                        scan_number, rt, tuple(float(mz) for mz in masses[inside])
                    )
                continue
            if (
                self._thermo.ms1_order is None
                or getattr(raw.GetFilterForScanNumber(scan_number), "MSOrder", None)
                != self._thermo.ms1_order
            ):
                continue
            spectrum = raw.GetCentroidStream(scan_number, False)
            masses = np.asarray(getattr(spectrum, "Masses", None) or [], dtype=float)
            intensities = np.asarray(
                getattr(spectrum, "Intensities", None) or [], dtype=float
            )
            valid = (
                np.isfinite(masses)
                & np.isfinite(intensities)
                & (masses > 0)
                & (intensities > 0)
            )
            rt = float(raw.RetentionTimeFromScanNumber(scan_number))
            for index, low, high in windows:
                inside = valid & (masses >= low) & (masses <= high)
                results[index] = Ms1PrecursorObservation(
                    scan_number, rt, tuple(float(mz) for mz in masses[inside])
                )
        return tuple(results)

    def acquisition_rt_bounds(self) -> tuple[float, float]:
        """Return this opened RAW's native acquisition span, without chronology I/O."""
        if self._acquisition_rt_bounds is not None:
            return self._acquisition_rt_bounds
        header = self._raw_file.RunHeaderEx
        start, end = float(header.StartTime), float(header.EndTime)
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end < start
        ):
            raise ValueError("RAW acquisition RT bounds must be finite and ordered")
        self._acquisition_rt_bounds = (start, end)
        return self._acquisition_rt_bounds

    def iter_ms2_scans(
        self, rt_min: float | None = None, rt_max: float | None = None
    ) -> Iterator[Ms2ScanEvent]:
        yield from self._iter_ms2_scans(rt_min, rt_max, scan_numbers=None)

    def iter_ms2_scan_subset(
        self, rt_min: float | None, rt_max: float | None, scan_numbers: frozenset[int]
    ) -> Iterator[Ms2ScanEvent]:
        """Read declared scans within the same native RT-to-scan window."""
        yield from self._iter_ms2_scans(rt_min, rt_max, scan_numbers=scan_numbers)

    def read_ms2_window(
        self, rt_min: float, rt_max: float, *, precursor_scope: Ms2PrecursorScope
    ) -> tuple[Ms2ScanEvent, ...]:
        """Read spectra only for MS2 events the scope admits."""
        return tuple(
            self._iter_ms2_scans(
                rt_min, rt_max, scan_numbers=None, precursor_scope=precursor_scope
            )
        )

    def _iter_ms2_scans(
        self,
        rt_min: float | None,
        rt_max: float | None,
        *,
        scan_numbers: frozenset[int] | None,
        precursor_scope: Ms2PrecursorScope | None = None,
    ) -> Iterator[Ms2ScanEvent]:
        """Yield admitted MS2 events and acquisition errors."""
        one_sided_crop = (rt_min is None) != (rt_max is None)
        for name, value in (("rt_min", rt_min), ("rt_max", rt_max)):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be finite and non-negative or None")
        if rt_min is not None and rt_max is not None and rt_min > rt_max:
            raise ValueError("rt_min must not exceed rt_max")
        if rt_min is None or rt_max is None:
            acquisition_start, acquisition_end = self.acquisition_rt_bounds()
            rt_min = (
                acquisition_start if rt_min is None else max(rt_min, acquisition_start)
            )
            rt_max = acquisition_end if rt_max is None else min(rt_max, acquisition_end)
        if rt_min > rt_max:
            return
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
                scan_rt = float(self._raw_file.RetentionTimeFromScanNumber(scan_number))
                if one_sided_crop and not rt_min <= scan_rt <= rt_max:
                    continue
                filter_obj = self._raw_file.GetFilterForScanNumber(scan_number)
                if getattr(filter_obj, "MSOrder", None) != self._thermo.ms2_order:
                    continue
                if self._precursor_trailer_indices is None:
                    self._precursor_trailer_indices = {
                        key: index
                        for index, item in enumerate(
                            self._raw_file.GetTrailerExtraHeaderInformation()
                        )
                        if (key := str(item.Label).strip().rstrip(":").casefold())
                        in {
                            "monoisotopic m/z",
                            "charge state",
                            "master scan number",
                            "ms2 isolation width",
                        }
                    }
                precursor_mz = _extract_precursor_mz(filter_obj)
                precursor_metadata = _read_precursor_metadata(
                    self._raw_file,
                    filter_obj,
                    scan_number,
                    self._precursor_trailer_indices,
                )
                fragment_scale = None
                if self.mass_recalibration_result.outcome == "applied":
                    fragment_scale = self._mass_scale(scan_number)
                    precursor_mz, precursor_metadata = self._recalibrate_ms2_precursor(
                        scan_number, precursor_mz, precursor_metadata
                    )
                if precursor_scope is not None and not precursor_scope(
                    precursor_mz, precursor_metadata
                ):
                    continue
                scan_data = self._raw_file.GetSimplifiedScan(scan_number)
                masses = np.asarray(getattr(scan_data, "Masses", []), dtype=float)
                intensities = np.asarray(
                    getattr(scan_data, "Intensities", []), dtype=float
                )
                base_peak = float(np.max(intensities)) if len(intensities) else 0.0
                if fragment_scale is not None:
                    masses = masses / fragment_scale
                yield Ms2ScanEvent(
                    scan=Ms2Scan(
                        scan_number=scan_number,
                        rt=scan_rt,
                        precursor_mz=precursor_mz,
                        masses=masses,
                        intensities=intensities,
                        base_peak=base_peak,
                        precursor_metadata=precursor_metadata,
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

    def _mass_scale(self, scan_number: int) -> float:
        """Cache the restoration scale after reading this scan's native trailer."""
        scale = self._mass_scale_by_scan.get(scan_number)
        if scale is None:
            scale = mass_restoration_scale(
                self.mass_recalibration_result, self._scan_lm(scan_number), scan_number
            )
            self._mass_scale_by_scan[scan_number] = scale
        return scale

    def _recalibrated_ms1_spectrum(
        self, scan_number: int
    ) -> tuple[float, np.ndarray, np.ndarray] | None:
        """Cache a policy-adjusted MS1 spectrum; None for other scans."""
        if scan_number in self._recalibrated_ms1:
            return self._recalibrated_ms1[scan_number]
        raw = self._raw_file
        if self._thermo.ms1_order is None:
            raise RawReaderError("mass recalibration requires the vendor MS1 order")
        spectrum: tuple[float, np.ndarray, np.ndarray] | None = None
        if (
            getattr(raw.GetFilterForScanNumber(scan_number), "MSOrder", None)
            == self._thermo.ms1_order
        ):
            centroids = raw.GetCentroidStream(scan_number, False)
            masses = np.asarray(getattr(centroids, "Masses", None) or [], dtype=float)
            intensities = np.asarray(
                getattr(centroids, "Intensities", None) or [], dtype=float
            )
            spectrum = restore_ms1_spectrum(
                float(raw.RetentionTimeFromScanNumber(scan_number)),
                masses,
                intensities,
                self._mass_scale(scan_number),
            )
        self._recalibrated_ms1[scan_number] = spectrum
        return spectrum

    def _recalibrated_xic(self, request: XICRequest) -> XICTrace:
        """Read native MS1 facts before integrating in restored coordinates."""
        start_scan, end_scan = self.scan_window_for_request(request)
        spectra = []
        for scan_number in range(start_scan, end_scan + 1):
            spectrum = self._recalibrated_ms1_spectrum(scan_number)
            if spectrum is not None:
                spectra.append(spectrum)
        return restored_xic(spectra, request)

    def _recalibrate_ms2_precursor(
        self, scan_number: int, precursor_mz: float, metadata: Ms2PrecursorMetadata
    ) -> tuple[float, Ms2PrecursorMetadata]:
        """Read parent facts here; mass-coordinate decisions remain pure."""
        if metadata.master_scan_number is None:
            if metadata.monoisotopic_mz is None:
                return precursor_mz, metadata
            raise RawReaderError(
                f"MS2 scan {scan_number} records a precursor without its parent MS1"
            )
        parent_scale = self._mass_scale(metadata.master_scan_number)
        measured = is_recorded_precursor(precursor_mz, metadata, parent_scale)
        if not measured:
            parent = self._recalibrated_ms1_spectrum(metadata.master_scan_number)
            if parent is not None:
                measured = matches_parent_centroid(
                    precursor_mz / parent_scale, parent[1]
                )
        return restore_ms2_precursor(
            precursor_mz, metadata, parent_scale, measured=measured
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


class _SourceBoundRawFileHandle(RawFileHandle):
    """Native handle carrying the RAW source verified when it was opened."""

    __slots__ = ("_bound_acquisition_source", "_raw_open_receipt")

    def __init__(
        self,
        raw_file: Any,
        thermo: _ThermoApi,
        source: AcquisitionSourceIdentity,
        raw_open_receipt: RawAcquisitionSessionReceipt | None,
        mass_recalibration: MassRecalibration,
    ) -> None:
        super().__init__(raw_file, thermo, mass_recalibration)
        self._bound_acquisition_source = source
        self._raw_open_receipt = raw_open_receipt

    @property
    def acquisition_source(self) -> AcquisitionSourceIdentity:
        return self._bound_acquisition_source

    @property
    def raw_open_receipt(self) -> RawAcquisitionSessionReceipt | None:
        return self._raw_open_receipt


def require_source_bound_raw_handle(
    raw: object,
    expected: AcquisitionSourceIdentity,
) -> RawFileHandle:
    if not isinstance(raw, _SourceBoundRawFileHandle):
        raise RawReaderError("RAW handle has no acquisition source binding")
    if raw.acquisition_source != expected:
        raise RawReaderError("RAW handle is bound to a different acquisition source")
    return raw


def open_raw(
    path: Path,
    dll_dir: Path,
    *,
    mass_recalibration: MassRecalibration = MassRecalibration.NONE,
    prepared_mass_recalibration: PreparedRawMassRecalibration | None = None,
    expected_acquisition_source: AcquisitionSourceIdentity | None = None,
    _expected_path_identity: tuple[int, int, int, int, int] | None = None,
    _import_module: Callable[[str], Any] = importlib.import_module,
    _thermo: _ThermoApi | None = None,
) -> RawFileHandle:
    mass_recalibration = resolve_mass_recalibration(mass_recalibration)
    if prepared_mass_recalibration is not None:
        if prepared_mass_recalibration.result.policy is not mass_recalibration:
            raise RawReaderError("prepared mass recalibration uses a different policy")
        if expected_acquisition_source is not None and (
            expected_acquisition_source
            != prepared_mass_recalibration.acquisition.source
        ):
            raise RawReaderError("prepared mass recalibration belongs to another RAW")
        expected_acquisition_source = prepared_mass_recalibration.acquisition.source
        if _expected_path_identity is None:
            _expected_path_identity = (
                prepared_mass_recalibration.acquisition.path_identity
            )
    errors = preflight_raw_reader(dll_dir, import_module_func=_import_module)
    if errors:
        raise RawReaderError(" ".join(errors))

    resolved_dll_dir = dll_dir.resolve()
    try:
        ensure_assemblies_loaded(resolved_dll_dir, _import_module)
        thermo = _thermo or _load_thermo_api(_import_module)
    except Exception as exc:
        raise RawReaderError(
            f"Failed to load Thermo DLL or open raw file: {exc}"
        ) from exc
    source_binding = (
        _verify_expected_acquisition_source(path, expected_acquisition_source)
        if _expected_path_identity is None
        else bind_expected_path_identity(
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
        return RawFileHandle(raw_file, thermo, mass_recalibration)
    raw_open_receipt = (
        None
        if expected_acquisition_source is None
        else _mint_raw_acquisition_session_receipt(expected_acquisition_source)
    )
    handle = _SourceBoundRawFileHandle(
        raw_file,
        thermo,
        source_binding.source,
        raw_open_receipt,
        mass_recalibration,
    )
    if prepared_mass_recalibration is not None:
        try:
            handle.use_mass_recalibration_result(prepared_mass_recalibration.result)
        except BaseException:
            handle.__exit__()
            raise
    return handle


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
    try:
        run_header = raw.RunHeaderEx
        start_time = float(run_header.StartTime)
        end_time = float(run_header.EndTime)
    except handle._thermo.header_errors as exc:
        raise RawReaderError(f"RAW has no selected MS device: {path}: {exc}") from exc
    return InjectionHeader(
        raw_path=str(path.resolve()),
        acquisition_source=str(info.RawFileName or "").strip(),
        creation_date=creation,
        start_time_min=start_time,
        end_time_min=end_time,
        instrument_method=str(info.InstrumentMethodFile or "").strip(),
    )


def _dispose_after_failed_open(raw_file: Any | None) -> Exception | None:
    if raw_file is None:
        return None
    try:
        raw_file.Dispose()
    except Exception as exc:
        return exc
    return None


def preflight_raw_reader(
    dll_dir: Path,
    *,
    import_module_func: Callable[[str], Any] = importlib.import_module,
) -> list[str]:
    return dotnet_runtime_errors(import_module_func) + thermo_dll_errors(dll_dir)


def dotnet_runtime_errors(
    import_module_func: Callable[[str], Any] = importlib.import_module,
) -> list[str]:
    """Select .NET 8 or later for RawFileReader; report why it cannot load."""
    errors: list[str] = []
    try:
        pythonnet = import_module_func("pythonnet")
        if pythonnet.get_runtime_info() is None:
            pythonnet.set_runtime("coreclr", runtime_config=str(_DOTNET_RUNTIME_CONFIG))
    except ImportError:
        errors.append(
            "pythonnet is not installed; Install project dependencies with `uv sync`."
        )
    except Exception as exc:
        errors.append(
            f".NET 8 or later is not available ({exc}); "
            f"install the .NET 10 runtime with `{DOTNET_INSTALL_COMMAND}`."
        )
    return errors


def thermo_dll_errors(dll_dir: Path) -> list[str]:
    """Report a missing or pre-8 RawFileReader DLL set in ``dll_dir``."""
    errors: list[str] = []
    fix = (
        f"run `{THERMO_READER_INSTALL_COMMAND}` or open Settings and correct "
        "the DLL directory."
    )
    try:
        resolved = dll_dir.resolve(strict=True)
    except OSError:
        errors.append(
            f"RawFileReader DLL directory was not found at `{dll_dir}`; {fix}"
        )
        return errors

    missing = [name for name in EXPECTED_THERMO_DLLS if not (resolved / name).is_file()]
    if missing == ["OpenMcdf.dll"]:
        errors.append(
            f"`{resolved}` holds an older RawFileReader (such as Xcalibur's); "
            f"XIC Extractor needs RawFileReader 8, so {fix}"
        )
    else:
        errors.extend(
            f"RawFileReader DLL `{name}` was not found in `{resolved}`; {fix}"
            for name in missing
        )
    return errors


def ensure_assemblies_loaded(
    dll_dir: Path, import_module_func: Callable[[str], Any]
) -> None:
    global _ASSEMBLIES_LOADED
    with _ASSEMBLY_LOAD_LOCK:
        if _ASSEMBLIES_LOADED:
            return
        errors = dotnet_runtime_errors(import_module_func)
        if errors:
            raise RawReaderError(" ".join(errors))
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
        ms1_order=filter_enums.MSOrderType.Ms,
        header_errors=(business.NoSelectedDeviceException,),
    )


def _item_or_empty(values: Any, index: int) -> Any:
    if values is None or len(values) <= index:
        return []
    return values[index]


def _extract_precursor_mz(filter_obj: Any) -> float:
    get_reaction = getattr(filter_obj, "GetReaction", None)
    if callable(get_reaction):
        try:
            precursor = _positive_float_or_none(get_reaction(0).PrecursorMass)
        except AttributeError, IndexError, TypeError, ValueError:
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
    raw: Any,
    filter_obj: Any,
    scan_number: int,
    trailer_indices: dict[str, int],
) -> Ms2PrecursorMetadata:
    trailer_reader = getattr(raw, "GetTrailerExtraInformation", None)
    trailer = trailer_reader(scan_number) if callable(trailer_reader) else None
    # Preserve formatted vendor values: the typed trailer API changes precision.
    trailer_values = getattr(trailer, "Values", ())
    values = {
        key: trailer_values[index]
        for key, index in trailer_indices.items()
        if index < len(trailer_values)
    }
    get_reaction = getattr(filter_obj, "GetReaction", None)
    reaction = None
    if callable(get_reaction):
        try:
            reaction = get_reaction(0)
        except AttributeError, IndexError, TypeError, ValueError:
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
    except TypeError, ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _positive_float_or_none(value: Any) -> float | None:
    parsed = _finite_float_or_none(value)
    return parsed if parsed is not None and parsed > 0.0 else None


def read_raw_headers(
    path: Path, dll_dir: Path
) -> tuple[InjectionHeader, RawSampleHeader]:
    """Read chronology and sample facts using one selected native handle."""
    with open_raw(path, dll_dir=dll_dir) as handle:
        return _injection_header(handle, path), handle.sample_header


def read_sequence_samples(
    path: Path, dll_dir: Path
) -> tuple[tuple[str, str, str], ...]:
    """Copy SLD sample fields out of the vendor API before sequence projection."""
    errors = preflight_raw_reader(dll_dir)
    if errors:
        raise RawReaderError("; ".join(errors))
    ensure_assemblies_loaded(dll_dir.resolve(), importlib.import_module)
    business = importlib.import_module("ThermoFisher.CommonCore.Data.Business")
    sequence = business.SequenceFileReaderFactory.ReadFile(str(path.resolve()))
    if sequence is None or sequence.IsError or not sequence.IsOpen:
        raise RawReaderError(f"Unable to read Xcalibur sequence: {path}")
    return tuple(
        (
            str(sample.RawFileName or "").strip(),
            str(sample.Path or ""),
            str(sample.InstrumentMethodFile or "").strip(),
        )
        for sample in sequence.Samples
    )
