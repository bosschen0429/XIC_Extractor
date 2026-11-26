from xic_extractor.peak_detection.models import PeakCandidate, PeakCandidatesResult
from xic_extractor.peak_detection.selection import select_candidate

PREFERRED_RT_RELAXED_MAX_DELTA_MIN: float = 0.35
RELAXED_CANDIDATE_MIN_INTENSITY_RATIO: float = 0.03


def select_preferred_relaxed_candidate(
    candidates: tuple[PeakCandidate, ...],
    *,
    preferred_rt: float,
) -> PeakCandidate | None:
    nearest_candidate = min(
        candidates,
        key=lambda candidate: abs(candidate.selection_apex_rt - preferred_rt),
    )
    delta = abs(nearest_candidate.selection_apex_rt - preferred_rt)
    if delta > PREFERRED_RT_RELAXED_MAX_DELTA_MIN:
        return None

    strongest_candidate = max(
        candidates, key=lambda candidate: candidate.selection_apex_intensity
    )
    if (
        nearest_candidate.selection_apex_intensity
        < strongest_candidate.selection_apex_intensity
        * RELAXED_CANDIDATE_MIN_INTENSITY_RATIO
    ):
        return None
    return nearest_candidate


def _recovery_eligible(
    *,
    preferred_rt: float | None,
    strict_preferred_rt: bool,
    current_candidate: PeakCandidate | None,
) -> bool:
    if preferred_rt is None or strict_preferred_rt:
        return False
    return (
        current_candidate is None
        or abs(current_candidate.selection_apex_rt - preferred_rt)
        > PREFERRED_RT_RELAXED_MAX_DELTA_MIN
    )


def preferred_rt_recovery_from_candidates(
    candidates_result: PeakCandidatesResult,
    *,
    preferred_rt: float | None,
    strict_preferred_rt: bool,
) -> tuple[PeakCandidate | None, PeakCandidatesResult | None]:
    """Recover the candidate nearest a non-strict preferred RT within this set."""
    current_candidate = (
        select_candidate(
            candidates_result.candidates,
            preferred_rt=preferred_rt,
            strict_preferred_rt=strict_preferred_rt,
        )
        if candidates_result.status == "OK"
        else None
    )
    if (
        not _recovery_eligible(
            preferred_rt=preferred_rt,
            strict_preferred_rt=strict_preferred_rt,
            current_candidate=current_candidate,
        )
        or preferred_rt is None
        or candidates_result.status != "OK"
    ):
        return None, None
    candidate = select_preferred_relaxed_candidate(
        candidates_result.candidates,
        preferred_rt=preferred_rt,
    )
    if candidate is None:
        return None, None
    return candidate, candidates_result
