from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult

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
    precursor_mz: float
    owner_apex_rt: float
    owner_peak_start_rt: float
    owner_peak_end_rt: float
    owner_area: float
    owner_height: float
    primary_identity_event: IdentityEvent | None
    supporting_events: tuple[IdentityEvent, ...]
    identity_conflict: bool
    assignment_reason: str
    selected_integration: IntegrationResult | None = None
    quantification_eligible: bool = True
    quantification_exclusion_reason: str = ""
    cross_sample_member_subject: CrossSampleGroupMemberSubject | None = None
    cid_nl_composite_assessment: CidNlMethodAssessmentRef | None = None
    is_provisional: bool = False

    def __post_init__(self) -> None:
        integration = self.selected_integration
        apex = self.owner_apex_rt
        left = self.owner_peak_start_rt
        right = self.owner_peak_end_rt
        if integration is not None:
            apex = integration.rt_apex_min
            left = integration.rt_left_min
            right = integration.rt_right_min
        if not (left < apex < right):
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

    @property
    def event_candidate_ids(self) -> tuple[str, ...]:
        return tuple(event.candidate_id for event in self.all_events)


@dataclass(frozen=True)
class OwnerAssignment:
    candidate_id: str
    owner_id: str | None
    assignment_status: OwnerAssignmentStatus
    reason: str


@dataclass(frozen=True)
class AmbiguousOwnerRecord:
    ambiguity_id: str
    sample_stem: str
    candidate_ids: tuple[str, ...]
    reason: str
    neutral_loss_tag: str | None = None
    precursor_mz: float | None = None
    apex_rt: float | None = None
    product_mz: float | None = None
    observed_neutral_loss_da: float | None = None
