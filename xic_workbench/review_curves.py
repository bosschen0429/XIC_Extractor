"""Review curves of one Untargeted item: saved curves, or a new read from RAW.

A cell with a value shows the curve its run saved; smoothing and baseline are
recomputed with the run's signal-processing settings. Other samples are read
from the RAW file of the same name, with the run's mass recalibration.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from xic_extractor.acquisition.review_xics import ReviewAcquisitionCancelled
from xic_extractor.alignment_explanation.errors import ExplanationError
from xic_extractor.alignment_explanation.reader import (
    ReviewCurveLocation,
    ReviewCurves,
    read_row_cell_values,
)
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    MassRecalibrationResult,
    deserialize_mass_recalibration_result,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
    deserialize_signal_processing_profile,
    resolve_signal_processing_profile,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)
from xic_extractor.raw_reader import open_raw
from xic_inspector.sources.curve_bundle import (
    SAME_MZ_ROLE,
    ChromatogramCurve,
    CurveBundle,
    CurveBundleTrace,
    CurveIntegration,
    SampleQuantification,
    TransitionCurves,
)

RUN_METADATA_NAME = "alignment_run_metadata.json"
SAVED_ROLE = "分析時保存的曲線"
PUBLISHED_VALUE_METHOD = "published_matrix_value"


class _RawRead:
    """One RAW window to read: around a location, or across a row's m/z."""

    def __init__(
        self,
        location: ReviewCurveLocation,
        window: tuple[float, float],
        same_mz: bool = False,
    ) -> None:
        self.location = location
        self.window = window
        self.same_mz = same_mz


class RunSettings:
    """The run's signal-processing profile, mass recalibration and RAW folder."""

    def __init__(self, report: Path) -> None:
        path = report.parent / RUN_METADATA_NAME
        metadata: dict[str, Any] = (
            json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        )
        profile = metadata.get("signal_processing_profile_json")
        self.profile: SignalProcessingProfile = (
            deserialize_signal_processing_profile(profile)
            if profile is not None
            else resolve_signal_processing_profile()
        )
        recalibration = metadata.get("mass_recalibration_by_sample")
        self.mass_results: dict[str, MassRecalibrationResult] = (
            {
                stem: deserialize_mass_recalibration_result(json.dumps(result))
                for stem, result in json.loads(recalibration).items()
            }
            if recalibration is not None
            else {}
        )
        raw_dir = metadata.get("raw_dir")
        self.raw_dir = Path(raw_dir) if raw_dir else None


def review_curve_bundle(
    report: Path,
    curves: ReviewCurves,
    *,
    key: str,
    sample_ids: Iterable[str],
    raw_dir: Path | None = None,
    dll_dir: Path | None = None,
    padding_min: float = 0.5,
    same_mz_curves: bool = False,
    is_cancelled: Callable[[], bool] = lambda: False,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[CurveBundle, list[str]]:
    """Saved curves of the requested samples, and RAW reads for the rest.

    With ``same_mz_curves`` and a RAW folder, every sample with a value at this
    m/z also gets one curve spanning all rows' apexes there.
    Returns the bundle and the samples whose window has no usable signal.
    """
    requested = set(sample_ids)
    selected = [loc for loc in curves.locations if loc.raw_stem in requested]
    settings = RunSettings(report)
    traces, unusable = _saved_traces(
        (loc for loc in selected if loc.curve is not None), settings.profile
    )
    reads = [
        _RawRead(
            loc,
            (max(0.0, loc.rt_apex_min - padding_min), loc.rt_apex_min + padding_min),
        )
        for loc in selected
        if loc.curve is None
    ]
    if reads and (raw_dir is None or dll_dir is None):
        raise ExplanationError("讀取未保存曲線的樣本需要 RAW 與 DLL 資料夾。")
    if same_mz_curves and raw_dir is not None and dll_dir is not None:
        reads += _same_mz_reads(curves, padding_min)
    if reads:
        assert raw_dir is not None and dll_dir is not None
        read, missing = _raw_traces(
            reads,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            settings=settings,
            is_cancelled=is_cancelled,
            on_progress=on_progress,
        )
        traces += read
        unusable |= missing
    represented = {trace.sample_id for trace in traces if trace.role != SAME_MZ_ROLE}
    cells = [
        (sid, value, origin, apex)
        for _, sid, value, origin, apex in read_row_cell_values(report, {key})
        if sid in represented
    ]
    quantities = tuple(
        SampleQuantification(sid, value, "counts·s", origin)
        for sid, value, origin, _ in cells
    )
    traces = _with_published_values(traces, cells)
    return _curve_bundle(key, curves, traces, quantities), sorted(unusable)


def _same_mz_reads(curves: ReviewCurves, padding_min: float) -> list[_RawRead]:
    """One read per sample at the row's m/z, across every row's apex there."""
    if not curves.same_mz_peaks or curves.row_mz is None or curves.row_ppm is None:
        return []
    apexes = [peak.apex_rt_min for peak in curves.same_mz_peaks]
    window = (max(0.0, min(apexes) - padding_min), max(apexes) + padding_min)
    samples = {peak.raw_stem: peak.sample_label for peak in curves.same_mz_peaks}
    return [
        _RawRead(
            ReviewCurveLocation(
                key, label, None, curves.row_mz, curves.row_ppm, window[0], None
            ),
            window,
            same_mz=True,
        )
        for key, label in sorted(samples.items())
    ]


def _with_published_values(
    traces: list[CurveBundleTrace],
    cells: Iterable[tuple[str, float | None, str, float | None]],
) -> list[CurveBundleTrace]:
    """Attach each value's saved area and apex to the curve that gave it.

    The saved curve of a cell is the window of the peak that gave its value, so
    the reviewer can see which peak was counted. Nothing is integrated here.
    """
    published = {
        sid: CurveIntegration(value, apex, PUBLISHED_VALUE_METHOD)
        for sid, value, _, apex in cells
        if value is not None and apex is not None
    }
    return [
        replace(trace, integration=published[trace.sample_id])
        if trace.role == SAVED_ROLE and trace.sample_id in published
        else trace
        for trace in traces
    ]


def _saved_traces(
    locations: Iterable[ReviewCurveLocation], profile: SignalProcessingProfile
) -> tuple[list[CurveBundleTrace], set[str]]:
    """Traces of saved curves, and the samples whose curve has no usable signal."""
    traces: list[CurveBundleTrace] = []
    unusable: set[str] = set()
    for location in locations:
        assert location.curve is not None
        rt, raw = location.curve
        trace = _trace(location, rt, raw, profile, role=SAVED_ROLE, flags={})
        if trace is None:
            unusable.add(location.raw_stem)
        else:
            traces.append(trace)
    return traces, unusable


def _raw_traces(
    reads: Iterable[_RawRead],
    *,
    raw_dir: Path,
    dll_dir: Path,
    settings: RunSettings,
    is_cancelled: Callable[[], bool],
    on_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[list[CurveBundleTrace], set[str]]:
    """Read each window from the RAW of the same name, opening each RAW once.

    A sample is unusable when none of its review windows has signal.
    """
    by_stem: dict[str, list[_RawRead]] = {}
    for read in reads:
        by_stem.setdefault(read.location.raw_stem, []).append(read)
    traces: list[CurveBundleTrace] = []
    unusable: set[str] = set()
    for completed, (stem, sample_reads) in enumerate(sorted(by_stem.items())):
        _check_cancel(is_cancelled)
        raw_path = raw_dir / f"{stem}.raw"
        if not raw_path.is_file():
            raise ExplanationError(f"RAW 資料夾裡找不到 {stem}.raw")
        saved = settings.mass_results.get(stem)
        with open_raw(
            raw_path,
            dll_dir,
            mass_recalibration=MassRecalibration.NONE
            if saved is None
            else saved.policy,
        ) as raw:
            if saved is not None:
                raw.use_mass_recalibration_result(saved)
            usable = False
            for read in sample_reads:
                _check_cancel(is_cancelled)
                location = read.location
                lower, upper = read.window
                rt, intensity = raw.extract_xic(location.mz, lower, upper, location.ppm)
                trace = _trace(
                    location,
                    rt,
                    intensity,
                    settings.profile,
                    role=SAME_MZ_ROLE if read.same_mz else "diagnostic_reacquisition",
                    flags={"new_diagnostic_extraction": True}
                    if read.same_mz
                    else {
                        "new_diagnostic_extraction": True,
                        "acquired_at_group_position": location.physical_peak_key
                        is None,
                    },
                    window=read.window,
                )
                if trace is None:
                    continue
                if read.same_mz:
                    trace = replace(trace, trace_id=f"same-mz:{location.raw_stem}")
                else:
                    usable = True
                traces.append(trace)
        if not usable and any(not read.same_mz for read in sample_reads):
            unusable.add(stem)
        if on_progress is not None:
            on_progress(completed + 1, len(by_stem), stem)
    return traces, unusable


def _curve_bundle(
    key: str,
    curves: ReviewCurves,
    traces: list[CurveBundleTrace],
    quantities: tuple[SampleQuantification, ...],
) -> CurveBundle:
    reviewed = [trace for trace in traces if trace.role != SAME_MZ_ROLE]
    if not reviewed:
        raise ExplanationError("所選樣本在擷取範圍內都沒有可用訊號。")
    window = (
        min(trace.extraction_rt_window[0] for trace in reviewed),
        max(trace.extraction_rt_window[1] for trace in reviewed),
    )
    read_from_raw = any(
        trace.quality_flags.get("new_diagnostic_extraction") for trace in reviewed
    )
    return CurveBundle(
        (
            TransitionCurves(
                key=key,
                review_rt_window=window,
                quality_flags={"new_diagnostic_extraction": True}
                if read_from_raw
                else {},
                traces=tuple(sorted(traces, key=lambda trace: trace.trace_id)),
                quantifications=quantities,
                same_mz_peaks=curves.same_mz_peaks,
            ),
        ),
        sample_rt_calibration=curves.sample_rt_calibration,
        injection_order=curves.injection_order,
    )


def _trace(
    location: ReviewCurveLocation,
    rt: Any,
    intensity: Any,
    profile: SignalProcessingProfile,
    *,
    role: str,
    flags: dict[str, Any],
    window: tuple[float, float] | None = None,
) -> CurveBundleTrace | None:
    prepared = prepare_physical_signal(rt, intensity, profile=profile).prepared
    if prepared is None:
        return None
    raw = ChromatogramCurve(tuple(map(float, rt)), tuple(map(float, intensity)))
    return CurveBundleTrace(
        trace_id=(
            f"review:{location.raw_stem}:"
            f"{location.physical_peak_key or 'group-position'}"
        ),
        sample_id=location.raw_stem,
        sample_label=location.sample_label,
        role=role,
        raw_identity=location.raw_stem,
        mz=location.mz,
        ppm=location.ppm,
        extraction_rt_window=window or (raw.rt[0], raw.rt[-1]),
        producer="explanation_review_curve",
        producer_version="1",
        raw=raw,
        gaussian15=ChromatogramCurve(prepared.rt, prepared.smoothed),
        baseline=ChromatogramCurve(prepared.rt, prepared.baseline),
        positive_signal=ChromatogramCurve(prepared.rt, prepared.positive_signal),
        quality_flags=flags,
    )


def _check_cancel(is_cancelled: Callable[[], bool]) -> None:
    if is_cancelled():
        raise ReviewAcquisitionCancelled("Curve acquisition cancelled")
