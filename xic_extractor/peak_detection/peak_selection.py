from __future__ import annotations

from collections.abc import Callable

from xic_extractor.peak_detection import (
    detection_result as output_flow,
)
from xic_extractor.peak_detection.candidate_scoring import (
    candidate_score_summary,
    score_candidate,
)
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
    *,
    preferred_rt: float | None = None,
    strict_preferred_rt: bool = False,
    scoring_context_builder: Callable[[PeakCandidate], ScoringContext] | None = None,
    evidence_role: str = "",
    istd_pair: str = "",
    paired_istd_anchor_rt: float | None = None,
    evidence_selector: Callable[..., ScoredCandidate] = select_candidate_by_evidence,
) -> PeakDetectionResult:
    """Select and score supplied candidates without invoking a resolver.

    Preferred-RT recovery is restricted to this candidate set, preserving the
    physical-candidate selection policy.
    """
    relaxed_candidate, relaxed_search_result = preferred_rt_recovery_from_candidates(
        candidates_result,
        preferred_rt=preferred_rt,
        strict_preferred_rt=strict_preferred_rt,
    )
    chosen_reason: str | None = None
    chosen_severities: tuple[tuple[int, str], ...] = ()
    chosen_score_breakdown: tuple[tuple[str, str], ...] = ()
    candidate_scores: tuple[PeakCandidateScore, ...] = ()
    if candidates_result.status == "OK":
        all_candidates = candidates_result.candidates
        result_for_output = candidates_result

        selection_rt = selection_rt_for_scored_candidates(
            candidates_result.candidates,
            preferred_rt=preferred_rt,
            strict_preferred_rt=strict_preferred_rt,
        )
        if relaxed_candidate is not None and relaxed_search_result is not None:
            selection_rt = preferred_rt

        if scoring_context_builder is not None:
            scored_candidates = []
            for candidate in all_candidates:
                context = scoring_context_builder(candidate)
                scored_candidates.append(
                    score_candidate(
                        candidate,
                        context,
                        prior_rt=context.rt_prior,
                        evidence_role=evidence_role,
                        istd_pair=istd_pair,
                        paired_istd_anchor_rt=paired_istd_anchor_rt,
                    )
                )
            candidate_scores = tuple(
                candidate_score_summary(scored_candidate)
                for scored_candidate in scored_candidates
            )
            chosen = evidence_selector(
                scored_candidates,
                selection_rt=selection_rt,
                strict_selection_rt=strict_preferred_rt,
            )
            best_candidate = chosen.candidate
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
            reason=chosen_reason,
            severities=chosen_severities,
            score_breakdown=chosen_score_breakdown,
            candidate_scores=candidate_scores,
            selection_reference_rt=selection_rt,
            paired_istd_anchor_rt=paired_istd_anchor_rt,
        )

    return output_flow.detection_failure(candidates_result)
