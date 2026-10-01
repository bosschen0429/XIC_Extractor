from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.legacy_savgol import (
    find_peak_candidates_legacy_savgol,
)
from xic_extractor.peak_detection.local_minimum import (
    find_peak_candidates_local_minimum,
)
from xic_extractor.peak_detection.models import PeakCandidate, PeakCandidatesResult
from xic_extractor.settings_schema import (
    ARBITRATED_RESOLVER_RETIRED_MESSAGE,
    CANONICAL_RESOLVER_MODE,
    RESOLVER_MODES,
)


def find_peak_candidates(
    rt: np.ndarray,
    intensity: np.ndarray,
    config: ExtractionConfig,
    *,
    peak_min_prominence_ratio: float | None = None,
    local_minimum_finder: Callable[
        [np.ndarray, np.ndarray, ExtractionConfig],
        PeakCandidatesResult,
    ] = find_peak_candidates_local_minimum,
    legacy_savgol_finder: Callable[..., PeakCandidatesResult] = (
        find_peak_candidates_legacy_savgol
    ),
) -> PeakCandidatesResult:
    resolver_mode = getattr(config, "resolver_mode", CANONICAL_RESOLVER_MODE)
    if resolver_mode == "local_minimum":
        return local_minimum_finder(rt, intensity, config)
    if resolver_mode == "arbitrated":
        raise ValueError(ARBITRATED_RESOLVER_RETIRED_MESSAGE)
    if resolver_mode == "legacy_savgol":
        return legacy_savgol_finder(
            rt,
            intensity,
            config,
            peak_min_prominence_ratio=peak_min_prominence_ratio,
        )
    allowed = ", ".join(RESOLVER_MODES[:-1]) + f", or {RESOLVER_MODES[-1]}"
    raise ValueError(f"unsupported resolver mode {resolver_mode!r}; must be {allowed}")


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
        proposal_sources=tuple(
            dict.fromkeys((*candidate.proposal_sources, source))
        ),
    )
