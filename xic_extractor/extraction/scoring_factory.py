from collections.abc import Callable, Mapping

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.peak_detection import ms1_morphology
from xic_extractor.peak_detection.models import PeakCandidate, PeakDetectionResult
from xic_extractor.peak_detection.ms1_morphology import (
    MS1_MORPHOLOGY_AREA_SOURCE,
    MS1_MORPHOLOGY_TRACE_METHOD,
    configured_morphology_window_points,
    gaussian15_positive_asls_residual_trace,
)
from xic_extractor.peak_detection.physical_peak_models import PreparedSignalResult
from xic_extractor.peak_detection.scoring_metrics import compute_local_sn_cache
from xic_extractor.peak_detection.scoring_models import ScoringContext
from xic_extractor.peak_detection.scoring_quality import hard_quality_flags


def build_scoring_context_factory(
    *,
    config: ExtractionConfig,
) -> Callable[..., Callable[[PeakCandidate], ScoringContext]]:
    def _factory(
        *,
        target: Target,
        sample_name: str,
        rt: np.ndarray,
        intensity: np.ndarray,
        prepared_result: PreparedSignalResult,
        istd_rt_in_this_sample: float | None,
        paired_istd_fwhm: float | None,
        nl_result: NLResult | None,
        target_reference_rt: float | None = None,
        candidate_ms2_evidence_builder: Callable[
            [PeakCandidate], CandidateMS2Evidence | None
        ]
        | None = None,
        candidate_nl_support: Mapping[PeakCandidate, bool] | None = None,
    ) -> Callable[[PeakCandidate], ScoringContext]:
        # Expected RT is an identity prior, not a peak-generation or confidence window.
        rt_prior = (
            target_reference_rt
            if target_reference_rt is not None
            else target.expected_rt_min
            if target.expected_rt_min is not None
            else (target.rt_min + target.rt_max) / 2
        )
        rt_prior_sigma: float | None = None
        prior_source = (
            "paired_istd"
            if target.istd_pair and istd_rt_in_this_sample is not None
            else "expected_rt"
        )
        scoring_rt_min, scoring_rt_max = (
            (float(rt[0]), float(rt[-1])) if len(rt) else (0.0, 0.0)
        )

        rt_values = np.asarray(rt, dtype=float)
        intensity_values = np.asarray(intensity, dtype=float)
        prepared = prepared_result.prepared
        shape_rt = np.asarray(prepared.rt if prepared is not None else (), dtype=float)
        shape_signal = np.asarray(
            prepared.positive_signal if prepared is not None else (), dtype=float
        )
        baseline_array, residual_mad = compute_local_sn_cache(
            intensity_values, baseline_unclipped=prepared_result.baseline_unclipped
        )
        morphology_window_points = configured_morphology_window_points(config)
        active_intensity_values = intensity_values
        active_baseline_array = baseline_array
        active_trace_source = "raw"
        morphology_trace_method = ""
        morphology_trace_window_points: int | None = None
        if baseline_array is not None:
            try:
                active_intensity_values = gaussian15_positive_asls_residual_trace(
                    intensity_values,
                    baseline_array,
                    window_points=morphology_window_points,
                )
                active_baseline_array = np.zeros_like(active_intensity_values)
                active_trace_source = MS1_MORPHOLOGY_AREA_SOURCE
                morphology_trace_method = MS1_MORPHOLOGY_TRACE_METHOD
                morphology_trace_window_points = morphology_window_points
            except (TypeError, ValueError, FloatingPointError):
                active_intensity_values = intensity_values
                active_baseline_array = baseline_array
        target_window_ms2_present = (
            nl_result is not None and nl_result.matched_scan_count > 0
        )
        target_window_nl_match = nl_result is not None and nl_result.status in {
            "OK",
            "WARN",
        }

        def builder(candidate: PeakCandidate) -> ScoringContext:
            candidate_ms2 = (
                candidate_ms2_evidence_builder(candidate)
                if candidate_ms2_evidence_builder is not None
                else None
            )
            ms2_present = (
                candidate_ms2.ms2_present
                if candidate_ms2 is not None
                else target_window_ms2_present
            )
            nl_match = (
                candidate_ms2.nl_match
                if candidate_ms2 is not None
                else target_window_nl_match
            )
            if candidate_nl_support is not None:
                nl_match = candidate_nl_support[candidate]
            ms2_trace_strength = (
                candidate_ms2.trace.strength if candidate_ms2 is not None else None
            )
            ms2_alignment_source = (
                candidate_ms2.alignment_source if candidate_ms2 is not None else None
            )
            trigger_scan_count = (
                candidate_ms2.trigger_scan_count if candidate_ms2 is not None else None
            )
            strict_nl_scan_count = (
                candidate_ms2.strict_nl_scan_count
                if candidate_ms2 is not None
                else None
            )
            ms1_peak_group_source = (
                candidate_ms2.ms1_peak_group_source if candidate_ms2 is not None else ""
            )
            ms1_peak_group_rt_min = (
                candidate_ms2.ms1_peak_group_rt_min
                if candidate_ms2 is not None
                else None
            )
            ms1_peak_group_rt_max = (
                candidate_ms2.ms1_peak_group_rt_max
                if candidate_ms2 is not None
                else None
            )
            ms1_peak_group_trigger_scan_count = (
                candidate_ms2.ms1_peak_group_trigger_scan_count
                if candidate_ms2 is not None
                else None
            )
            ms1_peak_group_strict_nl_scan_count = (
                candidate_ms2.ms1_peak_group_strict_nl_scan_count
                if candidate_ms2 is not None
                else None
            )
            ms1_peak_group_strict_nl_event_count = (
                candidate_ms2.ms1_peak_group_strict_nl_event_count
                if candidate_ms2 is not None
                else None
            )
            outside_ms1_peak_group_trigger_scan_count = (
                candidate_ms2.outside_ms1_peak_group_trigger_scan_count
                if candidate_ms2 is not None
                else None
            )
            outside_ms1_peak_group_strict_nl_scan_count = (
                candidate_ms2.outside_ms1_peak_group_strict_nl_scan_count
                if candidate_ms2 is not None
                else None
            )
            half_width_ratio, width_min = ms1_morphology.compute_shape_metrics(
                shape_rt,
                shape_signal,
                candidate.selection_apex_index,
            )
            # Both widths are half-prominence widths in minutes; carrier names
            # retain the existing fwhm spelling.
            fwhm_ratio: float | None = None
            if (
                not target.is_istd
                and width_min is not None
                and paired_istd_fwhm is not None
                and paired_istd_fwhm > 0
            ):
                fwhm_ratio = width_min / paired_istd_fwhm
            return ScoringContext(
                rt_array=rt_values,
                intensity_array=active_intensity_values,
                apex_index=candidate.selection_apex_index,
                half_width_ratio=half_width_ratio,
                fwhm_ratio=fwhm_ratio,
                ms2_present=ms2_present,
                nl_match=nl_match,
                rt_prior=rt_prior,
                rt_prior_sigma=rt_prior_sigma,
                rt_min=scoring_rt_min,
                rt_max=scoring_rt_max,
                neutral_loss_required=target.neutral_loss_da is not None,
                count_no_ms2_as_detected=False,
                ms2_trace_strength=ms2_trace_strength,
                ms2_alignment_source=ms2_alignment_source,
                trigger_scan_count=trigger_scan_count,
                strict_nl_scan_count=strict_nl_scan_count,
                ms1_peak_group_source=ms1_peak_group_source,
                ms1_peak_group_rt_min=ms1_peak_group_rt_min,
                ms1_peak_group_rt_max=ms1_peak_group_rt_max,
                ms1_peak_group_trigger_scan_count=(ms1_peak_group_trigger_scan_count),
                ms1_peak_group_strict_nl_scan_count=(
                    ms1_peak_group_strict_nl_scan_count
                ),
                ms1_peak_group_strict_nl_event_count=(
                    ms1_peak_group_strict_nl_event_count
                ),
                outside_ms1_peak_group_trigger_scan_count=(
                    outside_ms1_peak_group_trigger_scan_count
                ),
                outside_ms1_peak_group_strict_nl_scan_count=(
                    outside_ms1_peak_group_strict_nl_scan_count
                ),
                baseline_array=active_baseline_array,
                residual_mad=residual_mad,
                active_trace_source=active_trace_source,
                morphology_trace_method=morphology_trace_method,
                morphology_trace_window_points=morphology_trace_window_points,
            )

        setattr(builder, "rt_prior", rt_prior)
        setattr(builder, "prior_source", prior_source)
        return builder

    return _factory


def selected_shape_metrics(
    prepared_result: PreparedSignalResult,
    peak_result: PeakDetectionResult,
) -> tuple[float, float | None] | None:
    """Return selected physical-signal shape and half-prominence width in minutes."""
    candidate = selected_candidate(peak_result)
    prepared = prepared_result.prepared
    if candidate is None or prepared is None:
        return None
    return ms1_morphology.compute_shape_metrics(
        np.asarray(prepared.rt),
        np.asarray(prepared.positive_signal),
        candidate.selection_apex_index,
    )


def selected_candidate(peak_result: PeakDetectionResult) -> PeakCandidate | None:
    if peak_result.peak is None:
        return None
    for candidate in peak_result.candidates:
        if candidate.peak == peak_result.peak:
            return candidate
    return None


def is_credible_istd_peak(peak_result: PeakDetectionResult) -> bool:
    candidate = selected_candidate(peak_result)
    if candidate is None:
        return False
    flags = tuple(str(flag) for flag in getattr(candidate, "quality_flags", ()))
    return not hard_quality_flags(flags)


def paired_istd_fwhm(
    target: Target,
    istd_shape_metrics_by_label: dict[str, tuple[float, float | None]],
) -> float | None:
    """Return selected ISTD half-prominence width in minutes (legacy name)."""
    if not target.istd_pair:
        return None
    metrics = istd_shape_metrics_by_label.get(target.istd_pair)
    if metrics is None:
        return None
    return metrics[1]
