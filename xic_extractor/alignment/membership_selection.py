"""Bounded final-membership costs and decisions over admitted peak owners.

Final membership and bounded Backfill assignments consume this policy;
bootstrap remains conservative. A comparison universe shares one RT basis.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from statistics import median
from typing import NamedTuple

import numpy as np

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_rules import GroupingRules
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_drift import (
    MIN_COEFFICIENT_MEMBERS,
    DriftCoefficients,
    candidate_coefficients,
    upper_pairs,
)
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.alignment.sample_rt_calibration import RtEstimate
from xic_extractor.mass_accuracy import absolute_ppm_error, within_ppm
from xic_extractor.peak_detection.ms1_shape_identity import compare_local_peak_shapes

Partition = tuple[tuple[str, ...], ...]
OwnerFacts = tuple[str, float, float]
OwnerPartition = tuple[tuple[OwnerFacts, ...], ...]
_RT_POPULATION_MIN_MEMBERS = 4
_RT_POPULATION_MIN_GAP_SEC = 20.0
_RT_POPULATION_RANGE_RATIO = 2.0
_COEFFICIENT_COST_SEC = 10.0
_NEIGHBOR_JUMP_SEC = 120.0


class _PairTerms(NamedTuple):
    """RT-independent parts of one pair cost, in their summation order."""

    weight: float
    mass: float
    composition: float
    neighborhood: float
    shape: float | None
    weak: float


@dataclass(frozen=True, slots=True)
class _PairArrays:
    """One group's pairs, in ``combinations`` order, with every input that is
    the same under each of its drift models.

    ``max_cost`` repeats ``MembershipComparison._compose`` elementwise in the
    same operation order, so each pair cost is the identical float.
    """

    first: np.ndarray
    second: np.ndarray
    weight: np.ndarray
    mass: np.ndarray
    composition: np.ndarray
    neighborhood: np.ndarray
    shape: np.ndarray
    weak: np.ndarray
    rts: np.ndarray
    rt_weights: np.ndarray
    starts: np.ndarray
    ends: np.ndarray
    regional: np.ndarray

    @classmethod
    def of(cls, comparison: MembershipComparison, key: tuple[str, ...]) -> _PairArrays:
        first, second = upper_pairs(len(key))
        values = comparison._prepare_pair_values()
        columns = values[comparison._pair_indices(key)].T
        columns[4, np.isnan(columns[4])] = 0.0
        regions = [comparison.regions[p] for p in key]
        starts = np.array([r[0] if r[0] is not None else np.nan for r in regions])  # type: ignore[index]
        ends = np.array([r[2] if r[2] is not None else np.nan for r in regions])  # type: ignore[index]
        weight, mass, composition, neighborhood, shape, weak = columns
        return cls(
            first,
            second,
            weight,
            mass,
            composition,
            neighborhood,
            shape,
            weak,
            np.array([comparison.rts[p] for p in key]),
            np.array([comparison.rt_weights[p] for p in key]),
            starts,
            ends,
            ~(
                np.isnan(starts[first])
                | np.isnan(ends[first])
                | np.isnan(starts[second])
                | np.isnan(ends[second])
            ),
        )

    def max_cost(
        self,
        comparison: MembershipComparison,
        key: tuple[str, ...],
        offsets: Mapping[str, float],
        unknown: frozenset[str],
    ) -> float:
        if not len(self.first):
            return 0.0
        offset = np.array([offsets.get(p, 0.0) for p in key])
        rt_weights = self.rt_weights * np.array(
            [0.5 if p in unknown else 1.0 for p in key]
        )
        rts, starts, ends = self.rts, self.starts, self.ends
        a, b = self.first, self.second
        preferred = comparison.config.preferred_rt_sec
        delta = np.abs((rts[a] - offset[a]) - (rts[b] - offset[b])) * 60
        rt_weight = np.minimum(rt_weights[a], rt_weights[b])
        cost = 0.70 * self.weight * rt_weight * delta / preferred
        cost = cost + self.mass
        cost = cost + self.composition
        cost = cost + self.neighborhood
        cost = cost + self.shape
        gap = (
            np.maximum(
                np.maximum(0.0, (starts[a] - offset[a]) - (ends[b] - offset[b])),
                (starts[b] - offset[b]) - (ends[a] - offset[a]),
            )
            * 60
        )
        regional = self.regional & (gap != 0)
        region_cost = self.weight * rt_weight * 0.25 * np.minimum(gap / preferred, 1.0)
        cost = cost + np.where(regional, region_cost, 0.0)
        cost = cost + self.weak
        return float(cost.max())


class _GroupInputs:
    """Inputs one proposed group shares across all of its drift models.

    Built lazily while that group's models are scored; nothing outlives the
    group's evaluation. ``witness`` is the zero model's costliest pair.
    """

    __slots__ = ("_comparison", "_key", "_pairs", "_neighbors", "witness")

    def __init__(self, comparison: MembershipComparison, key: tuple[str, ...]) -> None:
        self._comparison, self._key = comparison, key
        self._pairs: _PairArrays | None = None
        self._neighbors: tuple[np.ndarray, np.ndarray] | None | bool = False
        self.witness: tuple[str, str] | None = None

    def pairs(self) -> _PairArrays:
        if self._pairs is None:
            self._pairs = _PairArrays.of(self._comparison, self._key)
        return self._pairs

    def trend_neighbors(self) -> tuple[np.ndarray, np.ndarray] | None:
        if self._neighbors is False:
            self._neighbors = self._comparison._trend_neighbors(self._key)
        return self._neighbors  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class _GroupModel:
    coefficients: DriftCoefficients
    evidence: float
    population_gap: bool


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
        owner.peak.mz,
        owner.peak.rt_apex_min,
        owner.peak.rt_left_min,
        owner.peak.rt_right_min,
        owner.peak.height_raw,
        () if (area := owner.peak.area_raw_counts_seconds) is None else (area,),
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
        facts.append((sample, owner.peak.mz, owner.peak.rt_apex_min))
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
        fixed_coefficients: Mapping[tuple[str, ...], DriftCoefficients] | None = None,
    ) -> None:
        """``fixed_coefficients`` keeps settled groups' drift fits unchanged."""
        self.owners = {o.owner_id: o for o in owners}
        self.measured_keys = {o.owner_id: measured_owner_key(o) for o in owners}
        ranked = sorted(set(self.measured_keys.values()))
        dense = {measured: index for index, measured in enumerate(ranked)}
        self._measured_rank = {
            key: dense[value] for key, value in self.measured_keys.items()
        }
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
            else {o.owner_id: o.peak.rt_apex_min for o in owners}
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
            else (
                o.peak.rt_left_min,
                o.peak.rt_apex_min,
                o.peak.rt_right_min,
            )
            for o in owners
        }
        self.orders = rt_frame.injection_order
        self.quality = {}
        self.weak = set()
        for owner in owners:
            integration = owner.peak.integration
            quality = integration.signal_quality if integration is not None else None
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
                "ambiguous" in owner.assignment_reason
                or rules.support_level(owner, owner, config) == "weak"
            ):
                self.weak.add(owner.owner_id)
        ordered_rts = sorted(self.rts.items(), key=lambda item: item[1])
        radius_sec = 120.0
        self.density = {}
        left = right = 0
        for key, rt in ordered_rts:
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
        drift = rt_frame.drift_model if self.rt_basis == "drift_corrected" else None
        self.drift = drift if drift is not None and drift.anchors else None
        self._batch_lookup = (
            self.drift.batch_state_lookup() if self.drift is not None else None
        )
        shift = self.drift.sample_shift_min if self.drift is not None else {}
        self._sequence = {
            o.owner_id: (
                self.orders[o.sample_stem],
                self._measured_rank[o.owner_id],
                shift.get(o.sample_stem, 0.0),
            )
            for o in owners
            if o.sample_stem in self.orders
        }
        self._pair_terms: dict[tuple[str, str], _PairTerms | None] = {}
        self._pair_positions = {p: i for i, p in enumerate(sorted(self.owners))}
        size = len(self.owners)
        self._pair_row_offsets = {
            p: i * (2 * size - i - 1) // 2 - i - 1
            for p, i in self._pair_positions.items()
        }
        self._pair_values: np.ndarray | None = None
        self._pair_cost_values: np.ndarray | None = None
        self._models: dict[tuple[str, ...], tuple[_GroupModel, ...]] = {}
        self._batch_states: dict[tuple[str, ...], Mapping[str, float]] = {}
        self._fixed_coefficients = dict(fixed_coefficients or {})
        self._settled: tuple[tuple[frozenset[str], DriftCoefficients], ...] = ()
        self._pair_frames: dict[
            tuple[DriftCoefficients, tuple[str, ...]], Mapping[str, float] | None
        ] = {}
        self._mean_pairs: dict[
            tuple[tuple[str, ...], DriftCoefficients, tuple[str, ...]], float
        ] = {}
        self._fixed_models: dict[
            tuple[tuple[str, ...], tuple[str, ...]], _GroupModel
        ] = {}
        self._fixed_costs: dict[tuple[tuple[str, ...], tuple[str, ...]], float] = {}
        self._coefficient_cost = 0.70 * _COEFFICIENT_COST_SEC / config.preferred_rt_sec

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
            self.pairs[key] = self._compose(key[0], key[1])
            if self._pair_cost_values is not None and key[0] != key[1]:
                self._pair_cost_values[
                    self._pair_row_offsets[key[0]] + self._pair_positions[key[1]]
                ] = self.pairs[key][0]
        return self.pairs[key]

    def _pair_indices(self, key: tuple[str, ...]) -> np.ndarray:
        """Packed positions of a canonical group's pairs, in combinations order."""
        first, second = upper_pairs(len(key))
        positions = np.array([self._pair_positions[p] for p in key])
        rows = np.array([self._pair_row_offsets[p] for p in key])
        return rows[first] + positions[second]

    def _prepare_pair_values(self) -> np.ndarray:
        """Materialize numeric pair evidence once a drift fit needs it.

        Subsequent pair evaluations fill the same table. Uncomputed zero-model
        costs stay NaN so the scalar path retains its original short circuit.
        """
        if self._pair_values is None:
            size = len(self.owners)
            count = size * (size - 1) // 2
            self._pair_values = np.empty((count, 6))
            self._pair_cost_values = np.full(count, np.nan)
            for (a, b), terms in self._pair_terms.items():
                if terms is not None:
                    self._pair_values[
                        self._pair_row_offsets[a] + self._pair_positions[b]
                    ] = terms
            for (a, b), (cost, _) in self.pairs.items():
                if a != b:
                    self._pair_cost_values[
                        self._pair_row_offsets[a] + self._pair_positions[b]
                    ] = cost
        return self._pair_values

    def _terms(self, first: str, second: str) -> _PairTerms | None:
        """RT-independent pair evidence, computed once per pair."""
        key = (first, second) if first <= second else (second, first)
        if key in self._pair_terms:
            return self._pair_terms[key]
        left, right = self.owners[key[0]], self.owners[key[1]]
        cfg = self.config
        method = self.rules.pair_evidence(left, right, cfg)
        if (
            left.sample_stem == right.sample_stem
            or not self.trajectories.permits_membership(key)
            or not within_ppm(
                max(left.peak.mz, right.peak.mz),
                min(left.peak.mz, right.peak.mz),
                cfg.max_ppm,
            )
            or method.identity_failure
            or (
                method.measurement_failure
                and not method.measurement_failure_allows_rt_continuity
            )
        ):
            self._pair_terms[key] = None
            return None
        weight = min(self.quality[key[0]], self.quality[key[1]])
        mass = (
            0.15
            * absolute_ppm_error(
                max(left.peak.mz, right.peak.mz),
                min(left.peak.mz, right.peak.mz),
            )
            / cfg.max_ppm
        )
        composition = 0.15 * self.rules.pair_mass_fraction(left, right, cfg)
        gap = abs(
            self.orders.get(left.sample_stem, 0) - self.orders.get(right.sample_stem, 0)
        )
        density = max(self.density[key[0]], self.density[key[1]])
        neighborhood = 0.10 * gap / (gap + 16) * density / (density + 10)
        shape = None
        if left.peak_shape is not None and right.peak_shape is not None:
            similarity = compare_local_peak_shapes(
                left.peak_shape, right.peak_shape
            ).similarity
            if similarity is not None and similarity < cfg.min_peak_shape_similarity:
                shape = (
                    weight
                    * 0.25
                    * (cfg.min_peak_shape_similarity - similarity)
                    / cfg.min_peak_shape_similarity
                )
        weak = 0.20 * (int(key[0] in self.weak) + int(key[1] in self.weak)) / 2
        terms = _PairTerms(weight, mass, composition, neighborhood, shape, weak)
        self._pair_terms[key] = terms
        if self._pair_values is not None:
            self._pair_values[
                self._pair_row_offsets[key[0]] + self._pair_positions[key[1]]
            ] = terms
        return terms

    def _compose(
        self,
        first: str,
        second: str,
        offsets: Mapping[str, float] | None = None,
        unadjusted: frozenset[str] = frozenset(),
    ) -> tuple[float, tuple[str, ...]]:
        """Pair cost in one group's coordinate; offsets are its modeled drift.

        A member whose drift regressor is unknown keeps its general coordinate
        with half the RT influence.
        """
        terms = self._terms(first, second)
        if terms is None:
            return math.inf, ()
        cfg = self.config
        first_offset = offsets.get(first, 0.0) if offsets else 0.0
        second_offset = offsets.get(second, 0.0) if offsets else 0.0
        delta = (
            abs((self.rts[first] - first_offset) - (self.rts[second] - second_offset))
            * 60
        )
        rt_weight = min(
            self.rt_weights[first] * (0.5 if first in unadjusted else 1.0),
            self.rt_weights[second] * (0.5 if second in unadjusted else 1.0),
        )
        weight = terms.weight
        cost = 0.70 * weight * rt_weight * delta / cfg.preferred_rt_sec
        cost += terms.mass
        cost += terms.composition
        cost += terms.neighborhood
        contradictions = []
        if terms.shape is not None:
            cost += terms.shape
            contradictions.append("shape_disagreement")
        a, b = self.regions[first], self.regions[second]
        assert a is not None and b is not None
        if (
            a[0] is not None
            and a[2] is not None
            and b[0] is not None
            and b[2] is not None
        ):
            region_gap = (
                max(
                    0.0,
                    (a[0] - first_offset) - (b[2] - second_offset),
                    (b[0] - second_offset) - (a[2] - first_offset),
                )
                * 60
            )
            if region_gap:
                cost += (
                    weight
                    * rt_weight
                    * 0.25
                    * min(region_gap / cfg.preferred_rt_sec, 1.0)
                )
                contradictions.append("region_disagreement")
        cost += terms.weak
        return cost, tuple(contradictions)

    def fixed_pair(
        self, first: str, second: str, original: tuple[str, ...]
    ) -> tuple[float, tuple[str, ...]]:
        """One pair in a fixed Backfill group's coordinate (its original model)."""
        basis = tuple(sorted(original))
        coefficients = self.coefficients(basis)
        if not coefficients.nonzero_count:
            return self.pair(first, second)
        frame_key = (coefficients, basis)
        if frame_key not in self._pair_frames:
            self._pair_frames[frame_key] = self._sample_offsets(coefficients, basis)
        offsets, unknown = self._frame(self._pair_frames[frame_key], (first, second))
        return self._compose(first, second, offsets, unknown)

    def _model_pairs(
        self,
        key: tuple[str, ...],
        coefficients: DriftCoefficients,
        basis: tuple[str, ...],
    ) -> Callable[[str, str], tuple[float, tuple[str, ...]]]:
        """Score pairs among ``key`` in one model, sharing its drift frame."""
        if not coefficients.nonzero_count:
            return self.pair
        offsets, unknown = self._frame(self._sample_offsets(coefficients, basis), key)
        return lambda first, second: self._compose(first, second, offsets, unknown)

    def group_cost(self, group: tuple[str, ...]) -> float:
        """Least feasible cost over zero and fitted drift coefficients."""
        return min(
            (
                model.evidence
                for model in self._group_models(group)
                if not model.population_gap
            ),
            default=math.inf,
        )

    def coefficients(self, group: tuple[str, ...]) -> DriftCoefficients:
        """The drift coefficients behind this group's cost; zero when none help."""
        fixed = self._fixed_coefficients.get(tuple(sorted(group)))
        if fixed is not None:
            return fixed
        feasible = [m for m in self._group_models(group) if not m.population_gap]
        if not feasible:
            return DriftCoefficients()
        return min(
            feasible, key=lambda m: (m.evidence, m.coefficients.nonzero_count)
        ).coefficients

    def _group_evidence_cost(self, group: tuple[str, ...]) -> float:
        return min(model.evidence for model in self._group_models(group))

    def _group_models(self, group: tuple[str, ...]) -> tuple[_GroupModel, ...]:
        cached = self._models.get(group)
        if cached is not None:
            return cached
        key = tuple(sorted(group))
        if key not in self._models:
            inputs = _GroupInputs(self, key)
            zero = self._evaluate(key, DriftCoefficients(), inputs=inputs)
            jumps = math.isfinite(zero.evidence) and self._breaks_sequence(key, 0.0)
            models = [
                _GroupModel(zero.coefficients, math.inf, False) if jumps else zero
            ]
            candidates = (
                self._candidate_coefficients(key)
                if math.isfinite(zero.evidence)
                else ()
            )
            if candidates:
                bound = math.inf if zero.population_gap or jumps else zero.evidence
                for coefficients in candidates:
                    model = self._evaluate(
                        key, coefficients, bound=bound, inputs=inputs
                    )
                    if math.isfinite(model.evidence) and self._breaks_sequence(
                        key, coefficients.sample_shift
                    ):
                        model = _GroupModel(coefficients, math.inf, False)
                    models.append(model)
            self._models[key] = tuple(models)
            self.group_costs[key] = models[0].evidence
            self.population_gaps[key] = models[0].population_gap
        return self._models[key]

    def _breaks_sequence(
        self,
        key: tuple[str, ...],
        sample_shift: float,
        fixed: frozenset[str] = frozenset(),
    ) -> bool:
        """Whether injection-order neighbours jump beyond ``_NEIGHBOR_JUMP_SEC``
        after removing ``sample_shift`` times each sample's shift M.

        A jump between two ``fixed`` members is not checked: Backfill cannot
        move original members, so only jumps touching a new owner count.

        The batch-state term a*B is never removed: a group's own trajectory
        can define B and would then excuse its own jump. A sample whose shift
        is unknown keeps its general coordinate; a sample without an injection
        order has no neighbours. A zero model broken only by a jump does not
        bound the drift candidates, since one of them may remove the jump.
        """
        sequence = self._sequence
        placed = sorted(
            (p for p in key if p in sequence), key=lambda p: sequence[p][:2]
        )
        limit = _NEIGHBOR_JUMP_SEC / 60.0
        previous: tuple[str, float] | None = None
        for p in placed:
            value = self.rts[p] - sample_shift * sequence[p][2]
            if (
                previous is not None
                and abs(value - previous[1]) > limit
                and not (p in fixed and previous[0] in fixed)
            ):
                return True
            previous = (p, value)
        return False

    def _candidate_coefficients(
        self, key: tuple[str, ...]
    ) -> tuple[DriftCoefficients, ...]:
        if self.drift is None:
            return ()
        span_sec = (max(self.rts[p] for p in key) - min(self.rts[p] for p in key)) * 60
        if span_sec * 0.95 / self.config.preferred_rt_sec < self._coefficient_cost:
            return ()
        fitted: tuple[DriftCoefficients, ...] = ()
        if len(key) >= MIN_COEFFICIENT_MEMBERS:
            batch_state = self._batch_state(key)
            shift = self.drift.sample_shift_min
            fitted = candidate_coefficients(
                tuple(
                    (
                        self.rts[p],
                        batch_state.get(self.owners[p].sample_stem),
                        shift.get(self.owners[p].sample_stem),
                    )
                    for p in key
                )
            )
        settled = sorted(
            {
                coefficients
                for members, coefficients in self._settled
                if coefficients not in fitted and not members.isdisjoint(key)
            },
            key=lambda c: (c.time, c.sample_shift),
        )
        return (*fitted, *settled)

    def settle(self, partition: Partition) -> None:
        """Offer each settled group's drift fit to every group it overlaps.

        Moving one owner refits that group's coefficients. A refit
        perturbation can shift a worst pair the moved owner is not part of.
        The owner should be judged by the model of the group it joins or
        leaves. Own refits remain candidates; the settled set stays fixed
        while reassignment runs, so cost remains a function of the partition.
        """
        self._settled = tuple(
            (frozenset(group), coefficients)
            for group in partition
            if (coefficients := self.coefficients(group)).nonzero_count
        )
        self._models.clear()
        self.group_peer_fits.clear()

    def _batch_state(self, key: tuple[str, ...]) -> Mapping[str, float]:
        if key not in self._batch_states:
            assert self._batch_lookup is not None
            self._batch_states[key] = self._batch_lookup(
                key, float(median(self.rts[p] for p in key))
            )
        return self._batch_states[key]

    def _sample_offsets(
        self, coefficients: DriftCoefficients, basis: tuple[str, ...]
    ) -> Mapping[str, float] | None:
        """Each sample's modeled drift under one model; ``basis`` owns B.

        None when the model is zero or the RT basis carries no drift; samples
        whose regressor is unknown are absent.
        """
        if not coefficients.nonzero_count or self.drift is None:
            return None
        return self.drift.sample_offsets(coefficients, self._batch_state(basis))

    def _frame(
        self, by_sample: Mapping[str, float] | None, members: Sequence[str]
    ) -> tuple[dict[str, float], frozenset[str]]:
        """The members' modeled drift; only the members a consumer scores.

        Members whose regressor is unknown are returned separately; they keep
        their general coordinate.
        """
        if by_sample is None:
            return {}, frozenset()
        offsets = {}
        for owner_id in members:
            sample = self.owners[owner_id].sample_stem
            if sample in by_sample:
                offsets[owner_id] = by_sample[sample]
        return offsets, frozenset(members).difference(offsets)

    def _witness_cost(
        self,
        witness: tuple[str, str],
        coefficients: DriftCoefficients,
        basis: tuple[str, ...],
    ) -> float:
        """One pair's cost in a drift model, framed as ``_frame`` frames it.

        A sample's modeled drift depends only on its own regressors, so the
        pair needs only its two samples' offsets.
        """
        assert self.drift is not None
        batch_state = self._batch_state(basis)
        offsets = {}
        for owner_id in witness:
            sample = self.owners[owner_id].sample_stem
            offset = coefficients.offset_min(
                batch_state.get(sample), self.drift.sample_shift_min.get(sample)
            )
            if offset is not None:
                offsets[owner_id] = offset
        unknown = frozenset(witness).difference(offsets)
        return self._compose(*witness, offsets, unknown)[0]

    def _evaluate(
        self,
        key: tuple[str, ...],
        coefficients: DriftCoefficients,
        basis: tuple[str, ...] | None = None,
        *,
        bound: float = math.inf,
        inputs: _GroupInputs | None = None,
    ) -> _GroupModel:
        """Score one model; above ``bound`` it is reported as infinite.

        Each (group, model) is scored once, so its drift frame is not retained.
        """
        if len({self.owners[p].sample_stem for p in key}) != len(key):
            return _GroupModel(coefficients, math.inf, False)
        model_cost = self._coefficient_cost * coefficients.nonzero_count
        if (
            inputs is not None
            and inputs.witness is not None
            and math.isfinite(bound)
            and coefficients.nonzero_count
            and 1.0
            + self._witness_cost(inputs.witness, coefficients, basis or key)
            + model_cost
            >= bound
        ):
            return _GroupModel(coefficients, math.inf, False)
        offsets, unknown = self._frame(
            self._sample_offsets(coefficients, basis or key), key
        )
        pair_cost: float | None
        if inputs is not None and (offsets or unknown):
            pair_cost = inputs.pairs().max_cost(self, key, offsets, unknown)
            if not math.isfinite(pair_cost) or 1.0 + pair_cost + model_cost >= bound:
                return _GroupModel(coefficients, math.inf, False)
            evidence = (
                1.0 + pair_cost + self._group_trend_cost(key, offsets, unknown, inputs)
            )
            if coefficients.nonzero_count:
                evidence += model_cost
            return _GroupModel(
                coefficients,
                evidence,
                self.has_rt_population_gap(key, offsets, unknown),
            )
        pair_cost = None
        if (
            not (offsets or unknown)
            and len(key) > 2
            and self._pair_cost_values is not None
        ):
            cached = self._pair_cost_values[self._pair_indices(key)]
            if not np.isnan(cached).any():
                worst = int(cached.argmax())
                pair_cost = float(cached[worst])
                if inputs is not None:
                    first_index, second_index = upper_pairs(len(key))
                    inputs.witness = (
                        key[first_index[worst]],
                        key[second_index[worst]],
                    )
        if pair_cost is None:
            pair_cost = 0.0
            for first, second in combinations(key, 2):
                value = (
                    self._compose(first, second, offsets, unknown)[0]
                    if offsets or unknown
                    else self.pair(first, second)[0]
                )
                if inputs is not None and (inputs.witness is None or value > pair_cost):
                    inputs.witness = (first, second)
                pair_cost = max(pair_cost, value)
                if (
                    not math.isfinite(pair_cost)
                    or 1.0 + pair_cost + model_cost >= bound
                ):
                    return _GroupModel(coefficients, math.inf, False)
        elif not math.isfinite(pair_cost) or 1.0 + pair_cost + model_cost >= bound:
            return _GroupModel(coefficients, math.inf, False)
        evidence = (
            1.0 + pair_cost + self._group_trend_cost(key, offsets, unknown, inputs)
        )
        if coefficients.nonzero_count:
            evidence += model_cost
        return _GroupModel(
            coefficients, evidence, self.has_rt_population_gap(key, offsets, unknown)
        )

    def fixed_group_model(
        self, group: tuple[str, ...], original: tuple[str, ...]
    ) -> _GroupModel:
        """A Backfill extension under its original group's model, never refit."""
        key, basis = tuple(sorted(group)), tuple(sorted(original))
        if (key, basis) not in self._fixed_models:
            self._fixed_models[(key, basis)] = self._evaluate(
                key, self.coefficients(basis), basis
            )
        return self._fixed_models[(key, basis)]

    def fixed_group_cost(
        self, group: tuple[str, ...], original: tuple[str, ...]
    ) -> float:
        """Score an extension without reopening constraints between fixed members.

        Original members cannot move in Backfill. Their pre-existing hard
        conflict is not a new candidate's failure, nor an infinite baseline to
        subtract. Every pair involving a new owner still has to be feasible,
        and so does every injection-order neighbour jump involving one.
        The complete extension still obeys the RT-population constraint, and
        keeps the original group's drift coefficients rather than refitting.
        """
        key, basis = tuple(sorted(group)), tuple(sorted(original))
        if (key, basis) not in self._fixed_costs:
            self._fixed_costs[(key, basis)] = self._fixed_group_cost(key, basis)
        return self._fixed_costs[(key, basis)]

    def _fixed_group_cost(self, key: tuple[str, ...], basis: tuple[str, ...]) -> float:
        model = self.fixed_group_model(key, basis)
        if key != basis and self._breaks_sequence(
            key, model.coefficients.sample_shift, frozenset(basis)
        ):
            return math.inf
        if math.isfinite(model.evidence) and (key == basis or not model.population_gap):
            return model.evidence
        coefficients = model.coefficients
        offsets, unknown = self._frame(self._sample_offsets(coefficients, basis), key)
        if key != basis and self.has_rt_population_gap(key, offsets, unknown):
            return math.inf
        if len({self.owners[p].sample_stem for p in key}) != len(key):
            return math.inf
        fixed = frozenset(basis)
        pair_cost = 0.0
        for first, second in combinations(key, 2):
            value = self._compose(first, second, offsets, unknown)[0]
            if not math.isfinite(value) and first in fixed and second in fixed:
                continue
            pair_cost = max(pair_cost, value)
        cost = 1.0 + pair_cost + self._group_trend_cost(key, offsets, unknown)
        if coefficients.nonzero_count:
            cost += self._coefficient_cost * coefficients.nonzero_count
        return cost

    def fixed_peer_fit(
        self, groups: Sequence[tuple[tuple[str, ...], tuple[str, ...]]]
    ) -> float:
        """Mean peer fit of a Backfill configuration of (group, original) pairs.

        Each extension is scored in its original group's model, as its cost is.
        """
        total = math.fsum(
            len(group)
            * self._mean_pair(
                tuple(sorted(group)),
                self.coefficients(tuple(sorted(original))),
                tuple(sorted(original)),
            )
            for group, original in groups
        )
        return total / len(self.owners) if self.owners else 0.0

    def _mean_pair(
        self,
        key: tuple[str, ...],
        coefficients: DriftCoefficients,
        basis: tuple[str, ...],
    ) -> float:
        if len(key) < 2:
            return 1.0
        cache_key = (key, coefficients, basis)
        if cache_key not in self._mean_pairs:
            pair = self._model_pairs(key, coefficients, basis)
            self._mean_pairs[cache_key] = math.fsum(
                pair(a, b)[0] for a, b in combinations(key, 2)
            ) / (len(key) * (len(key) - 1) / 2)
        return self._mean_pairs[cache_key]

    def _group_trend_cost(
        self,
        key: tuple[str, ...],
        offsets: Mapping[str, float] | None = None,
        unadjusted: frozenset[str] = frozenset(),
        inputs: _GroupInputs | None = None,
    ) -> float:
        """Worst weighted residual from the six nearest injections' median RT."""
        rts = np.array(
            [self.rts[p] - (offsets.get(p, 0.0) if offsets else 0.0) for p in key]
        )
        if len(key) <= 2 or (rts.max() - rts.min()) * 60 <= 90.0:
            return 0.0
        found = (
            inputs.trend_neighbors()
            if inputs is not None
            else self._trend_neighbors(key)
        )
        if found is None:
            return 0.0
        ordered, neighbors = found
        weights = np.array(
            [self.rt_weights[p] * (0.5 if p in unadjusted else 1.0) for p in key]
        )
        residual = np.abs(rts[ordered] - np.median(rts[neighbors], axis=1)) * 60
        rt_weight = np.minimum(weights[ordered], weights[neighbors].min(axis=1))
        return float((rt_weight * 0.20 * np.maximum(0.0, residual / 90.0 - 1)).max())

    def _trend_neighbors(
        self, key: tuple[str, ...]
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Members with an injection order and their six nearest injections.

        Neighbors are ordered by injection distance, then measured facts,
        then group order, as a stable sort on those keys would order them.
        They do not depend on RT, so every drift model of a group shares them.
        """
        samples = [self.owners[p].sample_stem for p in key]
        ordered = np.array([i for i, s in enumerate(samples) if s in self.orders])
        if not len(ordered):
            return None
        orders = np.array([self.orders.get(s, 0) for s in samples], dtype=np.int64)
        ranks = np.array([self._measured_rank[p] for p in key])
        size = len(key)
        distance = np.abs(orders[None, :] - orders[ordered][:, None])
        position = np.broadcast_to(np.arange(size), distance.shape)
        rank = np.broadcast_to(ranks, distance.shape)
        distance = np.where(
            position == ordered[:, None], np.iinfo(np.int64).max, distance
        )
        order = np.lexsort((position, rank, distance), axis=1)
        return ordered, order[:, : min(6, size - 1)]

    def has_rt_population_gap(
        self,
        group: tuple[str, ...],
        offsets: Mapping[str, float] | None = None,
        unadjusted: frozenset[str] = frozenset(),
    ) -> bool:
        """Reject two concentrated populations in a complete common-RT group.

        This is not a pair veto: adding intervening observations can make a
        group feasible. Raw coordinates never fill gaps in calibration support.
        Populations that split the batch into earlier and later injections stay
        a cost: residual drift explains them as well as two compounds do.
        Modeled drift is removed before the populations are compared; members
        whose drift is unknown are not placed in that frame.
        """
        group = tuple(key for key in group if key not in unadjusted)
        if len(group) < _RT_POPULATION_MIN_MEMBERS or any(
            key not in self.common_rts for key in group
        ):
            return False
        common = (
            {key: self.common_rts[key] - offsets.get(key, 0.0) for key in group}
            if offsets
            else self.common_rts
        )
        ordered = sorted(group, key=lambda key: common[key])
        rts = [common[key] for key in ordered]
        split = max(range(1, len(rts)), key=lambda i: rts[i] - rts[i - 1])
        gap_sec = (rts[split] - rts[split - 1]) * 60
        side_range_sec = max(rts[split - 1] - rts[0], rts[-1] - rts[split]) * 60
        threshold_sec = max(
            _RT_POPULATION_MIN_GAP_SEC, _RT_POPULATION_RANGE_RATIO * side_range_sec
        )
        if gap_sec < threshold_sec and not math.isclose(
            gap_sec, threshold_sec, rel_tol=0, abs_tol=1e-10
        ):
            return False
        samples = [self.owners[key].sample_stem for key in ordered]
        if any(sample not in self.orders for sample in samples):
            return True
        orders = [self.orders[sample] for sample in samples]
        earlier, later = orders[:split], orders[split:]
        return not (max(earlier) < min(later) or max(later) < min(earlier))

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
                key = tuple(sorted(group))
                self.group_peer_fits[group] = self._mean_pair(
                    key, self.coefficients(key), key
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
            if candidate != partition and math.isfinite(self.cost(candidate)):
                variants.add(candidate)
        ordered = sorted(variants, key=lambda p: (self.cost(p), self.partition_key(p)))
        if not ordered:
            return ()
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
            key = tuple(sorted(group))
            pair = self._model_pairs(key, self.coefficients(key), key)
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
                            for reason in pair(owner_id, peer)[1]
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
