from dataclasses import replace
from pathlib import Path

from xic_extractor.discovery.evidence_score import score_discovery_evidence
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings

_APEX_RT_EPSILON_MIN = 1e-6


def assign_peak_anchors(
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    settings: DiscoverySettings | None = None,
) -> tuple[DiscoveryCandidate, ...]:
    anchored = _assign_peak_anchors(candidates)
    scored = _score_all_evidence(anchored, settings=settings)
    return _bound_competing_inferred_precursor_hypotheses(scored)


def _bound_competing_inferred_precursor_hypotheses(
    candidates: tuple[DiscoveryCandidate, ...],
) -> tuple[DiscoveryCandidate, ...]:
    competing: dict[
        tuple[Path, str, str, int],
        list[DiscoveryCandidate],
    ] = {}
    for candidate in candidates:
        if (
            candidate.discovery_candidate_state != "ms1_feature_nl_rescued"
            or candidate.precursor_mz_basis != "product_plus_neutral_loss"
        ):
            continue
        key = (
            candidate.raw_file,
            candidate.sample_stem,
            candidate.neutral_loss_tag,
            candidate.best_ms2_scan_id,
        )
        competing.setdefault(key, []).append(candidate)

    demoted_by_id: dict[str, DiscoveryCandidate] = {}
    for hypotheses in competing.values():
        if len(hypotheses) < 2:
            continue
        preferred = min(hypotheses, key=_inferred_precursor_hypothesis_rank)
        for hypothesis in hypotheses:
            if hypothesis.candidate_id == preferred.candidate_id:
                continue
            demoted_by_id[hypothesis.candidate_id] = replace(
                hypothesis,
                discovery_candidate_state="review_only_ambiguous_coisolation",
                reason=(
                    "competing feature-backed inferred precursor hypotheses share "
                    "the best MS2 scan; retained for review; "
                    f"preferred_candidate_id={preferred.candidate_id}"
                ),
            )

    return tuple(
        demoted_by_id.get(candidate.candidate_id, candidate)
        for candidate in candidates
    )


def _inferred_precursor_hypothesis_rank(
    candidate: DiscoveryCandidate,
) -> tuple[float, float, float, str]:
    precursor_delta = candidate.scan_precursor_delta_da
    return (
        -float(candidate.evidence_score),
        -candidate.ms2_product_max_intensity,
        float("inf") if precursor_delta is None else abs(precursor_delta),
        candidate.candidate_id,
    )


def _assign_peak_anchors(
    candidates: tuple[DiscoveryCandidate, ...],
) -> tuple[DiscoveryCandidate, ...]:
    groups: list[list[DiscoveryCandidate]] = []
    for candidate in candidates:
        group_index = _matching_anchor_index(candidate, groups)
        if group_index is None:
            groups.append([candidate])
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
        assigned_by_candidate_id[candidate.candidate_id]
        for candidate in candidates
    )


def _score_all_evidence(
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    settings: DiscoverySettings | None = None,
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
) -> int | None:
    for index, group in enumerate(groups):
        if any(_same_peak_anchor(candidate, existing) for existing in group):
            return index
    return None


def _same_peak_anchor(
    first: DiscoveryCandidate,
    second: DiscoveryCandidate,
) -> bool:
    return (
        first.raw_file == second.raw_file
        and first.sample_stem == second.sample_stem
        and first.neutral_loss_tag == second.neutral_loss_tag
        and first.ms1_peak_found
        and second.ms1_peak_found
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
