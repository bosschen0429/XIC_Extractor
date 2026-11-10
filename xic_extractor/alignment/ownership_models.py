from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
)
from xic_extractor.peak_detection.ms1_shape_identity import LocalPeakShape
from xic_extractor.peak_detection.observations import PeakObservation

if TYPE_CHECKING:
    from xic_extractor.alignment.cross_sample.peak_groups import (
        CrossSampleGroupMemberSubject,
    )

OwnerAssignmentStatus = Literal["primary", "supporting", "ambiguous", "unresolved"]


@dataclass(frozen=True)
class IdentityEvent:
    candidate_id: str
    sample_stem: str
    raw_file: str
    neutral_loss_tag: str
    precursor_mz: float
    product_mz: float
    observed_neutral_loss_da: float
    seed_rt: float
    evidence_score: int
    seed_event_count: int


@dataclass(frozen=True)
class SampleLocalMS1Owner:
    owner_id: str
    sample_stem: str
    raw_file: str
    peak: PeakObservation
    primary_identity_event: IdentityEvent | None
    supporting_events: tuple[IdentityEvent, ...]
    assignment_reason: str
    quantification_eligible: bool = True
    quantification_exclusion_reason: str = ""
    cross_sample_member_subject: CrossSampleGroupMemberSubject | None = None
    cid_nl_composite_assessment: CidNlMethodAssessment | None = None
    peak_shape: LocalPeakShape | None = None

    def __post_init__(self) -> None:
        integration = self.peak.integration
        if not (
            integration.rt_left_min < integration.rt_apex_min < integration.rt_right_min
        ):
            object.__setattr__(self, "quantification_eligible", False)
            if not self.quantification_exclusion_reason:
                object.__setattr__(
                    self,
                    "quantification_exclusion_reason",
                    "owner_apex_on_integration_boundary",
                )

    @property
    def all_events(self) -> tuple[IdentityEvent, ...]:
        if self.primary_identity_event is None:
            return self.supporting_events
        return (self.primary_identity_event, *self.supporting_events)

    @property
    def neutral_loss_tag(self) -> str:
        if self.primary_identity_event is None:
            return ""
        return self.primary_identity_event.neutral_loss_tag
