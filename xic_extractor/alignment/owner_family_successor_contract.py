"""Owner-family successor contract for cross-sample group identity.

This module documents and verifies the compatibility bridge from the legacy
``OwnerAlignedFeature`` facade to ``CrossSamplePeakGroupHypothesis``.  In this
alignment context, "family" means a cross-sample chemical-entity group/display
label, not discovery's per-sample peak anchor.

Use ``group_hypothesis_id`` for pipeline identity decisions.
``public_family_id`` and legacy ``feature_family_id`` surfaces remain display
labels for output compatibility.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupEdgeFact,
    CrossSamplePeakGroupHardGateChallengeFact,
    CrossSamplePeakGroupReviewFact,
    cross_sample_peak_group_hypothesis_from_owner_feature,
)
from xic_extractor.alignment.edge_scoring import OwnerEdgeEvidence
from xic_extractor.alignment.owner_clustering import OwnerAlignedFeature

OwnerFamilyInvariant: TypeAlias = Literal[
    "stable_cross_sample_family_membership",
    "owner_edge_evidence_projection",
    "bounded_grouping_edge_semantics",
    "hard_family_split_gates",
    "review_only_owner_records",
    "backfill_seed_and_matrix_delivery",
]
OwnerFamilyInvariantDisposition: TypeAlias = Literal[
    "successor_owned",
    "successor_gap",
    "active_policy",
    "internal_constructor_candidate",
    "compatibility_adapter_candidate",
    "retirement_candidate_after_parity",
]
OwnerClusteringDisposition: TypeAlias = Literal[
    "keep_as_stage",
    "internal_constructor_candidate",
    "compatibility_adapter_candidate",
    "retirement_candidate_after_parity",
]

OWNER_FAMILY_INVARIANTS: tuple[OwnerFamilyInvariant, ...] = (
    "stable_cross_sample_family_membership",
    "owner_edge_evidence_projection",
    "bounded_grouping_edge_semantics",
    "hard_family_split_gates",
    "review_only_owner_records",
    "backfill_seed_and_matrix_delivery",
)
OWNER_CLUSTERING_GROUP_MIGRATION_FINAL_DISPOSITION: OwnerClusteringDisposition = (
    "compatibility_adapter_candidate"
)
OWNER_CLUSTERING_GROUP_MIGRATION_REASON = (
    "Owner-family migration disposition is compatibility_adapter_candidate: "
    "successor "
    "cross-sample peak group construction owns complete-link by default plus "
    "full-batch continuity recovery, hard split gates, review-only "
    "construction records, and "
    "delivery metadata; owner_group_delivery.OwnerGroupDeliveryFeature now "
    "defines the successor-owned backfill/matrix/process delivery shape, while "
    "owner_clustering.py preserves the public OwnerAlignedFeature facade and "
    "concrete dataclass adapter."
)
OWNER_CLUSTERING_GROUP_MIGRATION_EXIT_RULE = (
    "Do not retire owner_clustering.py or replace OwnerAlignedFeature until "
    "pre-backfill consolidation, diagnostic probes, public adapter tests, and "
    "any remaining concrete-dataclass consumers accept the successor contract "
    "or an explicit delivery adapter directly, with "
    "public parity proven for alignment_matrix.tsv, alignment_cells.tsv, "
    "alignment_review.tsv, and owner_edge_evidence.tsv when emitted."
)


@dataclass(frozen=True)
class OwnerFamilyInvariantMapping:
    invariant: OwnerFamilyInvariant
    current_owner: str
    current_state: str
    successor_surface: str
    disposition: OwnerFamilyInvariantDisposition
    public_oracle: str
    exit_rule: str


@dataclass(frozen=True)
class OwnerClusteringDispositionDecision:
    disposition: OwnerClusteringDisposition
    reason: str
    blocking_invariants: tuple[OwnerFamilyInvariant, ...]
    exit_rule: str


def owner_family_successor_mapping(
    feature: OwnerAlignedFeature,
    *,
    edge_evidence: Sequence[OwnerEdgeEvidence] | None = None,
) -> tuple[OwnerFamilyInvariantMapping, ...]:
    """Project current cross-sample group semantics onto the successor contract.

    This is intentionally a read-only migration guard. It does not replace
    ``cluster_sample_local_owners`` or participate in matrix construction, and
    it should be updated once the successor cross-sample group spine owns these
    invariants.  ``feature_family_id`` remains a compatibility display label;
    identity decisions should use ``group_hypothesis_id``.
    """

    peak_group = cross_sample_peak_group_hypothesis_from_owner_feature(
        feature,
        edge_evidence=edge_evidence or (),
    )
    projected_edge_facts = peak_group.edge_facts
    review_facts = peak_group.review_facts
    hard_gate_challenge_facts = peak_group.hard_gate_challenge_facts
    review_state = _review_fact_state(feature, review_facts)
    return (
        OwnerFamilyInvariantMapping(
            invariant="stable_cross_sample_family_membership",
            current_owner="OwnerAlignedFeature.feature_family_id / owners",
            current_state=(
                f"group_hypothesis_id={peak_group.group_hypothesis_id};"
                f"public_family_id={peak_group.public_family_id};"
                f"owner_count={len(peak_group.owner_ids)};"
                f"owner_ids={';'.join(peak_group.owner_ids)};"
                f"event_ids={';'.join(peak_group.event_ids)};"
                f"event_member_count={peak_group.event_member_count};"
                f"source={peak_group.source}"
            ),
            successor_surface="CrossSamplePeakGroupHypothesis shadow projection",
            disposition="successor_owned",
            public_oracle=(
                "tests/test_alignment_owner_family_successor_contract.py::"
                "test_cross_sample_peak_group_hypothesis_projects_owner_membership"
            ),
            exit_rule=(
                "Cross-sample group identity owns membership parity. Keep "
                "owner_clustering.py active until bounded grouping semantics, "
                "hard split gates, review-only records, and backfill/matrix "
                "delivery are successor-owned with alignment_matrix.tsv, "
                "alignment_cells.tsv, and alignment_review.tsv parity."
            ),
        ),
        OwnerFamilyInvariantMapping(
            invariant="owner_edge_evidence_projection",
            current_owner=(
                "owner_clustering.cluster_sample_local_owners / "
                "edge_scoring.evaluate_owner_edge / edge_evidence_sink"
            ),
            current_state=_edge_fact_state(feature, projected_edge_facts),
            successor_surface=(
                "CrossSamplePeakGroupEdgeFact shadow projection"
                if projected_edge_facts
                else "successor_constructor_no_edge_required"
            ),
            disposition=(
                "successor_owned"
                if projected_edge_facts or len(feature.owners) < 2
                else "active_policy"
            ),
            public_oracle=(
                "tests/test_alignment_owner_family_successor_contract.py::"
                "test_successor_constructor_matches_owner_adapter_delivery_fields"
                if not projected_edge_facts
                else (
                    "tests/test_alignment_owner_family_successor_contract.py::"
                    "test_strong_owner_edge_projects_support_fact_and_marks_successor_owned"
                )
            ),
            exit_rule=(
                "Project owner edge evidence into successor-visible facts "
                "before using it as migration evidence."
                if not projected_edge_facts and len(feature.owners) >= 2
                else "Cross-sample group evidence owns projection of current "
                "owner edge facts. Bounded grouping policy, hard gates, "
                "review-only owner records, and backfill/matrix delivery keep "
                "their existing live dispositions until later parity proves "
                "migration."
            ),
        ),
        OwnerFamilyInvariantMapping(
            invariant="bounded_grouping_edge_semantics",
            current_owner=(
                "cross_sample_peak_groups."
                "construct_cross_sample_peak_group_hypotheses / "
                "complete-link default with full-batch continuity recovery"
            ),
            current_state=(
                f"family_evidence={feature.evidence};"
                f"shadow_edge_fact_count={len(projected_edge_facts)}"
            ),
            successor_surface=("CrossSamplePeakGroupHypothesis successor constructor"),
            disposition="successor_owned",
            public_oracle=(
                "tests/test_alignment_owner_family_successor_contract.py::"
                "test_successor_constructor_matches_owner_adapter_delivery_fields"
            ),
            exit_rule=(
                "The successor constructor owns bounded cross-sample membership "
                "construction, but owner_clustering.py remains the public "
                "OwnerAlignedFeature adapter until concrete adapter consumers "
                "migrate."
            ),
        ),
        OwnerFamilyInvariantMapping(
            invariant="hard_family_split_gates",
            current_owner=(
                "cross_sample_peak_groups.construct_cross_sample_peak_group_hypotheses"
            ),
            current_state=_hard_gate_challenge_state(
                feature,
                hard_gate_challenge_facts,
            ),
            successor_surface=(
                "CrossSamplePeakGroupHypothesis successor constructor"
                if hard_gate_challenge_facts
                else "successor_family_split_policy"
            ),
            disposition="successor_owned",
            public_oracle=(
                "tests/test_alignment_owner_family_successor_contract.py::"
                "test_successor_constructor_enforces_hard_split_gates"
            ),
            exit_rule=(
                "The successor constructor owns same-sample, neutral-loss, "
                "precursor, product, and observed-loss split gates. "
                "owner_clustering.py remains an adapter until concrete "
                "delivery consumers no longer require OwnerAlignedFeature."
            ),
        ),
        OwnerFamilyInvariantMapping(
            invariant="review_only_owner_records",
            current_owner=(
                "OwnerAlignedFeature.review_only / identity_conflict / "
                "ambiguous owner fields"
            ),
            current_state=review_state,
            successor_surface=(
                "CrossSamplePeakGroupReviewFact shadow projection"
                if review_facts
                else "successor_constructor_review_policy"
            ),
            disposition=(
                "successor_owned"
                if review_facts or not _feature_has_review_only_state(feature)
                else "active_policy"
            ),
            public_oracle=(
                "tests/test_alignment_owner_family_successor_contract.py::"
                "test_successor_constructor_preserves_review_only_records"
                if not review_facts
                else (
                    "tests/test_alignment_owner_family_successor_contract.py::"
                    "test_identity_conflict_review_only_feature_projects_review_challenge_fact"
                )
            ),
            exit_rule=(
                "Successor projection must carry review-only families and "
                "ambiguous cells without contaminating production matrix rows."
                if not review_facts
                else "Cross-sample group evidence owns the review-only fact projection "
                "for this feature. Keep owner_clustering.py active until "
                "bounded grouping construction, hard split gates, and "
                "backfill/matrix delivery are also successor-owned with "
                "public writer parity."
            ),
        ),
        OwnerFamilyInvariantMapping(
            invariant="backfill_seed_and_matrix_delivery",
            current_owner=(
                "CrossSamplePeakGroupHypothesis delivery metadata adapted to "
                "OwnerGroupDeliveryFeature.family_center_* / "
                "backfill_seed_centers / confirm_local_owners_with_backfill / "
                "group_hypothesis_id / gap_fill_state"
            ),
            current_state=(
                f"seed_center_count={len(feature.backfill_seed_centers)};"
                f"confirm_local_owners_with_backfill="
                f"{feature.confirm_local_owners_with_backfill};"
                f"group_hypothesis_id={peak_group.group_hypothesis_id};"
                f"public_family_id={peak_group.public_family_id}"
            ),
            successor_surface=(
                "CrossSamplePeakGroupHypothesis delivery metadata plus "
                "OwnerGroupDeliveryFeature structural delivery contract"
            ),
            disposition="successor_owned",
            public_oracle=(
                "tests/test_alignment_owner_family_successor_contract.py::"
                "test_owner_group_migration_is_adapter_candidate_after_parity"
            ),
            exit_rule=(
                "Owner-backfill, owner-matrix, process payloads, cells, review, "
                "and workbook audit surfaces expose successor group and "
                "gap-fill projection. Keep OwnerAlignedFeature only as the "
                "public concrete facade until external callers migrate."
            ),
        ),
    )


def owner_clustering_disposition(
    mappings: tuple[OwnerFamilyInvariantMapping, ...],
) -> OwnerClusteringDispositionDecision:
    """Return the final owner-clustering migration disposition.

    This migration evaluator only promotes to adapter when no cross-sample
    group invariant remains an active owner-clustering policy or a successor
    gap. Retirement still requires concrete adapter consumers to stop depending
    on ``OwnerAlignedFeature``.
    """

    blocking = tuple(
        mapping.invariant
        for mapping in mappings
        if mapping.disposition in {"successor_gap", "active_policy"}
    )
    if blocking:
        return OwnerClusteringDispositionDecision(
            disposition="keep_as_stage",
            reason=(
                "owner_clustering.py remains keep_as_stage because at least "
                "one owner-family invariant is still active policy or a "
                "successor gap."
            ),
            blocking_invariants=blocking,
            exit_rule=OWNER_CLUSTERING_GROUP_MIGRATION_EXIT_RULE,
        )
    return OwnerClusteringDispositionDecision(
        disposition=OWNER_CLUSTERING_GROUP_MIGRATION_FINAL_DISPOSITION,
        reason=OWNER_CLUSTERING_GROUP_MIGRATION_REASON,
        blocking_invariants=blocking,
        exit_rule=OWNER_CLUSTERING_GROUP_MIGRATION_EXIT_RULE,
    )


def _feature_has_review_only_state(feature: OwnerAlignedFeature) -> bool:
    return (
        feature.review_only
        or feature.identity_conflict
        or feature.ambiguous_sample_stem is not None
        or bool(feature.ambiguous_candidate_ids)
    )


def _review_fact_state(
    feature: OwnerAlignedFeature,
    review_facts: tuple[CrossSamplePeakGroupReviewFact, ...],
) -> str:
    if not review_facts:
        state = (
            "review_only_present"
            if _feature_has_review_only_state(feature)
            else "review_only_supported_by_stage"
        )
        return f"{state};review_fact_count=0"

    identity_conflict_count = sum(1 for fact in review_facts if fact.identity_conflict)
    ambiguous_owner_count = sum(
        1 for fact in review_facts if fact.reason == "ambiguous_owner"
    )
    candidate_details = ";".join(
        (f"{fact.ambiguous_sample_stem or ''}:{','.join(fact.ambiguous_candidate_ids)}")
        for fact in review_facts
        if fact.ambiguous_sample_stem is not None or fact.ambiguous_candidate_ids
    )
    evidence = ";".join(fact.evidence for fact in review_facts)
    return (
        f"review_fact_count={len(review_facts)};"
        f"identity_conflict={identity_conflict_count};"
        f"ambiguous_owner={ambiguous_owner_count};"
        f"evidence={evidence};"
        f"ambiguous_candidates={candidate_details}"
    )


def _hard_gate_challenge_state(
    feature: OwnerAlignedFeature,
    hard_gate_challenge_facts: tuple[CrossSamplePeakGroupHardGateChallengeFact, ...],
) -> str:
    base = (
        f"neutral_loss_tag={feature.neutral_loss_tag};"
        f"product_mz={feature.family_product_mz};"
        f"observed_loss={feature.family_observed_neutral_loss_da};"
        f"hard_gate_challenge_fact_count={len(hard_gate_challenge_facts)}"
    )
    if not hard_gate_challenge_facts:
        return base

    challenges = ";".join(
        (
            f"{fact.owner_pair_ids[0]}|{fact.owner_pair_ids[1]}:"
            f"{fact.failure_reason or 'none'}:{fact.reason}"
        )
        for fact in hard_gate_challenge_facts
    )
    return f"{base};hard_gate_challenges={challenges}"


def _edge_fact_state(
    feature: OwnerAlignedFeature,
    edge_facts: tuple[CrossSamplePeakGroupEdgeFact, ...],
) -> str:
    if not edge_facts:
        return f"family_evidence={feature.evidence};edge_fact_count=0"

    support_count = sum(1 for fact in edge_facts if fact.role == "membership_support")
    challenge_count = sum(
        1 for fact in edge_facts if fact.role == "membership_challenge"
    )
    blocked_count = sum(1 for fact in edge_facts if fact.decision == "blocked_edge")
    edge_pairs = ";".join(
        (
            f"{fact.owner_pair_ids[0]}|{fact.owner_pair_ids[1]}:"
            f"{fact.role}:{fact.decision}:{fact.failure_reason or 'none'}"
        )
        for fact in edge_facts
    )
    return (
        f"family_evidence={feature.evidence};"
        f"edge_fact_count={len(edge_facts)};"
        f"support_count={support_count};"
        f"challenge_count={challenge_count};"
        f"blocked_count={blocked_count};"
        f"edge_pairs={edge_pairs}"
    )
