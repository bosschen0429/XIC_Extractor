"""Finalized group/sample positions for completed Untarget results."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupMemberSubject,
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.sample_metadata import SampleMetadata


class UntargetProjectionContractError(ValueError):
    """Raised when an Untargeted projection cannot close exactly."""


@dataclass(frozen=True, slots=True)
class GroupCellSubject:
    """One finalized group crossed with one canonical sample.

    A missing member is the explicit absence at this group/sample position.
    """

    group: CrossSamplePeakGroupHypothesis
    canonical_sample: SampleMetadata
    member_subject: CrossSampleGroupMemberSubject | None

    @property
    def group_hypothesis_id(self) -> str:
        return self.group.group_hypothesis_id
