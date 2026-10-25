"""Bounded final-membership costs and decisions over admitted peak owners.

The membership entry owns activation. Bootstrap and Backfill admission do not
consume this policy. All comparisons in a mass neighbourhood share one RT basis.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from statistics import median

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_rules import GroupingRules
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.mass_accuracy import absolute_ppm_error, within_ppm
from xic_extractor.peak_detection.ms1_shape_identity import compare_local_peak_shapes

Partition = tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class AcceptedMembershipRisk:
    status: str
    phase: str
    selected_owner_ids: tuple[str, ...]
    alternative_partition: Partition | None
    equivalent_alternatives: tuple[Partition, ...]
    selected_cost: float
    alternative_cost: float | None
    cost_gap: float | None
    rt_basis: str
    evidence_basis: str
    soft_contradictions: tuple[str, ...]
    risk_flags: tuple[str, ...]


def measured_owner_key(owner: SampleLocalMS1Owner) -> tuple[object, ...]:
    """Physical measurements decide non-equivalent ties, never display IDs."""
    return (
        owner.precursor_mz,
        owner.owner_apex_rt,
        owner.owner_peak_start_rt,
        owner.owner_peak_end_rt,
        owner.owner_height,
        owner.owner_area,
        tuple(
            sorted(
                (
                    e.product_mz,
                    e.observed_neutral_loss_da,
                    e.evidence_score,
                    e.seed_event_count,
                )
                for e in owner.all_events
            )
        ),
    )


def canonical_partition(groups: Sequence[Sequence[str]]) -> Partition:
    return tuple(sorted(tuple(sorted(group)) for group in groups if group))


class MembershipComparison:
    """One finite comparison universe, prepared once and reused after moves."""

    def __init__(
        self,
        owners: Sequence[SampleLocalMS1Owner],
        *,
        rules: GroupingRules,
        config: AlignmentConfig,
        rt_frame: RtFrame,
        trajectories: PeakTrajectories,
    ) -> None:
        self.owners = {o.owner_id: o for o in owners}
        self.measured_keys = {o.owner_id: measured_owner_key(o) for o in owners}
        self.rules, self.config, self.trajectories = rules, config, trajectories
        regions = {o.owner_id: rt_frame.calibrated_peak_region(o) for o in owners}
        coordinates = {o.owner_id: rt_frame.membership_coordinate(o) for o in owners}
        common = bool(owners) and all(c is not None for c in coordinates.values())
        self.rt_basis = (
            "diagnostic_override"
            if common
            and any(
                c is not None and c[1] == "diagnostic_override"
                for c in coordinates.values()
            )
            else "drift_corrected"
            if common
            else "raw"
        )
        self.rts = (
            {
                key: coordinate[0]
                for key, coordinate in coordinates.items()
                if coordinate is not None
            }
            if common
            else {o.owner_id: o.owner_apex_rt for o in owners}
        )
        self.regions = {
            o.owner_id: (
                regions[o.owner_id]
                if self.rt_basis == "drift_corrected"
                and regions[o.owner_id] is not None
                else (None, self.rts[o.owner_id], None)
            )
            if common
            else (o.owner_peak_start_rt, o.owner_apex_rt, o.owner_peak_end_rt)
            for o in owners
        }
        self.orders = rt_frame.injection_order
        self.quality = {}
        self.weak = set()
        for owner in owners:
            quality = (
                owner.selected_integration.signal_quality
                if owner.selected_integration
                else None
            )
            self.quality[owner.owner_id] = (
                0.75
                if quality is None
                or quality.status != "available"
                or quality.low_signal_to_noise is None
                else 0.5
                if quality.low_signal_to_noise
                else 1.0
            )
            if (
                owner.is_provisional
                or owner.identity_conflict
                or "ambiguous" in owner.assignment_reason
                or rules.support_level(owner, owner, config) == "weak"
            ):
                self.weak.add(owner.owner_id)
        self.density = {
            key: sum(
                abs(rt - other) * 60 <= min(120.0, config.max_rt_sec)
                for other in self.rts.values()
            )
            for key, rt in self.rts.items()
        }
        self.pairs: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}
        self.group_costs: dict[tuple[str, ...], float] = {}

    def partition_key(self, partition: Partition) -> tuple[object, ...]:
        return tuple(
            sorted(
                tuple(sorted(self.measured_keys[key] for key in group))
                for group in partition
            )
        )

    def pair(self, first: str, second: str) -> tuple[float, tuple[str, ...]]:
        key = (min(first, second), max(first, second))
        if key not in self.pairs:
            self.pairs[key] = self._pair(first, second)
        return self.pairs[key]

    def _pair(self, first: str, second: str) -> tuple[float, tuple[str, ...]]:
        left, right = self.owners[first], self.owners[second]
        cfg = self.config
        method = self.rules.pair_evidence(left, right, cfg)
        if (
            left.sample_stem == right.sample_stem
            or not self.trajectories.permits_membership((first, second))
            or not within_ppm(
                max(left.precursor_mz, right.precursor_mz),
                min(left.precursor_mz, right.precursor_mz),
                cfg.max_ppm,
            )
            or method.identity_failure
            or (
                method.measurement_failure
                and not method.measurement_failure_allows_rt_continuity
            )
        ):
            return math.inf, ()
        delta = abs(self.rts[first] - self.rts[second]) * 60
        if delta > cfg.max_rt_sec:
            return math.inf, ()  # outside the declared candidate universe
        weight = min(self.quality[first], self.quality[second])
        cost = 0.70 * weight * delta / cfg.preferred_rt_sec
        cost += (
            0.15
            * absolute_ppm_error(
                max(left.precursor_mz, right.precursor_mz),
                min(left.precursor_mz, right.precursor_mz),
            )
            / cfg.max_ppm
        )
        cost += 0.15 * self.rules.pair_mass_fraction(left, right, cfg)
        gap = abs(
            self.orders.get(left.sample_stem, 0) - self.orders.get(right.sample_stem, 0)
        )
        density = max(self.density[first], self.density[second])
        cost += 0.10 * gap / (gap + 16) * density / (density + 10)
        contradictions = []
        if left.peak_shape is not None and right.peak_shape is not None:
            similarity = compare_local_peak_shapes(
                left.peak_shape, right.peak_shape
            ).similarity
            if similarity is not None and similarity < cfg.min_peak_shape_similarity:
                cost += (
                    weight
                    * 0.25
                    * (cfg.min_peak_shape_similarity - similarity)
                    / cfg.min_peak_shape_similarity
                )
                contradictions.append("shape_disagreement")
        a, b = self.regions[first], self.regions[second]
        assert a is not None and b is not None
        if (
            a[0] is not None
            and a[2] is not None
            and b[0] is not None
            and b[2] is not None
        ):
            region_gap = max(0.0, a[0] - b[2], b[0] - a[2]) * 60
            if region_gap:
                cost += weight * 0.25 * min(region_gap / cfg.preferred_rt_sec, 1.0)
                contradictions.append("region_disagreement")
        cost += 0.20 * (int(first in self.weak) + int(second in self.weak)) / 2
        return cost, tuple(contradictions)

    def group_cost(self, group: tuple[str, ...]) -> float:
        key = tuple(sorted(group))
        if key not in self.group_costs:
            if len({self.owners[p].sample_stem for p in key}) != len(key):
                self.group_costs[key] = math.inf
                return math.inf
            pair_cost = 0.0
            for first, second in combinations(key, 2):
                pair_cost = max(pair_cost, self.pair(first, second)[0])
                if not math.isfinite(pair_cost):
                    break
            trend = 0.0
            if len(key) > 2 and math.isfinite(pair_cost):
                for owner_id in key:
                    owner = self.owners[owner_id]
                    if owner.sample_stem not in self.orders:
                        continue
                    neighbors = sorted(
                        (p for p in key if p != owner_id),
                        key=lambda p: (
                            abs(
                                self.orders.get(self.owners[p].sample_stem, 0)
                                - self.orders[owner.sample_stem]
                            ),
                            self.measured_keys[p],
                        ),
                    )[:6]
                    residual = (
                        abs(self.rts[owner_id] - median(self.rts[p] for p in neighbors))
                        * 60
                    )
                    trend = max(trend, 0.20 * max(0.0, residual / 90.0 - 1))
            self.group_costs[key] = 1.0 + pair_cost + trend
        return self.group_costs[key]

    def cost(self, partition: Partition) -> float:
        return math.fsum(self.group_cost(group) for group in partition)

    def merge(self, partition: Partition) -> Partition:
        """Refresh full-band costs after every accepted union; no mutual-best gate."""
        active = set(partition)
        moves: list[
            tuple[float, tuple[object, ...], tuple[str, ...], tuple[str, ...]]
        ] = []

        def propose(left: tuple[str, ...], right: tuple[str, ...]) -> None:
            union = tuple(sorted((*left, *right)))
            delta = (
                self.group_cost(union) - self.group_cost(left) - self.group_cost(right)
            )
            if delta < -1e-12:
                heapq.heappush(
                    moves, (delta, self.partition_key((union,)), left, right)
                )

        for left, right in combinations(partition, 2):
            propose(left, right)
        while moves:
            _, _, left, right = heapq.heappop(moves)
            if left not in active or right not in active:
                continue
            active.remove(left)
            active.remove(right)
            union = tuple(sorted((*left, *right)))
            for other in active:
                propose(union, other)
            active.add(union)
        return canonical_partition(tuple(active))

    def alternatives(
        self, owner_id: str, partition: Partition
    ) -> tuple[Partition, ...]:
        """Final owner alternatives, including separation and occupancy swaps."""
        current = next(group for group in partition if owner_id in group)
        remainder = tuple(p for p in current if p != owner_id)
        others = [g for g in partition if g != current]
        variants = set()
        if remainder:
            variants.add(canonical_partition([*others, remainder, (owner_id,)]))
        sample = self.owners[owner_id].sample_stem
        for target in others:
            occupied = tuple(p for p in target if self.owners[p].sample_stem == sample)
            new_target = tuple(p for p in target if p not in occupied) + (owner_id,)
            new_current = (*remainder, *occupied)
            candidate = canonical_partition(
                [g for g in others if g != target] + [new_current, new_target]
            )
            if math.isfinite(self.cost(candidate)):
                variants.add(candidate)
        return tuple(
            sorted(variants, key=lambda p: (self.cost(p), self.partition_key(p)))
        )

    def risks(self, partition: Partition) -> dict[str, AcceptedMembershipRisk]:
        result = {}
        selected_cost = self.cost(partition)
        for group in partition:
            for owner_id in group:
                alternatives = self.alternatives(owner_id, partition)
                alternative = alternatives[0] if alternatives else None
                alternative_cost = (
                    self.cost(alternative) if alternative is not None else None
                )
                gap = (
                    alternative_cost - selected_cost
                    if alternative_cost is not None
                    else None
                )
                equivalents = tuple(
                    p for p in alternatives if abs(self.cost(p) - selected_cost) <= 1e-9
                )
                contradictions = tuple(
                    sorted(
                        {
                            reason
                            for peer in group
                            if peer != owner_id
                            for reason in self.pair(owner_id, peer)[1]
                        }
                    )
                )
                flags = set()
                if gap is not None and gap <= 0.05:
                    flags.add("accepted_competition")
                if contradictions:
                    flags.add("accepted_contradiction")
                region = self.regions[owner_id]
                assert region is not None
                if (
                    self.rt_basis != "drift_corrected"
                    or region[0] is None
                    or region[2] is None
                    or self.quality[owner_id] < 1.0
                ):
                    flags.add("low_rt_support")
                if owner_id in self.weak:
                    flags.add("weak_identity")
                result[owner_id] = AcceptedMembershipRisk(
                    "equivalent_alternatives"
                    if equivalents
                    else "evaluated_alternative"
                    if alternative
                    else "no_feasible_alternative",
                    "initial",
                    group,
                    alternative,
                    equivalents,
                    selected_cost,
                    alternative_cost,
                    gap,
                    self.rt_basis,
                    "bounded_merge_and_sample_assignment;complete_group_cost",
                    contradictions,
                    tuple(sorted(flags)),
                )
        return result
