from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from xic_extractor.alignment.cross_sample.identity_models import (
    CrossSampleGroupMemberSubject,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
)
from xic_extractor.peak_detection.ms1_shape_identity import LocalPeakShape
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.peak_detection.peak_hypothesis_identity import MeasuredPeakHypothesis

OwnerAssignmentStatus = Literal["primary", "supporting", "ambiguous", "unresolved"]


MS1_OBSERVATION_ASSIGNMENT = "ms1_observation_hypothesis"


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
    source_scans: tuple[tuple[int, float], ...] = ()


@dataclass(frozen=True)
class SampleLocalMS1Owner:
    hypothesis: MeasuredPeakHypothesis
    sample_stem: str
    raw_file: str
    primary_identity_event: IdentityEvent | None
    supporting_events: tuple[IdentityEvent, ...]
    assignment_reason: str
    quantification_eligible: bool = True
    quantification_exclusion_reason: str = ""
    assessment: CidNlMethodAssessment | None = None
    peak_shape: LocalPeakShape | None = None

    def __post_init__(self) -> None:
        peak = self.peak
        if peak.integration is None or not (
            peak.rt_left_min < peak.rt_apex_min < peak.rt_right_min
        ):
            object.__setattr__(self, "quantification_eligible", False)
            if not self.quantification_exclusion_reason:
                object.__setattr__(
                    self,
                    "quantification_exclusion_reason",
                    "unresolved_component_area"
                    if peak.integration is None
                    else "owner_apex_on_integration_boundary",
                )

    @property
    def owner_id(self) -> str:
        return self.hypothesis.hypothesis_id

    @property
    def peak(self) -> PeakObservation:
        return self.hypothesis.peak

    @property
    def cross_sample_member_subject(self) -> CrossSampleGroupMemberSubject:
        return CrossSampleGroupMemberSubject(
            sample_key=self.peak.sample_key,
            subject_kind="peak_hypothesis",
            subject_id=self.owner_id,
            physical_peaks=(self.peak.physical_identity,),
        )

    @property
    def cid_nl_composite_assessment(self) -> CidNlMethodAssessment | None:
        if (
            self.assessment is not None
            and self.primary_identity_event is not None
            and self.assessment.method_subject.composition is not None
        ):
            return self.assessment
        return None

    @property
    def neutral_loss_tag(self) -> str:
        if self.primary_identity_event is None:
            return ""
        return self.primary_identity_event.neutral_loss_tag
