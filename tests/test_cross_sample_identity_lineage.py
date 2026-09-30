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


def test_successor_application_rejects_mutable_closure_containers(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleContractError,
        CrossSamplePartitionTransition,
        mint_cross_sample_group_identity,
    )

    fixture, (member,) = _subjects(tmp_path, "sample-a")
    predecessor = mint_cross_sample_group_identity(
        processing_request_id=fixture.request.processing_request_id,
        member_subjects=(member,),
    )
    with pytest.raises(CrossSampleContractError, match="immutable tuples"):
        CrossSamplePartitionTransition(
            status="unassessable",
            predecessors=[predecessor],  # type: ignore[arg-type]
            successors=[],  # type: ignore[arg-type]
            lineage=[],  # type: ignore[arg-type]
            reason="eligible_neighborhood_exceeds_budget",
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


