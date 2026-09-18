"""Shadow-only owner for injection-local ModeTrack assignment evidence."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Literal, final

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.drift_evidence import (
    LocalTrajectoryAssessment,
    LocalTrajectoryStatus,
)
from xic_extractor.alignment.mode_track.assignment_contracts import (
    _MODE_TRACK_MINT_TOKEN,
    ModeTrackAssignment,
    ModeTrackAssignmentBatch,
    ModeTrackAssignmentObservation,
    ModeTrackCandidateObservation,
    ModeTrackContractError,
    ModeTrackModel,
    ModeTrackModelSet,
    ModeTrackPredictionAssessment,
    ModeTrackRepairAssignmentObservation,
    ModeTrackSupportAnchor,
    ModeTrackTrajectoryPolicy,
    _anchor_identity_payload,  # noqa: F401
    _assignment_identity_payload,  # noqa: F401
    _batch_identity_payload,
    _candidate_identity_payload,
    _canonical_anchors,
    _canonical_json_bytes,
    _finite_float,
    _mint_assignment,
    _mint_assignment_batch,
    _mint_model_set,
    _mint_support_anchor,
    _ModeTrackAssignmentUnavailableError,  # noqa: F401
    _peak_hypothesis_family_exclusions,
    _peak_observation_content_id,
    _positive_int,
    _repair_assignment_observation_payload,
    _require_canonical_texts,
    _require_unique_support_entities,
    _required_text,
    _resolve_sample_acquisition,
    _RuntimeFinal,
    _typed_digest,
    _validate_assignment_batch_graph,
    _validate_assignment_observation_binding,
    _validate_model_set_binding,
    _validate_peak_hypothesis_binding,
    _validate_support_anchor,
    _validate_support_assessment,
)
from xic_extractor.alignment.mode_track.assignment_prediction import (
    _predict_track,
    _track_evidence_refs,
    _validate_trajectory_sample,
)
from xic_extractor.alignment.mode_track.assignment_resolution import (
    _assign_candidate,
    _global_no_write_state,  # noqa: F401
    _resolve_candidate_decisions,
)
from xic_extractor.alignment.mode_track.family_context import (
    FamilyContextRef,
    validate_family_context_ref_for_consumption,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.processing_identity import (
    IdentityContractError,
    ProcessingOwnerReferences,
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
                "ModeTrackFitOnceSampleSource must be minted by the ModeTrack "
                "model/assignment owner"
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
                "sample source trajectory must be an identity-trajectory "
                "owner assessment"
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
                "ModeTrackFitOnceSource must be minted by the ModeTrack "
                "model/assignment owner"
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
            batch_candidate_ids = tuple(
                sorted(
                    assignment.candidate_observation_id
                    for assignment in batch.assignments
                    if assignment.candidate_observation_id is not None
                )
            )
            if batch_candidate_ids != source.candidate_observation_ids:
                raise ModeTrackContractError(
                    "initial assignment batch crossed its candidate snapshot"
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
    """Mint one complete sample carrier from existing ModeTrack and
    identity-trajectory owner values.
    """

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise ModeTrackContractError("processing_owners must be owner-validated")
    if type(sample) is not ResolvedCanonicalSampleIdentity:
        raise ModeTrackContractError("sample must be owner-resolved")
    if type(candidates) is not tuple:
        raise ModeTrackContractError("candidates must be an immutable tuple")
    if type(trajectory_assessment) is not LocalTrajectoryAssessment:
        raise ModeTrackContractError(
            "trajectory_assessment must be an identity-trajectory owner "
            "assessment"
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
            "trajectory_assessment must be an identity-trajectory owner "
            "assessment"
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
    """Revalidate one ModeTrack batch against its complete current owner closure."""

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
