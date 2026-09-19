"""Terminal CrossSample bridges used by the shared alignment workflow."""

from __future__ import annotations

from copy import copy
from dataclasses import replace
from pathlib import Path

import pytest

import xic_extractor.alignment.cross_sample_peak_groups as cross_sample_owner
import xic_extractor.alignment.cross_sample_reconciliation as reconciliation_owner
from tests.test_alignment_owner_clustering import _owner
from tests.test_backfill_observation_repair_authorization import (
    _authorization_population,
)
from tests.test_backfill_observation_repair_execution import _repair_result
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.backfill import execute_observation_repair
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupMemberSubject,
    CrossSampleNeighborhoodBudget,
    CrossSampleSuccessorApplication,
    classify_cross_sample_initial_neighborhood_edge,
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
    CrossSampleReconciliationContractError,
    build_cross_sample_post_mt0_context,
    consume_mode_track_final_attempt,
    consume_observation_repair_terminal,
)
from xic_extractor.alignment.mode_track.assignment import ModeTrackTrajectoryPolicy
from xic_extractor.alignment.mode_track.contracts import ModeTrackEvidenceProfile
from xic_extractor.alignment.mode_track.producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
)
from xic_extractor.alignment.mode_track.refinement import (
    run_post_repair_mode_track_final_attempt,
)
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)


def test_nonmember_no_repair_preserves_owner_negative_without_mt1(
    tmp_path: Path,
) -> None:
    fixture, _spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    executed = []

    def execute(candidate):
        built, result = _repair_result(fixture, coverage, candidate)
        unselected = copy(built.peak_fact_observations[0])
        object.__setattr__(unselected, "selected_for_output", False)
        no_repair_result = replace(result, peak_fact_observations=(unselected,))
        executed.append(no_repair_result)
        return no_repair_result

    terminal = execute_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
        execute=execute,
    )
    candidate = terminal.candidate
    assert candidate is not None
    assert terminal.receipt is not None
    assert terminal.receipt.outcome == "no_repair"
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
        owners=tuple(candidate.logical_request.item.feature.owners),
        classified_edges=(),
        anchors=(),
        injection_order={
            owner.sample_stem: index
            for index, owner in enumerate(
                candidate.logical_request.item.feature.owners,
                start=1,
            )
        },
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory", "fixed-band", 0.1, 10, 10
        ),
        budget=CrossSampleNeighborhoodBudget(86, 3655),
    )

    final = consume_observation_repair_terminal(
        context=context,
        terminal=terminal,
        repair_result=executed[0],
        alignment_config=coverage.epoch.alignment_config,
    )

    assert type(final) is CrossSampleFinalReceipt
    assert final.repair_terminal is terminal
    assert final.repair_receipt is terminal.receipt
    assert final.repair_epoch == 1
    assert final.cs2_state.repair_outcome == "no_repair"
    assert final.final_population == context.population
    assert final.mode_track_final_attempt is None


def test_existing_member_terminal_and_exact_final_attempt_close_once(
    tmp_path: Path,
) -> None:
    fixture, spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    captured = []

    def execute(candidate):
        _built, result = _repair_result(fixture, coverage, candidate)
        captured.append(result)
        return result

    terminal = execute_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
        execute=execute,
    )
    candidate = terminal.candidate
    assert candidate is not None
    group = candidate.predecessor_group
    application = CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=(group,),
        successors=(group,),
        lineage=(),
        reason="unchanged_membership_no_lineage",
    )
    owners = tuple(candidate.logical_request.item.feature.owners)
    assessments_by_sample = {
        assessment.content.canonical_sample_identity_id: assessment
        for assessment in spine.assessments
    }
    _family, _model, _batches, source = bootstrap_mode_tracks_from_cid_nl_support(
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
    context = build_cross_sample_post_mt0_context(
        processing_request_id=fixture.request.processing_request_id,
        processing_owners=fixture.owners,
        population=(group,),
        application=application,
        owners=owners,
        classified_edges=(),
        anchors=(),
        injection_order={
            owner.sample_stem: index
            for index, owner in enumerate(owners, start=1)
        },
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory", "fixed-band", 0.1, 10, 10
        ),
        budget=CrossSampleNeighborhoodBudget(86, 3655),
        fit_once_source_id=source.fit_once_source_id,
    )
    post_repair = consume_observation_repair_terminal(
        context=context,
        terminal=terminal,
        repair_result=captured[0],
        alignment_config=coverage.epoch.alignment_config,
    )
    assert not isinstance(post_repair, CrossSampleFinalReceipt)
    assert post_repair.repair_terminal is terminal
    assert terminal.receipt is not None
    final_attempt = run_post_repair_mode_track_final_attempt(
        source=source,
        repair_receipt=terminal.receipt,
        post_repair=post_repair,
    )

    final = consume_mode_track_final_attempt(
        context=context,
        post_repair=post_repair,
        terminal=terminal,
        final_attempt=final_attempt,
    )

    assert final.repair_terminal is terminal
    assert final.repair_receipt is terminal.receipt
    assert final.mode_track_final_attempt is final_attempt
    assert final.mode_track_proposal is final_attempt.proposal
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="exact repair terminal object",
    ):
        consume_mode_track_final_attempt(
            context=context,
            post_repair=post_repair,
            terminal=copy(terminal),
            final_attempt=final_attempt,
        )
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="repair epoch must be an exact integer",
    ):
        replace(
            final,
            repair_epoch=True,
            _mint_token=reconciliation_owner._FINAL_RECEIPT_MINT_TOKEN,
        )
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="exact MT1 final attempt",
    ):
        replace(
            final,
            mode_track_final_attempt=None,
            _mint_token=reconciliation_owner._FINAL_RECEIPT_MINT_TOKEN,
        )


def test_unchanged_existing_member_closes_full_multicomponent_trajectory(
    tmp_path: Path,
) -> None:
    fixture, _spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    captured = []

    def execute(candidate):
        _built, result = _repair_result(fixture, coverage, candidate)
        captured.append(result)
        return result

    terminal = execute_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
        execute=execute,
    )
    candidate = terminal.candidate
    receipt = terminal.receipt
    assert candidate is not None
    assert receipt is not None
    assert receipt.evidence is not None
    repaired_group = candidate.predecessor_group
    repaired_sample_id = candidate.sample_identity.identity.sample_identity_id
    repaired_physical_id = typed_identity_digest(
        "xic.peak-observation-content:",
        canonical_identity_basis_bytes({"sample_stem": "repaired"}),
    )
    repaired_member_basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject",
            "processing_request_id": repaired_group.processing_request_id,
            "canonical_sample_identity_id": repaired_sample_id,
            "subject": {"kind": "physical_observation", "id": repaired_physical_id},
            "physical_observation_ids": [repaired_physical_id],
        }
    )
    repaired_member = CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest(
            "xic.group-member-subject:",
            repaired_member_basis,
        ),
        processing_request_id=repaired_group.processing_request_id,
        canonical_sample_identity_id=repaired_sample_id,
        subject_kind="physical_observation",
        subject_id=repaired_physical_id,
        physical_observation_ids=(repaired_physical_id,),
        basis_bytes=repaired_member_basis,
    )
    repaired_member_group = mint_cross_sample_group_identity(
        processing_request_id=repaired_group.processing_request_id,
        member_subjects=(repaired_member,),
    )
    current_group = mint_cross_sample_group_identity(
        processing_request_id=repaired_group.processing_request_id,
        member_subjects=(*repaired_group.member_subjects, repaired_member),
    )
    repaired_owner = replace(
        _owner(
            "repaired",
            "repaired",
            apex_rt=receipt.evidence.rt_apex_min,
        ),
        cross_sample_member_subject=repaired_member,
    )
    physical_id = typed_identity_digest(
        "xic.peak-observation-content:",
        canonical_identity_basis_bytes({"sample_stem": "unaffected"}),
    )
    member_basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject",
            "processing_request_id": repaired_group.processing_request_id,
            "canonical_sample_identity_id": "test-sample:unaffected",
            "subject": {"kind": "physical_observation", "id": physical_id},
            "physical_observation_ids": [physical_id],
        }
    )
    unaffected_member = CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest(
            "xic.group-member-subject:",
            member_basis,
        ),
        processing_request_id=repaired_group.processing_request_id,
        canonical_sample_identity_id="test-sample:unaffected",
        subject_kind="physical_observation",
        subject_id=physical_id,
        physical_observation_ids=(physical_id,),
        basis_bytes=member_basis,
    )
    unaffected_owner = replace(
        _owner("unaffected", "unaffected", apex_rt=18.0),
        cross_sample_member_subject=unaffected_member,
    )
    unaffected_group = mint_cross_sample_group_identity(
        processing_request_id=repaired_group.processing_request_id,
        member_subjects=(unaffected_member,),
    )
    population = tuple(
        sorted(
            (current_group, unaffected_group),
            key=lambda group: group.group_hypothesis_id,
        )
    )
    owners = tuple(
        sorted(
            (
                *candidate.logical_request.item.feature.owners,
                repaired_owner,
                unaffected_owner,
            ),
            key=lambda owner: (
                owner.cross_sample_member_subject.member_subject_id,
                owner.owner_id,
            ),
        )
    )
    predecessors = tuple(
        sorted(
            (repaired_group, repaired_member_group, unaffected_group),
            key=lambda group: group.group_hypothesis_id,
        )
    )
    application = CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=predecessors,
        successors=population,
        lineage=cross_sample_owner._expected_cross_sample_lineage(
            predecessors=predecessors,
            successors=population,
        ),
        reason="membership_changed",
    )
    classified_edges = tuple(
        classify_cross_sample_initial_neighborhood_edge(
            left_owner=owners[left_index],
            right_owner=owners[right_index],
            context="complete_link",
            config=AlignmentConfig(),
        )
        for left_index in range(len(owners))
        for right_index in range(left_index + 1, len(owners))
    )
    context = build_cross_sample_post_mt0_context(
        processing_request_id=fixture.request.processing_request_id,
        processing_owners=fixture.owners,
        population=population,
        application=application,
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
    )

    final = consume_observation_repair_terminal(
        context=context,
        terminal=terminal,
        repair_result=captured[0],
        alignment_config=coverage.epoch.alignment_config,
    )

    assert isinstance(final, CrossSampleFinalReceipt)
    assert final.cs2_state.trajectory_changed is False
    assert final.pre_trajectory_input_id == context.trajectory_input.trajectory_input_id
    assert (
        final.post_trajectory_input_id
        == context.trajectory_input.trajectory_input_id
    )
    assert final.mode_track_final_attempt is None
