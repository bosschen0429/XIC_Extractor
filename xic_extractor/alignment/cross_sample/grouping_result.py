"""Stable cross-sample membership shared by Backfill and finalization."""

from __future__ import annotations

from dataclasses import dataclass, field

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
    CrossSamplePartitionTransition,
)
from xic_extractor.alignment.membership_selection import AcceptedMembershipRisk
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.sample_metadata import SampleMetadata


class CrossSampleGroupingContractError(ValueError):
    """Raised when a grouping result crosses its run's samples."""


@dataclass(frozen=True, slots=True)
class CrossSampleGroupingResult:
    """One immutable membership result, including explicit recovery admissions.

    The partition owner has already closed membership and lineage. This owner
    binds that answer to the run's samples once, without rebuilding its
    trajectory. Recovery may extend a missing sample with exact lineage while
    preserving every original group and member.
    """

    samples: tuple[SampleMetadata, ...]
    partition_transition: CrossSamplePartitionTransition
    rt_frame: SampleRtFrame = field(compare=False)

    backfill_membership_risks: tuple[tuple[str, str, AcceptedMembershipRisk], ...] = ()

    @property
    def initial_population(self) -> tuple[CrossSampleGroupIdentity, ...]:
        return self.partition_transition.predecessors

    @property
    def final_population(self) -> tuple[CrossSampleGroupIdentity, ...]:
        return self.partition_transition.successors
