from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Any, Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleClaimEdgeFacts,
    CrossSampleContractError,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    classify_cross_sample_claim_edge,
    cross_sample_group_identity_from_carrier,
    validate_cross_sample_group_population,
)
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.output_rows import cells_by_cluster, count_status, row_id


@dataclass(frozen=True)
class _ClaimCandidate:
    index: int
    cell: AlignedCell
    cluster: Any
    cluster_id: str
    family_mz: float
    detected_count: int
    event_member_count: int
    event_cluster_count: int
    review_only: bool
    group_identity: CrossSampleGroupIdentity
    member_subject: CrossSampleGroupMemberSubject

    @property
    def group_hypothesis_id(self) -> str:
        return self.group_identity.group_hypothesis_id


@dataclass
class _ClaimGroup:
    candidates: list[_ClaimCandidate]
    min_mz: float
    max_mz: float
    winner_sort_key: tuple[object, ...] | None = None


@dataclass(frozen=True)
class _ClusterClaimStats:
    detected_count: int
    event_member_count: int
    event_cluster_count: int


def apply_ms1_peak_claim_registry(
    matrix: AlignmentMatrix,
    config: AlignmentConfig,
) -> AlignmentMatrix:
    cluster_by_id = {row_id(cluster): cluster for cluster in matrix.clusters}
    grouped_cells = cells_by_cluster(matrix)
    active_identities_by_cluster: dict[str, CrossSampleGroupIdentity] = {}
    for cluster_id, cluster in cluster_by_id.items():
        state = getattr(cluster, "cross_sample_state", "active")
        if state == "render_only":
            continue
        if state != "active":
            raise CrossSampleContractError("claim carrier state is not closed")
        active_identities_by_cluster[cluster_id] = (
            cross_sample_group_identity_from_carrier(cluster)
        )
    validate_cross_sample_group_population(
        tuple(active_identities_by_cluster.values())
    )
    stats_by_cluster: dict[str, _ClusterClaimStats] = {}
    candidates_by_sample: dict[str, list[_ClaimCandidate]] = {}
    for index, cell in enumerate(matrix.cells):
        if cell.status not in {"detected", "rescued"}:
            continue
        cell_cluster = cluster_by_id.get(cell.cluster_id)
        if cell_cluster is None or not _cell_has_finite_peak_claim(cell):
            continue
        group_identity = active_identities_by_cluster.get(cell.cluster_id)
        if group_identity is None:
            continue
        try:
            member_subject = _member_for_claim_sample(
                cell_cluster,
                group_identity=group_identity,
                sample_stem=cell.sample_stem,
            )
        except CrossSampleContractError:
            if _is_explicit_value_only_no_owner_repair(cell, cell_cluster):
                continue
            raise
        family_mz = _family_center_mz(cell_cluster)
        if not _finite(family_mz):
            continue
        stats = stats_by_cluster.get(cell.cluster_id)
        if stats is None:
            cluster_cells = grouped_cells.get(cell.cluster_id, ())
            stats = _ClusterClaimStats(
                detected_count=count_status(cluster_cells, "detected"),
                event_member_count=_event_member_count(cell_cluster),
                event_cluster_count=len(_event_cluster_ids(cell_cluster)),
            )
            stats_by_cluster[cell.cluster_id] = stats
        candidates_by_sample.setdefault(cell.sample_stem, []).append(
            _ClaimCandidate(
                index=index,
                cell=cell,
                cluster=cell_cluster,
                cluster_id=cell.cluster_id,
                family_mz=family_mz,
                detected_count=stats.detected_count,
                event_member_count=stats.event_member_count,
                event_cluster_count=stats.event_cluster_count,
                review_only=bool(getattr(cell_cluster, "review_only", False)),
                group_identity=group_identity,
                member_subject=member_subject,
            ),
        )

    replacements: dict[int, AlignedCell] = {}
    for candidates in candidates_by_sample.values():
        if len(candidates) < 2:
            continue
        exact_candidate_indexes = _exact_peak_candidate_indexes(candidates)
        exact_replacements = _exact_peak_replacements(candidates, config)
        replacements.update(exact_replacements)
        fuzzy_candidates = [
            candidate
            for candidate in candidates
            if candidate.index not in exact_replacements
            and candidate.index not in exact_candidate_indexes
        ]
        for group in _claim_groups(fuzzy_candidates, config):
            if len(group.candidates) < 2:
                continue
            replacements.update(_duplicate_replacements(group.candidates))

    if not replacements:
        return matrix
    return AlignmentMatrix(
        clusters=matrix.clusters,
        cells=tuple(
            replacements.get(index, cell)
            for index, cell in enumerate(matrix.cells)
        ),
        sample_order=matrix.sample_order,
    )


def _is_explicit_value_only_no_owner_repair(
    cell: AlignedCell,
    cluster: object,
) -> bool:
    """Recognize the one closed no-local-owner repair projection."""

    if (
        cell.status != "rescued"
        or cell.gap_fill_state != "gap_fill_rescued"
        or cell.missing_observation_state != "queried_and_detected"
    ):
        return False
    return not any(
        getattr(owner, "sample_stem", None) == cell.sample_stem
        for owner in getattr(cluster, "owners", ())
    )


def _exact_peak_replacements(
    candidates: list[_ClaimCandidate],
    config: AlignmentConfig,
) -> dict[int, AlignedCell]:
    replacements: dict[int, AlignedCell] = {}
    for group in _exact_peak_groups(candidates):
        anchor = group[0]
        if not all(
            _claim_edge_is_eligible(
                anchor,
                candidate,
                config,
                comparison_kind="exact_candidate",
            )
            for candidate in group[1:]
        ):
            continue
        replacements.update(_duplicate_replacements(group))
    return replacements


def _exact_peak_candidate_indexes(
    candidates: list[_ClaimCandidate],
) -> set[int]:
    """Reserve exact-key candidates for physical-identity classification only."""

    return {
        candidate.index
        for group in _exact_peak_groups(candidates)
        for candidate in group
    }


def _exact_peak_groups(
    candidates: list[_ClaimCandidate],
) -> tuple[list[_ClaimCandidate], ...]:
    """Partition exact geometry keys before CrossSample classifies them."""

    keyed: dict[tuple[float, float, float, float], list[_ClaimCandidate]] = (
        defaultdict(list)
    )
    for candidate in candidates:
        keyed[_exact_peak_key(candidate.cell)].append(candidate)
    return tuple(group for group in keyed.values() if len(group) > 1)


def _exact_peak_key(cell: AlignedCell) -> tuple[float, float, float, float]:
    assert cell.apex_rt is not None
    assert cell.peak_start_rt is not None
    assert cell.peak_end_rt is not None
    assert cell.area is not None
    return (
        round(cell.apex_rt, 8),
        round(cell.peak_start_rt, 8),
        round(cell.peak_end_rt, 8),
        round(cell.area, 6),
    )


def _claim_groups(
    candidates: list[_ClaimCandidate],
    config: AlignmentConfig,
) -> tuple[_ClaimGroup, ...]:
    groups: list[_ClaimGroup] = []
    active: list[_ClaimGroup] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            item.family_mz,
            item.cell.apex_rt,
            _candidate_group_hypothesis_id(item),
        ),
    ):
        active = [
            group
            for group in active
            if not _mz_window_is_past(candidate.family_mz, group.max_mz, config)
        ]
        compatible_groups = [
            group
            for group in active
            if all(
                _claim_edge_is_eligible(candidate, existing, config)
                for existing in group.candidates
            )
        ]
        if compatible_groups:
            group = min(compatible_groups, key=_group_sort_key)
            _add_candidate_to_group(group, candidate)
            continue
        group = _ClaimGroup(
            candidates=[candidate],
            min_mz=candidate.family_mz,
            max_mz=candidate.family_mz,
        )
        groups.append(group)
        active.append(group)
    return tuple(groups)


def _duplicate_replacements(
    candidates: list[_ClaimCandidate],
) -> dict[int, AlignedCell]:
    production_candidates = [
        candidate for candidate in candidates if not candidate.review_only
    ]
    if production_candidates:
        winner = min(production_candidates, key=_winner_sort_key)
        return {
            candidate.index: _duplicate_cell(
                candidate.cell,
                winner.cluster_id,
                winner_group_hypothesis_id=winner.group_hypothesis_id,
                source_group_hypothesis_id=candidate.group_hypothesis_id,
            )
            for candidate in candidates
            if candidate.index != winner.index
        }
    return {
        candidate.index: _review_only_duplicate_cell(
            candidate.cell,
            source_group_hypothesis_id=candidate.group_hypothesis_id,
        )
        for candidate in candidates
    }


def _claim_edge_is_eligible(
    left: _ClaimCandidate,
    right: _ClaimCandidate,
    config: AlignmentConfig,
    *,
    comparison_kind: Literal["numeric_candidate", "exact_candidate"] = (
        "numeric_candidate"
    ),
) -> bool:
    return classify_cross_sample_claim_edge(
        CrossSampleClaimEdgeFacts(
            left_group=left.group_identity,
            right_group=right.group_identity,
            left_member_subject=left.member_subject,
            right_member_subject=right.member_subject,
            left_family_mz=left.family_mz,
            right_family_mz=right.family_mz,
            left_apex_rt=_required_peak_value(left.cell.apex_rt, field="apex_rt"),
            right_apex_rt=_required_peak_value(right.cell.apex_rt, field="apex_rt"),
            left_peak_start_rt=_required_peak_value(
                left.cell.peak_start_rt,
                field="peak_start_rt",
            ),
            left_peak_end_rt=_required_peak_value(
                left.cell.peak_end_rt,
                field="peak_end_rt",
            ),
            right_peak_start_rt=_required_peak_value(
                right.cell.peak_start_rt,
                field="peak_start_rt",
            ),
            right_peak_end_rt=_required_peak_value(
                right.cell.peak_end_rt,
                field="peak_end_rt",
            ),
            context=(
                "repair_derived_observation"
                if "rescued" in {left.cell.status, right.cell.status}
                else "claim_candidate"
            ),
            comparison_kind=comparison_kind,
            left_neutral_loss_tag=str(
                getattr(left.cluster, "neutral_loss_tag", "")
            ),
            right_neutral_loss_tag=str(
                getattr(right.cluster, "neutral_loss_tag", "")
            ),
            left_targeted_istd_anchor=getattr(
                left.cluster,
                "targeted_istd_anchor",
                None,
            ),
            right_targeted_istd_anchor=getattr(
                right.cluster,
                "targeted_istd_anchor",
                None,
            ),
        ),
        config=config,
    ) == "eligible"


def _required_peak_value(value: float | None, *, field: str) -> float:
    if value is None:
        raise ValueError(f"claim candidate {field} is required")
    return value


def _winner_sort_key(candidate: _ClaimCandidate) -> tuple[object, ...]:
    return (
        1 if candidate.review_only else 0,
        -candidate.detected_count,
        -candidate.event_member_count,
        -candidate.event_cluster_count,
        0 if candidate.cell.status == "detected" else 1,
        abs(candidate.cell.rt_delta_sec)
        if candidate.cell.rt_delta_sec is not None
        else math.inf,
        _candidate_group_hypothesis_id(candidate),
        candidate.cell.sample_stem,
    )


def _group_sort_key(group: _ClaimGroup) -> tuple[object, ...]:
    if group.winner_sort_key is None:
        group.winner_sort_key = min(
            (_winner_sort_key(candidate) for candidate in group.candidates),
        )
    return (*group.winner_sort_key, group.min_mz, group.max_mz)


def _candidate_group_hypothesis_id(candidate: _ClaimCandidate) -> str:
    return candidate.group_hypothesis_id


def _member_for_claim_sample(
    cluster: Any,
    *,
    group_identity: CrossSampleGroupIdentity,
    sample_stem: str,
) -> CrossSampleGroupMemberSubject:
    members = tuple(
        owner.cross_sample_member_subject
        for owner in getattr(cluster, "owners", ())
        if owner.sample_stem == sample_stem
        and owner.cross_sample_member_subject is not None
    )
    if len(members) != 1:
        raise CrossSampleContractError(
            "claim candidate has no exact owner-backed member for its sample"
        )
    member = members[0]
    if member not in group_identity.member_subjects:
        raise CrossSampleContractError(
            "claim candidate owner member is outside canonical group identity"
        )
    return member


def _add_candidate_to_group(
    group: _ClaimGroup,
    candidate: _ClaimCandidate,
) -> None:
    group.candidates.append(candidate)
    group.min_mz = min(group.min_mz, candidate.family_mz)
    group.max_mz = max(group.max_mz, candidate.family_mz)
    if group.winner_sort_key is not None:
        group.winner_sort_key = min(
            group.winner_sort_key,
            _winner_sort_key(candidate),
        )


def _duplicate_cell(
    cell: AlignedCell,
    winner_id: str,
    *,
    winner_group_hypothesis_id: str,
    source_group_hypothesis_id: str,
) -> AlignedCell:
    return replace(
        cell,
        status="duplicate_assigned",
        reason=(
            "duplicate MS1 peak claim; "
            f"winner={winner_id}; original_status={cell.status}"
        ),
        gap_fill_state="not_filled",
        gap_fill_reason="not_requested_duplicate_loser",
        missing_observation_state="duplicate_claim_loser",
        group_claim_state="duplicate_loser",
        claim_source_status=cell.status,
        claim_winner_group_hypothesis_id=winner_group_hypothesis_id,
        claim_source_group_hypothesis_id=source_group_hypothesis_id,
    )


def _review_only_duplicate_cell(
    cell: AlignedCell,
    *,
    source_group_hypothesis_id: str,
) -> AlignedCell:
    return replace(
        cell,
        status="duplicate_assigned",
        reason=(
            "review-only MS1 peak claim; "
            f"winner=none; original_status={cell.status}"
        ),
        gap_fill_state="not_filled",
        gap_fill_reason="not_requested_duplicate_loser",
        missing_observation_state="duplicate_claim_loser",
        group_claim_state="review_only_duplicate_loser",
        claim_source_status=cell.status,
        claim_winner_group_hypothesis_id="",
        claim_source_group_hypothesis_id=source_group_hypothesis_id,
    )


def _cell_has_finite_peak_claim(cell: AlignedCell) -> bool:
    return all(
        _finite(value)
        for value in (
            cell.area,
            cell.apex_rt,
            cell.peak_start_rt,
            cell.peak_end_rt,
        )
    )


def _apex_delta_sec(left: AlignedCell, right: AlignedCell) -> float:
    assert left.apex_rt is not None
    assert right.apex_rt is not None
    return abs(left.apex_rt - right.apex_rt) * 60.0


def _window_overlap_fraction(left: AlignedCell, right: AlignedCell) -> float:
    assert left.peak_start_rt is not None
    assert left.peak_end_rt is not None
    assert right.peak_start_rt is not None
    assert right.peak_end_rt is not None
    left_width = left.peak_end_rt - left.peak_start_rt
    right_width = right.peak_end_rt - right.peak_start_rt
    if left_width <= 0 or right_width <= 0:
        return 0.0
    overlap = min(left.peak_end_rt, right.peak_end_rt) - max(
        left.peak_start_rt,
        right.peak_start_rt,
    )
    if overlap <= 0:
        return 0.0
    return overlap / min(left_width, right_width)


def _mz_window_is_past(
    candidate_mz: float,
    group_max_mz: float,
    config: AlignmentConfig,
) -> bool:
    return candidate_mz > group_max_mz and _ppm(group_max_mz, candidate_mz) > (
        config.duplicate_fold_ppm
    )


def _family_center_mz(row: Any) -> float:
    if hasattr(row, "family_center_mz"):
        return float(row.family_center_mz)
    return float(row.cluster_center_mz)


def _event_cluster_ids(row: Any) -> tuple[str, ...]:
    if hasattr(row, "event_cluster_ids"):
        return tuple(row.event_cluster_ids)
    return (str(row.cluster_id), *tuple(row.folded_cluster_ids))


def _event_member_count(row: Any) -> int:
    if hasattr(row, "event_member_count"):
        return int(row.event_member_count)
    return len(row.members) + int(row.folded_member_count)


def _ppm(left: float, right: float) -> float:
    denominator = max(abs(left), 1e-12)
    return abs(left - right) / denominator * 1_000_000.0


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )
