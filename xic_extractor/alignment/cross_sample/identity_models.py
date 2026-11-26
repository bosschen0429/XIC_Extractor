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
    raw_stem: str
    subject_kind: Literal["peak_hypothesis"]
    subject_id: str
    physical_peaks: tuple[PeakObservation, ...]


def group_member_subject_from_peak_hypothesis(
    *,
    sample: SampleMetadata,
    hypothesis: MeasuredPeakHypothesis,
) -> CrossSampleGroupMemberSubject:
    return CrossSampleGroupMemberSubject(
        raw_stem=sample.raw_stem,
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
        if member.raw_stem in samples:
            raise CrossSampleContractError(
                "group has two identity-bearing members for one canonical sample"
            )
        if physical.intersection(member.physical_peaks):
            raise CrossSampleContractError("group duplicates a physical observation")
        samples.add(member.raw_stem)
        physical.update(member.physical_peaks)
        members.append(member)
    return tuple(sorted(members, key=lambda item: item.raw_stem))


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
