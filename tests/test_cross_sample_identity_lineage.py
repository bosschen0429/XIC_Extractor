"""Focused contract tests for canonical CrossSample identity and lineage."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

import pytest

from tests.cid_nl_peak_support_no_raw_fixture import cid_nl_peak
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)


def _member(fixture, sample_stem: str, **peak_kwargs):
    from xic_extractor.alignment.cross_sample.peak_groups import (
        group_member_subject_from_peak_hypothesis,
    )
    from xic_extractor.peak_detection.peak_hypothesis_identity import (
        compose_ms1_observation_peak_hypothesis,
    )

    hypothesis = compose_ms1_observation_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        peak_observation=cid_nl_peak(fixture, sample_stem, **peak_kwargs),
    )
    return group_member_subject_from_peak_hypothesis(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        hypothesis=hypothesis,
    )


def _subjects(tmp_path, *sample_stems: str):
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=sample_stems
    )
    subjects = tuple(_member(fixture, sample_stem) for sample_stem in sample_stems)
    return fixture, subjects


def _mint_group_in_process(processing_request_id, members):
    from xic_extractor.alignment.cross_sample.peak_groups import (
        mint_cross_sample_group_identity,
    )

    group = mint_cross_sample_group_identity(
        processing_request_id=processing_request_id,
        member_subjects=members,
    )
    return group


def test_group_rejects_two_identity_members_for_one_canonical_sample(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample.peak_groups import (
        CrossSampleContractError,
        mint_cross_sample_group_identity,
    )

    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-a",)
    )
    first = _member(fixture, "sample-a", apex_rt=8.0)
    second = _member(fixture, "sample-a", apex_rt=8.03)
    assert first != second

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


def test_equivalent_hypothesis_wrappers_share_member_identity(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample.peak_groups import (
        group_member_subject_from_peak_hypothesis,
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

    assert wrapped_a == wrapped_b


def test_group_mint_rejects_crossed_processing_request(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample.peak_groups import (
        CrossSampleContractError,
        mint_cross_sample_group_identity,
    )

    fixture, members = _subjects(tmp_path, "sample-a", "sample-b")
    other, _ = _subjects(tmp_path, "sample-c")
    with pytest.raises(CrossSampleContractError, match="crossed processing request"):
        mint_cross_sample_group_identity(
            processing_request_id=other.request.processing_request_id,
            member_subjects=(members[0],),
        )


def test_initial_edge_classifier_is_closed_and_preserves_existing_policy() -> None:
    from dataclasses import replace as dc_replace

    from tests.alignment_pipeline_helpers import owner_edge_evidence
    from xic_extractor.alignment import AlignmentConfig
    from xic_extractor.alignment.cross_sample.peak_groups import (
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
