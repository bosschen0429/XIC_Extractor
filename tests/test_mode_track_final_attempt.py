"""Public post-repair ModeTrack final-attempt contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from inspect import signature
from pathlib import Path

import pytest

import xic_extractor.alignment.mode_track as mode_track_api
import xic_extractor.alignment.mode_track.refinement as refinement_owner
from tests.issue161_no_raw_fixture import issue161_peak
from tests.test_alignment_owner_clustering import _owner
from tests.test_backfill_observation_repair_authorization import (
    _authorization_population,
)
from tests.test_backfill_observation_repair_execution import _repair_result
from tests.test_cell_observations import _repaired_handoff
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.backfill import execute_observation_repair
from xic_extractor.alignment.cross_sample import (
    reconcile_nonmember_observation_repair,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleNeighborhoodBudget,
    CrossSampleSuccessorApplication,
    classify_cross_sample_initial_neighborhood_edge,
    group_member_subject_from_peak_observation,
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
    CrossSamplePostRepairModeTrackInput,
    CrossSampleReconciliationContractError,
    build_cross_sample_post_mt0_context,
    consume_mode_track_final_attempt,
    reconcile_observation_repair,
)
from xic_extractor.alignment.mode_track import (
    AssignedModeTrackFinalAttempt,
    AssignmentUnavailableModeTrackFinalAttempt,
    ModeTrackFinalAttemptContractError,
    RejectedModeTrackFinalAttempt,
    UnassessableModeTrackFinalAttempt,
    UntrainedModeTrackFinalAttempt,
    run_post_repair_mode_track_final_attempt,
)
from xic_extractor.alignment.mode_track.assignment import (
    ModeTrackRepairAssignmentObservation,
    ModeTrackTrajectoryPolicy,
    _ModeTrackAssignmentUnavailableError,
    assign_mode_tracks,
    fit_mode_track_model_set,
    mint_mode_track_fit_once_source,
)
from xic_extractor.alignment.mode_track.contracts import (
    ModeTrackEvidenceProfile,
    ModeTrackRefinementContractError,
    UntrainedModeTrackRefinementEvidence,
    _mint_accepted_mode_track_refinement,
    _mint_untrained_mode_track_refinement_evidence,
)
from xic_extractor.alignment.mode_track.producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
)
from xic_extractor.alignment.mode_track.refinement import _mint_final_attempt_result


def test_final_attempt_public_operation_has_no_caller_authority_inputs() -> None:
    assert tuple(signature(run_post_repair_mode_track_final_attempt).parameters) == (
        "source",
        "repair_receipt",
        "post_repair",
    )
    assert not hasattr(mode_track_api, "propose_mode_track_refinement")
    assert not hasattr(mode_track_api, "AssignedModeTrackRefinementEvidence")
    assert not hasattr(mode_track_api, "UntrainedModeTrackRefinementEvidence")
    with pytest.raises(TypeError):
        run_post_repair_mode_track_final_attempt(
            source=None,  # type: ignore[arg-type]
            repair_receipt=None,  # type: ignore[arg-type]
            post_repair=None,  # type: ignore[arg-type]
            successor_member_subject_ids=(),  # type: ignore[call-arg]
        )


def test_final_attempt_result_variants_reject_caller_construction() -> None:
    for result_type in (
        AssignedModeTrackFinalAttempt,
        UntrainedModeTrackFinalAttempt,
        AssignmentUnavailableModeTrackFinalAttempt,
        RejectedModeTrackFinalAttempt,
        UnassessableModeTrackFinalAttempt,
    ):
        with pytest.raises(ModeTrackFinalAttemptContractError):
            result_type(  # type: ignore[call-arg]
                binding=None,
                assignment_batches=(),
                proposal=None,
                _mint_token=object(),
            )


def test_existing_member_repair_replaces_stale_candidate_without_training(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, spine, _handoff, _cell, repair, _observation = _repaired_handoff(
        tmp_path
    )
    group = repair.subject.predecessor_group
    member = group.member_subjects[0]
    assert repair.evidence is not None
    repaired_apex = repair.evidence.rt_apex_min
    assert repaired_apex != 9.0
    _, model_set, _, source = bootstrap_mode_tracks_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=((fixture.sample("s1"), spine.assessments[0]),),
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
    )
    owner = replace(
        _owner("s1", "existing-repair", apex_rt=9.0),
        cross_sample_member_subject=member,
    )
    context = build_cross_sample_post_mt0_context(
        processing_request_id=group.processing_request_id,
        processing_owners=fixture.owners,
        population=(group,),
        application=repair.subject.successor_application,
        owners=(owner,),
        classified_edges=(),
        anchors=(),
        injection_order={"s1": 1},
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
        ),
        budget=CrossSampleNeighborhoodBudget(86, 3655),
        fit_once_source_id=source.fit_once_source_id,
    )
    post_repair = reconcile_observation_repair(
        context=context,
        repair_receipt=repair,
    )
    assert isinstance(post_repair, CrossSamplePostRepairModeTrackInput)
    result = run_post_repair_mode_track_final_attempt(
        source=source,
        repair_receipt=repair,
        post_repair=post_repair,
    )

    assert source.model_set is model_set
    repaired = tuple(
        assignment
        for batch in result.assignment_batches
        for assignment in batch.assignments
        if isinstance(
            assignment.candidate_observation,
            ModeTrackRepairAssignmentObservation,
        )
    )
    assert len(repaired) == 1
    assert repaired[0].candidate_observation.apex_rt_min == repaired_apex
    assert repaired[0].candidate_observation_class == "non_training"
    assert repaired[0].direct_support_assessment is None
    assert all(
        assignment.peak_observation_content_id
        not in member.physical_observation_ids
        for batch in result.assignment_batches
        for assignment in batch.assignments
    )

    def unavailable_assignment(**_kwargs):
        raise _ModeTrackAssignmentUnavailableError

    monkeypatch.setattr(
        refinement_owner,
        "assign_mode_tracks",
        unavailable_assignment,
    )
    unavailable = run_post_repair_mode_track_final_attempt(
        source=source,
        repair_receipt=repair,
        post_repair=post_repair,
    )
    assert isinstance(unavailable, AssignmentUnavailableModeTrackFinalAttempt)
    assert unavailable.assignment_batches == ()
    assert unavailable.proposal.reason_codes == ("assignment_unavailable",)


def test_admitted_repair_with_unaffected_group_runs_one_final_assignment_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    assessments_by_sample = {
        assessment.content.canonical_sample_identity_id: assessment
        for assessment in spine.assessments
    }
    _, _, _, source = bootstrap_mode_tracks_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=tuple(
            (sample, assessments_by_sample[sample.identity.sample_identity_id])
            for sample in fixture.samples
        ),
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
    )
    _, _, _, crossed_source = bootstrap_mode_tracks_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=tuple(
            (sample, assessments_by_sample[sample.identity.sample_identity_id])
            for sample in fixture.samples
        ),
        alignment_config=AlignmentConfig(anchor_min_seed_events=100),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
    )
    assert crossed_source.fit_once_source_id != source.fit_once_source_id
    executed = []

    def execute(candidate):
        _built, result = _repair_result(fixture, coverage, candidate)
        executed.append(result)
        return result

    terminal = execute_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
        execute=execute,
    )
    candidate = terminal.candidate
    assert candidate is not None
    owners = tuple(candidate.logical_request.item.feature.owners)
    group = candidate.predecessor_group
    unaffected_member = group_member_subject_from_peak_observation(
        processing_owners=fixture.owners,
        sample=fixture.sample("s3"),
        observation=issue161_peak(fixture, "s3"),
    )
    unaffected_group = mint_cross_sample_group_identity(
        processing_request_id=group.processing_request_id,
        member_subjects=(unaffected_member,),
    )
    unaffected_owner = replace(
        _owner("s3", "unaffected", apex_rt=8.2),
        cross_sample_member_subject=unaffected_member,
    )
    owners = (*owners, unaffected_owner)
    population = tuple(
        sorted(
            (group, unaffected_group),
            key=lambda item: item.group_hypothesis_id,
        )
    )
    classified_edges = tuple(
        classify_cross_sample_initial_neighborhood_edge(
            left_owner=left_owner,
            right_owner=right_owner,
            context="hard_gate",
            config=coverage.epoch.alignment_config,
        )
        for left_index, left_owner in enumerate(owners)
        for right_owner in owners[left_index + 1 :]
    )
    context = build_cross_sample_post_mt0_context(
        processing_request_id=group.processing_request_id,
        processing_owners=fixture.owners,
        population=population,
        application=CrossSampleSuccessorApplication(
            status="assessable",
            predecessors=population,
            successors=population,
            lineage=(),
            reason="unchanged_membership_no_lineage",
        ),
        owners=owners,
        classified_edges=classified_edges,
        anchors=(),
        injection_order={
            owner.sample_stem: index
            for index, owner in enumerate(owners, start=1)
        },
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
        ),
        budget=CrossSampleNeighborhoodBudget(86, 3655),
        fit_once_source_id=source.fit_once_source_id,
    )
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="exactly equal",
    ):
        component_member_ids = {
            member.member_subject_id for member in group.member_subjects
        }
        component_owners = tuple(
            owner
            for owner in owners
            if owner.cross_sample_member_subject is not None
            and owner.cross_sample_member_subject.member_subject_id
            in component_member_ids
        )
        component_edges = tuple(
            edge
            for edge in classified_edges
            if edge.left_member_subject_id in component_member_ids
            and edge.right_member_subject_id in component_member_ids
        )
        build_cross_sample_post_mt0_context(
            processing_request_id=group.processing_request_id,
            processing_owners=fixture.owners,
            population=population,
            application=CrossSampleSuccessorApplication(
                status="assessable",
                predecessors=(group,),
                successors=(group,),
                lineage=(),
                reason="unchanged_membership_no_lineage",
            ),
            owners=component_owners,
            classified_edges=component_edges,
            anchors=(),
            injection_order={
                owner.sample_stem: index
                for index, owner in enumerate(component_owners, start=1)
            },
            evidence_profile=ModeTrackEvidenceProfile(
                "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
            ),
            budget=CrossSampleNeighborhoodBudget(86, 3655),
            fit_once_source_id=source.fit_once_source_id,
        )
    reconciled = reconcile_nonmember_observation_repair(
        context=context,
        repair_terminal=terminal,
        repair_result=executed[0],
        alignment_config=coverage.epoch.alignment_config,
    )
    assert reconciled.mode_track_input is not None
    calls = 0
    real_assign = refinement_owner.assign_mode_tracks

    def counted_assign(**kwargs):
        nonlocal calls
        calls += 1
        return real_assign(**kwargs)

    monkeypatch.setattr(refinement_owner, "assign_mode_tracks", counted_assign)
    monkeypatch.setattr(
        refinement_owner,
        "bootstrap_mode_tracks_from_cid_nl_support",
        lambda **_kwargs: pytest.fail("final attempt refit the model"),
    )
    result = refinement_owner.run_post_repair_mode_track_final_attempt(
        source=source,
        repair_receipt=terminal.receipt,
        post_repair=reconciled.mode_track_input,
    )

    assert calls == len(
        {
            member.canonical_sample_identity_id
            for group in reconciled.mode_track_input.post_repair_population
            for member in group.member_subjects
        }
    )
    repaired_assignments = tuple(
        assignment
        for batch in result.assignment_batches
        for assignment in batch.assignments
        if isinstance(
            assignment.candidate_observation,
            refinement_owner.ModeTrackRepairAssignmentObservation,
        )
    )
    assert len(repaired_assignments) == 1
    assert repaired_assignments[0].candidate_observation_class == "non_training"
    assert repaired_assignments[0].direct_support_assessment is None
    component_ids = (
        reconciled.mode_track_input.post_repair_trajectory.member_subject_ids
    )
    accepted = _mint_accepted_mode_track_refinement(
        trajectory_input=reconciled.mode_track_input.post_repair_trajectory,
        successor_member_subject_ids=(component_ids,),
    )
    accepted_result = _mint_final_attempt_result(
        source=source,
        repair_receipt=terminal.receipt,
        post_repair=reconciled.mode_track_input,
        assignment_batches=result.assignment_batches,
        proposal=accepted,
        discriminant="assigned",
    )
    final = consume_mode_track_final_attempt(
        context=context,
        post_repair=reconciled.mode_track_input,
        terminal=terminal,
        final_attempt=accepted_result,
    )
    assert type(final) is CrossSampleFinalReceipt
    assert unaffected_group in final.final_population
    assert {
        member.member_subject_id
        for group_identity in final.final_population
        for member in group_identity.member_subjects
    } == {
        member.member_subject_id
        for group_identity in reconciled.mode_track_input.post_repair_population
        for member in group_identity.member_subjects
    }
    crossed_attempt = refinement_owner.run_post_repair_mode_track_final_attempt(
        source=crossed_source,
        repair_receipt=terminal.receipt,
        post_repair=reconciled.mode_track_input,
    )
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="complete CrossSample binding",
    ):
        consume_mode_track_final_attempt(
            context=context,
            post_repair=reconciled.mode_track_input,
            terminal=terminal,
            final_attempt=crossed_attempt,
        )


def test_no_anchor_attempt_returns_unassessable_for_over_budget_neighborhood(
    tmp_path: Path,
) -> None:
    fixture, spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    assessments_by_sample = {
        assessment.content.canonical_sample_identity_id: assessment
        for assessment in spine.assessments
    }
    family_context, _, _, trained_source = bootstrap_mode_tracks_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=tuple(
            (sample, assessments_by_sample[sample.identity.sample_identity_id])
            for sample in fixture.samples
        ),
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
    )
    untrained_model = fit_mode_track_model_set(
        processing_owners=fixture.owners,
        family_context=family_context,
        direct_support_anchors=(),
        policy=trained_source.model_set.policy,
    )
    untrained_batches = tuple(
        assign_mode_tracks(
            processing_owners=fixture.owners,
            sample=sample_source.sample,
            model_set=untrained_model,
            candidates=sample_source.candidates,
            trajectory_assessment=sample_source.trajectory_assessment,
        )
        for sample_source in trained_source.sample_sources
    )
    source = mint_mode_track_fit_once_source(
        processing_owners=fixture.owners,
        family_context=family_context,
        model_set=untrained_model,
        sample_sources=trained_source.sample_sources,
        initial_assignment_batches=untrained_batches,
    )
    executed = []

    def execute(candidate):
        _built, result = _repair_result(fixture, coverage, candidate)
        executed.append(result)
        return result

    terminal = execute_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
        execute=execute,
    )
    candidate = terminal.candidate
    assert candidate is not None
    owners = tuple(candidate.logical_request.item.feature.owners)
    group = candidate.predecessor_group
    context = build_cross_sample_post_mt0_context(
        processing_request_id=group.processing_request_id,
        processing_owners=fixture.owners,
        population=(group,),
        application=CrossSampleSuccessorApplication(
            status="assessable",
            predecessors=(group,),
            successors=(group,),
            lineage=(),
            reason="unchanged_membership_no_lineage",
        ),
        owners=owners,
        classified_edges=(),
        anchors=(),
        injection_order={
            owner.sample_stem: index
            for index, owner in enumerate(owners, start=1)
        },
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
        ),
        budget=CrossSampleNeighborhoodBudget(1, 3655),
        fit_once_source_id=source.fit_once_source_id,
    )
    reconciled = reconcile_nonmember_observation_repair(
        context=context,
        repair_terminal=terminal,
        repair_result=executed[0],
        alignment_config=coverage.epoch.alignment_config,
    )
    assert reconciled.mode_track_input is not None
    assert (
        reconciled.mode_track_input.post_repair_trajectory.neighborhood_status
        == "unassessable"
    )

    result = run_post_repair_mode_track_final_attempt(
        source=source,
        repair_receipt=terminal.receipt,
        post_repair=reconciled.mode_track_input,
    )

    assert isinstance(result, UnassessableModeTrackFinalAttempt)
    assert result.assignment_batches == ()


def test_untrained_evidence_is_owner_minted_and_immutable() -> None:
    profile = ModeTrackEvidenceProfile(
        "anchor-trajectory-v1",
        "fixed-band-v1",
        0.1,
        10,
        10,
    )
    evidence = _mint_untrained_mode_track_refinement_evidence(
        anchors=(),
        evidence_profile=profile,
        reason="assignment_unavailable",
    )

    assert evidence.variant == "assignment_unavailable"
    assert evidence.reason == "assignment_unavailable"
    assert evidence.anchors == ()
    assert evidence.evidence_profile == profile
    with pytest.raises(FrozenInstanceError):
        evidence.reason = "untrained"
    with pytest.raises((TypeError, ModeTrackRefinementContractError)):
        UntrainedModeTrackRefinementEvidence(
            variant="untrained",
            anchors=(),
            evidence_profile=profile,
            reason="untrained",
        )
