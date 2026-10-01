from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np

from xic_extractor.peak_detection.baseline import (
    asls_baseline,
    bounded_trace_interval,
    integrate_with_baseline,
)
from xic_extractor.peak_detection.hypotheses import (
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.integration import integrate_area_counts_seconds
from xic_extractor.peak_detection.ms1_morphology import (
    MS1_MORPHOLOGY_AREA_SOURCE,
    gaussian15_positive_asls_residual_metrics,
)
from xic_extractor.peak_detection.selected_envelope import (
    SelectedEnvelopeBoundaryEvaluation,
    SelectedEnvelopePolicy,
    evaluate_selected_envelope_boundary,
)


def selected_envelope_evaluation_from_hypothesis(
    hypothesis: PeakHypothesis,
    *,
    rt_values: Any,
    intensity_values: Any,
    quantitation_context_rt_start: float,
    quantitation_context_rt_end: float,
    policy: SelectedEnvelopePolicy | None = None,
    blank_like_context: bool = False,
) -> SelectedEnvelopeBoundaryEvaluation:
    if not hypothesis.audit.selected:
        raise ValueError("selected-envelope diagnostics require a selected hypothesis")
    _validate_context_contains_resolver(
        hypothesis,
        quantitation_context_rt_start=quantitation_context_rt_start,
        quantitation_context_rt_end=quantitation_context_rt_end,
    )
    rt, intensity = _context_trace(
        rt_values,
        intensity_values,
        quantitation_context_rt_start=quantitation_context_rt_start,
        quantitation_context_rt_end=quantitation_context_rt_end,
    )
    baseline = asls_baseline(intensity)
    return evaluate_selected_envelope_boundary(
        rt,
        intensity,
        baseline,
        selected_apex_rt=hypothesis.integration.rt_apex_min,
        resolver_rt_start=hypothesis.integration.rt_left_min,
        resolver_rt_end=hypothesis.integration.rt_right_min,
        quantitation_context_rt_start=float(rt[0]),
        quantitation_context_rt_end=float(rt[-1]),
        selected_candidate_id=hypothesis.hypothesis_id,
        policy=policy,
        legacy_resolver_provenance=hypothesis.resolver_mode,
        blank_like_context=blank_like_context,
    )


def selected_envelope_promoted_hypothesis_from_hypothesis(
    hypothesis: PeakHypothesis,
    *,
    rt_values: Any,
    intensity_values: Any,
    quantitation_context_rt_start: float,
    quantitation_context_rt_end: float,
    policy: SelectedEnvelopePolicy | None = None,
    blank_like_context: bool = False,
) -> tuple[PeakHypothesis, SelectedEnvelopeBoundaryEvaluation]:
    evaluation = selected_envelope_evaluation_from_hypothesis(
        hypothesis,
        rt_values=rt_values,
        intensity_values=intensity_values,
        quantitation_context_rt_start=quantitation_context_rt_start,
        quantitation_context_rt_end=quantitation_context_rt_end,
        policy=policy,
        blank_like_context=blank_like_context,
    )
    if evaluation.row_boundary_decision != "accept_candidate":
        return hypothesis, evaluation
    if (
        evaluation.selected_boundary_mode == "resolver_interval"
        and hypothesis.integration.area_ms1_morphology is not None
        and hypothesis.integration.ms1_morphology_area_source
        == MS1_MORPHOLOGY_AREA_SOURCE
    ):
        return hypothesis, evaluation

    rt, intensity = _context_trace(
        rt_values,
        intensity_values,
        quantitation_context_rt_start=quantitation_context_rt_start,
        quantitation_context_rt_end=quantitation_context_rt_end,
    )
    baseline = asls_baseline(intensity)
    promoted_integration = _promoted_integration(
        hypothesis.integration,
        evaluation=evaluation,
        rt=rt,
        intensity=intensity,
        baseline=baseline,
    )
    return replace(hypothesis, integration=promoted_integration), evaluation


def _promoted_integration(
    integration: IntegrationResult,
    *,
    evaluation: SelectedEnvelopeBoundaryEvaluation,
    rt: np.ndarray,
    intensity: np.ndarray,
    baseline: np.ndarray,
) -> IntegrationResult:
    interval = evaluation.selected_envelope_interval
    left, right = bounded_trace_interval(
        interval.start_index,
        interval.end_index,
        len(rt),
    )
    baseline_integration = integrate_with_baseline(
        intensity,
        rt,
        left,
        right,
        baseline_values=baseline,
    )
    morphology = gaussian15_positive_asls_residual_metrics(
        rt,
        intensity,
        baseline,
        left,
        right,
        window_points=evaluation.morphology_trace_window_points,
    )
    return IntegrationResult(
        rt_left_min=float(rt[left]),
        rt_apex_min=integration.rt_apex_min,
        rt_right_min=float(rt[right - 1]),
        raw_apex_rt_min=integration.raw_apex_rt_min,
        rt_width_min=float(rt[right - 1] - rt[left]),
        height_raw=integration.height_raw,
        height_smoothed=integration.height_smoothed,
        area_raw_counts_seconds=integrate_area_counts_seconds(
            intensity,
            rt,
            left,
            right,
        ),
        integration_method="selected_envelope_gaussian15",
        boundary_sources=_boundary_sources(integration, evaluation),
        area_baseline_corrected=baseline_integration.area_baseline_corrected,
        area_uncertainty=baseline_integration.area_uncertainty,
        area_uncertainty_formula_version=(
            baseline_integration.area_uncertainty_formula_version
        ),
        baseline_residual_mad=baseline_integration.baseline_residual_mad,
        area_uncertainty_noise_source=(
            baseline_integration.area_uncertainty_noise_source
        ),
        baseline_type=baseline_integration.baseline_type,
        baseline_score=baseline_integration.baseline_score,
        raw_scan_indices=tuple(range(left, right)),
        area_ms1_morphology=morphology.area_positive_asls_residual,
        ms1_morphology_area_source=MS1_MORPHOLOGY_AREA_SOURCE,
        ms1_morphology_trace_method=morphology.trace_method,
        ms1_morphology_trace_window_points=morphology.trace_window_points,
        ms1_morphology_trace_effective_points=morphology.trace_effective_points,
    )


def _boundary_sources(
    integration: IntegrationResult,
    evaluation: SelectedEnvelopeBoundaryEvaluation,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                *integration.boundary_sources,
                "selected_envelope",
                "gaussian15_morphology",
                evaluation.boundary_change_class,
            )
        )
    )


def _context_trace(
    rt_values: Any,
    intensity_values: Any,
    *,
    quantitation_context_rt_start: float,
    quantitation_context_rt_end: float,
) -> tuple[np.ndarray, np.ndarray]:
    rt = np.asarray(rt_values, dtype=float)
    intensity = np.asarray(intensity_values, dtype=float)
    if rt.ndim != 1 or intensity.ndim != 1:
        raise ValueError("rt_values and intensity_values must be one-dimensional")
    if len(rt) != len(intensity):
        raise ValueError("rt_values and intensity_values must have the same length")
    if not np.all(np.isfinite(rt)):
        raise ValueError("rt_values must contain finite values")
    if len(rt) >= 2 and not np.all(np.diff(rt) > 0.0):
        raise ValueError("rt_values must be strictly increasing")
    start = min(
        float(quantitation_context_rt_start),
        float(quantitation_context_rt_end),
    )
    end = max(
        float(quantitation_context_rt_start),
        float(quantitation_context_rt_end),
    )
    left = int(np.searchsorted(rt, start, side="left"))
    right = int(np.searchsorted(rt, end, side="right"))
    if right - left < 2:
        raise ValueError("quantitation context must contain at least 2 scans")
    context_rt = rt[left:right]
    context_intensity = intensity[left:right]
    if not (np.all(np.isfinite(context_rt)) and np.all(np.isfinite(context_intensity))):
        raise ValueError("quantitation context trace must contain finite values")
    return context_rt, context_intensity


def _validate_context_contains_resolver(
    hypothesis: PeakHypothesis,
    *,
    quantitation_context_rt_start: float,
    quantitation_context_rt_end: float,
) -> None:
    start = min(
        float(quantitation_context_rt_start),
        float(quantitation_context_rt_end),
    )
    end = max(
        float(quantitation_context_rt_start),
        float(quantitation_context_rt_end),
    )
    if (
        start > hypothesis.integration.rt_left_min
        or end < hypothesis.integration.rt_right_min
    ):
        raise ValueError("quantitation context must contain the resolver interval")
