"""CID-NL cell-group context and shared immutable owner facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.processing_identity import (
    ProcessingAttemptIdentity,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

TerminalObservationVariant: TypeAlias = Literal[
    "observed",
    "searched_no_observation",
]


class CellObservationContractError(ValueError):
    """Raised when one assessment cannot enter an existing group cell exactly."""


def select_cid_nl_group_backfill_subject(
    group: CrossSamplePeakGroupHypothesis,
    subjects: tuple[CidNlMethodSubject, ...],
) -> CidNlMethodSubject:
    """Select the same canonical search subject before and after cell assembly."""
    center = group.group_center_mz
    if center is None:
        raise CellObservationContractError(
            "group backfill subject requires a finite group center m/z"
        )
    return min(
        subjects,
        key=lambda subject: (
            abs(subject.precursor_mz - center) / max(abs(center), 1e-12),
            subject.method_subject_id,
        ),
    )


@dataclass(frozen=True, slots=True)
class CidNlCellGroupSupport:
    """One exact SUPPORT closure that owns membership in a provisional group."""

    sample: ResolvedCanonicalSampleIdentity
    assessment: CidNlMethodAssessmentRef
    hypothesis: PeakHypothesis


@dataclass(frozen=True, slots=True)
class CidNlCellGroupContext:
    """Method-subject proof for one group and every SUPPORT owner that formed it."""

    group: CrossSamplePeakGroupHypothesis
    supports: tuple[CidNlCellGroupSupport, ...]
    config: AlignmentConfig
    processing_attempt: ProcessingAttemptIdentity
    rt_frame: RtFrame
    sample_universe: frozenset[str] | None = None
    _method_subjects: tuple[CidNlMethodSubject, ...] = field(
        init=False, repr=False, compare=False
    )
    _method_subject: CidNlMethodSubject = field(init=False, repr=False, compare=False)
    _owner_ids: tuple[str, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        subjects = tuple(
            sorted(
                {
                    support.assessment.content.method_subject.method_subject_id: (
                        support.assessment.content.method_subject
                    )
                    for support in self.supports
                }.values(),
                key=lambda subject: subject.method_subject_id,
            )
        )
        object.__setattr__(self, "_method_subjects", subjects)
        object.__setattr__(
            self,
            "_method_subject",
            select_cid_nl_group_backfill_subject(self.group, subjects),
        )
        object.__setattr__(self, "_owner_ids", tuple(sorted(self.group.owner_ids)))

    @property
    def group_identity(self) -> CrossSampleGroupIdentity:
        return self.group.membership

    @property
    def method_subjects(self) -> tuple[CidNlMethodSubject, ...]:
        return self._method_subjects

    @property
    def method_subject(self) -> CidNlMethodSubject:
        return self._method_subject

    @property
    def method_subject_id(self) -> str:
        return self.method_subject.method_subject_id

    @property
    def target_label(self) -> str | None:
        target = self.method_subject.target
        return None if target is None else target.lookup_label

    @property
    def sorted_owner_ids(self) -> tuple[str, ...]:
        return self._owner_ids
