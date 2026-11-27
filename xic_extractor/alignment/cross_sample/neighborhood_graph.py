"""Neighborhood graphs, edge evaluations, and partition transitions for CrossSample."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from xic_extractor.alignment.cross_sample.identity_models import (
    CrossSampleGroupMemberSubject,
    CrossSampleNeighborhoodStatus,
)

if TYPE_CHECKING:
    from xic_extractor.alignment.cross_sample.peak_groups import (
        CrossSamplePeakGroupHypothesis,
    )


@dataclass(frozen=True, slots=True)
class CrossSampleLineageEdge:
    predecessor: CrossSamplePeakGroupHypothesis
    successor: CrossSamplePeakGroupHypothesis


@dataclass(frozen=True, slots=True)
class CrossSamplePartitionTransition:
    status: CrossSampleNeighborhoodStatus
    predecessors: tuple[CrossSamplePeakGroupHypothesis, ...]
    successors: tuple[CrossSamplePeakGroupHypothesis, ...]
    lineage: tuple[CrossSampleLineageEdge, ...]
    reason: str
    admitted_members: tuple[CrossSampleGroupMemberSubject, ...] = ()


def _expected_cross_sample_lineage(
    *,
    predecessors: tuple[CrossSamplePeakGroupHypothesis, ...],
    successors: tuple[CrossSamplePeakGroupHypothesis, ...],
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
