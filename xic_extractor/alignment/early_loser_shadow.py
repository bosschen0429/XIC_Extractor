from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignmentMatrix
from xic_extractor.alignment.output_rows import row_id
from xic_extractor.alignment.owner_backfill_request_plan import (
    build_owner_backfill_request_plan,
)
from xic_extractor.alignment.owner_clustering import OwnerAlignedFeature
from xic_extractor.alignment.owner_group_delivery import (
    OwnerGroupDeliveryFeatures,
    delivery_consolidation_state,
    delivery_consolidation_winner_group_hypothesis_id,
    delivery_group_hypothesis_id,
)
from xic_extractor.alignment.pre_backfill_consolidation import (
    consolidate_pre_backfill_identity_families,
)

EARLY_LOSER_SHADOW_SCHEMA_VERSION = "alignment_early_loser_shadow_v1"
EARLY_LOSER_SHADOW_BINDING_VERSION = "alignment_early_loser_shadow_binding_v1"
_SOURCE_STAGE = "pre_backfill_identity_consolidation"

ShadowProposalStatus = Literal["survivor", "loser", "unresolved"]
ShadowComparisonStatus = Literal[
    "confirmed_loser",
    "false_loser",
    "winner_mismatch",
    "retained_survivor",
    "late_loser",
    "unresolved",
]
ShadowVerdict = Literal["GO", "KILL", "REDESIGN"]


@dataclass(frozen=True)
class EarlyLoserShadowWorkload:
    feature_count: int
    requested_feature_count: int
    requested_cell_count: int
    logical_xic_request_count: int
    evidence_kind: str = "owner_backfill_request_plan_projection"


@dataclass(frozen=True)
class EarlyLoserShadowProposalRow:
    source_feature_family_id: str
    source_group_hypothesis_id: str
    proposal_status: ShadowProposalStatus
    proposal_reason: str
    source_stage: str
    direct_winner_group_hypothesis_id: str
    terminal_winner_group_hypothesis_id: str
    lineage: tuple[str, ...]


@dataclass(frozen=True)
class EarlyLoserShadowProposal:
    run_id: str
    source_owner_snapshot_sha256: str
    alignment_config_sha256: str
    request_universe_sha256: str
    rows: tuple[EarlyLoserShadowProposalRow, ...]
    baseline_workload: EarlyLoserShadowWorkload
    proposed_workload: EarlyLoserShadowWorkload


@dataclass(frozen=True)
class EarlyLoserShadowComparisonRow:
    source_feature_family_id: str
    source_group_hypothesis_id: str
    proposal_status: ShadowProposalStatus
    proposal_reason: str
    proposal_source_stage: str
    proposal_direct_winner_group_hypothesis_id: str
    proposal_terminal_winner_group_hypothesis_id: str
    proposal_lineage: tuple[str, ...]
    final_consolidation_state: str
    final_direct_winner_group_hypothesis_id: str
    final_terminal_winner_group_hypothesis_id: str
    final_lineage: tuple[str, ...]
    comparison_status: ShadowComparisonStatus
    comparison_reason: str


@dataclass(frozen=True)
class EarlyLoserExpectedDiffSurface:
    surface: str
    observed_shadow_diff_status: str
    observed_shadow_diff_count: int | None
    counterfactual_activation_status: str
    counterfactual_activation_diff_count: int | None
    reason: str


@dataclass(frozen=True)
class EarlyLoserShadowReport:
    schema_version: str
    binding_schema_version: str
    run_id: str
    source_owner_snapshot_sha256: str
    alignment_config_sha256: str
    request_universe_sha256: str
    final_authority_projection_sha256: str
    durable_binding_scope: str
    backfill_scope: str
    validation_status: str
    authority: str
    verdict: ShadowVerdict
    verdict_reason: str
    activation_allowed: bool
    activation_gap_reasons: tuple[str, ...]
    baseline_workload: EarlyLoserShadowWorkload
    proposed_workload: EarlyLoserShadowWorkload
    projected_feature_reduction: int
    projected_cell_reduction: int
    projected_logical_xic_request_reduction: int
    actual_savings_claim: str
    proposed_survivor_count: int
    proposed_loser_count: int
    proposed_unresolved_count: int
    confirmed_loser_count: int
    false_loser_count: int
    winner_mismatch_count: int
    late_loser_count: int
    final_authority_comparison_count: int
    expected_diff_surfaces: tuple[EarlyLoserExpectedDiffSurface, ...]
    rows: tuple[EarlyLoserShadowComparisonRow, ...]
    rollback: str


def build_early_loser_shadow_proposal(
    features: tuple[OwnerAlignedFeature, ...],
    *,
    run_id: str,
    sample_order: tuple[str, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
) -> EarlyLoserShadowProposal:
    if not run_id.strip():
        raise ValueError("early-loser shadow run_id must be non-empty")
    source_by_group = _features_by_group(features, label="source")
    shadow_features = consolidate_pre_backfill_identity_families(
        features,
        config=alignment_config,
    )
    shadow_by_group = _features_by_group(shadow_features, label="shadow")
    if set(source_by_group) != set(shadow_by_group):
        raise ValueError("early-loser shadow changed the owner feature universe")

    proposal_lineages = {
        group_id: _resolve_lineage(group_id, shadow_by_group)
        for group_id in shadow_by_group
    }
    rows: list[EarlyLoserShadowProposalRow] = []
    for group_id in sorted(source_by_group):
        source = source_by_group[group_id]
        proposed = shadow_by_group[group_id]
        state = delivery_consolidation_state(proposed)
        lineage = proposal_lineages[group_id]
        if bool(getattr(source, "review_only", False)):
            status: ShadowProposalStatus = "unresolved"
            reason = "source_owner_feature_is_review_only"
        elif bool(getattr(source, "identity_conflict", False)):
            status = "unresolved"
            reason = "source_owner_feature_has_identity_conflict"
        elif state == "primary_loser":
            status = "loser"
            reason = "owner_pre_backfill_identity_consolidation_loser"
        else:
            status = "survivor"
            reason = "retained_by_owner_pre_backfill_identity_consolidation"
        rows.append(
            EarlyLoserShadowProposalRow(
                source_feature_family_id=(
                    str(getattr(source, "feature_family_id", ""))
                    or row_id(source)
                ),
                source_group_hypothesis_id=group_id,
                proposal_status=status,
                proposal_reason=reason,
                source_stage=_SOURCE_STAGE,
                direct_winner_group_hypothesis_id=_direct_winner(
                    group_id,
                    proposed,
                ),
                terminal_winner_group_hypothesis_id=lineage[-1],
                lineage=lineage,
            )
        )
    return EarlyLoserShadowProposal(
        run_id=run_id,
        source_owner_snapshot_sha256=_canonical_sha256(
            [
                asdict(feature)
                for feature in sorted(
                    features,
                    key=delivery_group_hypothesis_id,
                )
            ]
        ),
        alignment_config_sha256=_canonical_sha256(asdict(alignment_config)),
        request_universe_sha256=_canonical_sha256(
            {
                "sample_order": sample_order,
                "raw_sample_stems": sorted(raw_sample_stems),
            }
        ),
        rows=tuple(rows),
        baseline_workload=_workload(
            features,
            sample_order=sample_order,
            raw_sample_stems=raw_sample_stems,
            alignment_config=alignment_config,
        ),
        proposed_workload=_workload(
            shadow_features,
            sample_order=sample_order,
            raw_sample_stems=raw_sample_stems,
            alignment_config=alignment_config,
        ),
    )


def compare_early_loser_shadow_with_final_authority(
    proposal: EarlyLoserShadowProposal,
    final_matrix: AlignmentMatrix,
) -> EarlyLoserShadowReport:
    final_by_group = _features_by_group(final_matrix.clusters, label="final")
    final_lineages = {
        group_id: _resolve_lineage(group_id, final_by_group)
        for group_id in final_by_group
    }
    rows: list[EarlyLoserShadowComparisonRow] = []
    for proposal_row in proposal.rows:
        group_id = proposal_row.source_group_hypothesis_id
        final_feature = final_by_group.get(group_id)
        if final_feature is None:
            raise ValueError(
                "early-loser final authority is missing source group "
                f"{group_id!r}"
            )
        final_lineage = final_lineages[group_id]
        final_state = delivery_consolidation_state(final_feature)
        comparison_status, comparison_reason = _comparison(
            proposal_row,
            final_lineage=final_lineage,
            final_lineages=final_lineages,
        )
        rows.append(
            EarlyLoserShadowComparisonRow(
                source_feature_family_id=proposal_row.source_feature_family_id,
                source_group_hypothesis_id=group_id,
                proposal_status=proposal_row.proposal_status,
                proposal_reason=proposal_row.proposal_reason,
                proposal_source_stage=proposal_row.source_stage,
                proposal_direct_winner_group_hypothesis_id=(
                    proposal_row.direct_winner_group_hypothesis_id
                ),
                proposal_terminal_winner_group_hypothesis_id=(
                    proposal_row.terminal_winner_group_hypothesis_id
                ),
                proposal_lineage=proposal_row.lineage,
                final_consolidation_state=final_state,
                final_direct_winner_group_hypothesis_id=_direct_winner(
                    group_id,
                    final_feature,
                ),
                final_terminal_winner_group_hypothesis_id=final_lineage[-1],
                final_lineage=final_lineage,
                comparison_status=comparison_status,
                comparison_reason=comparison_reason,
            )
        )

    ordered_rows = tuple(sorted(rows, key=lambda item: item.source_group_hypothesis_id))
    counts = _comparison_counts(ordered_rows)
    baseline = proposal.baseline_workload
    proposed = proposal.proposed_workload
    projected_feature_reduction = (
        baseline.requested_feature_count - proposed.requested_feature_count
    )
    projected_cell_reduction = (
        baseline.requested_cell_count - proposed.requested_cell_count
    )
    projected_logical_xic_request_reduction = (
        baseline.logical_xic_request_count - proposed.logical_xic_request_count
    )
    verdict, verdict_reason = _verdict(
        counts,
        projected_feature_reduction=projected_feature_reduction,
        projected_cell_reduction=projected_cell_reduction,
        projected_logical_xic_request_reduction=(
            projected_logical_xic_request_reduction
        ),
    )
    activation_gaps = _activation_gap_reasons(counts)
    return EarlyLoserShadowReport(
        schema_version=EARLY_LOSER_SHADOW_SCHEMA_VERSION,
        binding_schema_version=EARLY_LOSER_SHADOW_BINDING_VERSION,
        run_id=proposal.run_id,
        source_owner_snapshot_sha256=proposal.source_owner_snapshot_sha256,
        alignment_config_sha256=proposal.alignment_config_sha256,
        request_universe_sha256=proposal.request_universe_sha256,
        final_authority_projection_sha256=_canonical_sha256(
            [asdict(row) for row in ordered_rows]
        ),
        durable_binding_scope=(
            "run+typed_source_owner_snapshot+alignment_config+request_universe+"
            "final_consolidation_comparison_projection;"
            "does_not_bind_matrix_cells_or_values"
        ),
        backfill_scope="full-audit",
        validation_status="diagnostic_only",
        authority=(
            "diagnostic_projection_only_final_claim_and_primary_consolidation_"
            "remain_authoritative"
        ),
        verdict=verdict,
        verdict_reason=verdict_reason,
        activation_allowed=False,
        activation_gap_reasons=activation_gaps,
        baseline_workload=baseline,
        proposed_workload=proposed,
        projected_feature_reduction=projected_feature_reduction,
        projected_cell_reduction=projected_cell_reduction,
        projected_logical_xic_request_reduction=(
            projected_logical_xic_request_reduction
        ),
        actual_savings_claim=(
            "unavailable: projected owner workload counts are not elapsed-time, "
            "RAW-call, chromatogram-call, or point savings"
        ),
        proposed_survivor_count=counts["survivor"],
        proposed_loser_count=counts["loser"],
        proposed_unresolved_count=counts["unresolved"],
        confirmed_loser_count=counts["confirmed_loser"],
        false_loser_count=counts["false_loser"],
        winner_mismatch_count=counts["winner_mismatch"],
        late_loser_count=counts["late_loser"],
        final_authority_comparison_count=len(ordered_rows),
        expected_diff_surfaces=_expected_diff_surfaces(),
        rows=ordered_rows,
        rollback=(
            "disable the opt-in shadow sidecar; unchanged owner features continue "
            "through Backfill and final settlement"
        ),
    )


def _workload(
    features: OwnerGroupDeliveryFeatures,
    *,
    sample_order: tuple[str, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
) -> EarlyLoserShadowWorkload:
    plan = build_owner_backfill_request_plan(
        features,
        sample_order=sample_order,
        raw_sample_stems=raw_sample_stems,
        alignment_config=alignment_config,
    )
    items = tuple(
        item
        for sample_stem in sample_order
        for item in plan.requests_for_sample(sample_stem)
    )
    requested_features = {
        delivery_group_hypothesis_id(item.feature) for item in items
    }
    requested_cells = {
        (delivery_group_hypothesis_id(item.feature), item.sample_stem)
        for item in items
    }
    return EarlyLoserShadowWorkload(
        feature_count=len(features),
        requested_feature_count=len(requested_features),
        requested_cell_count=len(requested_cells),
        logical_xic_request_count=len(items),
    )


def _features_by_group(features, *, label: str) -> dict[str, object]:
    by_group: dict[str, object] = {}
    for feature in features:
        group_id = delivery_group_hypothesis_id(feature).strip()
        if not group_id:
            raise ValueError(f"{label} owner feature has a blank group identity")
        if group_id in by_group:
            raise ValueError(f"{label} owner feature repeats group {group_id!r}")
        by_group[group_id] = feature
    return by_group


def _resolve_lineage(
    source_group_id: str,
    features_by_group: dict[str, object],
) -> tuple[str, ...]:
    lineage: list[str] = []
    visited: set[str] = set()
    current = source_group_id
    source_state = delivery_consolidation_state(features_by_group[current])
    while True:
        if current in visited:
            raise ValueError(
                "consolidation lineage contains a cycle: "
                + " -> ".join((*lineage, current))
            )
        visited.add(current)
        lineage.append(current)
        feature = features_by_group.get(current)
        if feature is None:
            raise ValueError(
                f"consolidation lineage references missing group {current!r}"
            )
        state = delivery_consolidation_state(feature)
        if state == "primary_loser":
            winner = delivery_consolidation_winner_group_hypothesis_id(feature)
            if not winner:
                raise ValueError(
                    f"primary_loser {current!r} has no winner group identity"
                )
            if winner not in features_by_group:
                raise ValueError(
                    f"primary_loser {current!r} references missing winner {winner!r}"
                )
            current = winner
            continue
        if state == "primary_winner":
            winner = delivery_consolidation_winner_group_hypothesis_id(feature)
            if winner != current:
                raise ValueError(
                    f"primary_winner {current!r} must self-reference its winner"
                )
            return tuple(lineage)
        if state == "not_consolidated":
            if source_state == "primary_loser":
                raise ValueError(
                    f"primary_loser {source_group_id!r} terminates at "
                    f"non-winner {current!r}"
                )
            return tuple(lineage)
        raise ValueError(
            f"group {current!r} has unknown consolidation state {state!r}"
        )


def _direct_winner(group_id: str, feature: object) -> str:
    if delivery_consolidation_state(feature) == "primary_loser":
        return delivery_consolidation_winner_group_hypothesis_id(feature)
    return group_id


def _comparison(
    proposal_row: EarlyLoserShadowProposalRow,
    *,
    final_lineage: tuple[str, ...],
    final_lineages: dict[str, tuple[str, ...]],
) -> tuple[ShadowComparisonStatus, str]:
    if proposal_row.proposal_status == "unresolved":
        return "unresolved", "pre-Backfill owner left the source unresolved"
    if proposal_row.proposal_status == "survivor":
        if len(final_lineage) > 1:
            return (
                "late_loser",
                "final authority settled this retained shadow survivor later",
            )
        return "retained_survivor", "final authority retained the shadow survivor"

    if len(final_lineage) == 1:
        return "false_loser", "final authority retained the proposed loser"
    proposed_terminal = proposal_row.terminal_winner_group_hypothesis_id
    proposed_final_lineage = final_lineages.get(proposed_terminal)
    if proposed_final_lineage is None:
        raise ValueError(
            "final authority is missing proposed terminal winner "
            f"{proposed_terminal!r}"
        )
    if final_lineage[-1] != proposed_final_lineage[-1]:
        return (
            "winner_mismatch",
            "proposed and final lineages terminate at different winners",
        )
    return (
        "confirmed_loser",
        "final authority confirms the loser and terminal winner; direct hops may "
        "differ across legal consolidation stages",
    )


def _comparison_counts(
    rows: tuple[EarlyLoserShadowComparisonRow, ...],
) -> dict[str, int]:
    counts = {
        "survivor": 0,
        "loser": 0,
        "unresolved": 0,
        "confirmed_loser": 0,
        "false_loser": 0,
        "winner_mismatch": 0,
        "late_loser": 0,
    }
    for row in rows:
        counts[row.proposal_status] += 1
        if row.comparison_status in {
            "confirmed_loser",
            "false_loser",
            "winner_mismatch",
            "late_loser",
        }:
            counts[row.comparison_status] += 1
    return counts


def _verdict(
    counts: dict[str, int],
    *,
    projected_feature_reduction: int,
    projected_cell_reduction: int,
    projected_logical_xic_request_reduction: int,
) -> tuple[ShadowVerdict, str]:
    if counts["false_loser"] or counts["winner_mismatch"]:
        return (
            "KILL",
            "at least one proposed loser disagrees with final owner authority",
        )
    if counts["loser"] == 0:
        return (
            "REDESIGN",
            "the existing owner proposal found no loser decision value",
        )
    if (
        projected_feature_reduction <= 0
        or projected_cell_reduction <= 0
        or projected_logical_xic_request_reduction <= 0
    ):
        return (
            "KILL",
            "the owner proposal has insufficient positive feature, cell, or "
            "logical XIC request decision value",
        )
    return (
        "GO",
        "owner-valid loser lineage closes for shadow evaluation only; activation "
        "remains blocked by the expected-diff gaps",
    )


def _activation_gap_reasons(counts: dict[str, int]) -> tuple[str, ...]:
    gaps = [
        "counterfactual selected observations are unavailable before Backfill",
        "counterfactual missing/extra cells, status/reason, and areas are unavailable",
        "counterfactual Matrix identity/value and review population are unavailable",
        "actual elapsed-time, RAW-call, chromatogram-call, and point savings "
        "are unavailable",
    ]
    if counts["late_loser"]:
        gaps.append(
            f"{counts['late_loser']} final losers were not knowable from the current "
            "pre-Backfill owner proposal"
        )
    if counts["unresolved"]:
        gaps.append(
            f"{counts['unresolved']} source groups remain owner-unresolved "
            "before Backfill"
        )
    return tuple(gaps)


def _expected_diff_surfaces() -> tuple[EarlyLoserExpectedDiffSurface, ...]:
    owner_comparison = {
        "survivors_losers",
        "winner_identity",
    }
    unavailable = {
        "selected_observations",
        "missing_extra_cells",
        "status_reason",
        "areas",
        "matrix_identity",
        "matrix_value",
        "review_population",
    }
    surfaces: list[EarlyLoserExpectedDiffSurface] = []
    for surface in (
        "survivors_losers",
        "winner_identity",
        "selected_observations",
        "missing_extra_cells",
        "status_reason",
        "areas",
        "matrix_identity",
        "matrix_value",
        "review_population",
        "rollback",
    ):
        if surface in owner_comparison:
            counterfactual_status = "owner_comparison_available"
            reason = "typed proposal is compared with typed final authority"
        elif surface in unavailable:
            counterfactual_status = "unavailable_activation_blocker"
            reason = (
                "the no-write shadow does not execute a counterfactual survivor-only "
                "Backfill path"
            )
        else:
            counterfactual_status = "available"
            reason = "rollback is removal of the opt-in diagnostic sidecar"
        surfaces.append(
            EarlyLoserExpectedDiffSurface(
                surface=surface,
                observed_shadow_diff_status="hard_zero_no_write",
                observed_shadow_diff_count=0,
                counterfactual_activation_status=counterfactual_status,
                counterfactual_activation_diff_count=None,
                reason=reason,
            )
        )
    return tuple(surfaces)


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256((payload + "\n").encode("utf-8")).hexdigest()
