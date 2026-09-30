"""Cross-sample peak group construction.

This module groups SampleLocalMS1Owner instances into cross-sample chemical-
entity groups using m/z, RT, product m/z, observed neutral loss, and edge
evidence.  The output ``CrossSamplePeakGroupHypothesis`` is the primary
cross-sample identity unit.

``public_family_id`` is a *display label* preserved for output compatibility.
It is NOT an identity anchor; use ``group_hypothesis_id`` for identity
decisions within the pipeline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_identity_models import (
    CrossSampleContractError,
    CrossSampleEdgeDisposition,
    CrossSampleGroupDisposition,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleIdentityBasisRegistry,
    CrossSampleInitialEdgeContext,
    CrossSampleNeighborhoodStatus,
    group_member_subject_from_discovery_physical_selection,
    group_member_subject_from_peak_hypothesis,
    group_member_subject_from_peak_observation,
    mint_cross_sample_group_identity,
    validate_cross_sample_group_population,
)
from xic_extractor.alignment.cross_sample_neighborhood_graph import (
    CrossSampleLineageEdge,
    CrossSampleNeighborhoodEdge,
    CrossSamplePartitionTransition,
    _expected_cross_sample_lineage,
    classify_cross_sample_initial_edge,
)
from xic_extractor.alignment.edge_scoring import (
    DriftLookupProtocol,
    DriftPriorSource,
    EdgeDecision,
    HardGateFailureReason,
    OwnerEdgeEvidence,
    RTEvidenceBasis,
)
from xic_extractor.alignment.feature_membership_inference import (
    BatchRtWarp,
    FeatureInferenceOperationCounts,
    FeatureMembershipGroup,
    SparseFeatureDecision,
    infer_feature_memberships,
)
from xic_extractor.alignment.ownership_models import (
    AmbiguousOwnerRecord,
    SampleLocalMS1Owner,
)

if TYPE_CHECKING:
    pass

CrossSamplePeakGroupEdgeRole: TypeAlias = Literal[
    "membership_support",
    "membership_challenge",
]
CrossSamplePeakGroupConstructionPolicy: TypeAlias = Literal[
    "none",
    "construction_time_hard_gate_observed",
]
CrossSamplePeakGroupReviewReason: TypeAlias = Literal[
    "identity_conflict",
    "ambiguous_owner",
    "review_only",
]
CrossSamplePeakGroupReviewRole: TypeAlias = Literal["review_only_challenge"]
CrossSamplePeakGroupHardGateRole: TypeAlias = Literal["split_gate_challenge"]
CrossSamplePeakGroupConstructionRole: TypeAlias = Literal[
    "owner_aligned_feature_shadow",
    "successor_constructor",
]


@dataclass(frozen=True)
class CrossSamplePeakGroupEdgeFact:
    left_owner_id: str
    right_owner_id: str
    owner_pair_ids: tuple[str, str]
    decision: EdgeDecision
    role: CrossSamplePeakGroupEdgeRole
    failure_reason: HardGateFailureReason | Literal[""]
    rt_raw_delta_sec: float
    rt_drift_corrected_delta_sec: float | None
    drift_prior_source: DriftPriorSource
    rt_basis: RTEvidenceBasis
    injection_order_gap: int | None
    score: int
    reason: str
    construction_policy: CrossSamplePeakGroupConstructionPolicy = "none"
    source: str = "owner_edge_evidence_shadow"


@dataclass(frozen=True)
class CrossSamplePeakGroupReviewFact:
    feature_family_id: str
    public_family_id: str
    review_only: bool
    identity_conflict: bool
    ambiguous_sample_stem: str | None
    ambiguous_candidate_ids: tuple[str, ...]
    evidence: str
    reason: CrossSamplePeakGroupReviewReason
    role: CrossSamplePeakGroupReviewRole = "review_only_challenge"
    source: str = "owner_aligned_feature_review_shadow"


@dataclass(frozen=True)
class CrossSamplePeakGroupHardGateChallengeFact:
    left_owner_id: str
    right_owner_id: str
    owner_pair_ids: tuple[str, str]
    decision: EdgeDecision
    role: CrossSamplePeakGroupHardGateRole
    failure_reason: HardGateFailureReason | Literal[""]
    reason: str
    construction_policy: CrossSamplePeakGroupConstructionPolicy = (
        "construction_time_hard_gate_observed"
    )
    source: str = "owner_edge_hard_gate_shadow"


@dataclass(frozen=True)
class CrossSamplePeakGroupHypothesis:
    """Cross-sample chemical-entity group hypothesis.

    Identity semantics:
    - ``group_hypothesis_id``: primary identity key for pipeline decisions.
    - ``public_family_id``: display label for output compatibility.
      It is intentionally independent of ``group_hypothesis_id``.
    - ``feature_family_id`` property: legacy output alias for ``public_family_id``.
    """

    group_hypothesis_id: str
    public_family_id: str
    owner_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    event_member_count: int
    processing_request_id: str = ""
    member_subjects: tuple[CrossSampleGroupMemberSubject, ...] = ()
    identity_basis_bytes: bytes = b""
    disposition: CrossSampleGroupDisposition = "established"
    source: str = "owner_aligned_feature_shadow"
    neutral_loss_tag: str = ""
    group_center_mz: float | None = None
    group_center_rt: float | None = None
    group_product_mz: float | None = None
    group_observed_neutral_loss_da: float | None = None
    has_anchor: bool = False
    owners: tuple[SampleLocalMS1Owner, ...] = ()
    evidence: str = ""
    identity_conflict: bool = False
    review_only: bool = False
    confirm_local_owners_with_backfill: bool = False
    backfill_seed_centers: tuple[tuple[float, float], ...] = ()
    ambiguous_sample_stem: str | None = None
    ambiguous_candidate_ids: tuple[str, ...] = ()
    construction_role: CrossSamplePeakGroupConstructionRole = (
        "owner_aligned_feature_shadow"
    )
    edge_facts: tuple[CrossSamplePeakGroupEdgeFact, ...] = ()
    review_facts: tuple[CrossSamplePeakGroupReviewFact, ...] = ()
    hard_gate_challenge_facts: tuple[
        CrossSamplePeakGroupHardGateChallengeFact, ...
    ] = ()
    inference_basis: FeatureMembershipGroup | None = None
    inference_decisions: tuple[SparseFeatureDecision, ...] = ()

    @property
    def feature_family_id(self) -> str:
        return self.public_family_id

    @property
    def cluster_id(self) -> str:
        return self.public_family_id

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
    def family_product_mz(self) -> float:
        if self.group_product_mz is None:
            raise ValueError("cross-sample peak group is missing product m/z")
        return self.group_product_mz

    @property
    def family_observed_neutral_loss_da(self) -> float:
        if self.group_observed_neutral_loss_da is None:
            raise ValueError("cross-sample peak group is missing observed neutral loss")
        return self.group_observed_neutral_loss_da

    @property
    def members(self) -> tuple[SampleLocalMS1Owner, ...]:
        return self.owners

    @property
    def event_cluster_ids(self) -> tuple[str, ...]:
        return self.owner_ids

    @property
    def group_construction_role(self) -> str:
        if self.construction_role == "successor_constructor":
            return "successor_constructor"
        return "successor_projection_adapter"

    @property
    def group_delivery_role(self) -> str:
        return "successor_delivery_protocol"

    @property
    def group_membership_source(self) -> str:
        return "cross_sample_peak_group_hypothesis"


def validate_cross_sample_peak_group_identity(
    group: CrossSamplePeakGroupHypothesis,
    *,
    require_canonical: bool = False,
) -> CrossSampleGroupIdentity | None:
    """Replay the canonical identity retained by a scientific group carrier."""

    if not isinstance(group, CrossSamplePeakGroupHypothesis):
        raise CrossSampleContractError("group must be a CrossSample hypothesis")
    has_identity = bool(
        group.processing_request_id
        or group.member_subjects
        or group.identity_basis_bytes
    )
    if not has_identity:
        if require_canonical:
            raise CrossSampleContractError(
                "scientific CrossSample carrier is missing canonical identity"
            )
        return None
    if not (
        group.processing_request_id
        and group.member_subjects
        and group.identity_basis_bytes
    ):
        raise CrossSampleContractError(
            "canonical CrossSample carrier has incomplete identity fields"
        )
    identity = CrossSampleGroupIdentity(
        group_hypothesis_id=group.group_hypothesis_id,
        processing_request_id=group.processing_request_id,
        member_subjects=group.member_subjects,
        basis_bytes=group.identity_basis_bytes,
        disposition=group.disposition,
    )
    return identity


def construct_cross_sample_peak_group_hypotheses(
    owners: tuple[SampleLocalMS1Owner, ...] | list[SampleLocalMS1Owner],
    *,
    config: AlignmentConfig,
    sample_universe: frozenset[str] | None = None,
    injection_order: Mapping[str, int] | None = None,
    drift_lookup: DriftLookupProtocol | None = None,
    edge_evidence_sink: list[OwnerEdgeEvidence] | None = None,
    processing_request_id: str | None = None,
    known_identity_bindings: Sequence[tuple[str, str]] = (),
    operation_counts_sink: list[FeatureInferenceOperationCounts] | None = None,
    bootstrap: bool = False,
    rt_warp: BatchRtWarp | None = None,
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
    request_ids = {
        subject.processing_request_id for subject in subjects if subject is not None
    }
    if len(request_ids) != 1:
        raise CrossSampleContractError("owners crossed processing requests")
    request_id = next(iter(request_ids))
    if processing_request_id is not None and processing_request_id != request_id:
        raise CrossSampleContractError("explicit request crossed owner subjects")
    if sample_universe is not None and any(
        owner.sample_stem not in sample_universe for owner in owners
    ):
        raise ValueError("sample_universe omits an owner sample")
    inference = infer_feature_memberships(
        owners,
        config=config,
        injection_order=injection_order,
        drift_lookup=drift_lookup,
        known_identity_bindings=known_identity_bindings,
        bootstrap=bootstrap,
        rt_warp=rt_warp,
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
            processing_request_id=request_id,
            disposition="unresolved" if unresolved else None,
        )
        hypothesis = _hypothesis_from_owner_group(
            group,
            group_identity=group_identity,
            public_family_id=f"FAM{index:06d}",
            evidence=f"owner_feature_inference;{basis.disposition};{basis.reason}",
            identity_conflict=any(owner.identity_conflict for owner in group),
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


def review_only_peak_group_hypotheses_from_ambiguous_records(
    records: tuple[AmbiguousOwnerRecord, ...],
    *,
    start_index: int,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    hypotheses: list[CrossSamplePeakGroupHypothesis] = []
    for offset, record in enumerate(records):
        if (
            record.neutral_loss_tag is None
            or record.precursor_mz is None
            or record.apex_rt is None
            or record.product_mz is None
            or record.observed_neutral_loss_da is None
        ):
            continue
        family_id = f"FAM{start_index + offset:06d}"
        hypotheses.append(
            CrossSamplePeakGroupHypothesis(
                group_hypothesis_id=family_id,
                public_family_id=family_id,
                owner_ids=(),
                event_ids=(),
                event_member_count=0,
                source="cross_sample_peak_group_successor_constructor",
                neutral_loss_tag=record.neutral_loss_tag,
                group_center_mz=record.precursor_mz,
                group_center_rt=record.apex_rt,
                group_product_mz=record.product_mz,
                group_observed_neutral_loss_da=record.observed_neutral_loss_da,
                has_anchor=False,
                owners=(),
                evidence="ambiguous_ms1_owner_review_only",
                review_only=True,
                ambiguous_sample_stem=record.sample_stem,
                ambiguous_candidate_ids=record.candidate_ids,
                construction_role="successor_constructor",
            ),
        )
    return tuple(hypotheses)


def _hypothesis_from_owner_group(
    group: tuple[SampleLocalMS1Owner, ...],
    *,
    group_identity: CrossSampleGroupIdentity,
    public_family_id: str,
    evidence: str,
    identity_conflict: bool = False,
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
    event_ids = tuple(
        event_id for owner in group for event_id in owner.event_candidate_ids
    )
    is_singleton = len(group) == 1
    has_definitive = any(not getattr(owner, "is_provisional", False) for owner in group)
    has_anchor = has_definitive or not is_singleton
    unsupported_singleton = is_singleton and not has_definitive
    effective_review_only = review_only or unsupported_singleton
    effective_evidence = (
        f"{evidence};provisional_singleton_review_only"
        if unsupported_singleton and "provisional_singleton" not in evidence
        else evidence
    )
    return CrossSamplePeakGroupHypothesis(
        group_hypothesis_id=group_identity.group_hypothesis_id,
        public_family_id=public_family_id,
        owner_ids=tuple(owner.owner_id for owner in group),
        event_ids=event_ids,
        event_member_count=sum(len(owner.all_events) for owner in group),
        processing_request_id=group_identity.processing_request_id,
        member_subjects=group_identity.member_subjects,
        identity_basis_bytes=group_identity.basis_bytes,
        disposition=group_identity.disposition,
        source="cross_sample_peak_group_successor_constructor",
        neutral_loss_tag=group[0].neutral_loss_tag,
        group_center_mz=center_mz,
        group_center_rt=center_rt,
        group_product_mz=center_product_mz,
        group_observed_neutral_loss_da=center_loss,
        has_anchor=has_anchor,
        owners=group,
        evidence=effective_evidence,
        identity_conflict=identity_conflict,
        review_only=effective_review_only,
        construction_role="successor_constructor",
    )


def _identity_from_owners(
    owners: Sequence[SampleLocalMS1Owner],
    *,
    processing_request_id: str,
    disposition: CrossSampleGroupDisposition | None = None,
) -> CrossSampleGroupIdentity:
    subjects = tuple(owner.cross_sample_member_subject for owner in owners)
    if any(subject is None for subject in subjects):
        raise CrossSampleContractError("owner is missing a CrossSample member subject")
    return mint_cross_sample_group_identity(
        processing_request_id=processing_request_id,
        member_subjects=cast(tuple[CrossSampleGroupMemberSubject, ...], subjects),
        disposition=disposition,
    )


__all__ = (
    "CrossSampleContractError",
    "CrossSampleEdgeDisposition",
    "CrossSampleGroupDisposition",
    "CrossSampleGroupIdentity",
    "CrossSampleGroupMemberSubject",
    "CrossSampleIdentityBasisRegistry",
    "CrossSampleInitialEdgeContext",
    "CrossSampleLineageEdge",
    "CrossSampleNeighborhoodEdge",
    "CrossSampleNeighborhoodStatus",
    "_expected_cross_sample_lineage",
    "CrossSamplePeakGroupConstructionPolicy",
    "CrossSamplePeakGroupConstructionRole",
    "CrossSamplePeakGroupEdgeFact",
    "CrossSamplePeakGroupEdgeRole",
    "CrossSamplePeakGroupHardGateChallengeFact",
    "CrossSamplePeakGroupHardGateRole",
    "CrossSamplePeakGroupHypothesis",
    "CrossSamplePeakGroupReviewFact",
    "CrossSamplePeakGroupReviewReason",
    "CrossSamplePeakGroupReviewRole",
    "CrossSamplePartitionTransition",
    "classify_cross_sample_initial_edge",
    "construct_cross_sample_peak_group_hypotheses",
    "group_member_subject_from_discovery_physical_selection",
    "group_member_subject_from_peak_hypothesis",
    "group_member_subject_from_peak_observation",
    "mint_cross_sample_group_identity",
    "review_only_peak_group_hypotheses_from_ambiguous_records",
    "validate_cross_sample_group_population",
    "validate_cross_sample_peak_group_identity",
)
