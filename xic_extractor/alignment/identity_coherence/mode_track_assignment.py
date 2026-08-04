"""Shadow-only owner for injection-local ModeTrack assignment evidence."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import InitVar, dataclass, field
from typing import Literal, final

from xic_extractor.alignment.cell_observations import CellObservationRef
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.drift_evidence import LocalTrajectoryAssessment
from xic_extractor.alignment.hypothesis_owner_adapter import (
    validate_cid_nl_assessment_hypothesis_closure,
)
from xic_extractor.alignment.identity_coherence.family_context import (
    FamilyContextRef,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    ProcessingOwnerReferences,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "mode_track_assignment_v1"
BATCH_SCHEMA_VERSION = "mode_track_assignment_batch_v1"
MODEL_SET_SCHEMA_VERSION = "mode_track_model_set_v1"
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
    "unsupported_interpolation",
    "unsupported_extrapolation",
]

_MODE_TRACK_MINT_TOKEN = object()


class ModeTrackContractError(ValueError):
    """Raised when an M8 owner reference is crossed, forged, or incomplete."""


@final
@dataclass(frozen=True, slots=True)
class ModeTrackTrajectoryPolicy:
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
        if half_width != self.prediction_half_width_min:
            raise ModeTrackContractError(
                "prediction_half_width_min must use a canonical float"
            )
        _nonnegative_int(self.max_interpolation_gap, field="max_interpolation_gap")
        _nonnegative_int(self.max_extrapolation_gap, field="max_extrapolation_gap")


@final
@dataclass(frozen=True, slots=True)
class ModeTrackSupportAnchor:
    """One exact direct-SUPPORT training fact assigned to one ModeTrack."""

    anchor_id: str
    group_hypothesis_id: str
    canonical_sample_identity_id: str
    acquisition_source_id: str
    injection_order: int
    apex_rt_min: float
    peak_hypothesis_id: str
    support_cell_artifact_content_id: str | None
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
    direct_support_cell: CellObservationRef | None = field(
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
        _positive_int(self.injection_order, field="injection_order")
        _finite_float(self.apex_rt_min, field="apex_rt_min")
        _require_canonical_texts(
            self.evidence_artifact_ids,
            field="evidence_artifact_ids",
        )
        if self.source_assignment_id is not None:
            _required_text(self.source_assignment_id, field="source_assignment_id")
        expected = _anchor_identity_payload(
            group_hypothesis_id=self.group_hypothesis_id,
            canonical_sample_identity_id=self.canonical_sample_identity_id,
            acquisition_source_id=self.acquisition_source_id,
            injection_order=self.injection_order,
            apex_rt_min=self.apex_rt_min,
            peak_hypothesis_id=self.peak_hypothesis_id,
            support_cell_artifact_content_id=(self.support_cell_artifact_content_id),
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
class ModeTrackModel:
    """One existing group identity plus its direct-SUPPORT trajectory anchors."""

    group_hypothesis_id: str
    anchors: tuple[ModeTrackSupportAnchor, ...]
    track: CrossSamplePeakGroupHypothesis = field(compare=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.track, CrossSamplePeakGroupHypothesis):
            raise ModeTrackContractError("track must be an existing group hypothesis")
        if self.group_hypothesis_id != self.track.group_hypothesis_id:
            raise ModeTrackContractError("track group identity was substituted")
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
class ModeTrackModelSet:
    """One fit-once family snapshot used by all sample assignments."""

    model_set_id: str
    family_context_id: str
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
        _require_canonical_texts(self.lineage_refs, field="lineage_refs")
        _validate_shadow_authority(self.validation_status, self.product_authority)
        expected = _model_set_identity_payload(
            family_context_id=self.family_context_id,
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
class ModeTrackCandidateObservation:
    """One owner-backed PeakHypothesis eligible for shadow assignment."""

    candidate_observation_id: str
    canonical_sample_identity_id: str
    acquisition_source_id: str
    injection_order: int
    peak_hypothesis_id: str
    apex_rt_min: float
    observation_class: CandidateObservationClass
    direct_support_cell_artifact_content_id: str | None
    sample: ResolvedCanonicalSampleIdentity = field(compare=False, repr=False)
    acquisition_source: AcquisitionSourceIdentity = field(compare=False, repr=False)
    peak_hypothesis: PeakHypothesis = field(compare=False, repr=False)
    direct_support_cell: CellObservationRef | None = field(
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
            if self.direct_support_cell is None:
                raise ModeTrackContractError(
                    "direct_support candidate requires its exact SUPPORT cell"
                )
        elif self.direct_support_cell is not None:
            raise ModeTrackContractError(
                "non_training candidate cannot retain a training SUPPORT cell"
            )
        _positive_int(self.injection_order, field="injection_order")
        expected = _candidate_identity_payload(
            canonical_sample_identity_id=self.canonical_sample_identity_id,
            acquisition_source_id=self.acquisition_source_id,
            injection_order=self.injection_order,
            peak_hypothesis_id=self.peak_hypothesis_id,
            apex_rt_min=self.apex_rt_min,
            observation_class=self.observation_class,
            direct_support_cell_artifact_content_id=(
                self.direct_support_cell_artifact_content_id
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
class ModeTrackPredictionAssessment:
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
            "unsupported_interpolation",
            "unsupported_extrapolation",
        }:
            raise ModeTrackContractError("unsupported prediction status")
        _require_canonical_texts(self.anchor_refs, field="anchor_refs")
        _require_canonical_texts(
            self.support_artifact_content_ids,
            field="support_artifact_content_ids",
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
class ModeTrackAssignment:
    """One immutable, shadow-only candidate-to-track assignment fact."""

    assignment_id: str
    family_context_id: str
    canonical_sample_identity_id: str
    acquisition_source_id: str
    injection_order: int
    peak_hypothesis_id: str | None
    candidate_observation_class: CandidateObservationClass | None
    group_hypothesis_id: str | None
    state: AssignmentState
    reason_codes: tuple[str, ...]
    prediction_assessments: tuple[ModeTrackPredictionAssessment, ...]
    trajectory_evidence_refs: tuple[str, ...]
    lineage_refs: tuple[str, ...]
    sample: ResolvedCanonicalSampleIdentity = field(compare=False, repr=False)
    acquisition_source: AcquisitionSourceIdentity = field(compare=False, repr=False)
    peak_hypothesis: PeakHypothesis | None = field(compare=False, repr=False)
    direct_support_cell: CellObservationRef | None = field(
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
        _positive_int(self.injection_order, field="injection_order")
        _require_canonical_texts(self.reason_codes, field="reason_codes")
        _require_canonical_texts(
            self.trajectory_evidence_refs,
            field="trajectory_evidence_refs",
            allow_empty=True,
        )
        _require_canonical_texts(self.lineage_refs, field="lineage_refs")
        prediction_ids = tuple(
            item.group_hypothesis_id for item in self.prediction_assessments
        )
        if prediction_ids != tuple(sorted(set(prediction_ids))):
            raise ModeTrackContractError(
                "prediction assessments must be unique and canonical-sorted"
            )
        if self.state == "uniquely_assigned":
            if self.group_hypothesis_id is None or self.peak_hypothesis is None:
                raise ModeTrackContractError(
                    "unique assignment requires one track and PeakHypothesis"
                )
        elif self.group_hypothesis_id is not None:
            raise ModeTrackContractError(
                "non-unique assignment cannot grant a ModeTrack identity"
            )
        if self.state == "not_observed":
            if self.peak_hypothesis is not None:
                raise ModeTrackContractError(
                    "not_observed assignment cannot carry a PeakHypothesis"
                )
        elif self.peak_hypothesis is None:
            raise ModeTrackContractError(
                "observed assignment state requires a PeakHypothesis"
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


@final
@dataclass(frozen=True, slots=True)
class ModeTrackAssignmentBatch:
    """Exactly one deterministic assignment pass for one sample and model set."""

    assignment_batch_id: str
    family_context_id: str
    model_set_id: str
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
        _positive_int(self.injection_order, field="injection_order")
        canonical = _canonical_assignments(self.assignments)
        if self.assignments != canonical:
            raise ModeTrackContractError("assignments must be canonical-sorted")
        if any(
            item.family_context_id != self.family_context_id
            or item.canonical_sample_identity_id != self.canonical_sample_identity_id
            or item.acquisition_source_id != self.acquisition_source_id
            or item.injection_order != self.injection_order
            for item in self.assignments
        ):
            raise ModeTrackContractError(
                "assignment batch contains crossed Family or sample references"
            )
        hypothesis_ids = tuple(
            item.peak_hypothesis_id
            for item in self.assignments
            if item.peak_hypothesis_id is not None
        )
        if len(hypothesis_ids) != len(set(hypothesis_ids)):
            raise ModeTrackContractError(
                "each PeakHypothesis may be assigned at most once per sample"
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
    if not isinstance(family_context, FamilyContextRef):
        raise ModeTrackContractError("family_context must be owner-minted")
    if not isinstance(policy, ModeTrackTrajectoryPolicy):
        raise ModeTrackContractError("policy must be explicit and typed")
    if not isinstance(direct_support_anchors, tuple):
        raise ModeTrackContractError("direct_support_anchors must be a tuple")
    anchors_by_track: dict[str, list[ModeTrackSupportAnchor]] = {
        group_id: [] for group_id in family_context.group_hypothesis_ids
    }
    track_by_id = {
        track.group_hypothesis_id: track for track in family_context.mode_tracks
    }
    for anchor in direct_support_anchors:
        if not isinstance(anchor, ModeTrackSupportAnchor):
            raise ModeTrackContractError(
                "training inputs must be owner-minted ModeTrackSupportAnchor refs"
            )
        group_id = anchor.group_hypothesis_id
        if group_id not in anchors_by_track:
            raise ModeTrackContractError(
                "training SUPPORT anchor belongs to a group outside the Family"
            )
        if (
            anchor.source_assignment_id is None
            and anchor.peak_hypothesis_id not in track_by_id[group_id].owner_ids
        ):
            raise ModeTrackContractError(
                "initial direct SUPPORT anchor is not an owner of its ModeTrack"
            )
        _resolve_sample_acquisition(processing_owners, anchor.sample)
        anchors_by_track[group_id].append(anchor)
    tracks = tuple(
        ModeTrackModel(
            group_hypothesis_id=track.group_hypothesis_id,
            anchors=_canonical_anchors(
                tuple(anchors_by_track[track.group_hypothesis_id])
            ),
            track=track,
        )
        for track in family_context.mode_tracks
    )
    lineage_refs = tuple(
        sorted(
            {
                family_context.family_context_id,
                *family_context.source_lineage_refs,
                *(
                    anchor.support_cell_artifact_content_id
                    for track in tracks
                    for anchor in track.anchors
                    if anchor.support_cell_artifact_content_id is not None
                ),
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

    if not isinstance(track, CrossSamplePeakGroupHypothesis):
        raise ModeTrackContractError("track must be an existing group hypothesis")
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
        direct_support_cell=None,
        source_assignment_id=None,
    )


def mint_mode_track_candidate_observation(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    peak_hypothesis: PeakHypothesis,
    direct_support_cell: CellObservationRef | None = None,
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
    support_content_id: str | None = None
    observation_class: CandidateObservationClass = "non_training"
    if direct_support_cell is not None:
        _validate_support_cell(
            sample=sample,
            peak_hypothesis=peak_hypothesis,
            cell=direct_support_cell,
        )
        observation_class = "direct_support"
        support_content_id = (
            direct_support_cell.artifact.content_identity.artifact_content_id
        )
    apex_rt_min = _finite_float(
        peak_hypothesis.integration.rt_apex_min,
        field="peak_hypothesis.integration.rt_apex_min",
    )
    payload = _candidate_identity_payload(
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=acquisition_source.acquisition_source_id,
        injection_order=injection_order,
        peak_hypothesis_id=peak_hypothesis.hypothesis_id,
        apex_rt_min=apex_rt_min,
        observation_class=observation_class,
        direct_support_cell_artifact_content_id=support_content_id,
    )
    return ModeTrackCandidateObservation(
        candidate_observation_id=_typed_digest("mode-track-candidate:", payload),
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=acquisition_source.acquisition_source_id,
        injection_order=injection_order,
        peak_hypothesis_id=peak_hypothesis.hypothesis_id,
        apex_rt_min=apex_rt_min,
        observation_class=observation_class,
        direct_support_cell_artifact_content_id=support_content_id,
        sample=sample,
        acquisition_source=acquisition_source,
        peak_hypothesis=peak_hypothesis,
        direct_support_cell=direct_support_cell,
        _mint_token=_MODE_TRACK_MINT_TOKEN,
    )


def assign_mode_tracks(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    model_set: ModeTrackModelSet,
    candidates: tuple[ModeTrackCandidateObservation, ...],
    trajectory_assessment: LocalTrajectoryAssessment,
) -> ModeTrackAssignmentBatch:
    """Assign every sample hypothesis once without granting product authority."""

    acquisition_source, injection_order = _resolve_sample_acquisition(
        processing_owners,
        sample,
    )
    if not isinstance(model_set, ModeTrackModelSet):
        raise ModeTrackContractError("model_set must be owner-minted")
    if not isinstance(candidates, tuple):
        raise ModeTrackContractError("candidates must be an immutable tuple")
    if not isinstance(trajectory_assessment, LocalTrajectoryAssessment):
        raise ModeTrackContractError(
            "trajectory_assessment must be an M3 owner assessment"
        )
    _validate_trajectory_sample(trajectory_assessment, sample)
    canonical_candidates = tuple(
        sorted(candidates, key=lambda item: item.candidate_observation_id)
    )
    if len({item.candidate_observation_id for item in canonical_candidates}) != len(
        canonical_candidates
    ):
        raise ModeTrackContractError("candidate observations must be unique")
    for candidate in canonical_candidates:
        if not isinstance(candidate, ModeTrackCandidateObservation):
            raise ModeTrackContractError("candidates must be owner-minted")
        if (
            candidate.canonical_sample_identity_id != sample.identity.sample_identity_id
            or candidate.acquisition_source_id
            != acquisition_source.acquisition_source_id
            or candidate.injection_order != injection_order
        ):
            raise ModeTrackContractError(
                "candidate observation references a different sample acquisition"
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
            }
        )
    )
    if not canonical_candidates:
        not_observed_assignments = (
            _mint_assignment(
                family_context_id=model_set.family_context_id,
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

    global_state = _global_no_write_state(
        model_set=model_set,
        predictions=predictions,
        trajectory_assessment=trajectory_assessment,
    )
    provisional = tuple(
        _assign_candidate(
            candidate=candidate,
            family_context_id=model_set.family_context_id,
            sample=sample,
            acquisition_source=acquisition_source,
            injection_order=injection_order,
            predictions=predictions,
            trajectory_assessment=trajectory_assessment,
            lineage_refs=lineage_refs,
            global_state=global_state,
        )
        for candidate in canonical_candidates
    )
    unique_by_track: dict[str, list[ModeTrackAssignment]] = {}
    for assignment in provisional:
        if assignment.state == "uniquely_assigned":
            assert assignment.group_hypothesis_id is not None
            unique_by_track.setdefault(assignment.group_hypothesis_id, []).append(
                assignment
            )
    occupied_ids = {
        assignment.assignment_id
        for same_track in unique_by_track.values()
        if len(same_track) > 1
        for assignment in same_track
    }
    assignments = tuple(
        _remint_assignment(
            assignment,
            group_hypothesis_id=None,
            state="ambiguous_multiple_tracks",
            reason_codes=("track_occupancy_conflict",),
        )
        if assignment.assignment_id in occupied_ids
        else assignment
        for assignment in provisional
    )
    return _mint_assignment_batch(
        model_set=model_set,
        sample=sample,
        acquisition_source=acquisition_source,
        injection_order=injection_order,
        assignments=assignments,
    )


def reinforce_mode_track_model_set(
    *,
    model_set: ModeTrackModelSet,
    assignment_batch: ModeTrackAssignmentBatch,
) -> ModeTrackModelSet:
    """Add only uniquely assigned direct SUPPORT facts to exactly one track."""

    if not isinstance(model_set, ModeTrackModelSet) or not isinstance(
        assignment_batch,
        ModeTrackAssignmentBatch,
    ):
        raise ModeTrackContractError("reinforcement requires owner-minted inputs")
    if (
        assignment_batch.model_set_id != model_set.model_set_id
        or assignment_batch.family_context_id != model_set.family_context_id
    ):
        raise ModeTrackContractError(
            "assignment batch was produced from a different model set"
        )
    additions: dict[str, list[ModeTrackSupportAnchor]] = {}
    for assignment in assignment_batch.assignments:
        if (
            assignment.state != "uniquely_assigned"
            or assignment.candidate_observation_class != "direct_support"
        ):
            continue
        assert assignment.group_hypothesis_id is not None
        assert assignment.direct_support_cell is not None
        assert assignment.peak_hypothesis is not None
        anchor = _mint_support_anchor(
            processing_owners=None,
            sample=assignment.sample,
            acquisition_source=assignment.acquisition_source,
            injection_order=assignment.injection_order,
            group_hypothesis_id=assignment.group_hypothesis_id,
            direct_support_assessment=(
                assignment.direct_support_cell.method_assessment
            ),
            direct_support_cell=assignment.direct_support_cell,
            peak_hypothesis=assignment.peak_hypothesis,
            source_assignment_id=assignment.assignment_id,
        )
        additions.setdefault(assignment.group_hypothesis_id, []).append(anchor)
    if not additions:
        return model_set
    updated_tracks: list[ModeTrackModel] = []
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
        updated_tracks.append(
            ModeTrackModel(
                group_hypothesis_id=track.group_hypothesis_id,
                anchors=_canonical_anchors(tuple(anchors)),
                track=track.track,
            )
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
        policy=model_set.policy,
        tracks=tuple(updated_tracks),
        lineage_refs=lineage_refs,
    )


def canonical_mode_track_assignment_batch_bytes(
    batch: ModeTrackAssignmentBatch,
) -> bytes:
    """Serialize one owner-validated batch without public/product projections."""

    if not isinstance(batch, ModeTrackAssignmentBatch):
        raise TypeError("canonical serialization requires ModeTrackAssignmentBatch")
    if batch.assignment_batch_id != _typed_digest(
        "mode-track-assignment-batch:",
        _batch_identity_payload(batch),
    ):
        raise ModeTrackContractError(
            "assignment_batch_id mismatches canonical owner content"
        )
    payload = _batch_identity_payload(batch)
    payload["schema_version"] = BATCH_SCHEMA_VERSION
    payload["assignment_batch_id"] = batch.assignment_batch_id
    payload["validation_status"] = batch.validation_status
    payload["product_authority"] = batch.product_authority
    return _canonical_json_bytes(payload)


def _mint_support_anchor(
    *,
    processing_owners: ProcessingOwnerReferences | None,
    sample: ResolvedCanonicalSampleIdentity,
    group_hypothesis_id: str,
    direct_support_assessment: CidNlMethodAssessmentRef,
    peak_hypothesis: PeakHypothesis,
    direct_support_cell: CellObservationRef | None,
    source_assignment_id: str | None,
    acquisition_source: AcquisitionSourceIdentity | None = None,
    injection_order: int | None = None,
) -> ModeTrackSupportAnchor:
    _validate_support_assessment(
        sample=sample,
        peak_hypothesis=peak_hypothesis,
        assessment=direct_support_assessment,
    )
    if direct_support_cell is not None:
        _validate_support_cell(
            sample=sample,
            peak_hypothesis=peak_hypothesis,
            cell=direct_support_cell,
        )
        if direct_support_cell.method_assessment is not direct_support_assessment:
            raise ModeTrackContractError(
                "SUPPORT cell and anchor must reuse the exact assessment"
            )
    if processing_owners is not None:
        source, order = _resolve_sample_acquisition(processing_owners, sample)
    else:
        if acquisition_source is None or injection_order is None:
            raise ModeTrackContractError(
                "reinforcement requires retained acquisition and injection identity"
            )
        source, order = acquisition_source, injection_order
    apex_rt_min = _finite_float(
        peak_hypothesis.integration.rt_apex_min,
        field="peak_hypothesis.integration.rt_apex_min",
    )
    cell_content_id = (
        None
        if direct_support_cell is None
        else direct_support_cell.artifact.content_identity.artifact_content_id
    )
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
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=source.acquisition_source_id,
        injection_order=order,
        apex_rt_min=apex_rt_min,
        peak_hypothesis_id=peak_hypothesis.hypothesis_id,
        support_cell_artifact_content_id=cell_content_id,
        support_assessment_artifact_content_id=assessment_content_id,
        evidence_artifact_ids=evidence_ids,
        source_assignment_id=source_assignment_id,
    )
    return ModeTrackSupportAnchor(
        anchor_id=_typed_digest("mode-track-anchor:", payload),
        group_hypothesis_id=group_hypothesis_id,
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition_source_id=source.acquisition_source_id,
        injection_order=order,
        apex_rt_min=apex_rt_min,
        peak_hypothesis_id=peak_hypothesis.hypothesis_id,
        support_cell_artifact_content_id=cell_content_id,
        support_assessment_artifact_content_id=assessment_content_id,
        evidence_artifact_ids=evidence_ids,
        source_assignment_id=source_assignment_id,
        sample=sample,
        acquisition_source=source,
        peak_hypothesis=peak_hypothesis,
        direct_support_assessment=direct_support_assessment,
        direct_support_cell=direct_support_cell,
        _mint_token=_MODE_TRACK_MINT_TOKEN,
    )


def _mint_model_set(
    *,
    family_context: FamilyContextRef,
    policy: ModeTrackTrajectoryPolicy,
    tracks: tuple[ModeTrackModel, ...],
    lineage_refs: tuple[str, ...],
) -> ModeTrackModelSet:
    canonical_tracks = tuple(sorted(tracks, key=lambda item: item.group_hypothesis_id))
    canonical_lineage = tuple(sorted(set(lineage_refs)))
    payload = _model_set_identity_payload(
        family_context_id=family_context.family_context_id,
        policy=policy,
        tracks=canonical_tracks,
        lineage_refs=canonical_lineage,
    )
    return ModeTrackModelSet(
        model_set_id=_typed_digest("mode-track-model-set:", payload),
        family_context_id=family_context.family_context_id,
        policy=policy,
        tracks=canonical_tracks,
        lineage_refs=canonical_lineage,
        family_context=family_context,
        _mint_token=_MODE_TRACK_MINT_TOKEN,
    )


def _predict_track(
    track: ModeTrackModel,
    *,
    injection_order: int,
    trajectory_assessment: LocalTrajectoryAssessment,
    policy: ModeTrackTrajectoryPolicy,
) -> ModeTrackPredictionAssessment:
    anchors = track.anchors
    anchor_refs = tuple(sorted(anchor.anchor_id for anchor in anchors))
    support_refs = tuple(
        sorted(
            anchor.support_cell_artifact_content_id
            or anchor.support_assessment_artifact_content_id
            for anchor in anchors
        )
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
    orders = tuple(anchor.injection_order for anchor in anchors)
    if injection_order in orders:
        center = anchors[orders.index(injection_order)].apex_rt_min
    elif orders[0] < injection_order < orders[-1]:
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
    drift_delta = _trajectory_delta(trajectory_assessment)
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
        reason_codes=("prediction_available",),
    )


def _global_no_write_state(
    *,
    model_set: ModeTrackModelSet,
    predictions: tuple[ModeTrackPredictionAssessment, ...],
    trajectory_assessment: LocalTrajectoryAssessment,
) -> tuple[AssignmentState, tuple[str, ...]] | None:
    if trajectory_assessment.status != "assessable":
        return (
            "technical_unassessable",
            tuple(sorted(trajectory_assessment.reason_codes)),
        )
    unavailable = tuple(item for item in predictions if item.status != "predicted")
    if unavailable:
        statuses = {item.status for item in unavailable}
        reasons = tuple(
            sorted({reason for item in unavailable for reason in item.reason_codes})
        )
        state: AssignmentState = (
            "technical_unassessable" if "sparse_anchors" in statuses else "out_of_model"
        )
        return state, reasons
    if _trajectories_cross(model_set, predictions):
        return "technical_unassessable", ("trajectory_crossing",)
    if _prediction_intervals_overlap(predictions):
        return "ambiguous_multiple_tracks", ("prediction_intervals_overlap",)
    return None


def _assign_candidate(
    *,
    candidate: ModeTrackCandidateObservation,
    family_context_id: str,
    sample: ResolvedCanonicalSampleIdentity,
    acquisition_source: AcquisitionSourceIdentity,
    injection_order: int,
    predictions: tuple[ModeTrackPredictionAssessment, ...],
    trajectory_assessment: LocalTrajectoryAssessment,
    lineage_refs: tuple[str, ...],
    global_state: tuple[AssignmentState, tuple[str, ...]] | None,
) -> ModeTrackAssignment:
    if global_state is not None:
        state, reasons = global_state
        group_id = None
    else:
        matching = tuple(
            item
            for item in predictions
            if item.prediction_interval_min is not None
            and item.prediction_interval_min[0]
            <= candidate.apex_rt_min
            <= item.prediction_interval_min[1]
        )
        if len(matching) == 1:
            state = "uniquely_assigned"
            group_id = matching[0].group_hypothesis_id
            reasons = ("unique_interval_all_competitors_excluded",)
        elif len(matching) > 1:
            state = "ambiguous_multiple_tracks"
            group_id = None
            reasons = ("candidate_matches_multiple_tracks",)
        else:
            state = "out_of_model"
            group_id = None
            reasons = ("candidate_outside_all_prediction_intervals",)
    return _mint_assignment(
        family_context_id=family_context_id,
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
    sample: ResolvedCanonicalSampleIdentity,
    acquisition_source: AcquisitionSourceIdentity,
    injection_order: int,
    candidate: ModeTrackCandidateObservation | None,
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
    observation_class = None if candidate is None else candidate.observation_class
    trajectory_refs = tuple(sorted(trajectory_assessment.landmark_evidence_refs))
    identity_payload = _assignment_payload(
        family_context_id=family_context_id,
        canonical_sample_identity_id=sample_id,
        acquisition_source_id=source_id,
        injection_order=injection_order,
        peak_hypothesis_id=hypothesis_id,
        candidate_observation_class=observation_class,
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
        canonical_sample_identity_id=sample_id,
        acquisition_source_id=source_id,
        injection_order=injection_order,
        peak_hypothesis_id=hypothesis_id,
        candidate_observation_class=observation_class,
        group_hypothesis_id=group_hypothesis_id,
        state=state,
        reason_codes=canonical_reasons,
        prediction_assessments=canonical_predictions,
        trajectory_evidence_refs=trajectory_refs,
        lineage_refs=canonical_lineage,
        sample=sample,
        acquisition_source=acquisition_source,
        peak_hypothesis=None if candidate is None else candidate.peak_hypothesis,
        direct_support_cell=(
            None if candidate is None else candidate.direct_support_cell
        ),
        _mint_token=_MODE_TRACK_MINT_TOKEN,
    )


def _remint_assignment(
    assignment: ModeTrackAssignment,
    *,
    group_hypothesis_id: str | None,
    state: AssignmentState,
    reason_codes: tuple[str, ...],
) -> ModeTrackAssignment:
    canonical_reasons = tuple(sorted(set(reason_codes)))
    identity_payload = _assignment_payload(
        family_context_id=assignment.family_context_id,
        canonical_sample_identity_id=assignment.canonical_sample_identity_id,
        acquisition_source_id=assignment.acquisition_source_id,
        injection_order=assignment.injection_order,
        peak_hypothesis_id=assignment.peak_hypothesis_id,
        candidate_observation_class=assignment.candidate_observation_class,
        group_hypothesis_id=group_hypothesis_id,
        state=state,
        reason_codes=canonical_reasons,
        prediction_assessments=assignment.prediction_assessments,
        trajectory_evidence_refs=assignment.trajectory_evidence_refs,
        lineage_refs=assignment.lineage_refs,
    )
    return ModeTrackAssignment(
        assignment_id=_typed_digest(
            "mode-track-assignment:",
            identity_payload,
        ),
        family_context_id=assignment.family_context_id,
        canonical_sample_identity_id=assignment.canonical_sample_identity_id,
        acquisition_source_id=assignment.acquisition_source_id,
        injection_order=assignment.injection_order,
        peak_hypothesis_id=assignment.peak_hypothesis_id,
        candidate_observation_class=assignment.candidate_observation_class,
        group_hypothesis_id=group_hypothesis_id,
        state=state,
        reason_codes=canonical_reasons,
        prediction_assessments=assignment.prediction_assessments,
        trajectory_evidence_refs=assignment.trajectory_evidence_refs,
        lineage_refs=assignment.lineage_refs,
        sample=assignment.sample,
        acquisition_source=assignment.acquisition_source,
        peak_hypothesis=assignment.peak_hypothesis,
        direct_support_cell=assignment.direct_support_cell,
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


def _resolve_cell_sample(
    processing_owners: ProcessingOwnerReferences,
    cell: CellObservationRef,
) -> ResolvedCanonicalSampleIdentity:
    matching = tuple(
        sample
        for sample in processing_owners.sample_resolutions
        if sample.identity.sample_identity_id
        == cell.content.canonical_sample_identity_id
    )
    if len(matching) != 1:
        raise ModeTrackContractError(
            "SUPPORT cell must resolve to one request-bound canonical sample"
        )
    return matching[0]


def _resolve_sample_acquisition(
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> tuple[AcquisitionSourceIdentity, int]:
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise ModeTrackContractError("processing_owners must be owner-validated")
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise ModeTrackContractError("sample must be owner-resolved")
    source = resolve_request_acquisition_source(
        processing_owners=processing_owners,
        sample=sample,
    )
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


def _validate_support_cell(
    *,
    sample: ResolvedCanonicalSampleIdentity,
    peak_hypothesis: PeakHypothesis,
    cell: CellObservationRef,
) -> None:
    if not isinstance(cell, CellObservationRef):
        raise ModeTrackContractError("direct SUPPORT proof must be a cell reference")
    if cell.content.method_result != "SUPPORT":
        raise ModeTrackContractError("only direct SUPPORT may train a ModeTrack")
    if cell.peak_hypothesis is not peak_hypothesis:
        raise ModeTrackContractError(
            "SUPPORT cell and candidate must reuse the exact PeakHypothesis"
        )
    validate_cid_nl_assessment_hypothesis_closure(
        sample=sample,
        assessment=cell.method_assessment,
        hypothesis=peak_hypothesis,
    )


def _validate_support_assessment(
    *,
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
    validate_cid_nl_assessment_hypothesis_closure(
        sample=sample,
        assessment=assessment,
        hypothesis=peak_hypothesis,
    )


def _validate_trajectory_sample(
    assessment: LocalTrajectoryAssessment,
    sample: ResolvedCanonicalSampleIdentity,
) -> None:
    valid_tokens = {sample.metadata.sample_name, sample.metadata.raw_stem}
    if len(assessment.sample_stems) != 1 or assessment.sample_stems[0] not in (
        valid_tokens
    ):
        raise ModeTrackContractError(
            "local trajectory assessment must cover exactly the assigned sample"
        )


def _trajectory_delta(assessment: LocalTrajectoryAssessment) -> float:
    if assessment.status != "assessable":
        return 0.0
    accepted = tuple(
        point.rt_drift_delta_min
        for point in assessment.landmark_points
        if point.quality == "accepted"
    )
    if not accepted:
        raise ModeTrackContractError(
            "assessable trajectory must retain accepted local landmarks"
        )
    return _canonical_float(statistics.median(accepted))


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


def _trajectories_cross(
    model_set: ModeTrackModelSet,
    predictions: tuple[ModeTrackPredictionAssessment, ...],
) -> bool:
    prediction_by_id = {item.group_hypothesis_id: item for item in predictions}
    ordered = tuple(
        sorted(
            model_set.tracks,
            key=lambda item: (
                _finite_float(item.track.group_center_rt, field="group_center_rt"),
                item.group_hypothesis_id,
            ),
        )
    )
    base_centers = tuple(
        _finite_float(item.track.group_center_rt, field="group_center_rt")
        for item in ordered
    )
    predicted_centers = tuple(
        prediction_by_id[item.group_hypothesis_id].predicted_rt_min for item in ordered
    )
    if any(value is None for value in predicted_centers):
        return False
    available_centers = tuple(value for value in predicted_centers if value is not None)
    return any(
        left >= right
        for left, right in zip(base_centers, base_centers[1:], strict=False)
    ) or any(
        left >= right
        for left, right in zip(
            available_centers,
            available_centers[1:],
            strict=False,
        )
    )


def _prediction_intervals_overlap(
    predictions: tuple[ModeTrackPredictionAssessment, ...],
) -> bool:
    intervals = tuple(
        sorted(
            (
                item.prediction_interval_min
                for item in predictions
                if item.prediction_interval_min is not None
            ),
            key=lambda item: item[0],
        )
    )
    return any(
        left[1] >= right[0]
        for left, right in zip(intervals, intervals[1:], strict=False)
    )


def _canonical_anchors(
    anchors: tuple[ModeTrackSupportAnchor, ...],
) -> tuple[ModeTrackSupportAnchor, ...]:
    if not isinstance(anchors, tuple) or any(
        not isinstance(item, ModeTrackSupportAnchor) for item in anchors
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
    if not isinstance(assignments, tuple) or any(
        not isinstance(item, ModeTrackAssignment) for item in assignments
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
        canonical_sample_identity_id=assignment.canonical_sample_identity_id,
        acquisition_source_id=assignment.acquisition_source_id,
        injection_order=assignment.injection_order,
        peak_hypothesis_id=assignment.peak_hypothesis_id,
        candidate_observation_class=assignment.candidate_observation_class,
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
    canonical_sample_identity_id: str,
    acquisition_source_id: str,
    injection_order: int,
    peak_hypothesis_id: str | None,
    candidate_observation_class: CandidateObservationClass | None,
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
        "canonical_sample_identity_id": canonical_sample_identity_id,
        "acquisition_source_id": acquisition_source_id,
        "injection_order": injection_order,
        "peak_hypothesis_id": peak_hypothesis_id,
        "candidate_observation_class": candidate_observation_class,
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
    return {"schema": "mode_track_support_anchor_v1", **values}


def _candidate_identity_payload(**values: object) -> dict[str, object]:
    return {"schema": "mode_track_candidate_observation_v1", **values}


def _model_set_identity_payload(
    *,
    family_context_id: str,
    policy: ModeTrackTrajectoryPolicy,
    tracks: tuple[ModeTrackModel, ...],
    lineage_refs: tuple[str, ...],
) -> dict[str, object]:
    return {
        "schema": MODEL_SET_SCHEMA_VERSION,
        "family_context_id": family_context_id,
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
    "ModeTrackModel",
    "ModeTrackModelSet",
    "ModeTrackPredictionAssessment",
    "ModeTrackSupportAnchor",
    "ModeTrackTrajectoryPolicy",
    "assign_mode_tracks",
    "canonical_mode_track_assignment_batch_bytes",
    "fit_mode_track_model_set",
    "mint_mode_track_candidate_observation",
    "reinforce_mode_track_model_set",
)
