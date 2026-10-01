from __future__ import annotations

from pathlib import Path

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.instrument_qc.models import InstrumentQCStatus, SDOLEKTrendRow
from xic_extractor.instrument_qc.pipeline_contracts import XICSource
from xic_extractor.instrument_qc.targets import InstrumentQCTarget
from xic_extractor.peak_detection.facade import find_peak_and_area


def extract_target_row(
    *,
    raw: XICSource,
    raw_path: Path,
    sample_name: str,
    injection_order: int | None,
    target: InstrumentQCTarget,
    dll_dir: Path,
) -> SDOLEKTrendRow:
    rt, intensity = raw.extract_xic(
        mz=target.precursor_mz,
        rt_min=target.rt_min,
        rt_max=target.rt_max,
        ppm_tol=target.ppm_tol,
    )
    result = find_peak_and_area(rt, intensity, _peak_config(raw_path.parent, dll_dir))
    if result.peak is None or result.status != "OK":
        return error_row(
            raw_path=raw_path,
            sample_name=sample_name,
            injection_order=injection_order,
            target=target,
            reason=result.status,
            status="not_detected",
        )
    peak = result.peak
    base_width = peak.peak_end - peak.peak_start
    rt_delta = peak.rt - target.reference_rt_min
    width_ratio = (
        base_width / target.reference_base_width_min
        if target.reference_base_width_min
        else None
    )
    flags = _trend_flags(rt_delta=rt_delta, width_ratio=width_ratio)
    return SDOLEKTrendRow(
        sample_name=sample_name,
        raw_path=raw_path,
        injection_order=injection_order,
        compound=target.compound,
        precursor_mz=target.precursor_mz,
        identity_evidence="MS1_ONLY",
        reference_rt_min=target.reference_rt_min,
        rt_delta_to_reference_min=rt_delta,
        apex_rt_min=peak.rt,
        area=peak.area,
        base_width_min=base_width,
        reference_base_width_min=target.reference_base_width_min,
        base_width_ratio_to_reference=width_ratio,
        peak_start_rt_min=peak.peak_start,
        peak_end_rt_min=peak.peak_end,
        trend_confidence="warning" if flags else "clean",
        trend_flags=flags,
        status="detected",
        reason="OK",
    )


def error_row(
    *,
    raw_path: Path,
    sample_name: str,
    injection_order: int | None,
    target: InstrumentQCTarget,
    reason: str,
    status: InstrumentQCStatus = "error",
) -> SDOLEKTrendRow:
    return SDOLEKTrendRow(
        sample_name=sample_name,
        raw_path=raw_path,
        injection_order=injection_order,
        compound=target.compound,
        precursor_mz=target.precursor_mz,
        identity_evidence="MS1_ONLY",
        reference_rt_min=target.reference_rt_min,
        rt_delta_to_reference_min=None,
        apex_rt_min=None,
        area=None,
        base_width_min=None,
        reference_base_width_min=target.reference_base_width_min,
        base_width_ratio_to_reference=None,
        peak_start_rt_min=None,
        peak_end_rt_min=None,
        trend_confidence="low",
        trend_flags=("LOW_PEAK_CONFIDENCE",),
        status=status,
        reason=reason,
    )


def _trend_flags(
    *,
    rt_delta: float,
    width_ratio: float | None,
) -> tuple[str, ...]:
    flags: list[str] = []
    if abs(rt_delta) > 0.50:
        flags.append("RT_OUTLIER")
    if width_ratio is not None and (width_ratio < 0.50 or width_ratio > 1.75):
        flags.append("WIDTH_OUTLIER")
    return tuple(flags)


def _peak_config(data_dir: Path, dll_dir: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=data_dir,
        dll_dir=dll_dir,
        output_csv=Path("instrument_qc.csv"),
        diagnostics_csv=Path("instrument_qc_diagnostics.csv"),
        smooth_window=7,
        smooth_polyorder=2,
        peak_rel_height=0.5,
        peak_min_prominence_ratio=0.05,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        resolver_mode="local_minimum",
    )
