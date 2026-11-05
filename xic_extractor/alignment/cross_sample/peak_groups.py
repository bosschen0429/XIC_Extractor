"""Cross-sample peak group construction.

This module groups SampleLocalMS1Owner instances using shared physical rules
and explicitly supplied method evidence. The output
``CrossSamplePeakGroupHypothesis`` is the primary cross-sample identity unit.

``feature_id`` is the shared output label.
It is NOT an identity anchor; use ``group_hypothesis_id`` for identity
decisions within the pipeline.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, TypeAlias, cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.identity_models import (
    CrossSampleContractError,
    CrossSampleEdgeDisposition,
    CrossSampleGroupDisposition,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleInitialEdgeContext,
    CrossSampleNeighborhoodStatus,
    group_member_subject_from_peak_hypothesis,
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.cross_sample.neighborhood_graph import (
    CrossSampleLineageEdge,
    CrossSampleNeighborhoodEdge,
    CrossSamplePartitionTransition,
    _expected_cross_sample_lineage,
    classify_cross_sample_initial_edge,
)
from xic_extractor.alignment.edge_scoring import (
    OwnerEdgeEvidence,
)
from xic_extractor.alignment.feature_membership_inference import (
    FeatureInferenceOperationCounts,
    FeatureMembershipGroup,
    SparseFeatureDecision,
    infer_feature_memberships,
)
from xic_extractor.alignment.grouping_rules import GroupingRules
from xic_extractor.alignment.ownership_models import (
    SampleLocalMS1Owner,
)
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_frame import RtFrame

CrossSamplePeakGroupConstructionRole: TypeAlias = Literal[
    "owner_aligned_feature_shadow",
    "successor_constructor",
]


@dataclass(frozen=True)
class CrossSamplePeakGroupHypothesis:
    """Cross-sample chemical-entity group hypothesis.

    Identity semantics:
    - ``group_hypothesis_id``: primary identity key for pipeline decisions.
    - ``feature_id``: shared output label.
      It is intentionally independent of ``group_hypothesis_id``.
    """

    membership: CrossSampleGroupIdentity
    feature_id: str
    owner_ids: tuple[str, ...]
    event_member_count: int
    source: str = "owner_aligned_feature_shadow"
    neutral_loss_tag: str = ""
    group_center_mz: float | None = None
    group_center_rt: float | None = None
    group_product_mz: float | None = None
    group_observed_neutral_loss_da: float | None = None
    has_anchor: bool = False
    owners: tuple[SampleLocalMS1Owner, ...] = ()
    evidence: str = ""
    review_only: bool = False
    confirm_local_owners_with_backfill: bool = False
    backfill_seed_centers: tuple[tuple[float, float], ...] = ()
    construction_role: CrossSamplePeakGroupConstructionRole = (
        "owner_aligned_feature_shadow"
    )
    inference_basis: FeatureMembershipGroup | None = None
    inference_decisions: tuple[SparseFeatureDecision, ...] = ()

    @property
    def group_hypothesis_id(self) -> str:
        return self.membership.group_hypothesis_id

    @property
    def member_subjects(self) -> tuple[CrossSampleGroupMemberSubject, ...]:
        return self.membership.member_subjects

    @property
    def disposition(self) -> CrossSampleGroupDisposition:
        return self.membership.disposition

    @property
    def cluster_id(self) -> str:
        return self.feature_id

    @property
    def family_center_mz(self) -> float:
        if self.group_center_mz is None:
            raise ValueError("cross-sample peak group is missing center m/z")
        return self.group_center_mz

    @property
    def family_center_rt(self) -> float:
        if self.group_center_rt is None:
            raise ValueError("cross-sample peak group is missing center RT")
        return self.group_center_rt

    @property
    def members(self) -> tuple[SampleLocalMS1Owner, ...]:
        return self.owners

    @property
    def event_cluster_ids(self) -> tuple[str, ...]:
        return self.owner_ids


def construct_cross_sample_peak_group_hypotheses(
    owners: tuple[SampleLocalMS1Owner, ...] | list[SampleLocalMS1Owner],
    *,
    config: AlignmentConfig,
    rules: GroupingRules,
    sample_universe: frozenset[str] | None = None,
    rt_frame: RtFrame,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None = None,
    known_identity_bindings: Sequence[tuple[str, str]] = (),
    operation_counts_sink: list[FeatureInferenceOperationCounts] | None = None,
    bootstrap: bool = False,
    accept_competition: bool = False,
    peak_trajectories: PeakTrajectories | None = None,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """Infer feature membership and mint its canonical CrossSample identity.

    Known identity is an input constraint, not a pairwise RT exception. The
    legacy edge sink remains empty because no pair evaluations are implied by
    a feature-level decision; consumers use ``inference_basis`` instead.
    """
    if not owners:
        return ()
    subjects = tuple(owner.cross_sample_member_subject for owner in owners)
    if any(subject is None for subject in subjects):
        raise CrossSampleContractError(
            "every owner requires an exact member subject before grouping"
        )
    if sample_universe is not None and any(
        owner.sample_stem not in sample_universe for owner in owners
    ):
        raise ValueError("sample_universe omits an owner sample")
    inference = infer_feature_memberships(
        owners,
        config=config,
        rt_frame=rt_frame,
        rules=rules,
        known_identity_bindings=known_identity_bindings,
        bootstrap=bootstrap,
        accept_competition=accept_competition,
        peak_trajectories=peak_trajectories,
    )
    if operation_counts_sink is not None:
        operation_counts_sink.append(inference.operation_counts)
    by_id = {owner.owner_id: owner for owner in owners}
    decisions_by_owner: dict[str, list[SparseFeatureDecision]] = {}
    for decision in inference.sparse_decisions:
        decisions_by_owner.setdefault(decision.owner_id, []).append(decision)
    hypotheses = []
    for index, basis in enumerate(inference.groups, start=1):
        group = tuple(by_id[owner_id] for owner_id in basis.owner_ids)
        if basis.disposition == "unresolved_conflict":
            # The input contradicts a unique selected observation per sample.
            # Do not turn one known identity into multiple published groups.
            raise CrossSampleContractError(
                "known identity has conflicting sample observations; "
                "selection is unresolved and identity cannot be split"
            )
        unresolved = basis.disposition == "undecided"
        group_identity = _identity_from_owners(
            group,
            disposition="unresolved" if unresolved else None,
        )
        hypothesis = _hypothesis_from_owner_group(
            group,
            group_identity=group_identity,
            feature_id=f"F{index:06d}",
            evidence=f"owner_feature_inference;{basis.disposition};{basis.reason}",
            review_only=unresolved,
        )
        hypotheses.append(
            replace(
                hypothesis,
                inference_basis=basis,
                inference_decisions=tuple(
                    decision
                    for owner_id in basis.owner_ids
                    for decision in decisions_by_owner.get(owner_id, ())
                ),
            )
        )
    return tuple(hypotheses)


def _hypothesis_from_owner_group(
    group: tuple[SampleLocalMS1Owner, ...],
    *,
    group_identity: CrossSampleGroupIdentity,
    feature_id: str,
    evidence: str,
    review_only: bool = False,
) -> CrossSamplePeakGroupHypothesis:
    center_mz = sum(owner.precursor_mz for owner in group) / len(group)
    center_rt = sum(owner.owner_apex_rt for owner in group) / len(group)
    events = tuple(
        owner.primary_identity_event
        for owner in group
        if owner.primary_identity_event is not None
    )
    center_product_mz = (
        None if not events else sum(event.product_mz for event in events) / len(events)
    )
    center_loss = (
        None
        if not events
        else sum(event.observed_neutral_loss_da for event in events) / len(events)
    )
    is_singleton = len(group) == 1
    has_definitive = any(not owner.is_provisional for owner in group)
    has_anchor = has_definitive or not is_singleton
    unsupported_singleton = is_singleton and not has_definitive
    effective_review_only = review_only or unsupported_singleton
    effective_evidence = (
        f"{evidence};provisional_singleton_review_only"
        if unsupported_singleton and "provisional_singleton" not in evidence
        else evidence
    )
    return CrossSamplePeakGroupHypothesis(
        membership=group_identity,
        feature_id=feature_id,
        owner_ids=tuple(owner.owner_id for owner in group),
        event_member_count=sum(len(owner.all_events) for owner in group),
        source="cross_sample_peak_group_successor_constructor",
        neutral_loss_tag=group[0].neutral_loss_tag,
        group_center_mz=center_mz,
        group_center_rt=center_rt,
        group_product_mz=center_product_mz,
        group_observed_neutral_loss_da=center_loss,
        has_anchor=has_anchor,
        owners=group,
        evidence=effective_evidence,
        review_only=effective_review_only,
        construction_role="successor_constructor",
    )


def _identity_from_owners(
    owners: Sequence[SampleLocalMS1Owner],
    *,
    disposition: CrossSampleGroupDisposition | None = None,
) -> CrossSampleGroupIdentity:
    subjects = tuple(owner.cross_sample_member_subject for owner in owners)
    if any(subject is None for subject in subjects):
        raise CrossSampleContractError("owner is missing a CrossSample member subject")
    return mint_cross_sample_group_identity(
        member_subjects=cast(tuple[CrossSampleGroupMemberSubject, ...], subjects),
        disposition=disposition,
    )


__all__ = (
    "CrossSampleContractError",
    "CrossSampleEdgeDisposition",
    "CrossSampleGroupDisposition",
    "CrossSampleGroupIdentity",
    "CrossSampleGroupMemberSubject",
    "CrossSampleInitialEdgeContext",
    "CrossSampleLineageEdge",
    "CrossSampleNeighborhoodEdge",
    "CrossSampleNeighborhoodStatus",
    "_expected_cross_sample_lineage",
    "CrossSamplePeakGroupConstructionRole",
    "CrossSamplePeakGroupHypothesis",
    "CrossSamplePartitionTransition",
    "classify_cross_sample_initial_edge",
    "construct_cross_sample_peak_group_hypotheses",
    "group_member_subject_from_peak_hypothesis",
    "mint_cross_sample_group_identity",
)
