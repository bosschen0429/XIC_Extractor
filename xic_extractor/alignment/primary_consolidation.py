from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, fields, is_dataclass, replace
from itertools import combinations
from statistics import mean, median
from types import SimpleNamespace
from typing import Any

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleContractError,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleLineageEdge,
    CrossSampleNeighborhoodBudget,
    CrossSampleNeighborhoodEdge,
    CrossSamplePrimaryConsolidationEdgeFacts,
    CrossSamplePrimaryDispositionEdgeFacts,
    CrossSampleSuccessorApplication,
    apply_group_disposition,
    classify_cross_sample_primary_consolidation_edge,
    classify_cross_sample_primary_disposition_edge,
    cross_sample_group_identity_from_carrier,
    mint_cross_sample_consolidation_successor,
    predecessor_membership_neighborhood_edges,
    validate_cross_sample_group_population,
    validate_cross_sample_successor_owner_projection,
)
from xic_extractor.alignment.family_compatibility import (
    family_center_mz,
    family_center_rt,
    family_observed_loss,
    family_product_mz,
    ppm,
)
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.output_rows import cells_by_cluster, row_id

_PRESENT_STATUSES = {"detected", "rescued"}
_SAME_APEX_DETECTED_PREFERENCE_SEC = 1.0
_PRIMARY_CONSOLIDATION_BUDGET = CrossSampleNeighborhoodBudget(
    max_members=64,
    max_eligible_edges=128,
)


@dataclass(frozen=True)
class _Observation:
    cell: AlignedCell
    source_cluster_id: str
    original_status: str


@dataclass(frozen=True)
class _FamilyStats:
    detected_count: int
    rescue_count: int
    duplicate_count: int
    ambiguous_count: int
    total_area: float

def consolidate_primary_family_rows(
    matrix: AlignmentMatrix,
    config: AlignmentConfig,
) -> AlignmentMatrix:
    clusters_by_id = {row_id(cluster): cluster for cluster in matrix.clusters}
    _active_primary_population(clusters_by_id)
    grouped_cells = cells_by_cluster(matrix)
    edge_facts = _duplicate_claim_edges(matrix, clusters_by_id, config)
    graph = _duplicate_claim_graph(edge_facts)
    components = _connected_components(graph)
    if not components:
        return _demote_near_duplicate_primary_competitors(matrix, config)

    cluster_replacements: dict[str, Any] = {}
    replacement_cells: dict[str, tuple[AlignedCell, ...]] = {}
    for component in components:
        winner_id = _winner_cluster_id(component, clusters_by_id, grouped_cells)
        selected_by_sample = _selected_observations_by_sample(
            component,
            grouped_cells,
        )
        center = _component_center(
            component,
            clusters_by_id=clusters_by_id,
            selected_by_sample=selected_by_sample,
            winner_id=winner_id,
        )
        successor_application = _primary_consolidation_successor(
            component,
            clusters_by_id=clusters_by_id,
            grouped_cells=grouped_cells,
            selected_by_sample=selected_by_sample,
            edge_facts=edge_facts,
        )
        if successor_application.status == "unassessable":
            continue
        successor = successor_application.successors[0]
        cluster_replacements[winner_id] = _primary_cluster(
            clusters_by_id[winner_id],
            component=component,
            clusters_by_id=clusters_by_id,
            center=center,
            successor=successor,
            lineage=successor_application.lineage,
        )
        for cluster_id in component:
            if cluster_id == winner_id:
                continue
            cluster_replacements[cluster_id] = _loser_cluster(
                clusters_by_id[cluster_id],
                winner_id=winner_id,
                winner_group_hypothesis_id=successor.group_hypothesis_id,
            )
        replacement_cells[winner_id] = _winner_cells(
            winner_id,
            grouped_cells=grouped_cells,
            selected_by_sample=selected_by_sample,
            sample_order=matrix.sample_order,
            center_rt=center["family_center_rt"],
            winner_group_hypothesis_id=successor.group_hypothesis_id,
        )

    clusters = tuple(
        cluster_replacements.get(row_id(cluster), cluster)
        for cluster in matrix.clusters
    )
    cells: list[AlignedCell] = []
    for cluster in matrix.clusters:
        cluster_id = row_id(cluster)
        if cluster_id in replacement_cells:
            cells.extend(replacement_cells[cluster_id])
        elif cluster_id in cluster_replacements:
            cells.extend(
                _render_only_loser_cell(cell)
                for cell in grouped_cells.get(cluster_id, ())
            )
        else:
            cells.extend(grouped_cells.get(cluster_id, ()))
    consolidated = AlignmentMatrix(
        clusters=clusters,
        cells=tuple(cells),
        sample_order=matrix.sample_order,
    )
    return _demote_near_duplicate_primary_competitors(consolidated, config)


def _demote_near_duplicate_primary_competitors(
    matrix: AlignmentMatrix,
    config: AlignmentConfig,
) -> AlignmentMatrix:
    clusters_by_id = {row_id(cluster): cluster for cluster in matrix.clusters}
    _active_primary_population(clusters_by_id)
    grouped_cells = cells_by_cluster(matrix)
    graph = _near_duplicate_primary_graph(matrix, clusters_by_id, grouped_cells, config)
    components = _connected_components(graph)
    if not components:
        return matrix

    cluster_replacements: dict[str, Any] = {}
    for component in components:
        winner_id = _family_winner_cluster_id(component, clusters_by_id, grouped_cells)
        for cluster_id in component:
            if cluster_id == winner_id:
                continue
            cluster_replacements[cluster_id] = _disposition_only_cluster(
                clusters_by_id[cluster_id],
            )
    return AlignmentMatrix(
        clusters=tuple(
            cluster_replacements.get(row_id(cluster), cluster)
            for cluster in matrix.clusters
        ),
        cells=matrix.cells,
        sample_order=matrix.sample_order,
    )


def _duplicate_claim_edges(
    matrix: AlignmentMatrix,
    clusters_by_id: dict[str, Any],
    config: AlignmentConfig,
) -> dict[tuple[str, str], CrossSampleNeighborhoodEdge]:
    active_by_group_id: dict[str, tuple[str, CrossSampleGroupIdentity]] = {}
    for cluster_id, cluster in clusters_by_id.items():
        if _render_only_cluster(cluster):
            continue
        identity = cross_sample_group_identity_from_carrier(cluster)
        if identity.group_hypothesis_id in active_by_group_id:
            raise CrossSampleContractError(
                "primary active population duplicates canonical group identity"
            )
        active_by_group_id[identity.group_hypothesis_id] = (cluster_id, identity)
    edges: dict[tuple[str, str], CrossSampleNeighborhoodEdge] = {}
    for cell in matrix.cells:
        if cell.status != "duplicate_assigned":
            continue
        source_group_id = cell.claim_source_group_hypothesis_id
        winner_group_id = cell.claim_winner_group_hypothesis_id
        if not source_group_id or not winner_group_id:
            continue
        source = active_by_group_id.get(source_group_id)
        winner = active_by_group_id.get(winner_group_id)
        if source is None or winner is None:
            continue
        source_cluster_id, source_identity = source
        winner_cluster_id, winner_identity = winner
        if (
            source_cluster_id != cell.cluster_id
            or source_cluster_id == winner_cluster_id
        ):
            continue
        source_member = _member_for_primary_sample(
            clusters_by_id[source_cluster_id],
            identity=source_identity,
            sample_stem=cell.sample_stem,
        )
        winner_member = _member_for_primary_sample(
            clusters_by_id[winner_cluster_id],
            identity=winner_identity,
            sample_stem=cell.sample_stem,
        )
        if source_member is None or winner_member is None:
            continue
        edge = classify_cross_sample_primary_consolidation_edge(
            CrossSamplePrimaryConsolidationEdgeFacts(
                left_group=source_identity,
                right_group=winner_identity,
                left_member_subject=source_member,
                right_member_subject=winner_member,
                left_neutral_loss_tag=_neutral_loss_tag(
                    clusters_by_id[source_cluster_id],
                ),
                right_neutral_loss_tag=_neutral_loss_tag(
                    clusters_by_id[winner_cluster_id],
                ),
                left_precursor_mz=_family_center_mz(
                    clusters_by_id[source_cluster_id],
                ),
                right_precursor_mz=_family_center_mz(
                    clusters_by_id[winner_cluster_id],
                ),
                left_product_mz=_family_product_mz(
                    clusters_by_id[source_cluster_id],
                ),
                right_product_mz=_family_product_mz(
                    clusters_by_id[winner_cluster_id],
                ),
                left_observed_loss_da=_family_observed_loss(
                    clusters_by_id[source_cluster_id],
                ),
                right_observed_loss_da=_family_observed_loss(
                    clusters_by_id[winner_cluster_id],
                ),
                left_rt_min=_family_center_rt(clusters_by_id[source_cluster_id]),
                right_rt_min=_family_center_rt(clusters_by_id[winner_cluster_id]),
                claim_source_group_hypothesis_id=source_group_id,
                claim_winner_group_hypothesis_id=winner_group_id,
                left_targeted_istd_anchor=(
                    getattr(
                        clusters_by_id[source_cluster_id],
                        "targeted_istd_anchor",
                        None,
                    )
                ),
                right_targeted_istd_anchor=(
                    getattr(
                        clusters_by_id[winner_cluster_id],
                        "targeted_istd_anchor",
                        None,
                    )
                ),
            ),
            config=config,
        )
        if edge.disposition == "eligible":
            edges[_ordered_pair(source_cluster_id, winner_cluster_id)] = edge
    return edges


def _duplicate_claim_graph(
    edges: dict[tuple[str, str], CrossSampleNeighborhoodEdge],
) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for left, right in edges:
        graph[left].add(right)
        graph[right].add(left)
    return graph


def _near_duplicate_primary_graph(
    matrix: AlignmentMatrix,
    clusters_by_id: dict[str, Any],
    grouped_cells: dict[str, tuple[AlignedCell, ...]],
    config: AlignmentConfig,
) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    active_population = _active_primary_population(clusters_by_id)
    stats_by_id = {
        cluster_id: _family_stats(grouped_cells.get(cluster_id, ()))
        for cluster_id in active_population
    }
    for left_id, right_id in combinations(sorted(active_population), 2):
        edge = _primary_disposition_edge(
            left=clusters_by_id[left_id],
            right=clusters_by_id[right_id],
            left_group=active_population[left_id],
            right_group=active_population[right_id],
            left_stats=stats_by_id[left_id],
            right_stats=stats_by_id[right_id],
            left_cells=grouped_cells.get(left_id, ()),
            right_cells=grouped_cells.get(right_id, ()),
            config=config,
        )
        if edge is None or edge.disposition != "eligible":
            continue
        graph[left_id].add(right_id)
        graph[right_id].add(left_id)
    return graph


def _ordered_pair(left_id: str, right_id: str) -> tuple[str, str]:
    return (left_id, right_id) if left_id < right_id else (right_id, left_id)


def _primary_disposition_edge(
    left: Any,
    right: Any,
    *,
    left_group: CrossSampleGroupIdentity,
    right_group: CrossSampleGroupIdentity,
    left_stats: _FamilyStats,
    right_stats: _FamilyStats,
    left_cells: tuple[AlignedCell, ...],
    right_cells: tuple[AlignedCell, ...],
    config: AlignmentConfig,
) -> CrossSampleNeighborhoodEdge | None:
    return classify_cross_sample_primary_disposition_edge(
        CrossSamplePrimaryDispositionEdgeFacts(
            left_group=left_group,
            right_group=right_group,
            left_detected_members=_detected_primary_members(
                left,
                identity=left_group,
                cells=left_cells,
            ),
            right_detected_members=_detected_primary_members(
                right,
                identity=right_group,
                cells=right_cells,
            ),
            left_detected_count=left_stats.detected_count,
            right_detected_count=right_stats.detected_count,
            left_rescue_count=left_stats.rescue_count,
            right_rescue_count=right_stats.rescue_count,
            left_neutral_loss_tag=_neutral_loss_tag(left),
            right_neutral_loss_tag=_neutral_loss_tag(right),
            left_precursor_mz=_family_center_mz(left),
            right_precursor_mz=_family_center_mz(right),
            left_product_mz=_family_product_mz(left),
            right_product_mz=_family_product_mz(right),
            left_observed_loss_da=_family_observed_loss(left),
            right_observed_loss_da=_family_observed_loss(right),
            left_rt_min=_family_center_rt(left),
            right_rt_min=_family_center_rt(right),
            left_targeted_istd_anchor=getattr(
                left,
                "targeted_istd_anchor",
                None,
            ),
            right_targeted_istd_anchor=getattr(
                right,
                "targeted_istd_anchor",
                None,
            ),
        ),
        config=config,
    )


def _detected_primary_members(
    cluster: object,
    *,
    identity: CrossSampleGroupIdentity,
    cells: tuple[AlignedCell, ...],
) -> tuple[CrossSampleGroupMemberSubject, ...]:
    members: list[CrossSampleGroupMemberSubject] = []
    for cell in cells:
        if cell.status != "detected" or not _valid_present_cell(cell):
            continue
        member = _member_for_primary_sample(
            cluster,
            identity=identity,
            sample_stem=cell.sample_stem,
        )
        if member is not None:
            members.append(member)
    if len({member.member_subject_id for member in members}) != len(members):
        raise CrossSampleContractError(
            "primary disposition detected members are not one-to-one"
        )
    return tuple(members)


def _connected_components(graph: dict[str, set[str]]) -> tuple[tuple[str, ...], ...]:
    visited: set[str] = set()
    components: list[tuple[str, ...]] = []
    ordered_neighbors = {
        node: tuple(sorted(neighbors)) for node, neighbors in graph.items()
    }
    for start in sorted(graph):
        if start in visited:
            continue
        queue: deque[str] = deque((start,))
        visited.add(start)
        component: list[str] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in ordered_neighbors[current]:
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append(neighbor)
        if len(component) > 1:
            components.append(tuple(component))
    return tuple(components)


def _winner_cluster_id(
    component: tuple[str, ...],
    clusters_by_id: dict[str, Any],
    grouped_cells: dict[str, tuple[AlignedCell, ...]],
) -> str:
    center_rt = _component_seed_rt(component, grouped_cells, clusters_by_id)
    return min(
        component,
        key=lambda cluster_id: (
            -_current_present_count(grouped_cells.get(cluster_id, ())),
            -_current_detected_count(grouped_cells.get(cluster_id, ())),
            abs(_family_center_rt(clusters_by_id[cluster_id]) - center_rt),
            _canonical_group_sort_id(clusters_by_id[cluster_id]),
        ),
    )


def _family_winner_cluster_id(
    component: tuple[str, ...],
    clusters_by_id: dict[str, Any],
    grouped_cells: dict[str, tuple[AlignedCell, ...]],
) -> str:
    center_rt = _component_seed_rt(component, grouped_cells, clusters_by_id)
    stats_by_id = {
        cluster_id: _family_stats(grouped_cells.get(cluster_id, ()))
        for cluster_id in component
    }
    return min(
        component,
        key=lambda cluster_id: (
            -int(stats_by_id[cluster_id].detected_count >= 2),
            -stats_by_id[cluster_id].detected_count,
            stats_by_id[cluster_id].duplicate_count
            + stats_by_id[cluster_id].ambiguous_count,
            -stats_by_id[cluster_id].rescue_count,
            -stats_by_id[cluster_id].total_area,
            abs(_family_center_rt(clusters_by_id[cluster_id]) - center_rt),
            _canonical_group_sort_id(clusters_by_id[cluster_id]),
        ),
    )


def _selected_observations_by_sample(
    component: tuple[str, ...],
    grouped_cells: dict[str, tuple[AlignedCell, ...]],
) -> dict[str, _Observation]:
    observations = [
        observation
        for cluster_id in component
        for cell in grouped_cells.get(cluster_id, ())
        for observation in (_observation(cell),)
        if observation is not None
    ]
    if not observations:
        return {}
    center_rt = _observation_seed_rt(observations)
    by_sample: dict[str, list[_Observation]] = defaultdict(list)
    for observation in observations:
        by_sample[observation.cell.sample_stem].append(observation)
    return {
        sample_stem: _selected_observation(items, center_rt)
        for sample_stem, items in by_sample.items()
    }


def _canonical_group_sort_id(cluster: object) -> str:
    return cross_sample_group_identity_from_carrier(cluster).group_hypothesis_id


def _render_only_cluster(cluster: object) -> bool:
    state = getattr(cluster, "cross_sample_state", "active")
    if state not in {"active", "render_only"}:
        raise CrossSampleContractError("primary cluster state is not closed")
    return state == "render_only"


def _active_primary_population(
    clusters_by_id: dict[str, Any],
) -> dict[str, CrossSampleGroupIdentity]:
    active: dict[str, CrossSampleGroupIdentity] = {}
    active_group_ids: set[str] = set()
    for cluster_id, cluster in clusters_by_id.items():
        if _render_only_cluster(cluster):
            continue
        identity = cross_sample_group_identity_from_carrier(cluster)
        if identity.group_hypothesis_id in active_group_ids:
            raise CrossSampleContractError(
                "primary active population duplicates canonical group identity"
            )
        active_group_ids.add(identity.group_hypothesis_id)
        active[cluster_id] = identity
    validate_cross_sample_group_population(tuple(active.values()))
    return active


def _member_for_primary_sample(
    cluster: object,
    *,
    identity: CrossSampleGroupIdentity,
    sample_stem: str,
) -> CrossSampleGroupMemberSubject | None:
    members = tuple(
        owner.cross_sample_member_subject
        for owner in getattr(cluster, "owners", ())
        if owner.sample_stem == sample_stem
        and owner.cross_sample_member_subject is not None
    )
    if not members:
        return None
    if len(members) != 1 or members[0] not in identity.member_subjects:
        raise CrossSampleContractError(
            "primary claim edge lacks an exact owner-backed sample member"
        )
    return members[0]


def _neutral_loss_tag(cluster: object) -> str:
    value = str(getattr(cluster, "neutral_loss_tag", ""))
    if not value:
        raise CrossSampleContractError(
            "primary consolidation neutral-loss tag is required"
        )
    return value


def _primary_consolidation_successor(
    component: tuple[str, ...],
    *,
    clusters_by_id: dict[str, Any],
    grouped_cells: dict[str, tuple[AlignedCell, ...]],
    selected_by_sample: dict[str, _Observation],
    edge_facts: dict[tuple[str, str], CrossSampleNeighborhoodEdge],
) -> CrossSampleSuccessorApplication:
    """Mint the accepted component proposal; values stay a facade projection."""

    predecessors = tuple(
        cross_sample_group_identity_from_carrier(clusters_by_id[cluster_id])
        for cluster_id in component
    )
    if not selected_by_sample:
        return CrossSampleSuccessorApplication(
            successors=(),
            lineage=(),
            status="unassessable",
            reason="primary_consolidation_has_no_selected_value_projection",
        )
    successor_members = _primary_successor_member_closure(
        component,
        clusters_by_id=clusters_by_id,
        grouped_cells=grouped_cells,
        edge_facts=edge_facts,
    )
    if successor_members is None:
        return CrossSampleSuccessorApplication(
            successors=(),
            lineage=(),
            status="unassessable",
            reason="primary_consolidation_has_no_deterministic_claim_member_closure",
        )
    edges = list(predecessor_membership_neighborhood_edges(predecessors))
    component_ids = set(component)
    edges.extend(
        edge
        for (left_cluster, right_cluster), edge in edge_facts.items()
        if left_cluster in component_ids and right_cluster in component_ids
    )
    return mint_cross_sample_consolidation_successor(
        predecessors=predecessors,
        successor_members=successor_members,
        edges=tuple(edges),
        budget=_PRIMARY_CONSOLIDATION_BUDGET,
        population=tuple(_active_primary_population(clusters_by_id).values()),
    )


def _primary_successor_member_closure(
    component: tuple[str, ...],
    *,
    clusters_by_id: dict[str, Any],
    grouped_cells: dict[str, tuple[AlignedCell, ...]],
    edge_facts: dict[tuple[str, str], CrossSampleNeighborhoodEdge],
) -> tuple[CrossSampleGroupMemberSubject, ...] | None:
    """Close membership from owner-backed claims, never selected cell values."""

    identities_by_cluster = {
        cluster_id: cross_sample_group_identity_from_carrier(
            clusters_by_id[cluster_id]
        )
        for cluster_id in component
    }
    cluster_by_group_id = {
        identity.group_hypothesis_id: cluster_id
        for cluster_id, identity in identities_by_cluster.items()
    }
    if len(cluster_by_group_id) != len(identities_by_cluster):
        return None
    members_by_sample: dict[str, list[CrossSampleGroupMemberSubject]] = defaultdict(
        list
    )
    for identity in identities_by_cluster.values():
        for member in identity.member_subjects:
            members_by_sample[member.canonical_sample_identity_id].append(member)

    winner_by_sample: dict[str, CrossSampleGroupMemberSubject] = {}
    claimed_source_ids: dict[str, set[str]] = defaultdict(set)
    for source_cluster_id in component:
        for cell in grouped_cells.get(source_cluster_id, ()):
            if cell.status != "duplicate_assigned":
                continue
            source_group_id = cell.claim_source_group_hypothesis_id
            winner_group_id = cell.claim_winner_group_hypothesis_id
            source_cluster = cluster_by_group_id.get(source_group_id)
            winner_cluster = cluster_by_group_id.get(winner_group_id)
            if (
                not source_group_id
                or not winner_group_id
                or source_cluster != source_cluster_id
                or winner_cluster is None
                or edge_facts.get(_ordered_pair(source_cluster, winner_cluster))
                is None
                or edge_facts[_ordered_pair(source_cluster, winner_cluster)].disposition
                != "eligible"
            ):
                return None
            source_member = _member_for_primary_sample(
                clusters_by_id[source_cluster],
                identity=identities_by_cluster[source_cluster],
                sample_stem=cell.sample_stem,
            )
            winner_member = _member_for_primary_sample(
                clusters_by_id[winner_cluster],
                identity=identities_by_cluster[winner_cluster],
                sample_stem=cell.sample_stem,
            )
            if source_member is None or winner_member is None:
                return None
            sample_id = source_member.canonical_sample_identity_id
            if winner_member.canonical_sample_identity_id != sample_id:
                return None
            existing_winner = winner_by_sample.get(sample_id)
            if existing_winner is not None and existing_winner != winner_member:
                return None
            winner_by_sample[sample_id] = winner_member
            claimed_source_ids[sample_id].add(source_member.member_subject_id)

    selected: list[CrossSampleGroupMemberSubject] = []
    for sample_id, candidates in members_by_sample.items():
        if len(candidates) == 1:
            selected.append(candidates[0])
            continue
        winner = winner_by_sample.get(sample_id)
        candidate_ids = {candidate.member_subject_id for candidate in candidates}
        if (
            winner is None
            or winner.member_subject_id not in candidate_ids
            or claimed_source_ids[sample_id]
            != candidate_ids.difference({winner.member_subject_id})
        ):
            return None
        selected.append(winner)
    return tuple(sorted(selected, key=lambda member: member.basis_bytes))


def _component_center(
    component: tuple[str, ...],
    *,
    clusters_by_id: dict[str, Any],
    selected_by_sample: dict[str, _Observation],
    winner_id: str,
) -> dict[str, float]:
    selected_cells = [observation.cell for observation in selected_by_sample.values()]
    selected_rts = [
        cell.apex_rt
        for cell in selected_cells
        if cell.apex_rt is not None and _finite(cell.apex_rt)
    ]
    return {
        "family_center_mz": median(
            _family_center_mz(clusters_by_id[cluster_id])
            for cluster_id in component
        ),
        "family_center_rt": (
            mean(selected_rts)
            if selected_rts
            else median(
                _family_center_rt(clusters_by_id[cluster_id])
                for cluster_id in component
            )
        ),
        "family_product_mz": _component_product_mz(
            component,
            clusters_by_id=clusters_by_id,
            winner_id=winner_id,
        ),
        "family_observed_neutral_loss_da": _component_observed_loss(
            component,
            clusters_by_id=clusters_by_id,
            winner_id=winner_id,
        ),
    }


def _component_product_mz(
    component: tuple[str, ...],
    *,
    clusters_by_id: dict[str, Any],
    winner_id: str,
) -> float:
    if len(_component_neutral_loss_tags(component, clusters_by_id)) > 1:
        return _family_product_mz(clusters_by_id[winner_id])
    return median(
        _family_product_mz(clusters_by_id[cluster_id])
        for cluster_id in component
    )


def _component_observed_loss(
    component: tuple[str, ...],
    *,
    clusters_by_id: dict[str, Any],
    winner_id: str,
) -> float:
    if len(_component_neutral_loss_tags(component, clusters_by_id)) > 1:
        return _family_observed_loss(clusters_by_id[winner_id])
    return median(
        _family_observed_loss(clusters_by_id[cluster_id])
        for cluster_id in component
    )


def _winner_cells(
    winner_id: str,
    *,
    grouped_cells: dict[str, tuple[AlignedCell, ...]],
    selected_by_sample: dict[str, _Observation],
    sample_order: tuple[str, ...],
    center_rt: float,
    winner_group_hypothesis_id: str,
) -> tuple[AlignedCell, ...]:
    current_by_sample = {
        cell.sample_stem: cell for cell in grouped_cells.get(winner_id, ())
    }
    cells: list[AlignedCell] = []
    for sample_stem in sample_order:
        observation = selected_by_sample.get(sample_stem)
        if observation is not None:
            cells.append(
                _merged_observation_cell(
                    observation,
                    winner_id,
                    center_rt,
                    winner_group_hypothesis_id=winner_group_hypothesis_id,
                )
            )
            continue
        current = current_by_sample.get(sample_stem)
        if current is not None:
            cells.append(_with_cluster_and_rt_delta(current, winner_id, center_rt))
    return tuple(cells)


def _primary_cluster(
    cluster: Any,
    *,
    component: tuple[str, ...],
    clusters_by_id: dict[str, Any],
    center: dict[str, float],
    successor: CrossSampleGroupIdentity,
    lineage: tuple[CrossSampleLineageEdge, ...],
) -> Any:
    event_cluster_ids = tuple(
        event_id
        for cluster_id in component
        for event_id in _event_cluster_ids(clusters_by_id[cluster_id])
    )
    evidence = (
        f"owner_complete_link;owner_count={max(2, len(event_cluster_ids))};"
        f"primary_family_consolidated;family_count={len(component)}"
        f"{_shared_ms1_peak_nl_evidence(component, clusters_by_id)}"
    )
    updates = {
        **center,
        "group_hypothesis_id": successor.group_hypothesis_id,
        "public_family_id": row_id(cluster),
        "processing_request_id": successor.processing_request_id,
        "member_subjects": successor.member_subjects,
        "identity_basis_bytes": successor.basis_bytes,
        "disposition": successor.disposition,
        "lineage": _accumulated_lineage(component, clusters_by_id, lineage),
        "cross_sample_lineage": _accumulated_lineage(
            component,
            clusters_by_id,
            lineage,
        ),
        "consolidation_state": "primary_winner",
        "cross_sample_state": "active",
        "consolidation_winner_group_hypothesis_id": successor.group_hypothesis_id,
        "consolidation_source_group_hypothesis_id": ";".join(
            cross_sample_group_identity_from_carrier(
                clusters_by_id[cluster_id]
            ).group_hypothesis_id
            for cluster_id in component
        ),
        "has_anchor": any(
            bool(getattr(clusters_by_id[cluster_id], "has_anchor", False))
            for cluster_id in component
        ),
        "event_cluster_ids": event_cluster_ids,
        "event_member_count": sum(
            _event_member_count(clusters_by_id[cluster_id]) for cluster_id in component
        ),
        "evidence": evidence,
        "review_only": False,
    }
    updates.update(
        _successor_owner_updates(
            component,
            clusters_by_id=clusters_by_id,
            successor=successor,
        )
    )
    return _clone_cluster(
        cluster,
        updates,
    )


def _accumulated_lineage(
    component: tuple[str, ...],
    clusters_by_id: dict[str, Any],
    lineage: tuple[CrossSampleLineageEdge, ...],
) -> tuple[CrossSampleLineageEdge, ...]:
    return tuple(
        sorted(
            {
                *(
                    edge
                    for cluster_id in component
                    for edge in getattr(
                        clusters_by_id[cluster_id],
                        "cross_sample_lineage",
                        getattr(clusters_by_id[cluster_id], "lineage", ()),
                    )
                ),
                *lineage,
            },
            key=lambda edge: (
                edge.predecessor_group_hypothesis_id,
                edge.successor_group_hypothesis_id,
            ),
        )
    )


def _shared_ms1_peak_nl_evidence(
    component: tuple[str, ...],
    clusters_by_id: dict[str, Any],
) -> str:
    tags = _component_neutral_loss_tags(component, clusters_by_id)
    if len(tags) < 2:
        return ""
    return f";shared_ms1_peak_nl_tags={','.join(tags)}"


def _component_neutral_loss_tags(
    component: tuple[str, ...],
    clusters_by_id: dict[str, Any],
) -> tuple[str, ...]:
    tags = sorted(
        str(getattr(clusters_by_id[cluster_id], "neutral_loss_tag", ""))
        for cluster_id in component
    )
    return tuple(tag for tag in dict.fromkeys(tags) if tag)


def _successor_owner_updates(
    component: tuple[str, ...],
    *,
    clusters_by_id: dict[str, Any],
    successor: CrossSampleGroupIdentity,
) -> dict[str, tuple[Any, ...]]:
    component_clusters = tuple(clusters_by_id[cluster_id] for cluster_id in component)
    if not all(hasattr(cluster, "owners") for cluster in component_clusters):
        raise ValueError("primary consolidation requires canonical owner projections")
    successor_member_ids = {
        member.member_subject_id for member in successor.member_subjects
    }
    owners = tuple(
        owner
        for cluster in component_clusters
        for owner in tuple(getattr(cluster, "owners"))
        if getattr(owner, "cross_sample_member_subject", None) is not None
        and owner.cross_sample_member_subject.member_subject_id in successor_member_ids
    )
    return {
        "owners": validate_cross_sample_successor_owner_projection(
            successor=successor,
            owners=owners,
        )
    }


def _loser_cluster(
    cluster: Any,
    *,
    winner_id: str,
    winner_group_hypothesis_id: str,
) -> Any:
    identity = cross_sample_group_identity_from_carrier(cluster)
    demoted = apply_group_disposition(identity, disposition="unresolved")
    evidence = _family_evidence(cluster)
    suffix = f"primary_family_consolidation_loser;winner={winner_id}"
    return _clone_cluster(
        cluster,
        {
            "evidence": f"{evidence};{suffix}" if evidence else suffix,
            "review_only": True,
            "processing_request_id": "",
            "member_subjects": (),
            "identity_basis_bytes": b"",
            "group_hypothesis_id": "",
            "cross_sample_lineage": (),
            "lineage": (),
            "disposition": demoted.disposition,
            "cross_sample_state": "render_only",
            "consolidation_state": "primary_loser",
            "consolidation_winner_group_hypothesis_id": (
                winner_group_hypothesis_id
            ),
            "consolidation_source_group_hypothesis_id": "",
        },
    )


def _disposition_only_cluster(cluster: Any) -> Any:
    """Project a review demotion without removing canonical membership."""

    identity = cross_sample_group_identity_from_carrier(cluster)
    demoted = apply_group_disposition(identity, disposition="unresolved")
    return _clone_cluster(
        cluster,
        {
            "review_only": True,
            "disposition": demoted.disposition,
            "cross_sample_state": "active",
            "consolidation_state": "not_consolidated",
            "consolidation_winner_group_hypothesis_id": "",
            "consolidation_source_group_hypothesis_id": "",
        },
    )


def _merged_observation_cell(
    observation: _Observation,
    winner_id: str,
    center_rt: float,
    *,
    winner_group_hypothesis_id: str,
) -> AlignedCell:
    cell = _with_cluster_and_rt_delta(observation.cell, winner_id, center_rt)
    reason = (
        "primary family consolidation; "
        f"source_family={observation.source_cluster_id}; "
        f"original_status={observation.original_status}; "
        f"source_reason={observation.cell.reason}"
    )
    return replace(
        cell,
        status=observation.original_status,  # type: ignore[arg-type]
        reason=reason,
        group_hypothesis_id=winner_group_hypothesis_id,
        public_family_id=winner_id,
        consolidation_state=(
            "primary_winner"
            if observation.source_cluster_id == winner_id
            else "moved_to_primary_winner"
        ),
        consolidation_winner_group_hypothesis_id=winner_group_hypothesis_id,
        consolidation_source_group_hypothesis_id=(
            observation.cell.group_hypothesis_id
        ),
    )


def _render_only_loser_cell(cell: AlignedCell) -> AlignedCell:
    return replace(
        cell,
        group_hypothesis_id="",
        claim_winner_group_hypothesis_id="",
        claim_source_group_hypothesis_id="",
        consolidation_state="primary_loser",
        consolidation_winner_group_hypothesis_id="",
        consolidation_source_group_hypothesis_id="",
    )


def _with_cluster_and_rt_delta(
    cell: AlignedCell,
    cluster_id: str,
    center_rt: float,
) -> AlignedCell:
    rt_delta_sec = (
        (cell.apex_rt - center_rt) * 60.0
        if cell.apex_rt is not None and _finite(cell.apex_rt)
        else None
    )
    return replace(cell, cluster_id=cluster_id, rt_delta_sec=rt_delta_sec)


def _observation(cell: AlignedCell) -> _Observation | None:
    original_status = _original_status(cell)
    if original_status not in _PRESENT_STATUSES:
        return None
    if not _valid_present_cell(cell):
        return None
    return _Observation(
        cell=cell,
        source_cluster_id=cell.cluster_id,
        original_status=original_status,
    )


def _observation_seed_rt(observations: list[_Observation]) -> float:
    detected = [
        observation.cell.apex_rt
        for observation in observations
        if observation.original_status == "detected"
        and observation.cell.apex_rt is not None
    ]
    if detected:
        return median(detected)
    return median(
        observation.cell.apex_rt
        for observation in observations
        if observation.cell.apex_rt is not None
    )


def _component_seed_rt(
    component: tuple[str, ...],
    grouped_cells: dict[str, tuple[AlignedCell, ...]],
    clusters_by_id: dict[str, Any],
) -> float:
    observations = [
        observation
        for cluster_id in component
        for cell in grouped_cells.get(cluster_id, ())
        for observation in (_observation(cell),)
        if observation is not None
    ]
    if observations:
        return _observation_seed_rt(observations)
    return median(
        _family_center_rt(clusters_by_id[cluster_id])
        for cluster_id in component
    )


def _observation_sort_key(
    observation: _Observation,
    center_rt: float,
) -> tuple[object, ...]:
    cell = observation.cell
    area = float(cell.area or 0.0)
    return (
        -area,
        0 if observation.original_status == "detected" else 1,
        abs((cell.apex_rt or center_rt) - center_rt),
        _trace_quality_rank(cell.trace_quality),
        _observation_group_sort_id(observation),
    )


def _selected_observation(
    observations: list[_Observation],
    center_rt: float,
) -> _Observation:
    same_apex_detected_projection = _same_apex_detected_projection(observations)
    if same_apex_detected_projection:
        return min(
            same_apex_detected_projection,
            key=lambda item: _detected_preference_observation_sort_key(
                item,
                center_rt,
            ),
        )
    return min(observations, key=lambda item: _observation_sort_key(item, center_rt))


def _same_apex_detected_projection(
    observations: list[_Observation],
) -> list[_Observation]:
    detected_apexes = [
        observation.cell.apex_rt
        for observation in observations
        if observation.original_status == "detected"
        and observation.cell.apex_rt is not None
        and _finite(observation.cell.apex_rt)
    ]
    if not detected_apexes:
        return []
    projected = [
        observation
        for observation in observations
        if observation.original_status != "detected"
        and observation.cell.apex_rt is not None
        and _finite(observation.cell.apex_rt)
        and any(
            abs(observation.cell.apex_rt - detected_apex) * 60.0
            <= _SAME_APEX_DETECTED_PREFERENCE_SEC
            for detected_apex in detected_apexes
        )
    ]
    if not projected:
        return []
    return [
        observation
        for observation in observations
        if observation.cell.apex_rt is not None
        and _finite(observation.cell.apex_rt)
        and any(
            abs(observation.cell.apex_rt - detected_apex) * 60.0
            <= _SAME_APEX_DETECTED_PREFERENCE_SEC
            for detected_apex in detected_apexes
        )
    ]


def _detected_preference_observation_sort_key(
    observation: _Observation,
    center_rt: float,
) -> tuple[object, ...]:
    cell = observation.cell
    area = float(cell.area or 0.0)
    return (
        0 if observation.original_status == "detected" else 1,
        -area,
        abs((cell.apex_rt or center_rt) - center_rt),
        _trace_quality_rank(cell.trace_quality),
        _observation_group_sort_id(observation),
    )


def _observation_group_sort_id(observation: _Observation) -> str:
    group_hypothesis_id = observation.cell.group_hypothesis_id
    if not group_hypothesis_id:
        raise ValueError(
            "primary consolidation requires canonical group_hypothesis_id "
            "for a scientific tie-break"
        )
    return group_hypothesis_id


def _current_present_count(cells: tuple[AlignedCell, ...]) -> int:
    return sum(1 for cell in cells if cell.status in _PRESENT_STATUSES)


def _current_detected_count(cells: tuple[AlignedCell, ...]) -> int:
    return sum(1 for cell in cells if cell.status == "detected")


def _family_stats(cells: tuple[AlignedCell, ...]) -> _FamilyStats:
    detected = tuple(cell for cell in cells if cell.status == "detected")
    rescued = tuple(cell for cell in cells if cell.status == "rescued")
    return _FamilyStats(
        detected_count=sum(1 for cell in detected if _valid_present_cell(cell)),
        rescue_count=sum(1 for cell in rescued if _valid_present_cell(cell)),
        duplicate_count=sum(1 for cell in cells if cell.status == "duplicate_assigned"),
        ambiguous_count=sum(
            1 for cell in cells if cell.status == "ambiguous_ms1_owner"
        ),
        total_area=sum(
            float(cell.area or 0.0) for cell in cells if _valid_present_cell(cell)
        ),
    )


def _valid_present_cell(cell: AlignedCell) -> bool:
    return (
        cell.area is not None
        and _finite(cell.area)
        and cell.area > 0
        and cell.apex_rt is not None
        and _finite(cell.apex_rt)
        and cell.peak_start_rt is not None
        and _finite(cell.peak_start_rt)
        and cell.peak_end_rt is not None
        and _finite(cell.peak_end_rt)
    )


def _clone_cluster(cluster: Any, updates: dict[str, Any]) -> Any:
    if is_dataclass(cluster) and not isinstance(cluster, type):
        allowed = {field.name for field in fields(cluster)}
        return replace(
            cluster,
            **{key: value for key, value in updates.items() if key in allowed},
        )
    if isinstance(cluster, SimpleNamespace):
        values = vars(cluster).copy()
        values.update(updates)
        return SimpleNamespace(**values)
    values = {
        key: getattr(cluster, key)
        for key in dir(cluster)
        if not key.startswith("_") and not callable(getattr(cluster, key))
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _original_status(cell: AlignedCell) -> str:
    if cell.status != "duplicate_assigned":
        return cell.status
    return cell.claim_source_status


def _trace_quality_rank(value: str) -> int:
    return {"clean": 0, "owner_exact_apex_match": 1, "weak": 2, "poor": 3}.get(
        value,
        4,
    )


def _family_center_mz(row: Any) -> float:
    return family_center_mz(row)


def _family_center_rt(row: Any) -> float:
    return family_center_rt(row)


def _family_product_mz(row: Any) -> float:
    return family_product_mz(row)


def _family_observed_loss(row: Any) -> float:
    return family_observed_loss(row)


def _event_cluster_ids(row: Any) -> tuple[str, ...]:
    if hasattr(row, "event_cluster_ids"):
        return tuple(row.event_cluster_ids)
    return (str(row.cluster_id), *tuple(row.folded_cluster_ids))


def _event_member_count(row: Any) -> int:
    if hasattr(row, "event_member_count"):
        return int(row.event_member_count)
    return len(row.members) + int(row.folded_member_count)


def _family_evidence(row: Any) -> str:
    if hasattr(row, "evidence"):
        return str(row.evidence)
    if hasattr(row, "fold_evidence"):
        return str(row.fold_evidence)
    return ""


def _ppm(left: float, right: float) -> float:
    return ppm(left, right)


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )
