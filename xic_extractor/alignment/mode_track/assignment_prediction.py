"""ModeTrack trajectory prediction and anchor geometry."""


from __future__ import annotations

import math
from typing import Literal

from xic_extractor.alignment.drift_evidence import (
    LocalTrajectoryAssessment,
)
from xic_extractor.alignment.mode_track.assignment_contracts import (
    ModeTrackContractError,
    ModeTrackModel,
    ModeTrackModelSet,
    ModeTrackPredictionAssessment,
    ModeTrackSupportAnchor,
    ModeTrackTrajectoryPolicy,
    _canonical_float,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    ProcessingOwnerReferences,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "mode_track_assignment"
BATCH_SCHEMA_VERSION = "mode_track_assignment_batch"
MODEL_SET_SCHEMA_VERSION = "mode_track_model_set"
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
