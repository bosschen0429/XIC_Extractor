"""Contract tests for the CrossSample canonical identity owner."""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import pytest

from tests.issue161_no_raw_fixture import issue161_peak
from tests.package2_no_raw_fixture import build_package2_no_raw_fixture


def _subjects(tmp_path):
    from xic_extractor.alignment.cross_sample_peak_groups import (
        group_member_subject_from_peak_observation,
    )

    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
    )
    first = group_member_subject_from_peak_observation(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample-a"),
        observation=issue161_peak(fixture, "sample-a"),
    )
    second = group_member_subject_from_peak_observation(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample-b"),
        observation=issue161_peak(fixture, "sample-b"),
    )
    return fixture, first, second


def _assessable_neighborhood(*members):
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        build_bounded_connected_neighborhood,
    )
    return build_bounded_connected_neighborhood(
        member_subjects=members,
        edges=(),
        budget=CrossSampleNeighborhoodBudget(
            max_members=len(members),
            max_eligible_edges=0,
        ),
    )


def _mint_group_receipt_in_fresh_process(processing_request_id, members):
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

    _, first, _ = _subjects(tmp_path)
    registry = CrossSampleIdentityBasisRegistry()
    registry.register(first.member_subject_id, first.basis_bytes)

    with pytest.raises(CrossSampleContractError, match="collision"):
        registry.register(first.member_subject_id, first.basis_bytes + b" ")


def test_member_basis_retains_exact_processing_request_id(tmp_path) -> None:
    fixture, first, _ = _subjects(tmp_path)

    assert json.loads(first.basis_bytes) == {
        "canonical_sample_identity_id": first.canonical_sample_identity_id,
        "processing_request_id": fixture.request.processing_request_id,
        "schema_version": "group_member_subject_v1",
        "subject": {"id": first.subject_id, "kind": first.subject_kind},
    }


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


def test_population_rejects_cross_group_duplicate_member_at_production_seam(
    tmp_path,
) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        mint_cross_sample_group_identity,
        validate_cross_sample_group_population,
    )

    fixture, first, second = _subjects(tmp_path)
    singleton = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(first,),
    )
    overlapping = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(first, second),
    )

    with pytest.raises(CrossSampleContractError, match="canonical member"):
        validate_cross_sample_group_population((singleton, overlapping))


def test_group_identity_and_lineage_are_order_parallel_retry_invariant(
    tmp_path,
) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleSuccessorProposal,
        apply_coupled_successor_proposal,
        mint_cross_sample_group_identity,
    )

    fixture, first, second = _subjects(tmp_path)
    original = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(first, second),
    )
    retried = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(second, first),
    )
    assert retried == original

    proposal = CrossSampleSuccessorProposal(
        processing_request_id=fixture.request.processing_request_id,
        predecessor_group_ids=(original.group_hypothesis_id,),
        successor_member_sets=((first,), (second,)),
    )
    first_application = apply_coupled_successor_proposal(
        predecessors=(original,),
        proposal=proposal,
        neighborhood=_assessable_neighborhood(first, second),
        population=(original,),
    )
    retry_application = apply_coupled_successor_proposal(
        predecessors=(original,),
        proposal=proposal,
        neighborhood=_assessable_neighborhood(first, second),
        population=(original,),
    )
    assert retry_application == first_application
    assert tuple(
        item.group_hypothesis_id for item in first_application.successors
    ) == tuple(item.group_hypothesis_id for item in retry_application.successors)


def test_group_identity_is_process_and_retry_order_invariant(tmp_path) -> None:
    fixture, first, second = _subjects(tmp_path)
    request_id = fixture.request.processing_request_id

    with ProcessPoolExecutor(max_workers=2) as executor:
        receipts = tuple(
            executor.map(
                _mint_group_receipt_in_fresh_process,
                (request_id, request_id),
                ((first, second), (second, first)),
            )
        )

    assert receipts[0] == receipts[1]


def test_consolidation_successor_remints_identity_and_retains_lineage(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        CrossSampleNeighborhoodEdge,
        mint_cross_sample_consolidation_successor,
        mint_cross_sample_group_identity,
    )

    fixture, first, second = _subjects(tmp_path)
    predecessors = (
        mint_cross_sample_group_identity(
            processing_request_id=fixture.request.processing_request_id,
            member_subjects=(first,),
        ),
        mint_cross_sample_group_identity(
            processing_request_id=fixture.request.processing_request_id,
            member_subjects=(second,),
        ),
    )

    application = mint_cross_sample_consolidation_successor(
        predecessors=predecessors,
        successor_members=(first, second),
        edges=(
            CrossSampleNeighborhoodEdge(
                left_member_subject_id=first.member_subject_id,
                right_member_subject_id=second.member_subject_id,
                disposition="eligible",
            ),
        ),
        budget=CrossSampleNeighborhoodBudget(max_members=2, max_eligible_edges=1),
        population=predecessors,
    )

    assert len(application.successors) == 1
    successor = application.successors[0]
    assert successor.group_hypothesis_id not in {
        predecessor.group_hypothesis_id for predecessor in predecessors
    }
    assert tuple(
        edge.predecessor_group_hypothesis_id for edge in application.lineage
    ) == tuple(
        sorted(predecessor.group_hypothesis_id for predecessor in predecessors)
    )


def test_connected_neighborhood_rejects_fabricated_components(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleConnectedNeighborhood,
        CrossSampleContractError,
    )

    _, first, second = _subjects(tmp_path)
    with pytest.raises(CrossSampleContractError, match="components"):
        CrossSampleConnectedNeighborhood(
            status="assessable",
            member_subject_ids=tuple(
                sorted((first.member_subject_id, second.member_subject_id))
            ),
            eligible_edges=(),
            connected_components=((first.member_subject_id, second.member_subject_id),),
        )


def test_successor_rejects_unaffected_population_member_collision(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        CrossSampleMembershipClosure,
        CrossSampleNeighborhoodBudget,
        CrossSampleNeighborhoodEdge,
        CrossSampleSuccessorProposal,
        apply_coupled_successor_proposal,
        build_bounded_connected_neighborhood,
        mint_cross_sample_group_identity,
    )

    fixture, first, second = _subjects(tmp_path)
    predecessor = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(first,),
    )
    unaffected = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(second,),
    )
    proposal = CrossSampleSuccessorProposal(
        processing_request_id=fixture.request.processing_request_id,
        predecessor_group_ids=(predecessor.group_hypothesis_id,),
        successor_member_sets=((first, second),),
        membership_closure=CrossSampleMembershipClosure(added_members=(second,)),
    )
    neighborhood = build_bounded_connected_neighborhood(
        member_subjects=(first, second),
        edges=(
            CrossSampleNeighborhoodEdge(
                left_member_subject_id=first.member_subject_id,
                right_member_subject_id=second.member_subject_id,
                disposition="eligible",
            ),
        ),
        budget=CrossSampleNeighborhoodBudget(max_members=2, max_eligible_edges=1),
    )

    with pytest.raises(CrossSampleContractError, match="unaffected"):
        apply_coupled_successor_proposal(
            predecessors=(predecessor,),
            proposal=proposal,
            neighborhood=neighborhood,
            population=(predecessor, unaffected),
        )


def test_excluded_consolidation_edge_returns_typed_no_application(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        CrossSampleNeighborhoodEdge,
        mint_cross_sample_consolidation_successor,
        mint_cross_sample_group_identity,
    )

    fixture, first, second = _subjects(tmp_path)
    predecessors = tuple(
        mint_cross_sample_group_identity(
            processing_request_id=fixture.request.processing_request_id,
            member_subjects=(member,),
        )
        for member in (first, second)
    )
    application = mint_cross_sample_consolidation_successor(
        predecessors=predecessors,
        successor_members=(first, second),
        edges=(
            CrossSampleNeighborhoodEdge(
                left_member_subject_id=first.member_subject_id,
                right_member_subject_id=second.member_subject_id,
                disposition="excluded",
            ),
        ),
        budget=CrossSampleNeighborhoodBudget(max_members=2, max_eligible_edges=1),
        population=predecessors,
    )

    assert application.status == "unassessable"
    assert application.successors == ()
    assert application.lineage == ()


def test_oversized_eligible_a_b_b_c_component_applies_no_partial_successor(
    tmp_path,
) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        CrossSampleNeighborhoodEdge,
        group_member_subject_from_peak_observation,
        mint_cross_sample_consolidation_partition,
        mint_cross_sample_group_identity,
    )

    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=("sample-a", "sample-b", "sample-c"),
    )
    members = tuple(
        group_member_subject_from_peak_observation(
            processing_owners=fixture.owners,
            sample=fixture.sample(sample_stem),
            observation=issue161_peak(fixture, sample_stem),
        )
        for sample_stem in ("sample-a", "sample-b", "sample-c")
    )
    predecessors = tuple(
        mint_cross_sample_group_identity(
            processing_request_id=fixture.request.processing_request_id,
            member_subjects=(member,),
        )
        for member in members
    )

    application = mint_cross_sample_consolidation_partition(
        predecessors=predecessors,
        successor_member_sets=(members,),
        edges=(
            CrossSampleNeighborhoodEdge(
                left_member_subject_id=members[0].member_subject_id,
                right_member_subject_id=members[1].member_subject_id,
                disposition="eligible",
            ),
            CrossSampleNeighborhoodEdge(
                left_member_subject_id=members[1].member_subject_id,
                right_member_subject_id=members[2].member_subject_id,
                disposition="eligible",
            ),
        ),
        budget=CrossSampleNeighborhoodBudget(max_members=2, max_eligible_edges=2),
        population=predecessors,
    )

    assert application.status == "unassessable"
    assert application.successors == ()
    assert application.lineage == ()
    assert tuple(group.member_subjects for group in predecessors) == tuple(
        (member,) for member in members
    )


def test_lineage_carrier_rejects_forged_member_closure(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        CrossSamplePeakGroupHypothesis,
        validate_cross_sample_peak_group_identity,
    )

    fixture, first, second = _subjects(tmp_path)
    application = _two_member_consolidation(
        fixture.request.processing_request_id,
        first,
        second,
    )
    successor = application.successors[0]
    forged = replace(application.lineage[0], member_subject_ids=())
    carrier = CrossSamplePeakGroupHypothesis(
        group_hypothesis_id=successor.group_hypothesis_id,
        public_family_id="FAM-display-only",
        owner_ids=(),
        event_ids=(),
        event_member_count=0,
        processing_request_id=successor.processing_request_id,
        member_subjects=successor.member_subjects,
        identity_basis_bytes=successor.basis_bytes,
        disposition=successor.disposition,
        lineage=(forged,),
    )

    with pytest.raises(CrossSampleContractError, match="forged"):
        validate_cross_sample_peak_group_identity(carrier, require_canonical=True)


def test_lineage_carrier_replays_two_stage_consolidation(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        CrossSampleNeighborhoodEdge,
        CrossSamplePeakGroupHypothesis,
        group_member_subject_from_peak_observation,
        mint_cross_sample_consolidation_successor,
        mint_cross_sample_group_identity,
        predecessor_membership_neighborhood_edges,
        validate_cross_sample_peak_group_identity,
    )

    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=("sample-a", "sample-b", "sample-c"),
    )
    members = tuple(
        group_member_subject_from_peak_observation(
            processing_owners=fixture.owners,
            sample=fixture.sample(sample_stem),
            observation=issue161_peak(fixture, sample_stem),
        )
        for sample_stem in ("sample-a", "sample-b", "sample-c")
    )
    first, second, third = members
    predecessors = tuple(
        mint_cross_sample_group_identity(
            processing_request_id=fixture.request.processing_request_id,
            member_subjects=(member,),
        )
        for member in members
    )
    first_application = mint_cross_sample_consolidation_successor(
        predecessors=predecessors[:2],
        successor_members=(first, second),
        edges=(
            CrossSampleNeighborhoodEdge(
                left_member_subject_id=first.member_subject_id,
                right_member_subject_id=second.member_subject_id,
                disposition="eligible",
            ),
        ),
        budget=CrossSampleNeighborhoodBudget(max_members=2, max_eligible_edges=1),
        population=predecessors,
    )
    intermediate = first_application.successors[0]
    second_application = mint_cross_sample_consolidation_successor(
        predecessors=(intermediate, predecessors[2]),
        successor_members=(first, second, third),
        edges=(
            *predecessor_membership_neighborhood_edges(
                (intermediate, predecessors[2]),
            ),
            CrossSampleNeighborhoodEdge(
                left_member_subject_id=second.member_subject_id,
                right_member_subject_id=third.member_subject_id,
                disposition="eligible",
            ),
        ),
        budget=CrossSampleNeighborhoodBudget(max_members=3, max_eligible_edges=2),
        population=(intermediate, predecessors[2]),
    )
    successor = second_application.successors[0]
    carrier = CrossSamplePeakGroupHypothesis(
        group_hypothesis_id=successor.group_hypothesis_id,
        public_family_id="FAM-display-only",
        owner_ids=(),
        event_ids=(),
        event_member_count=0,
        processing_request_id=successor.processing_request_id,
        member_subjects=successor.member_subjects,
        identity_basis_bytes=successor.basis_bytes,
        disposition=successor.disposition,
        lineage=(*first_application.lineage, *second_application.lineage),
    )

    assert len(carrier.lineage) == 4
    assert validate_cross_sample_peak_group_identity(
        carrier,
        require_canonical=True,
    ) is None


def _two_member_consolidation(processing_request_id, first, second):
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        CrossSampleNeighborhoodEdge,
        mint_cross_sample_consolidation_successor,
        mint_cross_sample_group_identity,
    )

    predecessors = tuple(
        mint_cross_sample_group_identity(
            processing_request_id=processing_request_id,
            member_subjects=(member,),
        )
        for member in (first, second)
    )
    return mint_cross_sample_consolidation_successor(
        predecessors=predecessors,
        successor_members=(first, second),
        edges=(
            CrossSampleNeighborhoodEdge(
                left_member_subject_id=first.member_subject_id,
                right_member_subject_id=second.member_subject_id,
                disposition="eligible",
            ),
        ),
        budget=CrossSampleNeighborhoodBudget(max_members=2, max_eligible_edges=1),
        population=predecessors,
    )


def test_excluded_edge_cannot_enter_connected_neighborhood(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        CrossSampleNeighborhoodEdge,
        build_bounded_connected_neighborhood,
    )

    _, first, second = _subjects(tmp_path)
    neighborhood = build_bounded_connected_neighborhood(
        member_subjects=(first, second),
        edges=(
            CrossSampleNeighborhoodEdge(
                left_member_subject_id=first.member_subject_id,
                right_member_subject_id=second.member_subject_id,
                disposition="excluded",
            ),
        ),
        budget=CrossSampleNeighborhoodBudget(max_members=2, max_eligible_edges=1),
    )

    assert neighborhood.status == "assessable"
    assert neighborhood.connected_components == tuple(
        (member_subject_id,)
        for member_subject_id in sorted(
            (first.member_subject_id, second.member_subject_id)
        )
    )


def test_coupled_successor_application_rejects_partial_set_atomically(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        CrossSampleSuccessorProposal,
        apply_coupled_successor_proposal,
        mint_cross_sample_group_identity,
    )

    fixture, first, second = _subjects(tmp_path)
    predecessor = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(first, second),
    )
    proposal = CrossSampleSuccessorProposal(
        processing_request_id=fixture.request.processing_request_id,
        predecessor_group_ids=(predecessor.group_hypothesis_id,),
        successor_member_sets=((first,),),
    )

    with pytest.raises(CrossSampleContractError, match="partition"):
        apply_coupled_successor_proposal(
            predecessors=(predecessor,),
            proposal=proposal,
            neighborhood=_assessable_neighborhood(first, second),
            population=(predecessor,),
        )
    assert predecessor.member_subjects == (first, second)


def test_unchanged_membership_returns_exact_predecessor_without_self_lineage(
    tmp_path,
) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleSuccessorProposal,
        apply_coupled_successor_proposal,
        mint_cross_sample_group_identity,
    )

    fixture, first, second = _subjects(tmp_path)
    predecessor = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(first, second),
    )
    proposal = CrossSampleSuccessorProposal(
        processing_request_id=fixture.request.processing_request_id,
        predecessor_group_ids=(predecessor.group_hypothesis_id,),
        successor_member_sets=((first, second),),
    )

    application = apply_coupled_successor_proposal(
        predecessors=(predecessor,),
        proposal=proposal,
        neighborhood=_assessable_neighborhood(first, second),
        population=(predecessor,),
    )

    assert application.successors == (predecessor,)
    assert application.lineage == ()
    assert application.reason == "unchanged_membership_no_lineage"


def test_mixed_partition_retains_unchanged_group_without_self_lineage(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleNeighborhoodBudget,
        CrossSampleNeighborhoodEdge,
        mint_cross_sample_consolidation_partition,
        mint_cross_sample_group_identity,
    )

    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=("sample-a", "sample-b", "sample-c"),
    )
    from xic_extractor.alignment.cross_sample_peak_groups import (
        group_member_subject_from_peak_observation,
    )

    members = tuple(
        group_member_subject_from_peak_observation(
            processing_owners=fixture.owners,
            sample=fixture.sample(sample_stem),
            observation=issue161_peak(fixture, sample_stem),
        )
        for sample_stem in ("sample-a", "sample-b", "sample-c")
    )
    predecessors = tuple(
        mint_cross_sample_group_identity(
            processing_request_id=fixture.request.processing_request_id,
            member_subjects=(member,),
        )
        for member in members
    )

    application = mint_cross_sample_consolidation_partition(
        predecessors=predecessors,
        successor_member_sets=((members[0], members[1]), (members[2],)),
        edges=(
            CrossSampleNeighborhoodEdge(
                left_member_subject_id=members[0].member_subject_id,
                right_member_subject_id=members[1].member_subject_id,
                disposition="eligible",
            ),
        ),
        budget=CrossSampleNeighborhoodBudget(max_members=3, max_eligible_edges=1),
        population=predecessors,
    )

    assert application.status == "applied"
    unchanged = next(
        group
        for group in application.successors
        if group.member_subjects == (members[2],)
    )
    assert unchanged.group_hypothesis_id == predecessors[2].group_hypothesis_id
    assert {
        edge.predecessor_group_hypothesis_id for edge in application.lineage
    } == {predecessors[0].group_hypothesis_id, predecessors[1].group_hypothesis_id}
    assert all(
        edge.predecessor_group_hypothesis_id
        != edge.successor_group_hypothesis_id
        for edge in application.lineage
    )


def test_group_disposition_is_durable_and_closed(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        apply_group_disposition,
        mint_cross_sample_group_identity,
    )

    fixture, first, second = _subjects(tmp_path)
    established = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(first, second),
        disposition="established",
    )

    assert established.disposition == "established"
    unresolved = apply_group_disposition(established, disposition="unresolved")
    assert unresolved.group_hypothesis_id == established.group_hypothesis_id
    assert unresolved.member_subjects == established.member_subjects
    assert unresolved.basis_bytes == established.basis_bytes
    assert unresolved.disposition == "unresolved"
    with pytest.raises(CrossSampleContractError, match="singleton"):
        mint_cross_sample_group_identity(
            processing_request_id=fixture.request.processing_request_id,
            member_subjects=(first,),
            disposition="established",
        )


def test_pre_consolidation_excludes_numeric_compatible_different_istd_anchors(
    tmp_path,
) -> None:
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSamplePreConsolidationEdgeFacts,
        classify_cross_sample_pre_consolidation_edge,
        mint_cross_sample_group_identity,
    )
    from xic_extractor.alignment.drift_evidence import (
        build_targeted_istd_identity_anchor,
    )
    from xic_extractor.configuration.models import Target
    from xic_extractor.configuration.target_identity import mint_target_list_snapshot

    fixture, first, second = _subjects(tmp_path)
    left_group = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(first,),
    )
    right_group = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(second,),
    )

    def anchor(label: str, namespace: str):
        target = Target(
            label=label,
            mz=500.0,
            rt_min=8.0,
            rt_max=9.0,
            ppm_tol=20.0,
            neutral_loss_da=116.0474,
            nl_ppm_warn=20.0,
            nl_ppm_max=50.0,
            is_istd=True,
            istd_pair="",
        )
        return build_targeted_istd_identity_anchor(
            mint_target_list_snapshot((target,), identity_namespace=namespace),
            observed_labels=(label,),
        )

    edge = classify_cross_sample_pre_consolidation_edge(
        CrossSamplePreConsolidationEdgeFacts(
            left_group=left_group,
            right_group=right_group,
            left_neutral_loss_tag="NL116",
            right_neutral_loss_tag="NL116",
            left_precursor_mz=500.0,
            right_precursor_mz=500.0,
            left_product_mz=384.0,
            right_product_mz=384.0,
            left_observed_loss_da=116.0474,
            right_observed_loss_da=116.0474,
            left_rt_min=8.5,
            right_rt_min=8.5,
            left_targeted_istd_anchor=anchor("istd-left", "left"),
            right_targeted_istd_anchor=anchor("istd-right", "right"),
        ),
        config=AlignmentConfig(),
    )

    assert edge.disposition == "excluded"


def test_claim_disposition_and_backfill_exclude_different_istd_anchors(
    tmp_path,
) -> None:
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleBackfillScopeEdgeFacts,
        CrossSampleClaimEdgeFacts,
        CrossSamplePrimaryDispositionEdgeFacts,
        classify_cross_sample_backfill_scope_edge,
        classify_cross_sample_claim_edge,
        classify_cross_sample_primary_disposition_edge,
        group_member_subject_from_peak_observation,
        mint_cross_sample_group_identity,
    )
    from xic_extractor.alignment.drift_evidence import (
        build_targeted_istd_identity_anchor,
    )
    from xic_extractor.configuration.models import Target
    from xic_extractor.configuration.target_identity import mint_target_list_snapshot

    fixture, first, second = _subjects(tmp_path)
    same_sample_distinct = group_member_subject_from_peak_observation(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample-a"),
        observation=issue161_peak(fixture, "sample-a", apex_rt=8.05),
    )
    left_group = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(first,),
    )
    right_group = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(second,),
    )
    same_sample_group = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(same_sample_distinct,),
    )

    def anchor(label: str, namespace: str):
        target = Target(
            label=label,
            mz=500.0,
            rt_min=8.0,
            rt_max=9.0,
            ppm_tol=20.0,
            neutral_loss_da=116.0474,
            nl_ppm_warn=20.0,
            nl_ppm_max=50.0,
            is_istd=True,
            istd_pair="",
        )
        return build_targeted_istd_identity_anchor(
            mint_target_list_snapshot((target,), identity_namespace=namespace),
            observed_labels=(label,),
        )

    left_anchor = anchor("istd-left", "left")
    right_anchor = anchor("istd-right", "right")
    config = AlignmentConfig()
    assert (
        classify_cross_sample_claim_edge(
            CrossSampleClaimEdgeFacts(
                left_group=left_group,
                right_group=right_group,
                left_member_subject=first,
                right_member_subject=second,
                left_family_mz=500.0,
                right_family_mz=500.0,
                left_apex_rt=8.5,
                right_apex_rt=8.5,
                left_peak_start_rt=8.4,
                left_peak_end_rt=8.6,
                right_peak_start_rt=8.4,
                right_peak_end_rt=8.6,
                context="claim_candidate",
                left_neutral_loss_tag="NL116",
                right_neutral_loss_tag="NL116",
                left_targeted_istd_anchor=left_anchor,
                right_targeted_istd_anchor=right_anchor,
            ),
            config=config,
        )
        == "excluded"
    )
    assert (
        classify_cross_sample_primary_disposition_edge(
            CrossSamplePrimaryDispositionEdgeFacts(
                left_group=left_group,
                right_group=same_sample_group,
                left_detected_members=(first,),
                right_detected_members=(same_sample_distinct,),
                left_detected_count=1,
                right_detected_count=2,
                left_rescue_count=2,
                right_rescue_count=2,
                left_neutral_loss_tag="NL116",
                right_neutral_loss_tag="NL116",
                left_precursor_mz=500.0,
                right_precursor_mz=500.0,
                left_product_mz=384.0,
                right_product_mz=384.0,
                left_observed_loss_da=116.0474,
                right_observed_loss_da=116.0474,
                left_rt_min=8.5,
                right_rt_min=8.5,
                left_targeted_istd_anchor=left_anchor,
                right_targeted_istd_anchor=right_anchor,
            ),
            config=config,
        ).disposition
        == "excluded"
    )
    assert (
        classify_cross_sample_backfill_scope_edge(
            CrossSampleBackfillScopeEdgeFacts(
                left_group=left_group,
                right_group=right_group,
                left_neutral_loss_tag="NL116",
                right_neutral_loss_tag="NL116",
                left_precursor_mz=500.0,
                right_precursor_mz=500.0,
                left_product_mz=384.0,
                right_product_mz=384.0,
                left_observed_loss_da=116.0474,
                right_observed_loss_da=116.0474,
                left_rt_min=8.5,
                right_rt_min=8.5,
                left_targeted_istd_anchor=left_anchor,
                right_targeted_istd_anchor=right_anchor,
            ),
            config=config,
        ).disposition
        == "excluded"
    )


def test_unresolved_predecessor_cannot_reopen_pre_consolidation_edge(tmp_path) -> None:
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSamplePreConsolidationEdgeFacts,
        classify_cross_sample_pre_consolidation_edge,
        mint_cross_sample_group_identity,
    )

    fixture, first, second = _subjects(tmp_path)
    unresolved = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(first,),
        disposition="unresolved",
    )
    established = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(second,),
    )

    edge = classify_cross_sample_pre_consolidation_edge(
        CrossSamplePreConsolidationEdgeFacts(
            left_group=unresolved,
            right_group=established,
            left_neutral_loss_tag="NL116",
            right_neutral_loss_tag="NL116",
            left_precursor_mz=500.0,
            right_precursor_mz=500.0,
            left_product_mz=384.0,
            right_product_mz=384.0,
            left_observed_loss_da=116.0474,
            right_observed_loss_da=116.0474,
            left_rt_min=8.5,
            right_rt_min=8.5,
        ),
        config=AlignmentConfig(),
    )

    assert edge.disposition == "excluded"


def test_unassessable_neighborhood_cannot_bypass_successor_application(
    tmp_path,
) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        CrossSampleNeighborhoodBudget,
        CrossSampleSuccessorProposal,
        apply_coupled_successor_proposal,
        build_bounded_connected_neighborhood,
        mint_cross_sample_group_identity,
    )

    fixture, first, second = _subjects(tmp_path)
    predecessor = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(first, second),
    )
    unassessable = build_bounded_connected_neighborhood(
        member_subjects=(first, second),
        edges=(),
        budget=CrossSampleNeighborhoodBudget(max_members=1, max_eligible_edges=0),
    )
    proposal = CrossSampleSuccessorProposal(
        processing_request_id=fixture.request.processing_request_id,
        predecessor_group_ids=(predecessor.group_hypothesis_id,),
        successor_member_sets=((first,), (second,)),
    )

    with pytest.raises(CrossSampleContractError, match="unassessable"):
        apply_coupled_successor_proposal(
            predecessors=(predecessor,),
            proposal=proposal,
            neighborhood=unassessable,
            population=(predecessor,),
        )
