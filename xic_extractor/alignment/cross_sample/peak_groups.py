"""Cross-sample peak group construction.

This module groups SampleLocalMS1Owner instances using shared physical rules
and explicitly supplied method evidence. ``CrossSamplePeakGroupHypothesis`` is
the one representation of a group from grouping to the written outputs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeAlias, cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.identity_models import (
    CrossSampleContractError,
    CrossSampleEdgeDisposition,
    CrossSampleGroupDisposition,
    CrossSampleGroupMemberSubject,
    CrossSampleInitialEdgeContext,
    CrossSampleNeighborhoodStatus,
    canonical_group_members,
    group_member_subject_from_peak_hypothesis,
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

GroupDestination: TypeAlias = Literal["matrix", "archive"]


@dataclass(frozen=True)
class CrossSamplePeakGroupHypothesis:
    """Peaks across samples treated as one feature.

    Equality and hash are the membership: the sample-ordered member subjects and
    the disposition. Every other field is a fact about that membership.
    ``group_center_mz``/``group_center_rt`` are decided when the group is
    constructed; a group extended by Backfill keeps the centre its searches used.
    """

    member_subjects: tuple[CrossSampleGroupMemberSubject, ...]
    disposition: CrossSampleGroupDisposition
    owners: tuple[SampleLocalMS1Owner, ...] = field(compare=False)
    group_center_mz: float = field(compare=False)
    group_center_rt: float = field(compare=False)
    neutral_loss_tag: str = field(default="", compare=False)
    group_product_mz: float | None = field(default=None, compare=False)
    group_observed_neutral_loss_da: float | None = field(default=None, compare=False)
    has_anchor: bool = field(default=False, compare=False)
    evidence: str = field(default="", compare=False)
    inference_basis: FeatureMembershipGroup | None = field(default=None, compare=False)
    inference_decisions: tuple[SparseFeatureDecision, ...] = field(
        default=(), compare=False
    )

    @property
    def group_hypothesis_id(self) -> str:
        """In-process key: the sample-ordered member hypothesis keys."""

        return " + ".join(member.subject_id for member in self.member_subjects)

    @property
    def owner_ids(self) -> tuple[str, ...]:
        return tuple(owner.owner_id for owner in self.owners)

    @property
    def destination(self) -> GroupDestination:
        """Only an established group is delivered to the Matrix."""

        return "matrix" if self.disposition == "established" else "archive"


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
    """Infer feature membership and build one group per inferred membership.

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
    for basis in inference.groups:
        group = tuple(by_id[owner_id] for owner_id in basis.owner_ids)
        if basis.disposition == "unresolved_conflict":
            # The input contradicts a unique selected observation per sample.
            # Do not turn one known identity into multiple published groups.
            raise CrossSampleContractError(
                "known identity has conflicting sample observations; "
                "selection is unresolved and identity cannot be split"
            )
        hypotheses.append(
            _hypothesis_from_owner_group(
                group,
                disposition=(
                    "unresolved" if basis.disposition == "undecided" else None
                ),
                evidence=f"owner_feature_inference;{basis.disposition};{basis.reason}",
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
    disposition: CrossSampleGroupDisposition | None,
    evidence: str,
    center: tuple[float, float] | None = None,
    inference_basis: FeatureMembershipGroup | None = None,
    inference_decisions: tuple[SparseFeatureDecision, ...] = (),
) -> CrossSamplePeakGroupHypothesis:
    """Build one group from its owners; ``center`` keeps an earlier decision."""

    subjects = tuple(owner.cross_sample_member_subject for owner in group)
    if any(subject is None for subject in subjects):
        raise CrossSampleContractError("owner is missing a CrossSample member subject")
    members = canonical_group_members(
        cast(tuple[CrossSampleGroupMemberSubject, ...], subjects)
    )
    if center is None:
        center = (
            sum(owner.precursor_mz for owner in group) / len(group),
            sum(owner.owner_apex_rt for owner in group) / len(group),
        )
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
    unsupported_singleton = is_singleton and not has_definitive
    return CrossSamplePeakGroupHypothesis(
        member_subjects=members,
        disposition=disposition
        or ("established" if len(members) >= 2 else "provisional"),
        owners=group,
        group_center_mz=center[0],
        group_center_rt=center[1],
        neutral_loss_tag=group[0].neutral_loss_tag,
        group_product_mz=center_product_mz,
        group_observed_neutral_loss_da=center_loss,
        has_anchor=has_definitive or not is_singleton,
        evidence=(
            f"{evidence};provisional_singleton_review_only"
            if unsupported_singleton and "provisional_singleton" not in evidence
            else evidence
        ),
        inference_basis=inference_basis,
        inference_decisions=inference_decisions,
    )


__all__ = (
    "CrossSampleContractError",
    "CrossSampleEdgeDisposition",
    "CrossSampleGroupDisposition",
    "CrossSampleGroupMemberSubject",
    "CrossSampleInitialEdgeContext",
    "CrossSampleLineageEdge",
    "CrossSampleNeighborhoodEdge",
    "CrossSampleNeighborhoodStatus",
    "_expected_cross_sample_lineage",
    "GroupDestination",
    "CrossSamplePeakGroupHypothesis",
    "CrossSamplePartitionTransition",
    "classify_cross_sample_initial_edge",
    "construct_cross_sample_peak_group_hypotheses",
    "group_member_subject_from_peak_hypothesis",
)
