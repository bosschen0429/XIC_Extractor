from __future__ import annotations

from collections.abc import Callable

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection import (
    engine_candidates as candidate_flow,
)
from xic_extractor.peak_detection import (
    engine_output as output_flow,
)
from xic_extractor.peak_detection import (
    engine_scoring as scoring_flow,
)
from xic_extractor.peak_detection.candidate_scoring import score_candidate
from xic_extractor.peak_detection.candidate_selection import (
    select_candidate_by_evidence,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidateScore,
    PeakCandidatesResult,
    PeakDetectionResult,
)
from xic_extractor.peak_detection.relaxed_search import preferred_rt_relaxed_search
from xic_extractor.peak_detection.scoring_models import (
    ScoredCandidate,
    ScoringContext,
)
from xic_extractor.peak_detection.scoring_reason import score_breakdown_fields
from xic_extractor.peak_detection.selection import (
    select_candidate,
    selection_rt_for_scored_candidates,
)


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
    candidate_finder: Callable[..., PeakCandidatesResult] | None = None,
    score_candidate_func: Callable[..., ScoredCandidate] = score_candidate,
    evidence_selector: Callable[..., ScoredCandidate] = select_candidate_by_evidence,
) -> PeakDetectionResult:
    candidate_finder = candidate_finder or find_peak_candidates
    candidates_result = candidate_finder(rt, intensity, config)
    chosen_confidence: str | None = None
    chosen_reason: str | None = None
    chosen_severities: tuple[tuple[int, str], ...] = ()
    chosen_score_breakdown: tuple[tuple[str, str], ...] = ()
    candidate_scores: tuple[PeakCandidateScore, ...] = ()
    if candidates_result.status == "OK":
        preliminary_candidate = select_candidate(
            candidates_result.candidates,
            preferred_rt=preferred_rt,
            strict_preferred_rt=strict_preferred_rt,
        )
        relaxed_candidate, relaxed_search_result = preferred_rt_relaxed_search(
            rt,
            intensity,
            config,
            preferred_rt=preferred_rt,
            strict_preferred_rt=strict_preferred_rt,
            current_candidate=preliminary_candidate,
            candidate_finder=candidate_finder,
        )
        all_candidates = candidates_result.candidates
        result_for_output = candidates_result
        if relaxed_candidate is not None and relaxed_search_result is not None:
            relaxed_candidate, relaxed_search_result = (
                candidate_flow.mark_relaxed_candidate(
                    relaxed_candidate,
                    relaxed_search_result,
                )
            )
            all_candidates, relaxed_candidate = (
                candidate_flow.append_or_merge_relaxed_candidate(
                    all_candidates,
                    relaxed_candidate,
                )
            )
            result_for_output = candidate_flow.with_candidates(
                candidates_result,
                all_candidates,
            )

        selection_rt = selection_rt_for_scored_candidates(
            candidates_result.candidates,
            preferred_rt=preferred_rt,
            strict_preferred_rt=strict_preferred_rt,
        )
        if relaxed_candidate is not None and relaxed_search_result is not None:
            selection_rt = preferred_rt

        if scoring_context_builder is not None:
            scored_candidates = [
                scoring_flow.score_with_context(
                    candidate,
                    scoring_context_builder(candidate),
                    istd_confidence_note=istd_confidence_note,
                    evidence_role=evidence_role,
                    istd_pair=istd_pair,
                    paired_istd_anchor_rt=paired_istd_anchor_rt,
                    score_candidate_func=score_candidate_func,
                )
                for candidate in all_candidates
            ]
            candidate_scores = tuple(
                scoring_flow.candidate_score_summary(scored_candidate)
                for scored_candidate in scored_candidates
            )
            chosen = evidence_selector(
                scored_candidates,
                selection_rt=selection_rt,
                strict_selection_rt=strict_preferred_rt,
            )
            best_candidate = chosen.candidate
            chosen_confidence = chosen.confidence.value
            chosen_reason = chosen.reason
            chosen_severities = chosen.severities
            chosen_score_breakdown = score_breakdown_fields(chosen.evidence_score)
        else:
            if relaxed_candidate is not None and relaxed_search_result is not None:
                best_candidate = relaxed_candidate
            else:
                best_candidate = select_candidate(
                    all_candidates,
                    preferred_rt=preferred_rt,
                    strict_preferred_rt=strict_preferred_rt,
                )
        return output_flow.detection_success(
            result_for_output,
            best_candidate,
            confidence=chosen_confidence,
            reason=chosen_reason,
            severities=chosen_severities,
            score_breakdown=chosen_score_breakdown,
            candidate_scores=candidate_scores,
            selection_reference_rt=selection_rt,
            paired_istd_anchor_rt=paired_istd_anchor_rt,
        )

    relaxed_candidate, relaxed_search_result = preferred_rt_relaxed_search(
        rt,
        intensity,
        config,
        preferred_rt=preferred_rt,
        strict_preferred_rt=strict_preferred_rt,
        current_candidate=None,
        candidate_finder=candidate_finder,
    )
    if relaxed_candidate is not None and relaxed_search_result is not None:
        relaxed_candidate, relaxed_search_result = (
            candidate_flow.mark_relaxed_candidate(
                relaxed_candidate,
                relaxed_search_result,
            )
        )
        if scoring_context_builder is not None:
            scored_relaxed_search = scoring_flow.score_with_context(
                relaxed_candidate,
                scoring_context_builder(relaxed_candidate),
                istd_confidence_note=istd_confidence_note,
                evidence_role=evidence_role,
                istd_pair=istd_pair,
                paired_istd_anchor_rt=paired_istd_anchor_rt,
                score_candidate_func=score_candidate_func,
            )
            candidate_scores = (
                scoring_flow.candidate_score_summary(scored_relaxed_search),
            )
            return output_flow.detection_success(
                relaxed_search_result,
                relaxed_candidate,
                confidence=scored_relaxed_search.confidence.value,
                reason=scored_relaxed_search.reason,
                severities=scored_relaxed_search.severities,
                score_breakdown=score_breakdown_fields(scored_relaxed_search.evidence_score),
                candidate_scores=candidate_scores,
                selection_reference_rt=preferred_rt,
                paired_istd_anchor_rt=paired_istd_anchor_rt,
            )
        return output_flow.detection_success(
            relaxed_search_result,
            relaxed_candidate,
            selection_reference_rt=preferred_rt,
            paired_istd_anchor_rt=paired_istd_anchor_rt,
        )
    return output_flow.detection_failure(candidates_result)


def find_peak_candidates(
    rt: np.ndarray,
    intensity: np.ndarray,
    config: ExtractionConfig,
    *,
    peak_min_prominence_ratio: float | None = None,
    local_minimum_finder: Callable[
        [np.ndarray, np.ndarray, ExtractionConfig],
        PeakCandidatesResult,
    ] = candidate_flow.find_peak_candidates_local_minimum,
    legacy_savgol_finder: Callable[..., PeakCandidatesResult] = (
        candidate_flow.find_peak_candidates_legacy_savgol
    ),
) -> PeakCandidatesResult:
    return candidate_flow.find_peak_candidates(
        rt,
        intensity,
        config,
        peak_min_prominence_ratio=peak_min_prominence_ratio,
        local_minimum_finder=local_minimum_finder,
        legacy_savgol_finder=legacy_savgol_finder,
    )
