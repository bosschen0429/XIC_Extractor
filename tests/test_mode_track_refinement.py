"""Public contract tests for proposal-only ModeTrack refinement."""

from __future__ import annotations

from dataclasses import replace

import pytest


def test_final_attempt_public_operation_has_no_caller_authority_inputs() -> None:
    from inspect import signature

    from xic_extractor.alignment.mode_track import (
        run_post_repair_mode_track_final_attempt,
    )

    assert tuple(signature(run_post_repair_mode_track_final_attempt).parameters) == (
        "source",
        "repair_receipt",
        "post_repair",
    )
    with pytest.raises(TypeError):
        run_post_repair_mode_track_final_attempt(
            source=None,  # type: ignore[arg-type]
            repair_receipt=None,  # type: ignore[arg-type]
            post_repair=None,  # type: ignore[arg-type]
            successor_member_subject_ids=(),  # type: ignore[call-arg]
        )


def test_final_attempt_result_variants_reject_caller_construction() -> None:
    from xic_extractor.alignment.mode_track import (
        AssignedModeTrackFinalAttempt,
        AssignmentUnavailableModeTrackFinalAttempt,
        ModeTrackFinalAttemptContractError,
        RejectedModeTrackFinalAttempt,
        UnassessableModeTrackFinalAttempt,
        UntrainedModeTrackFinalAttempt,
    )

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
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_alignment_owner_clustering import _owner
    from tests.test_cell_observations import _repaired_handoff
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
    )
    from xic_extractor.alignment.cross_sample_reconciliation import (
        CrossSamplePostRepairModeTrackInput,
        build_cross_sample_post_mt0_context,
        reconcile_observation_repair,
    )
    from xic_extractor.alignment.mode_track import (
        run_post_repair_mode_track_final_attempt,
    )
    from xic_extractor.alignment.mode_track.assignment import (
        ModeTrackRepairAssignmentObservation,
        ModeTrackTrajectoryPolicy,
    )
    from xic_extractor.alignment.mode_track.contracts import ModeTrackEvidenceProfile
    from xic_extractor.alignment.mode_track.producer import (
        bootstrap_mode_tracks_from_cid_nl_support,
    )

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

    import xic_extractor.alignment.mode_track.refinement as refinement_owner
    from xic_extractor.alignment.mode_track import (
        AssignmentUnavailableModeTrackFinalAttempt,
    )
    from xic_extractor.alignment.mode_track.assignment import (
        _ModeTrackAssignmentUnavailableError,
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


def _application_context(*, predecessors, edges, budget):
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleCoupledProposalContext,
    )

    return CrossSampleCoupledProposalContext(
        predecessors=tuple(
            sorted(predecessors, key=lambda group: group.group_hypothesis_id)
        ),
        classified_edges=tuple(edges),
        budget=budget,
    )


def test_trajectory_input_identity_ignores_value_only_owner_changes() -> None:
    from dataclasses import fields

    from tests.test_alignment_owner_clustering import _owner
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        classify_cross_sample_initial_neighborhood_edge,
        mint_cross_sample_group_identity,
    )
    from xic_extractor.alignment.mode_track import (
        ModeTrackEvidenceProfile,
        ModeTrackRefinementContractError,
        mint_mode_track_trajectory_input,
    )

    owners = (_owner("sample-a", "000001"), _owner("sample-b", "000001"))
    members = tuple(owner.cross_sample_member_subject for owner in owners)
    predecessors = tuple(
        mint_cross_sample_group_identity(
            processing_request_id=member.processing_request_id,
            member_subjects=(member,),
        )
        for member in members
    )
    edges = (
        classify_cross_sample_initial_neighborhood_edge(
            left_owner=owners[0],
            right_owner=owners[1],
            context="hard_gate",
            config=AlignmentConfig(),
        ),
    )
    kwargs = {
        "predecessors": predecessors,
        "classified_edges": edges,
        "anchors": (),
        "injection_order": {"sample-a": 1, "sample-b": 2},
        "evidence_profile": ModeTrackEvidenceProfile(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
        "budget": CrossSampleNeighborhoodBudget(
            max_members=86,
            max_eligible_edges=3655,
        ),
    }

    original = mint_mode_track_trajectory_input(owners=owners, **kwargs)
    value_changed = mint_mode_track_trajectory_input(
        owners=tuple(
            replace(
                owner,
                owner_area=owner.owner_area + 500.0,
                owner_peak_start_rt=owner.owner_peak_start_rt - 0.02,
                owner_peak_end_rt=owner.owner_peak_end_rt + 0.02,
            )
            for owner in owners
        ),
        **kwargs,
    )
    context_changed = mint_mode_track_trajectory_input(
        owners=owners,
        **{
            **kwargs,
            "classified_edges": (
                classify_cross_sample_initial_neighborhood_edge(
                    left_owner=owners[0],
                    right_owner=owners[1],
                    context="complete_link",
                    config=AlignmentConfig(),
                ),
            ),
        },
    )
    assert value_changed.trajectory_input_id == original.trajectory_input_id
    assert context_changed.trajectory_input_id == original.trajectory_input_id
    assert {
        field.name for field in fields(type(original))
    }.isdisjoint({"predecessors", "classified_edges", "budget"})
    with pytest.raises(ModeTrackRefinementContractError, match="exact owner"):
        mint_mode_track_trajectory_input(
            owners=owners,
            **{
                **kwargs,
                "anchors": ("anchor:forged",),
            },
        )
    with pytest.raises(ModeTrackRefinementContractError, match="must be minted"):
        replace(
            original,
            trajectory_input_id="trajectory:forged",
            _mint_token=object(),
        )


def test_accepted_proposal_bound_to_another_snapshot_fails_closed() -> None:
    from tests.test_alignment_owner_clustering import _owner
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        classify_cross_sample_initial_neighborhood_edge,
        mint_cross_sample_group_identity,
    )
    from xic_extractor.alignment.mode_track import (
        ModeTrackEvidenceProfile,
        ModeTrackRefinementContractError,
        apply_accepted_mode_track_refinement,
        mint_mode_track_trajectory_input,
    )
    from xic_extractor.alignment.mode_track.contracts import (
        _mint_accepted_mode_track_refinement,
    )

    owners = (_owner("sample-a", "000001"), _owner("sample-b", "000001"))
    members = tuple(owner.cross_sample_member_subject for owner in owners)
    predecessors = tuple(
        mint_cross_sample_group_identity(
            processing_request_id=member.processing_request_id,
            member_subjects=(member,),
        )
        for member in members
    )
    edge = classify_cross_sample_initial_neighborhood_edge(
        left_owner=owners[0],
        right_owner=owners[1],
        context="hard_gate",
        config=AlignmentConfig(),
    )
    common = {
        "predecessors": predecessors,
        "owners": owners,
        "classified_edges": (edge,),
        "anchors": (),
        "injection_order": {"sample-a": 1, "sample-b": 2},
        "budget": CrossSampleNeighborhoodBudget(
            max_members=86,
            max_eligible_edges=3655,
        ),
    }
    original = mint_mode_track_trajectory_input(
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
        ),
        **common,
    )
    crossed = mint_mode_track_trajectory_input(
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v2", "fixed-band-v1", 0.1, 10, 10
        ),
        **common,
    )
    proposal = _mint_accepted_mode_track_refinement(
        trajectory_input=original,
        successor_member_subject_ids=(
            tuple(member.member_subject_id for member in members),
        ),
    )

    with pytest.raises(ModeTrackRefinementContractError, match="another snapshot"):
        apply_accepted_mode_track_refinement(
            trajectory_input=crossed,
            proposal=proposal,
            application_context=_application_context(
                predecessors=predecessors,
                edges=(edge,),
                budget=common["budget"],
            ),
            population=predecessors,
        )


def test_proposal_variants_are_closed_and_immutable() -> None:
    from dataclasses import FrozenInstanceError

    from xic_extractor.alignment.mode_track.contracts import (
        AcceptedModeTrackRefinement,
        InsufficientModeTrackTraining,
        ModeTrackRefinementContractError,
        RejectedModeTrackRefinement,
        UnassessableModeTrackRefinement,
        _mint_insufficient_mode_track_training,
        _mint_rejected_mode_track_refinement,
        _mint_unassessable_mode_track_refinement,
    )

    rejected = _mint_rejected_mode_track_refinement(
        trajectory_input_id="trajectory:test",
        reason_codes=("ambiguous_assignment",),
    )
    insufficient = _mint_insufficient_mode_track_training(
        trajectory_input_id="trajectory:test",
        reason_codes=("no_direct_support_anchor",),
    )
    unassessable = _mint_unassessable_mode_track_refinement(
        trajectory_input_id="trajectory:test",
    )

    assert {rejected.variant, insufficient.variant, unassessable.variant} == {
        "rejected_refinement",
        "insufficient_training",
        "unassessable",
    }
    with pytest.raises(FrozenInstanceError):
        rejected.trajectory_input_id = "trajectory:mutated"
    forged_variants = (
        (
            AcceptedModeTrackRefinement,
            {
                "variant": "accepted_refinement",
                "trajectory_input_id": "trajectory:forged",
                "successor_member_subject_ids": (("member:forged",),),
            },
        ),
        (
            RejectedModeTrackRefinement,
            {
                "variant": "rejected_refinement",
                "trajectory_input_id": "trajectory:forged",
                "reason_codes": ("forged",),
            },
        ),
        (
            InsufficientModeTrackTraining,
            {
                "variant": "insufficient_training",
                "trajectory_input_id": "trajectory:forged",
                "reason_codes": ("forged",),
            },
        ),
        (
            UnassessableModeTrackRefinement,
            {
                "variant": "unassessable",
                "trajectory_input_id": "trajectory:forged",
                "reason": "neighborhood_too_large",
            },
        ),
    )
    for proposal_type, kwargs in forged_variants:
        with pytest.raises((TypeError, ModeTrackRefinementContractError)):
            proposal_type(**kwargs)
    with pytest.raises(TypeError):
        RejectedModeTrackRefinement(
            variant="rejected_refinement",
            trajectory_input_id="trajectory:test",
            reason_codes=("ambiguous_assignment",),
            unknown_field=True,
        )


def test_untrained_evidence_is_owner_minted_and_immutable() -> None:
    from dataclasses import FrozenInstanceError

    from xic_extractor.alignment.mode_track.contracts import (
        ModeTrackEvidenceProfile,
        ModeTrackRefinementContractError,
        UntrainedModeTrackRefinementEvidence,
        _mint_untrained_mode_track_refinement_evidence,
    )

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


def test_oversized_neighborhood_is_unassessable_without_subset_processing() -> None:
    from tests.test_alignment_owner_clustering import _owner
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        classify_cross_sample_initial_neighborhood_edge,
        mint_cross_sample_group_identity,
    )
    from xic_extractor.alignment.mode_track import (
        ModeTrackEvidenceProfile,
        ModeTrackRefinementContractError,
        UnassessableModeTrackRefinement,
        mint_mode_track_trajectory_input,
    )
    from xic_extractor.alignment.mode_track.contracts import (
        _mint_unassessable_mode_track_refinement,
    )

    owners = tuple(_owner(f"sample-{index}", "000001") for index in range(3))
    members = tuple(owner.cross_sample_member_subject for owner in owners)
    predecessors = tuple(
        mint_cross_sample_group_identity(
            processing_request_id=member.processing_request_id,
            member_subjects=(member,),
        )
        for member in members
    )
    edges = tuple(
        classify_cross_sample_initial_neighborhood_edge(
            left_owner=owners[left_index],
            right_owner=owners[right_index],
            context="hard_gate",
            config=AlignmentConfig(),
        )
        for left_index in range(len(owners))
        for right_index in range(left_index + 1, len(owners))
    )
    trajectory_input = mint_mode_track_trajectory_input(
        predecessors=predecessors,
        owners=owners,
        classified_edges=edges,
        anchors=(),
        injection_order={f"sample-{index}": index + 1 for index in range(3)},
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
        ),
        budget=CrossSampleNeighborhoodBudget(
            max_members=2,
            max_eligible_edges=2,
        ),
    )

    proposal = _mint_unassessable_mode_track_refinement(
        trajectory_input_id=trajectory_input.trajectory_input_id,
    )

    assert type(proposal) is UnassessableModeTrackRefinement
    assert proposal.reason == "neighborhood_too_large"
    with pytest.raises(
        ModeTrackRefinementContractError,
        match="must be minted",
    ):
        replace(
            trajectory_input,
            neighborhood_status="assessable",
            _mint_token=object(),
        )


def test_successor_partition_is_complete_and_order_independent() -> None:
    from tests.test_alignment_owner_clustering import _owner
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        classify_cross_sample_initial_neighborhood_edge,
        mint_cross_sample_group_identity,
    )
    from xic_extractor.alignment.mode_track import (
        ModeTrackEvidenceProfile,
        ModeTrackRefinementContractError,
        apply_accepted_mode_track_refinement,
        mint_mode_track_trajectory_input,
    )
    from xic_extractor.alignment.mode_track.contracts import (
        _mint_accepted_mode_track_refinement,
    )

    owners = (_owner("sample-a", "000001"), _owner("sample-b", "000001"))
    members = tuple(owner.cross_sample_member_subject for owner in owners)
    predecessors = tuple(
        mint_cross_sample_group_identity(
            processing_request_id=member.processing_request_id,
            member_subjects=(member,),
        )
        for member in members
    )
    edge = classify_cross_sample_initial_neighborhood_edge(
        left_owner=owners[0],
        right_owner=owners[1],
        context="hard_gate",
        config=AlignmentConfig(),
    )
    budget = CrossSampleNeighborhoodBudget(86, 3655)
    trajectory_input = mint_mode_track_trajectory_input(
        predecessors=predecessors,
        owners=owners,
        classified_edges=(edge,),
        anchors=(),
        injection_order={"sample-a": 1, "sample-b": 2},
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
        ),
        budget=budget,
    )
    member_ids = tuple(member.member_subject_id for member in members)

    forward = _mint_accepted_mode_track_refinement(
        trajectory_input=trajectory_input,
        successor_member_subject_ids=(member_ids,),
    )
    reversed_proposal = _mint_accepted_mode_track_refinement(
        trajectory_input=trajectory_input,
        successor_member_subject_ids=(tuple(reversed(member_ids)),),
    )
    assert apply_accepted_mode_track_refinement(
        trajectory_input=trajectory_input,
        proposal=forward,
        application_context=_application_context(
            predecessors=predecessors,
            edges=(edge,),
            budget=budget,
        ),
        population=predecessors,
    ) == apply_accepted_mode_track_refinement(
        trajectory_input=trajectory_input,
        proposal=reversed_proposal,
        application_context=_application_context(
            predecessors=predecessors,
            edges=(edge,),
            budget=budget,
        ),
        population=tuple(reversed(predecessors)),
    )
    with pytest.raises(ModeTrackRefinementContractError, match="complete"):
        _mint_accepted_mode_track_refinement(
            trajectory_input=trajectory_input,
            successor_member_subject_ids=((member_ids[0],),),
        )


def test_accepted_partition_cannot_reopen_excluded_edge() -> None:
    from tests.test_alignment_owner_clustering import _owner
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        CrossSampleNeighborhoodBudget,
        classify_cross_sample_initial_neighborhood_edge,
        mint_cross_sample_group_identity,
    )
    from xic_extractor.alignment.mode_track import (
        ModeTrackEvidenceProfile,
        apply_accepted_mode_track_refinement,
        mint_mode_track_trajectory_input,
    )
    from xic_extractor.alignment.mode_track.contracts import (
        _mint_accepted_mode_track_refinement,
    )

    owners = (
        _owner("sample-a", "000001", precursor_mz=500.0),
        _owner("sample-b", "000001", precursor_mz=500.0175),
        _owner("sample-c", "000001", precursor_mz=500.035),
    )
    config = AlignmentConfig(preferred_ppm=40.0)
    members = tuple(owner.cross_sample_member_subject for owner in owners)
    predecessors = tuple(
        mint_cross_sample_group_identity(
            processing_request_id=member.processing_request_id,
            member_subjects=(member,),
        )
        for member in members
    )
    edges = tuple(
        classify_cross_sample_initial_neighborhood_edge(
            left_owner=owners[left_index],
            right_owner=owners[right_index],
            context="complete_link",
            config=config,
        )
        for left_index in range(len(owners))
        for right_index in range(left_index + 1, len(owners))
    )
    assert [edge.disposition for edge in edges] == [
        "eligible",
        "excluded",
        "eligible",
    ]
    trajectory_input = mint_mode_track_trajectory_input(
        predecessors=predecessors,
        owners=owners,
        classified_edges=edges,
        anchors=(),
        injection_order={
            f"sample-{letter}": index + 1
            for index, letter in enumerate("abc")
        },
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
        ),
        budget=CrossSampleNeighborhoodBudget(86, 3655),
    )
    member_ids = tuple(member.member_subject_id for member in members)
    proposal = _mint_accepted_mode_track_refinement(
        trajectory_input=trajectory_input,
        successor_member_subject_ids=((member_ids[0], member_ids[2]), (member_ids[1],)),
    )

    with pytest.raises(CrossSampleContractError, match="reopens an excluded"):
        apply_accepted_mode_track_refinement(
            trajectory_input=trajectory_input,
            proposal=proposal,
            application_context=_application_context(
                predecessors=predecessors,
                edges=edges,
                budget=CrossSampleNeighborhoodBudget(86, 3655),
            ),
            population=predecessors,
        )


def test_unchanged_cross_sample_application_preserves_full_group_receipt() -> None:
    from tests.test_alignment_owner_clustering import _owner
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        apply_coupled_successor_proposal,
        mint_cross_sample_group_identity,
    )

    owner = _owner("sample-a", "000001")
    member = owner.cross_sample_member_subject
    predecessor = mint_cross_sample_group_identity(
        processing_request_id=member.processing_request_id,
        member_subjects=(member,),
        disposition="unresolved",
    )

    application = apply_coupled_successor_proposal(
        predecessors=(predecessor,),
        successor_member_sets=((member,),),
        edges=(),
        budget=CrossSampleNeighborhoodBudget(86, 3655),
        population=(predecessor,),
    )

    assert application.predecessors == (predecessor,)
    assert application.successors == (predecessor,)
    assert application.successors[0].disposition == "unresolved"
    assert application.lineage == ()
    assert application.reason == "unchanged_membership_no_lineage"
