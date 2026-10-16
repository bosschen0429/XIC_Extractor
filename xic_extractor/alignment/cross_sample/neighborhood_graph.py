"""Neighborhood graphs, edge evaluations, and partition transitions for CrossSample."""

from __future__ import annotations

import json
from dataclasses import InitVar, dataclass, field
from typing import Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.identity_models import (
    _CROSS_SAMPLE_GROUP_PREFIX,
    CrossSampleContractError,
    CrossSampleEdgeDisposition,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleInitialEdgeContext,
    CrossSampleNeighborhoodStatus,
    _required_text,
    validate_cross_sample_group_population,
)
from xic_extractor.alignment.edge_scoring import (
    OwnerEdgeEvidence,
    evaluate_owner_edge,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.processing_identity import (
    PROCESSING_REQUEST_ID_PREFIX,
    canonical_identity_basis_bytes,
    typed_identity_digest,
    validate_typed_identity_basis,
    validate_typed_identity_id,
)

_CROSS_SAMPLE_LINEAGE_EDGE_PREFIX = "xic.cross-sample-lineage-edge:"
_INITIAL_EDGE_MINT_TOKEN = object()
INITIAL_GROUPING_POLICY: Literal["feature-group-membership-v1"] = (
    "feature-group-membership-v1"
)
UNLISTED_PAIR_DISPOSITION: Literal["not_evaluated"] = "not_evaluated"


@dataclass(frozen=True, slots=True)
class CrossSampleNeighborhoodEdge:
    left_member_subject_id: str
    right_member_subject_id: str
    disposition: CrossSampleEdgeDisposition
    initial_evidence: OwnerEdgeEvidence
    initial_context: CrossSampleInitialEdgeContext
    config: AlignmentConfig
    left_owner: SampleLocalMS1Owner
    right_owner: SampleLocalMS1Owner
    rt_frame: RtFrame = field(
        repr=False,
        compare=False,
    )
    _mint_token: InitVar[object | None] = field(default=None, kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object | None) -> None:
        left = _required_text(self.left_member_subject_id, field="left member")
        right = _required_text(self.right_member_subject_id, field="right member")
        if left == right:
            raise CrossSampleContractError("neighborhood edge cannot be a self-edge")
        if self.disposition not in {"eligible", "review_only", "excluded"}:
            raise CrossSampleContractError("edge disposition is not closed")
        if not isinstance(self.initial_evidence, OwnerEdgeEvidence):
            raise CrossSampleContractError(
                "neighborhood edge must be classified by CrossSample"
            )
        if not isinstance(self.left_owner, SampleLocalMS1Owner) or not isinstance(
            self.right_owner, SampleLocalMS1Owner
        ):
            raise CrossSampleContractError("neighborhood edge owners must be typed")
        left_member = self.left_owner.cross_sample_member_subject
        right_member = self.right_owner.cross_sample_member_subject
        if (
            left_member is None
            or right_member is None
            or left_member.member_subject_id != left
            or right_member.member_subject_id != right
            or self.initial_evidence.left_owner_id != self.left_owner.owner_id
            or self.initial_evidence.right_owner_id != self.right_owner.owner_id
            or self.initial_evidence.left_sample_stem != self.left_owner.sample_stem
            or self.initial_evidence.right_sample_stem != self.right_owner.sample_stem
        ):
            raise CrossSampleContractError(
                "neighborhood evidence crossed its exact owner-member pair"
            )
        exact_evidence = self.initial_evidence
        if _mint_token is not _INITIAL_EDGE_MINT_TOKEN:
            exact_evidence = evaluate_owner_edge(
                self.left_owner,
                self.right_owner,
                config=self.config,
                rt_frame=self.rt_frame,
            )
        if self.initial_evidence != exact_evidence:
            raise CrossSampleContractError(
                "neighborhood edge does not retain exact owner evidence"
            )
        expected = classify_cross_sample_initial_edge(
            exact_evidence,
            context=self.initial_context,
            config=self.config,
        )
        if self.disposition != expected:
            raise CrossSampleContractError(
                "neighborhood edge disposition contradicts owner classification"
            )


def classify_cross_sample_initial_edge(
    edge: OwnerEdgeEvidence,
    *,
    context: CrossSampleInitialEdgeContext,
    config: AlignmentConfig,
) -> CrossSampleEdgeDisposition:
    """Classify raw owner evidence without caller-authored compatibility flags."""

    if not isinstance(edge, OwnerEdgeEvidence):
        raise CrossSampleContractError("initial edge evidence must be owner-minted")
    if not isinstance(config, AlignmentConfig):
        raise CrossSampleContractError("initial edge config must be typed")
    if context not in {
        "hard_gate",
        "complete_link",
        "full_batch_continuity",
        "targeted_istd_continuity",
    }:
        raise CrossSampleContractError("initial edge context is not closed")
    if edge.decision == "blocked_edge":
        if context == "targeted_istd_continuity" and edge.failure_reason in {
            "product_mz_out_of_tolerance",
            "observed_loss_out_of_tolerance",
        }:
            return _targeted_istd_rt_disposition(edge, config=config)
        return "excluded"
    if context == "hard_gate":
        return "eligible"
    if edge.decision == "strong_edge":
        return "eligible"
    if _full_batch_weak_edge_is_eligible(edge, config=config):
        return "eligible"
    if context == "complete_link":
        return "review_only"
    if context == "targeted_istd_continuity":
        return _targeted_istd_rt_disposition(edge, config=config)
    return "review_only"


def _full_batch_weak_edge_is_eligible(
    edge: OwnerEdgeEvidence,
    *,
    config: AlignmentConfig,
) -> bool:
    return (
        edge.decision == "weak_edge"
        and not edge.failure_reason
        and edge.rt_basis == "raw"
        and edge.rt_raw_delta_sec <= config.preferred_rt_sec
        and edge.owner_quality == "clean"
        and edge.seed_support_level == "moderate"
    )


def _targeted_istd_rt_disposition(
    edge: OwnerEdgeEvidence,
    *,
    config: AlignmentConfig,
) -> CrossSampleEdgeDisposition:
    if (
        edge.owner_quality in {"weak", "ambiguous_nearby"}
        or edge.seed_support_level == "weak"
    ):
        return "review_only"
    rt_delta_sec = edge.rt_raw_delta_sec
    if (
        edge.rt_basis == "drift_corrected"
        and edge.rt_drift_corrected_delta_sec is not None
    ):
        rt_delta_sec = edge.rt_drift_corrected_delta_sec
    return "eligible" if rt_delta_sec <= config.preferred_rt_sec else "review_only"


@dataclass(frozen=True, slots=True)
class CrossSampleLineageEdge:
    lineage_edge_id: str
    predecessor_group_hypothesis_id: str
    successor_group_hypothesis_id: str
    processing_request_id: str
    predecessor_identity_basis_bytes: bytes
    predecessor_group_hypothesis_ids: tuple[str, ...]
    member_subject_ids: tuple[str, ...]
    basis_bytes: bytes

    def __post_init__(self) -> None:
        predecessor = _required_text(
            self.predecessor_group_hypothesis_id,
            field="lineage predecessor",
        )
        successor = _required_text(
            self.successor_group_hypothesis_id,
            field="lineage successor",
        )
        if predecessor == successor:
            raise CrossSampleContractError("lineage cannot be a self-edge")
        request_id = validate_typed_identity_id(
            self.processing_request_id,
            prefix=PROCESSING_REQUEST_ID_PREFIX,
            field="lineage processing_request_id",
        )
        try:
            validate_typed_identity_basis(
                predecessor,
                prefix=_CROSS_SAMPLE_GROUP_PREFIX,
                basis_bytes=self.predecessor_identity_basis_bytes,
                field="lineage predecessor_group_hypothesis_id",
            )
            validate_typed_identity_id(
                successor,
                prefix=_CROSS_SAMPLE_GROUP_PREFIX,
                field="lineage successor_group_hypothesis_id",
            )
            predecessor_basis = json.loads(
                self.predecessor_identity_basis_bytes.decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise CrossSampleContractError(str(exc)) from exc
        if (
            not isinstance(predecessor_basis, dict)
            or set(predecessor_basis)
            != {
                "schema_version",
                "processing_request_id",
                "member_subject_ids",
            }
            or predecessor_basis["schema_version"] != "cross_sample_group"
            or predecessor_basis["processing_request_id"] != request_id
        ):
            raise CrossSampleContractError("lineage predecessor crossed request")
        predecessor_member_ids = predecessor_basis["member_subject_ids"]
        if (
            not isinstance(predecessor_member_ids, list)
            or not predecessor_member_ids
            or any(
                not isinstance(item, str) or not item for item in predecessor_member_ids
            )
            or sorted(set(predecessor_member_ids)) != predecessor_member_ids
        ):
            raise CrossSampleContractError("lineage predecessor basis is malformed")
        predecessor_ids = self.predecessor_group_hypothesis_ids
        if (
            not predecessor_ids
            or tuple(sorted(set(predecessor_ids))) != predecessor_ids
            or predecessor not in predecessor_ids
        ):
            raise CrossSampleContractError(
                "lineage predecessor closure must be non-empty and canonical"
            )
        if (
            not self.member_subject_ids
            or tuple(sorted(set(self.member_subject_ids))) != self.member_subject_ids
        ):
            raise CrossSampleContractError(
                "lineage member closure must be non-empty and canonical"
            )
        if not set(predecessor_member_ids).intersection(self.member_subject_ids):
            raise CrossSampleContractError(
                "lineage predecessor does not overlap successor membership"
            )
        basis = canonical_identity_basis_bytes(
            {
                "schema_version": "cross_sample_lineage_edge",
                "processing_request_id": request_id,
                "predecessor_group_hypothesis_id": predecessor,
                "predecessor_identity_basis": predecessor_basis,
                "predecessor_group_hypothesis_ids": list(predecessor_ids),
                "successor_group_hypothesis_id": successor,
                "member_subject_ids": list(self.member_subject_ids),
            }
        )
        if self.basis_bytes != basis:
            raise CrossSampleContractError("lineage edge basis bytes are not exact")
        try:
            validate_typed_identity_basis(
                self.lineage_edge_id,
                prefix=_CROSS_SAMPLE_LINEAGE_EDGE_PREFIX,
                basis_bytes=basis,
                field="lineage_edge_id",
            )
        except ValueError as exc:
            raise CrossSampleContractError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class CrossSamplePartitionTransition:
    status: CrossSampleNeighborhoodStatus
    predecessors: tuple[CrossSampleGroupIdentity, ...]
    successors: tuple[CrossSampleGroupIdentity, ...]
    lineage: tuple[CrossSampleLineageEdge, ...]
    reason: str
    admitted_members: tuple[CrossSampleGroupMemberSubject, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.predecessors) is not tuple
            or type(self.successors) is not tuple
            or type(self.lineage) is not tuple
            or type(self.admitted_members) is not tuple
        ):
            raise CrossSampleContractError(
                "partition transition closures must be immutable tuples"
            )
        if any(
            not isinstance(group, CrossSampleGroupIdentity)
            for group in self.predecessors
        ):
            raise CrossSampleContractError(
                "partition transition requires typed predecessors"
            )
        predecessor_ids = tuple(
            group.group_hypothesis_id for group in self.predecessors
        )
        if predecessor_ids != tuple(sorted(predecessor_ids)):
            raise CrossSampleContractError(
                "partition transition predecessors are not canonical-sorted"
            )
        validate_cross_sample_group_population(self.predecessors)
        if self.status == "unassessable":
            if (
                not self.predecessors
                or self.successors
                or self.lineage
                or self.admitted_members
                or self.reason != ("eligible_neighborhood_exceeds_budget")
            ):
                raise CrossSampleContractError(
                    "unassessable partition transition is not atomic"
                )
            return
        if self.status != "assessable" or bool(self.successors) != bool(
            self.predecessors
        ):
            raise CrossSampleContractError("partition transition status is invalid")
        successor_ids = tuple(group.group_hypothesis_id for group in self.successors)
        if successor_ids != tuple(sorted(successor_ids)):
            raise CrossSampleContractError(
                "partition transition successors are not canonical-sorted"
            )
        predecessor_members = sorted(
            member.member_subject_id
            for group in self.predecessors
            for member in group.member_subjects
        )
        successor_members = sorted(
            member.member_subject_id
            for group in self.successors
            for member in group.member_subjects
        )
        admitted_ids = [member.member_subject_id for member in self.admitted_members]
        if (
            len(admitted_ids) != len(set(admitted_ids))
            or set(admitted_ids).intersection(predecessor_members)
            or sorted([*predecessor_members, *admitted_ids]) != successor_members
        ):
            raise CrossSampleContractError(
                "partition transition is not a complete partition"
            )
        if self.admitted_members:
            # Recovery extends an existing group; it cannot repartition the
            # original owners, create a feature, or move an internal standard.
            if (
                len(self.successors) != len(self.predecessors)
                or any(
                    sum(
                        set(predecessor.member_subjects).issubset(
                            successor.member_subjects
                        )
                        for successor in self.successors
                    )
                    != 1
                    for predecessor in self.predecessors
                )
                or any(
                    sum(
                        set(predecessor.member_subjects).issubset(
                            successor.member_subjects
                        )
                        for predecessor in self.predecessors
                    )
                    != 1
                    for successor in self.successors
                )
            ):
                raise CrossSampleContractError(
                    "recovery must preserve every original group"
                )
        expected_lineage = _expected_cross_sample_lineage(
            predecessors=self.predecessors,
            successors=self.successors,
        )
        if self.lineage != expected_lineage:
            raise CrossSampleContractError(
                "partition transition lineage is not the complete atomic closure"
            )
        unchanged = set(predecessor_ids) == set(successor_ids)
        expected_reason = (
            "unchanged_membership_no_lineage" if unchanged else "membership_changed"
        )
        if self.reason != expected_reason:
            raise CrossSampleContractError(
                "partition transition reason contradicts its transition"
            )


def _mint_cross_sample_lineage_edge(
    *,
    predecessor: CrossSampleGroupIdentity,
    successor: CrossSampleGroupIdentity,
    predecessor_group_hypothesis_ids: tuple[str, ...],
) -> CrossSampleLineageEdge:
    member_subject_ids = tuple(
        member.member_subject_id for member in successor.member_subjects
    )
    predecessor_basis = json.loads(predecessor.basis_bytes.decode("utf-8"))
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_lineage_edge",
            "processing_request_id": predecessor.processing_request_id,
            "predecessor_group_hypothesis_id": predecessor.group_hypothesis_id,
            "predecessor_identity_basis": predecessor_basis,
            "predecessor_group_hypothesis_ids": list(predecessor_group_hypothesis_ids),
            "successor_group_hypothesis_id": successor.group_hypothesis_id,
            "member_subject_ids": list(member_subject_ids),
        }
    )
    return CrossSampleLineageEdge(
        lineage_edge_id=typed_identity_digest(
            _CROSS_SAMPLE_LINEAGE_EDGE_PREFIX,
            basis,
        ),
        predecessor_group_hypothesis_id=predecessor.group_hypothesis_id,
        successor_group_hypothesis_id=successor.group_hypothesis_id,
        processing_request_id=predecessor.processing_request_id,
        predecessor_identity_basis_bytes=predecessor.basis_bytes,
        predecessor_group_hypothesis_ids=predecessor_group_hypothesis_ids,
        member_subject_ids=member_subject_ids,
        basis_bytes=basis,
    )


def _expected_cross_sample_lineage(
    *,
    predecessors: tuple[CrossSampleGroupIdentity, ...],
    successors: tuple[CrossSampleGroupIdentity, ...],
) -> tuple[CrossSampleLineageEdge, ...]:
    lineage_edges: list[CrossSampleLineageEdge] = []
    for successor in successors:
        relevant_predecessors = tuple(
            predecessor
            for predecessor in predecessors
            if set(predecessor.member_subjects).intersection(successor.member_subjects)
            and predecessor.group_hypothesis_id != successor.group_hypothesis_id
        )
        predecessor_closure = tuple(
            predecessor.group_hypothesis_id for predecessor in relevant_predecessors
        )
        lineage_edges.extend(
            _mint_cross_sample_lineage_edge(
                predecessor=predecessor,
                successor=successor,
                predecessor_group_hypothesis_ids=predecessor_closure,
            )
            for predecessor in relevant_predecessors
        )
    return tuple(lineage_edges)


__all__ = (
    "CrossSampleLineageEdge",
    "CrossSampleNeighborhoodEdge",
    "CrossSamplePartitionTransition",
    "INITIAL_GROUPING_POLICY",
    "UNLISTED_PAIR_DISPOSITION",
    "_CROSS_SAMPLE_LINEAGE_EDGE_PREFIX",
    "_expected_cross_sample_lineage",
    "_full_batch_weak_edge_is_eligible",
    "_mint_cross_sample_lineage_edge",
    "_targeted_istd_rt_disposition",
    "classify_cross_sample_initial_edge",
)
