"""Grow, bridge and extend window bands using local injection RT trends."""

from __future__ import annotations

import math
import statistics
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from heapq import heappop, heappush
from typing import cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_evidence import peak_correspondence_conflict
from xic_extractor.alignment.grouping_rules import GroupingRules
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.mass_accuracy import absolute_ppm_error, within_ppm
from xic_extractor.peak_detection.ms1_shape_identity import LocalPeakShapeComparisons

from .sample_assignment import _Candidate, _solve_sample_assignment
from .window_edges import _WINDOW_AMBIGUITY_MARGIN, _WindowEdge

_FEATURE_TREND_NEIGHBORS = 6


_WINDOW_TREND_TOLERANCE_SEC = 90.0


@dataclass(frozen=True, slots=True)
class _NearestInjectionMedians:
    """Reuse one ordered member population for bounded local trend queries."""

    orders: tuple[int, ...]
    owners_by_order: tuple[tuple[SampleLocalMS1Owner, ...], ...]
    injection_order: Mapping[str, int]
    corrected_rt: Mapping[str, float] | None

    @classmethod
    def prepare(
        cls,
        members: Sequence[SampleLocalMS1Owner],
        injection_order: Mapping[str, int],
        corrected_rt: Mapping[str, float] | None = None,
    ) -> _NearestInjectionMedians:
        grouped: dict[int, list[SampleLocalMS1Owner]] = defaultdict(list)
        for owner in members:
            order = injection_order.get(owner.sample_stem)
            if order is not None:
                grouped[order].append(owner)
        orders = tuple(sorted(grouped))
        return cls(
            orders,
            tuple(
                tuple(sorted(grouped[order], key=lambda owner: owner.owner_id))
                for order in orders
            ),
            injection_order,
            corrected_rt,
        )

    def median(
        self,
        target: SampleLocalMS1Owner,
        *,
        exclude_owner_id: str | None = None,
    ) -> float | None:
        target_order = self.injection_order.get(target.sample_stem)
        if target_order is None:
            return None
        right = bisect_left(self.orders, target_order)
        left = right - 1
        nearest: list[SampleLocalMS1Owner] = []
        while len(nearest) < _FEATURE_TREND_NEIGHBORS and (
            left >= 0 or right < len(self.orders)
        ):
            use_left = right >= len(self.orders) or (
                left >= 0
                and (target_order - self.orders[left], self.orders[left])
                <= (self.orders[right] - target_order, self.orders[right])
            )
            index = left if use_left else right
            for owner in self.owners_by_order[index]:
                if owner.owner_id != exclude_owner_id:
                    nearest.append(owner)
                    if len(nearest) == _FEATURE_TREND_NEIGHBORS:
                        break
            if use_left:
                left -= 1
            else:
                right += 1
        if not nearest:
            return None
        return statistics.median(
            self.corrected_rt[owner.owner_id]
            if self.corrected_rt is not None
            else owner.owner_apex_rt
            for owner in nearest
        )


def _join_window_bands(
    members: dict[str, list[SampleLocalMS1Owner]],
    arcs: Sequence[_WindowEdge],
    *,
    shape_comparisons: LocalPeakShapeComparisons,
    config: AlignmentConfig,
    rules: GroupingRules,
    rt_frame: RtFrame,
    trajectories: PeakTrajectories,
    orders: Mapping[str, int],
    corrected_rt: Mapping[str, float],
    common_rt: Mapping[str, float],
    shape_conflicts: dict[str, set[str]],
    counts: Counter[str],
) -> None:
    """Grow owners normally; compare established bands as complete alternatives.

    An old pair priority no longer represents two established bands. Refresh
    their median RT/mass cost after each merge, preserving the existing score
    and gates. A closer band remains an alternative when sample occupancy
    prevents merging it: that conflict does not support a worse assignment.
    Only summaries and incident links of the changed band are recomputed.
    """
    samples = {
        key: {owner.sample_stem for owner in group} for key, group in members.items()
    }
    roots = {owner.owner_id: key for key, group in members.items() for owner in group}
    links: dict[str, dict[str, tuple[float, str, str]]] = {key: {} for key in members}
    for arc in arcs:
        left, right = roots[arc.left_id], roots[arc.right_id]
        if left == right:
            continue
        edge_priority = (arc.union_priority, arc.left_id, arc.right_id)
        previous = links[left].get(right)
        if previous is None or edge_priority < previous:
            links[left][right] = links[right][left] = edge_priority

    def center(key: str) -> tuple[float | None, float, float | None]:
        group = members[key]
        measured_rts = [
            common_rt[owner.owner_id] for owner in group if owner.owner_id in common_rt
        ]
        return (
            statistics.median(measured_rts) if measured_rts else None,
            statistics.median(owner.precursor_mz for owner in group),
            rules.mass_center(group),
        )

    centers = {key: center(key) for key in members}

    def priority(left: str, right: str) -> tuple[float, str, str]:
        pair_cost, first_id, second_id = links[left][right]
        first, second = centers[left], centers[right]
        if (
            min(len(members[left]), len(members[right])) < 2
            or first[0] is None
            or second[0] is None
        ):
            return pair_cost, first_id, second_id
        counts["feature_summary_comparisons"] += 1
        cost = (
            0.70 * 60.0 * abs(first[0] - second[0]) / config.preferred_rt_sec
            + 0.15
            * min(
                absolute_ppm_error(max(first[1], second[1]), min(first[1], second[1]))
                / config.max_ppm,
                2.0,
            )
            + 0.15 * min(rules.mass_fraction(first[2], second[2], config), 2.0)
        )
        return cost, first_id, second_id

    costs: dict[tuple[str, str], tuple[float, str, str]] = {}
    candidates: list[tuple[tuple[float, str, str], tuple[str, str]]] = []
    incident: dict[str, list[tuple[tuple[float, str, str], tuple[str, str]]]] = {
        key: [] for key in members
    }

    def refresh_cost(left: str, right: str) -> None:
        pair = (left, right)
        value = priority(left, right)
        costs[pair] = value
        entry = (value, pair)
        heappush(candidates, entry)
        heappush(incident[left], entry)
        heappush(incident[right], entry)

    def best_cost(key: str) -> float:
        heap = incident[key]
        while heap and costs.get(heap[0][1]) != heap[0][0]:
            heappop(heap)
        return heap[0][0][0] if heap else math.inf

    for left in links:
        for right in links[left]:
            if left < right:
                refresh_cost(left, right)
    while costs:
        deferred = []
        while candidates:
            value, pair = heappop(candidates)
            if costs.get(pair) != value:
                continue
            left, right = pair
            if samples[left] & samples[right]:
                # Occupancy cannot disappear as bands grow. Retain its cost as
                # an alternative, but it never becomes a merge candidate.
                continue
            if (
                min(len(members[left]), len(members[right])) < 2
                or centers[left][0] is None
                or centers[right][0] is None
                or value[0] <= best_cost(left)
                and value[0] <= best_cost(right)
            ):
                break
            deferred.append((value, pair))
        else:
            return
        # Removing or merging an edge can expose a previously blocked band's
        # best alternative. Reconsider those edges, not the entire graph.
        for entry in deferred:
            heappush(candidates, entry)
        left, right = pair
        del costs[pair]
        del links[left][right]
        del links[right][left]
        left_members, right_members = members[left], members[right]
        if not trajectories.permits_membership(
            owner.owner_id for owner in (*left_members, *right_members)
        ):
            continue
        if conflict := peak_correspondence_conflict(
            left_members,
            right_members,
            config,
            rt_frame=rt_frame,
            shape_comparisons=shape_comparisons,
        ):
            first_id, second_id = conflict
            shape_conflicts[first_id].add(second_id)
            shape_conflicts[second_id].add(first_id)
            continue
        if not rt_trend_supports_union(
            left_members,
            right_members,
            orders=orders,
            corrected_rt=corrected_rt,
            max_rt_sec=config.max_rt_sec,
        ):
            continue
        if (len(left_members), left) < (len(right_members), right):
            left, right = right, left
        members[left].extend(members.pop(right))
        samples[left].update(samples.pop(right))
        centers[left] = center(left)
        del centers[right]
        for neighbor, old_priority in links.pop(right).items():
            del links[neighbor][right]
            first, second = sorted((right, neighbor))
            del costs[(first, second)]
            kept = min(links[left].get(neighbor, old_priority), old_priority)
            links[left][neighbor] = links[neighbor][left] = kept
        for neighbor in links[left]:
            first, second = sorted((left, neighbor))
            refresh_cost(first, second)


def rt_trend_supports_union(
    left: Sequence[SampleLocalMS1Owner],
    right: Sequence[SampleLocalMS1Owner],
    *,
    orders: Mapping[str, int],
    corrected_rt: Mapping[str, float],
    max_rt_sec: float,
) -> bool:
    """Check the whole union against the shared local RT-band tolerance."""
    combined = tuple((*left, *right))
    if len(combined) < 10:
        return True
    trends = _NearestInjectionMedians.prepare(combined, orders, corrected_rt)
    for owner in combined:
        expected = trends.median(owner, exclude_owner_id=owner.owner_id)
        if expected is not None and abs(
            corrected_rt[owner.owner_id] - expected
        ) * 60.0 > min(_WINDOW_TREND_TOLERANCE_SEC, max_rt_sec):
            return False
    return True


def _bridge_separated_window_bands(
    members: dict[str, list[SampleLocalMS1Owner]],
    *,
    rt_frame: RtFrame,
    rules: GroupingRules,
    weak: Mapping[str, str],
    orders: Mapping[str, int],
    corrected_rt: Mapping[str, float],
    config: AlignmentConfig,
    counts: Counter[str],
    trajectories: PeakTrajectories,
) -> None:
    """Compare established bands across unobserved injections as whole bands."""
    while True:
        bands = {
            key: tuple(group)
            for key, group in members.items()
            if len(group) >= 2 and not any(owner.owner_id in weak for owner in group)
        }
        if len(bands) < 2:
            return
        samples = {
            key: {owner.sample_stem for owner in group} for key, group in bands.items()
        }
        spans = {
            key: (
                min(orders[owner.sample_stem] for owner in group),
                max(orders[owner.sample_stem] for owner in group),
            )
            for key, group in bands.items()
        }
        rt_centers = {
            key: statistics.median(corrected_rt[owner.owner_id] for owner in group)
            for key, group in bands.items()
        }
        mz_centers = {
            key: statistics.median(owner.precursor_mz for owner in group)
            for key, group in bands.items()
        }
        method_centers = {key: rules.mass_center(group) for key, group in bands.items()}
        offers: dict[str, list[tuple[float, str]]] = defaultdict(list)
        keys = sorted(bands)
        for index, left in enumerate(keys):
            for right in keys[index + 1 :]:
                left_start, left_end = spans[left]
                right_start, right_end = spans[right]
                gap = max(right_start - left_end, left_start - right_end)
                if gap <= 16 or samples[left] & samples[right]:
                    continue
                if not trajectories.permits_membership(
                    owner.owner_id for owner in (*bands[left], *bands[right])
                ):
                    continue
                counts["feature_summary_comparisons"] += 1
                left_mz, right_mz = mz_centers[left], mz_centers[right]
                if not within_ppm(
                    max(left_mz, right_mz), min(left_mz, right_mz), config.max_ppm
                ):
                    continue
                rt_delta_sec = abs(rt_centers[left] - rt_centers[right]) * 60.0
                if (
                    rt_delta_sec > config.preferred_rt_sec
                    or peak_correspondence_conflict(
                        bands[left],
                        bands[right],
                        config,
                        rt_frame=rt_frame,
                    )
                    is not None
                    or not rt_trend_supports_union(
                        bands[left],
                        bands[right],
                        orders=orders,
                        corrected_rt=corrected_rt,
                        max_rt_sec=config.max_rt_sec,
                    )
                ):
                    continue
                precursor_fraction = (
                    absolute_ppm_error(left_mz, right_mz) / config.max_ppm
                )
                method_fraction = rules.mass_fraction(
                    method_centers[left], method_centers[right], config
                )
                cost = (
                    0.70 * rt_delta_sec / config.preferred_rt_sec
                    + 0.15 * min(precursor_fraction, 2.0)
                    + 0.15 * min(method_fraction, 2.0)
                )
                offers[left].append((cost, right))
                offers[right].append((cost, left))
                counts["supported_candidate_arcs"] += 1

        best: dict[str, tuple[float, str]] = {}
        for key, options in offers.items():
            ranked = sorted(options)
            if (
                len(ranked) == 1
                or ranked[1][0] > ranked[0][0] + _WINDOW_AMBIGUITY_MARGIN
            ):
                best[key] = ranked[0]
        mutual = sorted(
            (cost, left, right)
            for left, (cost, right) in best.items()
            if left < right and best.get(right, (None, None))[1] == left
        )
        if not mutual:
            return
        _, left, right = mutual[0]
        members[left].extend(members.pop(right))


def _window_band_candidate(
    owner: SampleLocalMS1Owner,
    key: str,
    band: tuple[SampleLocalMS1Owner, ...],
    *,
    rt_frame: RtFrame,
    rules: GroupingRules,
    orders: Mapping[str, int],
    corrected_rt: Mapping[str, float],
    config: AlignmentConfig,
    trends: _NearestInjectionMedians | None = None,
    trajectories: PeakTrajectories | None = None,
    shape_conflicts: dict[str, set[str]] | None = None,
) -> _Candidate | None:
    """One shared local-trend rule for singleton assignment and Backfill admission."""
    if owner.sample_stem in {item.sample_stem for item in band}:
        return None
    if trajectories is not None and not trajectories.permits_membership(
        (owner.owner_id, *(item.owner_id for item in band))
    ):
        return None
    if not any(
        within_ppm(
            max(owner.precursor_mz, item.precursor_mz),
            min(owner.precursor_mz, item.precursor_mz),
            config.max_ppm,
        )
        for item in band
    ):
        return None
    if len(band) >= 10:
        expected = (
            trends or _NearestInjectionMedians.prepare(band, orders, corrected_rt)
        ).median(owner)
        tolerance_sec = min(
            config.max_rt_sec,
            max(_WINDOW_TREND_TOLERANCE_SEC, config.preferred_rt_sec),
        )
    else:
        expected = statistics.median(corrected_rt[item.owner_id] for item in band)
        nearest_gap = min(
            abs(orders[owner.sample_stem] - orders[item.sample_stem]) for item in band
        )
        tolerance_sec = min(
            config.max_rt_sec,
            120.0,
            max(config.preferred_rt_sec, 45.0 + 4.0 * nearest_gap),
        )
    if expected is None:
        return None
    rt_delta_sec = abs(corrected_rt[owner.owner_id] - expected) * 60.0
    if rt_delta_sec > tolerance_sec:
        return None
    if conflict := peak_correspondence_conflict(
        (owner,),
        band,
        config,
        rt_frame=rt_frame,
    ):
        if shape_conflicts is not None:
            first, second = conflict
            shape_conflicts[first].add(second)
            shape_conflicts[second].add(first)
        return None
    precursor_median = statistics.median(item.precursor_mz for item in band)
    precursor_fraction = (
        absolute_ppm_error(owner.precursor_mz, precursor_median) / config.max_ppm
    )
    method_fraction = rules.mass_fraction(
        rules.mass_center((owner,)), rules.mass_center(band), config
    )
    cost = min(
        0.999999,
        0.70 * rt_delta_sec / tolerance_sec
        + 0.15 * min(precursor_fraction, 2.0)
        + 0.15 * min(method_fraction, 2.0),
    )
    return _Candidate(
        feature_key=key,
        cost=cost,
        state="supported",
        reason="window_band_median_and_feature_trend",
        witness_owner_ids=tuple(item.owner_id for item in band),
        rt_basis="drift_corrected",
        rt_raw_delta_sec=None,
        rt_drift_corrected_delta_sec=rt_delta_sec,
    )


def _assign_window_singletons(
    members: dict[str, list[SampleLocalMS1Owner]],
    *,
    rt_frame: RtFrame,
    rules: GroupingRules,
    weak: dict[str, str],
    orders: Mapping[str, int],
    corrected_rt: Mapping[str, float],
    config: AlignmentConfig,
    counts: Counter[str],
    trajectories: PeakTrajectories,
    shape_conflicts: dict[str, set[str]],
) -> None:
    """Reassess singleton competition against the established bands."""
    bands = {key: tuple(group) for key, group in members.items() if len(group) >= 2}
    if not bands:
        return
    band_trends = {
        key: _NearestInjectionMedians.prepare(band, orders, corrected_rt)
        for key, band in bands.items()
        if len(band) >= 10
    }
    by_sample: dict[str, list[SampleLocalMS1Owner]] = defaultdict(list)
    candidates_by_owner: dict[str, tuple[_Candidate, ...]] = {}
    # Identity-ineligible owners never enter a window. Earlier pairwise
    # ambiguity must be reconsidered against the remaining band choices.
    for group in members.values():
        if len(group) != 1:
            continue
        owner = group[0]
        row_candidates = []
        for key, band in bands.items():
            candidate = _window_band_candidate(
                owner,
                key,
                band,
                orders=orders,
                corrected_rt=corrected_rt,
                config=config,
                trends=band_trends.get(key),
                trajectories=trajectories,
                shape_conflicts=shape_conflicts,
                rules=rules,
                rt_frame=rt_frame,
            )
            if candidate is not None:
                row_candidates.append(candidate)
        row_candidates.sort(
            key=lambda candidate: (cast(float, candidate.cost), candidate.feature_key)
        )
        if (
            len(row_candidates) >= 2
            and cast(float, row_candidates[1].cost)
            <= cast(float, row_candidates[0].cost) + _WINDOW_AMBIGUITY_MARGIN
        ):
            weak[owner.owner_id] = "alternative_bands_have_near_equal_cost"
            continue
        if row_candidates:
            by_sample[owner.sample_stem].append(owner)
            candidates_by_owner[owner.owner_id] = tuple(row_candidates)
    for sample in sorted(by_sample, key=lambda stem: (orders[stem], stem)):
        # Earlier samples may already have extended these initially fixed
        # bands. Do not let independently compatible singletons bridge two
        # contradictory shapes through the original band members.
        for owner in by_sample[sample]:
            remaining = []
            for candidate in candidates_by_owner[owner.owner_id]:
                conflict = peak_correspondence_conflict(
                    (owner,),
                    members[candidate.feature_key],
                    config,
                    rt_frame=rt_frame,
                )
                if conflict is None:
                    remaining.append(candidate)
                else:
                    first, second = conflict
                    shape_conflicts[first].add(second)
                    shape_conflicts[second].add(first)
            candidates_by_owner[owner.owner_id] = tuple(remaining)
        assignments, ambiguous = _solve_sample_assignment(
            by_sample[sample],
            candidates_by_owner,
            counts,
            ambiguity_margin=_WINDOW_AMBIGUITY_MARGIN,
        )
        for owner in by_sample[sample]:
            selected = assignments.get(owner.owner_id)
            if selected is None or owner.owner_id in ambiguous:
                if owner.owner_id in ambiguous:
                    weak[owner.owner_id] = "alternative_bands_have_equal_cost"
                continue
            members[selected].append(owner)
            members.pop(owner.owner_id)
            weak.pop(owner.owner_id, None)
