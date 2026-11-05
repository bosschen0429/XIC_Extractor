from __future__ import annotations

from collections.abc import Callable

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection import candidate_search
from xic_extractor.peak_detection.candidate_scoring import score_candidate
from xic_extractor.peak_detection.candidate_selection import (
    select_candidate_by_evidence,
)
from xic_extractor.peak_detection.local_minimum import (
    find_peak_candidates_local_minimum,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidatesResult,
    PeakDetectionResult,
)
from xic_extractor.peak_detection.peak_selection import select_peak_and_area
from xic_extractor.peak_detection.relaxed_search import preferred_rt_relaxed_search
from xic_extractor.peak_detection.savgol import find_peak_candidates_savgol
from xic_extractor.peak_detection.scoring_models import ScoringContext
from xic_extractor.peak_detection.selection import select_candidate


def find_peak_and_area(
    rt: np.ndarray,
    intensity: np.ndarray,
    config: ExtractionConfig,
    *,
    preferred_rt: float | None = None,
    strict_preferred_rt: bool = False,
    scoring_context_builder: Callable[[PeakCandidate], ScoringContext] | None = None,
    evidence_role: str = "",
    istd_pair: str = "",
    paired_istd_anchor_rt: float | None = None,
) -> PeakDetectionResult:
    """Search resolver candidates, recover near preferred RT, then select and score."""
    candidates_result = find_peak_candidates(rt, intensity, config)
    preliminary_candidate = (
        select_candidate(
            candidates_result.candidates,
            preferred_rt=preferred_rt,
            strict_preferred_rt=strict_preferred_rt,
        )
        if candidates_result.status == "OK"
        else None
    )
    recovery = preferred_rt_relaxed_search(
        rt,
        intensity,
        config,
        preferred_rt=preferred_rt,
        strict_preferred_rt=strict_preferred_rt,
        current_candidate=preliminary_candidate,
        candidate_finder=find_peak_candidates,
    )
    return select_peak_and_area(
        candidates_result,
        config,
        preferred_rt=preferred_rt,
        strict_preferred_rt=strict_preferred_rt,
        scoring_context_builder=scoring_context_builder,
        evidence_role=evidence_role,
        istd_pair=istd_pair,
        paired_istd_anchor_rt=paired_istd_anchor_rt,
        preferred_rt_recovery=recovery,
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
    return candidate_search.find_peak_candidates(
        rt,
        intensity,
        config,
        peak_min_prominence_ratio=peak_min_prominence_ratio,
        local_minimum_finder=find_peak_candidates_local_minimum,
        savgol_finder=find_peak_candidates_savgol,
    )
