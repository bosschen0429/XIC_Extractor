"""Public contract tests for proposal-only ModeTrack refinement."""

from __future__ import annotations

from dataclasses import replace

import pytest


@pytest.mark.parametrize(
    "context", ["complete_link", "full_batch_continuity", "targeted_istd_continuity"]
)
@pytest.mark.parametrize("reverse", [False, True])
def test_sparse_initial_pairs_preserve_dense_relevant_edges(context, reverse):
    import math
    from itertools import combinations

    from tests.test_alignment_owner_clustering import _DriftLookup, _owner
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.cross_sample_neighborhood_graph import (
        classify_cross_sample_initial_neighborhood_edge,
        iter_cross_sample_initial_owner_pairs,
    )

    config = AlignmentConfig()
    owners = (
        _owner("a", "base"),
        _owner("a", "same-sample"),
        _owner("b", "weak", evidence_score=45, seed_event_count=1),
        _owner("c", "drift", apex_rt=10.0),
        _owner("d", "mass", precursor_mz=900.0),
        _owner("e", "tag", neutral_loss_tag="different"),
        _owner("f", "conflict", identity_conflict=True),
        _owner("g", "product", product_mz=390.0),
        _owner("h", "loss", observed_loss=120.0),
        _owner("i", "boundary", precursor_mz=500.025),
        _owner("j", "above", precursor_mz=math.nextafter(500.025, math.inf)),
        _owner("k", "below", precursor_mz=math.nextafter(500.025, -math.inf)),
        replace(_owner("l", "physical"), primary_identity_event=None),
        replace(_owner("m", "physical"), primary_identity_event=None),
    )
    if reverse:
        owners = tuple(reversed(owners))
    drift = _DriftLookup(
        deltas={
            owner.sample_stem: (1.5 if owner.sample_stem == "c" else 0.0)
            for owner in owners
        }, orders={},
    )

    def classify(pair):
        return classify_cross_sample_initial_neighborhood_edge(
            left_owner=pair[0], right_owner=pair[1], context=context,
            config=config, drift_lookup=drift,
        )

    def relevant(edges):
        return {
            (edge.left_member_subject_id, edge.right_member_subject_id): edge
            for edge in edges if edge.disposition != "excluded"
        }

    dense = tuple(map(classify, combinations(owners, 2)))
    pairs = tuple(iter_cross_sample_initial_owner_pairs(
        owners, config=config, context=context,
    ))
    sparse = tuple(map(classify, pairs))
    assert relevant(sparse) == relevant(dense)
    assert len(pairs) < len(dense)
    assert len({(left.owner_id, right.owner_id) for left, right in pairs}) == len(pairs)


def test_disjoint_mass_population_never_classifies_cartesian_pairs(monkeypatch):
    from tests.test_alignment_owner_clustering import _owner
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment import cross_sample_neighborhood_graph as graph

    owners = tuple(
        _owner(f"s{index}", "peak", precursor_mz=100.0 + index)
        for index in range(600)
    )
    ppm_calls = 0
    original = graph._ppm

    def count_ppm(*args):
        nonlocal ppm_calls
        ppm_calls += 1
        return original(*args)

    def no_pair(*args, **kwargs):
        pytest.fail("disjoint mass windows must not visit hard-gate pairs")

    monkeypatch.setattr(graph, "_ppm", count_ppm)
    monkeypatch.setattr(graph, "_hard_gate_failure", no_pair)
    assert tuple(graph.iter_cross_sample_initial_owner_pairs(
        owners, config=AlignmentConfig(),
    )) == ()
    assert ppm_calls <= 2 * len(owners)


def test_initial_edge_owner_evaluates_pair_once(monkeypatch):
    from tests.test_alignment_owner_clustering import _owner
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment import cross_sample_neighborhood_graph as graph

    calls = 0
    original = graph.evaluate_owner_edge

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(graph, "evaluate_owner_edge", counted)
    edge = graph.classify_cross_sample_initial_neighborhood_edge(
        left_owner=_owner("a", "peak"), right_owner=_owner("b", "peak"),
        context="complete_link", config=AlignmentConfig(),
    )
    assert calls == 1
    with pytest.raises(ValueError, match="disposition contradicts"):
        replace(edge, disposition="excluded")


def test_initial_edge_reuses_only_exact_oriented_grouping_facts(monkeypatch):
    from tests.test_alignment_owner_clustering import _owner
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment import cross_sample_neighborhood_graph as graph
    from xic_extractor.alignment.cross_sample_peak_groups import (
        construct_cross_sample_peak_group_hypotheses,
    )

    config = AlignmentConfig()
    owners = (_owner("a", "peak"), _owner("b", "peak"))
    evaluated_edges = []
    edge_cache = {}
    construct_cross_sample_peak_group_hypotheses(
        owners,
        config=config,
        edge_evidence_sink=evaluated_edges,
        _initial_edge_cache=edge_cache,
    )
    assert len(evaluated_edges) == 1
    owner_by_id = {owner.owner_id: owner for owner in owners}
    cached_left = owner_by_id[evaluated_edges[0].left_owner_id]
    cached_right = owner_by_id[evaluated_edges[0].right_owner_id]
    facts = graph._retain_cross_sample_initial_pair_facts(
        owners=owners,
        config=config,
        drift_lookup=None,
        edge_cache=edge_cache,
    )
    calls = 0
    original = graph.evaluate_owner_edge

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(graph, "evaluate_owner_edge", counted)
    cached = graph.classify_cross_sample_initial_neighborhood_edge(
        left_owner=cached_left,
        right_owner=cached_right,
        context="complete_link",
        config=config,
        initial_pair_facts=facts,
    )
    assert calls == 0
    assert cached.initial_evidence is evaluated_edges[0]

    reversed_edge = graph.classify_cross_sample_initial_neighborhood_edge(
        left_owner=cached_right,
        right_owner=cached_left,
        context="complete_link",
        config=config,
        initial_pair_facts=facts,
    )
    assert calls == 1
    assert reversed_edge.initial_evidence.left_owner_id == cached_right.owner_id

    graph.classify_cross_sample_initial_neighborhood_edge(
        left_owner=cached_left,
        right_owner=cached_right,
        context="complete_link",
        config=replace(config),
        initial_pair_facts=facts,
    )
    assert calls == 2


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
            trajectory_policy_version="anchor-trajectory",
            band_policy_version="fixed-band",
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
    disconnected = mint_mode_track_trajectory_input(
        owners=owners, **{**kwargs, "classified_edges": ()},
    )
    assert disconnected.trajectory_input_id != original.trajectory_input_id
    assert disconnected.eligible_member_pairs == ()
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
            "anchor-trajectory", "fixed-band", 0.1, 10, 10
        ),
        **common,
    )
    crossed = mint_mode_track_trajectory_input(
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory", "fixed-band", 0.2, 10, 10
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
            "anchor-trajectory", "fixed-band", 0.1, 10, 10
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
            "anchor-trajectory", "fixed-band", 0.1, 10, 10
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


@pytest.mark.parametrize("omit_excluded", [False, True])
def test_accepted_partition_cannot_reopen_excluded_edge(omit_excluded: bool) -> None:
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
    if omit_excluded:
        edges = tuple(edge for edge in edges if edge.disposition != "excluded")
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
            "anchor-trajectory", "fixed-band", 0.1, 10, 10
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
