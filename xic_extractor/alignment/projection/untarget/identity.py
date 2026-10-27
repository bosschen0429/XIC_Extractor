"""Finalized group/sample positions for completed Untarget results."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class UntargetProjectionContractError(ValueError):
    """Raised when an Untargeted projection cannot close exactly."""


@dataclass(frozen=True, slots=True)
class GroupCellSubject:
    """One finalized group crossed with one canonical sample.

    A missing member is the explicit absence at this group/sample position.
    """

    group: CrossSampleGroupIdentity
    canonical_sample: ResolvedCanonicalSampleIdentity
    member_subject: CrossSampleGroupMemberSubject | None

    @property
    def processing_request_id(self) -> str:
        return self.group.processing_request_id

    @property
    def group_hypothesis_id(self) -> str:
        return self.group.group_hypothesis_id
