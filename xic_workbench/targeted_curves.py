"""Read-only, on-demand Targeted and paired-ISTD XIC acquisition."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, cast

from xic_extractor.analysis_set import discover_raw_files
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    resolve_mass_recalibration,
)
from xic_extractor.configuration.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.diagnostics.alignment_explanation import ExplanationError
from xic_extractor.peak_detection.physical_peak_evaluation import (
    enumerate_physical_segment_universe,
    physical_peak_support_failures_at_reference,
    prepare_physical_signal,
)
from xic_extractor.processing_identity import (
    FingerprintCancelledError,
    build_acquisition_source_identity,
)
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    open_raw,
)
from xic_inspector.sources.curve_bundle import (
    ChromatogramCurve,
    CurveBundle,
    CurveBundleTrace,
    PeakBounds,
    TransitionCurves,
    write_curve_bundle,
)
from xic_inspector.sources.targeted_workbook import (
    TargetedWorkbookLocator,
    TargetedWorkbookSource,
)
from xic_workbench.acquisition import CurveAcquisitionCancelled


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(value) else None
    return None


def export_targeted_curves(
    report: Path,
    output: Path,
    *,
    raw_dir: Path,
    dll_dir: Path,
    target_id: str,
    sample_ids: tuple[str, ...],
    padding_min: float = 0.5,
    ppm: float = 5.0,
    expected_source: TargetedWorkbookLocator | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Acquire curves, never select peaks, integrate, or update the workbook.

    XLSX stores sample names but not RAW fingerprints. New RAW identities bind
    this diagnostic read only; they cannot certify the original acquisition.
    Full-trace preparation preserves signal-processing context before display
    zoom, and saved peak bounds are overlays rather than recomputed decisions.
    """
    if not math.isfinite(padding_min + ppm) or padding_min <= 0 or ppm <= 0:
        raise ExplanationError("Curve padding and ppm must be positive and finite")
    if output.resolve() == report.resolve() or output.exists():
        raise FileExistsError(f"Curve output must be a new file: {output}")
    source = TargetedWorkbookSource(report)
    if expected_source is not None and source.locator != expected_source:
        raise ExplanationError("Targeted 結果已變更，請重新開啟後再取得 XIC。")
    targets = {row["Label"]: row for row in source.targets}
    if target_id not in targets:
        raise ExplanationError("所選 Target 不在這份工作簿。")
    target = targets[target_id]
    selected_targets = [target]
    pair = target.get("ISTD Pair")
    if pair:
        if pair not in targets:
            raise ExplanationError("工作簿缺少配對 ISTD 的目標設定。")
        selected_targets.append(targets[pair])
    records = {(r["SampleName"], r["Target"]): r for r in source.records}
    samples = tuple(dict.fromkeys(sample_ids))
    if not samples or any((sample, target_id) not in records for sample in samples):
        raise ExplanationError("所選樣本沒有此 Target 的結果紀錄。")
    metadata = dict(cast(list[tuple[str, Any]], source.summary["method_context"]))
    mass_policy = resolve_mass_recalibration(
        metadata.get("mass_recalibration", MassRecalibration.NONE)
    )
    profile = resolve_signal_processing_profile(
        signal_processing_method=metadata.get("signal_processing_method") or "gaussian",
        signal_processing_window_points=metadata.get("signal_processing_window_points")
        or 15,
    )
    traces = []
    reference_rts: list[float] = []
    raw_sources = {}

    def check_cancel() -> None:
        if is_cancelled is not None and is_cancelled():
            raise CurveAcquisitionCancelled("已取消曲線載入。")

    check_cancel()
    paths_by_stem: dict[str, list[Path]] = {}
    for sample in samples:
        if Path(sample).name != sample or sample in {".", ".."}:
            raise ExplanationError("工作簿樣本名稱不可含路徑。")
        paths_by_stem[sample.casefold()] = []
    try:
        raw_paths = discover_raw_files(raw_dir)
    except ValueError as exc:
        raise ExplanationError(str(exc)) from exc
    for path in raw_paths:
        if path.stem.casefold() in paths_by_stem:
            paths_by_stem[path.stem.casefold()].append(path)
    selected_paths = {}
    for sample in samples:
        matches = paths_by_stem[sample.casefold()]
        if not matches:
            raise ExplanationError(
                f"找不到所選樣本的 RAW：{sample}（搜尋根目錄：{raw_dir}）"
            )
        if len(matches) > 1:
            raise ExplanationError(
                f"樣本 {sample} 對應多個 RAW，請選擇唯一對應的資料夾："
                + "、".join(str(path) for path in matches)
            )
        selected_paths[sample] = matches[0]

    for index, sample in enumerate(samples):
        check_cancel()
        path = selected_paths[sample]
        try:
            identity = build_acquisition_source_identity(
                path,
                acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
                observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
                should_cancel=is_cancelled,
            )
        except FingerprintCancelledError as exc:
            raise CurveAcquisitionCancelled("已取消曲線載入。") from exc
        raw_sources[sample] = identity.acquisition_source_id
        with open_raw(
            path,
            dll_dir,
            expected_acquisition_source=identity,
            mass_recalibration=mass_policy,
        ) as raw:
            lower, upper = raw.acquisition_rt_bounds()
            for spec in selected_targets:
                check_cancel()
                label = spec["Label"]
                mz = _number(spec.get("m/z"))
                tolerance = _number(spec.get("ppm tol", ppm))
                if mz is None or mz <= 0 or tolerance is None or tolerance <= 0:
                    raise ExplanationError(f"{label} 缺少有效的 m/z 或 ppm 設定。")
                record = records.get((sample, label), {})
                saved_rt = _number(record.get("RT"))
                expected = [_number(spec.get(k)) for k in ("RT min", "RT max")]
                reference_rts.extend(v for v in [saved_rt, *expected] if v is not None)
                rt, intensity = raw.extract_xic(mz, lower, upper, tolerance)
                prepared_result = prepare_physical_signal(
                    rt, intensity, profile=profile
                )
                prepared = prepared_result.prepared
                if prepared is None:
                    raise ExplanationError(
                        f"{sample} / {label} 沒有可繪製的 XIC 資料。"
                    )
                reference = records.get((sample, spec.get("ISTD Pair")), {})
                reference_rt = _number(reference.get("RT"))
                support_failures: tuple[str, ...] = ()
                if (
                    str(record.get("Counted Detection")).upper() == "FALSE"
                    and str(reference.get("Counted Detection")).upper() == "TRUE"
                    and reference_rt is not None
                ):
                    support_failures = physical_peak_support_failures_at_reference(
                        prepared,
                        enumerate_physical_segment_universe(prepared_result),
                        reference_rt,
                    )
                bounds = None
                start, end = (_number(record.get(k)) for k in ("PeakStart", "PeakEnd"))
                if saved_rt is not None and start is not None and end is not None:
                    if start <= saved_rt <= end:
                        bounds = PeakBounds(start, saved_rt, end)
                traces.append(
                    CurveBundleTrace(
                        trace_id=f"targeted:{sample}:{label}",
                        sample_id=sample,
                        sample_label=sample,
                        role=f"{spec['Role']} · {label}",
                        raw_identity=identity.acquisition_source_id,
                        mz=mz,
                        ppm=tolerance,
                        extraction_rt_window=(lower, upper),
                        producer="targeted_review_xic",
                        producer_version="1",
                        raw=ChromatogramCurve(
                            tuple(map(float, rt)), tuple(map(float, intensity))
                        ),
                        gaussian15=ChromatogramCurve(prepared.rt, prepared.smoothed),
                        baseline=ChromatogramCurve(prepared.rt, prepared.baseline),
                        positive_signal=ChromatogramCurve(
                            prepared.rt, prepared.positive_signal
                        ),
                        peak_bounds=bounds,
                        quality_flags={
                            "target_label": label,
                            "target_role": spec["Role"],
                            "paired_istd": label != target_id,
                            "new_diagnostic_extraction": True,
                            "saved_area": record.get("Area"),
                            "saved_counted_detection": record.get("Counted Detection"),
                            "reference_support_failures": list(support_failures),
                            "support_reference_rt": reference_rt,
                            "source_binding_basis": "workbook_sample_name_unverified",
                            "original_acquisition_verified": False,
                            "workbook_sha256": source.locator.workbook_sha256,
                        },
                    )
                )
        if on_progress is not None:
            on_progress(index + 1, len(samples), sample)
    check_cancel()
    extent = (
        min(t.extraction_rt_window[0] for t in traces),
        max(t.extraction_rt_window[1] for t in traces),
    )
    review_window = (
        (
            max(extent[0], min(reference_rts) - padding_min),
            min(extent[1], max(reference_rts) + padding_min),
        )
        if reference_rts
        else extent
    )
    if review_window[0] >= review_window[1]:
        review_window = extent
    bundle = CurveBundle(
        (
            TransitionCurves(
                key=target_id,
                review_rt_window=review_window,
                quality_flags={
                    "new_diagnostic_extraction": True,
                    "targeted_pair": True,
                    "source_binding_basis": "workbook_sample_name_unverified",
                },
                traces=tuple(traces),
            ),
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_curve_bundle(bundle, output)
    return {
        "output": str(output.resolve()),
        "sample_count": len(samples),
        "curve_count": len(traces),
        "bytes": output.stat().st_size,
        "acquisition_sources": raw_sources,
        "source_binding_basis": "workbook_sample_name_unverified",
        "original_acquisition_verified": False,
    }
