import warnings
from collections.abc import Callable
from typing import Any

import numpy as np
from scipy.signal import peak_widths

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.injection_rolling import rolling_median_rt
from xic_extractor.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.peak_detection.ms1_morphology import (
    MS1_MORPHOLOGY_AREA_SOURCE,
    MS1_MORPHOLOGY_TRACE_METHOD,
    configured_morphology_window_points,
    gaussian15_positive_asls_residual_trace,
)
from xic_extractor.peak_detection.scoring_metrics import compute_local_sn_cache
from xic_extractor.peak_detection.scoring_models import ScoringContext
from xic_extractor.peak_detection.scoring_quality import hard_quality_flags
from xic_extractor.rt_prior_library import LibraryEntry
from xic_extractor.signal_processing import (
    PeakCandidate,
    PeakDetectionResult,
)


def build_scoring_context_factory(
    *,
    config: ExtractionConfig,
    injection_order: dict[str, int],
    istd_rts_by_sample: dict[str, dict[str, float]],
    rt_prior_library: dict[tuple[str, str], LibraryEntry],
) -> Callable[..., Callable[[PeakCandidate], ScoringContext]]:
    def _factory(
        *,
        target: Target,
        sample_name: str,
        rt: np.ndarray,
        intensity: np.ndarray,
        istd_rt_in_this_sample: float | None,
        paired_istd_fwhm: float | None,
        nl_result: NLResult | None,
        candidate_ms2_evidence_builder: Callable[
            [PeakCandidate], CandidateMS2Evidence | None
        ]
        | None = None,
    ) -> Callable[[PeakCandidate], ScoringContext]:
        rt_prior: float | None = None
        rt_prior_sigma: float | None = None
        prior_source = ""

        if target.is_istd:
            rt_map = istd_rts_by_sample.get(target.label, {})
            rt_prior = rolling_median_rt(
                target.label,
                sample_name,
                rt_map,
                injection_order,
                window=config.rolling_window_size,
            )
            if rt_prior is not None:
                prior_source = "rolling_median"
            else:
                library_entry = rt_prior_library.get((target.label, "ISTD"))
                if (
                    library_entry is not None
                    and library_entry.median_abs_rt is not None
                ):
                    rt_prior = library_entry.median_abs_rt
                    rt_prior_sigma = library_entry.sigma_abs_rt
                    prior_source = "library_abs"
        else:
            library_entry = rt_prior_library.get((target.label, "analyte"))
            if (
                library_entry is not None
                and istd_rt_in_this_sample is not None
                and library_entry.median_delta_rt is not None
            ):
                rt_prior = istd_rt_in_this_sample + library_entry.median_delta_rt
                rt_prior_sigma = library_entry.sigma_delta_rt
                prior_source = "delta_rt_library"

        rt_values = np.asarray(rt, dtype=float)
        intensity_values = np.asarray(intensity, dtype=float)
        baseline_array, residual_mad = compute_local_sn_cache(intensity_values)
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
        target_window_nl_match = (
            nl_result is not None and nl_result.status in {"OK", "WARN"}
        )
        prefer_rt_prior_tiebreak = (
            not target.is_istd
            and bool(target.istd_pair)
            and istd_rt_in_this_sample is not None
            and rt_prior is not None
            and prior_source == "delta_rt_library"
        )

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
                candidate_ms2.ms1_peak_group_source
                if candidate_ms2 is not None
                else ""
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
            half_width_ratio, width_min = compute_shape_metrics(
                rt_values,
                active_intensity_values,
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
                rt_min=target.rt_min,
                rt_max=target.rt_max,
                dirty_matrix=config.dirty_matrix_mode,
                neutral_loss_required=target.neutral_loss_da is not None,
                count_no_ms2_as_detected=config.count_no_ms2_as_detected,
                ms2_trace_strength=ms2_trace_strength,
                ms2_alignment_source=ms2_alignment_source,
                trigger_scan_count=trigger_scan_count,
                strict_nl_scan_count=strict_nl_scan_count,
                ms1_peak_group_source=ms1_peak_group_source,
                ms1_peak_group_rt_min=ms1_peak_group_rt_min,
                ms1_peak_group_rt_max=ms1_peak_group_rt_max,
                ms1_peak_group_trigger_scan_count=(
                    ms1_peak_group_trigger_scan_count
                ),
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
                prefer_rt_prior_tiebreak=prefer_rt_prior_tiebreak,
                active_trace_source=active_trace_source,
                morphology_trace_method=morphology_trace_method,
                morphology_trace_window_points=morphology_trace_window_points,
            )

        setattr(builder, "rt_prior", rt_prior)
        setattr(builder, "prior_source", prior_source)
        return builder

    return _factory


def make_scoring_context_factory(
    *,
    config: ExtractionConfig,
    injection_order: dict[str, int],
    istd_rts_by_sample: dict[str, dict[str, float]],
    rt_prior_library: dict[tuple[str, str], Any],
) -> Callable[..., Any]:
    return build_scoring_context_factory(
        config=config,
        injection_order=injection_order,
        istd_rts_by_sample=istd_rts_by_sample,
        rt_prior_library=rt_prior_library,
    )


def compute_shape_metrics(
    rt: np.ndarray,
    intensity: np.ndarray,
    apex_index: int,
) -> tuple[float, float | None]:
    """Return the RT half-width ratio and half-prominence width in minutes."""
    rt_values = np.asarray(rt, dtype=float)
    values = np.asarray(intensity, dtype=float)
    if len(values) == 0 or apex_index < 0 or apex_index >= len(values):
        return 1.0, None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        widths, _, left_ips, right_ips = peak_widths(
            values,
            [apex_index],
            rel_height=0.5,
        )
    if len(widths) == 0:
        return 1.0, None
    left_rt, right_rt = np.interp(
        [left_ips[0], right_ips[0]],
        np.arange(len(values)),
        rt_values,
    )
    width_min = float(right_rt - left_rt)
    if not np.isfinite(width_min) or width_min <= 0:
        return 1.0, None
    left = float(rt_values[apex_index] - left_rt)
    right = float(right_rt - rt_values[apex_index])
    if left <= 0 or right <= 0:
        return 1.0, width_min
    return float(left / right), width_min


def selected_shape_metrics(
    rt: np.ndarray,
    intensity: np.ndarray,
    peak_result: PeakDetectionResult,
) -> tuple[float, float | None] | None:
    """Return selected trace shape with its half-prominence width in minutes."""
    candidate = selected_candidate(peak_result)
    if candidate is None:
        return None
    return compute_shape_metrics(rt, intensity, candidate.selection_apex_index)


def selected_candidate(peak_result: PeakDetectionResult) -> PeakCandidate | None:
    if peak_result.peak is None:
        return None
    for candidate in peak_result.candidates:
        if candidate.peak == peak_result.peak:
            return candidate
    return None


def allow_prepass_anchor(peak_result: PeakDetectionResult) -> bool:
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
