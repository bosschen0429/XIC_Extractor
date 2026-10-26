from __future__ import annotations

import math
import statistics
from bisect import bisect_left, bisect_right, insort
from collections import Counter, defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import combinations
from typing import Literal, cast

import numpy as np
from scipy.optimize import linear_sum_assignment

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.edge_scoring import (
    OwnerEdgeEvidence,
    RTEvidenceBasis,
    evaluate_owner_edge,
)
from xic_extractor.alignment.grouping_evidence import (
    envelope_fraction,
    owner_uncertainty_reason,
    peak_correspondence_conflict,
)
from xic_extractor.alignment.grouping_rules import GroupingRules, MethodFeatureEnvelope
from xic_extractor.alignment.membership_selection import (
    AcceptedMembershipRisk,
    MembershipComparison,
    Partition,
    canonical_partition,
    measured_owner_key,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.mass_accuracy import (
    absolute_ppm_error,
    mz_tolerance_da,
    mz_window,
    within_ppm,
)

FeatureMembershipDisposition = Literal[
    "assigned",
    "undecided",
    "known_identity",
    "unresolved_conflict",
]
FeatureMembershipBasis = Literal[
    "window_level_rt_band",
    "peak_trajectory",
    "bidirectional_membership_agreement",
    "directional_membership_disagreement",
    "insufficient_cross_sample_evidence",
    "known_identity_constraint",
    "known_identity_input_conflict",
]
KnownIdentityConstraintStatus = Literal["satisfied", "unresolved_conflict"]
DecisionDirection = Literal["forward", "reverse", "window"]
SparseDecisionState = Literal[
    "assigned",
    "birth",
    "competition_unassigned",
    "evaluated_undecided",
    "proven_incompatible",
    "supported_not_selected",
]

_ALGORITHM = "finite_bidirectional_sparse_feature_assignment"
_OBJECTIVE = (
    "per_direction_per_sample_minimum_supported_assignment_cost_with_private_"
    "unassigned_options;final_membership_requires_directional_agreement"
)
_TERMINATION = "completed_two_finite_directional_passes"
_UNASSIGNED_COST = 1.0
_INFEASIBLE_COST = 1_000_000.0
_AMBIGUITY_EPSILON = 1e-9
_WINDOW_AMBIGUITY_MARGIN = 0.05
_FEATURE_TREND_NEIGHBORS = 6
_WINDOW_TREND_TOLERANCE_SEC = 90.0


@dataclass(frozen=True, slots=True)
class FeatureMembershipGroup:
    owner_ids: tuple[str, ...]
    disposition: FeatureMembershipDisposition
    basis: FeatureMembershipBasis
    reason: str
    known_identity_label: str | None = None


@dataclass(frozen=True, slots=True)
class KnownIdentityMembershipConstraint:
    label: str
    owner_ids: tuple[str, ...]
    status: KnownIdentityConstraintStatus
    reason: str


@dataclass(frozen=True, slots=True)
class SparseFeatureDecision:
    direction: DecisionDirection
    sample_stem: str
    owner_id: str
    feature_key: str | None
    state: SparseDecisionState
    reason: str
    cost: float | None = None
    witness_owner_ids: tuple[str, ...] = ()
    # Which witness carried the decision and on which RT coordinate. The
    # witness list alone cannot answer either, so an audit cannot tell whether
    # a membership stood on raw or drift-corrected RT without these.
    selected_witness_owner_id: str | None = None
    rt_basis: RTEvidenceBasis | None = None
    rt_raw_delta_sec: float | None = None
    rt_drift_corrected_delta_sec: float | None = None
    accepted_risk: AcceptedMembershipRisk | None = None


@dataclass(frozen=True, slots=True)
class FeatureInferenceOperationCounts:
    input_owner_rows: int
    unique_owners: int
    duplicate_owner_rows_ignored: int
    known_identity_bindings: int
    directional_passes: int
    samples_processed: int
    index_queries: int
    index_entries_scanned: int
    candidate_features_considered: int
    feature_summary_comparisons: int
    owner_edge_evaluations: int
    supported_candidate_arcs: int
    competition_components: int
    assignment_solver_calls: int
    alternative_solver_calls: int
    births: int
    ambiguous_assignments: int
    consensus_disagreements: int
    rt_candidate_features_pruned: int = 0
    index_axis_updates: int = 0
    index_axis_updates_avoided: int = 0
    trivial_assignment_components: int = 0
    largest_component_owners: int = 0
    largest_component_features: int = 0
    largest_component_arcs: int = 0


@dataclass(frozen=True, slots=True)
class FeatureMembershipInferenceResult:
    groups: tuple[FeatureMembershipGroup, ...]
    known_identity_constraints: tuple[KnownIdentityMembershipConstraint, ...]
    sparse_decisions: tuple[SparseFeatureDecision, ...]
    operation_counts: FeatureInferenceOperationCounts
    algorithm: str = _ALGORITHM
    objective: str = _OBJECTIVE
    termination: str = _TERMINATION
    pair_coverage: str = "not_materialized;absence_means_not_evaluated"


@dataclass(frozen=True, slots=True)
class BatchRtWarp:
    """Read-only historical offset map for retained grouping snapshots."""

    knots_by_sample: tuple[tuple[str, tuple[tuple[float, float], ...]], ...]
    anchor_group_count: int

    def has_support(self, sample_stem: str) -> bool:
        """Distinguish an unsupported identity transform from measured zero."""
        return bool(self._knots(sample_stem))

    def offset_min(self, sample_stem: str, rt_min: float) -> float:
        return _interpolated_offset(self._knots(sample_stem), rt_min)

    def corrected_rt_min(self, owner: SampleLocalMS1Owner) -> float:
        return self.common_rt_min(owner.sample_stem, owner.owner_apex_rt)

    def common_rt_min(self, sample_stem: str, observed_rt: float) -> float:
        # Knots are keyed by common RT; re-key them by observed RT to invert.
        knots = self._knots(sample_stem)
        return observed_rt - _interpolated_offset(
            tuple((rt + offset, offset) for rt, offset in knots), observed_rt
        )

    def _knots(self, sample_stem: str) -> tuple[tuple[float, float], ...]:
        return dict(self.knots_by_sample).get(sample_stem, ())


def _interpolated_offset(
    knots: tuple[tuple[float, float], ...], rt_min: float
) -> float:
    if not knots:
        return 0.0
    if rt_min <= knots[0][0]:
        return knots[0][1]
    if rt_min >= knots[-1][0]:
        return knots[-1][1]
    right = bisect_right(knots, rt_min, key=lambda knot: knot[0])
    left_rt, left_offset = knots[right - 1]
    right_rt, right_offset = knots[right]
    fraction = (rt_min - left_rt) / (right_rt - left_rt)
    return left_offset + fraction * (right_offset - left_offset)


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


@dataclass(slots=True)
class _FeatureTrack:
    key: str
    seed: SampleLocalMS1Owner
    last: SampleLocalMS1Owner
    sample_stems: set[str]
    precursor_min: float
    precursor_max: float
    method: MethodFeatureEnvelope
    members: list[SampleLocalMS1Owner]

    @classmethod
    def from_owner(
        cls,
        owner: SampleLocalMS1Owner,
        *,
        rules: GroupingRules,
        direction: DecisionDirection,
    ) -> _FeatureTrack:
        return cls(
            key=f"{direction}:{owner.owner_id}",
            seed=owner,
            last=owner,
            sample_stems={owner.sample_stem},
            precursor_min=owner.precursor_mz,
            precursor_max=owner.precursor_mz,
            method=rules.feature_envelope(owner),
            members=[owner],
        )

    def add(self, owner: SampleLocalMS1Owner) -> None:
        self.method.add(owner)
        self.members.append(owner)
        self.sample_stems.add(owner.sample_stem)
        self.precursor_min = min(self.precursor_min, owner.precursor_mz)
        self.precursor_max = max(self.precursor_max, owner.precursor_mz)
        self.last = owner

    @property
    def witness_owners(self) -> tuple[SampleLocalMS1Owner, ...]:
        if self.seed.owner_id == self.last.owner_id:
            return (self.last,)
        return (self.last, self.seed)


class _FeatureIndex:
    """Range index over feature extrema; exact envelopes are checked afterward."""

    def __init__(self) -> None:
        self._axes: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)

    def add(self, track: _FeatureTrack) -> None:
        for key, value in self._entries(track):
            insort(self._axes[key], (value, track.key))

    def extend(
        self, track: _FeatureTrack, owner: SampleLocalMS1Owner, counts: Counter[str]
    ) -> None:
        previous = dict(self._entries(track))
        track.add(owner)
        current = dict(self._entries(track))
        for key in previous.keys() | current.keys():
            if previous.get(key) == current.get(key):
                counts["index_axis_updates_avoided"] += 1
                continue
            counts["index_axis_updates"] += 1
            items = self._axes[key]
            if key in previous:
                item = (previous[key], track.key)
                index = bisect_left(items, item)
                if index < len(items) and items[index] == item:
                    items.pop(index)
            if key in current:
                insort(items, (current[key], track.key))

    def candidates(
        self,
        owner: SampleLocalMS1Owner,
        *,
        rules: GroupingRules,
        config: AlignmentConfig,
        counts: Counter[str],
    ) -> set[str]:
        candidates: set[str] = set()
        for queries in rules.index_query_alternatives(owner, config):
            matches: set[str] | None = None
            for key, value, tolerance in queries:
                axis_matches = self._matches(key, value, tolerance, counts)
                matches = (
                    axis_matches
                    if matches is None
                    else matches.intersection(axis_matches)
                )
                if not matches:
                    break
            candidates.update(matches or ())
        return candidates

    def _matches(
        self,
        key: tuple[str, str],
        value: float,
        tolerance: float,
        counts: Counter[str],
    ) -> set[str]:
        items = self._axes.get(key, ())
        lower, upper = mz_window(value, tolerance)
        first = bisect_left(items, (lower, ""))
        last = bisect_right(items, (upper, chr(0x10FFFF)))
        counts["index_entries_scanned"] += last - first
        return {feature_key for _, feature_key in items[first:last]}

    @staticmethod
    def _entries(track: _FeatureTrack) -> tuple[tuple[tuple[str, str], float], ...]:
        return track.method.index_entries(track.precursor_min)


@dataclass(frozen=True, slots=True)
class _Candidate:
    feature_key: str
    cost: float | None
    state: Literal["supported", "evaluated_undecided", "proven_incompatible"]
    reason: str
    witness_owner_ids: tuple[str, ...]
    selected_witness_owner_id: str | None = None
    rt_basis: RTEvidenceBasis | None = None
    rt_raw_delta_sec: float | None = None
    rt_drift_corrected_delta_sec: float | None = None


@dataclass(frozen=True, slots=True)
class _DirectionalResult:
    groups: tuple[tuple[str, ...], ...]
    decisions: tuple[SparseFeatureDecision, ...]
    owner_uncertainty: frozenset[str]


def infer_feature_memberships(
    owners: Sequence[SampleLocalMS1Owner],
    *,
    rules: GroupingRules,
    config: AlignmentConfig,
    rt_frame: RtFrame,
    known_identity_bindings: Sequence[tuple[str, str]] = (),
    bootstrap: bool = False,
    accept_competition: bool = True,
    peak_trajectories: PeakTrajectories | None = None,
) -> FeatureMembershipInferenceResult:
    """Infer ordinary whole-window bands, preserving exact known identities.

    Directional inference supplies bootstrap anchors for the batch warp and
    handles composition groups. Its groups do not constrain ordinary bands.
    """
    unique_owners, duplicate_rows = _unique_owners(owners)
    owner_by_id = {owner.owner_id: owner for owner in unique_owners}
    counts: Counter[str] = Counter(
        input_owner_rows=len(owners),
        unique_owners=len(unique_owners),
        duplicate_owner_rows_ignored=duplicate_rows,
    )
    (
        known_groups,
        constraints,
        constrained_owner_ids,
    ) = _known_identity_groups(
        owner_by_id,
        rt_frame,
        counts,
        known_identity_bindings,
    )
    ordinary = tuple(
        owner for owner in unique_owners if owner.owner_id not in constrained_owner_ids
    )
    trajectories = _membership_trajectories(
        ordinary, peak_trajectories or PeakTrajectories(), config, rules=rules
    )
    use_directional = bootstrap or rules.requires_directional_membership(ordinary)
    if accept_competition and not bootstrap:
        trajectories = trajectories.common_frame_membership(
            (
                o.owner_id
                for o in ordinary
                if rt_frame.membership_coordinate(o) is not None
            ),
            preserve_independent=True,
        )
        directions = (
            tuple(
                _directional_inference(
                    ordinary,
                    rules=rules,
                    direction=cast(DecisionDirection, direction),
                    config=config,
                    rt_frame=rt_frame,
                    counts=counts,
                    seed_by_owner={},
                    trajectories=trajectories,
                )
                for direction in ("forward", "reverse")
            )
            if use_directional
            else ()
        )
        inferred_groups, decisions = _accepted_final_memberships(
            ordinary,
            rules=rules,
            config=config,
            rt_frame=rt_frame,
            trajectories=trajectories,
            counts=counts,
            directional_partitions=tuple(result.groups for result in directions),
        )
        algorithm = "bounded_complete_group_selection"
        objective = "minimum_complete_group_cost_then_mean_peer_fit_over_bounded_moves"
        termination = "no_improving_bounded_move"
    elif use_directional:
        trajectory_seeds = _trajectory_seeds(
            ordinary,
            trajectories,
            config,
            rules=rules,
            rt_frame=rt_frame,
        )
        seed_by_owner = {
            owner_id: index
            for index, seed in enumerate(trajectory_seeds)
            if seed.disposition == "assigned"
            for owner_id in seed.owner_ids
        }
        forward = _directional_inference(
            ordinary,
            direction="forward",
            config=config,
            rt_frame=rt_frame,
            counts=counts,
            seed_by_owner=seed_by_owner,
            trajectories=trajectories,
            rules=rules,
        )
        reverse = _directional_inference(
            ordinary,
            direction="reverse",
            config=config,
            rt_frame=rt_frame,
            counts=counts,
            seed_by_owner=seed_by_owner,
            trajectories=trajectories,
            rules=rules,
        )
        inferred_groups = _consensus_groups(
            ordinary, forward, reverse, counts, seeded_owner_ids=seed_by_owner.keys()
        )
        decisions = (*forward.decisions, *reverse.decisions)
        algorithm = _ALGORITHM
        objective = _OBJECTIVE
        termination = _TERMINATION
    else:
        inferred_groups, decisions = _window_inference(
            ordinary,
            config=config,
            rt_frame=rt_frame,
            counts=counts,
            trajectories=trajectories,
            rules=rules,
        )
        algorithm = "whole_window_peak_trajectories"
        objective = "ordered_pattern_and_local_rt_mass_evidence_in_one_membership_graph"
        termination = "completed_one_finite_window_pass"
    groups = tuple(sorted((*known_groups, *inferred_groups), key=_group_sort_key))
    operation_counts = FeatureInferenceOperationCounts(
        **{
            field_name: counts[field_name]
            for field_name in FeatureInferenceOperationCounts.__dataclass_fields__
        }
    )
    return FeatureMembershipInferenceResult(
        groups=groups,
        known_identity_constraints=constraints,
        sparse_decisions=tuple(decisions),
        operation_counts=operation_counts,
        algorithm=algorithm,
        objective=objective,
        termination=termination,
        pair_coverage=(
            "not_materialized;absence_means_not_evaluated"
            if use_directional
            else "bounded_window_edges;absence_means_not_evaluated"
        ),
    )


def _accepted_final_memberships(
    owners: tuple[SampleLocalMS1Owner, ...],
    *,
    rules: GroupingRules,
    config: AlignmentConfig,
    rt_frame: RtFrame,
    trajectories: PeakTrajectories,
    counts: Counter[str],
    directional_partitions: tuple[Partition, ...] = (),
) -> tuple[tuple[FeatureMembershipGroup, ...], tuple[SparseFeatureDecision, ...]]:
    """Select whole bands, then reassess samples with the assignment solver."""
    windows: list[list[SampleLocalMS1Owner]] = []
    for owner in sorted(owners, key=lambda o: (o.precursor_mz, measured_owner_key(o))):
        if not windows or not within_ppm(
            owner.precursor_mz, windows[-1][-1].precursor_mz, config.max_ppm
        ):
            windows.append([])
        windows[-1].append(owner)
    groups, decisions = [], []
    for window in windows:
        comparison = MembershipComparison(
            window,
            rules=rules,
            config=config,
            rt_frame=rt_frame,
            trajectories=trajectories,
        )
        partition = comparison.initial_partition()
        direction_candidates = [
            canonical_partition(
                [
                    tuple(key for key in group if key in comparison.owners)
                    for group in candidate
                ]
            )
            for candidate in directional_partitions
        ]
        partition = min(
            [partition, *direction_candidates],
            key=lambda p: (comparison.cost(p), comparison.partition_key(p)),
        )
        by_sample: dict[str, list[SampleLocalMS1Owner]] = defaultdict(list)
        for owner in window:
            by_sample[owner.sample_stem].append(owner)
        while True:
            moves = []
            for sample_owners in by_sample.values():
                ids = {o.owner_id for o in sample_owners}
                base = canonical_partition(
                    [tuple(p for p in g if p not in ids) for g in partition]
                )
                # Names order solver columns by measured features, not group IDs.
                bands = {
                    f"band:{i:08d}": group
                    for i, group in enumerate(
                        sorted(base, key=lambda g: comparison.partition_key((g,)))
                    )
                }
                candidates = {
                    owner.owner_id: tuple(
                        _Candidate(
                            key, delta, "supported", "complete_group_cost", group
                        )
                        for key, group in bands.items()
                        if math.isfinite(
                            delta := comparison.group_cost(
                                tuple(sorted((*group, owner.owner_id)))
                            )
                            - comparison.group_cost(group)
                        )
                        and delta <= _UNASSIGNED_COST
                    )
                    for owner in sample_owners
                }
                assignments, _ = _solve_sample_assignment(
                    sample_owners,
                    candidates,
                    counts,
                    owner_order={
                        o.owner_id: measured_owner_key(o) for o in sample_owners
                    },
                )
                proposed = {key: list(group) for key, group in bands.items()}
                singletons = []
                for owner in sample_owners:
                    if key := assignments.get(owner.owner_id):
                        proposed[key].append(owner.owner_id)
                    else:
                        singletons.append((owner.owner_id,))
                candidate = canonical_partition([*proposed.values(), *singletons])
                if (comparison.cost(candidate), comparison.partition_key(candidate)) < (
                    comparison.cost(partition) - 1e-12,
                    comparison.partition_key(partition),
                ):
                    moves.append(candidate)
            if not moves:
                refined, alternatives_by_owner = comparison.refine_choices(partition)
                if refined == partition:
                    break
                partition = refined
                continue
            partition = comparison.merge(
                min(
                    moves,
                    key=lambda p: (comparison.cost(p), comparison.partition_key(p)),
                )
            )
        risks = comparison.risks(partition, alternatives_by_owner=alternatives_by_owner)
        if len(direction_candidates) == 2:
            left = {
                owner_id: group
                for group in direction_candidates[0]
                for owner_id in group
            }
            right = {
                owner_id: group
                for group in direction_candidates[1]
                for owner_id in group
            }
            risks = {
                owner_id: replace(
                    risk,
                    risk_flags=tuple(
                        sorted({*risk.risk_flags, "direction_disagreement"})
                    ),
                )
                if left[owner_id] != right[owner_id]
                else risk
                for owner_id, risk in risks.items()
            }
        for group in partition:
            assigned = len(group) > 1
            groups.append(
                FeatureMembershipGroup(
                    group,
                    "assigned" if assigned else "undecided",
                    "window_level_rt_band",
                    "minimum_feasible_complete_group_cost"
                    if assigned
                    else "separation_has_lower_configuration_cost",
                )
            )
            for owner_id in group:
                owner = comparison.owners[owner_id]
                decisions.append(
                    SparseFeatureDecision(
                        "window",
                        owner.sample_stem,
                        owner_id,
                        f"window:{group[0]}",
                        "assigned" if assigned else "birth",
                        "complete_group_configuration_selected",
                        cost=comparison.group_cost(group),
                        witness_owner_ids=tuple(p for p in group if p != owner_id),
                        rt_basis="drift_corrected"
                        if comparison.rt_basis == "drift_corrected"
                        else "raw",
                        accepted_risk=risks[owner_id],
                    )
                )
        counts["owner_edge_evaluations"] += len(comparison.pairs)
        counts["feature_summary_comparisons"] += len(comparison.group_costs)
    counts["samples_processed"] += len({o.sample_stem for o in owners})
    counts["births"] += len(groups)
    return tuple(groups), tuple(decisions)


def _membership_trajectories(
    owners: Sequence[SampleLocalMS1Owner],
    geometry: PeakTrajectories,
    config: AlignmentConfig,
    *,
    rules: GroupingRules,
) -> PeakTrajectories:
    """Withdraw only pattern membership claims contradicted by direct peaks.

    Two disjoint, co-observed method-approved owners can distinguish envelopes.
    If a track spans members compatible exclusively with opposite sides, that
    track cannot authorize a common Feature. Missing or ambiguous evidence is
    not a contradiction; RT distance and shape similarity do not decide it.

    This projection is private to inference: seeds, same-track cost and derived
    distinctness all consume it. The caller retains the original geometry and
    localization links for the finalized groups.
    """
    if not geometry.tracks:
        return geometry
    supported = {
        owner.owner_id: owner
        for owner in owners
        if rules.trajectory_witness_eligible(owner)
        and not owner_uncertainty_reason(owner, config, rules=rules)
    }
    by_sample_chemistry: dict[
        tuple[str, tuple[str, str]], list[SampleLocalMS1Owner]
    ] = defaultdict(list)
    for owner in supported.values():
        key = owner.sample_stem, rules.chemistry_key(owner)
        by_sample_chemistry[key].append(owner)
    witnesses = []
    for sample_owners in by_sample_chemistry.values():
        for first, second in combinations(sample_owners, 2):
            if not within_ppm(
                max(first.precursor_mz, second.precursor_mz),
                min(first.precursor_mz, second.precursor_mz),
                config.max_ppm,
            ):
                continue
            first_bounds = _selected_peak_bounds(first)
            second_bounds = _selected_peak_bounds(second)
            if first_bounds[1] < second_bounds[0] or second_bounds[1] < first_bounds[0]:
                witnesses.append((first.owner_id, second.owner_id))
    if not witnesses:
        return geometry

    # Prepare shared method envelopes once. Shape has its own membership gate;
    # it cannot disambiguate the two direct peaks for this witness rule.
    anchors = {
        owner_id: _FeatureTrack.from_owner(
            replace(supported[owner_id], peak_shape=None),
            direction="window",
            rules=rules,
        )
        for owner_id in {owner_id for pair in witnesses for owner_id in pair}
    }
    compatibility: dict[tuple[str, str], bool] = {}

    def compatible(member_id: str, anchor_id: str) -> bool:
        key = member_id, anchor_id
        if key not in compatibility:
            compatibility[key] = (
                member_id == anchor_id
                or not _feature_hard_reason(
                    supported[member_id], anchors[anchor_id], config
                )[0]
            )
        return compatibility[key]

    retained = []
    for track in geometry.tracks:
        members = tuple(
            owner_id for owner_id in track.owner_ids if owner_id in supported
        )
        contradicted = False
        if len(members) >= 2:
            for first_id, second_id in witnesses:
                sides = {
                    (compatible(member_id, first_id), compatible(member_id, second_id))
                    for member_id in members
                }
                if (True, False) in sides and (False, True) in sides:
                    contradicted = True
                    break
        if not contradicted:
            retained.append(track)
    if len(retained) == len(geometry.tracks):
        return geometry
    return geometry.membership_subset(retained)


def _selected_peak_bounds(owner: SampleLocalMS1Owner) -> tuple[float, float]:
    integration = owner.selected_integration
    if integration is not None:
        return integration.rt_left_min, integration.rt_right_min
    return owner.owner_peak_start_rt, owner.owner_peak_end_rt


def _trajectory_seeds(
    owners: Sequence[SampleLocalMS1Owner],
    correspondence: PeakTrajectories | None,
    config: AlignmentConfig,
    *,
    rt_frame: RtFrame,
    rules: GroupingRules,
) -> tuple[FeatureMembershipGroup, ...]:
    if correspondence is None:
        return ()
    by_id = {owner.owner_id: owner for owner in owners}
    groups = []
    for context in correspondence.tracks:
        members = [
            by_id[owner_id] for owner_id in context.owner_ids if owner_id in by_id
        ]
        if not members:
            continue
        if (
            len({rules.chemistry_key(member) for member in members}) != 1
            or len({member.sample_stem for member in members}) != len(members)
            or not within_ppm(
                max(member.precursor_mz for member in members),
                min(member.precursor_mz for member in members),
                config.max_ppm,
            )
            or any(
                peak_correspondence_conflict(
                    (member,),
                    members[:index],
                    config,
                    trajectories=correspondence,
                    rt_frame=rt_frame,
                )
                is not None
                for index, member in enumerate(members)
            )
        ):
            continue
        owner_ids = tuple(member.owner_id for member in members)
        assigned = len(members) >= 2 and not any(
            owner_uncertainty_reason(member, config, rules=rules) for member in members
        )
        reason = (
            "ordered_ms1_pattern_links_existing_owners"
            if assigned
            else "pattern_location_does_not_supply_cross_sample_method_support"
        )
        groups.append(
            FeatureMembershipGroup(
                owner_ids=owner_ids,
                disposition="assigned" if assigned else "undecided",
                basis="peak_trajectory",
                reason=reason,
            )
        )
    return tuple(groups)


def _unique_owners(
    owners: Sequence[SampleLocalMS1Owner],
) -> tuple[tuple[SampleLocalMS1Owner, ...], int]:
    by_id: dict[str, SampleLocalMS1Owner] = {}
    duplicate_rows = 0
    for owner in owners:
        previous = by_id.get(owner.owner_id)
        if previous is None:
            by_id[owner.owner_id] = owner
        elif previous == owner:
            duplicate_rows += 1
        else:
            raise ValueError(
                f"conflicting owner rows share owner_id {owner.owner_id!r}"
            )
    return tuple(by_id[key] for key in sorted(by_id)), duplicate_rows


def _known_identity_groups(
    owner_by_id: Mapping[str, SampleLocalMS1Owner],
    rt_frame: RtFrame,
    counts: Counter[str],
    known_identity_bindings: Sequence[tuple[str, str]],
) -> tuple[
    tuple[FeatureMembershipGroup, ...],
    tuple[KnownIdentityMembershipConstraint, ...],
    frozenset[str],
]:
    retained_bindings = rt_frame.known_identity_bindings
    bindings = tuple(dict.fromkeys((*known_identity_bindings, *retained_bindings)))
    counts["known_identity_bindings"] = len(bindings)
    by_label: dict[str, set[str]] = defaultdict(set)
    labels_by_owner: dict[str, set[str]] = defaultdict(set)
    for owner_id, label in bindings:
        if owner_id not in owner_by_id:
            continue
        by_label[label].add(owner_id)
        labels_by_owner[owner_id].add(label)

    conflicting_owners = {
        owner_id for owner_id, labels in labels_by_owner.items() if len(labels) > 1
    }
    constraints: list[KnownIdentityMembershipConstraint] = []
    groups: list[FeatureMembershipGroup] = []
    constrained_owner_ids: set[str] = set()
    conflict_labels = {
        label for owner_id in conflicting_owners for label in labels_by_owner[owner_id]
    }
    conflict_owner_ids = {
        owner_id for label in conflict_labels for owner_id in by_label[label]
    }
    if conflict_owner_ids:
        constrained_owner_ids.update(conflict_owner_ids)
        groups.append(
            FeatureMembershipGroup(
                owner_ids=tuple(sorted(conflict_owner_ids)),
                disposition="unresolved_conflict",
                basis="known_identity_input_conflict",
                reason="owner_is_bound_to_multiple_known_identity_labels",
            )
        )

    for label in sorted(by_label):
        owner_ids = tuple(sorted(by_label[label]))
        owners_for_label = tuple(owner_by_id[owner_id] for owner_id in owner_ids)
        repeated_samples = len(
            {owner.sample_stem for owner in owners_for_label}
        ) != len(owners_for_label)
        cross_label_conflict = label in conflict_labels
        conflict = repeated_samples or cross_label_conflict
        reason = (
            "known_identity_has_multiple_bound_owners_in_one_sample"
            if repeated_samples
            else "owner_is_bound_to_multiple_known_identity_labels"
            if cross_label_conflict
            else "exact_known_identity_bindings_are_indivisible"
        )
        constraints.append(
            KnownIdentityMembershipConstraint(
                label=label,
                owner_ids=owner_ids,
                status="unresolved_conflict" if conflict else "satisfied",
                reason=reason,
            )
        )
        constrained_owner_ids.update(owner_ids)
        if cross_label_conflict:
            continue
        groups.append(
            FeatureMembershipGroup(
                owner_ids=owner_ids,
                disposition="unresolved_conflict"
                if repeated_samples
                else "known_identity",
                basis=(
                    "known_identity_input_conflict"
                    if repeated_samples
                    else "known_identity_constraint"
                ),
                reason=reason,
                known_identity_label=label,
            )
        )
    return tuple(groups), tuple(constraints), frozenset(constrained_owner_ids)


def _directional_inference(
    owners: tuple[SampleLocalMS1Owner, ...],
    *,
    rules: GroupingRules,
    direction: DecisionDirection,
    config: AlignmentConfig,
    rt_frame: RtFrame,
    counts: Counter[str],
    seed_by_owner: Mapping[str, int],
    trajectories: PeakTrajectories,
) -> _DirectionalResult:
    """Grow features in one injection direction from ordered-pattern seeds.

    An assigned pattern seed is one union: its first member processed in this
    direction competes like any owner, and later members join that member's
    feature. Unbound owners can extend the feature; owners bound to distinct
    trajectories cannot share one.
    """
    injection_order = rt_frame.injection_order
    counts["directional_passes"] += 1
    by_sample: dict[str, list[SampleLocalMS1Owner]] = defaultdict(list)
    for owner in owners:
        by_sample[owner.sample_stem].append(owner)
    sample_stems = sorted(
        by_sample,
        key=lambda sample: _sample_sort_key(sample, injection_order),
        reverse=direction == "reverse",
    )
    tracks: dict[str, _FeatureTrack] = {}
    index = _FeatureIndex()
    feature_members: dict[str, list[str]] = defaultdict(list)
    seed_feature: dict[int, str] = {}
    owner_feature: dict[str, str] = {}
    decisions: list[SparseFeatureDecision] = []
    uncertain: set[str] = set()

    def place(owner_id: str, feature_key: str) -> None:
        owner_feature[owner_id] = feature_key
        feature_members[feature_key].append(owner_id)
        seed = seed_by_owner.get(owner_id)
        if seed is not None:
            seed_feature.setdefault(seed, feature_key)

    for sample_stem in sample_stems:
        counts["samples_processed"] += 1
        sample_owners = tuple(
            sorted(by_sample[sample_stem], key=lambda item: item.owner_id)
        )
        # Seed members go first, so the sample's other owners see that occupancy.
        seeded_rows = tuple(
            owner
            for owner in sample_owners
            if seed_by_owner.get(owner.owner_id) in seed_feature
        )
        for owner in seeded_rows:
            feature_key = seed_feature[seed_by_owner[owner.owner_id]]
            track = tracks[feature_key]
            if peak_correspondence_conflict(
                (owner,),
                track.members,
                config,
                rt_frame=rt_frame,
            ):
                # Pattern agreement cannot override a contradictory measured
                # region or shape introduced by an intervening member.
                continue
            decisions.append(
                SparseFeatureDecision(
                    direction=direction,
                    sample_stem=sample_stem,
                    owner_id=owner.owner_id,
                    feature_key=feature_key,
                    state="assigned",
                    reason="ordered_ms1_pattern_links_existing_owners",
                    witness_owner_ids=tuple(
                        witness.owner_id for witness in track.witness_owners
                    ),
                )
            )
            index.extend(track, owner, counts)
            place(owner.owner_id, feature_key)
        supported_rows: list[SampleLocalMS1Owner] = []
        candidates_by_owner: dict[str, tuple[_Candidate, ...]] = {}
        for owner in sample_owners:
            if owner.owner_id in owner_feature:
                continue
            weak_reason = owner_uncertainty_reason(owner, config, rules=rules)
            if weak_reason:
                feature_key = f"{direction}:{owner.owner_id}"
                place(owner.owner_id, feature_key)
                uncertain.add(owner.owner_id)
                counts["births"] += 1
                decisions.append(
                    SparseFeatureDecision(
                        direction=direction,
                        sample_stem=sample_stem,
                        owner_id=owner.owner_id,
                        feature_key=feature_key,
                        state="evaluated_undecided",
                        reason=weak_reason,
                    )
                )
                continue
            supported_rows.append(owner)
            counts["index_queries"] += 1
            feature_keys = index.candidates(
                owner, config=config, counts=counts, rules=rules
            )
            feasible_keys = {
                key
                for key in feature_keys
                if _feature_rt_may_support(owner, tracks[key], config, rt_frame)
                and trajectories.permits_membership(
                    (owner.owner_id, *feature_members[key])
                )
            }
            counts["rt_candidate_features_pruned"] += len(feature_keys) - len(
                feasible_keys
            )
            feature_keys = feasible_keys
            counts["candidate_features_considered"] += len(feature_keys)
            row_candidates = tuple(
                _compare_owner_to_feature(
                    owner,
                    tracks[feature_key],
                    config=config,
                    rt_frame=rt_frame,
                    counts=counts,
                    rules=rules,
                )
                for feature_key in sorted(feature_keys)
            )
            candidates_by_owner[owner.owner_id] = row_candidates

        assignments, ambiguous = _solve_sample_assignment(
            supported_rows, candidates_by_owner, counts
        )
        # A pattern seed carries its own cross-sample support, as in the
        # window graph; competition only decides which feature it joins.
        uncertain.update(
            owner_id for owner_id in ambiguous if owner_id not in seed_by_owner
        )
        for owner in supported_rows:
            row_candidates = candidates_by_owner[owner.owner_id]
            assigned_key = assignments.get(owner.owner_id)
            supported_keys = {
                candidate.feature_key
                for candidate in row_candidates
                if candidate.state == "supported"
            }
            for candidate in row_candidates:
                state: SparseDecisionState
                reason = candidate.reason
                if candidate.state == "proven_incompatible":
                    state = "proven_incompatible"
                elif candidate.state == "evaluated_undecided":
                    state = "evaluated_undecided"
                elif (
                    owner.owner_id in ambiguous
                    and candidate.feature_key == assigned_key
                ):
                    state = "evaluated_undecided"
                    reason = "alternative_assignment_has_equal_objective"
                elif candidate.feature_key == assigned_key:
                    state = "assigned"
                else:
                    state = "supported_not_selected"
                decisions.append(
                    SparseFeatureDecision(
                        direction=direction,
                        sample_stem=sample_stem,
                        owner_id=owner.owner_id,
                        feature_key=candidate.feature_key,
                        state=state,
                        reason=reason,
                        cost=candidate.cost,
                        witness_owner_ids=candidate.witness_owner_ids,
                        selected_witness_owner_id=(candidate.selected_witness_owner_id),
                        rt_basis=candidate.rt_basis,
                        rt_raw_delta_sec=candidate.rt_raw_delta_sec,
                        rt_drift_corrected_delta_sec=(
                            candidate.rt_drift_corrected_delta_sec
                        ),
                    )
                )

            if assigned_key is not None and owner.owner_id not in ambiguous:
                track = tracks[assigned_key]
                index.extend(track, owner, counts)
                place(owner.owner_id, assigned_key)
                continue

            feature = _FeatureTrack.from_owner(owner, direction=direction, rules=rules)
            tracks[feature.key] = feature
            index.add(feature)
            place(owner.owner_id, feature.key)
            counts["births"] += 1
            if owner.owner_id in ambiguous:
                reason = "alternative_assignment_has_equal_objective"
                state = "evaluated_undecided"
            elif supported_keys:
                reason = "joint_competition_selected_unassigned"
                state = "competition_unassigned"
                if owner.owner_id not in seed_by_owner:
                    uncertain.add(owner.owner_id)
            else:
                reason = "no_supported_existing_feature"
                state = "birth"
            decisions.append(
                SparseFeatureDecision(
                    direction=direction,
                    sample_stem=sample_stem,
                    owner_id=owner.owner_id,
                    feature_key=feature.key,
                    state=state,
                    reason=reason,
                )
            )

    grouped: dict[str, list[str]] = defaultdict(list)
    for owner_id, feature_key in owner_feature.items():
        grouped[feature_key].append(owner_id)
    groups = tuple(sorted(tuple(sorted(owner_ids)) for owner_ids in grouped.values()))
    return _DirectionalResult(groups, tuple(decisions), frozenset(uncertain))


@dataclass(frozen=True, slots=True)
class _WindowEdge:
    left_id: str
    right_id: str
    ambiguity_cost: float
    union_priority: float


def _join_window_bands(
    members: dict[str, list[SampleLocalMS1Owner]],
    arcs: Sequence[_WindowEdge],
    *,
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

    costs = {
        (left, right): priority(left, right)
        for left in links
        for right in links[left]
        if left < right
    }
    while costs:
        best: dict[str, float] = {}
        for (left, right), (cost, _, _) in costs.items():
            best[left] = min(best.get(left, math.inf), cost)
            best[right] = min(best.get(right, math.inf), cost)
        available = (
            pair
            for pair, (cost, _, _) in costs.items()
            if not samples[pair[0]] & samples[pair[1]]
            and (
                min(len(members[pair[0]]), len(members[pair[1]])) < 2
                or centers[pair[0]][0] is None
                or centers[pair[1]][0] is None
                or cost <= best[pair[0]]
                and cost <= best[pair[1]]
            )
        )
        pair = min(available, key=costs.__getitem__, default=None)
        if pair is None:
            return
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
            left_members, right_members, config, rt_frame=rt_frame
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
            costs[(first, second)] = priority(first, second)


def _window_inference(
    owners: tuple[SampleLocalMS1Owner, ...],
    *,
    rules: GroupingRules,
    config: AlignmentConfig,
    rt_frame: RtFrame,
    counts: Counter[str],
    trajectories: PeakTrajectories,
) -> tuple[tuple[FeatureMembershipGroup, ...], tuple[SparseFeatureDecision, ...]]:
    """Find all RT bands in each mass neighbourhood in one finite pass.

    Raw ordered patterns can seed owners without measured RT coordinates.
    Calibrated bands use measured RT and mass, not forced pattern unions
    or zero-distance shortcuts. Every union keeps one physical owner per sample
    and distinct tracks apart.
    A feature's six nearest injections provide its RT trend once it has enough
    members; sparse bands rely on the batch warp with a wider RT allowance.
    After local bands settle, separated dense bands can bridge on whole-band
    evidence without treating missing injections as contrary identity evidence.
    """
    injection_order = rt_frame.injection_order
    if not owners:
        return (), ()
    samples = {owner.sample_stem for owner in owners}
    known_orders = {
        sample: order for sample, order in injection_order.items() if sample in samples
    }
    next_order = max(known_orders.values(), default=0) + 1
    orders = dict(known_orders)
    for sample in sorted(samples - known_orders.keys()):
        orders[sample] = next_order
        next_order += 1
    corrected_rt = {
        owner.owner_id: rt_frame.corrected_rt_min(owner) for owner in owners
    }
    common_rt = {
        owner.owner_id: rt
        for owner in owners
        if (rt := rt_frame.calibrated_rt_min(owner.sample_stem, owner.owner_apex_rt))
        is not None
    }
    trajectories = trajectories.common_frame_membership(common_rt)
    trajectory_seeds = _trajectory_seeds(
        tuple(owner for owner in owners if owner.owner_id not in common_rt),
        trajectories,
        config,
        rules=rules,
        rt_frame=rt_frame,
    )
    ordinary_by_id = {owner.owner_id: owner for owner in owners}
    track_indices = trajectories.owner_track_indices()
    weak = {
        owner.owner_id: reason
        for owner in owners
        if (reason := owner_uncertainty_reason(owner, config, rules=rules))
    }
    shape_conflicts: dict[str, set[str]] = defaultdict(set)
    eligible = tuple(owner for owner in owners if owner.owner_id not in weak)
    by_chemistry: dict[tuple[str, str], list[SampleLocalMS1Owner]] = defaultdict(list)
    for owner in eligible:
        by_chemistry[rules.chemistry_key(owner)].append(owner)

    final_components: list[tuple[str, ...]] = [(owner_id,) for owner_id in sorted(weak)]
    for chemistry in sorted(by_chemistry):
        sorted_owners = sorted(
            by_chemistry[chemistry],
            key=lambda owner: (owner.precursor_mz, owner.owner_id),
        )
        windows: list[list[SampleLocalMS1Owner]] = []
        for owner in sorted_owners:
            # A fixed window start can cut a coherent band when an unrelated
            # lower-mass owner happens to arrive first. Keep local mass
            # neighbourhoods connected, then enforce tolerance per edge.
            if not windows or owner.precursor_mz - windows[-1][
                -1
            ].precursor_mz > mz_tolerance_da(
                windows[-1][-1].precursor_mz, config.max_ppm
            ):
                windows.append([])
            windows[-1].append(owner)
        for window in windows:
            counts["index_queries"] += len(window)
            counts["candidate_features_considered"] += len(window)
            members = {owner.owner_id: [owner] for owner in window}
            seeded_owners: set[str] = set()
            for seed in trajectory_seeds:
                retained = [
                    owner_id for owner_id in seed.owner_ids if owner_id in members
                ]
                if seed.disposition != "assigned" or len(retained) < 2:
                    continue
                seeded_owners.update(retained)
                first, *others = retained
                for owner_id in others:
                    members[first].extend(members.pop(owner_id))

            arcs = _window_candidate_edges(
                window,
                config=config,
                orders=orders,
                corrected_rt=corrected_rt,
                common_rt=common_rt,
                track_indices=track_indices,
                trajectories=trajectories,
                counts=counts,
                shape_conflicts=shape_conflicts,
                rules=rules,
                rt_frame=rt_frame,
            )
            counts["supported_candidate_arcs"] += len(arcs)
            ambiguous_window, ambiguous_arcs = _ambiguous_window_edges(
                arcs, ordinary_by_id, seeded_owners
            )
            for owner_id in ambiguous_window:
                weak[owner_id] = "alternative_bands_have_near_equal_cost"
            counts["ambiguous_assignments"] += len(ambiguous_window)
            _join_window_bands(
                members,
                [
                    arc
                    for arc in arcs
                    if arc.left_id not in ambiguous_window
                    and arc.right_id not in ambiguous_window
                    and (arc.left_id, arc.right_id) not in ambiguous_arcs
                    and (arc.right_id, arc.left_id) not in ambiguous_arcs
                ],
                config=config,
                rules=rules,
                rt_frame=rt_frame,
                trajectories=trajectories,
                orders=orders,
                corrected_rt=corrected_rt,
                common_rt=common_rt,
                shape_conflicts=shape_conflicts,
                counts=counts,
            )
            _assign_window_singletons(
                members,
                weak=weak,
                orders=orders,
                corrected_rt=corrected_rt,
                config=config,
                counts=counts,
                trajectories=trajectories,
                shape_conflicts=shape_conflicts,
                rules=rules,
                rt_frame=rt_frame,
            )
            if rt_frame.has_coordinates and len(known_orders) == len(samples):
                _bridge_separated_window_bands(
                    members,
                    weak=weak,
                    orders=orders,
                    corrected_rt=corrected_rt,
                    config=config,
                    counts=counts,
                    trajectories=trajectories,
                    rules=rules,
                    rt_frame=rt_frame,
                )
            final_components.extend(
                tuple(sorted(owner.owner_id for owner in group))
                for group in members.values()
            )

    groups, decisions = _window_membership_result(
        final_components,
        owner_by_id=ordinary_by_id,
        weak=weak,
        orders=orders,
        corrected_rt=corrected_rt,
        rt_basis="drift_corrected" if rt_frame.has_coordinates else "raw",
        shape_conflicts=shape_conflicts,
    )
    counts["samples_processed"] = len(samples)
    counts["births"] = len(groups)
    return groups, decisions


def _window_candidate_edges(
    window: Sequence[SampleLocalMS1Owner],
    *,
    rt_frame: RtFrame,
    rules: GroupingRules,
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


def _window_membership_result(
    components: Sequence[tuple[str, ...]],
    *,
    owner_by_id: Mapping[str, SampleLocalMS1Owner],
    weak: Mapping[str, str],
    orders: Mapping[str, int],
    corrected_rt: Mapping[str, float],
    rt_basis: RTEvidenceBasis,
    shape_conflicts: Mapping[str, set[str]],
) -> tuple[tuple[FeatureMembershipGroup, ...], tuple[SparseFeatureDecision, ...]]:
    """Complete group dispositions and their matching per-owner decisions."""
    groups = []
    decisions = []
    for owner_ids in sorted(components):
        group_owners = tuple(owner_by_id[owner_id] for owner_id in owner_ids)
        assigned = len(owner_ids) >= 2 and not any(
            owner_id in weak for owner_id in owner_ids
        )
        shape_rejected = (
            len(owner_ids) == 1
            and owner_ids[0] not in weak
            and bool(shape_conflicts.get(owner_ids[0]))
        )
        unassigned_reason = (
            "peak_correspondence_mismatch"
            if shape_rejected
            else weak.get(owner_ids[0], "no_supported_cross_sample_membership")
        )
        groups.append(
            FeatureMembershipGroup(
                owner_ids=owner_ids,
                disposition="assigned" if assigned else "undecided",
                basis="window_level_rt_band"
                if assigned
                else "insufficient_cross_sample_evidence",
                reason=(
                    "whole_window_rt_trend_supports_one_band"
                    if assigned
                    else unassigned_reason
                ),
            )
        )
        feature_key = f"window:{owner_ids[0]}"
        trends = _NearestInjectionMedians.prepare(group_owners, orders, corrected_rt)
        for owner in group_owners:
            witness = min(
                (item for item in group_owners if item.owner_id != owner.owner_id),
                key=lambda item: (
                    abs(orders[item.sample_stem] - orders[owner.sample_stem]),
                    item.owner_id,
                ),
                default=None,
            )
            trend = (
                trends.median(owner, exclude_owner_id=owner.owner_id)
                if witness is not None
                else None
            )
            corrected_delta = (
                abs(corrected_rt[owner.owner_id] - trend) * 60.0
                if trend is not None
                else None
            )
            decisions.append(
                SparseFeatureDecision(
                    direction="window",
                    sample_stem=owner.sample_stem,
                    owner_id=owner.owner_id,
                    feature_key=feature_key,
                    state="assigned"
                    if assigned
                    else (
                        "proven_incompatible"
                        if shape_rejected
                        else "evaluated_undecided"
                    ),
                    reason=("window_level_rt_trend" if assigned else unassigned_reason),
                    witness_owner_ids=(witness.owner_id,)
                    if witness is not None
                    else tuple(sorted(shape_conflicts[owner.owner_id]))
                    if shape_rejected
                    else (),
                    selected_witness_owner_id=witness.owner_id
                    if witness is not None
                    else None,
                    rt_basis=rt_basis,
                    rt_raw_delta_sec=(
                        abs(owner.owner_apex_rt - witness.owner_apex_rt) * 60.0
                        if witness is not None
                        else None
                    ),
                    rt_drift_corrected_delta_sec=corrected_delta,
                )
            )
    return tuple(groups), tuple(decisions)


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


def _feature_rt_may_support(
    owner: SampleLocalMS1Owner,
    track: _FeatureTrack,
    config: AlignmentConfig,
    rt_frame: RtFrame,
) -> bool:
    """Exclude only tracks whose seed AND last cannot support either RT basis."""
    for witness in track.witness_owners:
        raw_delta = abs(owner.owner_apex_rt - witness.owner_apex_rt) * 60.0
        if not math.isfinite(raw_delta) or raw_delta <= config.preferred_rt_sec:
            return True
        if rt_frame.has_drift:
            corrected = rt_frame.corrected_pair_delta_sec(owner, witness)
            if (
                corrected is None
                or not math.isfinite(corrected)
                or corrected <= config.preferred_rt_sec
            ):
                return True
    return False


def supports_existing_feature(
    owner: SampleLocalMS1Owner,
    members: tuple[SampleLocalMS1Owner, ...],
    *,
    rules: GroupingRules,
    config: AlignmentConfig,
    rt_frame: RtFrame,
) -> bool:
    """Apply the same feature evidence rule without fitting or assigning again."""
    injection_order = rt_frame.injection_order
    if injection_order and all(
        item.sample_stem in injection_order for item in (owner, *members)
    ):
        corrected_rt = {
            item.owner_id: rt_frame.corrected_rt_min(item) for item in (owner, *members)
        }
        return (
            _window_band_candidate(
                owner,
                "frozen-feature",
                members,
                orders=injection_order,
                corrected_rt=corrected_rt,
                config=config,
                rules=rules,
                rt_frame=rt_frame,
            )
            is not None
        )
    ordered = sorted(members, key=lambda item: (item.sample_stem, item.owner_id))
    track = _FeatureTrack.from_owner(ordered[0], direction="forward", rules=rules)
    for member in ordered[1:]:
        track.add(member)
    candidate = _compare_owner_to_feature(
        owner, track, config=config, rt_frame=rt_frame, counts=Counter(), rules=rules
    )
    return candidate.cost is not None and candidate.cost < _UNASSIGNED_COST


def _compare_owner_to_feature(
    owner: SampleLocalMS1Owner,
    track: _FeatureTrack,
    *,
    rules: GroupingRules,
    config: AlignmentConfig,
    rt_frame: RtFrame,
    counts: Counter[str],
) -> _Candidate:
    counts["feature_summary_comparisons"] += 1
    hard_reason, mass_fraction = _feature_hard_reason(
        owner,
        track,
        config,
        rt_frame=rt_frame,
    )
    witnesses = track.witness_owners
    witness_ids = tuple(witness.owner_id for witness in witnesses)
    if hard_reason:
        return _Candidate(
            track.key, None, "proven_incompatible", hard_reason, witness_ids
        )

    evidence: list[OwnerEdgeEvidence] = []
    for witness in witnesses:
        left, right = sorted((owner, witness), key=lambda item: item.owner_id)
        evidence.append(
            evaluate_owner_edge(
                left,
                right,
                config=config,
                rt_frame=rt_frame,
                method_evidence=rules.pair_evidence(left, right, config),
            )
        )
        counts["owner_edge_evaluations"] += 1
    supported = tuple(
        item
        for item in evidence
        if item.decision == "strong_edge"
        or (
            item.decision == "weak_edge"
            and not item.failure_reason
            and item.rt_basis == "raw"
            and item.rt_raw_delta_sec <= config.preferred_rt_sec
            and item.owner_quality == "clean"
            and item.seed_support_level == "moderate"
        )
    )
    if not supported:
        reason = ";".join(sorted({item.reason for item in evidence}))
        return _Candidate(
            track.key,
            None,
            "evaluated_undecided",
            reason or "feature_evidence_not_decisive",
            witness_ids,
        )
    best = min(
        supported,
        key=lambda item: (
            _edge_cost(item, mass_fraction, config),
            item.left_owner_id,
            item.right_owner_id,
        ),
    )
    cost = _edge_cost(best, mass_fraction, config)
    selected_witness_owner_id = (
        best.right_owner_id
        if best.left_owner_id == owner.owner_id
        else best.left_owner_id
    )
    return _Candidate(
        track.key,
        cost,
        "supported",
        best.reason,
        witness_ids,
        selected_witness_owner_id=selected_witness_owner_id,
        rt_basis=best.rt_basis,
        rt_raw_delta_sec=best.rt_raw_delta_sec,
        rt_drift_corrected_delta_sec=best.rt_drift_corrected_delta_sec,
    )


def _feature_hard_reason(
    owner: SampleLocalMS1Owner,
    track: _FeatureTrack,
    config: AlignmentConfig,
    *,
    rt_frame: RtFrame | None = None,
) -> tuple[str, float]:
    if owner.sample_stem in track.sample_stems:
        return "same_sample", math.inf
    identity_failure, measurement_failure = track.method.failures(owner, config)
    if identity_failure:
        return identity_failure, math.inf
    mass_fraction = envelope_fraction(
        track.precursor_min, track.precursor_max, owner.precursor_mz, config.max_ppm
    )
    if mass_fraction > 1.0:
        return "precursor_envelope", mass_fraction
    if peak_correspondence_conflict(
        (owner,),
        track.members,
        config,
        rt_frame=rt_frame,
    ):
        return "peak_correspondence_mismatch", mass_fraction
    return measurement_failure, mass_fraction


def _edge_cost(
    edge: OwnerEdgeEvidence,
    mass_fraction: float,
    config: AlignmentConfig,
) -> float:
    rt_delta = (
        edge.rt_drift_corrected_delta_sec
        if edge.rt_basis == "drift_corrected"
        and edge.rt_drift_corrected_delta_sec is not None
        else edge.rt_raw_delta_sec
    )
    rt_fraction = min(rt_delta / config.preferred_rt_sec, 1.0)
    score_penalty = max(0.0, 100.0 - edge.score) / 100.0
    return min(
        0.999999, 0.70 * rt_fraction + 0.20 * mass_fraction + 0.10 * score_penalty
    )


def _solve_sample_assignment(
    owners: Sequence[SampleLocalMS1Owner],
    candidates_by_owner: Mapping[str, tuple[_Candidate, ...]],
    counts: Counter[str],
    *,
    ambiguity_margin: float = _AMBIGUITY_EPSILON,
    owner_order: Mapping[str, tuple[object, ...]] | None = None,
) -> tuple[dict[str, str], frozenset[str]]:
    arcs = {
        owner.owner_id: {
            candidate.feature_key: cast(float, candidate.cost)
            for candidate in candidates_by_owner[owner.owner_id]
            if candidate.state == "supported"
        }
        for owner in owners
    }
    counts["supported_candidate_arcs"] += sum(len(options) for options in arcs.values())
    reverse: dict[str, set[str]] = defaultdict(set)
    for owner_id, options in arcs.items():
        for feature_key in options:
            reverse[feature_key].add(owner_id)
    pending = {owner_id for owner_id, options in arcs.items() if options}
    assignments: dict[str, str] = {}
    ambiguous: set[str] = set()

    while pending:
        queue = [min(pending)]
        row_ids: set[str] = set()
        feature_keys: set[str] = set()
        while queue:
            owner_id = queue.pop()
            if owner_id in row_ids:
                continue
            row_ids.add(owner_id)
            for feature_key in arcs[owner_id]:
                if feature_key not in feature_keys:
                    feature_keys.add(feature_key)
                    queue.extend(reverse[feature_key] - row_ids)
        pending.difference_update(row_ids)
        rows = sorted(row_ids, key=owner_order.__getitem__ if owner_order else None)
        columns = sorted(feature_keys)
        counts["largest_component_owners"] = max(
            counts["largest_component_owners"], len(rows)
        )
        counts["largest_component_features"] = max(
            counts["largest_component_features"], len(columns)
        )
        counts["largest_component_arcs"] = max(
            counts["largest_component_arcs"], sum(len(arcs[key]) for key in rows)
        )
        if len(rows) == len(columns) == 1:
            assignments[rows[0]] = columns[0]
            if _UNASSIGNED_COST <= arcs[rows[0]][columns[0]] + ambiguity_margin:
                ambiguous.add(rows[0])
            continue
        counts["competition_components"] += 1
        if len(rows) == 1:
            owner_id = rows[0]
            feature_key = min(columns, key=lambda key: (arcs[owner_id][key], key))
            assignments[owner_id] = feature_key
            alternative = min(
                _UNASSIGNED_COST,
                min(arcs[owner_id][key] for key in columns if key != feature_key),
            )
            if alternative <= arcs[owner_id][feature_key] + ambiguity_margin:
                ambiguous.add(owner_id)
            counts["trivial_assignment_components"] += 1
            continue
        if len(columns) == 1:
            feature_key = columns[0]
            owner_id = min(
                rows,
                key=lambda key: (
                    arcs[key][feature_key],
                    owner_order[key] if owner_order else key,
                ),
            )
            best = arcs[owner_id][feature_key]
            alternative = min(
                _UNASSIGNED_COST,
                min(arcs[key][feature_key] for key in rows if key != owner_id),
            )
            # The stable ID breaks only the diagnostic representation tie.
            # Equal/near-equal optima remain ambiguous and cannot add membership.
            assignments[owner_id] = feature_key
            if alternative <= best + ambiguity_margin:
                ambiguous.add(owner_id)
            counts["trivial_assignment_components"] += 1
            continue
        matrix = _assignment_matrix(rows, columns, arcs)
        row_indexes, column_indexes = linear_sum_assignment(matrix)
        counts["assignment_solver_calls"] += 1
        optimum = float(matrix[row_indexes, column_indexes].sum())
        column_by_index = dict(enumerate(columns))
        selected = {
            rows[row_index]: column_by_index[column_index]
            for row_index, column_index in zip(row_indexes, column_indexes, strict=True)
            if column_index < len(columns)
        }
        row_index_by_id = {owner_id: index for index, owner_id in enumerate(rows)}
        column_index_by_key = {key: index for index, key in enumerate(columns)}
        for owner_id, feature_key in selected.items():
            row_index = row_index_by_id[owner_id]
            column_index = column_index_by_key[feature_key]
            saved = matrix[row_index, column_index]
            matrix[row_index, column_index] = _INFEASIBLE_COST
            alternative_rows, alternative_columns = linear_sum_assignment(matrix)
            counts["alternative_solver_calls"] += 1
            alternative = float(matrix[alternative_rows, alternative_columns].sum())
            matrix[row_index, column_index] = saved
            assignments[owner_id] = feature_key
            if alternative <= optimum + ambiguity_margin:
                ambiguous.add(owner_id)
    counts["ambiguous_assignments"] += len(ambiguous)
    return assignments, frozenset(ambiguous)


def _assignment_matrix(
    rows: Sequence[str],
    columns: Sequence[str],
    arcs: Mapping[str, Mapping[str, float]],
) -> np.ndarray:
    matrix = np.full((len(rows), len(columns) + len(rows)), _INFEASIBLE_COST)
    column_index = {feature_key: index for index, feature_key in enumerate(columns)}
    for row_index, owner_id in enumerate(rows):
        for feature_key, cost in arcs[owner_id].items():
            matrix[row_index, column_index[feature_key]] = cost
        matrix[row_index, len(columns) + row_index] = _UNASSIGNED_COST
    return matrix


def _consensus_groups(
    owners: tuple[SampleLocalMS1Owner, ...],
    forward: _DirectionalResult,
    reverse: _DirectionalResult,
    counts: Counter[str],
    *,
    seeded_owner_ids: Collection[str],
) -> tuple[FeatureMembershipGroup, ...]:
    forward_by_owner = _partition_by_owner(forward.groups)
    reverse_by_owner = _partition_by_owner(reverse.groups)
    intersections: dict[tuple[int, int], list[str]] = defaultdict(list)
    for owner in owners:
        key = (forward_by_owner[owner.owner_id], reverse_by_owner[owner.owner_id])
        intersections[key].append(owner.owner_id)

    groups: list[FeatureMembershipGroup] = []
    uncertain_owners = forward.owner_uncertainty | reverse.owner_uncertainty
    for (forward_index, reverse_index), member_ids in sorted(intersections.items()):
        owner_ids = tuple(sorted(member_ids))
        disagreement = forward.groups[forward_index] != reverse.groups[reverse_index]
        seeded = any(owner_id in seeded_owner_ids for owner_id in owner_ids)
        if seeded:
            # An unresolved extension does not revoke an assigned pattern union.
            # Keep its competition undecided without demoting the seed it joined.
            unresolved_extensions = {
                owner_id
                for owner_id in owner_ids
                if owner_id in uncertain_owners and owner_id not in seeded_owner_ids
            }
            for owner_id in sorted(unresolved_extensions):
                groups.append(
                    FeatureMembershipGroup(
                        owner_ids=(owner_id,),
                        disposition="undecided",
                        basis="insufficient_cross_sample_evidence",
                        reason="directional_assignment_contains_unresolved_competition",
                    )
                )
            owner_ids = tuple(
                owner_id
                for owner_id in owner_ids
                if owner_id not in unresolved_extensions
            )
        uncertain = any(owner_id in uncertain_owners for owner_id in owner_ids)
        if disagreement and not seeded:
            counts["consensus_disagreements"] += len(owner_ids)
            groups.append(
                FeatureMembershipGroup(
                    owner_ids=owner_ids,
                    disposition="undecided",
                    basis="directional_membership_disagreement",
                    reason="forward_and_reverse_feature_hypotheses_disagree",
                )
            )
        elif len(owner_ids) < 2 or uncertain:
            groups.append(
                FeatureMembershipGroup(
                    owner_ids=owner_ids,
                    disposition="undecided",
                    basis="insufficient_cross_sample_evidence",
                    reason=(
                        "directional_assignment_contains_unresolved_competition"
                        if uncertain
                        else "no_supported_cross_sample_membership"
                    ),
                )
            )
        elif disagreement:
            # Both passes keep a seed whole, so a disputed intersection holds
            # the seed and only the extensions both passes share.
            groups.append(
                FeatureMembershipGroup(
                    owner_ids=owner_ids,
                    disposition="assigned",
                    basis="peak_trajectory",
                    reason="ordered_ms1_pattern_links_existing_owners",
                )
            )
        else:
            groups.append(
                FeatureMembershipGroup(
                    owner_ids=owner_ids,
                    disposition="assigned",
                    basis="bidirectional_membership_agreement",
                    reason="both_finite_directional_passes_support_the_same_membership",
                )
            )
    return tuple(groups)


def _partition_by_owner(
    groups: Sequence[tuple[str, ...]],
) -> dict[str, int]:
    return {owner_id: index for index, group in enumerate(groups) for owner_id in group}


def _sample_sort_key(
    sample_stem: str,
    injection_order: Mapping[str, int] | None,
) -> tuple[bool, int, str]:
    if injection_order is None or sample_stem not in injection_order:
        return True, 0, sample_stem
    return False, injection_order[sample_stem], sample_stem


def _group_sort_key(group: FeatureMembershipGroup) -> tuple[object, ...]:
    return (
        min(group.owner_ids, default=""),
        group.known_identity_label or "",
        group.owner_ids,
    )


__all__ = [
    "BatchRtWarp",
    "FeatureInferenceOperationCounts",
    "FeatureMembershipGroup",
    "FeatureMembershipInferenceResult",
    "KnownIdentityMembershipConstraint",
    "SparseFeatureDecision",
    "infer_feature_memberships",
]
