"""ModeTrack assignment contracts, minting, validation, and codecs."""


from __future__ import annotations

import hashlib
import json
import math
from dataclasses import InitVar, dataclass, field
from typing import Literal, final

from xic_extractor.alignment.drift_evidence import (
    LocalTrajectoryAssessment,
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
    """Raised when a ModeTrack model/assignment owner reference is crossed,
    forged, or incomplete.
    """


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
    """Explicit lane policy; the ModeTrack model/assignment owner
    intentionally owns no universal thresholds.
    """

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
                "ModeTrackSupportAnchor must be minted by the ModeTrack "
                "model/assignment owner"
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
                "ModeTrackModelSet must be minted by the ModeTrack "
                "model/assignment owner"
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
                "ModeTrackCandidateObservation must be minted by the ModeTrack "
                "model/assignment owner"
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
                "ModeTrackAssignment must be minted by the ModeTrack "
                "model/assignment owner"
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
                "ModeTrackAssignmentBatch must be minted by the ModeTrack "
                "model/assignment owner"
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
            raise TypeError("assignment batch requires exact ModeTrack-owned types")
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
