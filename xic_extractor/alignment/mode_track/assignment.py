"""Shadow-only owner for injection-local ModeTrack assignment evidence."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import InitVar, dataclass, field
from typing import Literal, final

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.drift_evidence import (
    LocalTrajectoryAssessment,
    LocalTrajectoryStatus,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    validate_cid_nl_assessment_hypothesis_closure,
)
from xic_extractor.alignment.mode_track.family_context import (
    FamilyContextRef,
    validate_family_context_ref_for_consumption,
)
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentContractError,
    CidNlMethodAssessmentRef,
    validate_cid_nl_method_assessment_binding,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_MS1_OBSERVATION,
    PeakHypothesisContractError,
    validate_cid_nl_peak_hypothesis_binding,
    validate_ms1_observation_peak_hypothesis_binding,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    IdentityContractError,
    ProcessingOwnerReferences,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "mode_track_assignment_v2"
BATCH_SCHEMA_VERSION = "mode_track_assignment_batch_v2"
MODEL_SET_SCHEMA_VERSION = "mode_track_model_set_v3"
VALIDATION_STATUS = "shadow_ready"
PRODUCT_AUTHORITY = "none"

AssignmentState = Literal[
    "uniquely_assigned",
    "ambiguous_multiple_tracks",
    "out_of_model",
    "not_observed",
    "technical_unassessable",
]
CandidateObservationClass = Literal["direct_support", "non_training"]
PredictionStatus = Literal[
    "predicted",
    "sparse_anchors",
    "trajectory_unassessable",
    "unsupported_interpolation",
    "unsupported_extrapolation",
]

_MODE_TRACK_MINT_TOKEN = object()


class ModeTrackContractError(ValueError):
    """Raised when an M8 owner reference is crossed, forged, or incomplete."""


class _ModeTrackAssignmentUnavailableError(RuntimeError):
    """Signal that complete owner facts could not produce an assignment batch."""


class _RuntimeFinal:
    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if _RuntimeFinal not in cls.__bases__:
            raise TypeError(f"{cls.__bases__[0].__name__} is final")


@final
@dataclass(frozen=True, slots=True)
class ModeTrackTrajectoryPolicy(_RuntimeFinal):
    """Explicit lane policy; M8 intentionally owns no universal thresholds."""

    trajectory_policy_version: str
    band_policy_version: str
    prediction_half_width_min: float
    max_interpolation_gap: int
    max_extrapolation_gap: int

    def __post_init__(self) -> None:
        _required_text(
            self.trajectory_policy_version,
            field="trajectory_policy_version",
        )
        _required_text(self.band_policy_version, field="band_policy_version")
        half_width = _positive_float(
            self.prediction_half_width_min,
            field="prediction_half_width_min",
        )
        if (
            not isinstance(self.prediction_half_width_min, float)
            or half_width != self.prediction_half_width_min
        ):
            raise ModeTrackContractError(
                "prediction_half_width_min must use a canonical float"
            )
        _nonnegative_int(self.max_interpolation_gap, field="max_interpolation_gap")
        _nonnegative_int(self.max_extrapolation_gap, field="max_extrapolation_gap")


@final
@dataclass(frozen=True, slots=True)
class ModeTrackSupportAnchor(_RuntimeFinal):
    """One exact direct-SUPPORT training fact assigned to one ModeTrack."""

    anchor_id: str
    group_hypothesis_id: str
    processing_request_id: str
    processing_attempt_id: str
    canonical_sample_identity_id: str
    acquisition_source_id: str
    injection_order: int
    apex_rt_min: float
    peak_hypothesis_id: str
    peak_observation_content_id: str
    support_assessment_artifact_content_id: str
    evidence_artifact_ids: tuple[str, ...]
    source_assignment_id: str | None
    sample: ResolvedCanonicalSampleIdentity = field(compare=False, repr=False)
    acquisition_source: AcquisitionSourceIdentity = field(compare=False, repr=False)
    peak_hypothesis: PeakHypothesis = field(compare=False, repr=False)
    direct_support_assessment: CidNlMethodAssessmentRef = field(
        compare=False,
        repr=False,
    )
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _MODE_TRACK_MINT_TOKEN:
            raise ModeTrackContractError(
                "ModeTrackSupportAnchor must be minted by the M8 owner"
            )
        _required_text(self.group_hypothesis_id, field="group_hypothesis_id")
        _required_text(self.processing_request_id, field="processing_request_id")
        _required_text(self.processing_attempt_id, field="processing_attempt_id")
        _positive_int(self.injection_order, field="injection_order")
        _finite_float(self.apex_rt_min, field="apex_rt_min")
        _require_canonical_texts(
            self.evidence_artifact_ids,
            field="evidence_artifact_ids",
        )
        if (
            self.canonical_sample_identity_id != self.sample.identity.sample_identity_id
            or self.acquisition_source_id
            != self.acquisition_source.acquisition_source_id
            or self.peak_hypothesis_id != self.peak_hypothesis.hypothesis_id
            or self.peak_observation_content_id
            != _peak_observation_content_id(self.peak_hypothesis)
        ):
            raise ModeTrackContractError(
                "support anchor contains crossed sample, acquisition, or "
                "hypothesis refs"
            )
        if self.apex_rt_min != self.peak_hypothesis.integration.rt_apex_min:
            raise ModeTrackContractError("support anchor apex RT was substituted")
        expected_assessment_content_id = (
            self.direct_support_assessment.artifact.content_identity.artifact_content_id
        )
        if (
            self.support_assessment_artifact_content_id
            != expected_assessment_content_id
        ):
            raise ModeTrackContractError(
                "support anchor assessment artifact identity was substituted"
            )
        if self.source_assignment_id is not None:
            _required_text(self.source_assignment_id, field="source_assignment_id")
        expected = _anchor_identity_payload(
            group_hypothesis_id=self.group_hypothesis_id,
            processing_request_id=self.processing_request_id,
            processing_attempt_id=self.processing_attempt_id,
            canonical_sample_identity_id=self.canonical_sample_identity_id,
            acquisition_source_id=self.acquisition_source_id,
            injection_order=self.injection_order,
            apex_rt_min=self.apex_rt_min,
            peak_hypothesis_id=self.peak_hypothesis_id,
            peak_observation_content_id=self.peak_observation_content_id,
            support_assessment_artifact_content_id=(
                self.support_assessment_artifact_content_id
            ),
            evidence_artifact_ids=self.evidence_artifact_ids,
            source_assignment_id=self.source_assignment_id,
        )
        if self.anchor_id != _typed_digest("mode-track-anchor:", expected):
            raise ModeTrackContractError("anchor_id mismatches canonical owner content")


@final
@dataclass(frozen=True, slots=True)
class ModeTrackModel(_RuntimeFinal):
    """One existing group identity plus its direct-SUPPORT trajectory anchors."""

    group_hypothesis_id: str
    ordering_reference_rt_min: float
    anchors: tuple[ModeTrackSupportAnchor, ...]

    def __post_init__(self) -> None:
        _required_text(self.group_hypothesis_id, field="group_hypothesis_id")
        ordering_reference = _finite_float(
            self.ordering_reference_rt_min,
            field="ordering_reference_rt_min",
        )
        if (
            not isinstance(self.ordering_reference_rt_min, float)
            or ordering_reference != self.ordering_reference_rt_min
        ):
            raise ModeTrackContractError(
                "ordering_reference_rt_min must use a canonical float"
            )
        canonical = _canonical_anchors(self.anchors)
        if canonical != self.anchors:
            raise ModeTrackContractError("track anchors must be canonical-sorted")
        if any(
            anchor.group_hypothesis_id != self.group_hypothesis_id
            for anchor in self.anchors
        ):
            raise ModeTrackContractError("track contains an anchor for another group")
        injection_orders = tuple(anchor.injection_order for anchor in self.anchors)
        if len(injection_orders) != len(set(injection_orders)):
            raise ModeTrackContractError(
                "one ModeTrack may retain at most one anchor per injection"
            )


@final
@dataclass(frozen=True, slots=True)
class ModeTrackModelSet(_RuntimeFinal):
    """One fit-once family snapshot used by all sample assignments."""

    model_set_id: str
    family_context_id: str
    processing_request_id: str
    processing_attempt_id: str
    policy: ModeTrackTrajectoryPolicy
    tracks: tuple[ModeTrackModel, ...]
    lineage_refs: tuple[str, ...]
    family_context: FamilyContextRef = field(compare=False, repr=False)
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _MODE_TRACK_MINT_TOKEN:
            raise ModeTrackContractError(
                "ModeTrackModelSet must be minted by the M8 owner"
            )
        if self.family_context_id != self.family_context.family_context_id:
            raise ModeTrackContractError("model set Family context was substituted")
        _required_text(self.processing_request_id, field="processing_request_id")
        _required_text(self.processing_attempt_id, field="processing_attempt_id")
        canonical_tracks = tuple(
            sorted(self.tracks, key=lambda item: item.group_hypothesis_id)
        )
        if self.tracks != canonical_tracks:
            raise ModeTrackContractError("model tracks must be canonical-sorted")
        if tuple(track.group_hypothesis_id for track in self.tracks) != (
            self.family_context.group_hypothesis_ids
        ):
            raise ModeTrackContractError(
                "model tracks must close the exact Family membership"
            )
        ordering_references = {
            track.group_hypothesis_id: _finite_float(
                track.group_center_rt,
                field="family_context.mode_tracks.group_center_rt",
            )
            for track in self.family_context.mode_tracks
        }
        if any(
            track.ordering_reference_rt_min
            != ordering_references[track.group_hypothesis_id]
            for track in self.tracks
        ):
            raise ModeTrackContractError(
                "model track ordering reference was substituted"
            )
        _require_canonical_texts(self.lineage_refs, field="lineage_refs")
        _validate_shadow_authority(self.validation_status, self.product_authority)
        expected = _model_set_identity_payload(
            family_context_id=self.family_context_id,
            processing_request_id=self.processing_request_id,
            processing_attempt_id=self.processing_attempt_id,
            policy=self.policy,
            tracks=self.tracks,
            lineage_refs=self.lineage_refs,
        )
        if self.model_set_id != _typed_digest("mode-track-model-set:", expected):
            raise ModeTrackContractError(
                "model_set_id mismatches canonical owner content"
            )


@final
@dataclass(frozen=True, slots=True)
class ModeTrackCandidateObservation(_RuntimeFinal):
    """One owner-backed PeakHypothesis eligible for shadow assignment."""

    candidate_observation_id: str
    processing_request_id: str
    processing_attempt_id: str
    canonical_sample_identity_id: str
    acquisition_source_id: str
    injection_order: int
    peak_hypothesis_id: str
    peak_observation_content_id: str
    apex_rt_min: float
    observation_class: CandidateObservationClass
    direct_support_assessment_artifact_content_id: str | None
    sample: ResolvedCanonicalSampleIdentity = field(compare=False, repr=False)
    acquisition_source: AcquisitionSourceIdentity = field(compare=False, repr=False)
    peak_hypothesis: PeakHypothesis = field(compare=False, repr=False)
    direct_support_assessment: CidNlMethodAssessmentRef | None = field(
        compare=False,
        repr=False,
    )
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _MODE_TRACK_MINT_TOKEN:
            raise ModeTrackContractError(
                "ModeTrackCandidateObservation must be minted by the M8 owner"
            )
        if self.observation_class not in {"direct_support", "non_training"}:
            raise ModeTrackContractError("unsupported candidate observation class")
        if self.observation_class == "direct_support":
            if self.direct_support_assessment is None:
                raise ModeTrackContractError(
                    "direct_support candidate requires its exact SUPPORT assessment"
                )
        elif self.direct_support_assessment is not None:
            raise ModeTrackContractError(
                "non_training candidate cannot retain a SUPPORT assessment"
            )
        _required_text(self.processing_request_id, field="processing_request_id")
        _required_text(self.processing_attempt_id, field="processing_attempt_id")
        _positive_int(self.injection_order, field="injection_order")
        if (
            self.canonical_sample_identity_id != self.sample.identity.sample_identity_id
            or self.acquisition_source_id
            != self.acquisition_source.acquisition_source_id
            or self.peak_hypothesis_id != self.peak_hypothesis.hypothesis_id
            or self.peak_observation_content_id
            != _peak_observation_content_id(self.peak_hypothesis)
        ):
            raise ModeTrackContractError(
                "candidate contains crossed sample, acquisition, or hypothesis refs"
            )
        if self.apex_rt_min != self.peak_hypothesis.integration.rt_apex_min:
            raise ModeTrackContractError("candidate apex RT was substituted")
        expected_assessment_content_id = (
            None
            if self.direct_support_assessment is None
            else (
                self.direct_support_assessment.artifact.content_identity.artifact_content_id
            )
        )
        if (
            self.direct_support_assessment_artifact_content_id
            != expected_assessment_content_id
        ):
            raise ModeTrackContractError(
                "candidate SUPPORT assessment artifact identity was substituted"
            )
        expected = _candidate_identity_payload(
            processing_request_id=self.processing_request_id,
            processing_attempt_id=self.processing_attempt_id,
            canonical_sample_identity_id=self.canonical_sample_identity_id,
            acquisition_source_id=self.acquisition_source_id,
            injection_order=self.injection_order,
            peak_hypothesis_id=self.peak_hypothesis_id,
            peak_observation_content_id=self.peak_observation_content_id,
            apex_rt_min=self.apex_rt_min,
            observation_class=self.observation_class,
            direct_support_assessment_artifact_content_id=(
                self.direct_support_assessment_artifact_content_id
            ),
        )
        if self.candidate_observation_id != _typed_digest(
            "mode-track-candidate:",
            expected,
        ):
            raise ModeTrackContractError(
                "candidate_observation_id mismatches canonical owner content"
            )


@final
@dataclass(frozen=True, slots=True)
class ModeTrackRepairAssignmentObservation(_RuntimeFinal):
    """Identity-neutral repaired physical observation for one assignment pass."""

    candidate_observation_id: str
    processing_request_id: str
    processing_attempt_id: str
    canonical_sample_identity_id: str
    acquisition_source_id: str
    injection_order: int
    repaired_member_subject_id: str
    physical_observation_id: str
    apex_rt_min: float
    repair_receipt_id: str
    source_observation_id: str
    sample: ResolvedCanonicalSampleIdentity = field(compare=False, repr=False)
    acquisition_source: AcquisitionSourceIdentity = field(compare=False, repr=False)
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _MODE_TRACK_MINT_TOKEN:
            raise ModeTrackContractError(
                "ModeTrackRepairAssignmentObservation must be minted by ModeTrack"
            )
        if (
            self.canonical_sample_identity_id
            != self.sample.identity.sample_identity_id
            or self.acquisition_source_id
            != self.acquisition_source.acquisition_source_id
        ):
            raise ModeTrackContractError(
                "repair assignment observation crossed owner physical facts"
            )
        for field_name in (
            "repaired_member_subject_id",
            "physical_observation_id",
            "repair_receipt_id",
            "source_observation_id",
        ):
            _required_text(getattr(self, field_name), field=field_name)
        _positive_int(self.injection_order, field="injection_order")
        _finite_float(self.apex_rt_min, field="apex_rt_min")
        expected = _repair_assignment_observation_payload(
            processing_request_id=self.processing_request_id,
            processing_attempt_id=self.processing_attempt_id,
            canonical_sample_identity_id=self.canonical_sample_identity_id,
            acquisition_source_id=self.acquisition_source_id,
            injection_order=self.injection_order,
            repaired_member_subject_id=self.repaired_member_subject_id,
            physical_observation_id=self.physical_observation_id,
            apex_rt_min=self.apex_rt_min,
            repair_receipt_id=self.repair_receipt_id,
            source_observation_id=self.source_observation_id,
        )
        if self.candidate_observation_id != _typed_digest(
            "mode-track-repair-assignment-observation:",
            expected,
        ):
            raise ModeTrackContractError(
                "repair assignment observation identity mismatches owner content"
            )

    @property
    def peak_hypothesis_id(self) -> None:
        return None

    @property
    def peak_observation_content_id(self) -> str:
        return self.physical_observation_id

    @property
    def observation_class(self) -> Literal["non_training"]:
        return "non_training"

    @property
    def direct_support_assessment_artifact_content_id(self) -> None:
        return None

    @property
    def peak_hypothesis(self) -> None:
        return None

    @property
    def direct_support_assessment(self) -> None:
        return None


ModeTrackAssignmentObservation = (
    ModeTrackCandidateObservation | ModeTrackRepairAssignmentObservation
)


@final
@dataclass(frozen=True, slots=True)
class ModeTrackPredictionAssessment(_RuntimeFinal):
    """One track's injection-local prediction or explicit exclusion reason."""

    group_hypothesis_id: str
    status: PredictionStatus
    predicted_rt_min: float | None
    prediction_interval_min: tuple[float, float] | None
    anchor_refs: tuple[str, ...]
    support_artifact_content_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _required_text(self.group_hypothesis_id, field="group_hypothesis_id")
        if self.status not in {
            "predicted",
            "sparse_anchors",
            "trajectory_unassessable",
            "unsupported_interpolation",
            "unsupported_extrapolation",
        }:
            raise ModeTrackContractError("unsupported prediction status")
        provisional_geometry = (
            self.status == "predicted"
            and "provisional_group_geometry" in self.reason_codes
        )
        allow_empty_support = self.status == "sparse_anchors" or provisional_geometry
        _require_canonical_texts(
            self.anchor_refs,
            field="anchor_refs",
            allow_empty=allow_empty_support,
        )
        _require_canonical_texts(
            self.support_artifact_content_ids,
            field="support_artifact_content_ids",
            allow_empty=allow_empty_support,
        )
        _require_canonical_texts(self.reason_codes, field="reason_codes")
        if self.status == "predicted":
            predicted = _finite_float(
                self.predicted_rt_min,
                field="predicted_rt_min",
            )
            interval = _interval(
                self.prediction_interval_min,
                field="prediction_interval_min",
            )
            if not interval[0] <= predicted <= interval[1]:
                raise ModeTrackContractError(
                    "predicted RT must lie inside its prediction interval"
                )
        elif (
            self.predicted_rt_min is not None
            or self.prediction_interval_min is not None
        ):
            raise ModeTrackContractError(
                "unavailable prediction cannot carry a numeric interval"
            )


@final
@dataclass(frozen=True, slots=True)
class ModeTrackAssignment(_RuntimeFinal):
    """One immutable, shadow-only candidate-to-track assignment fact."""

    assignment_id: str
    family_context_id: str
    processing_request_id: str
    processing_attempt_id: str
    canonical_sample_identity_id: str
    acquisition_source_id: str
    injection_order: int
    candidate_observation_id: str | None
    peak_hypothesis_id: str | None
    peak_observation_content_id: str | None
    candidate_observation_class: CandidateObservationClass | None
    direct_support_assessment_artifact_content_id: str | None
    group_hypothesis_id: str | None
    state: AssignmentState
    reason_codes: tuple[str, ...]
    prediction_assessments: tuple[ModeTrackPredictionAssessment, ...]
    trajectory_evidence_refs: tuple[str, ...]
    lineage_refs: tuple[str, ...]
    sample: ResolvedCanonicalSampleIdentity = field(compare=False, repr=False)
    acquisition_source: AcquisitionSourceIdentity = field(compare=False, repr=False)
    candidate_observation: ModeTrackAssignmentObservation | None = field(
        compare=False,
        repr=False,
    )
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _MODE_TRACK_MINT_TOKEN:
            raise ModeTrackContractError(
                "ModeTrackAssignment must be minted by the M8 owner"
            )
        if self.state not in {
            "uniquely_assigned",
            "ambiguous_multiple_tracks",
            "out_of_model",
            "not_observed",
            "technical_unassessable",
        }:
            raise ModeTrackContractError("unsupported ModeTrack assignment state")
        _required_text(self.processing_request_id, field="processing_request_id")
        _required_text(self.processing_attempt_id, field="processing_attempt_id")
        _positive_int(self.injection_order, field="injection_order")
        _require_canonical_texts(self.reason_codes, field="reason_codes")
        _require_canonical_texts(
            self.trajectory_evidence_refs,
            field="trajectory_evidence_refs",
            allow_empty=True,
        )
        _require_canonical_texts(self.lineage_refs, field="lineage_refs")
        if (
            self.canonical_sample_identity_id != self.sample.identity.sample_identity_id
            or self.acquisition_source_id
            != self.acquisition_source.acquisition_source_id
        ):
            raise ModeTrackContractError(
                "assignment contains crossed sample or acquisition refs"
            )
        if type(self.prediction_assessments) is not tuple or any(
            type(item) is not ModeTrackPredictionAssessment
            for item in self.prediction_assessments
        ):
            raise ModeTrackContractError("prediction assessments must be typed refs")
        for prediction in self.prediction_assessments:
            ModeTrackPredictionAssessment.__post_init__(prediction)
        prediction_ids = tuple(
            item.group_hypothesis_id for item in self.prediction_assessments
        )
        if prediction_ids != tuple(sorted(set(prediction_ids))):
            raise ModeTrackContractError(
                "prediction assessments must be unique and canonical-sorted"
            )
        if self.state == "uniquely_assigned":
            if self.group_hypothesis_id is None or self.candidate_observation is None:
                raise ModeTrackContractError(
                    "unique assignment requires one track and owner observation"
                )
        elif self.group_hypothesis_id is not None:
            raise ModeTrackContractError(
                "non-unique assignment cannot grant a ModeTrack identity"
            )
        if self.state == "not_observed":
            if (
                self.candidate_observation_id is not None
                or self.peak_hypothesis_id is not None
                or self.peak_observation_content_id is not None
                or self.candidate_observation is not None
                or self.candidate_observation_class is not None
                or self.direct_support_assessment_artifact_content_id is not None
            ):
                raise ModeTrackContractError(
                    "not_observed assignment cannot carry candidate evidence"
                )
        else:
            if (
                self.candidate_observation_id is None
                or self.peak_observation_content_id is None
                or self.candidate_observation is None
                or self.candidate_observation_class is None
            ):
                raise ModeTrackContractError(
                    "observed assignment state requires its exact candidate"
                )
            _required_text(
                self.candidate_observation_id,
                field="candidate_observation_id",
            )
            candidate = self.candidate_observation
            if (
                type(candidate)
                not in {
                    ModeTrackCandidateObservation,
                    ModeTrackRepairAssignmentObservation,
                }
                or self.candidate_observation_id != candidate.candidate_observation_id
                or self.peak_hypothesis_id != candidate.peak_hypothesis_id
                or self.peak_observation_content_id
                != candidate.peak_observation_content_id
                or self.candidate_observation_class != candidate.observation_class
                or self.direct_support_assessment_artifact_content_id
                != candidate.direct_support_assessment_artifact_content_id
                or self.processing_request_id != candidate.processing_request_id
                or self.processing_attempt_id != candidate.processing_attempt_id
            ):
                raise ModeTrackContractError(
                    "assignment candidate identity was substituted"
                )
        _validate_shadow_authority(self.validation_status, self.product_authority)
        expected = _assignment_identity_payload(self)
        if self.assignment_id != _typed_digest(
            "mode-track-assignment:",
            expected,
        ):
            raise ModeTrackContractError(
                "assignment_id mismatches canonical owner content"
            )

    @property
    def peak_hypothesis(self) -> PeakHypothesis | None:
        candidate = self.candidate_observation
        return None if candidate is None else candidate.peak_hypothesis

    @property
    def direct_support_assessment(self) -> CidNlMethodAssessmentRef | None:
        candidate = self.candidate_observation
        return None if candidate is None else candidate.direct_support_assessment


@final
@dataclass(frozen=True, slots=True)
class ModeTrackAssignmentBatch(_RuntimeFinal):
    """Exactly one deterministic assignment pass for one sample and model set."""

    assignment_batch_id: str
    family_context_id: str
    model_set_id: str
    processing_request_id: str
    processing_attempt_id: str
    canonical_sample_identity_id: str
    acquisition_source_id: str
    injection_order: int
    trajectory_policy_version: str
    band_policy_version: str
    assignments: tuple[ModeTrackAssignment, ...]
    sample: ResolvedCanonicalSampleIdentity = field(compare=False, repr=False)
    acquisition_source: AcquisitionSourceIdentity = field(compare=False, repr=False)
    validation_status: str = VALIDATION_STATUS
    product_authority: str = PRODUCT_AUTHORITY
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _MODE_TRACK_MINT_TOKEN:
            raise ModeTrackContractError(
                "ModeTrackAssignmentBatch must be minted by the M8 owner"
            )
        if not self.assignments:
            raise ModeTrackContractError("assignment batch cannot be empty")
        _required_text(self.processing_request_id, field="processing_request_id")
        _required_text(self.processing_attempt_id, field="processing_attempt_id")
        _positive_int(self.injection_order, field="injection_order")
        if (
            self.canonical_sample_identity_id != self.sample.identity.sample_identity_id
            or self.acquisition_source_id
            != self.acquisition_source.acquisition_source_id
        ):
            raise ModeTrackContractError("batch contains crossed live owner refs")
        canonical = _canonical_assignments(self.assignments)
        if self.assignments != canonical:
            raise ModeTrackContractError("assignments must be canonical-sorted")
        if any(
            item.family_context_id != self.family_context_id
            or item.processing_request_id != self.processing_request_id
            or item.processing_attempt_id != self.processing_attempt_id
            or item.canonical_sample_identity_id != self.canonical_sample_identity_id
            or item.acquisition_source_id != self.acquisition_source_id
            or item.injection_order != self.injection_order
            or item.sample != self.sample
            or item.acquisition_source != self.acquisition_source
            or (
                item.candidate_observation is not None
                and (
                    item.candidate_observation.sample != self.sample
                    or item.candidate_observation.acquisition_source
                    != self.acquisition_source
                    or item.candidate_observation.injection_order
                    != self.injection_order
                )
            )
            for item in self.assignments
        ):
            raise ModeTrackContractError(
                "assignment batch contains crossed Family or sample references"
            )
        assignment_ids = tuple(item.assignment_id for item in self.assignments)
        if len(assignment_ids) != len(set(assignment_ids)):
            raise ModeTrackContractError("assignment IDs must be unique within a batch")
        hypothesis_ids = tuple(
            item.peak_hypothesis_id
            for item in self.assignments
            if item.peak_hypothesis_id is not None
        )
        if len(hypothesis_ids) != len(set(hypothesis_ids)):
            raise ModeTrackContractError(
                "each PeakHypothesis may be assigned at most once per sample"
            )
        observation_ids = tuple(
            item.peak_observation_content_id
            for item in self.assignments
            if item.peak_observation_content_id is not None
        )
        if len(observation_ids) != len(set(observation_ids)):
            raise ModeTrackContractError(
                "each physical peak observation may be assigned at most once per sample"
            )
        _validate_shadow_authority(self.validation_status, self.product_authority)
        expected = _batch_identity_payload(self)
        if self.assignment_batch_id != _typed_digest(
            "mode-track-assignment-batch:",
            expected,
        ):
            raise ModeTrackContractError(
                "assignment_batch_id mismatches canonical owner content"
            )


@final
@dataclass(frozen=True, slots=True)
class ModeTrackFitOnceSampleSource(_RuntimeFinal):
    """One complete, owner-bound sample input retained for a later final pass."""

    canonical_sample_identity_id: str
    acquisition_source_id: str
    injection_order: int
    candidate_observation_ids: tuple[str, ...]
    trajectory_status: LocalTrajectoryStatus
    trajectory_reason_codes: tuple[str, ...]
    trajectory_evidence_refs: tuple[str, ...]
    sample: ResolvedCanonicalSampleIdentity = field(compare=False, repr=False)
    candidates: tuple[ModeTrackCandidateObservation, ...] = field(
        compare=False,
        repr=False,
    )
    trajectory_assessment: LocalTrajectoryAssessment = field(
        compare=False,
        repr=False,
    )
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _MODE_TRACK_MINT_TOKEN:
            raise ModeTrackContractError(
                "ModeTrackFitOnceSampleSource must be minted by the M8 owner"
            )
        _required_text(
            self.canonical_sample_identity_id,
            field="canonical_sample_identity_id",
        )
        _required_text(self.acquisition_source_id, field="acquisition_source_id")
        _positive_int(self.injection_order, field="injection_order")
        _require_canonical_texts(
            self.candidate_observation_ids,
            field="candidate_observation_ids",
            allow_empty=True,
        )
        _require_canonical_texts(
            self.trajectory_reason_codes,
            field="trajectory_reason_codes",
        )
        _require_canonical_texts(
            self.trajectory_evidence_refs,
            field="trajectory_evidence_refs",
            allow_empty=True,
        )
        if type(self.sample) is not ResolvedCanonicalSampleIdentity:
            raise ModeTrackContractError("sample must be owner-resolved")
        if (
            self.canonical_sample_identity_id
            != self.sample.identity.sample_identity_id
        ):
            raise ModeTrackContractError("sample source identity was substituted")
        if type(self.candidates) is not tuple or any(
            type(candidate) is not ModeTrackCandidateObservation
            for candidate in self.candidates
        ):
            raise ModeTrackContractError(
                "sample source candidates must be owner-minted immutable refs"
            )
        canonical_candidates = tuple(
            sorted(
                self.candidates,
                key=lambda item: item.candidate_observation_id,
            )
        )
        if self.candidates != canonical_candidates:
            raise ModeTrackContractError(
                "sample source candidates must be canonical-sorted"
            )
        candidate_ids = tuple(
            candidate.candidate_observation_id for candidate in self.candidates
        )
        if self.candidate_observation_ids != candidate_ids:
            raise ModeTrackContractError(
                "sample source candidate IDs do not close retained candidates"
            )
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ModeTrackContractError("sample source candidate IDs must be unique")
        hypothesis_ids = tuple(
            candidate.peak_hypothesis_id for candidate in self.candidates
        )
        if len(hypothesis_ids) != len(set(hypothesis_ids)):
            raise ModeTrackContractError(
                "sample source candidate hypotheses must be unique"
            )
        observation_ids = tuple(
            candidate.peak_observation_content_id for candidate in self.candidates
        )
        if len(observation_ids) != len(set(observation_ids)):
            raise ModeTrackContractError(
                "sample source candidate observations must be unique"
            )
        # Candidate validation against the exact request/attempt is performed by
        # the enclosing source, where the owner references are available.  The
        # candidate object closure below still rejects a resealed candidate.
        for candidate in self.candidates:
            ModeTrackCandidateObservation.__post_init__(
                candidate,
                _MODE_TRACK_MINT_TOKEN,
            )
            if (
                candidate.canonical_sample_identity_id
                != self.canonical_sample_identity_id
                or candidate.acquisition_source_id != self.acquisition_source_id
                or candidate.injection_order != self.injection_order
            ):
                raise ModeTrackContractError(
                    "sample source contains a crossed candidate acquisition"
                )
        if type(self.trajectory_assessment) is not LocalTrajectoryAssessment:
            raise ModeTrackContractError(
                "sample source trajectory must be an M3 owner assessment"
            )
        if (
            self.trajectory_status != self.trajectory_assessment.status
            or self.trajectory_reason_codes != self.trajectory_assessment.reason_codes
            or self.trajectory_evidence_refs
            != self.trajectory_assessment.landmark_evidence_refs
        ):
            raise ModeTrackContractError(
                "sample source trajectory fields do not close its assessment"
            )


@final
@dataclass(frozen=True, slots=True)
class ModeTrackFitOnceSource(_RuntimeFinal):
    """Owner-sealed fit/model/sample/batch facts reused by one final attempt."""

    fit_once_source_id: str
    processing_request_id: str
    processing_attempt_id: str
    family_context_id: str
    model_set_id: str
    initial_assignment_batch_ids: tuple[str, ...]
    processing_owners: ProcessingOwnerReferences = field(compare=False, repr=False)
    family_context: FamilyContextRef = field(compare=False, repr=False)
    model_set: ModeTrackModelSet = field(compare=False, repr=False)
    sample_sources: tuple[ModeTrackFitOnceSampleSource, ...] = field(
        compare=False,
        repr=False,
    )
    initial_assignment_batches: tuple[ModeTrackAssignmentBatch, ...] = field(
        compare=False,
        repr=False,
    )
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _MODE_TRACK_MINT_TOKEN:
            raise ModeTrackContractError(
                "ModeTrackFitOnceSource must be minted by the M8 owner"
            )
        _required_text(self.fit_once_source_id, field="fit_once_source_id")
        _required_text(self.processing_request_id, field="processing_request_id")
        _required_text(self.processing_attempt_id, field="processing_attempt_id")
        _required_text(self.family_context_id, field="family_context_id")
        _required_text(self.model_set_id, field="model_set_id")
        _require_canonical_texts(
            self.initial_assignment_batch_ids,
            field="initial_assignment_batch_ids",
        )
        if type(self.processing_owners) is not ProcessingOwnerReferences:
            raise ModeTrackContractError("processing_owners must be owner-validated")
        try:
            self.processing_owners.__post_init__()
        except IdentityContractError as exc:
            raise ModeTrackContractError(str(exc)) from exc
        if (
            self.processing_request_id
            != self.processing_owners.request.processing_request_id
            or self.processing_attempt_id
            != self.processing_owners.attempt.processing_attempt_id
        ):
            raise ModeTrackContractError(
                "fit-once source belongs to a different processing attempt"
            )
        if type(self.family_context) is not FamilyContextRef:
            raise ModeTrackContractError("family_context must be owner-minted")
        validate_family_context_ref_for_consumption(self.family_context)
        if self.family_context_id != self.family_context.family_context_id:
            raise ModeTrackContractError("fit-once Family context was substituted")
        if type(self.model_set) is not ModeTrackModelSet:
            raise ModeTrackContractError("model_set must be owner-minted")
        _validate_model_set_binding(
            processing_owners=self.processing_owners,
            model_set=self.model_set,
        )
        if (
            self.model_set_id != self.model_set.model_set_id
            or self.model_set.family_context_id != self.family_context_id
            or self.model_set.family_context is not self.family_context
        ):
            raise ModeTrackContractError("fit-once model set was crossed")
        if type(self.sample_sources) is not tuple or any(
            type(item) is not ModeTrackFitOnceSampleSource
            for item in self.sample_sources
        ):
            raise ModeTrackContractError(
                "sample_sources must contain owner-minted immutable refs"
            )
        canonical_sources = tuple(
            sorted(
                self.sample_sources,
                key=lambda item: item.canonical_sample_identity_id,
            )
        )
        if self.sample_sources != canonical_sources:
            raise ModeTrackContractError("sample_sources must be canonical-sorted")
        expected_sample_ids = tuple(
            sorted(
                resolution.identity.sample_identity_id
                for resolution in self.processing_owners.sample_resolutions
            )
        )
        sample_ids = tuple(
            item.canonical_sample_identity_id for item in self.sample_sources
        )
        if sample_ids != expected_sample_ids:
            raise ModeTrackContractError(
                "fit-once source must cover the complete sample population"
            )
        if len(sample_ids) != len(set(sample_ids)):
            raise ModeTrackContractError("sample source identities must be unique")
        all_candidate_ids = tuple(
            candidate.candidate_observation_id
            for item in self.sample_sources
            for candidate in item.candidates
        )
        if len(all_candidate_ids) != len(set(all_candidate_ids)):
            raise ModeTrackContractError(
                "fit-once source candidate observations must be globally unique"
            )
        for item in self.sample_sources:
            ModeTrackFitOnceSampleSource.__post_init__(
                item,
                _MODE_TRACK_MINT_TOKEN,
            )
            _validate_trajectory_sample(
                item.trajectory_assessment,
                item.sample,
                processing_owners=self.processing_owners,
                acquisition_source=_resolve_sample_acquisition(
                    self.processing_owners,
                    item.sample,
                )[0],
                injection_order=_resolve_sample_acquisition(
                    self.processing_owners,
                    item.sample,
                )[1],
            )
            for candidate in item.candidates:
                _validate_assignment_observation_binding(
                    processing_owners=self.processing_owners,
                    sample=item.sample,
                    candidate=candidate,
                )
        if type(self.initial_assignment_batches) is not tuple or any(
            type(batch) is not ModeTrackAssignmentBatch
            for batch in self.initial_assignment_batches
        ):
            raise ModeTrackContractError(
                "initial_assignment_batches must contain owner-minted immutable refs"
            )
        canonical_batches = tuple(
            sorted(
                self.initial_assignment_batches,
                key=lambda item: item.assignment_batch_id,
            )
        )
        if self.initial_assignment_batches != canonical_batches:
            raise ModeTrackContractError(
                "initial_assignment_batches must be canonical-sorted"
            )
        batch_ids = tuple(
            batch.assignment_batch_id for batch in self.initial_assignment_batches
        )
        if self.initial_assignment_batch_ids != batch_ids:
            raise ModeTrackContractError(
                "fit-once batch IDs do not close retained batches"
            )
        if len(batch_ids) != len(set(batch_ids)):
            raise ModeTrackContractError("fit-once assignment batch IDs must be unique")
        source_by_sample_id = {
            item.canonical_sample_identity_id: item for item in self.sample_sources
        }
        seen_batch_samples: set[str] = set()
        for batch in self.initial_assignment_batches:
            validate_mode_track_assignment_batch_for_consumption(
                processing_owners=self.processing_owners,
                model_set=self.model_set,
                assignment_batch=batch,
            )
            source = source_by_sample_id.get(batch.canonical_sample_identity_id)
            if source is None or batch.sample != source.sample:
                raise ModeTrackContractError(
                    "initial assignment batch has no matching sample source"
                )
            if batch.canonical_sample_identity_id in seen_batch_samples:
                raise ModeTrackContractError(
                    "fit-once source contains duplicate sample batches"
                )
            seen_batch_samples.add(batch.canonical_sample_identity_id)
        if seen_batch_samples != set(sample_ids):
            raise ModeTrackContractError(
                "fit-once source must retain one batch for every sample"
            )
        expected_id = _typed_digest(
            "mode-track-fit-once-source:",
            _fit_once_source_identity_payload(self),
        )
        if self.fit_once_source_id != expected_id:
            raise ModeTrackContractError(
                "fit_once_source_id mismatches canonical owner content"
            )


def mint_mode_track_fit_once_sample_source(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    candidates: tuple[ModeTrackCandidateObservation, ...],
    trajectory_assessment: LocalTrajectoryAssessment,
) -> ModeTrackFitOnceSampleSource:
    """Mint one complete sample carrier from existing M8/M3 owner values."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise ModeTrackContractError("processing_owners must be owner-validated")
    if type(sample) is not ResolvedCanonicalSampleIdentity:
        raise ModeTrackContractError("sample must be owner-resolved")
    if type(candidates) is not tuple:
        raise ModeTrackContractError("candidates must be an immutable tuple")
    if type(trajectory_assessment) is not LocalTrajectoryAssessment:
        raise ModeTrackContractError(
            "trajectory_assessment must be an M3 owner assessment"
        )
    if any(type(item) is not ModeTrackCandidateObservation for item in candidates):
        raise ModeTrackContractError(
            "candidates must be owner-minted immutable refs"
        )
    acquisition_source, injection_order = _resolve_sample_acquisition(
        processing_owners,
        sample,
    )
    _validate_trajectory_sample(
        trajectory_assessment,
        sample,
        processing_owners=processing_owners,
        acquisition_source=acquisition_source,
        injection_order=injection_order,
    )
    for candidate in candidates:
        ModeTrackCandidateObservation.__post_init__(
            candidate,
            _MODE_TRACK_MINT_TOKEN,
        )
        _validate_assignment_observation_binding(
            processing_owners=processing_owners,
            sample=sample,
            candidate=candidate,
        )
    canonical_candidates = tuple(
        sorted(candidates, key=lambda item: item.candidate_observation_id)
    )
    return ModeTrackFitOnceSampleSource(
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=acquisition_source.acquisition_source_id,
        injection_order=injection_order,
        candidate_observation_ids=tuple(
            item.candidate_observation_id for item in canonical_candidates
        ),
        trajectory_status=trajectory_assessment.status,
        trajectory_reason_codes=trajectory_assessment.reason_codes,
        trajectory_evidence_refs=trajectory_assessment.landmark_evidence_refs,
        sample=sample,
        candidates=canonical_candidates,
        trajectory_assessment=trajectory_assessment,
        _mint_token=_MODE_TRACK_MINT_TOKEN,
    )


def mint_mode_track_fit_once_source(
    *,
    processing_owners: ProcessingOwnerReferences,
    family_context: FamilyContextRef,
    model_set: ModeTrackModelSet,
    sample_sources: tuple[ModeTrackFitOnceSampleSource, ...],
    initial_assignment_batches: tuple[ModeTrackAssignmentBatch, ...],
) -> ModeTrackFitOnceSource:
    """Mint the fit-once source by closing all existing owner values together."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise ModeTrackContractError("processing_owners must be owner-validated")
    if type(family_context) is not FamilyContextRef:
        raise ModeTrackContractError("family_context must be owner-minted")
    if type(model_set) is not ModeTrackModelSet:
        raise ModeTrackContractError("model_set must be owner-minted")
    if type(sample_sources) is not tuple:
        raise ModeTrackContractError("sample_sources must be an immutable tuple")
    if type(initial_assignment_batches) is not tuple:
        raise ModeTrackContractError(
            "initial_assignment_batches must be an immutable tuple"
        )
    if any(
        type(item) is not ModeTrackFitOnceSampleSource for item in sample_sources
    ):
        raise ModeTrackContractError(
            "sample_sources must contain owner-minted immutable refs"
        )
    if any(
        type(item) is not ModeTrackAssignmentBatch
        for item in initial_assignment_batches
    ):
        raise ModeTrackContractError(
            "initial_assignment_batches must contain owner-minted immutable refs"
        )
    canonical_sources = tuple(
        sorted(
            sample_sources,
            key=lambda item: item.canonical_sample_identity_id,
        )
    )
    canonical_batches = tuple(
        sorted(
            initial_assignment_batches,
            key=lambda item: item.assignment_batch_id,
        )
    )
    processing_request_id = processing_owners.request.processing_request_id
    processing_attempt_id = processing_owners.attempt.processing_attempt_id
    family_context_id = family_context.family_context_id
    model_set_id = model_set.model_set_id
    initial_assignment_batch_ids = tuple(
        item.assignment_batch_id for item in canonical_batches
    )
    fit_once_source_id = _typed_digest(
        "mode-track-fit-once-source:",
        _fit_once_source_identity_payload_values(
            processing_request_id=processing_request_id,
            processing_attempt_id=processing_attempt_id,
            family_context_id=family_context_id,
            model_set_id=model_set_id,
            sample_sources=canonical_sources,
            initial_assignment_batch_ids=initial_assignment_batch_ids,
        ),
    )
    return ModeTrackFitOnceSource(
        fit_once_source_id=fit_once_source_id,
        processing_request_id=processing_request_id,
        processing_attempt_id=processing_attempt_id,
        family_context_id=family_context_id,
        model_set_id=model_set_id,
        initial_assignment_batch_ids=initial_assignment_batch_ids,
        processing_owners=processing_owners,
        family_context=family_context,
        model_set=model_set,
        sample_sources=canonical_sources,
        initial_assignment_batches=canonical_batches,
        _mint_token=_MODE_TRACK_MINT_TOKEN,
    )


def validate_mode_track_fit_once_source_for_consumption(
    source: ModeTrackFitOnceSource,
) -> None:
    """Revalidate the complete fit-once source closure at a consumer boundary."""

    if type(source) is not ModeTrackFitOnceSource:
        raise ModeTrackContractError(
            "consumer validation requires exact fit-once source"
        )
    source.__post_init__(_MODE_TRACK_MINT_TOKEN)


def fit_mode_track_model_set(
    *,
    processing_owners: ProcessingOwnerReferences,
    family_context: FamilyContextRef,
    direct_support_anchors: tuple[ModeTrackSupportAnchor, ...],
    policy: ModeTrackTrajectoryPolicy,
) -> ModeTrackModelSet:
    """Fit every Family track once from owner-minted direct-SUPPORT anchors."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise ModeTrackContractError("processing_owners must be owner-validated")
    if type(family_context) is not FamilyContextRef:
        raise ModeTrackContractError("family_context must be owner-minted")
    validate_family_context_ref_for_consumption(family_context)
    if type(policy) is not ModeTrackTrajectoryPolicy:
        raise ModeTrackContractError("policy must be explicit and typed")
    ModeTrackTrajectoryPolicy.__post_init__(policy)
    if type(direct_support_anchors) is not tuple:
        raise ModeTrackContractError("direct_support_anchors must be a tuple")
    anchors_by_track: dict[str, list[ModeTrackSupportAnchor]] = {
        group_id: [] for group_id in family_context.group_hypothesis_ids
    }
    track_by_id = {
        track.group_hypothesis_id: track for track in family_context.mode_tracks
    }
    if any(
        type(anchor) is not ModeTrackSupportAnchor for anchor in direct_support_anchors
    ):
        raise ModeTrackContractError(
            "training inputs must be owner-minted ModeTrackSupportAnchor refs"
        )
    _require_unique_support_entities(direct_support_anchors)
    for anchor in direct_support_anchors:
        ModeTrackSupportAnchor.__post_init__(anchor, _MODE_TRACK_MINT_TOKEN)
        group_id = anchor.group_hypothesis_id
        if group_id not in anchors_by_track:
            raise ModeTrackContractError(
                "training SUPPORT anchor belongs to a group outside the Family"
            )
        if anchor.source_assignment_id is not None:
            raise ModeTrackContractError(
                "fit accepts initial anchors only; reinforced anchors require "
                "successor flow"
            )
        if anchor.peak_hypothesis_id not in track_by_id[group_id].owner_ids:
            raise ModeTrackContractError(
                "initial direct SUPPORT anchor is not an owner of its ModeTrack"
            )
        _validate_support_anchor(
            processing_owners=processing_owners,
            anchor=anchor,
        )
        source, order = _resolve_sample_acquisition(processing_owners, anchor.sample)
        if (
            anchor.acquisition_source_id != source.acquisition_source_id
            or anchor.injection_order != order
        ):
            raise ModeTrackContractError(
                "training SUPPORT anchor references a stale request binding"
            )
        family_reasons = _peak_hypothesis_family_exclusions(
            anchor.peak_hypothesis,
            family_context=family_context,
        )
        if family_reasons:
            raise ModeTrackContractError(
                "training SUPPORT anchor is outside the Family context"
            )
        anchors_by_track[group_id].append(anchor)
    tracks = tuple(
        ModeTrackModel(
            group_hypothesis_id=track.group_hypothesis_id,
            ordering_reference_rt_min=_finite_float(
                track.group_center_rt,
                field="track.group_center_rt",
            ),
            anchors=_canonical_anchors(
                tuple(anchors_by_track[track.group_hypothesis_id])
            ),
        )
        for track in family_context.mode_tracks
    )
    lineage_refs = tuple(
        sorted(
            {
                family_context.family_context_id,
                *family_context.source_lineage_refs,
                *(
                    anchor.support_assessment_artifact_content_id
                    for track in tracks
                    for anchor in track.anchors
                ),
            }
        )
    )
    return _mint_model_set(
        family_context=family_context,
        processing_request_id=processing_owners.request.processing_request_id,
        processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        policy=policy,
        tracks=tracks,
        lineage_refs=lineage_refs,
    )


def mint_mode_track_support_anchor(
    *,
    processing_owners: ProcessingOwnerReferences,
    track: CrossSamplePeakGroupHypothesis,
    sample: ResolvedCanonicalSampleIdentity,
    direct_support_assessment: CidNlMethodAssessmentRef,
    peak_hypothesis: PeakHypothesis,
) -> ModeTrackSupportAnchor:
    """Mint one initial anchor from an exact group-owner SUPPORT closure."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise ModeTrackContractError("processing_owners must be owner-validated")
    if not isinstance(track, CrossSamplePeakGroupHypothesis):
        raise ModeTrackContractError("track must be an existing group hypothesis")
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise ModeTrackContractError("sample must be owner-resolved")
    if not isinstance(direct_support_assessment, CidNlMethodAssessmentRef):
        raise ModeTrackContractError(
            "direct SUPPORT proof must be an owner-backed method assessment"
        )
    if not isinstance(peak_hypothesis, PeakHypothesis):
        raise ModeTrackContractError("peak_hypothesis must be a PeakHypothesis")
    if peak_hypothesis.hypothesis_id not in track.owner_ids:
        raise ModeTrackContractError(
            "direct SUPPORT hypothesis is not an owner of the supplied ModeTrack"
        )
    return _mint_support_anchor(
        processing_owners=processing_owners,
        sample=sample,
        group_hypothesis_id=track.group_hypothesis_id,
        direct_support_assessment=direct_support_assessment,
        peak_hypothesis=peak_hypothesis,
        source_assignment_id=None,
    )


def mint_mode_track_candidate_observation(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    peak_hypothesis: PeakHypothesis,
    direct_support_assessment: CidNlMethodAssessmentRef | None = None,
) -> ModeTrackCandidateObservation:
    """Bind one owner-backed hypothesis to its exact sample/acquisition identity."""

    acquisition_source, injection_order = _resolve_sample_acquisition(
        processing_owners,
        sample,
    )
    if not isinstance(peak_hypothesis, PeakHypothesis):
        raise ModeTrackContractError("candidate must be a PeakHypothesis")
    if not peak_hypothesis.is_owner_backed:
        raise ModeTrackContractError("candidate PeakHypothesis must be owner-backed")
    _validate_peak_hypothesis_binding(
        processing_owners=processing_owners,
        sample=sample,
        peak_hypothesis=peak_hypothesis,
    )
    support_content_id: str | None = None
    observation_class: CandidateObservationClass = "non_training"
    if direct_support_assessment is not None:
        _validate_support_assessment(
            processing_owners=processing_owners,
            sample=sample,
            peak_hypothesis=peak_hypothesis,
            assessment=direct_support_assessment,
        )
        observation_class = "direct_support"
        support_content_id = (
            direct_support_assessment.artifact.content_identity.artifact_content_id
        )
    apex_rt_min = _finite_float(
        peak_hypothesis.integration.rt_apex_min,
        field="peak_hypothesis.integration.rt_apex_min",
    )
    peak_observation_content_id = _peak_observation_content_id(peak_hypothesis)
    payload = _candidate_identity_payload(
        processing_request_id=processing_owners.request.processing_request_id,
        processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=acquisition_source.acquisition_source_id,
        injection_order=injection_order,
        peak_hypothesis_id=peak_hypothesis.hypothesis_id,
        peak_observation_content_id=peak_observation_content_id,
        apex_rt_min=apex_rt_min,
        observation_class=observation_class,
        direct_support_assessment_artifact_content_id=support_content_id,
    )
    return ModeTrackCandidateObservation(
        candidate_observation_id=_typed_digest("mode-track-candidate:", payload),
        processing_request_id=processing_owners.request.processing_request_id,
        processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=acquisition_source.acquisition_source_id,
        injection_order=injection_order,
        peak_hypothesis_id=peak_hypothesis.hypothesis_id,
        peak_observation_content_id=peak_observation_content_id,
        apex_rt_min=apex_rt_min,
        observation_class=observation_class,
        direct_support_assessment_artifact_content_id=support_content_id,
        sample=sample,
        acquisition_source=acquisition_source,
        peak_hypothesis=peak_hypothesis,
        direct_support_assessment=direct_support_assessment,
        _mint_token=_MODE_TRACK_MINT_TOKEN,
    )


def _mint_mode_track_repair_assignment_observation(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    repaired_member_subject_id: str,
    physical_observation_id: str,
    apex_rt_min: float,
    repair_receipt_id: str,
    source_observation_id: str,
) -> ModeTrackRepairAssignmentObservation:
    acquisition_source, injection_order = _resolve_sample_acquisition(
        processing_owners,
        sample,
    )
    payload = _repair_assignment_observation_payload(
        processing_request_id=processing_owners.request.processing_request_id,
        processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=acquisition_source.acquisition_source_id,
        injection_order=injection_order,
        repaired_member_subject_id=repaired_member_subject_id,
        physical_observation_id=physical_observation_id,
        apex_rt_min=apex_rt_min,
        repair_receipt_id=repair_receipt_id,
        source_observation_id=source_observation_id,
    )
    return ModeTrackRepairAssignmentObservation(
        candidate_observation_id=_typed_digest(
            "mode-track-repair-assignment-observation:",
            payload,
        ),
        processing_request_id=processing_owners.request.processing_request_id,
        processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=acquisition_source.acquisition_source_id,
        injection_order=injection_order,
        repaired_member_subject_id=repaired_member_subject_id,
        physical_observation_id=physical_observation_id,
        apex_rt_min=apex_rt_min,
        repair_receipt_id=repair_receipt_id,
        source_observation_id=source_observation_id,
        sample=sample,
        acquisition_source=acquisition_source,
        _mint_token=_MODE_TRACK_MINT_TOKEN,
    )


def assign_mode_tracks(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    model_set: ModeTrackModelSet,
    candidates: tuple[ModeTrackCandidateObservation, ...],
    repair_observations: tuple[ModeTrackRepairAssignmentObservation, ...] = (),
    trajectory_assessment: LocalTrajectoryAssessment,
) -> ModeTrackAssignmentBatch:
    """Assign every sample hypothesis once without granting product authority."""

    _validate_model_set_binding(
        processing_owners=processing_owners,
        model_set=model_set,
    )
    acquisition_source, injection_order = _resolve_sample_acquisition(
        processing_owners, sample
    )
    if (
        model_set.processing_request_id
        != processing_owners.request.processing_request_id
    ):
        raise ModeTrackContractError(
            "model set belongs to a different processing request"
        )
    if (
        model_set.processing_attempt_id
        != processing_owners.attempt.processing_attempt_id
    ):
        raise ModeTrackContractError(
            "model set belongs to a different processing attempt"
        )
    if type(candidates) is not tuple:
        raise ModeTrackContractError("candidates must be an immutable tuple")
    if type(repair_observations) is not tuple or any(
        type(item) is not ModeTrackRepairAssignmentObservation
        for item in repair_observations
    ):
        raise ModeTrackContractError(
            "repair observations must be exact owner-minted assignment refs"
        )
    if not isinstance(trajectory_assessment, LocalTrajectoryAssessment):
        raise ModeTrackContractError(
            "trajectory_assessment must be an M3 owner assessment"
        )
    _validate_trajectory_sample(
        trajectory_assessment,
        sample,
        processing_owners=processing_owners,
        acquisition_source=acquisition_source,
        injection_order=injection_order,
    )
    if any(
        type(candidate) is not ModeTrackCandidateObservation for candidate in candidates
    ):
        raise ModeTrackContractError("candidates must be owner-minted")
    for item in repair_observations:
        ModeTrackRepairAssignmentObservation.__post_init__(
            item,
            _MODE_TRACK_MINT_TOKEN,
        )
        if (
            item.processing_request_id
            != processing_owners.request.processing_request_id
            or item.processing_attempt_id
            != processing_owners.attempt.processing_attempt_id
            or item.canonical_sample_identity_id
            != sample.identity.sample_identity_id
            or item.acquisition_source_id
            != acquisition_source.acquisition_source_id
            or item.injection_order != injection_order
        ):
            raise ModeTrackContractError(
                "repair assignment observation crossed owner sample binding"
            )
    canonical_candidates: tuple[ModeTrackAssignmentObservation, ...] = tuple(
        sorted(
            (*candidates, *repair_observations),
            key=lambda item: item.candidate_observation_id,
        )
    )
    if len({item.candidate_observation_id for item in canonical_candidates}) != len(
        canonical_candidates
    ):
        raise ModeTrackContractError("candidate observations must be unique")
    hypothesis_ids = tuple(
        item.peak_hypothesis_id
        for item in canonical_candidates
        if item.peak_hypothesis_id is not None
    )
    if len(hypothesis_ids) != len(set(hypothesis_ids)):
        raise ModeTrackContractError("candidate PeakHypotheses must be unique")
    observation_ids = tuple(
        item.peak_observation_content_id for item in canonical_candidates
    )
    if len(observation_ids) != len(set(observation_ids)):
        raise ModeTrackContractError(
            "candidate physical peak observations must be unique"
        )
    for candidate in canonical_candidates:
        if type(candidate) is ModeTrackCandidateObservation:
            ModeTrackCandidateObservation.__post_init__(
                candidate,
                _MODE_TRACK_MINT_TOKEN,
            )
        else:
            ModeTrackRepairAssignmentObservation.__post_init__(
                candidate,
                _MODE_TRACK_MINT_TOKEN,
            )
        if (
            candidate.canonical_sample_identity_id != sample.identity.sample_identity_id
            or candidate.acquisition_source_id
            != acquisition_source.acquisition_source_id
            or candidate.injection_order != injection_order
        ):
            raise ModeTrackContractError(
                "candidate observation references a different sample acquisition"
            )
        _validate_assignment_observation_binding(
            processing_owners=processing_owners,
            sample=sample,
            candidate=candidate,
        )

    predictions = tuple(
        _predict_track(
            track,
            injection_order=injection_order,
            trajectory_assessment=trajectory_assessment,
            policy=model_set.policy,
        )
        for track in model_set.tracks
    )
    lineage_refs = tuple(
        sorted(
            {
                model_set.model_set_id,
                *model_set.lineage_refs,
                    *trajectory_assessment.landmark_evidence_refs,
                    *(
                        item.source_observation_id
                        for item in repair_observations
                    ),
                    *(
                        item.repair_receipt_id
                        for item in repair_observations
                    ),
            }
        )
    )
    if not canonical_candidates:
        not_observed_assignments = (
            _mint_assignment(
                family_context_id=model_set.family_context_id,
                processing_request_id=model_set.processing_request_id,
                processing_attempt_id=model_set.processing_attempt_id,
                sample=sample,
                acquisition_source=acquisition_source,
                injection_order=injection_order,
                candidate=None,
                group_hypothesis_id=None,
                state="not_observed",
                reason_codes=("not_observed",),
                predictions=predictions,
                trajectory_assessment=trajectory_assessment,
                lineage_refs=lineage_refs,
            ),
        )
        return _mint_assignment_batch(
            model_set=model_set,
            sample=sample,
            acquisition_source=acquisition_source,
            injection_order=injection_order,
            assignments=not_observed_assignments,
        )

    decisions = _resolve_candidate_decisions(
        candidates=canonical_candidates,
        model_set=model_set,
        predictions=predictions,
    )
    assignments = tuple(
        _assign_candidate(
            candidate=candidate,
            family_context=model_set.family_context,
            sample=sample,
            acquisition_source=acquisition_source,
            injection_order=injection_order,
            predictions=predictions,
            trajectory_assessment=trajectory_assessment,
            lineage_refs=lineage_refs,
            decision=decisions[candidate.candidate_observation_id],
        )
        for candidate in canonical_candidates
    )
    return _mint_assignment_batch(
        model_set=model_set,
        sample=sample,
        acquisition_source=acquisition_source,
        injection_order=injection_order,
        assignments=assignments,
    )


def validate_mode_track_assignment_batch_for_consumption(
    *,
    processing_owners: ProcessingOwnerReferences,
    model_set: ModeTrackModelSet,
    assignment_batch: ModeTrackAssignmentBatch,
) -> None:
    """Revalidate one M8 batch against its complete current owner closure."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise ModeTrackContractError("processing_owners must be owner-validated")
    _validate_model_set_binding(
        processing_owners=processing_owners, model_set=model_set
    )
    _validate_assignment_batch_graph(assignment_batch)
    batch = assignment_batch
    owner_key = (
        processing_owners.request.processing_request_id,
        processing_owners.attempt.processing_attempt_id,
    )
    if any(
        (item.processing_request_id, item.processing_attempt_id) != owner_key
        for item in (model_set, batch)
    ):
        raise ModeTrackContractError("inputs belong to different processing owners")
    if (
        batch.model_set_id,
        batch.family_context_id,
        batch.trajectory_policy_version,
        batch.band_policy_version,
    ) != (
        model_set.model_set_id,
        model_set.family_context_id,
        model_set.policy.trajectory_policy_version,
        model_set.policy.band_policy_version,
    ):
        raise ModeTrackContractError("batch belongs to a different model or policy")
    source, order = _resolve_sample_acquisition(processing_owners, batch.sample)
    if batch.acquisition_source != source or batch.injection_order != order:
        raise ModeTrackContractError("assignment batch has a stale sample binding")
    model_predictions = tuple(
        (track.group_hypothesis_id, *_track_evidence_refs(track))
        for track in model_set.tracks
    )
    first_assignment = batch.assignments[0]
    if any(
        item.prediction_assessments != first_assignment.prediction_assessments
        or item.trajectory_evidence_refs != first_assignment.trajectory_evidence_refs
        or item.lineage_refs != first_assignment.lineage_refs
        for item in batch.assignments[1:]
    ):
        raise ModeTrackContractError(
            "assignments disagree on batch-wide prediction or lineage facts"
        )
    for assignment in batch.assignments:
        if (
            tuple(
                (
                    item.group_hypothesis_id,
                    item.anchor_refs,
                    item.support_artifact_content_ids,
                )
                for item in assignment.prediction_assessments
            )
            != model_predictions
        ):
            raise ModeTrackContractError("assignment crosses the supplied model")
        candidate = assignment.candidate_observation
        if candidate is not None:
            _validate_assignment_observation_binding(
                processing_owners=processing_owners,
                sample=batch.sample,
                candidate=candidate,
            )
    if any(item.state == "not_observed" for item in batch.assignments):
        if len(batch.assignments) != 1 or (
            first_assignment.state,
            first_assignment.group_hypothesis_id,
            first_assignment.reason_codes,
        ) != ("not_observed", None, ("not_observed",)):
            raise ModeTrackContractError("not_observed batch has a forged decision")
        return
    candidates = tuple(
        item.candidate_observation
        for item in batch.assignments
        if item.candidate_observation is not None
    )
    decisions = _resolve_candidate_decisions(
        candidates=candidates,
        model_set=model_set,
        predictions=first_assignment.prediction_assessments,
    )
    if any(
        (item.state, item.group_hypothesis_id, item.reason_codes)
        != decisions[item.candidate_observation_id]
        for item in batch.assignments
        if item.candidate_observation_id is not None
    ):
        raise ModeTrackContractError("assignment decision was substituted")


def reinforce_mode_track_model_set(
    *,
    processing_owners: ProcessingOwnerReferences,
    model_set: ModeTrackModelSet,
    assignment_batch: ModeTrackAssignmentBatch,
) -> ModeTrackModelSet:
    """Add only uniquely assigned direct SUPPORT facts to exactly one track."""

    validate_mode_track_assignment_batch_for_consumption(
        processing_owners=processing_owners,
        model_set=model_set,
        assignment_batch=assignment_batch,
    )
    additions: dict[str, list[ModeTrackSupportAnchor]] = {}
    for assignment in assignment_batch.assignments:
        if (
            assignment.state != "uniquely_assigned"
            or assignment.candidate_observation_class != "direct_support"
        ):
            continue
        assert assignment.group_hypothesis_id is not None
        assert assignment.peak_hypothesis is not None
        assert assignment.direct_support_assessment is not None
        anchor = _mint_support_anchor(
            processing_owners=processing_owners,
            sample=assignment.sample,
            group_hypothesis_id=assignment.group_hypothesis_id,
            direct_support_assessment=assignment.direct_support_assessment,
            peak_hypothesis=assignment.peak_hypothesis,
            source_assignment_id=assignment.assignment_id,
        )
        additions.setdefault(assignment.group_hypothesis_id, []).append(anchor)
    if not additions:
        return model_set
    updated_tracks: list[ModeTrackModel] = []
    anchor_added = False
    for track in model_set.tracks:
        new_anchors = additions.get(track.group_hypothesis_id, [])
        anchors = list(track.anchors)
        for anchor in new_anchors:
            existing = tuple(
                item
                for item in anchors
                if item.injection_order == anchor.injection_order
            )
            if existing:
                if existing[0].peak_hypothesis_id == anchor.peak_hypothesis_id:
                    continue
                raise ModeTrackContractError(
                    "reinforcement would assign two anchors to one track injection"
                )
            anchors.append(anchor)
            anchor_added = True
        updated_tracks.append(
            ModeTrackModel(
                group_hypothesis_id=track.group_hypothesis_id,
                ordering_reference_rt_min=track.ordering_reference_rt_min,
                anchors=_canonical_anchors(tuple(anchors)),
            )
        )
    if not anchor_added:
        return model_set
    _require_unique_support_entities(
        tuple(anchor for track in updated_tracks for anchor in track.anchors)
    )
    lineage_refs = tuple(
        sorted(
            {
                *model_set.lineage_refs,
                *(item.assignment_id for item in assignment_batch.assignments),
            }
        )
    )
    return _mint_model_set(
        family_context=model_set.family_context,
        processing_request_id=model_set.processing_request_id,
        processing_attempt_id=model_set.processing_attempt_id,
        policy=model_set.policy,
        tracks=tuple(updated_tracks),
        lineage_refs=lineage_refs,
    )


def canonical_mode_track_assignment_batch_bytes(
    batch: ModeTrackAssignmentBatch,
) -> bytes:
    """Serialize one owner-validated batch without public/product projections."""

    _validate_assignment_batch_graph(batch)
    payload = _batch_identity_payload(batch)
    payload["schema_version"] = BATCH_SCHEMA_VERSION
    payload["assignment_batch_id"] = batch.assignment_batch_id
    payload["validation_status"] = batch.validation_status
    payload["product_authority"] = batch.product_authority
    return _canonical_json_bytes(payload)


def _mint_support_anchor(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    group_hypothesis_id: str,
    direct_support_assessment: CidNlMethodAssessmentRef,
    peak_hypothesis: PeakHypothesis,
    source_assignment_id: str | None,
) -> ModeTrackSupportAnchor:
    _validate_support_assessment(
        processing_owners=processing_owners,
        sample=sample,
        peak_hypothesis=peak_hypothesis,
        assessment=direct_support_assessment,
    )
    _validate_peak_hypothesis_binding(
        processing_owners=processing_owners,
        sample=sample,
        peak_hypothesis=peak_hypothesis,
    )
    source, order = _resolve_sample_acquisition(processing_owners, sample)
    apex_rt_min = _finite_float(
        peak_hypothesis.integration.rt_apex_min,
        field="peak_hypothesis.integration.rt_apex_min",
    )
    peak_observation_content_id = _peak_observation_content_id(peak_hypothesis)
    assessment_content_id = (
        direct_support_assessment.artifact.content_identity.artifact_content_id
    )
    evidence_ids = tuple(
        sorted(
            evidence.artifact.evidence_artifact_id
            for evidence in direct_support_assessment.evidence_refs
        )
    )
    payload = _anchor_identity_payload(
        group_hypothesis_id=group_hypothesis_id,
        processing_request_id=processing_owners.request.processing_request_id,
        processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=source.acquisition_source_id,
        injection_order=order,
        apex_rt_min=apex_rt_min,
        peak_hypothesis_id=peak_hypothesis.hypothesis_id,
        peak_observation_content_id=peak_observation_content_id,
        support_assessment_artifact_content_id=assessment_content_id,
        evidence_artifact_ids=evidence_ids,
        source_assignment_id=source_assignment_id,
    )
    return ModeTrackSupportAnchor(
        anchor_id=_typed_digest("mode-track-anchor:", payload),
        group_hypothesis_id=group_hypothesis_id,
        processing_request_id=processing_owners.request.processing_request_id,
        processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=source.acquisition_source_id,
        injection_order=order,
        apex_rt_min=apex_rt_min,
        peak_hypothesis_id=peak_hypothesis.hypothesis_id,
        peak_observation_content_id=peak_observation_content_id,
        support_assessment_artifact_content_id=assessment_content_id,
        evidence_artifact_ids=evidence_ids,
        source_assignment_id=source_assignment_id,
        sample=sample,
        acquisition_source=source,
        peak_hypothesis=peak_hypothesis,
        direct_support_assessment=direct_support_assessment,
        _mint_token=_MODE_TRACK_MINT_TOKEN,
    )


def _mint_model_set(
    *,
    family_context: FamilyContextRef,
    processing_request_id: str,
    processing_attempt_id: str,
    policy: ModeTrackTrajectoryPolicy,
    tracks: tuple[ModeTrackModel, ...],
    lineage_refs: tuple[str, ...],
) -> ModeTrackModelSet:
    canonical_tracks = tuple(sorted(tracks, key=lambda item: item.group_hypothesis_id))
    canonical_lineage = tuple(sorted(set(lineage_refs)))
    payload = _model_set_identity_payload(
        family_context_id=family_context.family_context_id,
        processing_request_id=processing_request_id,
        processing_attempt_id=processing_attempt_id,
        policy=policy,
        tracks=canonical_tracks,
        lineage_refs=canonical_lineage,
    )
    return ModeTrackModelSet(
        model_set_id=_typed_digest("mode-track-model-set:", payload),
        family_context_id=family_context.family_context_id,
        processing_request_id=processing_request_id,
        processing_attempt_id=processing_attempt_id,
        policy=policy,
        tracks=canonical_tracks,
        lineage_refs=canonical_lineage,
        family_context=family_context,
        _mint_token=_MODE_TRACK_MINT_TOKEN,
    )


def _track_evidence_refs(track: ModeTrackModel) -> tuple[tuple[str, ...], ...]:
    return (
        tuple(sorted(anchor.anchor_id for anchor in track.anchors)),
        tuple(
            sorted(
                anchor.support_assessment_artifact_content_id
                for anchor in track.anchors
            )
        ),
    )


def _predict_track(
    track: ModeTrackModel,
    *,
    injection_order: int,
    trajectory_assessment: LocalTrajectoryAssessment,
    policy: ModeTrackTrajectoryPolicy,
) -> ModeTrackPredictionAssessment:
    anchors = track.anchors
    anchor_refs, support_refs = _track_evidence_refs(track)
    orders = tuple(anchor.injection_order for anchor in anchors)
    if not anchors:
        center = track.ordering_reference_rt_min
        half_width = policy.prediction_half_width_min
        return ModeTrackPredictionAssessment(
            group_hypothesis_id=track.group_hypothesis_id,
            status="predicted",
            predicted_rt_min=center,
            prediction_interval_min=(
                _canonical_float(center - half_width),
                _canonical_float(center + half_width),
            ),
            anchor_refs=(),
            support_artifact_content_ids=(),
            reason_codes=("provisional_group_geometry",),
        )
    if injection_order in orders:
        center = anchors[orders.index(injection_order)].apex_rt_min
        half_width = policy.prediction_half_width_min
        return ModeTrackPredictionAssessment(
            group_hypothesis_id=track.group_hypothesis_id,
            status="predicted",
            predicted_rt_min=center,
            prediction_interval_min=(
                _canonical_float(center - half_width),
                _canonical_float(center + half_width),
            ),
            anchor_refs=anchor_refs,
            support_artifact_content_ids=support_refs,
            reason_codes=("exact_injection_anchor",),
        )
    if len(anchors) < 2:
        return ModeTrackPredictionAssessment(
            group_hypothesis_id=track.group_hypothesis_id,
            status="sparse_anchors",
            predicted_rt_min=None,
            prediction_interval_min=None,
            anchor_refs=anchor_refs,
            support_artifact_content_ids=support_refs,
            reason_codes=("sparse_track_anchors",),
        )
    if orders[0] < injection_order < orders[-1]:
        left, right = _bracketing_anchors(anchors, injection_order)
        if right.injection_order - left.injection_order > policy.max_interpolation_gap:
            return ModeTrackPredictionAssessment(
                group_hypothesis_id=track.group_hypothesis_id,
                status="unsupported_interpolation",
                predicted_rt_min=None,
                prediction_interval_min=None,
                anchor_refs=anchor_refs,
                support_artifact_content_ids=support_refs,
                reason_codes=("unsupported_interpolation",),
            )
        center = _linear_prediction(left, right, injection_order)
    else:
        nearest = anchors[0] if injection_order < orders[0] else anchors[-1]
        if abs(injection_order - nearest.injection_order) > (
            policy.max_extrapolation_gap
        ):
            return ModeTrackPredictionAssessment(
                group_hypothesis_id=track.group_hypothesis_id,
                status="unsupported_extrapolation",
                predicted_rt_min=None,
                prediction_interval_min=None,
                anchor_refs=anchor_refs,
                support_artifact_content_ids=support_refs,
                reason_codes=("unsupported_extrapolation",),
            )
        left, right = anchors[:2] if injection_order < orders[0] else anchors[-2:]
        center = _linear_prediction(left, right, injection_order)
    drift_delta, drift_reasons = _trajectory_delta(
        trajectory_assessment,
        predicted_rt_min=center,
    )
    if drift_delta is None:
        return ModeTrackPredictionAssessment(
            group_hypothesis_id=track.group_hypothesis_id,
            status="trajectory_unassessable",
            predicted_rt_min=None,
            prediction_interval_min=None,
            anchor_refs=anchor_refs,
            support_artifact_content_ids=support_refs,
            reason_codes=drift_reasons,
        )
    center = _canonical_float(center + drift_delta)
    half_width = policy.prediction_half_width_min
    interval = (
        _canonical_float(center - half_width),
        _canonical_float(center + half_width),
    )
    return ModeTrackPredictionAssessment(
        group_hypothesis_id=track.group_hypothesis_id,
        status="predicted",
        predicted_rt_min=center,
        prediction_interval_min=interval,
        anchor_refs=anchor_refs,
        support_artifact_content_ids=support_refs,
        reason_codes=tuple(sorted({"prediction_available", *drift_reasons})),
    )


def _global_no_write_state(
    *,
    predictions: tuple[ModeTrackPredictionAssessment, ...],
) -> tuple[AssignmentState, tuple[str, ...]] | None:
    unavailable = tuple(item for item in predictions if item.status != "predicted")
    if unavailable:
        statuses = {item.status for item in unavailable}
        reasons = tuple(
            sorted({reason for item in unavailable for reason in item.reason_codes})
        )
        state: AssignmentState = "technical_unassessable"
        if len(unavailable) == len(predictions) and statuses.issubset(
            {"unsupported_interpolation", "unsupported_extrapolation"}
        ):
            state = "out_of_model"
        return state, reasons
    return None


def _resolve_candidate_decisions(
    *,
    candidates: tuple[ModeTrackAssignmentObservation, ...],
    model_set: ModeTrackModelSet,
    predictions: tuple[ModeTrackPredictionAssessment, ...],
) -> dict[str, tuple[AssignmentState, str | None, tuple[str, ...]]]:
    global_state = _global_no_write_state(predictions=predictions)
    crossed_track_ids = _crossed_track_ids(model_set, predictions)
    decisions = {
        candidate.candidate_observation_id: _candidate_decision(
            candidate=candidate,
            family_context=model_set.family_context,
            predictions=predictions,
            global_state=global_state,
            crossed_track_ids=crossed_track_ids,
        )
        for candidate in candidates
    }
    unique_counts: dict[str, int] = {}
    for state, group_id, _ in decisions.values():
        if state == "uniquely_assigned":
            assert group_id is not None
            unique_counts[group_id] = unique_counts.get(group_id, 0) + 1
    anchor_incumbents = {
        track.group_hypothesis_id: anchor.peak_hypothesis_id
        for track in model_set.tracks
        for anchor in track.anchors
        if anchor.injection_order == candidates[0].injection_order
    }
    owner_candidates: dict[str, list[str]] = {}
    for candidate in candidates:
        if type(candidate) is ModeTrackRepairAssignmentObservation:
            continue
        for track in model_set.family_context.mode_tracks:
            if candidate.peak_hypothesis_id in track.owner_ids:
                owner_candidates.setdefault(track.group_hypothesis_id, []).append(
                    candidate.peak_hypothesis_id
                )
    owner_incumbents = {
        group_id: hypothesis_ids[0]
        for group_id, hypothesis_ids in owner_candidates.items()
        if len(hypothesis_ids) == 1
    }
    conflicting_owner_groups = {
        group_id
        for group_id, hypothesis_ids in owner_candidates.items()
        if len(hypothesis_ids) > 1
    }
    conflicting_owner_groups.update(
        group_id
        for group_id, hypothesis_id in owner_incumbents.items()
        if group_id in anchor_incumbents
        and anchor_incumbents[group_id] != hypothesis_id
    )
    incumbents = dict(anchor_incumbents)
    incumbents.update(
        (group_id, hypothesis_id)
        for group_id, hypothesis_id in owner_incumbents.items()
        if group_id not in conflicting_owner_groups
    )
    for candidate in candidates:
        state, group_id, _ = decisions[candidate.candidate_observation_id]
        if state != "uniquely_assigned":
            continue
        assert group_id is not None
        incumbent_id = incumbents.get(group_id)
        if group_id in conflicting_owner_groups:
            conflict = True
        elif incumbent_id is not None:
            conflict = candidate.peak_hypothesis_id != incumbent_id
        else:
            conflict = unique_counts[group_id] > 1
        if conflict:
            decisions[candidate.candidate_observation_id] = (
                "ambiguous_multiple_tracks",
                None,
                ("track_occupancy_conflict",),
            )
    return decisions


def _candidate_decision(
    *,
    candidate: ModeTrackAssignmentObservation,
    family_context: FamilyContextRef,
    predictions: tuple[ModeTrackPredictionAssessment, ...],
    global_state: tuple[AssignmentState, tuple[str, ...]] | None,
    crossed_track_ids: frozenset[str],
) -> tuple[AssignmentState, str | None, tuple[str, ...]]:
    if type(candidate) is ModeTrackCandidateObservation:
        family_exclusions = _peak_hypothesis_family_exclusions(
            candidate.peak_hypothesis,
            family_context=family_context,
        )
        if family_exclusions:
            return "out_of_model", None, family_exclusions
        owner_group_ids = tuple(
            track.group_hypothesis_id
            for track in family_context.mode_tracks
            if candidate.peak_hypothesis_id in track.owner_ids
        )
        if len(owner_group_ids) > 1:
            raise ModeTrackContractError(
                "candidate PeakHypothesis owns more than one ModeTrack"
            )
        if owner_group_ids:
            return (
                "uniquely_assigned",
                owner_group_ids[0],
                ("existing_group_owner_observation",),
            )
    if global_state is not None:
        state, reasons = global_state
        return state, None, reasons
    matching = tuple(
        item
        for item in predictions
        if item.prediction_interval_min is not None
        and item.prediction_interval_min[0]
        <= candidate.apex_rt_min
        <= item.prediction_interval_min[1]
    )
    if len(matching) == 1:
        group_id = matching[0].group_hypothesis_id
        if group_id in crossed_track_ids:
            return "technical_unassessable", None, ("trajectory_crossing",)
        reasons = ("unique_interval_all_competitors_excluded",)
        return "uniquely_assigned", group_id, reasons
    if len(matching) > 1:
        return "ambiguous_multiple_tracks", None, ("candidate_matches_multiple_tracks",)
    return "out_of_model", None, ("candidate_outside_all_prediction_intervals",)


def _assign_candidate(
    *,
    candidate: ModeTrackAssignmentObservation,
    family_context: FamilyContextRef,
    sample: ResolvedCanonicalSampleIdentity,
    acquisition_source: AcquisitionSourceIdentity,
    injection_order: int,
    predictions: tuple[ModeTrackPredictionAssessment, ...],
    trajectory_assessment: LocalTrajectoryAssessment,
    lineage_refs: tuple[str, ...],
    decision: tuple[AssignmentState, str | None, tuple[str, ...]],
) -> ModeTrackAssignment:
    state, group_id, reasons = decision
    return _mint_assignment(
        family_context_id=family_context.family_context_id,
        processing_request_id=candidate.processing_request_id,
        processing_attempt_id=candidate.processing_attempt_id,
        sample=sample,
        acquisition_source=acquisition_source,
        injection_order=injection_order,
        candidate=candidate,
        group_hypothesis_id=group_id,
        state=state,
        reason_codes=reasons,
        predictions=predictions,
        trajectory_assessment=trajectory_assessment,
        lineage_refs=lineage_refs,
    )


def _mint_assignment(
    *,
    family_context_id: str,
    processing_request_id: str,
    processing_attempt_id: str,
    sample: ResolvedCanonicalSampleIdentity,
    acquisition_source: AcquisitionSourceIdentity,
    injection_order: int,
    candidate: ModeTrackAssignmentObservation | None,
    group_hypothesis_id: str | None,
    state: AssignmentState,
    reason_codes: tuple[str, ...],
    predictions: tuple[ModeTrackPredictionAssessment, ...],
    trajectory_assessment: LocalTrajectoryAssessment,
    lineage_refs: tuple[str, ...],
) -> ModeTrackAssignment:
    canonical_reasons = tuple(sorted(set(reason_codes)))
    canonical_predictions = tuple(
        sorted(predictions, key=lambda item: item.group_hypothesis_id)
    )
    canonical_lineage = tuple(sorted(set(lineage_refs)))
    sample_id = sample.identity.sample_identity_id
    source_id = acquisition_source.acquisition_source_id
    hypothesis_id = None if candidate is None else candidate.peak_hypothesis_id
    observation_id = (
        None if candidate is None else candidate.peak_observation_content_id
    )
    candidate_id = None if candidate is None else candidate.candidate_observation_id
    observation_class = None if candidate is None else candidate.observation_class
    support_assessment_content_id = (
        None
        if candidate is None
        else candidate.direct_support_assessment_artifact_content_id
    )
    trajectory_refs = tuple(sorted(trajectory_assessment.landmark_evidence_refs))
    identity_payload = _assignment_payload(
        family_context_id=family_context_id,
        processing_request_id=processing_request_id,
        processing_attempt_id=processing_attempt_id,
        canonical_sample_identity_id=sample_id,
        acquisition_source_id=source_id,
        injection_order=injection_order,
        candidate_observation_id=candidate_id,
        peak_hypothesis_id=hypothesis_id,
        peak_observation_content_id=observation_id,
        candidate_observation_class=observation_class,
        direct_support_assessment_artifact_content_id=(support_assessment_content_id),
        group_hypothesis_id=group_hypothesis_id,
        state=state,
        reason_codes=canonical_reasons,
        prediction_assessments=canonical_predictions,
        trajectory_evidence_refs=trajectory_refs,
        lineage_refs=canonical_lineage,
    )
    return ModeTrackAssignment(
        assignment_id=_typed_digest(
            "mode-track-assignment:",
            identity_payload,
        ),
        family_context_id=family_context_id,
        processing_request_id=processing_request_id,
        processing_attempt_id=processing_attempt_id,
        canonical_sample_identity_id=sample_id,
        acquisition_source_id=source_id,
        injection_order=injection_order,
        candidate_observation_id=candidate_id,
        peak_hypothesis_id=hypothesis_id,
        peak_observation_content_id=observation_id,
        candidate_observation_class=observation_class,
        direct_support_assessment_artifact_content_id=(support_assessment_content_id),
        group_hypothesis_id=group_hypothesis_id,
        state=state,
        reason_codes=canonical_reasons,
        prediction_assessments=canonical_predictions,
        trajectory_evidence_refs=trajectory_refs,
        lineage_refs=canonical_lineage,
        sample=sample,
        acquisition_source=acquisition_source,
        candidate_observation=candidate,
        _mint_token=_MODE_TRACK_MINT_TOKEN,
    )


def _mint_assignment_batch(
    *,
    model_set: ModeTrackModelSet,
    sample: ResolvedCanonicalSampleIdentity,
    acquisition_source: AcquisitionSourceIdentity,
    injection_order: int,
    assignments: tuple[ModeTrackAssignment, ...],
) -> ModeTrackAssignmentBatch:
    canonical = _canonical_assignments(assignments)
    sample_id = sample.identity.sample_identity_id
    source_id = acquisition_source.acquisition_source_id
    identity_payload = _batch_payload(
        family_context_id=model_set.family_context_id,
        model_set_id=model_set.model_set_id,
        processing_request_id=model_set.processing_request_id,
        processing_attempt_id=model_set.processing_attempt_id,
        canonical_sample_identity_id=sample_id,
        acquisition_source_id=source_id,
        injection_order=injection_order,
        trajectory_policy_version=model_set.policy.trajectory_policy_version,
        band_policy_version=model_set.policy.band_policy_version,
        assignments=canonical,
    )
    return ModeTrackAssignmentBatch(
        assignment_batch_id=_typed_digest(
            "mode-track-assignment-batch:",
            identity_payload,
        ),
        family_context_id=model_set.family_context_id,
        model_set_id=model_set.model_set_id,
        processing_request_id=model_set.processing_request_id,
        processing_attempt_id=model_set.processing_attempt_id,
        canonical_sample_identity_id=sample_id,
        acquisition_source_id=source_id,
        injection_order=injection_order,
        trajectory_policy_version=model_set.policy.trajectory_policy_version,
        band_policy_version=model_set.policy.band_policy_version,
        assignments=canonical,
        sample=sample,
        acquisition_source=acquisition_source,
        _mint_token=_MODE_TRACK_MINT_TOKEN,
    )


def _resolve_sample_acquisition(
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> tuple[AcquisitionSourceIdentity, int]:
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise ModeTrackContractError("processing_owners must be owner-validated")
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise ModeTrackContractError("sample must be owner-resolved")
    try:
        source = resolve_request_acquisition_source(
            processing_owners=processing_owners,
            sample=sample,
        )
    except IdentityContractError as exc:
        raise ModeTrackContractError(str(exc)) from exc
    matching = tuple(
        binding
        for binding in processing_owners.request.acquisition_bindings
        if binding.adapter_input_key == sample.lookup_token
        and binding.acquisition_source_id == source.acquisition_source_id
    )
    if len(matching) != 1:
        raise ModeTrackContractError(
            "sample must resolve to one request injection binding"
        )
    return source, matching[0].injection_order


def _validate_peak_hypothesis_binding(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    peak_hypothesis: PeakHypothesis,
) -> None:
    if len(peak_hypothesis.peak_observation_refs) != 1:
        raise ModeTrackContractError(
            "candidate PeakHypothesis must reference one physical observation"
        )
    support = peak_hypothesis.evidence.cid_nl_chromatographic_support_ref
    if peak_hypothesis.hypothesis_method == HYPOTHESIS_METHOD_MS1_OBSERVATION:
        if support is not None:
            raise ModeTrackContractError(
                "observation-only PeakHypothesis cannot carry training support"
            )
        try:
            validate_ms1_observation_peak_hypothesis_binding(
                peak_hypothesis,
                processing_owners=processing_owners,
                sample=sample,
                peak_observation=peak_hypothesis.peak_observation_refs[0],
            )
        except PeakHypothesisContractError as exc:
            raise ModeTrackContractError(str(exc)) from exc
        return
    if support is None:
        raise ModeTrackContractError(
            "candidate PeakHypothesis must reference chromatographic support"
        )
    try:
        validate_cid_nl_peak_hypothesis_binding(
            peak_hypothesis,
            processing_owners=processing_owners,
            sample=sample,
            peak_observation=peak_hypothesis.peak_observation_refs[0],
            chromatographic_support=support,
        )
    except PeakHypothesisContractError as exc:
        raise ModeTrackContractError(str(exc)) from exc


def _validate_support_assessment(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    peak_hypothesis: PeakHypothesis,
    assessment: CidNlMethodAssessmentRef,
) -> None:
    if not isinstance(assessment, CidNlMethodAssessmentRef):
        raise ModeTrackContractError(
            "direct SUPPORT proof must be an owner-backed method assessment"
        )
    if assessment.content.method_result != "SUPPORT":
        raise ModeTrackContractError("only direct SUPPORT may train a ModeTrack")
    try:
        method_subject = assessment.content.method_subject
        if method_subject.subject_kind == "targeted_definition":
            validate_cid_nl_method_assessment_binding(
                assessment,
                processing_owners=processing_owners,
                sample=sample,
                target=_resolve_assessment_target(
                    processing_owners=processing_owners,
                    assessment=assessment,
                ),
            )
        else:
            validate_cid_nl_method_assessment_binding(
                assessment,
                processing_owners=processing_owners,
                sample=sample,
                method_subject=method_subject,
            )
        validate_cid_nl_assessment_hypothesis_closure(
            sample=sample,
            assessment=assessment,
            hypothesis=peak_hypothesis,
        )
    except (CidNlMethodAssessmentContractError, ValueError) as exc:
        raise ModeTrackContractError(str(exc)) from exc


def _resolve_assessment_target(
    *,
    processing_owners: ProcessingOwnerReferences,
    assessment: CidNlMethodAssessmentRef,
) -> ResolvedTargetDefinition:
    snapshot = processing_owners.target_snapshot
    subject = assessment.content.method_subject
    if snapshot is None or subject.target_definition_id is None:
        raise ModeTrackContractError(
            "targeted SUPPORT assessment lacks its request target snapshot"
        )
    matching = tuple(
        definition
        for definition in snapshot.definitions
        if definition.target_definition_id == subject.target_definition_id
        and definition.target_key.target_key_id == subject.target_key_id
    )
    if len(matching) != 1:
        raise ModeTrackContractError(
            "targeted SUPPORT assessment does not resolve to one request target"
        )
    definition = matching[0]
    return ResolvedTargetDefinition(
        definition=definition,
        snapshot=snapshot,
        lookup_label=definition.target_key.target_label,
    )


def _validate_support_anchor(
    *,
    processing_owners: ProcessingOwnerReferences,
    anchor: ModeTrackSupportAnchor,
) -> None:
    if anchor.processing_request_id != processing_owners.request.processing_request_id:
        raise ModeTrackContractError(
            "support anchor belongs to a different processing request"
        )
    if anchor.processing_attempt_id != processing_owners.attempt.processing_attempt_id:
        raise ModeTrackContractError(
            "support anchor belongs to a different processing attempt"
        )
    source, order = _resolve_sample_acquisition(processing_owners, anchor.sample)
    if (
        anchor.acquisition_source_id != source.acquisition_source_id
        or anchor.injection_order != order
    ):
        raise ModeTrackContractError(
            "support anchor references a stale sample acquisition binding"
        )
    _validate_peak_hypothesis_binding(
        processing_owners=processing_owners,
        sample=anchor.sample,
        peak_hypothesis=anchor.peak_hypothesis,
    )
    _validate_support_assessment(
        processing_owners=processing_owners,
        sample=anchor.sample,
        peak_hypothesis=anchor.peak_hypothesis,
        assessment=anchor.direct_support_assessment,
    )


def _validate_assignment_observation_binding(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    candidate: ModeTrackAssignmentObservation,
) -> None:
    if (
        candidate.processing_request_id
        != processing_owners.request.processing_request_id
    ):
        raise ModeTrackContractError(
            "candidate belongs to a different processing request"
        )
    if (
        candidate.processing_attempt_id
        != processing_owners.attempt.processing_attempt_id
    ):
        raise ModeTrackContractError(
            "candidate belongs to a different processing attempt"
        )
    if type(candidate) is ModeTrackRepairAssignmentObservation:
        ModeTrackRepairAssignmentObservation.__post_init__(
            candidate,
            _MODE_TRACK_MINT_TOKEN,
        )
        return
    _validate_peak_hypothesis_binding(
        processing_owners=processing_owners,
        sample=sample,
        peak_hypothesis=candidate.peak_hypothesis,
    )
    if candidate.direct_support_assessment is not None:
        _validate_support_assessment(
            processing_owners=processing_owners,
            sample=sample,
            peak_hypothesis=candidate.peak_hypothesis,
            assessment=candidate.direct_support_assessment,
        )


def _validate_assignment_batch_graph(batch: ModeTrackAssignmentBatch) -> None:
    try:
        if type(batch) is not ModeTrackAssignmentBatch:
            raise TypeError("assignment batch requires exact M8-owned types")
        ModeTrackAssignmentBatch.__post_init__(batch, _MODE_TRACK_MINT_TOKEN)
        for item in batch.assignments:
            ModeTrackAssignment.__post_init__(item, _MODE_TRACK_MINT_TOKEN)
            if type(item.candidate_observation) is ModeTrackCandidateObservation:
                ModeTrackCandidateObservation.__post_init__(
                    item.candidate_observation, _MODE_TRACK_MINT_TOKEN
                )
            elif (
                type(item.candidate_observation)
                is ModeTrackRepairAssignmentObservation
            ):
                ModeTrackRepairAssignmentObservation.__post_init__(
                    item.candidate_observation,
                    _MODE_TRACK_MINT_TOKEN,
                )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ModeTrackContractError(str(exc)) from exc


def _validate_model_set_binding(
    *,
    processing_owners: ProcessingOwnerReferences,
    model_set: ModeTrackModelSet,
) -> None:
    try:
        if (
            type(model_set) is not ModeTrackModelSet
            or type(model_set.policy) is not ModeTrackTrajectoryPolicy
            or type(model_set.family_context) is not FamilyContextRef
            or type(model_set.tracks) is not tuple
            or any(type(track) is not ModeTrackModel for track in model_set.tracks)
        ):
            raise TypeError("model set contains non-owner runtime types")
        validate_family_context_ref_for_consumption(model_set.family_context)
        ModeTrackModelSet.__post_init__(model_set, _MODE_TRACK_MINT_TOKEN)
        ModeTrackTrajectoryPolicy.__post_init__(model_set.policy)
        for track in model_set.tracks:
            ModeTrackModel.__post_init__(track)
            for anchor in track.anchors:
                ModeTrackSupportAnchor.__post_init__(anchor, _MODE_TRACK_MINT_TOKEN)
                _validate_support_anchor(
                    processing_owners=processing_owners, anchor=anchor
                )
        _require_unique_support_entities(
            tuple(anchor for track in model_set.tracks for anchor in track.anchors)
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ModeTrackContractError(str(exc)) from exc


def _require_unique_support_entities(
    anchors: tuple[ModeTrackSupportAnchor, ...],
) -> None:
    hypothesis_ids = tuple(anchor.peak_hypothesis_id for anchor in anchors)
    if len(hypothesis_ids) != len(set(hypothesis_ids)):
        raise ModeTrackContractError(
            "one PeakHypothesis cannot seed multiple ModeTracks"
        )
    observation_ids = tuple(anchor.peak_observation_content_id for anchor in anchors)
    if len(observation_ids) != len(set(observation_ids)):
        raise ModeTrackContractError(
            "one physical peak observation cannot seed multiple ModeTracks"
        )


def _peak_observation_content_id(peak_hypothesis: PeakHypothesis) -> str:
    if len(peak_hypothesis.peak_observation_refs) != 1:
        raise ModeTrackContractError(
            "PeakHypothesis must reference exactly one physical observation"
        )
    return peak_hypothesis.peak_observation_refs[0].content.peak_observation_content_id


def _peak_hypothesis_family_exclusions(
    peak_hypothesis: PeakHypothesis,
    *,
    family_context: FamilyContextRef,
) -> tuple[str, ...]:
    if not peak_hypothesis.peak_observation_refs:
        return ("peak_hypothesis_missing_physical_observation",)
    mz_lower, mz_upper = family_context.mz_interval
    rt_lower, rt_upper = family_context.rt_interval
    reasons: set[str] = set()
    if any(
        not mz_lower <= observation.content.mz <= mz_upper
        for observation in peak_hypothesis.peak_observation_refs
    ):
        reasons.add("peak_hypothesis_outside_family_mz")
    apex_rt = _finite_float(
        peak_hypothesis.integration.rt_apex_min,
        field="peak_hypothesis.integration.rt_apex_min",
    )
    if not rt_lower <= apex_rt <= rt_upper:
        reasons.add("peak_hypothesis_outside_family_rt")
    return tuple(sorted(reasons))


def _validate_trajectory_sample(
    assessment: LocalTrajectoryAssessment,
    sample: ResolvedCanonicalSampleIdentity,
    *,
    processing_owners: ProcessingOwnerReferences,
    acquisition_source: AcquisitionSourceIdentity,
    injection_order: int,
) -> None:
    valid_tokens = {sample.metadata.sample_name, sample.metadata.raw_stem}
    if len(assessment.sample_stems) != 1 or assessment.sample_stems[0] not in (
        valid_tokens
    ):
        raise ModeTrackContractError(
            "local trajectory assessment must cover exactly the assigned sample"
        )
    if any(
        point.injection_order != injection_order for point in assessment.landmark_points
    ):
        raise ModeTrackContractError(
            "local trajectory landmarks reference a stale injection binding"
        )
    if any(
        point.acquisition_source_id != acquisition_source.acquisition_source_id
        for point in assessment.landmark_points
    ):
        raise ModeTrackContractError(
            "local trajectory landmarks reference a stale acquisition source"
        )
    if any(
        point.processing_request_id != processing_owners.request.processing_request_id
        for point in assessment.landmark_points
    ):
        raise ModeTrackContractError(
            "local trajectory landmarks reference a different processing request"
        )
    if any(
        point.processing_attempt_id != processing_owners.attempt.processing_attempt_id
        for point in assessment.landmark_points
    ):
        raise ModeTrackContractError(
            "local trajectory landmarks reference a different processing attempt"
        )


def _trajectory_delta(
    assessment: LocalTrajectoryAssessment,
    *,
    predicted_rt_min: float,
) -> tuple[float | None, tuple[str, ...]]:
    if assessment.status == "conflict":
        return None, tuple(sorted(assessment.reason_codes))
    if assessment.status in {"missing_landmarks", "poor_landmarks"}:
        return 0.0, ("anchor_trajectory_without_local_correction",)
    accepted = tuple(
        point for point in assessment.landmark_points if point.quality == "accepted"
    )
    if not accepted:
        return 0.0, ("anchor_trajectory_without_local_correction",)
    by_local_rt: dict[float, set[float]] = {}
    for point in accepted:
        local_rt = _canonical_float(point.local_trajectory_rt_min)
        by_local_rt.setdefault(local_rt, set()).add(
            _canonical_float(point.rt_drift_delta_min)
        )
    if any(len(deltas) != 1 for deltas in by_local_rt.values()):
        return None, ("conflicting_local_landmark_shifts",)
    local_shifts = tuple(
        sorted(
            (local_rt, next(iter(deltas))) for local_rt, deltas in by_local_rt.items()
        )
    )
    if len(local_shifts) == 1:
        local_rt, shift = local_shifts[0]
        if math.isclose(predicted_rt_min, local_rt, rel_tol=0.0, abs_tol=1e-12):
            return shift, ("exact_local_landmark_shift",)
        return None, ("unsupported_local_landmark_extrapolation",)
    if not local_shifts[0][0] <= predicted_rt_min <= local_shifts[-1][0]:
        return None, ("unsupported_local_landmark_extrapolation",)
    for local_rt, shift in local_shifts:
        if math.isclose(predicted_rt_min, local_rt, rel_tol=0.0, abs_tol=1e-12):
            return shift, ("exact_local_landmark_shift",)
    left = max(item for item in local_shifts if item[0] < predicted_rt_min)
    right = min(item for item in local_shifts if item[0] > predicted_rt_min)
    span = right[0] - left[0]
    fraction = (predicted_rt_min - left[0]) / span
    shift = left[1] + fraction * (right[1] - left[1])
    return _canonical_float(shift), ("interpolated_local_landmark_shift",)


def _bracketing_anchors(
    anchors: tuple[ModeTrackSupportAnchor, ...],
    injection_order: int,
) -> tuple[ModeTrackSupportAnchor, ModeTrackSupportAnchor]:
    left = max(
        (item for item in anchors if item.injection_order < injection_order),
        key=lambda item: item.injection_order,
    )
    right = min(
        (item for item in anchors if item.injection_order > injection_order),
        key=lambda item: item.injection_order,
    )
    return left, right


def _linear_prediction(
    left: ModeTrackSupportAnchor,
    right: ModeTrackSupportAnchor,
    injection_order: int,
) -> float:
    span = right.injection_order - left.injection_order
    if span <= 0:
        raise ModeTrackContractError("trajectory anchors require increasing order")
    fraction = (injection_order - left.injection_order) / span
    return left.apex_rt_min + fraction * (right.apex_rt_min - left.apex_rt_min)


def _crossed_track_ids(
    model_set: ModeTrackModelSet,
    predictions: tuple[ModeTrackPredictionAssessment, ...],
) -> frozenset[str]:
    prediction_by_id = {item.group_hypothesis_id: item for item in predictions}
    ordered = tuple(
        sorted(
            model_set.tracks,
            key=lambda item: (
                item.ordering_reference_rt_min,
                item.group_hypothesis_id,
            ),
        )
    )
    crossed: set[str] = set()
    for left_index, left in enumerate(ordered):
        left_prediction = prediction_by_id[left.group_hypothesis_id].predicted_rt_min
        if left_prediction is None:
            continue
        for right in ordered[left_index + 1 :]:
            right_prediction = prediction_by_id[
                right.group_hypothesis_id
            ].predicted_rt_min
            if right_prediction is None:
                continue
            if (
                left.ordering_reference_rt_min >= right.ordering_reference_rt_min
                or left_prediction >= right_prediction
            ):
                crossed.update((left.group_hypothesis_id, right.group_hypothesis_id))
    return frozenset(crossed)


def _canonical_anchors(
    anchors: tuple[ModeTrackSupportAnchor, ...],
) -> tuple[ModeTrackSupportAnchor, ...]:
    if type(anchors) is not tuple or any(
        type(item) is not ModeTrackSupportAnchor for item in anchors
    ):
        raise ModeTrackContractError("anchors must be owner-minted immutable refs")
    ids = tuple(item.anchor_id for item in anchors)
    if len(ids) != len(set(ids)):
        raise ModeTrackContractError("anchors must be unique")
    return tuple(
        sorted(
            anchors,
            key=lambda item: (
                item.injection_order,
                item.canonical_sample_identity_id,
                item.peak_hypothesis_id,
            ),
        )
    )


def _canonical_assignments(
    assignments: tuple[ModeTrackAssignment, ...],
) -> tuple[ModeTrackAssignment, ...]:
    if type(assignments) is not tuple or any(
        type(item) is not ModeTrackAssignment for item in assignments
    ):
        raise ModeTrackContractError("assignments must be owner-minted refs")
    return tuple(
        sorted(
            assignments,
            key=lambda item: (item.peak_hypothesis_id or "", item.assignment_id),
        )
    )


def _assignment_identity_payload(assignment: ModeTrackAssignment) -> dict[str, object]:
    return _assignment_payload(
        family_context_id=assignment.family_context_id,
        processing_request_id=assignment.processing_request_id,
        processing_attempt_id=assignment.processing_attempt_id,
        canonical_sample_identity_id=assignment.canonical_sample_identity_id,
        acquisition_source_id=assignment.acquisition_source_id,
        injection_order=assignment.injection_order,
        candidate_observation_id=assignment.candidate_observation_id,
        peak_hypothesis_id=assignment.peak_hypothesis_id,
        peak_observation_content_id=assignment.peak_observation_content_id,
        candidate_observation_class=assignment.candidate_observation_class,
        direct_support_assessment_artifact_content_id=(
            assignment.direct_support_assessment_artifact_content_id
        ),
        group_hypothesis_id=assignment.group_hypothesis_id,
        state=assignment.state,
        reason_codes=assignment.reason_codes,
        prediction_assessments=assignment.prediction_assessments,
        trajectory_evidence_refs=assignment.trajectory_evidence_refs,
        lineage_refs=assignment.lineage_refs,
    )


def _assignment_payload(
    *,
    family_context_id: str,
    processing_request_id: str,
    processing_attempt_id: str,
    canonical_sample_identity_id: str,
    acquisition_source_id: str,
    injection_order: int,
    candidate_observation_id: str | None,
    peak_hypothesis_id: str | None,
    peak_observation_content_id: str | None,
    candidate_observation_class: CandidateObservationClass | None,
    direct_support_assessment_artifact_content_id: str | None,
    group_hypothesis_id: str | None,
    state: AssignmentState,
    reason_codes: tuple[str, ...],
    prediction_assessments: tuple[ModeTrackPredictionAssessment, ...],
    trajectory_evidence_refs: tuple[str, ...],
    lineage_refs: tuple[str, ...],
) -> dict[str, object]:
    return {
        "schema": SCHEMA_VERSION,
        "family_context_id": family_context_id,
        "processing_request_id": processing_request_id,
        "processing_attempt_id": processing_attempt_id,
        "canonical_sample_identity_id": canonical_sample_identity_id,
        "acquisition_source_id": acquisition_source_id,
        "injection_order": injection_order,
        "candidate_observation_id": candidate_observation_id,
        "peak_hypothesis_id": peak_hypothesis_id,
        "peak_observation_content_id": peak_observation_content_id,
        "candidate_observation_class": candidate_observation_class,
        "direct_support_assessment_artifact_content_id": (
            direct_support_assessment_artifact_content_id
        ),
        "group_hypothesis_id": group_hypothesis_id,
        "state": state,
        "reason_codes": list(reason_codes),
        "prediction_assessments": [
            _prediction_payload(item) for item in prediction_assessments
        ],
        "trajectory_evidence_refs": list(trajectory_evidence_refs),
        "lineage_refs": list(lineage_refs),
    }


def _batch_identity_payload(batch: ModeTrackAssignmentBatch) -> dict[str, object]:
    return _batch_payload(
        family_context_id=batch.family_context_id,
        model_set_id=batch.model_set_id,
        processing_request_id=batch.processing_request_id,
        processing_attempt_id=batch.processing_attempt_id,
        canonical_sample_identity_id=batch.canonical_sample_identity_id,
        acquisition_source_id=batch.acquisition_source_id,
        injection_order=batch.injection_order,
        trajectory_policy_version=batch.trajectory_policy_version,
        band_policy_version=batch.band_policy_version,
        assignments=batch.assignments,
    )


def _batch_payload(
    *,
    family_context_id: str,
    model_set_id: str,
    processing_request_id: str,
    processing_attempt_id: str,
    canonical_sample_identity_id: str,
    acquisition_source_id: str,
    injection_order: int,
    trajectory_policy_version: str,
    band_policy_version: str,
    assignments: tuple[ModeTrackAssignment, ...],
) -> dict[str, object]:
    return {
        "schema": BATCH_SCHEMA_VERSION,
        "family_context_id": family_context_id,
        "model_set_id": model_set_id,
        "processing_request_id": processing_request_id,
        "processing_attempt_id": processing_attempt_id,
        "canonical_sample_identity_id": canonical_sample_identity_id,
        "acquisition_source_id": acquisition_source_id,
        "injection_order": injection_order,
        "trajectory_policy_version": trajectory_policy_version,
        "band_policy_version": band_policy_version,
        "assignments": [
            {
                "assignment_id": item.assignment_id,
                **_assignment_identity_payload(item),
            }
            for item in assignments
        ],
    }


def _prediction_payload(
    prediction: ModeTrackPredictionAssessment,
) -> dict[str, object]:
    return {
        "group_hypothesis_id": prediction.group_hypothesis_id,
        "status": prediction.status,
        "predicted_rt_min": prediction.predicted_rt_min,
        "prediction_interval_min": (
            None
            if prediction.prediction_interval_min is None
            else list(prediction.prediction_interval_min)
        ),
        "anchor_refs": list(prediction.anchor_refs),
        "support_artifact_content_ids": list(prediction.support_artifact_content_ids),
        "reason_codes": list(prediction.reason_codes),
    }


def _fit_once_source_identity_payload(
    source: ModeTrackFitOnceSource,
) -> dict[str, object]:
    return _fit_once_source_identity_payload_values(
        processing_request_id=source.processing_request_id,
        processing_attempt_id=source.processing_attempt_id,
        family_context_id=source.family_context_id,
        model_set_id=source.model_set_id,
        sample_sources=source.sample_sources,
        initial_assignment_batch_ids=source.initial_assignment_batch_ids,
    )


def _fit_once_source_identity_payload_values(
    *,
    processing_request_id: str,
    processing_attempt_id: str,
    family_context_id: str,
    model_set_id: str,
    sample_sources: tuple[ModeTrackFitOnceSampleSource, ...],
    initial_assignment_batch_ids: tuple[str, ...],
) -> dict[str, object]:
    return {
        "schema": "mode_track_fit_once_source_v1",
        "processing_request_id": processing_request_id,
        "processing_attempt_id": processing_attempt_id,
        "family_context_id": family_context_id,
        "model_set_id": model_set_id,
        "sample_sources": [
            _fit_once_sample_source_payload(item) for item in sample_sources
        ],
        "initial_assignment_batch_ids": list(initial_assignment_batch_ids),
    }


def _fit_once_sample_source_payload(
    source: ModeTrackFitOnceSampleSource,
) -> dict[str, object]:
    return {
        "canonical_sample_identity_id": source.canonical_sample_identity_id,
        "acquisition_source_id": source.acquisition_source_id,
        "injection_order": source.injection_order,
        "candidate_observation_ids": list(source.candidate_observation_ids),
        "trajectory_status": source.trajectory_status,
        "trajectory_reason_codes": list(source.trajectory_reason_codes),
        "trajectory_evidence_refs": list(source.trajectory_evidence_refs),
        "sample": _resolved_sample_identity_payload(source.sample),
        "candidates": [
            _candidate_identity_payload(
                processing_request_id=item.processing_request_id,
                processing_attempt_id=item.processing_attempt_id,
                canonical_sample_identity_id=item.canonical_sample_identity_id,
                acquisition_source_id=item.acquisition_source_id,
                injection_order=item.injection_order,
                peak_hypothesis_id=item.peak_hypothesis_id,
                peak_observation_content_id=item.peak_observation_content_id,
                apex_rt_min=item.apex_rt_min,
                observation_class=item.observation_class,
                direct_support_assessment_artifact_content_id=(
                    item.direct_support_assessment_artifact_content_id
                ),
            )
            for item in source.candidates
        ],
        "trajectory_assessment": _trajectory_assessment_payload(
            source.trajectory_assessment
        ),
    }


def _resolved_sample_identity_payload(
    sample: ResolvedCanonicalSampleIdentity,
) -> dict[str, object]:
    metadata = sample.metadata
    identity = sample.identity
    return {
        "identity": {
            "sample_identity_id": identity.sample_identity_id,
            "sample_metadata_schema_version": identity.sample_metadata_schema_version,
            "identity_namespace": identity.identity_namespace,
            "sample_key": identity.sample_key,
        },
        "snapshot_id": sample.snapshot.snapshot_id,
        "snapshot_content_sha256": sample.snapshot.content_sha256,
        "lookup_token": sample.lookup_token,
        "metadata": {
            "sample_name": metadata.sample_name,
            "raw_stem": metadata.raw_stem,
            "injection_order": metadata.injection_order,
            "sample_role": metadata.sample_role,
            "batch_id": metadata.batch_id,
            "prep_batch_id": metadata.prep_batch_id,
            "matrix_type": metadata.matrix_type,
            "group": metadata.group,
            "excluded": metadata.excluded,
            "exclusion_reason": metadata.exclusion_reason,
        },
    }


def _trajectory_assessment_payload(
    assessment: LocalTrajectoryAssessment,
) -> dict[str, object]:
    return {
        "status": assessment.status,
        "sample_stems": list(assessment.sample_stems),
        "landmark_evidence_refs": list(assessment.landmark_evidence_refs),
        "reason_codes": list(assessment.reason_codes),
        "landmark_points": [
            {
                "sample_stem": point.sample_stem,
                "injection_order": point.injection_order,
                "acquisition_source_id": point.acquisition_source_id,
                "processing_request_id": point.processing_request_id,
                "processing_attempt_id": point.processing_attempt_id,
                "landmark_id": point.landmark_id,
                "observed_rt_min": point.observed_rt_min,
                "local_trajectory_rt_min": point.local_trajectory_rt_min,
                "rt_drift_delta_min": point.rt_drift_delta_min,
                "quality": point.quality,
                "evidence_ref": point.evidence_ref,
                "exclusion_reason": point.exclusion_reason,
                "validation_status": point.validation_status,
                "product_authority": point.product_authority,
            }
            for point in assessment.landmark_points
        ],
        "conflict_evidence_refs": list(assessment.conflict_evidence_refs),
        "validation_status": assessment.validation_status,
        "product_authority": assessment.product_authority,
    }


def _anchor_identity_payload(**values: object) -> dict[str, object]:
    return {"schema": "mode_track_support_anchor_v2", **values}


def _candidate_identity_payload(**values: object) -> dict[str, object]:
    return {"schema": "mode_track_candidate_observation_v2", **values}


def _repair_assignment_observation_payload(
    **values: object,
) -> dict[str, object]:
    return {"schema": "mode_track_repair_assignment_observation_v1", **values}


def _model_set_identity_payload(
    *,
    family_context_id: str,
    processing_request_id: str,
    processing_attempt_id: str,
    policy: ModeTrackTrajectoryPolicy,
    tracks: tuple[ModeTrackModel, ...],
    lineage_refs: tuple[str, ...],
) -> dict[str, object]:
    return {
        "schema": MODEL_SET_SCHEMA_VERSION,
        "family_context_id": family_context_id,
        "processing_request_id": processing_request_id,
        "processing_attempt_id": processing_attempt_id,
        "policy": {
            "trajectory_policy_version": policy.trajectory_policy_version,
            "band_policy_version": policy.band_policy_version,
            "prediction_half_width_min": policy.prediction_half_width_min,
            "max_interpolation_gap": policy.max_interpolation_gap,
            "max_extrapolation_gap": policy.max_extrapolation_gap,
        },
        "tracks": [
            {
                "group_hypothesis_id": track.group_hypothesis_id,
                "ordering_reference_rt_min": track.ordering_reference_rt_min,
                "anchor_ids": [anchor.anchor_id for anchor in track.anchors],
            }
            for track in tracks
        ],
        "lineage_refs": list(lineage_refs),
    }


def _typed_digest(prefix: str, value: object) -> str:
    return f"{prefix}{hashlib.sha256(_canonical_json_bytes(value)).hexdigest()}"


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _interval(value: object, *, field: str) -> tuple[float, float]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ModeTrackContractError(f"{field} must contain two bounds")
    lower = _finite_float(value[0], field=f"{field}[0]")
    upper = _finite_float(value[1], field=f"{field}[1]")
    if lower >= upper:
        raise ModeTrackContractError(f"{field} must be increasing")
    return lower, upper


def _finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ModeTrackContractError(f"{field} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ModeTrackContractError(f"{field} must be finite")
    return _canonical_float(normalized)


def _positive_float(value: object, *, field: str) -> float:
    normalized = _finite_float(value, field=field)
    if normalized <= 0:
        raise ModeTrackContractError(f"{field} must be positive")
    return normalized


def _canonical_float(value: float) -> float:
    return 0.0 if value == 0.0 else float(value)


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ModeTrackContractError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ModeTrackContractError(f"{field} must be a non-negative integer")
    return value


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ModeTrackContractError(f"{field} must be canonical non-empty text")
    return value


def _require_canonical_texts(
    values: tuple[str, ...],
    *,
    field: str,
    allow_empty: bool = False,
) -> None:
    if not isinstance(values, tuple) or (not allow_empty and not values):
        raise ModeTrackContractError(f"{field} must be a non-empty tuple")
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ModeTrackContractError(f"{field} must contain non-empty text")
    if values != tuple(sorted(set(values))):
        raise ModeTrackContractError(f"{field} must be unique and canonical-sorted")


def _validate_shadow_authority(validation_status: str, product_authority: str) -> None:
    if validation_status != VALIDATION_STATUS:
        raise ModeTrackContractError("validation_status must remain shadow_ready")
    if product_authority != PRODUCT_AUTHORITY:
        raise ModeTrackContractError(
            "ModeTrack evidence cannot grant product authority"
        )


__all__ = (
    "BATCH_SCHEMA_VERSION",
    "MODEL_SET_SCHEMA_VERSION",
    "PRODUCT_AUTHORITY",
    "SCHEMA_VERSION",
    "VALIDATION_STATUS",
    "ModeTrackAssignment",
    "ModeTrackAssignmentBatch",
    "ModeTrackCandidateObservation",
    "ModeTrackContractError",
    "ModeTrackFitOnceSampleSource",
    "ModeTrackFitOnceSource",
    "ModeTrackModel",
    "ModeTrackModelSet",
    "ModeTrackPredictionAssessment",
    "ModeTrackSupportAnchor",
    "ModeTrackTrajectoryPolicy",
    "assign_mode_tracks",
    "canonical_mode_track_assignment_batch_bytes",
    "fit_mode_track_model_set",
    "mint_mode_track_candidate_observation",
    "mint_mode_track_fit_once_sample_source",
    "mint_mode_track_fit_once_source",
    "mint_mode_track_support_anchor",
    "reinforce_mode_track_model_set",
    "validate_mode_track_assignment_batch_for_consumption",
    "validate_mode_track_fit_once_source_for_consumption",
)
