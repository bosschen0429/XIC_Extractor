from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction.anchors import (
    PAIRED_TARGET_NL_ANCHOR_REFERENCE_DELTA_MAX_MIN,
)
from xic_extractor.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.signal_processing import (
    PeakCandidate,
    PeakDetectionResult,
    find_peak_and_area,
)


@dataclass(frozen=True)
class WiderWindowPeak:
    peak_result: PeakDetectionResult
    rt: np.ndarray
    intensity: np.ndarray


def get_rt_window(
    raw: Any,
    target: Target,
    config: ExtractionConfig,
    *,
    reference_rt: float | None,
    target_reference_rt: float | None = None,
    sample_drift: float = 0.0,
) -> tuple[float, float, bool, float | None]:
    """Return (rt_min, rt_max, anchor_used, anchor_rt)."""
    from xic_extractor import extractor

    if target.neutral_loss_da is None or target.nl_ppm_max is None:
        return target.rt_min, target.rt_max, False, None

    rt_center = (target.rt_min + target.rt_max) / 2.0 + sample_drift
    paired_reference_rt = _paired_reference_inside_target_window(
        target,
        reference_rt=reference_rt,
    )
    fallback_reference_rt = (
        target_reference_rt
        if target_reference_rt is not None
        else paired_reference_rt
        if paired_reference_rt is not None
        else rt_center
    )
    anchor_reference_rt = (
        target_reference_rt if target_reference_rt is not None else paired_reference_rt
    )
    anchor_rt = extractor.find_nl_anchor_rt(
        raw,
        precursor_mz=target.mz,
        rt_center=rt_center,
        search_margin_min=config.nl_rt_anchor_search_margin_min,
        neutral_loss_da=target.neutral_loss_da,
        nl_ppm_max=target.nl_ppm_max,
        ms2_precursor_tol_da=config.ms2_precursor_tol_da,
        nl_min_intensity_ratio=config.nl_min_intensity_ratio,
        reference_rt=anchor_reference_rt,
    )
    if anchor_rt is not None:
        if _paired_target_anchor_is_too_far_from_reference(
            anchor_rt,
            reference_rt=anchor_reference_rt,
        ):
            half = config.nl_fallback_half_window_min
            return (
                max(0.0, fallback_reference_rt - half),
                fallback_reference_rt + half,
                False,
                fallback_reference_rt,
            )
        half = config.nl_rt_anchor_half_window_min
        return max(0.0, anchor_rt - half), anchor_rt + half, True, anchor_rt

    half = config.nl_fallback_half_window_min
    if reference_rt is not None:
        return (
            max(0.0, fallback_reference_rt - half),
            fallback_reference_rt + half,
            False,
            fallback_reference_rt,
        )
    return max(0.0, rt_center - half), rt_center + half, False, None


def _paired_reference_inside_target_window(
    target: Target,
    *,
    reference_rt: float | None,
) -> float | None:
    if target.is_istd or not target.istd_pair or reference_rt is None:
        return None
    if target.rt_min <= reference_rt <= target.rt_max:
        return reference_rt
    return None


def _paired_target_anchor_is_too_far_from_reference(
    anchor_rt: float,
    *,
    reference_rt: float | None,
) -> bool:
    if reference_rt is None:
        return False
    return (
        abs(anchor_rt - reference_rt)
        > PAIRED_TARGET_NL_ANCHOR_REFERENCE_DELTA_MAX_MIN
    )


def retry_istd_peak_with_wider_anchor_window(
    raw: Any,
    config: ExtractionConfig,
    target: Target,
    *,
    anchor_rt: float,
    scoring_context_factory: Callable[..., Any] | None,
    candidate_ms2_evidence_builder: Callable[
        [PeakCandidate], CandidateMS2Evidence | None
    ],
    sample_name: str,
    nl_result: NLResult | None,
    istd_confidence_note: str | None,
    istd_rt_in_this_sample: float | None,
    paired_istd_fwhm: float | None,
    peak_finder: Callable[..., PeakDetectionResult] = find_peak_and_area,
) -> WiderWindowPeak | None:
    wider_half_window = max(
        config.nl_fallback_half_window_min,
        config.nl_rt_anchor_half_window_min,
    )
    if wider_half_window <= config.nl_rt_anchor_half_window_min:
        return None

    rt_min = max(0.0, anchor_rt - wider_half_window)
    rt_max = anchor_rt + wider_half_window
    rt, intensity = raw.extract_xic(target.mz, rt_min, rt_max, target.ppm_tol)
    scoring_context_builder = None
    if scoring_context_factory is not None:
        scoring_context_builder = scoring_context_factory(
            target=target,
            sample_name=sample_name,
            rt=rt,
            intensity=intensity,
            istd_rt_in_this_sample=istd_rt_in_this_sample,
            paired_istd_fwhm=paired_istd_fwhm,
            nl_result=nl_result,
            candidate_ms2_evidence_builder=candidate_ms2_evidence_builder,
        )
    if scoring_context_builder is not None:
        peak_result = peak_finder(
            rt,
            intensity,
            config,
            preferred_rt=anchor_rt,
            strict_preferred_rt=False,
            scoring_context_builder=scoring_context_builder,
            istd_confidence_note=istd_confidence_note,
        )
    else:
        peak_result = peak_finder(
            rt,
            intensity,
            config,
            preferred_rt=anchor_rt,
            strict_preferred_rt=False,
        )
    if peak_result.peak is None:
        return None
    return WiderWindowPeak(peak_result=peak_result, rt=rt, intensity=intensity)
