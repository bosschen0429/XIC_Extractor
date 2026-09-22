from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction.rt_windows import (
    WiderWindowPeak,
    retry_istd_peak_with_wider_anchor_window,
)
from xic_extractor.extraction.scoring_factory import selected_candidate
from xic_extractor.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.peak_detection.scoring_quality import (
    candidate_quality_penalty,
    candidate_selection_quality_penalty,
)
from xic_extractor.signal_processing import PeakCandidate, PeakDetectionResult

_ISTD_WIDER_RETRY_MIN_INTENSITY_RATIO = 2.0


@dataclass(frozen=True)
class IstdAnchorRetryDecision:
    peak_result: PeakDetectionResult
    rt: np.ndarray | None = None
    intensity: np.ndarray | None = None


def retry_istd_anchor_peak_if_needed(
    peak_result: PeakDetectionResult,
    *,
    raw: Any,
    config: ExtractionConfig,
    target: Target,
    anchor_used: bool,
    anchor_rt: float | None,
    scoring_context_factory: Callable[..., Any] | None,
    candidate_ms2_evidence_builder: Callable[
        [PeakCandidate], CandidateMS2Evidence | None
    ],
    sample_name: str,
    nl_result: NLResult | None,
    istd_confidence_note: str | None,
    istd_rt_in_this_sample: float | None,
    paired_istd_fwhm: float | None,
    peak_finder: Callable[..., PeakDetectionResult],
) -> IstdAnchorRetryDecision:
    if not target.is_istd or not anchor_used or anchor_rt is None:
        return IstdAnchorRetryDecision(peak_result)

    if peak_result.peak is None:
        retried = _retry(
            raw=raw,
            config=config,
            target=target,
            anchor_rt=anchor_rt,
            scoring_context_factory=scoring_context_factory,
            candidate_ms2_evidence_builder=candidate_ms2_evidence_builder,
            sample_name=sample_name,
            nl_result=nl_result,
            istd_confidence_note=istd_confidence_note,
            istd_rt_in_this_sample=istd_rt_in_this_sample,
            paired_istd_fwhm=paired_istd_fwhm,
            peak_finder=peak_finder,
        )
        if retried is None:
            return IstdAnchorRetryDecision(peak_result)
        return IstdAnchorRetryDecision(
            retried.peak_result,
            rt=retried.rt,
            intensity=retried.intensity,
        )

    if not _should_try_wider_istd_anchor_retry(peak_result):
        return IstdAnchorRetryDecision(peak_result)
    retried = _retry(
        raw=raw,
        config=config,
        target=target,
        anchor_rt=anchor_rt,
        scoring_context_factory=scoring_context_factory,
        candidate_ms2_evidence_builder=candidate_ms2_evidence_builder,
        sample_name=sample_name,
        nl_result=nl_result,
        istd_confidence_note=istd_confidence_note,
        istd_rt_in_this_sample=istd_rt_in_this_sample,
        paired_istd_fwhm=paired_istd_fwhm,
        peak_finder=peak_finder,
    )
    if retried is not None and _should_use_wider_istd_retry(
        peak_result, retried.peak_result
    ):
        return IstdAnchorRetryDecision(
            retried.peak_result,
            rt=retried.rt,
            intensity=retried.intensity,
        )
    return IstdAnchorRetryDecision(peak_result)


def _retry(
    *,
    raw: Any,
    config: ExtractionConfig,
    target: Target,
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
    peak_finder: Callable[..., PeakDetectionResult],
) -> WiderWindowPeak | None:
    return retry_istd_peak_with_wider_anchor_window(
        raw,
        config,
        target,
        anchor_rt=anchor_rt,
        scoring_context_factory=scoring_context_factory,
        candidate_ms2_evidence_builder=candidate_ms2_evidence_builder,
        sample_name=sample_name,
        nl_result=nl_result,
        istd_confidence_note=istd_confidence_note,
        istd_rt_in_this_sample=istd_rt_in_this_sample,
        paired_istd_fwhm=paired_istd_fwhm,
        peak_finder=peak_finder,
    )


def _should_try_wider_istd_anchor_retry(
    peak_result: PeakDetectionResult,
) -> bool:
    if peak_result.peak is None:
        return False
    if peak_result.confidence in {"LOW", "VERY_LOW"}:
        return True
    candidate = selected_candidate(peak_result)
    if candidate is None:
        return False
    if getattr(candidate, "quality_flags", ()):
        return True
    return any(
        label == "local_sn" and severity >= 1
        for severity, label in peak_result.severities
    )


def _should_use_wider_istd_retry(
    current: PeakDetectionResult,
    retried: PeakDetectionResult,
) -> bool:
    if retried.peak is None:
        return False
    if current.peak is None:
        return True

    current_candidate = selected_candidate(current)
    retried_candidate = selected_candidate(retried)
    if current_candidate is None or retried_candidate is None:
        return False

    current_penalty = _selection_penalty(current_candidate)
    retried_penalty = _selection_penalty(retried_candidate)
    if retried_penalty > current_penalty:
        return False

    return retried_candidate.selection_apex_intensity >= (
        current_candidate.selection_apex_intensity
        * _ISTD_WIDER_RETRY_MIN_INTENSITY_RATIO
    )


def _selection_penalty(candidate: PeakCandidate) -> float:
    quality_penalty, _ = candidate_quality_penalty(candidate)
    return quality_penalty + candidate_selection_quality_penalty(candidate)
