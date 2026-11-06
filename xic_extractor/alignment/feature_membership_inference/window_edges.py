"""Score eligible window edges and identify near-equal pairing alternatives."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_evidence import peak_correspondence_conflict
from xic_extractor.alignment.grouping_rules import GroupingRules
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.mass_accuracy import absolute_ppm_error, within_ppm
from xic_extractor.peak_detection.ms1_shape_identity import LocalPeakShapeComparisons

_WINDOW_AMBIGUITY_MARGIN = 0.05


@dataclass(frozen=True, slots=True)
class _WindowEdge:
    left_id: str
    right_id: str
    ambiguity_cost: float
    union_priority: float


def _window_candidate_edges(
    window: Sequence[SampleLocalMS1Owner],
    *,
    rt_frame: RtFrame,
    rules: GroupingRules,
    shape_comparisons: LocalPeakShapeComparisons,
    config: AlignmentConfig,
    orders: Mapping[str, int],
    corrected_rt: Mapping[str, float],
    common_rt: Mapping[str, float],
    track_indices: Mapping[str, int],
    trajectories: PeakTrajectories,
    counts: Counter[str],
    shape_conflicts: dict[str, set[str]],
) -> list[_WindowEdge]:
    """Apply pair eligibility and score supported edges in one mass window."""
    window_rts = sorted(corrected_rt[owner.owner_id] for owner in window)
    sparse_radius_min = min(120.0, config.max_rt_sec) / 60.0
    local_detection_count = {
        owner.owner_id: bisect_right(
            window_rts, corrected_rt[owner.owner_id] + sparse_radius_min
        )
        - bisect_left(window_rts, corrected_rt[owner.owner_id] - sparse_radius_min)
        for owner in window
    }
    arcs: list[_WindowEdge] = []
    by_order = sorted(
        window, key=lambda owner: (orders[owner.sample_stem], owner.owner_id)
    )
    for left_index, left in enumerate(by_order):
        for right in by_order[left_index + 1 :]:
            gap = orders[right.sample_stem] - orders[left.sample_stem]
            sparse_fallback = (
                gap > 16
                and max(
                    local_detection_count[left.owner_id],
                    local_detection_count[right.owner_id],
                )
                <= 10
            )
            distant_dense_pair = gap > 16 and not sparse_fallback
            if left.sample_stem == right.sample_stem:
                continue
            if not trajectories.permits_membership((left.owner_id, right.owner_id)):
                continue
            if not within_ppm(
                max(left.precursor_mz, right.precursor_mz),
                min(left.precursor_mz, right.precursor_mz),
                config.max_ppm,
            ):
                continue
            same_track = left.owner_id in track_indices and track_indices[
                left.owner_id
            ] == track_indices.get(right.owner_id)
            rt_delta_sec = (
                0.0
                if same_track
                and left.owner_id not in common_rt
                and right.owner_id not in common_rt
                else (
                    abs(corrected_rt[left.owner_id] - corrected_rt[right.owner_id])
                    * 60.0
                )
            )
            # A crowded window can contain only distant singletons, so it
            # cannot rely on established-band bridging to compare them.
            # Use that bridge's RT tolerance for dense distant pairs too;
            # an unobserved injection gap is not extra RT evidence.
            if distant_dense_pair:
                rt_limit_sec = config.preferred_rt_sec
            elif sparse_fallback:
                rt_limit_sec = max(120.0, config.preferred_rt_sec)
            else:
                rt_limit_sec = min(120.0, 45.0 + 4.0 * gap)
            rt_limit_sec = min(rt_limit_sec, config.max_rt_sec)
            if rt_delta_sec > rt_limit_sec:
                continue
            counts["owner_edge_evaluations"] += 1
            if peak_correspondence_conflict(
                (left,),
                (right,),
                config,
                rt_frame=rt_frame,
                shape_comparisons=shape_comparisons,
            ):
                shape_conflicts[left.owner_id].add(right.owner_id)
                shape_conflicts[right.owner_id].add(left.owner_id)
                continue
            # The tolerance describes the full separation of the
            # endpoints, as it does for owner-to-band costs. Measuring
            # only to their midpoint halves the mass evidence and can
            # turn distinct coeluting bands into near-equal alternatives.
            precursor_fraction = (
                absolute_ppm_error(
                    max(left.precursor_mz, right.precursor_mz),
                    min(left.precursor_mz, right.precursor_mz),
                )
                / config.max_ppm
            )
            method_fraction = rules.pair_mass_fraction(left, right, config)
            non_rt_cost = (
                (0.20 if sparse_fallback else 0.0)
                + 0.15 * min(precursor_fraction, 2.0)
                + 0.15 * min(method_fraction, 2.0)
            )
            rt_scale = (
                rt_limit_sec
                if sparse_fallback or distant_dense_pair
                else config.preferred_rt_sec
            )
            # Retain the uncertainty allowance for per-sample competition.
            # It must not make a distant pair outrank closer RT support when
            # committing unions: a missing injection is not stronger evidence.
            ambiguity_rt_scale = (
                rt_scale
                if sparse_fallback or distant_dense_pair
                else rt_scale + 3.0 * gap
            )
            arcs.append(
                _WindowEdge(
                    left.owner_id,
                    right.owner_id,
                    non_rt_cost + 0.70 * rt_delta_sec / ambiguity_rt_scale,
                    non_rt_cost + 0.70 * rt_delta_sec / rt_scale,
                )
            )
    return arcs


def _ambiguous_window_edges(
    arcs: Sequence[_WindowEdge],
    owner_by_id: Mapping[str, SampleLocalMS1Owner],
    seeded_owners: set[str],
) -> tuple[set[str], set[tuple[str, str]]]:
    """Resolve per-sample competition without discarding independent support."""
    competing: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
    neighbors: dict[str, set[str]] = defaultdict(set)
    for arc in arcs:
        cost, left_id, right_id = arc.ambiguity_cost, arc.left_id, arc.right_id
        competing[(left_id, owner_by_id[right_id].sample_stem)].append((cost, right_id))
        competing[(right_id, owner_by_id[left_id].sample_stem)].append((cost, left_id))
        neighbors[left_id].add(right_id)
        neighbors[right_id].add(left_id)
    ambiguous_window: set[str] = set()
    preferred_arcs: set[tuple[str, str]] = set()
    ambiguous_arcs: set[tuple[str, str]] = set()
    for (owner_id, _sample), options in competing.items():
        ranked = sorted(options)
        if len(ranked) < 2 or ranked[1][0] > ranked[0][0] + _WINDOW_AMBIGUITY_MARGIN:
            preferred_arcs.add((owner_id, ranked[0][1]))
            continue
        alternatives = tuple(
            candidate_id
            for cost, candidate_id in ranked
            if cost <= ranked[0][0] + _WINDOW_AMBIGUITY_MARGIN
        )
        ambiguous_arcs.update((owner_id, item) for item in alternatives)
        shared_neighbors = set.intersection(
            *(neighbors[candidate_id] for candidate_id in alternatives)
        ) - {owner_id}
        if len({owner_by_id[item].sample_stem for item in shared_neighbors}) >= 2:
            # A doublet in one sample competes for many other owners;
            # isolate its alternatives without discarding their band.
            ambiguous_window.update(alternatives)
        else:
            ambiguous_window.add(owner_id)
    # A contested pairing to one sample does not invalidate independent
    # reciprocal support from another. Keep that support, but do not use
    # the contested arcs to grow a band. Unsupported doublets stay weak.
    independently_supported = {
        owner_id
        for owner_id, neighbor_id in preferred_arcs
        if (neighbor_id, owner_id) in preferred_arcs
    } | seeded_owners
    ambiguous_window.difference_update(independently_supported)
    return ambiguous_window, ambiguous_arcs
