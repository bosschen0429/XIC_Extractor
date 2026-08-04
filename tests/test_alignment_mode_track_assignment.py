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
    ModeTrackTrajectoryPolicy,
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
            peak_hypothesis_id=None,
            candidate_observation_class=None,
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
        max_interpolation_gap=4,
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
