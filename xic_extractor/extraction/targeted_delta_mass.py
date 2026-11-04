"""Acquire bounded Targeted channels and reuse the pure Delta Mass matcher.

The declared Target m/z is the quantitation position, not necessarily the
lowest-mass position. Only that position seeds the bounded search. Feature
masses denote XIC query windows, not measured centroid mass estimates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from xic_extractor.configuration.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.discovery.delta_mass_patterns import (
    complete_delta_mass_candidates,
    prepare_delta_mass_candidates,
)
from xic_extractor.discovery.detected_features import DetectedFeature
from xic_extractor.peak_detection.physical_peak_candidate_adapter import (
    answer_selected_physical_candidate,
    physical_candidates_result,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    enumerate_physical_segment_universe,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PHYSICAL_PEAK_ENVELOPE_POLICY,
)
from xic_extractor.xic_models import XICRequest

if TYPE_CHECKING:
    from xic_extractor.extraction.targeted_peak_selection import (
        TargetedCandidateAssessment,
    )


def assess_targeted_delta_mass(
    raw, assessment: TargetedCandidateAssessment, settings, quant_position
):
    """Retain a pattern answer for each measured quantitation-channel candidate."""
    target = assessment.target
    if not any(
        evaluation.state == "available"
        for evaluation in assessment.candidate_evaluations.values()
    ):
        return {candidate: () for candidate in assessment.candidate_evaluations}
    # A bounded Target search has a known quantitation channel. Other positions
    # provide evidence, never independent targets or quantitation anchors.
    quant_delta = next(
        p.delta_da for p in settings.positions if p.label == quant_position
    )
    base_mz = target.mz - quant_delta
    trace = assessment.trace_group.primary_trace
    profile = resolve_signal_processing_profile(
        signal_processing_method=assessment.config.signal_processing_method,
        signal_processing_window_points=assessment.config.signal_processing_window_points,
    )
    partners = [p for p in settings.positions if p.label != quant_position]
    requests = tuple(
        XICRequest(base_mz + p.delta_da, trace.rt_min, trace.rt_max, target.ppm_tol)
        for p in partners
    )
    traces = raw.extract_xic_many(requests)
    features = []
    candidates_by_position = {quant_position: tuple(assessment.candidate_evaluations)}
    evaluations_by_position = {quant_position: assessment.candidate_evaluations}
    for position, acquired in zip(partners, traces, strict=True):
        prepared = prepare_physical_signal(
            acquired.rt, acquired.intensity, profile=profile
        )
        universe = enumerate_physical_segment_universe(
            prepared,
            selection_policy_version=PHYSICAL_PEAK_ENVELOPE_POLICY,
            request_context_rt_start_min=trace.rt_min,
            request_context_rt_end_min=trace.rt_max,
        )
        candidates = physical_candidates_result(
            prepared, universe, selection_policy_version=PHYSICAL_PEAK_ENVELOPE_POLICY
        ).candidates
        candidates_by_position[position.label] = candidates
        evaluations_by_position[position.label] = {
            candidate: answer_selected_physical_candidate(
                prepared,
                universe,
                candidate,
                acquisition_rt_window=(trace.rt_min, trace.rt_max),
                selection_rt_window=(trace.rt_min, trace.rt_max),
                selection_policy_version=PHYSICAL_PEAK_ENVELOPE_POLICY,
            )
            for candidate in candidates
        }
    for position in settings.positions:
        for index, candidate in enumerate(candidates_by_position[position.label]):
            evaluation = evaluations_by_position[position.label][candidate]
            if evaluation.state != "available":
                continue
            features.append(
                DetectedFeature(
                    sample_stem=trace.sample_name,
                    feature_id=f"{position.label}:{index}",
                    source_feature_row_id=None,
                    mz=base_mz + position.delta_da,
                    rt_apex_min=candidate.selection_apex_rt,
                    rt_start_min=candidate.peak.peak_start,
                    rt_end_min=candidate.peak.peak_end,
                    height=candidate.peak.intensity,
                    area=candidate.peak.area,
                    trace_quality="OK",
                    shape_score=None,
                    source="targeted_xic_window",
                )
            )
    intervals = np.diff(np.asarray(trace.rt, dtype=float)) * 60
    interval = (
        float(np.median(intervals[intervals > 0])) if np.any(intervals > 0) else 0.0
    )
    pattern_preparation = prepare_delta_mass_candidates(
        features, settings, scan_interval_sec=interval
    )
    confirmation = (
        raw.extract_xic_many(pattern_preparation.requests)
        if pattern_preparation.requests
        else ()
    )
    hypotheses = complete_delta_mass_candidates(
        pattern_preparation, confirmation, settings
    )
    # An XIC window establishes bounded mass support, not a zero-error centroid.
    for hypothesis in hypotheses:
        for measured_position in hypothesis.positions:
            measured_position.observed_mz = None
            measured_position.mz_error_ppm = None
        _require_coelution_with_quantitation(hypothesis, quant_position)
    return {
        candidate: tuple(
            h for h in hypotheses if h.anchor_feature_id == f"{quant_position}:{index}"
        )
        for index, candidate in enumerate(assessment.candidate_evaluations)
    }


def _require_coelution_with_quantitation(hypothesis, quant_position) -> None:
    """Measure co-elution from the quantitation ion, not the lowest-mass ion.

    The shared matcher re-anchors its RT reference to the lowest-mass position;
    a Targeted pattern is defined relative to the quantitation peak instead.
    """
    if hypothesis.status != "candidate":
        return
    quant_apex = next(
        p.apex_rt_min
        for p in hypothesis.positions
        if p.position_label == quant_position
    )
    if quant_apex is None:
        return
    for position in hypothesis.positions:
        if position.apex_rt_min is None:
            continue
        if abs(position.apex_rt_min - quant_apex) * 60 > position.apex_threshold_sec:
            position.position_status = "fail"
            hypothesis.status = "rejected"
            hypothesis.rejected_at_layer = 4
            hypothesis.reject_reason = "apex_out_of_range"
