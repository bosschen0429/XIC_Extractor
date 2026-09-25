from __future__ import annotations

from collections.abc import Callable

import numpy as np

from xic_extractor.config import ExtractionConfig
from xic_extractor.peak_detection import engine_candidates as candidate_flow
from xic_extractor.peak_detection.candidate_scoring import score_candidate
from xic_extractor.peak_detection.candidate_selection import (
    select_candidate_by_evidence,
)
from xic_extractor.peak_detection.engine import (
    find_peak_and_area as _find_peak_and_area,
)
from xic_extractor.peak_detection.engine import (
    find_peak_candidates as _find_peak_candidates,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidatesResult,
    PeakDetectionResult,
)
from xic_extractor.peak_detection.scoring_models import ScoringContext

find_peak_candidates_legacy_savgol = (
    candidate_flow.find_peak_candidates_legacy_savgol
)
find_peak_candidates_local_minimum = (
    candidate_flow.find_peak_candidates_local_minimum
)

__all__ = [
    "find_peak_and_area",
    "find_peak_candidates",
    "score_candidate",
    "select_candidate_by_evidence",
    "find_peak_candidates_legacy_savgol",
    "find_peak_candidates_local_minimum",
]


def find_peak_and_area(
    rt: np.ndarray,
    intensity: np.ndarray,
    config: ExtractionConfig,
    *,
    preferred_rt: float | None = None,
    strict_preferred_rt: bool = False,
    scoring_context_builder: Callable[[PeakCandidate], ScoringContext] | None = None,
    istd_confidence_note: str | None = None,
    evidence_role: str = "",
    istd_pair: str = "",
    paired_istd_anchor_rt: float | None = None,
) -> PeakDetectionResult:
    return _find_peak_and_area(
        rt,
        intensity,
        config,
        preferred_rt=preferred_rt,
        strict_preferred_rt=strict_preferred_rt,
        scoring_context_builder=scoring_context_builder,
        istd_confidence_note=istd_confidence_note,
        evidence_role=evidence_role,
        istd_pair=istd_pair,
        paired_istd_anchor_rt=paired_istd_anchor_rt,
        candidate_finder=find_peak_candidates,
        score_candidate_func=score_candidate,
        evidence_selector=select_candidate_by_evidence,
    )


def find_peak_candidates(
    rt: np.ndarray,
    intensity: np.ndarray,
    config: ExtractionConfig,
    *,
    peak_min_prominence_ratio: float | None = None,
) -> PeakCandidatesResult:
    return _find_peak_candidates(
        rt,
        intensity,
        config,
        peak_min_prominence_ratio=peak_min_prominence_ratio,
        local_minimum_finder=find_peak_candidates_local_minimum,
        legacy_savgol_finder=find_peak_candidates_legacy_savgol,
    )
