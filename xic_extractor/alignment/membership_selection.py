"""Bounded final-membership costs and decisions over admitted peak owners.

Final membership and bounded Backfill assignments consume this policy;
bootstrap remains conservative. A comparison universe shares one RT basis.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from statistics import median

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_rules import GroupingRules
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.alignment.sample_rt_calibration import RtEstimate
from xic_extractor.mass_accuracy import absolute_ppm_error, within_ppm
from xic_extractor.peak_detection.ms1_shape_identity import compare_local_peak_shapes

Partition = tuple[tuple[str, ...], ...]
# A recorded owner is located by its sample, measured m/z and native apex RT.
OwnerFacts = tuple[str, float, float]
OwnerPartition = tuple[tuple[OwnerFacts, ...], ...]
_RT_POPULATION_MIN_MEMBERS = 4
_RT_POPULATION_MIN_GAP_SEC = 20.0
_RT_POPULATION_RANGE_RATIO = 2.0


@dataclass(frozen=True, slots=True)
class AcceptedMembershipRisk:
    status: str
    phase: str
    selected_owners: tuple[OwnerFacts, ...]
    alternative_partition: OwnerPartition | None
    equivalent_alternatives: tuple[OwnerPartition, ...]
    selected_cost: float
    alternative_cost: float | None
    cost_gap: float | None
    rt_basis: str
    evidence_basis: str
    soft_contradictions: tuple[str, ...]
    risk_flags: tuple[str, ...]
    selected_peer_fit: float | None = None
    alternative_peer_fit: float | None = None
    peer_fit_gap: float | None = None
    rt_estimate: RtEstimate | None = None


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


def owner_facts(
    owners: Mapping[str, SampleLocalMS1Owner], owner_ids: Sequence[str]
) -> tuple[OwnerFacts, ...]:
    """Measured facts that locate recorded owners without their in-process keys."""
    facts = []
    for owner_id in owner_ids:
        owner = owners[owner_id]
        member = owner.cross_sample_member_subject
        sample = owner.sample_stem if member is None else member.sample_key
        facts.append((sample, owner.precursor_mz, owner.owner_apex_rt))
    if len(set(facts)) != len(facts):
        raise ValueError("distinct recorded owners share sample, m/z and apex RT")
    return tuple(sorted(facts))


def owner_partition_facts(
    owners: Mapping[str, SampleLocalMS1Owner], partition: Partition
) -> OwnerPartition:
    return tuple(sorted(owner_facts(owners, group) for group in partition))


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
        self.common_rts = {
            key: coordinate.rt_min
            for key, coordinate in coordinates.items()
            if coordinate is not None
        }
        common = bool(owners) and all(c is not None for c in coordinates.values())
        self.rt_basis = (
            "diagnostic_override"
            if common
            and any(
                c is not None and c.method == "diagnostic_override"
                for c in coordinates.values()
            )
            else "drift_corrected"
            if common
            else "raw"
        )
        self.rts = (
            {
                key: coordinate.rt_min
                for key, coordinate in coordinates.items()
                if coordinate is not None
            }
            if common
            else {o.owner_id: o.owner_apex_rt for o in owners}
        )
        self.rt_estimates = coordinates if common else dict.fromkeys(coordinates)
        self.rt_weights = {
            key: estimate.rt_weight if estimate is not None else 1.0
            for key, estimate in self.rt_estimates.items()
        }
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
                or "ambiguous" in owner.assignment_reason
                or rules.support_level(owner, owner, config) == "weak"
            ):
                self.weak.add(owner.owner_id)
        ordered_rts = sorted(self.rts.items(), key=lambda item: item[1])
        radius_sec = min(120.0, config.max_rt_sec)
        self.density = {}
        left = right = 0
        for key, rt in ordered_rts:
            # Keep the original closed-radius arithmetic at floating-point
            # boundaries; forming rt +/- radius in minutes can round differently.
            while (rt - ordered_rts[left][1]) * 60 > radius_sec:
                left += 1
            while right < len(ordered_rts) and (
                abs(rt - ordered_rts[right][1]) * 60 <= radius_sec
            ):
                right += 1
            self.density[key] = right - left
        self.pairs: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}
        self.group_costs: dict[tuple[str, ...], float] = {}
        self.population_gaps: dict[tuple[str, ...], bool] = {}
        self.group_peer_fits: dict[tuple[str, ...], float] = {}

    def partition_key(self, partition: Partition) -> tuple[object, ...]:
        return tuple(
            sorted(
                tuple(sorted(self.measured_keys[key] for key in group))
                for group in partition
            )
        )

    def pair(self, first: str, second: str) -> tuple[float, tuple[str, ...]]:
        key = (first, second) if first <= second else (second, first)
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
        rt_weight = min(self.rt_weights[first], self.rt_weights[second])
        cost = 0.70 * weight * rt_weight * delta / cfg.preferred_rt_sec
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
                cost += (
                    weight
                    * rt_weight
                    * 0.25
                    * min(region_gap / cfg.preferred_rt_sec, 1.0)
                )
                contradictions.append("region_disagreement")
        cost += 0.20 * (int(first in self.weak) + int(second in self.weak)) / 2
        return cost, tuple(contradictions)

    def group_cost(self, group: tuple[str, ...]) -> float:
        key = group if group in self.population_gaps else tuple(sorted(group))
        if key not in self.population_gaps:
            self.population_gaps[key] = self.has_rt_population_gap(key)
        if self.population_gaps[key]:
            return math.inf
        return self._group_evidence_cost(key)

    def _group_evidence_cost(self, group: tuple[str, ...]) -> float:
        # Production partitions are already canonical; reuse their cost without
        # sorting the same group again for every assignment and merge proposal.
        cached = self.group_costs.get(group)
        if cached is not None:
            return cached
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
            self.group_costs[key] = (
                1.0 + pair_cost + self._group_trend_cost(key)
                if math.isfinite(pair_cost)
                else math.inf
            )
        return self.group_costs[key]

    def fixed_group_cost(
        self, group: tuple[str, ...], original: tuple[str, ...]
    ) -> float:
        """Score an extension without reopening constraints between fixed members.

        Original members cannot move in Backfill. Their pre-existing hard
        conflict is not a new candidate's failure, nor an infinite baseline to
        subtract. Every pair involving a new owner still has to be feasible.
        The complete extension still obeys the RT-population constraint.
        """
        cost = (
            self._group_evidence_cost(group)
            if group == original
            else self.group_cost(group)
        )
        if math.isfinite(cost):
            return cost
        if group != original and self.has_rt_population_gap(group):
            return math.inf
        if len({self.owners[p].sample_stem for p in group}) != len(group):
            return math.inf
        fixed = frozenset(original)
        pair_cost = 0.0
        for first, second in combinations(group, 2):
            value = self.pair(first, second)[0]
            if not math.isfinite(value) and first in fixed and second in fixed:
                continue
            pair_cost = max(pair_cost, value)
        return 1.0 + pair_cost + self._group_trend_cost(group)

    def _group_trend_cost(self, key: tuple[str, ...]) -> float:
        trend = 0.0

        # A neighbor median stays inside the group's RT range. A range of
        # at most 90 seconds therefore cannot incur the trend penalty.
        if (
            len(key) > 2
            and (max(self.rts[p] for p in key) - min(self.rts[p] for p in key)) * 60
            > 90.0
        ):
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
                rt_weight = min(self.rt_weights[p] for p in (owner_id, *neighbors))
                trend = max(trend, rt_weight * 0.20 * max(0.0, residual / 90.0 - 1))
        return trend

    def has_rt_population_gap(self, group: tuple[str, ...]) -> bool:
        """Reject two concentrated populations in a complete common-RT group.

        This is not a pair veto: adding intervening observations can make a
        group feasible. Raw coordinates never fill gaps in calibration support.
        """
        if len(group) < _RT_POPULATION_MIN_MEMBERS or any(
            key not in self.common_rts for key in group
        ):
            return False
        rts = sorted(self.common_rts[key] for key in group)
        split = max(range(1, len(rts)), key=lambda i: rts[i] - rts[i - 1])
        gap_sec = (rts[split] - rts[split - 1]) * 60
        side_range_sec = max(rts[split - 1] - rts[0], rts[-1] - rts[split]) * 60
        # Coordinate subtraction can put an exact threshold a few ulps below it.
        threshold_sec = max(
            _RT_POPULATION_MIN_GAP_SEC, _RT_POPULATION_RANGE_RATIO * side_range_sec
        )
        return gap_sec >= threshold_sec or math.isclose(
            gap_sec, threshold_sec, rel_tol=0, abs_tol=1e-10
        )

    def cost(self, partition: Partition) -> float:
        return math.fsum(self.group_cost(group) for group in partition)

    def peer_fit(self, partition: Partition) -> float:
        """Mean owner-to-peer cost; separation retains its unit cost.

        This only resolves primary-cost ties. Unlike the worst pair, it sees
        whether a replacement fits the other members, without adding a weight
        or weakening the complete-group cost.
        """
        for group in partition:
            if group not in self.group_peer_fits:
                self.group_peer_fits[group] = (
                    math.fsum(self.pair(a, b)[0] for a, b in combinations(group, 2))
                    / (len(group) * (len(group) - 1) / 2)
                    if len(group) > 1
                    else 1.0
                )
        return (
            math.fsum(len(g) * self.group_peer_fits[g] for g in partition)
            / len(self.owners)
            if self.owners
            else 0.0
        )

    def refine_choices(
        self, partition: Partition
    ) -> tuple[Partition, dict[str, tuple[Partition, ...]]]:
        """Act on better retained alternatives, rather than merely flag them.

        Primary cost never increases. On a primary tie, a strict improvement
        in average peer fit selects the representative. Reuse the final sweep
        for risk publication instead of evaluating every alternative twice.
        """
        while True:
            alternatives_by_owner = {
                owner_id: self.alternatives(owner_id, partition)
                for group in partition
                for owner_id in group
            }
            selected_cost, selected_fit = self.cost(partition), self.peer_fit(partition)
            improvements = {
                candidate
                for alternatives in alternatives_by_owner.values()
                for candidate in alternatives
                if self.cost(candidate) < selected_cost - 1e-12
                or (
                    self.cost(candidate) <= selected_cost
                    and self.peer_fit(candidate) < selected_fit - 1e-12
                )
            }
            if not improvements:
                return partition, alternatives_by_owner
            best_cost = min(self.cost(p) for p in improvements)
            partition = self.merge(
                min(
                    (p for p in improvements if self.cost(p) == best_cost),
                    key=lambda p: (self.peer_fit(p), self.partition_key(p)),
                )
            )

    def initial_partition(self) -> Partition:
        """Consider whole-band seeds as well as feasible pairwise growth.

        Population feasibility is not hereditary: a continuous band can be
        feasible while an intermediate union has separated populations.
        Evidence-only growth proposes bands, but only complete feasible bands
        enter selection. Infeasible proposals retain their feasible sub-bands;
        no selected group is split after configuration selection.
        """
        singletons = canonical_partition([(key,) for key in self.owners])
        partition = self.merge(singletons)
        if not any(self.population_gaps.values()):
            return partition
        feasible_seeds = self.merge(singletons, propose_whole_bands=True)
        alternative = self.merge(feasible_seeds)
        return min(
            (partition, alternative),
            key=lambda p: (self.cost(p), self.peer_fit(p), self.partition_key(p)),
        )

    def merge(
        self, partition: Partition, *, propose_whole_bands: bool = False
    ) -> Partition:
        """Refresh full-band costs after every accepted union; no mutual-best gate."""
        active = set(partition)
        feasible_bands = set(partition)
        group_cost = (
            self._group_evidence_cost if propose_whole_bands else self.group_cost
        )
        moves: list[
            tuple[float, tuple[object, ...], tuple[str, ...], tuple[str, ...]]
        ] = []

        def propose(left: tuple[str, ...], right: tuple[str, ...]) -> None:
            union = tuple(sorted((*left, *right)))
            delta = group_cost(union) - group_cost(left) - group_cost(right)
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
            if propose_whole_bands and math.isfinite(self.group_cost(union)):
                # Accepted growth is a tree: retain the last feasible frontier
                # even if a later, larger proposal introduces a population gap.
                members = set(union)
                feasible_bands = {
                    band for band in feasible_bands if members.isdisjoint(band)
                }
                feasible_bands.add(union)
            for other in active:
                propose(union, other)
            active.add(union)
        return canonical_partition(
            tuple(feasible_bands if propose_whole_bands else active)
        )

    def alternatives(
        self, owner_id: str, partition: Partition
    ) -> tuple[Partition, ...]:
        """Final owner alternatives, including separation and occupancy swaps."""
        current = next(group for group in partition if owner_id in group)
        remainder = tuple(p for p in current if p != owner_id)
        others = [g for g in partition if g != current]
        variants = set()
        if remainder:
            separated = canonical_partition([*others, remainder, (owner_id,)])
            if math.isfinite(self.cost(separated)):
                variants.add(separated)
        sample = self.owners[owner_id].sample_stem
        for target in others:
            occupied = tuple(p for p in target if self.owners[p].sample_stem == sample)
            new_target = tuple(p for p in target if p not in occupied) + (owner_id,)
            new_current = (*remainder, *occupied)
            candidate = canonical_partition(
                [g for g in others if g != target] + [new_current, new_target]
            )
            # Swapping two same-sample singletons is not a different membership.
            if candidate != partition and math.isfinite(self.cost(candidate)):
                variants.add(candidate)
        ordered = sorted(variants, key=lambda p: (self.cost(p), self.partition_key(p)))
        if not ordered:
            return ()
        # Only the best primary-cost tier can win. Do not scan every pair in
        # more expensive alternatives merely to order their unused tie-breaks.
        best_cost = self.cost(ordered[0])
        best = [p for p in ordered if self.cost(p) == best_cost]
        rest = [p for p in ordered if self.cost(p) != best_cost]
        return tuple(
            sorted(best, key=lambda p: (self.peer_fit(p), self.partition_key(p)))
        ) + tuple(rest)

    def risks(
        self,
        partition: Partition,
        *,
        alternatives_by_owner: dict[str, tuple[Partition, ...]] | None = None,
    ) -> dict[str, AcceptedMembershipRisk]:
        result = {}
        selected_cost = self.cost(partition)
        selected_fit = self.peer_fit(partition)
        for group in partition:
            for owner_id in group:
                alternatives = (
                    alternatives_by_owner[owner_id]
                    if alternatives_by_owner is not None
                    else self.alternatives(owner_id, partition)
                )
                alternative = alternatives[0] if alternatives else None
                alternative_cost = (
                    self.cost(alternative) if alternative is not None else None
                )
                gap = (
                    alternative_cost - selected_cost
                    if alternative_cost is not None
                    else None
                )
                alternative_fit = (
                    self.peer_fit(alternative) if alternative is not None else None
                )
                equivalents = tuple(
                    p
                    for p in alternatives
                    if abs(self.cost(p) - selected_cost) <= 1e-9
                    and abs(self.peer_fit(p) - selected_fit) <= 1e-9
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
                    or self.rt_weights[owner_id] < 1.0
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
                    owner_facts(self.owners, group),
                    None
                    if alternative is None
                    else owner_partition_facts(self.owners, alternative),
                    tuple(
                        owner_partition_facts(self.owners, value)
                        for value in equivalents
                    ),
                    selected_cost,
                    alternative_cost,
                    gap,
                    self.rt_basis,
                    "bounded_merge_and_sample_assignment;complete_group_cost;mean_peer_fit_tiebreak",
                    contradictions,
                    tuple(sorted(flags)),
                    selected_fit,
                    alternative_fit,
                    alternative_fit - selected_fit
                    if alternative_fit is not None
                    else None,
                    self.rt_estimates[owner_id],
                )
        return result
