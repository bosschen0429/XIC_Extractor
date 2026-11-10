"""Finalize complete-group selections or bidirectional membership consensus."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Collection, Sequence
from dataclasses import replace

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_rules import GroupingRules
from xic_extractor.alignment.membership_selection import (
    MembershipComparison,
    Partition,
    canonical_partition,
    measured_owner_key,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.mass_accuracy import within_ppm

from .directional import _DirectionalResult
from .models import FeatureMembershipGroup, SparseFeatureDecision
from .sample_assignment import _UNASSIGNED_COST, _Candidate, _solve_sample_assignment


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
    for owner in sorted(owners, key=lambda o: (o.peak.mz, measured_owner_key(o))):
        if not windows or not within_ppm(
            owner.peak.mz, windows[-1][-1].peak.mz, config.max_ppm
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
                    assess_ambiguity=False,
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
