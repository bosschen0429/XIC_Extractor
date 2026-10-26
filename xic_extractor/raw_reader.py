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
    ANALYSIS_SAMPLE_ROLES,
    AnalysisSet,
    discover_raw_files,
    select_analysis_set,
)
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    resolve_mass_recalibration,
)
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibrationResult as MassRecalibrationResult,
)
from xic_extractor.injection_timeline import (
    InjectionHeader,
    PerturbationPolicy,
    ScheduledInjection,
    build_injection_timeline,
)
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata, Ms2Scan, Ms2ScanEvent
from xic_extractor.precursor_evidence import Ms1PrecursorObservation
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    IdentityContractError,
    fingerprint_file_content,
)
from xic_extractor.sample_metadata import (
    SampleMetadata,
    SampleRole,
    infer_sample_role,
    load_and_match_sample_metadata,
)
from xic_extractor.xic_models import XICRequest, XICTrace

EXPECTED_THERMO_DLLS = (
    "ThermoFisher.CommonCore.Data.dll",
    "ThermoFisher.CommonCore.RawFileReader.dll",
)
THERMO_RAW_ACQUISITION_FORMAT = "thermo_raw"
THERMO_RAW_OBSERVATION_NAMESPACE = "thermo_raw_file_reader:Device.MS:1"
_LM_CORRECTION_TRAILER = "lm correction (ppm)"
# MS2 headers round the selected precursor to 1e-4 m/z.
_RECORDED_PRECURSOR_ROUNDING_MZ = 1e-4
_CARBON_ISOTOPE_SPACING_DA = 1.0033548

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
    ms1_order: Any = None
    header_errors: tuple[type[Exception], ...] = ()


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
    except (AttributeError, TypeError, ValueError):
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
                else self._probe_mass_recalibration()
            )
        return self._mass_recalibration_result

    def _probe_mass_recalibration(self) -> MassRecalibrationResult:
        """Frozen per-RAW EASY-IC probe: fit siloxane, independently validate DIOP."""
        raw = self._raw_file
        lm_values: list[float | None] = []
        fit: list[tuple[float | None, float]] = []
        val: list[tuple[float | None, float]] = []
        labels = [
            str(item.Label).strip().rstrip(":").casefold()
            for item in raw.GetTrailerExtraHeaderInformation()
        ]
        if _LM_CORRECTION_TRAILER in labels:
            self._lm_trailer_index = labels.index(_LM_CORRECTION_TRAILER)
        for scan in range(
            raw.RunHeaderEx.FirstSpectrum, raw.RunHeaderEx.LastSpectrum + 1
        ):
            if (
                getattr(raw.GetFilterForScanNumber(scan), "MSOrder", None)
                != self._thermo.ms1_order
            ):
                continue
            lm = self._scan_lm(scan) if self._lm_trailer_index is not None else None
            lm_values.append(lm)
            stream = raw.GetCentroidStream(scan, False)
            masses = np.asarray(getattr(stream, "Masses", None) or [], dtype=float)
            intensities = np.asarray(
                getattr(stream, "Intensities", None) or [], dtype=float
            )
            for reference, observations in ((371.101233, fit), (391.284286, val)):
                errors = (masses - reference) / reference * 1e6
                inside = (np.abs(errors) <= 40.0) & np.isfinite(intensities)
                if inside.any():
                    error = float(errors[inside][np.argmax(intensities[inside])])
                    observations.append((lm, error))
        result = MassRecalibrationResult(
            "unavailable",
            n_fit=len(fit),
            n_val=len(val),
            policy=self._mass_recalibration,
        )
        if len(fit) < 50 or len(val) < 50:
            return replace(result, reason="insufficient_references")
        val_errors = np.array([error for _, error in val])
        raw_p95 = float(np.percentile(np.abs(val_errors), 95))
        result = replace(result, val_raw_p95=raw_p95)
        if raw_p95 <= 3.0:
            return replace(result, outcome="not_needed")
        if (
            any(lm is None for lm in lm_values)
            or np.ptp(np.array(lm_values, dtype=float)) == 0
        ):
            return replace(result, reason="no_correction_signal")
        design = np.column_stack((np.ones(len(fit)), [lm for lm, _ in fit]))
        a, b = np.linalg.lstsq(design, [error for _, error in fit], rcond=None)[0]
        residuals = val_errors - (a + b * np.array([lm for lm, _ in val]))
        corrected_p95 = float(np.percentile(np.abs(residuals), 95))
        result = replace(result, val_corrected_p95=corrected_p95)
        if corrected_p95 <= 3.0 and corrected_p95 < raw_p95:
            return replace(result, outcome="applied", a=float(a), b=float(b))
        return replace(result, reason="validation_failed")

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

    def _iter_ms2_scans(
        self,
        rt_min: float | None,
        rt_max: float | None,
        *,
        scan_numbers: frozenset[int] | None,
    ) -> Iterator[Ms2ScanEvent]:
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
                scan_data = self._raw_file.GetSimplifiedScan(scan_number)
                masses = np.asarray(getattr(scan_data, "Masses", []), dtype=float)
                intensities = np.asarray(
                    getattr(scan_data, "Intensities", []), dtype=float
                )
                base_peak = float(np.max(intensities)) if len(intensities) else 0.0
                precursor_metadata = _read_precursor_metadata(
                    self._raw_file,
                    filter_obj,
                    scan_number,
                    self._precursor_trailer_indices,
                )
                if self.mass_recalibration_result.outcome == "applied":
                    masses, precursor_mz, precursor_metadata = self._recalibrate_ms2(
                        scan_number, masses, precursor_mz, precursor_metadata
                    )
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
        """Measured m/z divided by true m/z for one scan under the policy."""
        scale = self._mass_scale_by_scan.get(scan_number)
        if scale is not None:
            return scale
        result = self.mass_recalibration_result
        correction_ppm = self._scan_lm(scan_number)
        if correction_ppm is None:
            raise RawReaderError(
                f"scan {scan_number} lacks a finite 'LM Correction (ppm)' value"
            )
        assert result.a is not None and result.b is not None
        scale = 1.0 + (result.a + result.b * correction_ppm) * 1e-6
        self._mass_scale_by_scan[scan_number] = scale
        return scale

    def _recalibrated_ms1_spectrum(
        self, scan_number: int
    ) -> tuple[float, np.ndarray, np.ndarray] | None:
        """Cache one MS1 centroid spectrum on true m/z; None for other scans."""
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
            valid = (
                np.isfinite(masses)
                & np.isfinite(intensities)
                & (masses > 0)
                & (intensities > 0)
            )
            order = np.argsort(masses[valid], kind="stable")
            spectrum = (
                float(raw.RetentionTimeFromScanNumber(scan_number)),
                masses[valid][order] / self._mass_scale(scan_number),
                intensities[valid][order],
            )
        self._recalibrated_ms1[scan_number] = spectrum
        return spectrum

    def _recalibrated_xic(self, request: XICRequest) -> XICTrace:
        """Sum MS1 centroids per scan, as the vendor XIC does, on true m/z."""
        start_scan, end_scan = self.scan_window_for_request(request)
        tolerance = request.mz * request.ppm_tol / 1e6
        rts: list[float] = []
        values: list[float] = []
        for scan_number in range(start_scan, end_scan + 1):
            spectrum = self._recalibrated_ms1_spectrum(scan_number)
            if spectrum is None:
                continue
            rt, masses, intensities = spectrum
            low = np.searchsorted(masses, request.mz - tolerance, side="left")
            high = np.searchsorted(masses, request.mz + tolerance, side="right")
            rts.append(rt)
            values.append(float(intensities[low:high].sum()))
        return XICTrace.from_arrays(rts, values) if rts else XICTrace.empty()

    def _recalibrate_ms2(
        self,
        scan_number: int,
        masses: np.ndarray,
        precursor_mz: float,
        metadata: Ms2PrecursorMetadata,
    ) -> tuple[np.ndarray, float, Ms2PrecursorMetadata]:
        """Fragments use this scan; precursor readings use their parent MS1.

        A filter precursor matching the recorded mono, a charge-resolved M+n,
        or a parent centroid is measured; inclusion-list setpoints stay unchanged.
        """
        recalibrated_masses = masses / self._mass_scale(scan_number)
        monoisotopic = metadata.monoisotopic_mz
        if monoisotopic is None:
            return recalibrated_masses, precursor_mz, metadata
        if metadata.master_scan_number is None:
            raise RawReaderError(
                f"MS2 scan {scan_number} records a precursor without its parent MS1"
            )
        parent_scale = self._mass_scale(metadata.master_scan_number)
        measured_precursor = (
            abs(precursor_mz - monoisotopic) <= _RECORDED_PRECURSOR_ROUNDING_MZ
        )
        if metadata.charge is not None:
            # Compare in the recorded coordinate system, before correcting M.
            # Both measured masses share the parent scale, including spacing.
            spacing = _CARBON_ISOTOPE_SPACING_DA * parent_scale / metadata.charge
            isotope_number = round((precursor_mz - monoisotopic) / spacing)
            if isotope_number >= 1:
                measured_precursor = (
                    abs(precursor_mz - (monoisotopic + isotope_number * spacing))
                    <= _RECORDED_PRECURSOR_ROUNDING_MZ
                )
        if not measured_precursor:
            parent = self._recalibrated_ms1_spectrum(metadata.master_scan_number)
            if parent is not None:
                selected = precursor_mz / parent_scale
                index = int(np.searchsorted(parent[1], selected))
                # A selected parent centroid remains a measured precursor even
                # when the vendor's mono/isotope estimate is imprecise. Allow
                # only arithmetic roundoff here, not a mass-tolerance window
                # that could turn an inclusion-list setpoint into a measurement.
                measured_precursor = any(
                    math.isclose(selected, float(mass), rel_tol=1e-12, abs_tol=0.0)
                    for mass in parent[1][max(0, index - 1) : index + 1]
                )
        if measured_precursor:
            precursor_mz = precursor_mz / parent_scale
        return (
            recalibrated_masses,
            precursor_mz,
            replace(metadata, monoisotopic_mz=monoisotopic / parent_scale),
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
            MassRecalibration,
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
            mass_recalibration: MassRecalibration,
            *,
            _mint_token: object,
        ) -> None:
            if _mint_token is not mint_token:
                raise RawReaderError(
                    "owner-bound RAW handles must be minted by open_raw"
                )
            super().__init__(raw_file, thermo, mass_recalibration)
            self._bound_acquisition_source = source
            self._raw_open_receipt = raw_open_receipt

        def __setattr__(self, name: str, value: object) -> None:
            if name in {
                "_raw_file",
                "_thermo",
                "_bound_acquisition_source",
                "_raw_open_receipt",
                "_mass_recalibration",
            } and hasattr(self, name):
                raise AttributeError(f"{name} is sealed on owner-bound RAW handles")
            super().__setattr__(name, value)

        def __delattr__(self, name: str) -> None:
            if name in {
                "_raw_file",
                "_thermo",
                "_bound_acquisition_source",
                "_raw_open_receipt",
                "_mass_recalibration",
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
        mass_recalibration: MassRecalibration,
    ) -> RawFileHandle:
        return OwnerBoundRawFileHandle(
            raw_file,
            thermo,
            source,
            raw_open_receipt,
            mass_recalibration,
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
    mass_recalibration: MassRecalibration = MassRecalibration.NONE,
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
    mass_recalibration = resolve_mass_recalibration(mass_recalibration)
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
        if _raw_session_capability is None
        else _mint_raw_acquisition_session_receipt(_raw_session_capability)
    )
    return _mint_owner_bound_raw_handle(
        raw_file,
        thermo,
        source_binding.source,
        raw_open_receipt,
        mass_recalibration,
    )


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


def read_analysis_set(
    raw_root: Path,
    dll_dir: Path,
    sample_metadata: Sequence[SampleMetadata] | None = None,
    *,
    sequence_source: Path | None = None,
    raw_paths: Sequence[Path] | None = None,
    include_context: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> AnalysisSet:
    """Resolve who each RAW below root is and when it was injected.

    ``raw_paths`` names the RAW files when a caller already holds them (for
    example a Discovery batch); otherwise files below ``raw_root`` are discovered.
    With ``include_context=False``, explicit non-analysis annotations are resolved
    before I/O; those files remain unordered context without opening their RAW.

    The RAW headers own order (acquisition clock, older batches first) and the
    sample name. 上機資訊 (``sequence_source``) and workbench rows
    (``sample_metadata``, RAW-keyed) only annotate role, group, batch, and
    exclusion; a document may cover just some of the RAW. An unreadable RAW
    that would be analyzed stops resolution, because its order would be a
    guess; one that would not be analyzed is listed in ``unreadable``.
    """
    root = Path(raw_root)
    paths = tuple(raw_paths) if raw_paths is not None else discover_raw_files(root)
    sequence = (
        load_and_match_sample_metadata(sequence_source, [path.stem for path in paths])
        if sequence_source is not None
        else None
    )
    matched_stems = set(sequence.matched_raw_stems) if sequence is not None else set()
    overrides = (
        {
            row.raw_stem.casefold(): row
            for row in sequence.rows
            if row.raw_stem in matched_stems
        }
        if sequence is not None
        else {}
    )
    overrides.update({row.raw_stem.casefold(): row for row in sample_metadata or ()})
    headers: list[InjectionHeader] = []
    header_rows: dict[str, SampleMetadata] = {}
    unread: dict[str, SampleMetadata] = {}
    unread_errors: dict[str, str] = {}
    unopened: dict[str, SampleMetadata] = {}
    for index, path in enumerate(paths):
        if should_cancel is not None and should_cancel():
            raise InterruptedError("RAW 樣本準備已停止。")
        annotation = overrides.get(path.stem.casefold())
        if (
            not include_context
            and annotation is not None
            and (
                annotation.excluded
                or (
                    annotation.sample_role != "unknown"
                    and annotation.sample_role not in ANALYSIS_SAMPLE_ROLES
                )
            )
        ):
            unopened[str(path.resolve())] = replace(
                annotation, batch_id=annotation.batch_id or root.name
            )
            continue
        if progress is not None:
            progress(index, len(paths), f"讀取 RAW 檔頭：{path.name}")
        try:
            header, role, header_name = _analysis_header(path, dll_dir)
        except (RawReaderError, ValueError) as exc:
            unread_errors[str(path.resolve())] = str(exc)
            unread[str(path.resolve())] = SampleMetadata(
                sample_name=path.stem,
                raw_stem=path.stem,
                sample_role=infer_sample_role(None, sample_name=path.stem),
                batch_id=root.name,
            )
            continue
        headers.append(header)
        header_rows[header.raw_path] = SampleMetadata(
            sample_name=header_name,
            raw_stem=path.stem,
            sample_role=role,
            batch_id=root.name,
        )
    timeline = build_injection_timeline(headers, PerturbationPolicy())
    return select_analysis_set(
        timeline,
        header_rows,
        tuple(overrides.values()),
        unreadable=unread,
        sequence=sequence,
        unopened_context=unopened,
        unreadable_errors=unread_errors,
    )


_ANALYSIS_HEADER_CACHE: dict[
    tuple[str, int, int, str], tuple[InjectionHeader, SampleRole, str]
] = {}


def _analysis_header(
    path: Path, dll_dir: Path
) -> tuple[InjectionHeader, SampleRole, str]:
    """Header facts are immutable for an unchanged file; reuse across resolutions."""
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, str(dll_dir))
    cached = _ANALYSIS_HEADER_CACHE.get(key)
    if cached is not None:
        return cached
    with open_raw(path, dll_dir=dll_dir) as handle:
        header = _injection_header(handle, path)
        sample = handle.sample_header
    facts = (header, _raw_sample_role(path, sample), str(sample.sample_name or ""))
    _ANALYSIS_HEADER_CACHE[key] = facts
    return facts


def _raw_sample_role(path: Path, sample: RawSampleHeader) -> SampleRole:
    # Header SampleName is free text; use the filename and method vocabulary
    # consistently for analysis membership and auto-derived metadata.
    return infer_sample_role(
        sample.sample_type,
        sample_name=path.stem,
        comment=" ".join(
            (
                sample.sample_name,
                PureWindowsPath(sample.instrument_method).stem,
                sample.comment,
            )
        ),
    )


def read_sld_injections(path: Path, dll_dir: Path) -> tuple[ScheduledInjection, ...]:
    """Read an optional Xcalibur sequence; it supplies no acquisition clock."""
    errors = preflight_raw_reader(dll_dir)
    if errors:
        raise RawReaderError("; ".join(errors))
    ensure_assemblies_loaded(dll_dir.resolve(), importlib.import_module)
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
    if not os.path.samestat(before, after) or stat_identity(before) != stat_identity(
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
        path_identity=stat_identity(after),
    )


def bind_expected_path_identity(
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
        current = stat_identity(path.stat())
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
            current_path_identity = stat_identity(path.stat())
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


def stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
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


def _check_dotnet_runtime(pythonnet: Any) -> None:
    get_runtime_info = getattr(pythonnet, "get_runtime_info", None)
    if callable(get_runtime_info):
        get_runtime_info()


def ensure_assemblies_loaded(
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
