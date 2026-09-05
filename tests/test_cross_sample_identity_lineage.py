"""Focused contract tests for canonical CrossSample identity and lineage."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import pytest

from tests.cid_nl_peak_support_no_raw_fixture import cid_nl_peak
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)


def _subjects(tmp_path, *sample_stems: str):
    from xic_extractor.alignment.cross_sample_peak_groups import (
        group_member_subject_from_peak_observation,
    )

    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=sample_stems
    )
    subjects = tuple(
        group_member_subject_from_peak_observation(
            processing_owners=fixture.owners,
            sample=fixture.sample(sample_stem),
            observation=cid_nl_peak(fixture, sample_stem),
        )
        for sample_stem in sample_stems
    )
    return fixture, subjects


def _mint_group_in_process(processing_request_id, members):
    from xic_extractor.alignment.cross_sample_peak_groups import (
        mint_cross_sample_group_identity,
    )

    group = mint_cross_sample_group_identity(
        processing_request_id=processing_request_id,
        member_subjects=members,
    )
    return group.group_hypothesis_id, group.basis_bytes


def test_equal_digest_with_unequal_basis_bytes_fails_closed(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        CrossSampleIdentityBasisRegistry,
    )

    _, (member,) = _subjects(tmp_path, "sample-a")
    registry = CrossSampleIdentityBasisRegistry()
    registry.register(member.member_subject_id, member.basis_bytes)

    with pytest.raises(CrossSampleContractError, match="collision"):
        registry.register(member.member_subject_id, member.basis_bytes + b" ")


def test_group_rejects_two_identity_members_for_one_canonical_sample(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        group_member_subject_from_peak_observation,
        mint_cross_sample_group_identity,
    )

    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-a",)
    )
    first = group_member_subject_from_peak_observation(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample-a"),
        observation=cid_nl_peak(fixture, "sample-a", apex_rt=8.0),
    )
    second = group_member_subject_from_peak_observation(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample-a"),
        observation=cid_nl_peak(fixture, "sample-a", apex_rt=8.03),
    )
    assert first.member_subject_id != second.member_subject_id
    with pytest.raises(CrossSampleContractError, match="physical observation member"):
        replace(
            first,
            physical_observation_ids=second.physical_observation_ids,
        )

    with pytest.raises(CrossSampleContractError, match="canonical sample"):
        mint_cross_sample_group_identity(
            processing_request_id=fixture.request.processing_request_id,
            member_subjects=(first, second),
        )


def test_group_identity_is_order_and_parallelism_invariant(tmp_path) -> None:
    fixture, members = _subjects(tmp_path, "sample-a", "sample-b")
    request_id = fixture.request.processing_request_id

    with ProcessPoolExecutor(max_workers=2) as executor:
        receipts = tuple(
            executor.map(
                _mint_group_in_process,
                (request_id, request_id),
                (members, tuple(reversed(members))),
            )
        )

    assert receipts[0] == receipts[1]


def test_excluded_edge_cannot_connect_neighborhood(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        build_bounded_connected_neighborhood,
    )

    _, members = _subjects(tmp_path, "sample-a", "sample-b")
    neighborhood = build_bounded_connected_neighborhood(
        member_subjects=members,
        edges=(_neighborhood_edge(members[0], members[1], disposition="excluded"),),
        budget=CrossSampleNeighborhoodBudget(max_members=2, max_eligible_edges=1),
    )

    assert neighborhood.connected_components == tuple(
        (member.member_subject_id,)
        for member in sorted(members, key=lambda item: item.member_subject_id)
    )


def test_oversized_coupled_successor_applies_nothing(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        apply_coupled_successor_proposal,
        mint_cross_sample_group_identity,
    )

    fixture, members = _subjects(tmp_path, "sample-a", "sample-b", "sample-c")
    predecessors = tuple(
        mint_cross_sample_group_identity(
            processing_request_id=fixture.request.processing_request_id,
            member_subjects=(member,),
        )
        for member in members
    )
    application = apply_coupled_successor_proposal(
        predecessors=predecessors,
        successor_member_sets=(members,),
        edges=tuple(
            _neighborhood_edge(left, right, disposition="eligible")
            for left, right in zip(members, members[1:])
        ),
        budget=CrossSampleNeighborhoodBudget(max_members=2, max_eligible_edges=2),
        population=predecessors,
    )

    assert application.status == "unassessable"
    assert application.successors == ()
    assert application.lineage == ()


def test_equivalent_wrappers_share_identity_and_tagged_variant_stays_distinct(
    tmp_path,
) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        group_member_subject_from_peak_hypothesis,
        group_member_subject_from_peak_observation,
    )
    from xic_extractor.peak_detection.peak_hypothesis_identity import (
        compose_ms1_observation_peak_hypothesis,
    )

    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-a",)
    )
    observation = cid_nl_peak(fixture, "sample-a")
    hypothesis_a = compose_ms1_observation_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample-a"),
        peak_observation=observation,
    )
    hypothesis_b = compose_ms1_observation_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample-a"),
        peak_observation=observation,
    )
    wrapped_a = group_member_subject_from_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample-a"),
        hypothesis=hypothesis_a,
    )
    wrapped_b = group_member_subject_from_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample-a"),
        hypothesis=hypothesis_b,
    )
    physical = group_member_subject_from_peak_observation(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample-a"),
        observation=observation,
    )

    assert (wrapped_a.member_subject_id, wrapped_a.basis_bytes) == (
        wrapped_b.member_subject_id,
        wrapped_b.basis_bytes,
    )
    assert wrapped_a.member_subject_id != physical.member_subject_id


def test_group_and_population_reject_crossed_or_duplicate_membership(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        mint_cross_sample_group_identity,
        validate_cross_sample_group_population,
    )

    fixture, members = _subjects(tmp_path, "sample-a", "sample-b")
    other, _ = _subjects(tmp_path, "sample-c")
    with pytest.raises(CrossSampleContractError, match="crossed processing request"):
        mint_cross_sample_group_identity(
            processing_request_id=other.request.processing_request_id,
            member_subjects=(members[0],),
        )

    singleton = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(members[0],),
    )
    overlapping = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=members,
    )
    with pytest.raises(CrossSampleContractError, match="across groups"):
        validate_cross_sample_group_population((singleton, overlapping))


def test_disposition_and_value_only_projection_do_not_mint_lineage(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        CrossSamplePeakGroupHypothesis,
        apply_coupled_successor_proposal,
        mint_cross_sample_group_identity,
    )

    fixture, (member,) = _subjects(tmp_path, "sample-a")
    provisional = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(member,),
    )
    unresolved = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(member,),
        disposition="unresolved",
    )
    assert provisional.group_hypothesis_id == unresolved.group_hypothesis_id
    assert provisional.basis_bytes == unresolved.basis_bytes

    carrier = CrossSamplePeakGroupHypothesis(
        group_hypothesis_id=provisional.group_hypothesis_id,
        public_family_id="FAM000001",
        owner_ids=("owner-a",),
        event_ids=("event-a",),
        event_member_count=1,
        processing_request_id=provisional.processing_request_id,
        member_subjects=provisional.member_subjects,
        identity_basis_bytes=provisional.basis_bytes,
        disposition=provisional.disposition,
        group_center_mz=500.0,
    )
    value_changed = replace(carrier, group_center_mz=501.0)
    assert value_changed.group_hypothesis_id == carrier.group_hypothesis_id

    application = apply_coupled_successor_proposal(
        predecessors=(unresolved,),
        successor_member_sets=((member,),),
        edges=(),
        budget=CrossSampleNeighborhoodBudget(max_members=1, max_eligible_edges=0),
        population=(unresolved,),
    )
    assert application.reason == "unchanged_membership_no_lineage"
    assert application.lineage == ()


def test_membership_change_mints_deterministic_successor_lineage(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        apply_coupled_successor_proposal,
        mint_cross_sample_group_identity,
    )

    fixture, all_members = _subjects(
        tmp_path,
        "sample-a",
        "sample-b",
        "sample-c",
    )
    members = all_members[:2]
    predecessors = tuple(
        mint_cross_sample_group_identity(
            processing_request_id=fixture.request.processing_request_id,
            member_subjects=(member,),
        )
        for member in members
    )
    edge = _neighborhood_edge(members[0], members[1], disposition="eligible")
    kwargs = {
        "successor_member_sets": (members,),
        "edges": (edge,),
        "budget": CrossSampleNeighborhoodBudget(
            max_members=2,
            max_eligible_edges=1,
        ),
        "population": predecessors,
    }
    forward = apply_coupled_successor_proposal(
        predecessors=predecessors,
        **kwargs,
    )
    reversed_result = apply_coupled_successor_proposal(
        predecessors=tuple(reversed(predecessors)),
        **kwargs,
    )

    assert forward == reversed_result
    assert forward.reason == "membership_changed"
    assert len(forward.successors) == 1
    assert len(forward.lineage) == 2
    assert all(
        item.successor_group_hypothesis_id == forward.successors[0].group_hypothesis_id
        for item in forward.lineage
    )
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
    )

    with pytest.raises(CrossSampleContractError, match="atomic closure"):
        replace(forward, lineage=())
    with pytest.raises(CrossSampleContractError, match="atomic closure"):
        replace(forward, lineage=tuple(reversed(forward.lineage)))
    with pytest.raises(CrossSampleContractError, match="immutable tuples"):
        replace(forward, lineage=list(forward.lineage))  # type: ignore[arg-type]
    with pytest.raises(CrossSampleContractError, match="status is invalid"):
        replace(forward, successors=())

    with pytest.raises(CrossSampleContractError, match="overlap|basis bytes"):
        replace(
            forward.lineage[0],
            member_subject_ids=(members[0].member_subject_id,),
        )
    unrelated = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(all_members[2],),
    )
    with pytest.raises(
        CrossSampleContractError,
        match="predecessor closure|basis bytes",
    ):
        replace(
            forward.lineage[0],
            predecessor_group_hypothesis_id=unrelated.group_hypothesis_id,
            predecessor_identity_basis_bytes=unrelated.basis_bytes,
        )
    from xic_extractor.processing_identity import (
        canonical_identity_basis_bytes,
        typed_identity_digest,
    )

    impossible_basis = canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_group_v1",
            "processing_request_id": fixture.request.processing_request_id,
            "member_subject_ids": [
                members[0].member_subject_id,
                members[0].member_subject_id,
            ],
        }
    )
    impossible_id = typed_identity_digest(
        "xic.cross-sample-group.v1:",
        impossible_basis,
    )
    with pytest.raises(CrossSampleContractError, match="basis is malformed"):
        replace(
            forward.lineage[0],
            predecessor_group_hypothesis_id=impossible_id,
            predecessor_identity_basis_bytes=impossible_basis,
            predecessor_group_hypothesis_ids=(impossible_id,),
        )


def test_incomplete_coupled_successor_is_rejected_atomically(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        CrossSampleNeighborhoodBudget,
        apply_coupled_successor_proposal,
        mint_cross_sample_group_identity,
    )

    fixture, members = _subjects(tmp_path, "sample-a", "sample-b")
    predecessors = tuple(
        mint_cross_sample_group_identity(
            processing_request_id=fixture.request.processing_request_id,
            member_subjects=(member,),
        )
        for member in members
    )
    with pytest.raises(CrossSampleContractError, match="complete partition"):
        apply_coupled_successor_proposal(
            predecessors=predecessors,
            successor_member_sets=((members[0],),),
            edges=(_neighborhood_edge(members[0], members[1], disposition="eligible"),),
            budget=CrossSampleNeighborhoodBudget(
                max_members=2,
                max_eligible_edges=1,
            ),
            population=predecessors,
        )
    assert tuple(group.member_subjects for group in predecessors) == tuple(
        (member,) for member in members
    )
    with pytest.raises(CrossSampleContractError, match="current population subset"):
        apply_coupled_successor_proposal(
            predecessors=predecessors,
            successor_member_sets=(members,),
            edges=(_neighborhood_edge(members[0], members[1], disposition="eligible"),),
            budget=CrossSampleNeighborhoodBudget(
                max_members=2,
                max_eligible_edges=1,
            ),
            population=(predecessors[0],),
        )


def test_successor_application_rejects_mutable_closure_containers(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        CrossSampleSuccessorApplication,
        mint_cross_sample_group_identity,
    )

    fixture, (member,) = _subjects(tmp_path, "sample-a")
    predecessor = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(member,),
    )
    with pytest.raises(CrossSampleContractError, match="immutable tuples"):
        CrossSampleSuccessorApplication(
            status="unassessable",
            predecessors=[predecessor],  # type: ignore[arg-type]
            successors=[],  # type: ignore[arg-type]
            lineage=[],  # type: ignore[arg-type]
            reason="eligible_neighborhood_exceeds_budget",
        )


def test_caller_cannot_reopen_excluded_neighborhood_edge(tmp_path) -> None:
    from dataclasses import replace as dc_replace
    from types import SimpleNamespace

    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        CrossSampleNeighborhoodBudget,
        build_bounded_connected_neighborhood,
    )

    _, members = _subjects(tmp_path, "sample-a", "sample-b")
    excluded = _neighborhood_edge(members[0], members[1], disposition="excluded")
    with pytest.raises(CrossSampleContractError, match="contradicts"):
        dc_replace(
            excluded,
            disposition="eligible",
        )
    with pytest.raises(CrossSampleContractError, match="owner-member pair"):
        dc_replace(excluded, left_owner=excluded.right_owner)
    with pytest.raises(CrossSampleContractError, match="exact CrossSample"):
        build_bounded_connected_neighborhood(
            member_subjects=members,
            edges=(
                SimpleNamespace(
                    left_member_subject_id=members[0].member_subject_id,
                    right_member_subject_id=members[1].member_subject_id,
                    disposition="eligible",
                ),
            ),
            budget=CrossSampleNeighborhoodBudget(
                max_members=2,
                max_eligible_edges=1,
            ),
        )
    eligible = _neighborhood_edge(members[0], members[1], disposition="eligible")
    with pytest.raises(CrossSampleContractError, match="repeats an undirected"):
        build_bounded_connected_neighborhood(
            member_subjects=members,
            edges=(excluded, eligible),
            budget=CrossSampleNeighborhoodBudget(
                max_members=2,
                max_eligible_edges=1,
            ),
        )


def test_initial_edge_classifier_is_closed_and_preserves_existing_policy() -> None:
    from dataclasses import replace as dc_replace

    from tests.alignment_pipeline_helpers import owner_edge_evidence
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        classify_cross_sample_initial_edge,
    )

    strong = owner_edge_evidence()
    blocked = dc_replace(
        strong,
        decision="blocked_edge",
        failure_reason="same_sample",
    )
    weak = dc_replace(strong, decision="weak_edge")

    assert (
        classify_cross_sample_initial_edge(
            strong,
            context="complete_link",
            config=AlignmentConfig(),
        )
        == "eligible"
    )
    assert (
        classify_cross_sample_initial_edge(
            weak,
            context="complete_link",
            config=AlignmentConfig(),
        )
        == "review_only"
    )
    assert (
        classify_cross_sample_initial_edge(
            blocked,
            context="hard_gate",
            config=AlignmentConfig(),
        )
        == "excluded"
    )


def _neighborhood_edge(left, right, *, disposition):
    from tests.test_alignment_owner_clustering import _owner
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        classify_cross_sample_initial_neighborhood_edge,
    )

    left_owner = replace(
        _owner(f"edge-{left.member_subject_id[-8:]}", "000001"),
        cross_sample_member_subject=left,
    )
    right_owner = replace(
        _owner(f"edge-{right.member_subject_id[-8:]}", "000001"),
        cross_sample_member_subject=right,
    )
    if disposition == "excluded":
        right_owner = replace(right_owner, identity_conflict=True)
    elif disposition != "eligible":
        raise AssertionError(f"unsupported test disposition {disposition!r}")
    return classify_cross_sample_initial_neighborhood_edge(
        left_owner=left_owner,
        right_owner=right_owner,
        context="hard_gate",
        config=AlignmentConfig(),
    )


def test_neighborhood_edge_rejects_relabelled_owner_evidence(tmp_path) -> None:
    fixture, members = _subjects(tmp_path, "sample-a", "sample-b")
    del fixture
    edge = _neighborhood_edge(members[0], members[1], disposition="eligible")
    forged = replace(
        edge.initial_evidence,
        decision="blocked_edge",
        failure_reason="same_sample",
    )

    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
    )

    with pytest.raises(CrossSampleContractError, match="exact owner evidence"):
        replace(edge, initial_evidence=forged, disposition="excluded")
