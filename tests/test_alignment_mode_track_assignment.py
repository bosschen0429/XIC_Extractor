from __future__ import annotations

import ast
import json
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_alignment_conservative_backfill import (
    _assessment,
    _spine,
    _study_fixture,
)
from xic_extractor.alignment.cell_observations import CellObservationRef
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
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
from xic_extractor.alignment.identity_coherence.family_context import (
    mint_family_context_ref,
)
from xic_extractor.alignment.identity_coherence.mode_track_assignment import (
    ModeTrackAssignment,
    ModeTrackContractError,
    ModeTrackTrajectoryPolicy,
    _peak_hypothesis_family_exclusions,
    assign_mode_tracks,
    canonical_mode_track_assignment_batch_bytes,
    fit_mode_track_model_set,
    mint_mode_track_candidate_observation,
    mint_mode_track_support_anchor,
    reinforce_mode_track_model_set,
)
from xic_extractor.peak_detection.cid_nl_chromatographic_support import (
    compose_cid_nl_chromatographic_support_ref,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    compose_cid_nl_peak_hypothesis,
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
    assert assignment.trajectory_evidence_refs == ("peak-observation:target-landmark",)

    payload = json.loads(canonical_mode_track_assignment_batch_bytes(forward))
    assert payload["schema_version"] == "mode_track_assignment_batch_v1"
    assert payload["assignments"][0]["state"] == "uniquely_assigned"
    assert (
        payload["assignments"][0]["candidate_observation_id"]
        == candidate.candidate_observation_id
    )
    assert (
        payload["assignments"][0]["direct_support_cell_artifact_content_id"]
        == candidate.direct_support_cell_artifact_content_id
    )


@pytest.mark.parametrize(
    ("case", "expected_state"),
    (
        ("overlapping_intervals", "ambiguous_multiple_tracks"),
        ("outside_all_intervals", "out_of_model"),
        ("missing_landmarks", "technical_unassessable"),
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


def test_zero_anchor_tracks_are_typed_technical_no_write(tmp_path: Path) -> None:
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

    assert result.assignments[0].state == "technical_unassessable"
    assert "sparse_track_anchors" in result.assignments[0].reason_codes
    assert all(
        assessment.status == "sparse_anchors"
        and assessment.anchor_refs == ()
        and assessment.support_artifact_content_ids == ()
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
        candidates=(crossing.candidate(8.10, direct_support=False),),
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


@pytest.mark.parametrize(
    ("occupancy", "candidate_rts", "expected_tracks"),
    (
        ("m1_only", (7.87,), ("track_a",)),
        ("m2_only", (8.37,), ("track_b",)),
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


def test_each_track_uses_its_local_landmark_region_without_global_median(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    trajectory = _multi_point_trajectory(
        "target",
        2,
        ((7.82, -0.20), (8.32, 0.20)),
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


def test_local_landmarks_do_not_authorize_unbounded_rt_extrapolation(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    trajectory = _multi_point_trajectory(
        "target",
        2,
        ((7.90, -0.10), (8.00, 0.10)),
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
        context_policy_version="mode-track-family-context-v1",
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

    with pytest.raises(ModeTrackContractError, match="stale request binding"):
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
            trajectory_assessment=_trajectory("target", 99),
        )


def test_policy_rejects_integer_float_identity_alias() -> None:
    with pytest.raises(ModeTrackContractError, match="canonical float"):
        ModeTrackTrajectoryPolicy(
            trajectory_policy_version="piecewise-linear-injection-local-v1",
            band_policy_version="explicit-half-width-v1",
            prediction_half_width_min=1,  # type: ignore[arg-type]
            max_interpolation_gap=4,
            max_extrapolation_gap=2,
        )


def test_interpolation_extrapolation_and_poor_landmarks_fail_closed(
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
        trajectory_assessment=_poor_trajectory("target", 2),
    )
    assert poor_result.assignments[0].state == "technical_unassessable"
    assert "trajectory_poor_landmarks" in poor_result.assignments[0].reason_codes


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
            model_set=scenario.models,
            assignment_batch=non_training_batch,
        )
        == scenario.models
    )


def test_owner_objects_cannot_be_forged_or_gain_product_surface() -> None:
    with pytest.raises(TypeError, match="_mint_token"):
        ModeTrackAssignment(
            assignment_id="mode-track-assignment:forged",
            family_context_id="family-context:forged",
            canonical_sample_identity_id="sample:forged",
            acquisition_source_id="acquisition-source:forged",
            injection_order=1,
            candidate_observation_id=None,
            peak_hypothesis_id=None,
            candidate_observation_class=None,
            direct_support_cell_artifact_content_id=None,
            group_hypothesis_id=None,
            state="not_observed",
            reason_codes=("not_observed",),
            prediction_assessments=(),
            trajectory_evidence_refs=(),
            lineage_refs=("lineage:forged",),
            sample=None,
            acquisition_source=None,
            peak_hypothesis=None,
            direct_support_cell=None,
        )

    forbidden_tokens = {
        "matrix",
        "selected_area",
        "numeric_value",
        "counted_detection",
        "writer",
        "public_family",
        "feature_family",
        "method_subject",
    }
    assert not {
        field.name
        for field in fields(ModeTrackAssignment)
        if any(token in field.name for token in forbidden_tokens)
    }


def test_mode_track_owner_has_no_product_or_raw_scan_dependencies() -> None:
    path = (
        Path(__file__).parents[1]
        / "xic_extractor"
        / "alignment"
        / "identity_coherence"
        / "mode_track_assignment.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    )
    forbidden = (
        "matrix",
        "backfill",
        "writer",
        "raw",
        "thermo",
    )
    assert not {
        module
        for module in imported
        if any(token in module.lower() for token in forbidden)
    }


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
        self.assessable_trajectory = _trajectory(
            "target",
            target_injection_order,
        )
        self.missing_trajectory = assess_local_trajectory(
            LocalTrajectoryEvidenceLookup(points=()),
            ("target",),
        )

    def candidate(self, apex_rt: float, *, direct_support: bool):
        spine = _spine(
            self.fixture,
            support_apex_by_sample={"target": apex_rt},
            invocation=f"candidate:{apex_rt:.5f}:{direct_support}",
        )
        cell = _support_cells(spine)[0]
        return mint_mode_track_candidate_observation(
            processing_owners=self.fixture.owners,
            sample=self.fixture.sample("target"),
            peak_hypothesis=cell.peak_hypothesis,
            direct_support_cell=cell if direct_support else None,
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
        context_policy_version="mode-track-family-context-v1",
        source_lineage_refs=("synthetic:no-raw-mode-track-truth-table",),
        mode_tracks=(track_a_spine.group, track_b_spine.group),
    )
    policy = ModeTrackTrajectoryPolicy(
        trajectory_policy_version="piecewise-linear-injection-local-v1",
        band_policy_version="explicit-half-width-v1",
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


def _support_cells(spine) -> tuple[CellObservationRef, ...]:
    return tuple(
        cell for cell in spine.cells if cell.content.method_result == "SUPPORT"
    )


def _mode_track_spines(
    fixture,
    *,
    track_a_anchor_rts: tuple[float, float],
    track_b_anchor_rts: tuple[float, float],
):
    declarations = (
        ("anchor-1", track_a_anchor_rts[0], "track-a:anchor-1"),
        ("anchor-2", track_a_anchor_rts[1], "track-a:anchor-2"),
        ("anchor-1", track_b_anchor_rts[0], "track-b:anchor-1"),
        ("anchor-2", track_b_anchor_rts[1], "track-b:anchor-2"),
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
        max(
            abs(track_a_anchor_rts[1] - track_a_anchor_rts[0]),
            abs(track_b_anchor_rts[1] - track_b_anchor_rts[0]),
        )
        * 60.0
    )
    between_track_delta_sec = (
        min(
            abs(left - right)
            for left in track_a_anchor_rts
            for right in track_b_anchor_rts
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
    assert len(groups) == 2
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


def _trajectory(sample_stem: str, injection_order: int):
    point = LocalFeatureLandmarkEvidence(
        sample_stem=sample_stem,
        injection_order=injection_order,
        landmark_id=f"landmark:{sample_stem}",
        observed_rt_min=4.05,
        local_trajectory_rt_min=4.00,
        rt_drift_delta_min=0.05,
        quality="accepted",
        evidence_ref=f"peak-observation:{sample_stem}-landmark",
    )
    return assess_local_trajectory(
        LocalTrajectoryEvidenceLookup(points=(point,)),
        (sample_stem,),
    )


def _poor_trajectory(sample_stem: str, injection_order: int):
    point = LocalFeatureLandmarkEvidence(
        sample_stem=sample_stem,
        injection_order=injection_order,
        landmark_id=f"landmark:{sample_stem}:poor",
        observed_rt_min=4.05,
        local_trajectory_rt_min=4.00,
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
    local_rt_and_shift: tuple[tuple[float, float], ...],
):
    points = tuple(
        LocalFeatureLandmarkEvidence(
            sample_stem=sample_stem,
            injection_order=injection_order,
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
