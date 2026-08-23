from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from itertools import combinations
from statistics import mean, median

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleContractError,
    CrossSampleGroupIdentity,
    CrossSampleLineageEdge,
    CrossSampleNeighborhoodBudget,
    CrossSampleNeighborhoodEdge,
    CrossSamplePreConsolidationEdgeFacts,
    CrossSampleSuccessorApplication,
    apply_group_disposition,
    classify_cross_sample_pre_consolidation_edge,
    cross_sample_group_identity_from_carrier,
    mint_cross_sample_consolidation_partition,
    predecessor_membership_neighborhood_edges,
    validate_cross_sample_group_population,
    validate_cross_sample_successor_owner_projection,
)
from xic_extractor.alignment.matrix import (
    AlignedCell,
    AlignmentMatrix,
    AlignmentRowLike,
)
from xic_extractor.alignment.models import AlignmentCluster
from xic_extractor.alignment.output_rows import cells_by_cluster, row_id
from xic_extractor.alignment.owner_clustering import OwnerAlignedFeature

_MAX_BACKFILL_SEED_CENTERS = 2
_PRESENT_STATUSES = {"detected", "rescued"}
_PRE_BACKFILL_CONSOLIDATION_BUDGET = CrossSampleNeighborhoodBudget(
    max_members=64,
    max_eligible_edges=128,
)


def consolidate_pre_backfill_identity_families(
    features: tuple[OwnerAlignedFeature, ...],
    *,
    config: AlignmentConfig,
) -> tuple[OwnerAlignedFeature, ...]:
    active_features = _active_features(features)
    active_population = _active_population(active_features)
    if len(active_features) < 2:
        return features
    pair_edges = _pre_consolidation_pair_edges(active_features, config=config)
    components = _pre_consolidation_components(active_features, pair_edges)
    if not any(len(component) > 1 for component in components):
        return features

    replacements: dict[str, OwnerAlignedFeature] = {}
    for component in components:
        if len(component) == 1:
            continue
        partitions = _complete_link_partitions(component, pair_edges)
        if not any(len(partition) > 1 for partition in partitions):
            continue
        application = _component_successor(
            component,
            partitions=partitions,
            pair_edges=pair_edges,
            population=active_population,
        )
        if application.status == "unassessable":
            continue
        successor_by_members = {
            frozenset(
                member.member_subject_id for member in successor.member_subjects
            ): successor
            for successor in application.successors
        }
        for partition in partitions:
            if len(partition) == 1:
                continue
            winner = _winner_feature(partition)
            predecessor_members = frozenset(
                member.member_subject_id
                for feature in partition
                for member in cross_sample_group_identity_from_carrier(
                    feature
                ).member_subjects
            )
            successor = successor_by_members[predecessor_members]
            partition_lineage = tuple(
                edge
                for edge in application.lineage
                if edge.successor_group_hypothesis_id == successor.group_hypothesis_id
            )
            replacements[winner.feature_family_id] = _primary_feature(
                winner,
                partition,
                successor=successor,
                lineage=partition_lineage,
            )
            for feature in partition:
                if feature.feature_family_id != winner.feature_family_id:
                    replacements[feature.feature_family_id] = _loser_feature(
                        feature,
                        winner_id=winner.feature_family_id,
                        winner_group_hypothesis_id=successor.group_hypothesis_id,
                    )
    result = tuple(
        replacements.get(feature.feature_family_id, feature)
        for feature in features
    )
    _active_population(_active_features(result))
    return result


def recenter_pre_backfill_identity_families(
    matrix: AlignmentMatrix,
) -> AlignmentMatrix:
    grouped_cells = cells_by_cluster(matrix)
    centers_by_id: dict[str, float] = {}
    clusters: list[AlignmentCluster | AlignmentRowLike] = []
    for cluster in matrix.clusters:
        cluster_id = row_id(cluster)
        if (
            not isinstance(cluster, OwnerAlignedFeature)
            or not _is_pre_backfill_consolidated(cluster)
        ):
            clusters.append(cluster)
            continue
        present_rts = [
            cell.apex_rt
            for cell in grouped_cells.get(cluster_id, ())
            if cell.status in _PRESENT_STATUSES and cell.apex_rt is not None
        ]
        if not present_rts:
            clusters.append(cluster)
            continue
        center_rt = mean(present_rts)
        centers_by_id[cluster_id] = center_rt
        clusters.append(replace(cluster, family_center_rt=center_rt))
    if not centers_by_id:
        return matrix
    return AlignmentMatrix(
        clusters=tuple(clusters),
        cells=tuple(_recenter_cell(cell, centers_by_id) for cell in matrix.cells),
        sample_order=matrix.sample_order,
    )


def _complete_link_partitions(
    features: tuple[OwnerAlignedFeature, ...],
    pair_edges: dict[frozenset[str], CrossSampleNeighborhoodEdge],
) -> tuple[tuple[OwnerAlignedFeature, ...], ...]:
    groups: list[list[OwnerAlignedFeature]] = []
    ordered_features = tuple(sorted(features, key=_feature_sort_key))
    for feature in ordered_features:
        compatible_indexes = [
            index
            for index, group in enumerate(groups)
            if all(
                pair_edges[
                    frozenset(
                        (feature.feature_family_id, existing.feature_family_id),
                    )
                ].disposition
                == "eligible"
                for existing in group
            )
        ]
        if not compatible_indexes:
            groups.append([feature])
            continue
        best_index = min(
            compatible_indexes,
            key=lambda index: _group_match_score(feature, groups[index]),
        )
        groups[best_index].append(feature)
    return tuple(tuple(group) for group in groups)


def _primary_feature(
    winner: OwnerAlignedFeature,
    group: tuple[OwnerAlignedFeature, ...],
    *,
    successor: CrossSampleGroupIdentity,
    lineage: tuple[CrossSampleLineageEdge, ...],
) -> OwnerAlignedFeature:
    owners = tuple(owner for feature in group for owner in feature.owners)
    projected_owners = validate_cross_sample_successor_owner_projection(
        successor=successor,
        owners=owners,
    )
    family_count = len(group)
    evidence = (
        f"owner_complete_link;owner_count={len(owners)};"
        f"pre_backfill_identity_consolidated;family_count={family_count}"
    )
    return replace(
        winner,
        family_center_mz=median(feature.family_center_mz for feature in group),
        family_center_rt=median(feature.family_center_rt for feature in group),
        family_product_mz=median(feature.family_product_mz for feature in group),
        family_observed_neutral_loss_da=median(
            feature.family_observed_neutral_loss_da for feature in group
        ),
        has_anchor=any(feature.has_anchor for feature in group),
        owners=projected_owners,
        evidence=evidence,
        identity_conflict=False,
        review_only=False,
        confirm_local_owners_with_backfill=True,
        backfill_seed_centers=_seed_centers(group),
        ambiguous_sample_stem=None,
        ambiguous_candidate_ids=(),
        group_hypothesis_id=successor.group_hypothesis_id,
        public_family_id=winner.public_family_id,
        processing_request_id=successor.processing_request_id,
        member_subjects=successor.member_subjects,
        identity_basis_bytes=successor.basis_bytes,
        disposition=successor.disposition,
        cross_sample_lineage=_accumulated_lineage(group, lineage),
        group_construction_role="pre_backfill_successor_adapter",
        group_delivery_role="pre_backfill_successor_adapter",
        group_membership_source="pre_backfill_cross_sample_peak_group_projection",
        consolidation_state="primary_winner",
        cross_sample_state="active",
        consolidation_winner_group_hypothesis_id=successor.group_hypothesis_id,
        consolidation_source_group_hypothesis_id=";".join(
            cross_sample_group_identity_from_carrier(feature).group_hypothesis_id
            for feature in group
        ),
    )


def _loser_feature(
    feature: OwnerAlignedFeature,
    *,
    winner_id: str,
    winner_group_hypothesis_id: str | None = None,
) -> OwnerAlignedFeature:
    identity = cross_sample_group_identity_from_carrier(feature)
    demoted = apply_group_disposition(identity, disposition="unresolved")
    suffix = f"pre_backfill_identity_consolidation_loser;winner={winner_id}"
    evidence = f"{feature.evidence};{suffix}" if feature.evidence else suffix
    return replace(
        feature,
        evidence=evidence,
        review_only=True,
        group_hypothesis_id="",
        processing_request_id="",
        member_subjects=(),
        identity_basis_bytes=b"",
        cross_sample_lineage=(),
        disposition=demoted.disposition,
        group_delivery_role="pre_backfill_successor_adapter",
        group_membership_source="pre_backfill_cross_sample_peak_group_projection",
        cross_sample_state="render_only",
        consolidation_state="primary_loser",
        consolidation_winner_group_hypothesis_id=(
            winner_group_hypothesis_id or winner_id
        ),
        consolidation_source_group_hypothesis_id="",
    )


def _active_features(
    features: tuple[OwnerAlignedFeature, ...],
) -> tuple[OwnerAlignedFeature, ...]:
    active: list[OwnerAlignedFeature] = []
    for feature in features:
        if feature.cross_sample_state == "active":
            active.append(feature)
        elif feature.cross_sample_state != "render_only":
            raise CrossSampleContractError("pre-backfill carrier state is not closed")
    return tuple(active)


def _active_population(
    features: tuple[OwnerAlignedFeature, ...],
) -> tuple[CrossSampleGroupIdentity, ...]:
    population = tuple(
        cross_sample_group_identity_from_carrier(feature) for feature in features
    )
    validate_cross_sample_group_population(population)
    return population


def _pre_consolidation_pair_edges(
    features: tuple[OwnerAlignedFeature, ...],
    *,
    config: AlignmentConfig,
) -> dict[frozenset[str], CrossSampleNeighborhoodEdge]:
    edges: dict[frozenset[str], CrossSampleNeighborhoodEdge] = {}
    for left, right in combinations(features, 2):
        left_identity = cross_sample_group_identity_from_carrier(left)
        right_identity = cross_sample_group_identity_from_carrier(right)
        edges[frozenset((left.feature_family_id, right.feature_family_id))] = (
            classify_cross_sample_pre_consolidation_edge(
                CrossSamplePreConsolidationEdgeFacts(
                    left_group=left_identity,
                    right_group=right_identity,
                    left_neutral_loss_tag=left.neutral_loss_tag,
                    right_neutral_loss_tag=right.neutral_loss_tag,
                    left_precursor_mz=left.family_center_mz,
                    right_precursor_mz=right.family_center_mz,
                    left_product_mz=left.family_product_mz,
                    right_product_mz=right.family_product_mz,
                    left_observed_loss_da=left.family_observed_neutral_loss_da,
                    right_observed_loss_da=right.family_observed_neutral_loss_da,
                    left_rt_min=left.family_center_rt,
                    right_rt_min=right.family_center_rt,
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
        )
    return edges


def _pre_consolidation_components(
    features: tuple[OwnerAlignedFeature, ...],
    pair_edges: dict[frozenset[str], CrossSampleNeighborhoodEdge],
) -> tuple[tuple[OwnerAlignedFeature, ...], ...]:
    by_id = {feature.feature_family_id: feature for feature in features}
    neighbors: dict[str, set[str]] = {feature_id: set() for feature_id in by_id}
    for pair, edge in pair_edges.items():
        if edge.disposition != "eligible":
            continue
        left_id, right_id = tuple(pair)
        neighbors[left_id].add(right_id)
        neighbors[right_id].add(left_id)
    components: list[tuple[OwnerAlignedFeature, ...]] = []
    visited: set[str] = set()
    for start in sorted(
        by_id,
        key=lambda feature_id: _feature_sort_key(by_id[feature_id]),
    ):
        if start in visited:
            continue
        queue = [start]
        visited.add(start)
        component_ids: list[str] = []
        while queue:
            current = queue.pop()
            component_ids.append(current)
            for neighbor in sorted(neighbors[current]):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        components.append(
            tuple(
                sorted(
                    (by_id[item] for item in component_ids),
                    key=_feature_sort_key,
                )
            )
        )
    return tuple(components)


def _component_successor(
    component: tuple[OwnerAlignedFeature, ...],
    *,
    partitions: tuple[tuple[OwnerAlignedFeature, ...], ...],
    pair_edges: dict[frozenset[str], CrossSampleNeighborhoodEdge],
    population: tuple[CrossSampleGroupIdentity, ...],
) -> CrossSampleSuccessorApplication:
    predecessors = tuple(
        cross_sample_group_identity_from_carrier(feature) for feature in component
    )
    edges = list(predecessor_membership_neighborhood_edges(predecessors))
    component_ids = {feature.feature_family_id for feature in component}
    edges.extend(
        edge
        for pair, edge in pair_edges.items()
        if pair <= component_ids
    )
    return mint_cross_sample_consolidation_partition(
        predecessors=predecessors,
        successor_member_sets=tuple(
            tuple(
                member
                for feature in partition
                for member in cross_sample_group_identity_from_carrier(
                    feature
                ).member_subjects
            )
            for partition in partitions
        ),
        edges=tuple(edges),
        budget=_PRE_BACKFILL_CONSOLIDATION_BUDGET,
        population=population,
    )


def _accumulated_lineage(
    group: tuple[OwnerAlignedFeature, ...],
    lineage: tuple[CrossSampleLineageEdge, ...],
) -> tuple[CrossSampleLineageEdge, ...]:
    return tuple(
        sorted(
            {
                *(
                    edge
                    for feature in group
                    for edge in feature.cross_sample_lineage
                ),
                *lineage,
            },
            key=lambda edge: (
                edge.predecessor_group_hypothesis_id,
                edge.successor_group_hypothesis_id,
            ),
        )
    )


def _winner_feature(
    group: tuple[OwnerAlignedFeature, ...],
) -> OwnerAlignedFeature:
    center_rt = median(feature.family_center_rt for feature in group)
    return min(
        group,
        key=lambda feature: (
            -len(feature.owners),
            0 if feature.has_anchor else 1,
            abs(feature.family_center_rt - center_rt),
            _canonical_group_sort_id(feature),
        ),
    )


def _seed_centers(
    group: tuple[OwnerAlignedFeature, ...],
) -> tuple[tuple[float, float], ...]:
    ordered = tuple(
        sorted(
            {
                (feature.family_center_mz, feature.family_center_rt)
                for feature in group
            },
            key=lambda item: (item[1], item[0]),
        )
    )
    if len(ordered) <= _MAX_BACKFILL_SEED_CENTERS:
        return ordered
    indexes = {0, len(ordered) - 1}
    return tuple(ordered[index] for index in sorted(indexes))


def _is_pre_backfill_consolidated(feature: object) -> bool:
    evidence = getattr(feature, "evidence", getattr(feature, "fold_evidence", ""))
    return "pre_backfill_identity_consolidated" in str(evidence).split(";")


def _recenter_cell(
    cell: AlignedCell,
    centers_by_id: dict[str, float],
) -> AlignedCell:
    center_rt = centers_by_id.get(cell.cluster_id)
    if center_rt is None or cell.apex_rt is None:
        return cell
    return replace(cell, rt_delta_sec=(cell.apex_rt - center_rt) * 60.0)


def _group_match_score(
    feature: OwnerAlignedFeature,
    group: Iterable[OwnerAlignedFeature],
) -> tuple[float, float, str]:
    members = tuple(group)
    return (
        min(
            _ppm(feature.family_center_mz, member.family_center_mz)
            for member in members
        ),
        min(
            abs(feature.family_center_rt - member.family_center_rt)
            for member in members
        ),
        _canonical_group_sort_id(members[0]),
    )


def _feature_sort_key(feature: OwnerAlignedFeature) -> tuple[object, ...]:
    return (
        feature.neutral_loss_tag,
        feature.family_center_mz,
        feature.family_center_rt,
        feature.family_product_mz,
        feature.family_observed_neutral_loss_da,
        _canonical_group_sort_id(feature),
    )


def _canonical_group_sort_id(feature: OwnerAlignedFeature) -> str:
    return cross_sample_group_identity_from_carrier(feature).group_hypothesis_id


def _ppm(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), 1e-12) * 1_000_000.0
