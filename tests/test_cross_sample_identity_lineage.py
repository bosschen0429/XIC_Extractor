"""Focused contract tests for canonical CrossSample identity and lineage."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import pytest

from tests.issue161_no_raw_fixture import issue161_peak
from tests.package2_no_raw_fixture import build_package2_no_raw_fixture


def _subjects(tmp_path, *sample_stems: str):
    from xic_extractor.alignment.cross_sample_peak_groups import (
        group_member_subject_from_peak_observation,
    )

    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=sample_stems)
    subjects = tuple(
        group_member_subject_from_peak_observation(
            processing_owners=fixture.owners,
            sample=fixture.sample(sample_stem),
            observation=issue161_peak(fixture, sample_stem),
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

    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("sample-a",))
    first = group_member_subject_from_peak_observation(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample-a"),
        observation=issue161_peak(fixture, "sample-a", apex_rt=8.0),
    )
    second = group_member_subject_from_peak_observation(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample-a"),
        observation=issue161_peak(fixture, "sample-a", apex_rt=8.03),
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
        CrossSampleNeighborhoodEdge,
        build_bounded_connected_neighborhood,
    )

    _, members = _subjects(tmp_path, "sample-a", "sample-b")
    neighborhood = build_bounded_connected_neighborhood(
        member_subjects=members,
        edges=(
            CrossSampleNeighborhoodEdge(
                left_member_subject_id=members[0].member_subject_id,
                right_member_subject_id=members[1].member_subject_id,
                disposition="excluded",
            ),
        ),
        budget=CrossSampleNeighborhoodBudget(max_members=2, max_eligible_edges=1),
    )

    assert neighborhood.connected_components == tuple(
        (member.member_subject_id,)
        for member in sorted(members, key=lambda item: item.member_subject_id)
    )


def test_oversized_coupled_successor_applies_nothing(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        CrossSampleNeighborhoodEdge,
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
            CrossSampleNeighborhoodEdge(
                left_member_subject_id=left.member_subject_id,
                right_member_subject_id=right.member_subject_id,
                disposition="eligible",
            )
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

    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("sample-a",))
    observation = issue161_peak(fixture, "sample-a")
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
    assert value_changed.lineage == carrier.lineage == ()

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
        CrossSampleNeighborhoodEdge,
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
    edge = CrossSampleNeighborhoodEdge(
        left_member_subject_id=members[0].member_subject_id,
        right_member_subject_id=members[1].member_subject_id,
        disposition="eligible",
    )
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
        item.successor_group_hypothesis_id
        == forward.successors[0].group_hypothesis_id
        for item in forward.lineage
    )


def test_incomplete_coupled_successor_is_rejected_atomically(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        CrossSampleNeighborhoodBudget,
        CrossSampleNeighborhoodEdge,
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
            edges=(
                CrossSampleNeighborhoodEdge(
                    left_member_subject_id=members[0].member_subject_id,
                    right_member_subject_id=members[1].member_subject_id,
                    disposition="eligible",
                ),
            ),
            budget=CrossSampleNeighborhoodBudget(
                max_members=2,
                max_eligible_edges=1,
            ),
            population=predecessors,
        )
    assert tuple(group.member_subjects for group in predecessors) == tuple(
        (member,) for member in members
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

    assert classify_cross_sample_initial_edge(
        strong,
        context="complete_link",
        config=AlignmentConfig(),
    ) == "eligible"
    assert classify_cross_sample_initial_edge(
        weak,
        context="complete_link",
        config=AlignmentConfig(),
    ) == "review_only"
    assert classify_cross_sample_initial_edge(
        blocked,
        context="hard_gate",
        config=AlignmentConfig(),
    ) == "excluded"
