"""Bounded assignments of acquired Backfill candidates to fixed original groups."""

from __future__ import annotations

import math
from collections.abc import Sequence

from xic_extractor.alignment.membership_selection import (
    AcceptedMembershipRisk,
    MembershipComparison,
    canonical_partition,
)


def select_backfill_memberships(
    *,
    comparison: MembershipComparison,
    original_groups: dict[str, tuple[str, ...]],
    proposals: Sequence[tuple[str, str]],
    conflicts: frozenset[tuple[int, int]],
) -> tuple[frozenset[int], dict[int, AcceptedMembershipRisk]]:
    """Compare blank and fixed-group extensions, retaining 64 best configurations.

    Each proposal is (group ID, owner ID). Conflicts carry physical occupancy
    and cell exclusivity from the acquisition-evidence adapter. Original owners
    never move; no new candidate or measurement is created here.
    """
    bases = {
        key: comparison.fixed_group_cost(ids, ids)
        for key, ids in original_groups.items()
    }
    # A candidate-to-original hard conflict cannot be repaired by adding peers.
    # Exclude it from rescue bundles, but retain its blank in the cost universe.
    compatible_indices = tuple(
        index
        for index, (group, owner) in enumerate(proposals)
        if all(
            math.isfinite(comparison.pair(owner, peer)[0])
            for peer in original_groups[group]
        )
    )

    def groups_for(selected: frozenset[int]) -> dict[str, tuple[str, ...]]:
        groups = dict(original_groups)
        for index in sorted(selected):
            group, owner = proposals[index]
            groups[group] = (*groups[group], owner)
        return groups

    def cost(selected: frozenset[int]) -> float:
        # A blank costs one, as separation does in the membership policy.
        # Only changes to original groups contribute; their accepted state is fixed.
        groups = groups_for(selected)
        return (
            len(proposals)
            - len(selected)
            + math.fsum(
                comparison.fixed_group_cost(ids, original_groups[group]) - bases[group]
                for group, ids in groups.items()
                if ids != original_groups[group]
            )
        )

    def key(selected: frozenset[int]) -> tuple[object, ...]:
        partition = canonical_partition(tuple(groups_for(selected).values()))
        return (
            cost(selected),
            comparison.peer_fit(partition),
            comparison.partition_key(partition),
            partition,
        )

    states: set[frozenset[int]] = {frozenset()}
    for index in compatible_indices:
        expanded = set(states)
        for selected in states:
            if any(
                (min(index, other), max(index, other)) in conflicts
                for other in selected
            ):
                continue
            candidate = selected | {index}
            if math.isfinite(cost(candidate)):
                expanded.add(candidate)
        states = set(sorted(expanded, key=key)[:64])
    selected = min(states, key=key)

    def neighbors(current: frozenset[int]) -> set[frozenset[int]]:
        choices = {current - {index} for index in current}
        for index in compatible_indices:
            if index in current:
                continue
            displaced = {
                other
                for other in current
                if (min(index, other), max(index, other)) in conflicts
            }
            choices.add((current - displaced) | {index})
        # Feasibility is not hereditary: a complete group can be feasible even
        # when each partial extension has an RT gap. Try its compatible bundle
        # before relying on one-at-a-time improvements.
        for group in original_groups:
            bundle = frozenset(i for i in current if proposals[i][0] != group)
            for index in compatible_indices:
                candidate_group, _ = proposals[index]
                if candidate_group == group and not any(
                    (min(index, other), max(index, other)) in conflicts
                    for other in bundle
                ):
                    bundle |= {index}
            choices.add(bundle)
        return {choice for choice in choices if math.isfinite(cost(choice))}

    while True:
        nearby = neighbors(selected)
        improved = min((selected, *nearby), key=key)
        if improved == selected:
            states.update(nearby)
            break
        selected = improved
    selected_cost = cost(selected)
    groups = groups_for(selected)
    risks = {}
    for index in selected:
        group, owner = proposals[index]
        alternatives = {state for state in states if index not in state}
        alternatives.update(
            (
                selected - {index},
                frozenset(i for i in selected if proposals[i][0] != group),
            )
        )
        alternatives = {state for state in alternatives if math.isfinite(cost(state))}
        alternative = min(alternatives, key=key)
        alternative_cost = cost(alternative)
        equivalent = tuple(
            canonical_partition(tuple(groups_for(state).values()))
            for state in sorted(alternatives, key=key)
            if math.isclose(cost(state), selected_cost, abs_tol=1e-12)
        )
        contradictions = tuple(
            sorted(
                {
                    flag
                    for peer in groups[group]
                    if peer != owner
                    for flag in comparison.pair(owner, peer)[1]
                }
            )
        )
        flags = {"accepted_contradiction"} if contradictions else set()
        if not math.isfinite(comparison.group_cost(original_groups[group])):
            flags.update(("fixed_original_conflict", "accepted_contradiction"))
        if any(index in pair for pair in conflicts):
            flags.add("backfill_competition")
        if alternative_cost - selected_cost <= 0.05:
            flags.add("accepted_competition")
        risks[index] = AcceptedMembershipRisk(
            status="equivalent_alternatives" if equivalent else "evaluated_alternative",
            phase="backfill",
            selected_owner_ids=tuple(sorted(groups[group])),
            alternative_partition=canonical_partition(
                tuple(groups_for(alternative).values())
            ),
            equivalent_alternatives=equivalent,
            selected_cost=selected_cost,
            alternative_cost=alternative_cost,
            cost_gap=alternative_cost - selected_cost,
            rt_basis=comparison.rt_basis,
            evidence_basis="bounded_acquired_backfill",
            soft_contradictions=contradictions,
            risk_flags=tuple(sorted(flags)),
            rt_estimate=comparison.rt_estimates[owner],
        )
    return selected, risks
