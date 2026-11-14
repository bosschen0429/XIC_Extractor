"""Build the measured peaks that test owners and hypotheses hold."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.observations import (
    PeakObservation,
    prepare_peak_observation_provenance,
)
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.sample_metadata import SampleMetadata


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


def trace_peak(
    *,
    sample: SampleMetadata,
    trace: Trace,
    integration: IntegrationResult,
    resolver_mode: str = "local_minimum",
    proposal_sources: tuple[str, ...] = ("local_minimum",),
    physical_evaluation: PhysicalPeakEvaluation | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
) -> PeakObservation:
    """The peak ``integration`` selects on ``trace`` at its raw scan indices."""

    indices = integration.raw_scan_indices
    return PeakObservation(
        sample_key=sample.sample_name,
        mz=float(trace.mz),
        rt=tuple(float(trace.rt[index]) for index in indices),
        intensity=tuple(float(trace.intensity[index]) for index in indices),
        integration=replace(integration, raw_scan_indices=tuple(range(len(indices)))),
        generation_provenance=prepare_peak_observation_provenance(
            trace=trace,
            selected_indices=indices,
            resolver_mode=resolver_mode,
            proposal_sources=proposal_sources,
        ),
        source_trace=trace,
        physical_evaluation=physical_evaluation,
        signal_processing_profile=signal_processing_profile,
    )
