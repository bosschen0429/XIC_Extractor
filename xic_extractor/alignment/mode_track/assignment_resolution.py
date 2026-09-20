"""ModeTrack candidate decision and occupancy resolution."""


from __future__ import annotations

from typing import Literal

from xic_extractor.alignment.drift_evidence import (
    LocalTrajectoryAssessment,
)
from xic_extractor.alignment.mode_track.assignment_contracts import (
    ModeTrackAssignment,
    ModeTrackAssignmentObservation,
    ModeTrackCandidateObservation,
    ModeTrackContractError,
    ModeTrackModelSet,
    ModeTrackPredictionAssessment,
    ModeTrackRepairAssignmentObservation,
    _mint_assignment,
    _peak_hypothesis_assignment_scope_exclusions,
)
from xic_extractor.alignment.mode_track.assignment_prediction import (
    _crossed_track_ids,
)
from xic_extractor.alignment.mode_track.assignment_scope import (
    ModeTrackAssignmentScope,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
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
            assignment_scope=model_set.assignment_scope,
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
        for track in model_set.assignment_scope.groups:
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
    assignment_scope: ModeTrackAssignmentScope,
    predictions: tuple[ModeTrackPredictionAssessment, ...],
    global_state: tuple[AssignmentState, tuple[str, ...]] | None,
    crossed_track_ids: frozenset[str],
) -> tuple[AssignmentState, str | None, tuple[str, ...]]:
    if type(candidate) is ModeTrackCandidateObservation:
        scope_exclusions = _peak_hypothesis_assignment_scope_exclusions(
            candidate.peak_hypothesis,
            assignment_scope=assignment_scope,
        )
        if scope_exclusions:
            return "out_of_model", None, scope_exclusions
        owner_group_ids = tuple(
            track.group_hypothesis_id
            for track in assignment_scope.groups
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
    assignment_scope: ModeTrackAssignmentScope,
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
