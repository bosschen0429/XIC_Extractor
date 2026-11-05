"""Helpers shared by test_cross_sample_association_lineage and other test modules."""

from __future__ import annotations

from dataclasses import dataclass

import xic_extractor.alignment.cross_sample.peak_groups as cross_sample_owner
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.grouping_result import (
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSamplePartitionTransition,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
)


@dataclass(frozen=True, slots=True)
class _CoverageContext:
    processing_owners: ProcessingOwnerReferences
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    sample_order: tuple[str, ...]
    raw_sample_stems: frozenset[str]
    alignment_config: AlignmentConfig


class _Fixture:
    def __init__(self, context: _CoverageContext) -> None:
        self.context = context
        self.samples = {sample.lookup_token: sample for sample in self.context.samples}
        self.members: dict[str, CrossSampleGroupMemberSubject] = {}


def _final_receipt(
    fixture: _Fixture,
    *,
    predecessors: tuple[CrossSampleGroupIdentity, ...],
    successors: tuple[CrossSampleGroupIdentity, ...],
) -> CrossSampleGroupingResult:
    canonical_predecessors = tuple(
        sorted(predecessors, key=lambda group: group.group_hypothesis_id)
    )
    canonical_successors = tuple(
        sorted(successors, key=lambda group: group.group_hypothesis_id)
    )
    unchanged = {group.group_hypothesis_id for group in canonical_predecessors} == {
        group.group_hypothesis_id for group in canonical_successors
    }
    application = CrossSamplePartitionTransition(
        status="assessable",
        predecessors=canonical_predecessors,
        successors=canonical_successors,
        lineage=(
            ()
            if unchanged
            else cross_sample_owner._expected_cross_sample_lineage(
                predecessors=canonical_predecessors,
                successors=canonical_successors,
            )
        ),
        reason=(
            "unchanged_membership_no_lineage" if unchanged else "membership_changed"
        ),
    )
    return CrossSampleGroupingResult(
        processing_owners=fixture.context.processing_owners,
        partition_transition=application,
        rt_frame=SampleRtFrame(),
    )
