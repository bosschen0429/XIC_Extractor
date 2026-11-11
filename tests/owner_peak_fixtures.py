"""Build the captured peak that a test Alignment owner holds."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.peak_detection.capture import (
    SampleLocalCapture,
    bind_owner_backed_capture_hypothesis,
    mint_captured_peak_observation,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    compose_ms1_observation_peak_hypothesis,
)
from xic_extractor.peak_detection.search_scope import (
    SearchScope,
    require_capture_binding,
)


def measured_peak(
    *,
    mz: float,
    apex_rt: float,
    start_rt: float | None = None,
    end_rt: float | None = None,
    area: float = 1_000.0,
    height: float = 100.0,
    sample_key: str = "test-sample",
    intensity: tuple[float, ...] | None = None,
    **integration: Any,
) -> PeakObservation:
    """One selected peak whose integration carries the given measured values."""

    left = apex_rt - 0.05 if start_rt is None else start_rt
    right = apex_rt + 0.05 if end_rt is None else end_rt
    rt = tuple(sorted({left, apex_rt, right}))
    values = {
        "raw_apex_rt_min": apex_rt,
        "height_smoothed": height,
        **integration,
    }
    return PeakObservation(
        sample_key=sample_key,
        mz=mz,
        rt=rt,
        intensity=intensity
        if intensity is not None
        else tuple(height if point == apex_rt else 1.0 for point in rt),
        integration=IntegrationResult(
            rt_left_min=left,
            rt_apex_min=apex_rt,
            rt_right_min=right,
            rt_width_min=right - left,
            height_raw=height,
            area_raw_counts_seconds=area,
            raw_scan_indices=tuple(range(len(rt))),
            **values,
        ),
        generation_provenance=None,  # type: ignore[arg-type]
        source_trace=None,  # type: ignore[arg-type]
    )


def with_peak(
    owner: SampleLocalMS1Owner,
    *,
    mz: float | None = None,
    apex_rt: float | None = None,
    start_rt: float | None = None,
    end_rt: float | None = None,
    area: float | None = None,
    height: float | None = None,
    **integration: Any,
) -> SampleLocalMS1Owner:
    """The owner with only the given measured values of its peak changed."""

    peak = owner.peak
    old = peak.integration
    apex = old.rt_apex_min if apex_rt is None else apex_rt
    left = old.rt_left_min if start_rt is None else start_rt
    right = old.rt_right_min if end_rt is None else end_rt
    rt, intensity = peak.rt, peak.intensity
    if not rt[0] <= apex <= rt[-1]:
        # A moved apex needs a selected trace that contains it.
        rt = tuple(sorted({left, apex, right}))
        intensity = tuple(old.height_raw if point == apex else 1.0 for point in rt)
    changed = replace(
        old,
        rt_left_min=left,
        rt_apex_min=apex,
        rt_right_min=right,
        raw_apex_rt_min=old.raw_apex_rt_min if apex_rt is None else apex,
        rt_width_min=right - left,
        area_raw_counts_seconds=(old.area_raw_counts_seconds if area is None else area),
        height_raw=old.height_raw if height is None else height,
        height_smoothed=old.height_smoothed if height is None else height,
        raw_scan_indices=tuple(range(len(rt))),
        **integration,
    )
    return replace(
        owner,
        peak=replace(
            peak,
            mz=peak.mz if mz is None else mz,
            rt=rt,
            intensity=intensity,
            integration=changed,
        ),
    )


def bound_capture(
    capture: SampleLocalCapture, *, scope: SearchScope
) -> SampleLocalCapture:
    """Bind a capture's selected peak as its measured hypothesis, as runtime does."""

    if capture.selected_hypothesis is None:
        return capture
    sample_key = require_capture_binding(scope, capture.candidate_id).sample_key
    sample = next(item for item in scope.samples if item.sample_name == sample_key)
    observation = mint_captured_peak_observation(capture, sample=sample)
    return bind_owner_backed_capture_hypothesis(
        capture,
        observation=observation,
        hypothesis=compose_ms1_observation_peak_hypothesis(
            peak_observation=observation
        ),
    )
