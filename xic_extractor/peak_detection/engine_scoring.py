from __future__ import annotations

from collections.abc import Callable

from xic_extractor.peak_detection.candidate_scoring import score_candidate
from xic_extractor.peak_detection.models import PeakCandidate, PeakCandidateScore
from xic_extractor.peak_detection.scoring_models import (
    ScoredCandidate,
    ScoringContext,
)


def score_with_context(
    candidate: PeakCandidate,
    context: ScoringContext,
    *,
    istd_confidence_note: str | None,
    evidence_role: str = "",
    istd_pair: str = "",
    paired_istd_anchor_rt: float | None = None,
    score_candidate_func: Callable[..., ScoredCandidate] = score_candidate,
) -> ScoredCandidate:
    return score_candidate_func(
        candidate,
        context,
        prior_rt=context.rt_prior,
        istd_confidence_note=istd_confidence_note,
        evidence_role=evidence_role,
        istd_pair=istd_pair,
        paired_istd_anchor_rt=paired_istd_anchor_rt,
    )


def candidate_score_summary(scored_candidate: ScoredCandidate) -> PeakCandidateScore:
    evidence_score = scored_candidate.evidence_score
    return PeakCandidateScore(
        candidate=scored_candidate.candidate,
        confidence=scored_candidate.confidence.value,
        reason=scored_candidate.reason,
        raw_score=evidence_score.raw_score if evidence_score is not None else None,
        support_labels=(
            evidence_score.support_labels if evidence_score is not None else ()
        ),
        concern_labels=(
            evidence_score.concern_labels if evidence_score is not None else ()
        ),
        cap_labels=evidence_score.cap_labels if evidence_score is not None else (),
        prior_rt=scored_candidate.prior_rt,
        quality_penalty=scored_candidate.quality_penalty,
        selection_quality_penalty=scored_candidate.selection_quality_penalty,
        severities=scored_candidate.severities,
        evidence_facts=scored_candidate.evidence_facts,
    )
