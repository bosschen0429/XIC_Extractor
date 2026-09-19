from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_alignment_conservative_backfill import (
    _assessment,
    _study_fixture,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.drift_evidence import (
    LocalFeatureLandmarkEvidence,
    LocalTrajectoryEvidenceLookup,
    assess_local_trajectory,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.mode_track.assignment import (
    ModeTrackAssignment,
    ModeTrackContractError,
    ModeTrackPredictionAssessment,
    ModeTrackTrajectoryPolicy,
    _global_no_write_state,
    _peak_hypothesis_family_exclusions,
    assign_mode_tracks,
    canonical_mode_track_assignment_batch_bytes,
    fit_mode_track_model_set,
    mint_mode_track_candidate_observation,
    mint_mode_track_support_anchor,
    reinforce_mode_track_model_set,
)
from xic_extractor.alignment.mode_track.family_context import (
    mint_family_context_ref,
)
from xic_extractor.peak_detection.cid_nl_chromatographic_support import (
    compose_cid_nl_chromatographic_support_ref,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    compose_cid_nl_peak_hypothesis,
)
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
    resolve_request_acquisition_source,
)


def test_unique_assignment_is_owner_minted_typed_and_deterministic(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    candidate = scenario.candidate(7.87, direct_support=True)

    forward = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(candidate,),
        trajectory_assessment=scenario.assessable_trajectory,
    )
    repeated = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(candidate,),
        trajectory_assessment=scenario.assessable_trajectory,
    )

    assert forward == repeated
    assert canonical_mode_track_assignment_batch_bytes(forward) == (
        canonical_mode_track_assignment_batch_bytes(repeated)
    )
    assignment = forward.assignments[0]
    assert assignment.state == "uniquely_assigned"
    assert assignment.group_hypothesis_id == scenario.track_a.group_hypothesis_id
    assert assignment.candidate_observation_class == "direct_support"
    assert assignment.peak_hypothesis is candidate.peak_hypothesis
    assert assignment.sample is scenario.fixture.sample("target")
    assert assignment.acquisition_source is candidate.acquisition_source
    assert assignment.validation_status == "shadow_ready"
    assert assignment.product_authority == "none"
    assert assignment.prediction_assessments[0].anchor_refs
    assert assignment.trajectory_evidence_refs == (
        "peak-observation:target-landmark-1",
        "peak-observation:target-landmark-2",
    )

    payload = json.loads(canonical_mode_track_assignment_batch_bytes(forward))
    assert payload["schema_version"] == "mode_track_assignment_batch"
    assert payload["assignments"][0]["state"] == "uniquely_assigned"
    assert (
        payload["assignments"][0]["candidate_observation_id"]
        == candidate.candidate_observation_id
    )
    assert (
        payload["assignments"][0]["direct_support_assessment_artifact_content_id"]
        == candidate.direct_support_assessment_artifact_content_id
    )


def test_assignment_serialization_canonicalizes_equivalent_candidate_order(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    candidates = (
        scenario.candidate(7.87, direct_support=True),
        scenario.candidate(8.37, direct_support=True),
    )

    forward = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=candidates,
        trajectory_assessment=scenario.assessable_trajectory,
    )
    reversed_input = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=tuple(reversed(candidates)),
        trajectory_assessment=scenario.assessable_trajectory,
    )

    assert reversed_input == forward
    assert canonical_mode_track_assignment_batch_bytes(reversed_input) == (
        canonical_mode_track_assignment_batch_bytes(forward)
    )


@pytest.mark.parametrize(
    ("case", "expected_state"),
    (
        ("overlapping_intervals", "ambiguous_multiple_tracks"),
        ("outside_all_intervals", "out_of_model"),
        ("missing_landmarks", "uniquely_assigned"),
        ("no_candidate", "not_observed"),
    ),
)
def test_assignment_truth_table_is_explicit(
    tmp_path: Path,
    case: str,
    expected_state: str,
) -> None:
    half_width = 0.26 if case == "overlapping_intervals" else 0.1
    scenario = _scenario(tmp_path, prediction_half_width_min=half_width)
    if case == "no_candidate":
        candidates = ()
    else:
        apex = 8.12 if case == "overlapping_intervals" else 8.55
        if case == "missing_landmarks":
            apex = 7.87
        candidates = (scenario.candidate(apex, direct_support=False),)
    trajectory = (
        scenario.missing_trajectory
        if case == "missing_landmarks"
        else scenario.assessable_trajectory
    )

    result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=candidates,
        trajectory_assessment=trajectory,
    )

    assert tuple(item.state for item in result.assignments) == (expected_state,)
    if case == "no_candidate":
        assert result.assignments[0].peak_hypothesis is None
        assert result.assignments[0].candidate_observation_class is None


def test_prediction_interval_overlap_is_candidate_local(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, prediction_half_width_min=0.26)
    single_track_candidate = scenario.candidate(7.87, direct_support=False)
    overlapping_candidate = scenario.candidate(8.12, direct_support=False)

    result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(single_track_candidate, overlapping_candidate),
        trajectory_assessment=scenario.assessable_trajectory,
    )

    by_hypothesis = {item.peak_hypothesis_id: item for item in result.assignments}
    single_track_assignment = by_hypothesis[single_track_candidate.peak_hypothesis_id]
    overlap_assignment = by_hypothesis[overlapping_candidate.peak_hypothesis_id]
    assert single_track_assignment.state == "uniquely_assigned"
    assert (
        single_track_assignment.group_hypothesis_id
        == scenario.track_a.group_hypothesis_id
    )
    assert overlap_assignment.state == "ambiguous_multiple_tracks"
    assert overlap_assignment.reason_codes == ("candidate_matches_multiple_tracks",)


def test_sparse_anchors_and_unsupported_extrapolation_are_no_write(
    tmp_path: Path,
) -> None:
    sparse = _scenario(tmp_path / "sparse")
    sparse_models = fit_mode_track_model_set(
        processing_owners=sparse.fixture.owners,
        family_context=sparse.family_context,
        direct_support_anchors=sparse.track_a_spine.anchors
        + sparse.track_b_spine.anchors[:1],
        policy=sparse.policy,
    )
    sparse_result = assign_mode_tracks(
        processing_owners=sparse.fixture.owners,
        sample=sparse.fixture.sample("target"),
        model_set=sparse_models,
        candidates=(sparse.candidate(7.87, direct_support=False),),
        trajectory_assessment=sparse.assessable_trajectory,
    )
    assert sparse_result.assignments[0].state == "technical_unassessable"
    assert "sparse_track_anchors" in sparse_result.assignments[0].reason_codes

    extrapolated = _scenario(
        tmp_path / "extrapolated",
        sample_stems=("anchor-1", "anchor-2", "unused", "target"),
        target_injection_order=4,
        max_extrapolation_gap=1,
    )
    extrapolated_result = assign_mode_tracks(
        processing_owners=extrapolated.fixture.owners,
        sample=extrapolated.fixture.sample("target"),
        model_set=extrapolated.models,
        candidates=(extrapolated.candidate(8.10, direct_support=False),),
        trajectory_assessment=extrapolated.assessable_trajectory,
    )
    assert extrapolated_result.assignments[0].state == "out_of_model"
    assert "unsupported_extrapolation" in (
        extrapolated_result.assignments[0].reason_codes
    )


def test_zero_anchor_tracks_use_provisional_group_geometry(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    empty_models = fit_mode_track_model_set(
        processing_owners=scenario.fixture.owners,
        family_context=scenario.family_context,
        direct_support_anchors=(),
        policy=scenario.policy,
    )

    result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=empty_models,
        candidates=(scenario.candidate(7.87, direct_support=False),),
        trajectory_assessment=scenario.assessable_trajectory,
    )

    assert result.assignments[0].state == "uniquely_assigned"
    assert all(
        assessment.status == "predicted"
        and assessment.anchor_refs == ()
        and assessment.support_artifact_content_ids == ()
        and assessment.reason_codes == ("provisional_group_geometry",)
        for assessment in result.assignments[0].prediction_assessments
    )


def test_crossing_and_per_track_occupancy_are_explicit_no_write(
    tmp_path: Path,
) -> None:
    crossing = _scenario(
        tmp_path / "crossing",
        sample_stems=("anchor-1", "anchor-2", "unused", "target"),
        target_injection_order=4,
        track_a_anchor_rts=(7.90, 7.98),
        track_b_anchor_rts=(8.30, 8.22),
        max_extrapolation_gap=2,
    )
    crossing_result = assign_mode_tracks(
        processing_owners=crossing.fixture.owners,
        sample=crossing.fixture.sample("target"),
        model_set=crossing.models,
        candidates=(crossing.candidate(8.25, direct_support=False),),
        trajectory_assessment=crossing.assessable_trajectory,
    )
    assert crossing_result.assignments[0].state == "technical_unassessable"
    assert "trajectory_crossing" in crossing_result.assignments[0].reason_codes

    occupied = _scenario(tmp_path / "occupied")
    first = occupied.candidate(7.86, direct_support=False)
    second = occupied.candidate(7.88, direct_support=False)
    occupied_result = assign_mode_tracks(
        processing_owners=occupied.fixture.owners,
        sample=occupied.fixture.sample("target"),
        model_set=occupied.models,
        candidates=(first, second),
        trajectory_assessment=occupied.assessable_trajectory,
    )
    assert {item.state for item in occupied_result.assignments} == {
        "ambiguous_multiple_tracks"
    }
    assert all(
        "track_occupancy_conflict" in item.reason_codes
        for item in occupied_result.assignments
    )


def test_incumbent_anchor_occupies_track_for_other_hypotheses(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    anchor = scenario.track_a_spine.anchors[0]
    replay = mint_mode_track_candidate_observation(
        processing_owners=scenario.fixture.owners,
        sample=anchor.sample,
        peak_hypothesis=anchor.peak_hypothesis,
        direct_support_assessment=anchor.direct_support_assessment,
    )
    challenger_assessment, challenger_hypothesis = _candidate_assessment_hypothesis(
        scenario.fixture,
        sample_stem="anchor-1",
        apex_rt=7.82,
        invocation="candidate:incumbent-occupancy-challenger",
    )
    challenger = mint_mode_track_candidate_observation(
        processing_owners=scenario.fixture.owners,
        sample=anchor.sample,
        peak_hypothesis=challenger_hypothesis,
        direct_support_assessment=challenger_assessment,
    )
    trajectory = _trajectory(
        "anchor-1",
        anchor.injection_order,
        anchor.acquisition_source_id,
        scenario.fixture.owners,
    )

    replay_result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=anchor.sample,
        model_set=scenario.models,
        candidates=(replay,),
        trajectory_assessment=trajectory,
    )
    challenger_result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=anchor.sample,
        model_set=scenario.models,
        candidates=(challenger,),
        trajectory_assessment=trajectory,
    )
    combined_result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=anchor.sample,
        model_set=scenario.models,
        candidates=(replay, challenger),
        trajectory_assessment=trajectory,
    )

    replay_assignment = replay_result.assignments[0]
    challenger_assignment = challenger_result.assignments[0]
    assert replay_assignment.state == "uniquely_assigned"
    assert replay_assignment.group_hypothesis_id == anchor.group_hypothesis_id
    assert challenger_assignment.state == "ambiguous_multiple_tracks"
    assert challenger_assignment.group_hypothesis_id is None
    assert challenger_assignment.reason_codes == ("track_occupancy_conflict",)
    combined_by_hypothesis = {
        item.peak_hypothesis_id: item for item in combined_result.assignments
    }
    assert combined_by_hypothesis[replay.peak_hypothesis_id].state == (
        "uniquely_assigned"
    )
    assert combined_by_hypothesis[challenger.peak_hypothesis_id].state == (
        "ambiguous_multiple_tracks"
    )
    assert (
        reinforce_mode_track_model_set(
            processing_owners=scenario.fixture.owners,
            model_set=scenario.models,
            assignment_batch=combined_result,
        )
        is scenario.models
    )


@pytest.mark.parametrize(
    ("occupancy", "candidate_rts", "expected_tracks"),
    (
        ("cell_reference_only", (7.87,), ("track_a",)),
        ("backfill_decision_only", (8.37,), ("track_b",)),
        ("both", (7.87, 8.37), ("track_a", "track_b")),
        ("neither", (), ()),
    ),
)
def test_optional_occupancy_preserves_two_resolved_direct_support_peaks(
    tmp_path: Path,
    occupancy: str,
    candidate_rts: tuple[float, ...],
    expected_tracks: tuple[str, ...],
) -> None:
    scenario = _scenario(tmp_path / occupancy)
    candidates = tuple(
        scenario.candidate(apex_rt, direct_support=True) for apex_rt in candidate_rts
    )

    result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=candidates,
        trajectory_assessment=scenario.assessable_trajectory,
    )

    expected_ids = {
        getattr(scenario, name).group_hypothesis_id for name in expected_tracks
    }
    if expected_ids:
        assert {item.state for item in result.assignments} == {"uniquely_assigned"}
        assert {item.group_hypothesis_id for item in result.assignments} == expected_ids
        assert all(
            item.candidate_observation_class == "direct_support"
            for item in result.assignments
        )
        assert len(
            {
                item.peak_hypothesis_id
                for item in result.assignments
                if item.peak_hypothesis_id is not None
            }
        ) == len(candidates)
    else:
        assert tuple(item.state for item in result.assignments) == ("not_observed",)
        assert result.assignments[0].group_hypothesis_id is None


def test_tracks_follow_independent_local_drift_without_fixed_gap(
    tmp_path: Path,
) -> None:
    scenario = _scenario(
        tmp_path,
        track_a_anchor_rts=(7.78, 7.90),
        track_b_anchor_rts=(8.35, 8.29),
    )
    result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(
            scenario.candidate(7.89, direct_support=False),
            scenario.candidate(8.37, direct_support=False),
        ),
        trajectory_assessment=scenario.assessable_trajectory,
    )

    assert {item.state for item in result.assignments} == {"uniquely_assigned"}
    predictions = {
        assessment.group_hypothesis_id: assessment.predicted_rt_min
        for item in result.assignments
        for assessment in item.prediction_assessments
    }
    assert predictions[scenario.track_a.group_hypothesis_id] == pytest.approx(7.89)
    assert predictions[scenario.track_b.group_hypothesis_id] == pytest.approx(8.37)
    assert (
        scenario.track_a_spine.anchors[1].apex_rt_min
        - scenario.track_a_spine.anchors[0].apex_rt_min
    ) != (
        scenario.track_b_spine.anchors[1].apex_rt_min
        - scenario.track_b_spine.anchors[0].apex_rt_min
    )


def test_exact_anchor_prediction_does_not_reapply_same_injection_drift(
    tmp_path: Path,
) -> None:
    scenario = _scenario(
        tmp_path,
        sample_stems=("anchor-1", "target", "anchor-2"),
        prediction_half_width_min=0.1,
    )
    anchor = scenario.track_a_spine.anchors[0]
    candidate = mint_mode_track_candidate_observation(
        processing_owners=scenario.fixture.owners,
        sample=anchor.sample,
        peak_hypothesis=anchor.peak_hypothesis,
        direct_support_assessment=anchor.direct_support_assessment,
    )
    trajectory = _multi_point_trajectory(
        "anchor-1",
        anchor.injection_order,
        anchor.acquisition_source_id,
        ((7.0, 0.20), (9.0, 0.20)),
        scenario.fixture.owners,
    )

    result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=anchor.sample,
        model_set=scenario.models,
        candidates=(candidate,),
        trajectory_assessment=trajectory,
    )

    assignment = result.assignments[0]
    owning_prediction = next(
        item
        for item in assignment.prediction_assessments
        if item.group_hypothesis_id == anchor.group_hypothesis_id
    )
    assert candidate.peak_hypothesis_id == anchor.peak_hypothesis_id
    assert owning_prediction.predicted_rt_min == pytest.approx(anchor.apex_rt_min)
    assert assignment.state == "uniquely_assigned"
    assert assignment.group_hypothesis_id == anchor.group_hypothesis_id


def test_exact_anchor_prediction_does_not_require_local_drift_lookup(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    anchor = scenario.track_a_spine.anchors[0]
    candidate = mint_mode_track_candidate_observation(
        processing_owners=scenario.fixture.owners,
        sample=anchor.sample,
        peak_hypothesis=anchor.peak_hypothesis,
        direct_support_assessment=anchor.direct_support_assessment,
    )
    unrelated_trajectory = _multi_point_trajectory(
        "anchor-1",
        anchor.injection_order,
        anchor.acquisition_source_id,
        ((1.0, 0.20), (2.0, 0.20)),
        scenario.fixture.owners,
    )
    assert unrelated_trajectory.status == "assessable"

    result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=anchor.sample,
        model_set=scenario.models,
        candidates=(candidate,),
        trajectory_assessment=unrelated_trajectory,
    )

    assignment = result.assignments[0]
    assert assignment.state == "uniquely_assigned"
    assert assignment.group_hypothesis_id == anchor.group_hypothesis_id

    missing_result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=anchor.sample,
        model_set=scenario.models,
        candidates=(candidate,),
        trajectory_assessment=assess_local_trajectory(
            LocalTrajectoryEvidenceLookup(points=()),
            ("anchor-1",),
        ),
    )
    assert missing_result.assignments[0].state == "uniquely_assigned"
    assert (
        missing_result.assignments[0].group_hypothesis_id == anchor.group_hypothesis_id
    )


def test_each_track_uses_its_local_landmark_region_without_global_median(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    trajectory = _multi_point_trajectory(
        "target",
        2,
        scenario.target_acquisition_source_id,
        ((7.82, -0.20), (8.32, 0.20)),
        scenario.fixture.owners,
    )

    result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(
            scenario.candidate(7.62, direct_support=False),
            scenario.candidate(8.52, direct_support=False),
        ),
        trajectory_assessment=trajectory,
    )

    assert {item.state for item in result.assignments} == {"uniquely_assigned"}
    predictions = {
        assessment.group_hypothesis_id: assessment.predicted_rt_min
        for item in result.assignments
        for assessment in item.prediction_assessments
    }
    assert predictions[scenario.track_a.group_hypothesis_id] == pytest.approx(7.62)
    assert predictions[scenario.track_b.group_hypothesis_id] == pytest.approx(8.52)


def test_nonlocal_landmarks_keep_assignment_unassessable(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    trajectory = _multi_point_trajectory(
        "target",
        2,
        scenario.target_acquisition_source_id,
        ((7.90, -0.10), (8.00, 0.10)),
        scenario.fixture.owners,
    )

    result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(scenario.candidate(7.87, direct_support=False),),
        trajectory_assessment=trajectory,
    )

    assignment = result.assignments[0]
    assert assignment.state == "technical_unassessable"
    assert "unsupported_local_landmark_extrapolation" in assignment.reason_codes


def test_single_nonlocal_landmark_keeps_direct_support_unassessable(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    trajectory = _multi_point_trajectory(
        "target",
        2,
        scenario.target_acquisition_source_id,
        ((7.82, 0.05),),
        scenario.fixture.owners,
    )

    result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(scenario.candidate(7.87, direct_support=True),),
        trajectory_assessment=trajectory,
    )

    assert result.assignments[0].state == "technical_unassessable"
    assert "unsupported_local_landmark_extrapolation" in (
        result.assignments[0].reason_codes
    )


def test_candidate_physical_observation_must_stay_inside_family_universe(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    candidate = scenario.candidate(8.50, direct_support=False)
    outside_mz_hypothesis = SimpleNamespace(
        peak_observation_refs=(SimpleNamespace(content=SimpleNamespace(mz=700.0)),),
        integration=candidate.peak_hypothesis.integration,
    )
    assert _peak_hypothesis_family_exclusions(
        outside_mz_hypothesis,
        family_context=scenario.family_context,
    ) == ("peak_hypothesis_outside_family_mz",)

    tight_rt_context = mint_family_context_ref(
        mz_interval=scenario.family_context.mz_interval,
        rt_interval=(7.0, 8.40),
        context_policy_version="mode-track-family-context",
        source_lineage_refs=("synthetic:tight-family-rt-universe",),
        mode_tracks=(scenario.track_a, scenario.track_b),
    )
    tight_models = fit_mode_track_model_set(
        processing_owners=scenario.fixture.owners,
        family_context=tight_rt_context,
        direct_support_anchors=(
            scenario.track_a_spine.anchors + scenario.track_b_spine.anchors
        ),
        policy=scenario.policy,
    )
    result = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=tight_models,
        candidates=(candidate,),
        trajectory_assessment=scenario.assessable_trajectory,
    )

    assert result.assignments[0].state == "out_of_model"
    assert result.assignments[0].reason_codes == ("peak_hypothesis_outside_family_rt",)


def test_request_acquisition_and_injection_bindings_cannot_be_crossed(
    tmp_path: Path,
) -> None:
    original = _scenario(tmp_path / "original")
    foreign = _scenario(
        tmp_path / "foreign",
        sample_stems=("target", "anchor-1", "anchor-2"),
        target_injection_order=1,
    )
    assert (
        original.fixture.owners.request.processing_request_id
        != foreign.fixture.owners.request.processing_request_id
    )

    with pytest.raises(ModeTrackContractError, match="different processing request"):
        fit_mode_track_model_set(
            processing_owners=foreign.fixture.owners,
            family_context=original.family_context,
            direct_support_anchors=(
                original.track_a_spine.anchors + original.track_b_spine.anchors
            ),
            policy=original.policy,
        )

    with pytest.raises(ModeTrackContractError, match="different processing request"):
        assign_mode_tracks(
            processing_owners=foreign.fixture.owners,
            sample=foreign.fixture.sample("target"),
            model_set=original.models,
            candidates=(),
            trajectory_assessment=foreign.assessable_trajectory,
        )

    with pytest.raises(ModeTrackContractError, match="stale injection binding"):
        assign_mode_tracks(
            processing_owners=original.fixture.owners,
            sample=original.fixture.sample("target"),
            model_set=original.models,
            candidates=(),
            trajectory_assessment=_trajectory(
                "target",
                99,
                original.target_acquisition_source_id,
                original.fixture.owners,
            ),
        )

    stale_source = resolve_request_acquisition_source(
        processing_owners=original.fixture.owners,
        sample=original.fixture.sample("anchor-1"),
    )
    assert stale_source.acquisition_source_id != original.target_acquisition_source_id
    with pytest.raises(ModeTrackContractError, match="stale acquisition source"):
        assign_mode_tracks(
            processing_owners=original.fixture.owners,
            sample=original.fixture.sample("target"),
            model_set=original.models,
            candidates=(),
            trajectory_assessment=_trajectory(
                "target",
                2,
                stale_source.acquisition_source_id,
                original.fixture.owners,
            ),
        )


def test_fit_rejects_foreign_request_even_when_sources_and_order_match(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    foreign_owners = _different_request_same_sources(scenario.fixture)
    assert (
        foreign_owners.request.processing_request_id
        != scenario.fixture.owners.request.processing_request_id
    )
    assert (
        foreign_owners.request.acquisition_bindings
        == scenario.fixture.owners.request.acquisition_bindings
    )

    with pytest.raises(ModeTrackContractError, match="different processing request"):
        fit_mode_track_model_set(
            processing_owners=foreign_owners,
            family_context=scenario.family_context,
            direct_support_anchors=(
                scenario.track_a_spine.anchors + scenario.track_b_spine.anchors
            ),
            policy=scenario.policy,
        )


def test_assignment_rejects_candidate_from_foreign_request_before_prediction(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    candidate = scenario.candidate(7.87, direct_support=True)
    foreign_owners = _different_request_same_sources(scenario.fixture)
    foreign_fixture = replace(
        scenario.fixture,
        request=foreign_owners.request,
        attempt=foreign_owners.attempt,
        owners=foreign_owners,
        artifact_registry={},
        projections_by_hypothesis_id={},
    )
    foreign_track_a, foreign_track_b = _mode_track_spines(
        foreign_fixture,
        track_a_anchor_rts=(7.80, 7.84),
        track_b_anchor_rts=(8.30, 8.34),
    )
    foreign_context = mint_family_context_ref(
        mz_interval=(499.0, 501.0),
        rt_interval=(7.0, 10.0),
        context_policy_version="mode-track-family-context",
        source_lineage_refs=("synthetic:foreign-request-candidate-ingress",),
        mode_tracks=(foreign_track_a.group, foreign_track_b.group),
    )
    foreign_models = fit_mode_track_model_set(
        processing_owners=foreign_owners,
        family_context=foreign_context,
        direct_support_anchors=(foreign_track_a.anchors + foreign_track_b.anchors),
        policy=scenario.policy,
    )

    with pytest.raises(ModeTrackContractError, match="different processing request"):
        assign_mode_tracks(
            processing_owners=foreign_owners,
            sample=foreign_fixture.sample("target"),
            model_set=foreign_models,
            candidates=(candidate,),
            trajectory_assessment=_trajectory(
                "target",
                2,
                candidate.acquisition_source_id,
                foreign_owners,
            ),
        )


def test_fit_rejects_two_tracks_seeded_by_one_physical_observation(
    tmp_path: Path,
) -> None:
    fixture = _study_fixture(tmp_path, ("anchor-1",))
    sample = fixture.sample("anchor-1")
    first_assessment = _assessment(
        fixture,
        "anchor-1",
        support_apex=8.0,
        scan_number=8101,
        invocation="physical-cardinality:first",
    )
    second_assessment = _assessment(
        fixture,
        "anchor-1",
        support_apex=8.0,
        scan_number=8102,
        invocation="physical-cardinality:second",
    )
    first_hypothesis = _hypothesis_from_assessment(
        fixture,
        sample=sample,
        assessment=first_assessment,
    )
    second_hypothesis = _hypothesis_from_assessment(
        fixture,
        sample=sample,
        assessment=second_assessment,
    )
    assert first_hypothesis.hypothesis_id != second_hypothesis.hypothesis_id
    assert (
        first_hypothesis.peak_observation_refs[0].content.peak_observation_content_id
        == second_hypothesis.peak_observation_refs[
            0
        ].content.peak_observation_content_id
    )
    tracks = (
        CrossSamplePeakGroupHypothesis(
            group_hypothesis_id="track:a",
            public_family_id="display-family",
            owner_ids=(first_hypothesis.hypothesis_id,),
            event_ids=("event:first",),
            event_member_count=1,
            group_center_mz=500.0,
            group_center_rt=8.0,
        ),
        CrossSamplePeakGroupHypothesis(
            group_hypothesis_id="track:b",
            public_family_id="display-family",
            owner_ids=(second_hypothesis.hypothesis_id,),
            event_ids=("event:second",),
            event_member_count=1,
            group_center_mz=500.0,
            group_center_rt=8.0,
        ),
    )
    context = mint_family_context_ref(
        mz_interval=(499.0, 501.0),
        rt_interval=(7.0, 9.0),
        context_policy_version="mode-track-family-context",
        source_lineage_refs=("synthetic:physical-cardinality",),
        mode_tracks=tracks,
    )
    anchors = (
        mint_mode_track_support_anchor(
            processing_owners=fixture.owners,
            track=tracks[0],
            sample=sample,
            direct_support_assessment=first_assessment,
            peak_hypothesis=first_hypothesis,
        ),
        mint_mode_track_support_anchor(
            processing_owners=fixture.owners,
            track=tracks[1],
            sample=sample,
            direct_support_assessment=second_assessment,
            peak_hypothesis=second_hypothesis,
        ),
    )

    with pytest.raises(ModeTrackContractError, match="physical peak observation"):
        fit_mode_track_model_set(
            processing_owners=fixture.owners,
            family_context=context,
            direct_support_anchors=anchors,
            policy=ModeTrackTrajectoryPolicy(
                trajectory_policy_version="piecewise-linear-injection-local",
                band_policy_version="explicit-half-width",
                prediction_half_width_min=0.1,
                max_interpolation_gap=4,
                max_extrapolation_gap=2,
            ),
        )


def test_assignment_rejects_two_candidates_for_one_physical_observation(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    sample = scenario.fixture.sample("target")
    assessments = tuple(
        _assessment(
            scenario.fixture,
            "target",
            support_apex=7.87,
            scan_number=8200 + index,
            invocation=f"candidate-physical-cardinality:{index}",
        )
        for index in (1, 2)
    )
    hypotheses = tuple(
        _hypothesis_from_assessment(
            scenario.fixture,
            sample=sample,
            assessment=assessment,
        )
        for assessment in assessments
    )
    candidates = tuple(
        mint_mode_track_candidate_observation(
            processing_owners=scenario.fixture.owners,
            sample=sample,
            peak_hypothesis=hypothesis,
            direct_support_assessment=assessment,
        )
        for hypothesis, assessment in zip(hypotheses, assessments, strict=True)
    )
    assert candidates[0].peak_hypothesis_id != candidates[1].peak_hypothesis_id
    assert (
        candidates[0].peak_observation_content_id
        == candidates[1].peak_observation_content_id
    )

    with pytest.raises(ModeTrackContractError, match="physical peak observations"):
        assign_mode_tracks(
            processing_owners=scenario.fixture.owners,
            sample=sample,
            model_set=scenario.models,
            candidates=candidates,
            trajectory_assessment=scenario.assessable_trajectory,
        )

    support_candidate = scenario.candidate(7.87, direct_support=True)
    non_training_wrapper = mint_mode_track_candidate_observation(
        processing_owners=scenario.fixture.owners,
        sample=sample,
        peak_hypothesis=support_candidate.peak_hypothesis,
    )
    assert (
        support_candidate.candidate_observation_id
        != non_training_wrapper.candidate_observation_id
    )
    with pytest.raises(ModeTrackContractError, match="PeakHypotheses"):
        assign_mode_tracks(
            processing_owners=scenario.fixture.owners,
            sample=sample,
            model_set=scenario.models,
            candidates=(support_candidate, non_training_wrapper),
            trajectory_assessment=scenario.assessable_trajectory,
        )


def test_assignment_rejects_model_from_same_request_sibling_attempt(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    retry = scenario.fixture.retry()

    with pytest.raises(ModeTrackContractError, match="different processing attempt"):
        assign_mode_tracks(
            processing_owners=retry.owners,
            sample=retry.sample("target"),
            model_set=scenario.models,
            candidates=(),
            trajectory_assessment=scenario.missing_trajectory,
        )


@pytest.mark.parametrize(
    ("field_name", "message"),
    (
        ("processing_request_id", "different processing request"),
        ("processing_attempt_id", "different processing attempt"),
    ),
)
def test_assignment_rejects_crossed_trajectory_owner(
    tmp_path: Path,
    field_name: str,
    message: str,
) -> None:
    scenario = _scenario(tmp_path)
    crossed_points = tuple(
        replace(point, **{field_name: f"{field_name}:foreign"})
        for point in scenario.assessable_trajectory.landmark_points
    )
    crossed = assess_local_trajectory(
        LocalTrajectoryEvidenceLookup(points=crossed_points),
        ("target",),
    )

    with pytest.raises(ModeTrackContractError, match=message):
        assign_mode_tracks(
            processing_owners=scenario.fixture.owners,
            sample=scenario.fixture.sample("target"),
            model_set=scenario.models,
            candidates=(),
            trajectory_assessment=crossed,
        )


def test_candidate_rejects_support_assessment_from_sibling_attempt(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    candidate = scenario.candidate(7.87, direct_support=True)
    sibling = scenario.fixture.retry()
    sibling_assessment = _assessment(
        sibling,
        "target",
        support_apex=7.87,
        scan_number=712,
        invocation="candidate:sibling-assessment",
    )

    with pytest.raises(ModeTrackContractError):
        mint_mode_track_candidate_observation(
            processing_owners=scenario.fixture.owners,
            sample=scenario.fixture.sample("target"),
            peak_hypothesis=candidate.peak_hypothesis,
            direct_support_assessment=sibling_assessment,
        )


def test_crossing_is_local_to_the_crossed_tracks(tmp_path: Path) -> None:
    fixture = _study_fixture(
        tmp_path,
        ("anchor-1", "anchor-2", "unused", "target"),
    )
    spines = _mode_track_spines_many(
        fixture,
        track_anchor_rts=((7.90, 7.98), (8.30, 8.22), (8.40, 8.42)),
    )
    family_context = mint_family_context_ref(
        mz_interval=(499.0, 501.0),
        rt_interval=(7.0, 10.0),
        context_policy_version="mode-track-family-context",
        source_lineage_refs=("synthetic:crossing-locality",),
        mode_tracks=tuple(spine.group for spine in spines),
    )
    policy = ModeTrackTrajectoryPolicy(
        trajectory_policy_version="piecewise-linear-injection-local",
        band_policy_version="explicit-half-width",
        prediction_half_width_min=0.1,
        max_interpolation_gap=4,
        max_extrapolation_gap=2,
    )
    model_set = fit_mode_track_model_set(
        processing_owners=fixture.owners,
        family_context=family_context,
        direct_support_anchors=tuple(
            anchor for spine in spines for anchor in spine.anchors
        ),
        policy=policy,
    )
    assessment, hypothesis = _candidate_assessment_hypothesis(
        fixture,
        sample_stem="target",
        apex_rt=8.46,
        invocation="candidate:crossing-locality-track-c",
    )
    candidate = mint_mode_track_candidate_observation(
        processing_owners=fixture.owners,
        sample=fixture.sample("target"),
        peak_hypothesis=hypothesis,
    )
    target_source = resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=fixture.sample("target"),
    )

    result = assign_mode_tracks(
        processing_owners=fixture.owners,
        sample=fixture.sample("target"),
        model_set=model_set,
        candidates=(candidate,),
        trajectory_assessment=_trajectory(
            "target",
            4,
            target_source.acquisition_source_id,
            fixture.owners,
        ),
    )

    assert assessment.content.method_result == "SUPPORT"
    assert result.assignments[0].state == "uniquely_assigned"
    assert (
        result.assignments[0].group_hypothesis_id == spines[2].group.group_hypothesis_id
    )


def test_candidate_and_anchor_hypotheses_must_match_processing_owners(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path / "original")
    target_sample = scenario.fixture.sample("target")
    anchor = scenario.track_a_spine.anchors[0]

    with pytest.raises(ModeTrackContractError, match="different sample"):
        mint_mode_track_candidate_observation(
            processing_owners=scenario.fixture.owners,
            sample=target_sample,
            peak_hypothesis=anchor.peak_hypothesis,
        )

    candidate = scenario.candidate(7.87, direct_support=False)
    foreign = _scenario(
        tmp_path / "foreign",
        sample_stems=("target", "anchor-1", "anchor-2"),
        target_injection_order=1,
    )
    with pytest.raises(ModeTrackContractError, match="different processing request"):
        mint_mode_track_candidate_observation(
            processing_owners=foreign.fixture.owners,
            sample=foreign.fixture.sample("target"),
            peak_hypothesis=candidate.peak_hypothesis,
        )

    retry = scenario.fixture.retry()
    with pytest.raises(ModeTrackContractError, match="different processing attempt"):
        mint_mode_track_candidate_observation(
            processing_owners=retry.owners,
            sample=target_sample,
            peak_hypothesis=candidate.peak_hypothesis,
        )

    with pytest.raises(ModeTrackContractError, match="different processing attempt"):
        mint_mode_track_support_anchor(
            processing_owners=retry.owners,
            track=scenario.track_a,
            sample=anchor.sample,
            direct_support_assessment=anchor.direct_support_assessment,
            peak_hypothesis=anchor.peak_hypothesis,
        )


def test_public_ingress_normalizes_invalid_owner_inputs(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path / "scenario")
    foreign_root = tmp_path / "foreign"
    foreign_root.mkdir()
    foreign = _study_fixture(foreign_root, ("foreign",))
    candidate = scenario.candidate(7.87, direct_support=True)
    anchor = scenario.track_a_spine.anchors[0]

    with pytest.raises(ModeTrackContractError, match="different sample"):
        mint_mode_track_candidate_observation(
            processing_owners=scenario.fixture.owners,
            sample=foreign.sample("foreign"),
            peak_hypothesis=candidate.peak_hypothesis,
        )
    with pytest.raises(ModeTrackContractError, match="PeakHypothesis"):
        mint_mode_track_support_anchor(
            processing_owners=scenario.fixture.owners,
            track=scenario.track_a,
            sample=anchor.sample,
            direct_support_assessment=anchor.direct_support_assessment,
            peak_hypothesis=None,  # type: ignore[arg-type]
        )
    with pytest.raises(ModeTrackContractError, match="processing_owners"):
        reinforce_mode_track_model_set(
            processing_owners=None,  # type: ignore[arg-type]
            model_set=scenario.models,
            assignment_batch=assign_mode_tracks(
                processing_owners=scenario.fixture.owners,
                sample=scenario.fixture.sample("target"),
                model_set=scenario.models,
                candidates=(),
                trajectory_assessment=scenario.missing_trajectory,
            ),
        )


def test_model_set_identity_binds_ordering_behavior_snapshot(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    reordered_context = mint_family_context_ref(
        mz_interval=scenario.family_context.mz_interval,
        rt_interval=scenario.family_context.rt_interval,
        context_policy_version=scenario.family_context.context_policy_version,
        source_lineage_refs=scenario.family_context.source_lineage_refs,
        mode_tracks=(
            replace(
                scenario.track_a,
                group_center_rt=scenario.track_b.group_center_rt,
            ),
            replace(
                scenario.track_b,
                group_center_rt=scenario.track_a.group_center_rt,
            ),
        ),
    )
    reordered_models = fit_mode_track_model_set(
        processing_owners=scenario.fixture.owners,
        family_context=reordered_context,
        direct_support_anchors=(
            scenario.track_a_spine.anchors + scenario.track_b_spine.anchors
        ),
        policy=scenario.policy,
    )

    assert (
        reordered_context.family_context_id == scenario.family_context.family_context_id
    )
    assert reordered_models.model_set_id != scenario.models.model_set_id
    assert tuple(
        (track.group_hypothesis_id, track.ordering_reference_rt_min)
        for track in reordered_models.tracks
    ) != tuple(
        (track.group_hypothesis_id, track.ordering_reference_rt_min)
        for track in scenario.models.tracks
    )

    candidate = scenario.candidate(7.87, direct_support=False)
    original = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(candidate,),
        trajectory_assessment=scenario.assessable_trajectory,
    )
    reordered = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=reordered_models,
        candidates=(candidate,),
        trajectory_assessment=scenario.assessable_trajectory,
    )

    assert original.assignments[0].state == "uniquely_assigned"
    assert reordered.assignments[0].state == "technical_unassessable"
    assert reordered.assignments[0].reason_codes == ("trajectory_crossing",)
    assert canonical_mode_track_assignment_batch_bytes(reordered) != (
        canonical_mode_track_assignment_batch_bytes(original)
    )

    changed_policy = fit_mode_track_model_set(
        processing_owners=scenario.fixture.owners,
        family_context=scenario.family_context,
        direct_support_anchors=(
            scenario.track_a_spine.anchors + scenario.track_b_spine.anchors
        ),
        policy=replace(scenario.policy, prediction_half_width_min=0.11),
    )
    changed_anchors = fit_mode_track_model_set(
        processing_owners=scenario.fixture.owners,
        family_context=scenario.family_context,
        direct_support_anchors=(
            scenario.track_a_spine.anchors[:1] + scenario.track_b_spine.anchors
        ),
        policy=scenario.policy,
    )
    assert changed_policy.model_set_id != scenario.models.model_set_id
    assert changed_anchors.model_set_id != scenario.models.model_set_id


def test_duplicate_physical_trajectory_evidence_cannot_authorize_interpolation(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    shared_evidence_ref = "peak-observation:target-shared-landmark"
    points = tuple(
        LocalFeatureLandmarkEvidence(
            sample_stem="target",
            injection_order=2,
            acquisition_source_id=scenario.target_acquisition_source_id,
            processing_request_id=(
                scenario.fixture.owners.request.processing_request_id
            ),
            processing_attempt_id=(
                scenario.fixture.owners.attempt.processing_attempt_id
            ),
            landmark_id=f"landmark:target:{index}",
            observed_rt_min=local_rt + shift,
            local_trajectory_rt_min=local_rt,
            rt_drift_delta_min=shift,
            quality="accepted",
            evidence_ref=shared_evidence_ref,
        )
        for index, (local_rt, shift) in enumerate(
            ((7.00, 0.10), (9.00, 0.30)),
            start=1,
        )
    )

    with pytest.raises(ValueError, match="physical evidence refs must be unique"):
        assess_local_trajectory(
            LocalTrajectoryEvidenceLookup(points=points),
            ("target",),
        )


def test_interpolation_extrapolation_and_poor_landmark_fallback(
    tmp_path: Path,
) -> None:
    unsupported_interpolation = _scenario(
        tmp_path / "unsupported-interpolation",
        max_interpolation_gap=1,
    )
    interpolation_result = assign_mode_tracks(
        processing_owners=unsupported_interpolation.fixture.owners,
        sample=unsupported_interpolation.fixture.sample("target"),
        model_set=unsupported_interpolation.models,
        candidates=(unsupported_interpolation.candidate(7.87, direct_support=False),),
        trajectory_assessment=unsupported_interpolation.assessable_trajectory,
    )
    assert interpolation_result.assignments[0].state == "out_of_model"
    assert "unsupported_interpolation" in (
        interpolation_result.assignments[0].reason_codes
    )

    supported_extrapolation = _scenario(
        tmp_path / "supported-extrapolation",
        sample_stems=("anchor-1", "anchor-2", "unused", "target"),
        target_injection_order=4,
        max_extrapolation_gap=2,
    )
    extrapolation_result = assign_mode_tracks(
        processing_owners=supported_extrapolation.fixture.owners,
        sample=supported_extrapolation.fixture.sample("target"),
        model_set=supported_extrapolation.models,
        candidates=(supported_extrapolation.candidate(7.97, direct_support=False),),
        trajectory_assessment=supported_extrapolation.assessable_trajectory,
    )
    assert extrapolation_result.assignments[0].state == "uniquely_assigned"

    poor_landmarks = _scenario(tmp_path / "poor-landmarks")
    poor_result = assign_mode_tracks(
        processing_owners=poor_landmarks.fixture.owners,
        sample=poor_landmarks.fixture.sample("target"),
        model_set=poor_landmarks.models,
        candidates=(poor_landmarks.candidate(7.87, direct_support=False),),
        trajectory_assessment=_poor_trajectory(
            "target",
            2,
            poor_landmarks.target_acquisition_source_id,
            poor_landmarks.fixture.owners,
        ),
    )
    assert poor_result.assignments[0].state == "uniquely_assigned"
    assert any(
        "anchor_trajectory_without_local_correction" in item.reason_codes
        for item in poor_result.assignments[0].prediction_assessments
    )


def test_fit_rejects_reinforced_anchors(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    direct = scenario.candidate(7.87, direct_support=True)
    direct_batch = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(direct,),
        trajectory_assessment=scenario.assessable_trajectory,
    )
    reinforced = reinforce_mode_track_model_set(
        processing_owners=scenario.fixture.owners,
        model_set=scenario.models,
        assignment_batch=direct_batch,
    )
    with pytest.raises(ModeTrackContractError, match="initial anchors only"):
        fit_mode_track_model_set(
            processing_owners=scenario.fixture.owners,
            family_context=scenario.family_context,
            direct_support_anchors=tuple(
                anchor for track in reinforced.tracks for anchor in track.anchors
            ),
            policy=scenario.policy,
        )


def test_mixed_prediction_availability_is_technical_unassessable() -> None:
    predicted = ModeTrackPredictionAssessment(
        group_hypothesis_id="track:a",
        status="predicted",
        predicted_rt_min=8.0,
        prediction_interval_min=(7.9, 8.1),
        anchor_refs=("anchor:a",),
        support_artifact_content_ids=("assessment:a",),
        reason_codes=("prediction_available",),
    )
    unavailable = ModeTrackPredictionAssessment(
        group_hypothesis_id="track:b",
        status="unsupported_interpolation",
        predicted_rt_min=None,
        prediction_interval_min=None,
        anchor_refs=("anchor:b",),
        support_artifact_content_ids=("assessment:b",),
        reason_codes=("unsupported_interpolation",),
    )

    assert _global_no_write_state(predictions=(predicted, unavailable)) == (
        "technical_unassessable",
        ("unsupported_interpolation",),
    )


def test_only_unique_direct_support_reinforces_one_track(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    direct = scenario.candidate(7.87, direct_support=True)
    direct_batch = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(direct,),
        trajectory_assessment=scenario.assessable_trajectory,
    )
    reinforced = reinforce_mode_track_model_set(
        processing_owners=scenario.fixture.owners,
        model_set=scenario.models,
        assignment_batch=direct_batch,
    )

    before = {
        track.group_hypothesis_id: len(track.anchors)
        for track in scenario.models.tracks
    }
    after = {
        track.group_hypothesis_id: len(track.anchors) for track in reinforced.tracks
    }
    assert after[scenario.track_a.group_hypothesis_id] == (
        before[scenario.track_a.group_hypothesis_id] + 1
    )
    assert (
        after[scenario.track_b.group_hypothesis_id]
        == (before[scenario.track_b.group_hypothesis_id])
    )

    non_training = scenario.candidate(7.87, direct_support=False)
    non_training_batch = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(non_training,),
        trajectory_assessment=scenario.assessable_trajectory,
    )
    assert (
        reinforce_mode_track_model_set(
            processing_owners=scenario.fixture.owners,
            model_set=scenario.models,
            assignment_batch=non_training_batch,
        )
        == scenario.models
    )


def test_duplicate_direct_support_reinforcement_is_idempotent(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    direct = scenario.candidate(7.87, direct_support=True)
    first_batch = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=scenario.models,
        candidates=(direct,),
        trajectory_assessment=scenario.assessable_trajectory,
    )
    first = reinforce_mode_track_model_set(
        processing_owners=scenario.fixture.owners,
        model_set=scenario.models,
        assignment_batch=first_batch,
    )
    replay_batch = assign_mode_tracks(
        processing_owners=scenario.fixture.owners,
        sample=scenario.fixture.sample("target"),
        model_set=first,
        candidates=(direct,),
        trajectory_assessment=scenario.assessable_trajectory,
    )

    replayed = reinforce_mode_track_model_set(
        processing_owners=scenario.fixture.owners,
        model_set=first,
        assignment_batch=replay_batch,
    )

    assert replayed is first
    assert replayed.model_set_id == first.model_set_id
    assert replayed.lineage_refs == first.lineage_refs


def test_owner_objects_cannot_be_forged() -> None:
    with pytest.raises(TypeError, match="_mint_token"):
        ModeTrackAssignment(
            assignment_id="mode-track-assignment:forged",
            family_context_id="family-context:forged",
            processing_request_id="processing-request:forged",
            processing_attempt_id="processing-attempt:forged",
            canonical_sample_identity_id="sample:forged",
            acquisition_source_id="acquisition-source:forged",
            injection_order=1,
            candidate_observation_id=None,
            peak_hypothesis_id=None,
            peak_observation_content_id=None,
            candidate_observation_class=None,
            direct_support_assessment_artifact_content_id=None,
            group_hypothesis_id=None,
            state="not_observed",
            reason_codes=("not_observed",),
            prediction_assessments=(),
            trajectory_evidence_refs=(),
            lineage_refs=("lineage:forged",),
            sample=None,
            acquisition_source=None,
            candidate_observation=None,
        )


class _Scenario:
    def __init__(
        self,
        *,
        fixture,
        track_a_spine,
        track_b_spine,
        family_context,
        policy,
        models,
        target_injection_order: int,
    ) -> None:
        self.fixture = fixture
        self.track_a_spine = track_a_spine
        self.track_b_spine = track_b_spine
        self.track_a = track_a_spine.group
        self.track_b = track_b_spine.group
        self.family_context = family_context
        self.policy = policy
        self.models = models
        target_source = resolve_request_acquisition_source(
            processing_owners=fixture.owners,
            sample=fixture.sample("target"),
        )
        self.target_acquisition_source_id = target_source.acquisition_source_id
        self.assessable_trajectory = _trajectory(
            "target",
            target_injection_order,
            self.target_acquisition_source_id,
            self.fixture.owners,
        )
        self.missing_trajectory = assess_local_trajectory(
            LocalTrajectoryEvidenceLookup(points=()),
            ("target",),
        )

    def candidate(self, apex_rt: float, *, direct_support: bool):
        assessment, hypothesis = _candidate_assessment_hypothesis(
            self.fixture,
            sample_stem="target",
            apex_rt=apex_rt,
            invocation=f"candidate:{apex_rt:.5f}:{direct_support}",
        )
        return mint_mode_track_candidate_observation(
            processing_owners=self.fixture.owners,
            sample=self.fixture.sample("target"),
            peak_hypothesis=hypothesis,
            direct_support_assessment=assessment if direct_support else None,
        )


def _scenario(
    tmp_path: Path,
    *,
    sample_stems: tuple[str, ...] = ("anchor-1", "target", "anchor-2"),
    target_injection_order: int = 2,
    track_a_anchor_rts: tuple[float, float] = (7.80, 7.84),
    track_b_anchor_rts: tuple[float, float] = (8.30, 8.34),
    prediction_half_width_min: float = 0.1,
    max_interpolation_gap: int = 4,
    max_extrapolation_gap: int = 2,
) -> _Scenario:
    tmp_path.mkdir(parents=True, exist_ok=True)
    fixture = _study_fixture(tmp_path, sample_stems)
    track_a_spine, track_b_spine = _mode_track_spines(
        fixture,
        track_a_anchor_rts=track_a_anchor_rts,
        track_b_anchor_rts=track_b_anchor_rts,
    )
    assert track_a_spine.group is not None
    assert track_b_spine.group is not None
    context = mint_family_context_ref(
        mz_interval=(499.0, 501.0),
        rt_interval=(7.0, 10.0),
        context_policy_version="mode-track-family-context",
        source_lineage_refs=("synthetic:no-raw-mode-track-truth-table",),
        mode_tracks=(track_a_spine.group, track_b_spine.group),
    )
    policy = ModeTrackTrajectoryPolicy(
        trajectory_policy_version="piecewise-linear-injection-local",
        band_policy_version="explicit-half-width",
        prediction_half_width_min=prediction_half_width_min,
        max_interpolation_gap=max_interpolation_gap,
        max_extrapolation_gap=max_extrapolation_gap,
    )
    models = fit_mode_track_model_set(
        processing_owners=fixture.owners,
        family_context=context,
        direct_support_anchors=track_a_spine.anchors + track_b_spine.anchors,
        policy=policy,
    )
    return _Scenario(
        fixture=fixture,
        track_a_spine=track_a_spine,
        track_b_spine=track_b_spine,
        family_context=context,
        policy=policy,
        models=models,
        target_injection_order=target_injection_order,
    )


def _mode_track_spines(
    fixture,
    *,
    track_a_anchor_rts: tuple[float, float],
    track_b_anchor_rts: tuple[float, float],
):
    return _mode_track_spines_many(
        fixture,
        track_anchor_rts=(track_a_anchor_rts, track_b_anchor_rts),
    )


def _mode_track_spines_many(
    fixture,
    *,
    track_anchor_rts: tuple[tuple[float, float], ...],
):
    declarations = tuple(
        (
            sample_stem,
            apex_rt,
            f"track-{track_index}:anchor-{anchor_index}",
        )
        for track_index, anchor_rts in enumerate(track_anchor_rts, start=1)
        for anchor_index, (sample_stem, apex_rt) in enumerate(
            zip(("anchor-1", "anchor-2"), anchor_rts, strict=True),
            start=1,
        )
    )
    support_inputs = []
    for index, (sample_stem, apex_rt, invocation) in enumerate(
        declarations,
        start=1,
    ):
        sample = fixture.sample(sample_stem)
        assessment = _assessment(
            fixture,
            sample_stem,
            support_apex=apex_rt,
            scan_number=1000 * index,
            invocation=invocation,
        )
        peak = assessment.candidate_peaks[0]
        support = compose_cid_nl_chromatographic_support_ref(
            processing_owners=fixture.owners,
            sample=sample,
            scan_support_refs=assessment.individual_support_refs,
        )
        hypothesis = compose_cid_nl_peak_hypothesis(
            processing_owners=fixture.owners,
            sample=sample,
            peak_observation=peak,
            chromatographic_support=support,
        )
        owner = owner_from_cid_nl_assessment_support(
            sample=sample,
            assessment=assessment,
            hypothesis=hypothesis,
        )
        support_inputs.append((sample, assessment, peak, hypothesis, owner))

    within_track_delta_sec = (
        max(abs(anchor_rts[1] - anchor_rts[0]) for anchor_rts in track_anchor_rts)
        * 60.0
    )
    between_track_delta_sec = (
        min(
            abs(left - right)
            for left_index, left_track in enumerate(track_anchor_rts)
            for right_track in track_anchor_rts[left_index + 1 :]
            for left in left_track
            for right in right_track
        )
        * 60.0
    )
    max_rt_sec = (within_track_delta_sec + between_track_delta_sec) / 2.0
    config = AlignmentConfig(
        preferred_rt_sec=min(10.0, max_rt_sec),
        max_rt_sec=max_rt_sec,
        owner_apex_close_sec=max_rt_sec,
    )
    groups = construct_cross_sample_peak_group_hypotheses(
        tuple(item[4] for item in support_inputs),
        config=config,
    )
    assert len(groups) == len(track_anchor_rts)
    spines = []
    for group in groups:
        members = tuple(
            item for item in support_inputs if item[3].hypothesis_id in group.owner_ids
        )
        anchors = tuple(
            mint_mode_track_support_anchor(
                processing_owners=fixture.owners,
                track=group,
                sample=sample,
                direct_support_assessment=assessment,
                peak_hypothesis=hypothesis,
            )
            for sample, assessment, _peak, hypothesis, _owner in members
        )
        spines.append(SimpleNamespace(group=group, anchors=anchors))
    return tuple(sorted(spines, key=lambda item: item.group.group_center_rt))


def _candidate_assessment_hypothesis(
    fixture,
    *,
    sample_stem: str,
    apex_rt: float,
    invocation: str,
):
    sample = fixture.sample(sample_stem)
    assessment = _assessment(
        fixture,
        sample_stem,
        support_apex=apex_rt,
        scan_number=9001,
        invocation=invocation,
    )
    return assessment, _hypothesis_from_assessment(
        fixture,
        sample=sample,
        assessment=assessment,
    )


def _hypothesis_from_assessment(fixture, *, sample, assessment):
    support = compose_cid_nl_chromatographic_support_ref(
        processing_owners=fixture.owners,
        sample=sample,
        scan_support_refs=assessment.individual_support_refs,
    )
    hypothesis = compose_cid_nl_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=sample,
        peak_observation=assessment.candidate_peaks[0],
        chromatographic_support=support,
    )
    return hypothesis


def _different_request_same_sources(fixture):
    request = build_processing_request_identity(
        acquisition_sources=fixture.request.acquisition_sources,
        acquisition_bindings=fixture.request.acquisition_bindings,
        scientific_input_schema_version=(
            fixture.request.scientific_input_schema_version
        ),
        processing_algorithm_version="mode-track-foreign-request",
        processing_contract_version=fixture.request.processing_contract_version,
        scientific_inputs=json.loads(fixture.request.normalized_scientific_inputs_json),
    )
    attempt = issue_processing_attempt(request)
    return compose_processing_owner_references(
        request=request,
        attempt=attempt,
        sample_snapshot=fixture.samples[0].snapshot,
        sample_resolutions=fixture.samples,
        target_snapshot=fixture.target.snapshot,
    )


def _trajectory(
    sample_stem: str,
    injection_order: int,
    acquisition_source_id: str,
    processing_owners: ProcessingOwnerReferences,
):
    points = tuple(
        LocalFeatureLandmarkEvidence(
            sample_stem=sample_stem,
            injection_order=injection_order,
            acquisition_source_id=acquisition_source_id,
            processing_request_id=(processing_owners.request.processing_request_id),
            processing_attempt_id=(processing_owners.attempt.processing_attempt_id),
            landmark_id=f"landmark:{sample_stem}:{index}",
            observed_rt_min=local_rt + 0.05,
            local_trajectory_rt_min=local_rt,
            rt_drift_delta_min=0.05,
            quality="accepted",
            evidence_ref=f"peak-observation:{sample_stem}-landmark-{index}",
        )
        for index, local_rt in enumerate((7.00, 9.00), start=1)
    )
    return assess_local_trajectory(
        LocalTrajectoryEvidenceLookup(points=points),
        (sample_stem,),
    )


def _poor_trajectory(
    sample_stem: str,
    injection_order: int,
    acquisition_source_id: str,
    processing_owners: ProcessingOwnerReferences,
):
    point = LocalFeatureLandmarkEvidence(
        sample_stem=sample_stem,
        injection_order=injection_order,
        acquisition_source_id=acquisition_source_id,
        processing_request_id=processing_owners.request.processing_request_id,
        processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        landmark_id=f"landmark:{sample_stem}:poor",
        observed_rt_min=7.87,
        local_trajectory_rt_min=7.82,
        rt_drift_delta_min=0.05,
        quality="poor",
        evidence_ref=f"peak-observation:{sample_stem}-poor-landmark",
        exclusion_reason="synthetic poor landmark",
    )
    return assess_local_trajectory(
        LocalTrajectoryEvidenceLookup(points=(point,)),
        (sample_stem,),
    )


def _multi_point_trajectory(
    sample_stem: str,
    injection_order: int,
    acquisition_source_id: str,
    local_rt_and_shift: tuple[tuple[float, float], ...],
    processing_owners: ProcessingOwnerReferences,
):
    points = tuple(
        LocalFeatureLandmarkEvidence(
            sample_stem=sample_stem,
            injection_order=injection_order,
            acquisition_source_id=acquisition_source_id,
            processing_request_id=(processing_owners.request.processing_request_id),
            processing_attempt_id=(processing_owners.attempt.processing_attempt_id),
            landmark_id=f"landmark:{sample_stem}:{index}",
            observed_rt_min=local_rt + shift,
            local_trajectory_rt_min=local_rt,
            rt_drift_delta_min=shift,
            quality="accepted",
            evidence_ref=f"peak-observation:{sample_stem}-landmark-{index}",
        )
        for index, (local_rt, shift) in enumerate(local_rt_and_shift, start=1)
    )
    return assess_local_trajectory(
        LocalTrajectoryEvidenceLookup(points=points),
        (sample_stem,),
    )
