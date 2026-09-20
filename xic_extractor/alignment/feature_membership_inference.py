from __future__ import annotations

import math
from bisect import bisect_left, bisect_right, insort
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

import numpy as np
from scipy.optimize import linear_sum_assignment

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import KnownIstdOwnerLookup
from xic_extractor.alignment.edge_scoring import (
    DriftLookupProtocol,
    OwnerEdgeEvidence,
    _seed_support_level,
    evaluate_owner_edge,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner

FeatureMembershipDisposition = Literal[
    "assigned",
    "undecided",
    "known_identity",
    "unresolved_conflict",
]
FeatureMembershipBasis = Literal[
    "bidirectional_membership_agreement",
    "directional_membership_disagreement",
    "insufficient_cross_sample_evidence",
    "known_identity_constraint",
    "known_identity_input_conflict",
]
KnownIdentityConstraintStatus = Literal["satisfied", "unresolved_conflict"]
DecisionDirection = Literal["forward", "reverse"]
SparseDecisionState = Literal[
    "assigned",
    "birth",
    "competition_unassigned",
    "evaluated_undecided",
    "proven_incompatible",
    "supported_not_selected",
]

_ALGORITHM = "finite_bidirectional_sparse_feature_assignment_v1"
_OBJECTIVE = (
    "per_direction_per_sample_minimum_supported_assignment_cost_with_private_"
    "unassigned_options;final_membership_requires_directional_agreement"
)
_TERMINATION = "completed_two_finite_directional_passes"
_UNASSIGNED_COST = 1.0
_INFEASIBLE_COST = 1_000_000.0
_AMBIGUITY_EPSILON = 1e-9


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


@dataclass(slots=True)
class _FeatureTrack:
    key: str
    seed: SampleLocalMS1Owner
    last: SampleLocalMS1Owner
    sample_stems: set[str]
    precursor_min: float
    precursor_max: float
    product_min: float
    product_max: float
    loss_min: float
    loss_max: float
    neutral_loss_tag: str

    @classmethod
    def from_owner(
        cls, owner: SampleLocalMS1Owner, *, direction: DecisionDirection
    ) -> _FeatureTrack:
        event = owner.primary_identity_event
        return cls(
            key=f"{direction}:{owner.owner_id}",
            seed=owner,
            last=owner,
            sample_stems={owner.sample_stem},
            precursor_min=owner.precursor_mz,
            precursor_max=owner.precursor_mz,
            product_min=0.0 if event is None else event.product_mz,
            product_max=0.0 if event is None else event.product_mz,
            loss_min=0.0 if event is None else event.observed_neutral_loss_da,
            loss_max=0.0 if event is None else event.observed_neutral_loss_da,
            neutral_loss_tag=owner.neutral_loss_tag,
        )

    def add(self, owner: SampleLocalMS1Owner) -> None:
        event = owner.primary_identity_event
        self.sample_stems.add(owner.sample_stem)
        self.precursor_min = min(self.precursor_min, owner.precursor_mz)
        self.precursor_max = max(self.precursor_max, owner.precursor_mz)
        if event is not None:
            self.product_min = min(self.product_min, event.product_mz)
            self.product_max = max(self.product_max, event.product_mz)
            self.loss_min = min(self.loss_min, event.observed_neutral_loss_da)
            self.loss_max = max(self.loss_max, event.observed_neutral_loss_da)
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
        for axis, value in self._values(track):
            insort(self._axes[(track.neutral_loss_tag, axis)], (value, track.key))

    def remove(self, track: _FeatureTrack) -> None:
        for axis, value in self._values(track):
            items = self._axes[(track.neutral_loss_tag, axis)]
            item = (value, track.key)
            index = bisect_left(items, item)
            if index < len(items) and items[index] == item:
                items.pop(index)

    def candidates(
        self,
        owner: SampleLocalMS1Owner,
        *,
        config: AlignmentConfig,
        counts: Counter[str],
    ) -> set[str]:
        event = owner.primary_identity_event
        result: set[str] | None = None
        axes = [("precursor", owner.precursor_mz, config.max_ppm)]
        if event is not None:
            axes.extend(
                (
                    ("product", event.product_mz, config.product_mz_tolerance_ppm),
                    (
                        "loss",
                        event.observed_neutral_loss_da,
                        config.observed_loss_tolerance_ppm,
                    ),
                )
            )
        for axis, value, tolerance in axes:
            items = self._axes.get((owner.neutral_loss_tag, axis), ())
            fraction = tolerance / 1_000_000.0
            first = bisect_left(items, (value / (1.0 + fraction), ""))
            last = bisect_right(items, (value * (1.0 + fraction), chr(0x10FFFF)))
            counts["index_entries_scanned"] += last - first
            matches = {feature_key for _, feature_key in items[first:last]}
            result = matches if result is None else result.intersection(matches)
            if not result:
                break
        return result or set()

    @staticmethod
    def _values(track: _FeatureTrack) -> tuple[tuple[str, float], ...]:
        if track.seed.primary_identity_event is None:
            return (("precursor", track.precursor_min),)
        return (
            ("precursor", track.precursor_min),
            ("product", track.product_min),
            ("loss", track.loss_min),
        )


@dataclass(frozen=True, slots=True)
class _Candidate:
    feature_key: str
    cost: float | None
    state: Literal["supported", "evaluated_undecided", "proven_incompatible"]
    reason: str
    witness_owner_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _DirectionalResult:
    groups: tuple[tuple[str, ...], ...]
    decisions: tuple[SparseFeatureDecision, ...]
    owner_uncertainty: frozenset[str]


def infer_feature_memberships(
    owners: Sequence[SampleLocalMS1Owner],
    *,
    config: AlignmentConfig,
    injection_order: Mapping[str, int] | None,
    drift_lookup: DriftLookupProtocol | None,
    known_identity_bindings: Sequence[tuple[str, str]] = (),
) -> FeatureMembershipInferenceResult:
    """Infer cross-sample memberships without materializing an owner-pair graph.

    Exact known-ISTD bindings are indivisible input constraints. Unknown owners
    cannot enter those groups. Remaining owners are inferred twice, in opposite
    sample directions. Each sample uses one joint assignment with a private
    unassigned option per owner. Only memberships present in both finite passes
    are returned as assigned; disagreement remains explicitly undecided.
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
        drift_lookup,
        counts,
        known_identity_bindings,
    )
    ordinary = tuple(
        owner for owner in unique_owners if owner.owner_id not in constrained_owner_ids
    )

    forward = _directional_inference(
        ordinary,
        direction="forward",
        config=config,
        injection_order=injection_order,
        drift_lookup=drift_lookup,
        counts=counts,
    )
    reverse = _directional_inference(
        ordinary,
        direction="reverse",
        config=config,
        injection_order=injection_order,
        drift_lookup=drift_lookup,
        counts=counts,
    )
    inferred_groups = _consensus_groups(ordinary, forward, reverse, counts)
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
        sparse_decisions=tuple((*forward.decisions, *reverse.decisions)),
        operation_counts=operation_counts,
    )


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
    drift_lookup: DriftLookupProtocol | None,
    counts: Counter[str],
    known_identity_bindings: Sequence[tuple[str, str]],
) -> tuple[
    tuple[FeatureMembershipGroup, ...],
    tuple[KnownIdentityMembershipConstraint, ...],
    frozenset[str],
]:
    retained_bindings = (
        tuple((owner_id, label) for owner_id, label, _ in drift_lookup.bindings)
        if isinstance(drift_lookup, KnownIstdOwnerLookup)
        else ()
    )
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
    direction: DecisionDirection,
    config: AlignmentConfig,
    injection_order: Mapping[str, int] | None,
    drift_lookup: DriftLookupProtocol | None,
    counts: Counter[str],
) -> _DirectionalResult:
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
    owner_feature: dict[str, str] = {}
    decisions: list[SparseFeatureDecision] = []
    uncertain: set[str] = set()

    for sample_stem in sample_stems:
        counts["samples_processed"] += 1
        sample_owners = tuple(
            sorted(by_sample[sample_stem], key=lambda item: item.owner_id)
        )
        supported_rows: list[SampleLocalMS1Owner] = []
        candidates_by_owner: dict[str, tuple[_Candidate, ...]] = {}
        for owner in sample_owners:
            weak_reason = _owner_uncertainty_reason(owner, config)
            if weak_reason:
                feature_key = f"{direction}:{owner.owner_id}"
                owner_feature[owner.owner_id] = feature_key
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
            feature_keys = index.candidates(owner, config=config, counts=counts)
            counts["candidate_features_considered"] += len(feature_keys)
            row_candidates = tuple(
                _compare_owner_to_feature(
                    owner,
                    tracks[feature_key],
                    config=config,
                    drift_lookup=drift_lookup,
                    counts=counts,
                )
                for feature_key in sorted(feature_keys)
            )
            candidates_by_owner[owner.owner_id] = row_candidates

        assignments, ambiguous = _solve_sample_assignment(
            supported_rows, candidates_by_owner, counts
        )
        uncertain.update(ambiguous)
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
                    )
                )

            if assigned_key is not None and owner.owner_id not in ambiguous:
                track = tracks[assigned_key]
                index.remove(track)
                track.add(owner)
                index.add(track)
                owner_feature[owner.owner_id] = assigned_key
                continue

            feature = _FeatureTrack.from_owner(owner, direction=direction)
            tracks[feature.key] = feature
            index.add(feature)
            owner_feature[owner.owner_id] = feature.key
            counts["births"] += 1
            if owner.owner_id in ambiguous:
                reason = "alternative_assignment_has_equal_objective"
                state = "evaluated_undecided"
            elif supported_keys:
                reason = "joint_competition_selected_unassigned"
                state = "competition_unassigned"
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


def _compare_owner_to_feature(
    owner: SampleLocalMS1Owner,
    track: _FeatureTrack,
    *,
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None,
    counts: Counter[str],
) -> _Candidate:
    counts["feature_summary_comparisons"] += 1
    hard_reason, mass_fraction = _feature_hard_reason(owner, track, config)
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
            evaluate_owner_edge(left, right, config=config, drift_lookup=drift_lookup)
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
    return _Candidate(
        track.key,
        cost,
        "supported",
        best.reason,
        witness_ids,
    )


def _feature_hard_reason(
    owner: SampleLocalMS1Owner,
    track: _FeatureTrack,
    config: AlignmentConfig,
) -> tuple[str, float]:
    event = owner.primary_identity_event
    if owner.sample_stem in track.sample_stems:
        return "same_sample", math.inf
    if (event is None) != (track.seed.primary_identity_event is None):
        return "identity_evidence_type_mismatch", math.inf
    if owner.neutral_loss_tag != track.neutral_loss_tag:
        return "neutral_loss_tag_mismatch", math.inf
    mass_fraction = _envelope_fraction(
        track.precursor_min, track.precursor_max, owner.precursor_mz, config.max_ppm
    )
    if mass_fraction > 1.0:
        return "precursor_envelope", mass_fraction
    if event is None:
        return "", mass_fraction
    if (
        _envelope_fraction(
            track.product_min,
            track.product_max,
            event.product_mz,
            config.product_mz_tolerance_ppm,
        )
        > 1.0
    ):
        return "product_envelope", mass_fraction
    if (
        _envelope_fraction(
            track.loss_min,
            track.loss_max,
            event.observed_neutral_loss_da,
            config.observed_loss_tolerance_ppm,
        )
        > 1.0
    ):
        return "observed_loss_envelope", mass_fraction
    return "", mass_fraction


def _envelope_fraction(
    current_min: float,
    current_max: float,
    value: float,
    tolerance_ppm: float,
) -> float:
    lower = min(current_min, value)
    upper = max(current_max, value)
    if lower <= 0.0:
        return math.inf
    return (upper - lower) / lower * 1_000_000.0 / tolerance_ppm


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
        rows = sorted(row_ids)
        columns = sorted(feature_keys)
        if len(rows) == len(columns) == 1:
            assignments[rows[0]] = columns[0]
            continue
        counts["competition_components"] += 1
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
        for owner_id, feature_key in selected.items():
            row_index = rows.index(owner_id)
            column_index = columns.index(feature_key)
            saved = matrix[row_index, column_index]
            matrix[row_index, column_index] = _INFEASIBLE_COST
            alternative_rows, alternative_columns = linear_sum_assignment(matrix)
            counts["alternative_solver_calls"] += 1
            alternative = float(matrix[alternative_rows, alternative_columns].sum())
            matrix[row_index, column_index] = saved
            assignments[owner_id] = feature_key
            if alternative <= optimum + _AMBIGUITY_EPSILON:
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
        uncertain = any(owner_id in uncertain_owners for owner_id in owner_ids)
        if disagreement:
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


def _owner_uncertainty_reason(
    owner: SampleLocalMS1Owner,
    config: AlignmentConfig,
) -> str:
    if owner.identity_conflict:
        return "owner_identity_conflict"
    if owner.is_provisional:
        return "provisional_owner"
    if owner.owner_area <= 0.0:
        return "nonpositive_owner_area"
    if "ambiguous" in owner.assignment_reason:
        return "ambiguous_owner_assignment"
    if _seed_support_level(owner, owner, config) == "weak":
        return "weak_identity_evidence"
    return ""


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
    "FeatureInferenceOperationCounts",
    "FeatureMembershipGroup",
    "FeatureMembershipInferenceResult",
    "KnownIdentityMembershipConstraint",
    "SparseFeatureDecision",
    "infer_feature_memberships",
]
