"""Public contract tests for proposal-only ModeTrack refinement."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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


def test_trajectory_input_rejects_anchor_minted_for_another_group(
    tmp_path: Path,
) -> None:
    from tests.test_alignment_mode_track_assignment import _scenario
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        classify_cross_sample_initial_neighborhood_edge,
        construct_cross_sample_peak_group_hypotheses,
        validate_cross_sample_peak_group_identity,
    )
    from xic_extractor.alignment.mode_track import (
        ModeTrackEvidenceProfile,
        ModeTrackRefinementContractError,
        mint_mode_track_trajectory_input,
    )
    from xic_extractor.alignment.mode_track.assignment import (
        mint_mode_track_support_anchor,
    )

    scenario = _scenario(tmp_path)
    predecessor = scenario.track_a_spine.group
    original_anchor = scenario.track_a_spine.anchors[0]
    owners = predecessor.owners
    anchor_owner = next(
        owner
        for owner in owners
        if owner.owner_id == original_anchor.peak_hypothesis_id
    )
    alternate_group = construct_cross_sample_peak_group_hypotheses(
        (anchor_owner,),
        config=AlignmentConfig(),
    )[0]
    alternate_anchor = mint_mode_track_support_anchor(
        processing_owners=scenario.fixture.owners,
        track=alternate_group,
        sample=original_anchor.sample,
        direct_support_assessment=original_anchor.direct_support_assessment,
        peak_hypothesis=original_anchor.peak_hypothesis,
    )
    identity = validate_cross_sample_peak_group_identity(
        predecessor,
        require_canonical=True,
    )
    edges = tuple(
        classify_cross_sample_initial_neighborhood_edge(
            left_owner=owners[left_index],
            right_owner=owners[right_index],
            context="complete_link",
            config=AlignmentConfig(),
        )
        for left_index in range(len(owners))
        for right_index in range(left_index + 1, len(owners))
    )

    with pytest.raises(ModeTrackRefinementContractError, match="canonical owner"):
        mint_mode_track_trajectory_input(
            predecessors=(identity,),
            owners=owners,
            classified_edges=edges,
            anchors=(alternate_anchor,),
            injection_order={
                anchor.sample.metadata.raw_stem: anchor.injection_order
                for anchor in scenario.track_a_spine.anchors
            },
            evidence_profile=ModeTrackEvidenceProfile(
                "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
            ),
            budget=CrossSampleNeighborhoodBudget(
                max_members=86,
                max_eligible_edges=3655,
            ),
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
