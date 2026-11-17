"""Bounded assignments of acquired Backfill candidates to fixed original groups."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from xic_extractor.alignment.membership_selection import (
    AcceptedMembershipRisk,
    MembershipComparison,
    canonical_partition,
    owner_facts,
    owner_partition_facts,
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

    worst_pair = {
        index: max(
            (
                comparison.pair(proposals[index][1], peer)[0]
                for peer in original_groups[proposals[index][0]]
            ),
            default=0.0,
        )
        for index in compatible_indices
    }

    def groups_for(selected: frozenset[int]) -> dict[str, tuple[str, ...]]:
        groups = dict(original_groups)
        for index in sorted(selected):
            group, owner = proposals[index]
            groups[group] = (*groups[group], owner)
        return groups

    costs: dict[frozenset[int], float] = {}

    def cost(selected: frozenset[int]) -> float:
        # A blank costs one, as separation does in the membership policy.
        # Only changes to original groups contribute; their accepted state is fixed.
        if selected not in costs:
            groups = groups_for(selected)
            costs[selected] = (
                len(proposals)
                - len(selected)
                + math.fsum(
                    comparison.fixed_group_cost(ids, original_groups[group])
                    - bases[group]
                    for group, ids in groups.items()
                    if ids != original_groups[group]
                )
            )
        return costs[selected]

    def key(selected: frozenset[int]) -> tuple[object, ...]:
        groups = groups_for(selected)
        partition = canonical_partition(tuple(groups.values()))
        # Peer fit only breaks primary-cost ties, in each original's model.
        return (
            cost(selected),
            _OnTie(
                lambda: comparison.fixed_peer_fit(
                    tuple((ids, original_groups[name]) for name, ids in groups.items())
                )
            ),
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
        # when each partial extension has an RT gap. Try its compatible
        # bundles before relying on one-at-a-time improvements, nearest
        # candidates first so one distant candidate cannot poison the rest.
        for group in original_groups:
            bundle = frozenset(i for i in current if proposals[i][0] != group)
            choices.add(bundle)
            for index in sorted(
                (i for i in compatible_indices if proposals[i][0] == group),
                key=lambda i: (
                    worst_pair[i],
                    comparison.measured_keys[proposals[i][1]],
                ),
            ):
                if not any(
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
                    for flag in comparison.fixed_pair(
                        owner, peer, original_groups[group]
                    )[1]
                }
            )
        )
        flags = {"accepted_contradiction"} if contradictions else set()
        original_model = comparison.fixed_group_model(
            original_groups[group], original_groups[group]
        )
        if not math.isfinite(original_model.evidence) or original_model.population_gap:
            flags.update(("fixed_original_conflict", "accepted_contradiction"))
        if any(index in pair for pair in conflicts):
            flags.add("backfill_competition")
        if alternative_cost - selected_cost <= 0.05:
            flags.add("accepted_competition")
        risks[index] = AcceptedMembershipRisk(
            status="equivalent_alternatives" if equivalent else "evaluated_alternative",
            phase="backfill",
            selected_owners=owner_facts(comparison.owners, groups[group]),
            alternative_partition=owner_partition_facts(
                comparison.owners,
                canonical_partition(tuple(groups_for(alternative).values())),
            ),
            equivalent_alternatives=tuple(
                owner_partition_facts(comparison.owners, value) for value in equivalent
            ),
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


class _OnTie:
    """A sort-key value computed only when the preceding key elements tie."""

    __slots__ = ("_compute", "_value")

    def __init__(self, compute: Callable[[], float]) -> None:
        self._compute = compute
        self._value: float | None = None

    @property
    def value(self) -> float:
        if self._value is None:
            self._value = self._compute()
        return self._value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _OnTie) and self.value == other.value

    def __lt__(self, other: _OnTie) -> bool:
        return self.value < other.value

    __hash__ = None  # type: ignore[assignment]
