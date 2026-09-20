from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

import pytest

from tests.test_alignment_conservative_backfill import _assessment
from tests.test_alignment_mode_track_assignment import (
    _different_request_same_sources,
    _scenario,
)
from xic_extractor.alignment.drift_evidence import (
    LocalTrajectoryEvidenceLookup,
    assess_local_trajectory,
)
from xic_extractor.alignment.mode_track import assignment as mta
from xic_extractor.alignment.mode_track.assignment import (
    ModeTrackContractError,
    assign_mode_tracks,
    canonical_mode_track_assignment_batch_bytes,
    fit_mode_track_model_set,
    reinforce_mode_track_model_set,
    validate_mode_track_assignment_batch_for_consumption,
)
from xic_extractor.processing_identity import resolve_request_acquisition_source


def _batch_for_case(tmp_path: Path, case: str):
    scenario = _scenario(
        tmp_path,
        prediction_half_width_min=0.26 if case == "ambiguous" else 0.1,
    )
    if case == "not_observed":
        candidates = ()
    else:
        apex = {
            "uniquely_assigned": 7.87,
            "ambiguous": 8.12,
            "direct_support": 7.87,
            "out_of_model": 8.55,
            "technical_unassessable": 7.87,
        }[case]
        direct_support = case == "direct_support"
        candidates = (scenario.candidate(apex, direct_support=direct_support),)
    trajectory = scenario.assessable_trajectory
    if case == "technical_unassessable":
        trajectory = assess_local_trajectory(
            LocalTrajectoryEvidenceLookup(
                points=scenario.assessable_trajectory.landmark_points
            ),
            ("target",),
            conflict_evidence_refs=(
                scenario.assessable_trajectory.landmark_evidence_refs[0],
            ),
        )
    batch = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=candidates,
        trajectory_assessment=trajectory,
    )
    return scenario, batch


def _validate(owners, models, batch):
    return validate_mode_track_assignment_batch_for_consumption(
        processing_owners=owners,
        model_set=models,
        assignment_batch=batch,
    )


def _reseal(batch) -> None:
    assignment = batch.assignments[0]
    assignment_payload = mta._assignment_identity_payload(assignment)
    assignment_id = mta._typed_digest("mode-track-assignment:", assignment_payload)
    object.__setattr__(assignment, "assignment_id", assignment_id)
    batch_payload = mta._batch_identity_payload(batch)
    batch_id = mta._typed_digest("mode-track-assignment-batch:", batch_payload)
    object.__setattr__(batch, "assignment_batch_id", batch_id)


def _reseal_candidate(candidate) -> None:
    payload = mta._candidate_identity_payload(
        **_identity_values(candidate, "candidate_observation_id")
    )
    candidate_id = mta._typed_digest("mode-track-candidate:", payload)
    object.__setattr__(candidate, "candidate_observation_id", candidate_id)


def _identity_values(value, identity_field: str) -> dict[str, object]:
    live_refs = {
        "sample",
        "acquisition_source",
        "peak_hypothesis",
        "direct_support_assessment",
    }
    return {
        item.name: getattr(value, item.name)
        for item in fields(value)
        if item.name not in live_refs | {identity_field}
    }


@pytest.mark.parametrize(
    ("case", "expected_state"),
    (
        ("uniquely_assigned", "uniquely_assigned"),
        ("ambiguous", "ambiguous_multiple_tracks"),
        ("out_of_model", "out_of_model"),
        ("not_observed", "not_observed"),
        ("technical_unassessable", "technical_unassessable"),
        ("direct_support", "uniquely_assigned"),
    ),
)
def test_validator_accepts_all_states_and_exact_replay(
    tmp_path: Path, case: str, expected_state: str
) -> None:
    scenario, batch = _batch_for_case(tmp_path, case)
    assert tuple(item.state for item in batch.assignments) == (expected_state,)

    original_bytes = canonical_mode_track_assignment_batch_bytes(batch)
    for _ in range(2):
        assert _validate(scenario.fixture.owners, scenario.models, batch) is None
        assert canonical_mode_track_assignment_batch_bytes(batch) == original_bytes


@pytest.mark.parametrize(
    "case",
    (
        "same_sources_new_request",
        "sibling_attempt",
        "different_model",
        "different_policy",
        "stale_sample",
        "stale_acquisition",
        "stale_injection",
        "foreign_equal_content_support",
        "forged_assignment_id",
        "forged_batch_id",
        "duplicate_assignment_id",
        "duplicate_hypothesis_and_physical_wrapper",
        "substituted_anchor_live_refs",
        "partial_tuple",
        "malformed_tuple",
        "forged_prediction_anchor_refs",
        "forged_prediction_support_refs",
        "malformed_policy",
        "mutable_track_anchors",
    ),
)
def test_consumer_validator_rejects_crossed_stale_or_forged_closure(
    tmp_path: Path,
    case: str,
) -> None:
    scenario = _scenario(tmp_path)
    direct = scenario.candidate(7.87, direct_support=True)
    batch = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=() if case == "duplicate_assignment_id" else (direct,),
        trajectory_assessment=scenario.assessable_trajectory,
    )
    owners = scenario.fixture.owners
    models = scenario.models
    if case == "same_sources_new_request":
        owners = _different_request_same_sources(scenario.fixture)
    elif case == "sibling_attempt":
        owners = scenario.fixture.retry().owners
    elif case == "different_model":
        models = fit_mode_track_model_set(
            processing_owners=owners,
            assignment_scope=scenario.assignment_scope,
            direct_support_anchors=scenario.track_a_spine.anchors,
            policy=scenario.policy,
        )
    elif case == "different_policy":
        models = fit_mode_track_model_set(
            processing_owners=owners,
            assignment_scope=scenario.assignment_scope,
            direct_support_anchors=scenario.track_a_spine.anchors
            + scenario.track_b_spine.anchors,
            policy=replace(scenario.policy, prediction_half_width_min=0.11),
        )
    elif case == "stale_sample":
        object.__setattr__(batch, "sample", scenario.fixture.sample("anchor-1"))
    elif case == "stale_acquisition":
        stale = resolve_request_acquisition_source(
            processing_owners=owners, sample=scenario.fixture.sample("anchor-1")
        )
        object.__setattr__(batch, "acquisition_source", stale)
    elif case == "stale_injection":
        object.__setattr__(direct, "injection_order", 99)
        _reseal_candidate(direct)
        candidate_id = direct.candidate_observation_id
        assignment = batch.assignments[0]
        object.__setattr__(assignment, "candidate_observation_id", candidate_id)
        _reseal(batch)
    elif case == "foreign_equal_content_support":
        sibling = scenario.fixture.retry()
        foreign = _assessment(
            sibling,
            "target",
            support_apex=7.87,
            scan_number=9001,
            invocation="candidate:7.87000:True",
        )
        assert foreign.content == direct.direct_support_assessment.content
        assert foreign.artifact != direct.direct_support_assessment.artifact
        object.__setattr__(direct, "direct_support_assessment", foreign)
    elif case == "forged_assignment_id":
        object.__setattr__(batch.assignments[0], "assignment_id", "forged")
    elif case == "forged_batch_id":
        object.__setattr__(batch, "assignment_batch_id", "forged")
    elif case == "duplicate_assignment_id":
        object.__setattr__(batch, "assignments", batch.assignments * 2)
        digest = mta._typed_digest(
            "mode-track-assignment-batch:", mta._batch_identity_payload(batch)
        )
        object.__setattr__(batch, "assignment_batch_id", digest)
    elif case == "duplicate_hypothesis_and_physical_wrapper":
        object.__setattr__(batch, "assignments", batch.assignments * 2)
    elif case == "substituted_anchor_live_refs":
        victim, donor = models.tracks[0].anchors[0], models.tracks[1].anchors[0]
        object.__setattr__(victim, "peak_hypothesis", donor.peak_hypothesis)
        object.__setattr__(
            victim, "direct_support_assessment", donor.direct_support_assessment
        )
    elif case == "partial_tuple":
        object.__setattr__(batch, "assignments", ())
    elif case == "malformed_tuple":
        object.__setattr__(batch, "assignments", list(batch.assignments))
    elif case in {"forged_prediction_anchor_refs", "forged_prediction_support_refs"}:
        prediction = batch.assignments[0].prediction_assessments[0]
        field = (
            "anchor_refs"
            if case.endswith("anchor_refs")
            else "support_artifact_content_ids"
        )
        object.__setattr__(prediction, field, ("forged",))
        _reseal(batch)
    elif case == "malformed_policy":
        object.__setattr__(models, "policy", object())
    elif case == "mutable_track_anchors":
        object.__setattr__(models.tracks[0], "anchors", list(models.tracks[0].anchors))

    if case in {"stale_sample", "stale_acquisition", "stale_injection"}:
        with pytest.raises(ModeTrackContractError):
            canonical_mode_track_assignment_batch_bytes(batch)
    with pytest.raises(ModeTrackContractError):
        _validate(owners, models, batch)


def test_reinforcement_reuses_consumer_validation(tmp_path: Path) -> None:
    scenario, batch = _batch_for_case(tmp_path, "not_observed")
    object.__setattr__(batch, "sample", scenario.fixture.sample("anchor-1"))

    with pytest.raises(ModeTrackContractError):
        reinforce_mode_track_model_set(
            processing_owners=scenario.fixture.owners,
            model_set=scenario.models,
            assignment_batch=batch,
        )


@pytest.mark.parametrize("field", ("state", "group_hypothesis_id", "reason_codes"))
def test_consumer_validator_replays_exact_assignment_decision(
    tmp_path: Path, field: str
) -> None:
    scenario, batch = _batch_for_case(tmp_path, "uniquely_assigned")
    assignment = batch.assignments[0]
    different_group_id = next(
        track.group_hypothesis_id
        for track in scenario.models.tracks
        if track.group_hypothesis_id != assignment.group_hypothesis_id
    )
    replacement = {
        "state": "technical_unassessable",
        "group_hypothesis_id": different_group_id,
        "reason_codes": ("trajectory_crossing",),
    }[field]
    object.__setattr__(assignment, field, replacement)
    if field == "state":
        object.__setattr__(assignment, "group_hypothesis_id", None)
    _reseal(batch)

    with pytest.raises(ModeTrackContractError):
        _validate(scenario.fixture.owners, scenario.models, batch)


def test_validator_rejects_digest_consistent_candidate_apex(tmp_path: Path) -> None:
    scenario, batch = _batch_for_case(tmp_path, "uniquely_assigned")
    assignment = batch.assignments[0]
    candidate = assignment.candidate_observation
    assert candidate is not None
    object.__setattr__(candidate, "apex_rt_min", candidate.apex_rt_min + 5.0)
    _reseal_candidate(candidate)
    candidate_id = candidate.candidate_observation_id
    object.__setattr__(assignment, "candidate_observation_id", candidate_id)
    _reseal(batch)

    with pytest.raises(ModeTrackContractError):
        _validate(scenario.fixture.owners, scenario.models, batch)


def test_fit_rejects_digest_consistent_anchor_apex(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    anchor = scenario.models.tracks[0].anchors[0]
    object.__setattr__(anchor, "apex_rt_min", anchor.apex_rt_min + 5.0)
    payload = mta._anchor_identity_payload(**_identity_values(anchor, "anchor_id"))
    object.__setattr__(
        anchor, "anchor_id", mta._typed_digest("mode-track-anchor:", payload)
    )

    with pytest.raises(ModeTrackContractError):
        fit_mode_track_model_set(
            processing_owners=scenario.fixture.owners,
            assignment_scope=scenario.assignment_scope,
            direct_support_anchors=tuple(
                item for track in scenario.models.tracks for item in track.anchors
            ),
            policy=scenario.policy,
        )


@pytest.mark.parametrize(
    "field", ("prediction_assessments", "trajectory_evidence_refs", "lineage_refs")
)
def test_consumer_validator_rejects_assignment_specific_shared_facts(
    tmp_path: Path, field: str
) -> None:
    scenario = _scenario(tmp_path)
    batch = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(
            scenario.candidate(7.87, direct_support=False),
            scenario.candidate(8.37, direct_support=False),
        ),
        trajectory_assessment=scenario.assessable_trajectory,
    )
    assignment = batch.assignments[1]
    if field == "prediction_assessments":
        prediction = replace(
            assignment.prediction_assessments[0],
            reason_codes=("prediction_available", "substituted"),
        )
        value = (prediction, *assignment.prediction_assessments[1:])
    else:
        value = tuple(sorted((*getattr(assignment, field), "substituted")))
    object.__setattr__(assignment, field, value)
    _reseal(batch)

    with pytest.raises(ModeTrackContractError):
        _validate(scenario.fixture.owners, scenario.models, batch)


@pytest.mark.parametrize(
    "owner_type",
    (
        mta.ModeTrackTrajectoryPolicy,
        mta.ModeTrackSupportAnchor,
        mta.ModeTrackModel,
        mta.ModeTrackModelSet,
        mta.ModeTrackCandidateObservation,
        mta.ModeTrackPredictionAssessment,
        mta.ModeTrackAssignment,
        mta.ModeTrackAssignmentBatch,
    ),
)
def test_mode_track_owner_types_are_runtime_final(owner_type) -> None:
    with pytest.raises(TypeError):
        type(f"Forged{owner_type.__name__}", (owner_type,), {})
