"""Admit owners and known identities, then compose membership inference stages."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_rules import GroupingRules
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_frame import RtFrame

from .directional import _directional_inference
from .finalization import _accepted_final_memberships, _consensus_groups
from .models import (
    _ALGORITHM,
    _OBJECTIVE,
    _TERMINATION,
    DecisionDirection,
    FeatureInferenceOperationCounts,
    FeatureMembershipGroup,
    FeatureMembershipInferenceResult,
    KnownIdentityMembershipConstraint,
)
from .trajectory_membership import _membership_trajectories, _trajectory_seeds
from .window_inference import _window_inference


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


def _group_sort_key(group: FeatureMembershipGroup) -> tuple[object, ...]:
    return (
        min(group.owner_ids, default=""),
        group.known_identity_label or "",
        group.owner_ids,
    )
