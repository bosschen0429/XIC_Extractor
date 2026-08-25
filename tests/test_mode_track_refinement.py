"""Public contract tests for proposal-only ModeTrack refinement."""

from __future__ import annotations

from dataclasses import replace

import pytest


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
        mint_accepted_mode_track_refinement,
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
    proposal = mint_accepted_mode_track_refinement(
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

    from xic_extractor.alignment.mode_track import (
        InsufficientModeTrackTraining,
        RejectedModeTrackRefinement,
        UnassessableModeTrackRefinement,
    )

    rejected = RejectedModeTrackRefinement(
        variant="rejected_refinement",
        trajectory_input_id="trajectory:test",
        reason_codes=("ambiguous_assignment",),
    )
    insufficient = InsufficientModeTrackTraining(
        variant="insufficient_training",
        trajectory_input_id="trajectory:test",
        reason_codes=("no_direct_support_anchor",),
    )
    unassessable = UnassessableModeTrackRefinement(
        variant="unassessable",
        trajectory_input_id="trajectory:test",
        reason="neighborhood_too_large",
    )

    assert {rejected.variant, insufficient.variant, unassessable.variant} == {
        "rejected_refinement",
        "insufficient_training",
        "unassessable",
    }
    with pytest.raises(FrozenInstanceError):
        rejected.trajectory_input_id = "trajectory:mutated"
    with pytest.raises(TypeError):
        RejectedModeTrackRefinement(
            variant="rejected_refinement",
            trajectory_input_id="trajectory:test",
            reason_codes=("ambiguous_assignment",),
            unknown_field=True,
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
        propose_mode_track_successor_partition,
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

    proposal = propose_mode_track_successor_partition(
        trajectory_input=trajectory_input,
        successor_member_subject_ids=(
            tuple(member.member_subject_id for member in members[:2]),
        ),
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


def test_cid_nl_workflow_seam_applies_accepted_components_through_cross_sample(
    tmp_path,
) -> None:
    from tests.package2_no_raw_fixture import build_package2_no_raw_fixture
    from tests.test_alignment_mode_track_assignment import (
        _different_request_same_sources,
    )
    from tests.test_alignment_multi_hypothesis_support import (
        _assessment_with_observation_only_peak,
    )
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.mode_track import (
        InsufficientModeTrackTraining,
        ModeTrackRefinementContractError,
        ModeTrackTrajectoryPolicy,
        bootstrap_mode_track_refinement_from_cid_nl_support,
        mint_mode_track_trajectory_input,
    )
    from xic_extractor.extraction.untargeted_cid_nl_mode_track_workflow import (
        _apply_mode_track_refinement_transactions,
        _workflow_call_cost,
    )

    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("early",))
    assessment = _assessment_with_observation_only_peak(
        fixture,
        sample_stem="early",
        direct_apex_rt=7.80,
        observed_apex_rt=8.40,
        scan_number=201,
    )

    result = bootstrap_mode_track_refinement_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=((fixture.sample("early"), assessment),),
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
    )

    applications = _apply_mode_track_refinement_transactions(
        result.transactions
    )
    assert applications
    assert all(
        application.reason == "unchanged_membership_no_lineage"
        for application in applications
    )
    assert all(
        tuple(
            group.group_hypothesis_id for group in application.predecessors
        )
        == tuple(group.group_hypothesis_id for group in application.successors)
        and application.lineage == ()
        for application in applications
    )
    assert _workflow_call_cost(
        discovery_raw_open_count=1,
        m7_call_cost={
            "raw_open_count": 1,
            "xic_batch_count": 1,
            "logical_xic_request_count": 2,
            "adapter_chromatogram_call_count": 1,
            "trace_point_count": 3,
        },
    ) == {
        "discovery_raw_open_count": 1,
        "m7_raw_open_count": 1,
        "total_raw_open_count": 2,
        "m7_xic_batch_count": 1,
        "m7_logical_xic_request_count": 2,
        "m7_adapter_chromatogram_call_count": 1,
        "m7_trace_point_count": 3,
    }
    assert any(
        type(transaction.proposal) is InsufficientModeTrackTraining
        for transaction in result.transactions
    )
    assert {
        anchor_id
        for transaction in result.transactions
        for anchor_id in transaction.trajectory_input.anchor_ids
    } == {
        anchor.anchor_id
        for track in result.model_set.tracks
        for anchor in track.anchors
    }

    crossed_owners = _different_request_same_sources(fixture)
    crossed_fixture = replace(
        fixture,
        request=crossed_owners.request,
        attempt=crossed_owners.attempt,
        owners=crossed_owners,
    )
    crossed_assessment = _assessment_with_observation_only_peak(
        crossed_fixture,
        sample_stem="early",
        direct_apex_rt=7.80,
        observed_apex_rt=8.40,
        scan_number=201,
    )
    crossed_result = bootstrap_mode_track_refinement_from_cid_nl_support(
        processing_owners=crossed_fixture.owners,
        sample_assessments=(
            (crossed_fixture.sample("early"), crossed_assessment),
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
    anchored_transaction = next(
        transaction
        for transaction in result.transactions
        if transaction.trajectory_input.anchor_ids
    )
    foreign_anchor = next(
        anchor
        for track in crossed_result.model_set.tracks
        for anchor in track.anchors
    )
    assert (
        foreign_anchor.processing_request_id
        != anchored_transaction.trajectory_input.processing_request_id
    )
    predecessor_ids = {
        group.group_hypothesis_id
        for group in anchored_transaction.application_context.predecessors
    }
    component_owners = tuple(
        owner
        for group in result.family_context.mode_tracks
        if group.group_hypothesis_id in predecessor_ids
        for owner in group.owners
    )
    with pytest.raises(
        ModeTrackRefinementContractError,
        match="crossed its request",
    ):
        mint_mode_track_trajectory_input(
            predecessors=anchored_transaction.application_context.predecessors,
            owners=component_owners,
            classified_edges=(
                anchored_transaction.application_context.classified_edges
            ),
            anchors=(foreign_anchor,),
            injection_order=dict(
                anchored_transaction.trajectory_input.injection_order
            ),
            evidence_profile=anchored_transaction.trajectory_input.evidence_profile,
            budget=anchored_transaction.application_context.budget,
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
        mint_accepted_mode_track_refinement,
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

    forward = mint_accepted_mode_track_refinement(
        trajectory_input=trajectory_input,
        successor_member_subject_ids=(member_ids,),
    )
    reversed_proposal = mint_accepted_mode_track_refinement(
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
        mint_accepted_mode_track_refinement(
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
        mint_accepted_mode_track_refinement,
        mint_mode_track_trajectory_input,
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
    proposal = mint_accepted_mode_track_refinement(
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
