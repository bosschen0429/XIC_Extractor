from __future__ import annotations

from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidateScore,
    PeakCandidatesResult,
    PeakDetectionResult,
)


def detection_success(
    candidates_result: PeakCandidatesResult,
    candidate: PeakCandidate,
    *,
    confidence: str | None = None,
    reason: str | None = None,
    severities: tuple[tuple[int, str], ...] = (),
    score_breakdown: tuple[tuple[str, str], ...] = (),
    candidate_scores: tuple[PeakCandidateScore, ...] = (),
    selection_reference_rt: float | None = None,
    paired_istd_anchor_rt: float | None = None,
) -> PeakDetectionResult:
    return PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=candidates_result.n_points,
        max_smoothed=candidates_result.max_smoothed,
        n_prominent_peaks=candidates_result.n_prominent_peaks,
        candidates=candidates_result.candidates,
        confidence=confidence,
        reason=reason,
        severities=severities,
        score_breakdown=score_breakdown,
        candidate_scores=candidate_scores,
        selection_reference_rt=selection_reference_rt,
        paired_istd_anchor_rt=paired_istd_anchor_rt,
    )


def detection_failure(candidates_result: PeakCandidatesResult) -> PeakDetectionResult:
    return PeakDetectionResult(
        status=candidates_result.status,
        peak=None,
        n_points=candidates_result.n_points,
        max_smoothed=candidates_result.max_smoothed,
        n_prominent_peaks=candidates_result.n_prominent_peaks,
        candidates=candidates_result.candidates,
    )
