from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import numpy as np

from xic_extractor.config import ExtractionConfig
from xic_extractor.peak_detection.chrom_peak_candidate_adapter import (
    chrom_peak_segment_candidates,
)
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
    if resolver_mode in {"local_minimum", "region_first_safe_merge"}:
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


def augment_with_chrom_peak_segment_candidates(
    rt: np.ndarray,
    intensity: np.ndarray,
    config: ExtractionConfig,
    candidates_result: PeakCandidatesResult,
) -> PeakCandidatesResult:
    resolver_mode = getattr(config, "resolver_mode", CANONICAL_RESOLVER_MODE)
    if resolver_mode != CANONICAL_RESOLVER_MODE:
        return candidates_result
    chrom_candidates = chrom_peak_segment_candidates(rt, intensity, config)
    if not chrom_candidates:
        return candidates_result
    candidates = candidates_result.candidates
    for candidate in chrom_candidates:
        candidates = append_or_merge_chrom_peak_segment_candidate(
            candidates,
            candidate,
        )
    status = "OK" if candidates else candidates_result.status
    return replace(
        candidates_result,
        status=status,
        candidates=candidates,
        n_prominent_peaks=len(candidates),
    )


def append_or_merge_chrom_peak_segment_candidate(
    candidates: tuple[PeakCandidate, ...],
    chrom_candidate: PeakCandidate,
) -> tuple[PeakCandidate, ...]:
    merged: list[PeakCandidate] = []
    replaced = False
    for candidate in candidates:
        if _same_chrom_peak_segment_identity(candidate, chrom_candidate):
            merged.append(_chrom_boundary_upgrade_candidate(candidate, chrom_candidate))
            replaced = True
        else:
            merged.append(candidate)
    if not replaced:
        merged.append(chrom_candidate)
    return tuple(merged)


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


def _same_chrom_peak_segment_identity(
    candidate: PeakCandidate,
    chrom_candidate: PeakCandidate,
) -> bool:
    if _same_peak_candidate_identity(candidate, chrom_candidate):
        return True
    if candidate.selection_apex_index == chrom_candidate.selection_apex_index:
        return True
    return abs(candidate.selection_apex_rt - chrom_candidate.selection_apex_rt) <= 1e-9


def _chrom_boundary_upgrade_candidate(
    candidate: PeakCandidate,
    chrom_candidate: PeakCandidate,
) -> PeakCandidate:
    return replace(
        chrom_candidate,
        proposal_sources=_combine_proposal_sources(candidate, chrom_candidate),
        source_apex_rank=(
            candidate.source_apex_rank
            if candidate.source_apex_rank is not None
            else chrom_candidate.source_apex_rank
        ),
        cwt_best_scale=(
            chrom_candidate.cwt_best_scale
            if chrom_candidate.cwt_best_scale is not None
            else candidate.cwt_best_scale
        ),
        cwt_ridge_persistence=(
            chrom_candidate.cwt_ridge_persistence
            if chrom_candidate.cwt_ridge_persistence is not None
            else candidate.cwt_ridge_persistence
        ),
        ms2_evidence_peak_start=(
            chrom_candidate.ms2_evidence_peak_start
            if chrom_candidate.ms2_evidence_peak_start is not None
            else candidate.ms2_evidence_peak_start
        ),
        ms2_evidence_peak_end=(
            chrom_candidate.ms2_evidence_peak_end
            if chrom_candidate.ms2_evidence_peak_end is not None
            else candidate.ms2_evidence_peak_end
        ),
        merge_note=_combine_merge_note(
            candidate.merge_note,
            chrom_candidate.merge_note,
        ),
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


def _combine_merge_note(current: str, note: str) -> str:
    if not current:
        return note
    if not note or note in current.split("; "):
        return current
    return f"{current}; {note}"


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
