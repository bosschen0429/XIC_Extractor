from __future__ import annotations

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.baseline import asls_baseline
from xic_extractor.peak_detection.chrom_peak_segments import (
    ChromPeakSegment,
    ChromPeakSegmentPolicy,
    enumerate_chrom_peak_segments,
)
from xic_extractor.peak_detection.integration import (
    integrate_area_counts_seconds,
    raw_apex_index,
)
from xic_extractor.peak_detection.models import PeakCandidate, PeakResult
from xic_extractor.peak_detection.ms1_morphology import (
    configured_morphology_window_points,
)

_PRODUCT_SEGMENT_CLASSES = {"isolated_peak", "separate_peak"}
_PROPOSAL_SOURCE = "chrom_peak_segment"


def chrom_peak_segment_candidates(
    rt: np.ndarray,
    intensity: np.ndarray,
    config: ExtractionConfig,
) -> tuple[PeakCandidate, ...]:
    rt_values = np.asarray(rt, dtype=float)
    intensity_values = np.asarray(intensity, dtype=float)
    if rt_values.ndim != 1 or intensity_values.ndim != 1:
        return ()
    if len(rt_values) != len(intensity_values) or len(rt_values) < 2:
        return ()
    baseline = asls_baseline(intensity_values)
    enumeration = enumerate_chrom_peak_segments(
        rt_values,
        intensity_values,
        baseline,
        policy=_policy_from_config(config),
    )
    if enumeration.status != "OK":
        return ()
    return tuple(
        _candidate_from_segment(
            rt_values,
            intensity_values,
            segment,
            source_apex_rank=index + 1,
        )
        for index, segment in enumerate(enumeration.segments)
        if segment.segment_class in _PRODUCT_SEGMENT_CLASSES
    )


def is_chrom_peak_segment_candidate(candidate: PeakCandidate) -> bool:
    return _PROPOSAL_SOURCE in candidate.proposal_sources


def _policy_from_config(config: ExtractionConfig) -> ChromPeakSegmentPolicy:
    return ChromPeakSegmentPolicy(
        min_scan_count=max(int(getattr(config, "resolver_min_scans", 3)), 3),
        min_apex_residual=max(
            float(getattr(config, "resolver_min_absolute_height", 5.0)),
            5.0,
        ),
        min_apex_fraction_of_context=max(
            float(getattr(config, "resolver_min_relative_height", 0.05)),
            0.0,
        ),
        morphology_trace_method="gaussian_15",
        morphology_trace_window_points=configured_morphology_window_points(config),
    )


def _candidate_from_segment(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    segment: ChromPeakSegment,
    *,
    source_apex_rank: int,
) -> PeakCandidate:
    left = segment.interval.start_index
    right = segment.interval.end_index
    raw_apex_idx = raw_apex_index(intensity_values, left, right)
    area = integrate_area_counts_seconds(intensity_values, rt_values, left, right)
    raw_apex = float(intensity_values[raw_apex_idx])
    morphology_apex = raw_apex - segment.raw_apex_residual
    morphology_apex += segment.morphology_apex_residual
    peak = PeakResult(
        rt=segment.apex_rt_min,
        intensity=raw_apex,
        intensity_smoothed=float(morphology_apex),
        area=area,
        peak_start=segment.interval.rt_start_min,
        peak_end=segment.interval.rt_end_min,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=segment.apex_rt_min,
        selection_apex_intensity=segment.morphology_apex_residual,
        selection_apex_index=segment.apex_index,
        raw_apex_rt=float(rt_values[raw_apex_idx]),
        raw_apex_intensity=raw_apex,
        raw_apex_index=raw_apex_idx,
        prominence=segment.raw_apex_residual,
        region_scan_count=segment.interval.scan_count,
        region_duration_min=(
            segment.interval.rt_end_min - segment.interval.rt_start_min
        ),
        proposal_sources=(_PROPOSAL_SOURCE,),
        source_apex_rank=source_apex_rank,
        merge_note=f"{segment.segment_class}:{segment.boundary_stop_reason}",
    )
