"""Review curves of one Untargeted item: saved curves, or a new read from RAW.

A cell with a value shows the curve its run saved; smoothing and baseline are
recomputed with the run's signal-processing settings. Other samples are read
from the RAW file of the same name, with the run's mass recalibration.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

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
    ChromatogramCurve,
    CurveBundle,
    CurveBundleTrace,
    SampleQuantification,
    TransitionCurves,
)

RUN_METADATA_NAME = "alignment_run_metadata.json"
SAVED_ROLE = "分析時保存的曲線"


class CurveAcquisitionCancelled(Exception):
    """The caller cancelled curve reading."""


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
    is_cancelled: Callable[[], bool] = lambda: False,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[CurveBundle, list[str]]:
    """Saved curves of the requested samples, and RAW reads for the rest.

    Returns the bundle and the samples whose window has no usable signal.
    """
    requested = set(sample_ids)
    selected = [loc for loc in curves.locations if loc.sample_key in requested]
    settings = RunSettings(report)
    traces, unusable = _saved_traces(
        (loc for loc in selected if loc.curve is not None), settings.profile
    )
    pending = [loc for loc in selected if loc.curve is None]
    if pending:
        if raw_dir is None or dll_dir is None:
            raise ExplanationError("讀取未保存曲線的樣本需要 RAW 與 DLL 資料夾。")
        read, missing = _raw_traces(
            pending,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            settings=settings,
            padding_min=padding_min,
            is_cancelled=is_cancelled,
            on_progress=on_progress,
        )
        traces += read
        unusable |= missing
    represented = {trace.sample_id for trace in traces}
    quantities = tuple(
        SampleQuantification(sid, value, "counts·s", origin)
        for _, sid, value, origin, _ in read_row_cell_values(report, {key})
        if sid in represented
    )
    return _curve_bundle(key, curves, traces, quantities), sorted(unusable)


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
            unusable.add(location.sample_stem)
        else:
            traces.append(trace)
    return traces, unusable


def _raw_traces(
    locations: Iterable[ReviewCurveLocation],
    *,
    raw_dir: Path,
    dll_dir: Path,
    settings: RunSettings,
    padding_min: float,
    is_cancelled: Callable[[], bool],
    on_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[list[CurveBundleTrace], set[str]]:
    """Read locations from the RAW of the same name, around each recorded apex."""
    by_stem: dict[str, list[ReviewCurveLocation]] = {}
    for location in locations:
        by_stem.setdefault(location.sample_stem, []).append(location)
    traces: list[CurveBundleTrace] = []
    unusable: set[str] = set()
    for completed, (stem, sample_locations) in enumerate(sorted(by_stem.items())):
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
                # The run's own result, instead of probing every scan again.
                raw.use_mass_recalibration_result(saved)
            acquired = len(traces)
            for location in sample_locations:
                _check_cancel(is_cancelled)
                lower = max(0.0, location.rt_apex_min - padding_min)
                upper = location.rt_apex_min + padding_min
                rt, intensity = raw.extract_xic(location.mz, lower, upper, location.ppm)
                trace = _trace(
                    location,
                    rt,
                    intensity,
                    settings.profile,
                    role="diagnostic_reacquisition",
                    flags={
                        "new_diagnostic_extraction": True,
                        "acquired_at_group_position": location.physical_peak_key
                        is None,
                    },
                    window=(lower, upper),
                )
                if trace is not None:
                    traces.append(trace)
        if len(traces) == acquired:
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
    if not traces:
        raise ExplanationError("所選樣本在擷取範圍內都沒有可用訊號。")
    window = (
        min(trace.extraction_rt_window[0] for trace in traces),
        max(trace.extraction_rt_window[1] for trace in traces),
    )
    read_from_raw = any(
        trace.quality_flags.get("new_diagnostic_extraction") for trace in traces
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
            f"review:{location.sample_key}:"
            f"{location.physical_peak_key or 'group-position'}"
        ),
        sample_id=location.sample_key,
        sample_label=location.sample_stem,
        role=role,
        raw_identity=location.sample_stem,
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
        raise CurveAcquisitionCancelled("Curve acquisition cancelled")
