from collections import deque
from dataclasses import replace

from xic_extractor.discovery.evidence_score import score_discovery_evidence
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings
from xic_extractor.mass_accuracy import is_within_mz_tolerance

_APEX_RT_EPSILON_MIN = 1e-6


def assign_peak_anchors(
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    settings: DiscoverySettings,
) -> tuple[DiscoveryCandidate, ...]:
    anchored = _assign_peak_anchors(
        candidates,
        precursor_mz_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
    )
    return _score_all_evidence(anchored, settings=settings)


def _assign_peak_anchors(
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    precursor_mz_tolerance_ppm: float,
) -> tuple[DiscoveryCandidate, ...]:
    groups: list[list[DiscoveryCandidate]] = []
    active_group_indexes: deque[int] = deque()
    for candidate in sorted(candidates, key=_anchor_grouping_sort_key):
        if not candidate.ms1_peak_found or not _peak_bounds_present(candidate):
            groups.append([candidate])
            continue

        while active_group_indexes and not is_within_mz_tolerance(
            candidate.precursor_mz,
            groups[active_group_indexes[0]][0].precursor_mz,
            tolerance_ppm=precursor_mz_tolerance_ppm,
        ):
            active_group_indexes.popleft()

        group_index = _matching_anchor_index(
            candidate,
            groups,
            group_indexes=active_group_indexes,
            precursor_mz_tolerance_ppm=precursor_mz_tolerance_ppm,
        )
        if group_index is None:
            groups.append([candidate])
            active_group_indexes.append(len(groups) - 1)
        else:
            groups[group_index].append(candidate)

    anchor_ids = _anchor_ids(groups)
    assigned_by_candidate_id: dict[str, DiscoveryCandidate] = {}
    for group, anchor_id in zip(groups, anchor_ids, strict=True):
        group_size = len(group)
        for candidate in group:
            assigned_by_candidate_id[candidate.candidate_id] = replace(
                candidate,
                feature_family_id=anchor_id,
                feature_family_size=group_size,
            )

    return tuple(
        assigned_by_candidate_id[candidate.candidate_id] for candidate in candidates
    )


def _score_all_evidence(
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    settings: DiscoverySettings,
) -> tuple[DiscoveryCandidate, ...]:
    scored: list[DiscoveryCandidate] = []
    for candidate in candidates:
        discovery_evidence = score_discovery_evidence(candidate, settings=settings)
        scored.append(
            replace(
                candidate,
                evidence_score=discovery_evidence.score,
                evidence_tier=discovery_evidence.tier,
                ms2_support=discovery_evidence.ms2_support,
                ms1_support=discovery_evidence.ms1_support,
                rt_alignment=discovery_evidence.rt_alignment,
            )
        )
    return tuple(scored)


def _matching_anchor_index(
    candidate: DiscoveryCandidate,
    groups: list[list[DiscoveryCandidate]],
    *,
    group_indexes: deque[int],
    precursor_mz_tolerance_ppm: float,
) -> int | None:
    for index in group_indexes:
        group = groups[index]
        if all(
            _same_peak_anchor(
                candidate,
                existing,
                precursor_mz_tolerance_ppm=precursor_mz_tolerance_ppm,
            )
            for existing in group
        ):
            return index
    return None


def _anchor_grouping_sort_key(
    candidate: DiscoveryCandidate,
) -> tuple[float, str, float, float, str]:
    return (candidate.precursor_mz, *_candidate_sort_key(candidate))


def _same_peak_anchor(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
    *,
    precursor_mz_tolerance_ppm: float,
) -> bool:
    return (
        first.raw_file == second.raw_file
        and first.sample_stem == second.sample_stem
        and first.neutral_loss_tag == second.neutral_loss_tag
        and first.ms1_peak_found
        and second.ms1_peak_found
        and is_within_mz_tolerance(
            first.precursor_mz,
            second.precursor_mz,
            tolerance_ppm=precursor_mz_tolerance_ppm,
        )
        and _peak_bounds_present(first)
        and _peak_bounds_present(second)
        and _apex_matches(first, second)
        and _peak_intervals_overlap(first, second)
    )


def _anchor_ids(groups: list[list[DiscoveryCandidate]]) -> list[str]:
    sorted_groups = sorted(groups, key=_group_sort_key)
    anchor_id_by_key: dict[tuple[str, ...], str] = {}
    for index, group in enumerate(sorted_groups, start=1):
        anchor_id_by_key[_group_identity_key(group)] = (
            f"{group[0].sample_stem}@F{index:04d}"
        )
    return [anchor_id_by_key[_group_identity_key(group)] for group in groups]


def _group_sort_key(
    group: list[DiscoveryCandidate],
) -> tuple[str, float, float, str]:
    earliest = min(group, key=_candidate_sort_key)
    return _candidate_sort_key(earliest)


def _candidate_sort_key(candidate: DiscoveryCandidate) -> tuple[str, float, float, str]:
    apex = (
        candidate.ms1_apex_rt
        if candidate.ms1_apex_rt is not None
        else candidate.best_seed_rt
    )
    return (candidate.sample_stem, apex, candidate.best_seed_rt, candidate.candidate_id)


def _group_identity_key(group: list[DiscoveryCandidate]) -> tuple[str, ...]:
    return tuple(sorted(candidate.candidate_id for candidate in group))


def _peak_bounds_present(candidate: DiscoveryCandidate) -> bool:
    return (
        candidate.ms1_apex_rt is not None
        and candidate.ms1_peak_rt_start is not None
        and candidate.ms1_peak_rt_end is not None
        and candidate.ms1_peak_rt_start <= candidate.ms1_peak_rt_end
    )


def _apex_matches(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> bool:
    if first.ms1_apex_rt is None or second.ms1_apex_rt is None:
        return False
    return abs(first.ms1_apex_rt - second.ms1_apex_rt) <= _APEX_RT_EPSILON_MIN


def _peak_intervals_overlap(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> bool:
    first_start = first.ms1_peak_rt_start
    first_end = first.ms1_peak_rt_end
    second_start = second.ms1_peak_rt_start
    second_end = second.ms1_peak_rt_end
    if (
        first_start is None
        or first_end is None
        or second_start is None
        or second_end is None
    ):
        return False
    return max(first_start, second_start) <= min(first_end, second_end)
