from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection import (
    detection_result as output_flow,
)
from xic_extractor.peak_detection import (
    scoring_summary as scoring_flow,
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
from xic_extractor.peak_detection.relaxed_search import (
    preferred_rt_recovery_from_candidates,
)
from xic_extractor.peak_detection.scoring_models import (
    ScoredCandidate,
    ScoringContext,
)
from xic_extractor.peak_detection.scoring_reason import score_breakdown_fields
from xic_extractor.peak_detection.selection import (
    select_candidate,
    selection_rt_for_scored_candidates,
)


def select_peak_and_area(
    candidates_result: PeakCandidatesResult,
    config: ExtractionConfig,
    *,
    preferred_rt: float | None = None,
    strict_preferred_rt: bool = False,
    scoring_context_builder: Callable[[PeakCandidate], ScoringContext] | None = None,
    istd_confidence_note: str | None = None,
    evidence_role: str = "",
    istd_pair: str = "",
    paired_istd_anchor_rt: float | None = None,
    preferred_rt_recovery: (
        tuple[PeakCandidate | None, PeakCandidatesResult | None] | None
    ) = None,
    score_candidate_func: Callable[..., ScoredCandidate] = score_candidate,
    evidence_selector: Callable[..., ScoredCandidate] = select_candidate_by_evidence,
) -> PeakDetectionResult:
    """Select and score supplied candidates without invoking a resolver.

    With no search recovery supplied, preferred-RT recovery is restricted to
    this candidate set, preserving the physical-candidate selection policy.
    """
    retained_candidate_recovery = preferred_rt_recovery is None
    if preferred_rt_recovery is None:
        preferred_rt_recovery = preferred_rt_recovery_from_candidates(
            candidates_result,
            config,
            preferred_rt=preferred_rt,
            strict_preferred_rt=strict_preferred_rt,
        )
    relaxed_candidate, relaxed_search_result = preferred_rt_recovery
    chosen_confidence: str | None = None
    chosen_reason: str | None = None
    chosen_severities: tuple[tuple[int, str], ...] = ()
    chosen_score_breakdown: tuple[tuple[str, str], ...] = ()
    candidate_scores: tuple[PeakCandidateScore, ...] = ()
    if candidates_result.status == "OK":
        all_candidates = candidates_result.candidates
        result_for_output = candidates_result
        if (
            not retained_candidate_recovery
            and relaxed_candidate is not None
            and relaxed_search_result is not None
        ):
            # External search can introduce a new proposal. Recovery within the
            # supplied set must preserve the keys of already-bound evidence.
            relaxed_candidate, relaxed_search_result = mark_relaxed_candidate(
                relaxed_candidate,
                relaxed_search_result,
            )
            all_candidates, relaxed_candidate = append_or_merge_relaxed_candidate(
                all_candidates,
                relaxed_candidate,
            )
            result_for_output = with_candidates(
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

    if relaxed_candidate is not None and relaxed_search_result is not None:
        relaxed_candidate, relaxed_search_result = mark_relaxed_candidate(
            relaxed_candidate,
            relaxed_search_result,
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
                score_breakdown=score_breakdown_fields(
                    scored_relaxed_search.evidence_score
                ),
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


def append_or_merge_relaxed_candidate(
    candidates: tuple[PeakCandidate, ...],
    candidate: PeakCandidate,
) -> tuple[tuple[PeakCandidate, ...], PeakCandidate]:
    merged_candidates = list(candidates)
    for index, existing in enumerate(merged_candidates):
        if not _same_peak_candidate_identity(existing, candidate):
            continue
        merged_candidate = replace(
            existing,
            proposal_sources=_combine_proposal_sources(existing, candidate),
        )
        merged_candidates[index] = merged_candidate
        return tuple(merged_candidates), merged_candidate
    return (*candidates, candidate), candidate


def mark_relaxed_candidate(
    relaxed_candidate: PeakCandidate,
    relaxed_search_result: PeakCandidatesResult,
) -> tuple[PeakCandidate, PeakCandidatesResult]:
    marked_candidate = _with_added_proposal_source(
        relaxed_candidate,
        "preferred_rt_recovery",
    )
    marked_candidates = tuple(
        marked_candidate
        if candidate is relaxed_candidate or candidate == relaxed_candidate
        else candidate
        for candidate in relaxed_search_result.candidates
    )
    return marked_candidate, with_candidates(relaxed_search_result, marked_candidates)


def with_candidates(
    candidates_result: PeakCandidatesResult,
    candidates: tuple[PeakCandidate, ...],
) -> PeakCandidatesResult:
    return PeakCandidatesResult(
        status=candidates_result.status,
        candidates=candidates,
        n_points=candidates_result.n_points,
        max_smoothed=candidates_result.max_smoothed,
        n_prominent_peaks=candidates_result.n_prominent_peaks,
    )


def _combine_proposal_sources(
    first: PeakCandidate,
    second: PeakCandidate,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            source
            for source in (*first.proposal_sources, *second.proposal_sources)
            if source
        )
    )


def _same_peak_candidate_identity(
    first: PeakCandidate,
    second: PeakCandidate,
) -> bool:
    return (
        first.selection_apex_rt == second.selection_apex_rt
        and first.peak == second.peak
    )


def _with_added_proposal_source(
    candidate: PeakCandidate,
    source: str,
) -> PeakCandidate:
    return replace(
        candidate,
        proposal_sources=tuple(dict.fromkeys((*candidate.proposal_sources, source))),
    )
