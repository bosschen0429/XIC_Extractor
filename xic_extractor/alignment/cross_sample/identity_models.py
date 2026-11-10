"""Group members, dispositions and edge vocabulary for CrossSample."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.peak_detection.peak_hypothesis_identity import MeasuredPeakHypothesis
from xic_extractor.sample_metadata import SampleMetadata

CrossSampleGroupDisposition: TypeAlias = Literal[
    "established",
    "provisional",
    "unresolved",
    "rejected",
]
CrossSampleEdgeDisposition: TypeAlias = Literal[
    "eligible",
    "review_only",
    "excluded",
]
CrossSampleNeighborhoodStatus: TypeAlias = Literal["assessable", "unassessable"]
CrossSampleInitialEdgeContext: TypeAlias = Literal[
    "hard_gate",
    "complete_link",
    "full_batch_continuity",
    "targeted_istd_continuity",
]


class CrossSampleContractError(ValueError):
    """Raised when canonical CrossSample state cannot close exactly."""


@dataclass(frozen=True, slots=True)
class CrossSampleGroupMemberSubject:
    sample_key: str
    subject_kind: Literal["peak_hypothesis"]
    subject_id: str
    physical_peaks: tuple[PeakObservation, ...]


def group_member_subject_from_peak_hypothesis(
    *,
    sample: SampleMetadata,
    hypothesis: MeasuredPeakHypothesis,
) -> CrossSampleGroupMemberSubject:
    if not isinstance(sample, SampleMetadata):
        raise CrossSampleContractError("sample must be canonically resolved")
    if not isinstance(hypothesis, MeasuredPeakHypothesis):
        raise CrossSampleContractError("hypothesis must be owner-backed")
    sample_id = sample.sample_name
    if hypothesis.peak.sample_key != sample_id:
        raise CrossSampleContractError("peak hypothesis crossed physical observation")
    return CrossSampleGroupMemberSubject(
        sample_key=sample_id,
        subject_kind="peak_hypothesis",
        subject_id=hypothesis.hypothesis_id,
        physical_peaks=(hypothesis.peak.physical_identity,),
    )


def canonical_group_members(
    member_subjects: Sequence[CrossSampleGroupMemberSubject],
) -> tuple[CrossSampleGroupMemberSubject, ...]:
    if not member_subjects:
        raise CrossSampleContractError("cross-sample group requires a member")
    samples: set[str] = set()
    physical: set[PeakObservation] = set()
    members: list[CrossSampleGroupMemberSubject] = []
    for member in member_subjects:
        if not isinstance(member, CrossSampleGroupMemberSubject):
            raise CrossSampleContractError("group member is not a typed subject")
        if member.sample_key in samples:
            raise CrossSampleContractError(
                "group has two identity-bearing members for one canonical sample"
            )
        if physical.intersection(member.physical_peaks):
            raise CrossSampleContractError("group duplicates a physical observation")
        samples.add(member.sample_key)
        physical.update(member.physical_peaks)
        members.append(member)
    return tuple(sorted(members, key=lambda item: item.sample_key))


__all__ = (
    "CrossSampleContractError",
    "CrossSampleEdgeDisposition",
    "CrossSampleGroupDisposition",
    "CrossSampleGroupMemberSubject",
    "CrossSampleInitialEdgeContext",
    "CrossSampleNeighborhoodStatus",
    "canonical_group_members",
    "group_member_subject_from_peak_hypothesis",
)
