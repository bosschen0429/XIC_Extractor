"""Neighborhood graphs, edge evaluations, and partition transitions for CrossSample."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.identity_models import (
    CrossSampleContractError,
    CrossSampleEdgeDisposition,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleInitialEdgeContext,
    CrossSampleNeighborhoodStatus,
)
from xic_extractor.alignment.edge_scoring import (
    OwnerEdgeEvidence,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.rt_frame import RtFrame

INITIAL_GROUPING_POLICY: Literal["feature-group-membership"] = (
    "feature-group-membership"
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
        if context == "targeted_istd_continuity" and edge.failure_allows_rt_continuity:
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
    predecessor: CrossSampleGroupIdentity
    successor: CrossSampleGroupIdentity


@dataclass(frozen=True, slots=True)
class CrossSamplePartitionTransition:
    status: CrossSampleNeighborhoodStatus
    predecessors: tuple[CrossSampleGroupIdentity, ...]
    successors: tuple[CrossSampleGroupIdentity, ...]
    lineage: tuple[CrossSampleLineageEdge, ...]
    reason: str
    admitted_members: tuple[CrossSampleGroupMemberSubject, ...] = ()

    def __post_init__(self) -> None:
        if self.status == "assessable":
            expected = Counter(
                member
                for group in self.predecessors
                for member in group.member_subjects
            )
            expected.update(self.admitted_members)
            actual = Counter(
                member for group in self.successors for member in group.member_subjects
            )
            if actual != expected:
                raise CrossSampleContractError(
                    "partition transition is not a complete partition"
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
            and predecessor.member_subjects != successor.member_subjects
        )
        lineage_edges.extend(
            CrossSampleLineageEdge(
                predecessor=predecessor,
                successor=successor,
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
    "_expected_cross_sample_lineage",
    "_full_batch_weak_edge_is_eligible",
    "_targeted_istd_rt_disposition",
    "classify_cross_sample_initial_edge",
)
