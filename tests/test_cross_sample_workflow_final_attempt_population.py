"""Complete-population and fit-once provenance workflow bridge tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import xic_extractor.alignment.mode_track.refinement as refinement_owner
from tests.cid_nl_peak_support_no_raw_fixture import cid_nl_peak
from tests.test_alignment_owner_clustering import _owner
from tests.test_backfill_observation_repair_authorization import (
    _authorization_population,
)
from tests.test_backfill_observation_repair_execution import _repair_result
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
    CrossSampleReconciliationContractError,
    build_cross_sample_post_mt0_context,
    consume_mode_track_final_attempt,
)
from xic_extractor.alignment.mode_track.assignment import ModeTrackTrajectoryPolicy
from xic_extractor.alignment.mode_track.contracts import (
    ModeTrackEvidenceProfile,
    _mint_accepted_mode_track_refinement,
)
from xic_extractor.alignment.mode_track.producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
)
from xic_extractor.alignment.mode_track.refinement import _mint_final_attempt_result


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
            trajectory_policy_version="anchor-trajectory",
            band_policy_version="fixed-band",
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
            trajectory_policy_version="anchor-trajectory",
            band_policy_version="fixed-band",
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
        observation=cid_nl_peak(fixture, "s3"),
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
            owner.sample_stem: index for index, owner in enumerate(owners, start=1)
        },
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory", "fixed-band", 0.1, 10, 10
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
                "anchor-trajectory", "fixed-band", 0.1, 10, 10
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
    crossed_context = replace(
        context,
        fit_once_source_id=crossed_source.fit_once_source_id,
    )
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="complete CrossSample binding",
    ):
        consume_mode_track_final_attempt(
            context=crossed_context,
            post_repair=reconciled.mode_track_input,
            terminal=terminal,
            final_attempt=crossed_attempt,
        )
