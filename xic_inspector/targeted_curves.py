"""Project acquired Targeted XICs and saved workbook facts into Inspector curves."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import TYPE_CHECKING

from xic_extractor.alignment_explanation.errors import ExplanationError
from xic_inspector.sources.curve_bundle import (
    ChromatogramCurve,
    CurveBundle,
    CurveBundleTrace,
    PeakBounds,
    TransitionCurves,
)
from xic_inspector.sources.targeted_workbook import TargetedWorkbookSource, saved_number

if TYPE_CHECKING:
    from xic_extractor.acquisition.review_xics import AcquiredReviewXIC


def targeted_curve_bundle(
    source: TargetedWorkbookSource,
    target_id: str,
    signals: Iterable[AcquiredReviewXIC],
    *,
    padding_min: float = 0.5,
) -> CurveBundle:
    """Saved values are overlays, not new peak decisions or formal ND evidence."""
    if not math.isfinite(padding_min) or padding_min <= 0:
        raise ExplanationError("Curve padding must be positive and finite")
    targets = {row["Label"]: row for row in source.targets}
    traces = []
    reference_rts: list[float] = []
    for signal in signals:
        sample, label = signal.sample, signal.target
        spec = targets[label]
        record = source.curve_record(sample, label)
        saved_rt = saved_number(record.get("RT"))
        expected = [saved_number(spec.get(k)) for k in ("RT min", "RT max")]
        reference_rts.extend(v for v in [saved_rt, *expected] if v is not None)
        bounds = None
        start, end = (saved_number(record.get(k)) for k in ("PeakStart", "PeakEnd"))
        if saved_rt is not None and start is not None and end is not None:
            if start <= saved_rt <= end:
                bounds = PeakBounds(start, saved_rt, end)
        prepared = signal.prepared
        traces.append(
            CurveBundleTrace(
                trace_id=f"targeted:{sample}:{label}",
                sample_id=sample,
                sample_label=sample,
                role=f"{spec['Role']} · {label}",
                raw_identity=signal.acquisition_source_id,
                mz=signal.mz,
                ppm=signal.ppm,
                extraction_rt_window=signal.rt_window,
                producer="targeted_review_xic",
                producer_version="1",
                raw=ChromatogramCurve(
                    tuple(map(float, signal.trace.rt)),
                    tuple(map(float, signal.trace.intensity)),
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
                    "reference_support_failures": list(signal.support_failures),
                    "support_reference_rt": source.paired_reference_rt(sample, label),
                    "source_binding_basis": "workbook_sample_name_unverified",
                    "original_acquisition_verified": False,
                    "workbook_sha256": source.locator.workbook_sha256,
                },
            )
        )
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
    return CurveBundle(
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
