"""Focused contract tests for canonical CrossSample identity and lineage."""

from __future__ import annotations

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
        peak_observation=cid_nl_peak(fixture, sample_stem, **peak_kwargs),
    )
    return group_member_subject_from_peak_hypothesis(
        sample=fixture.sample(sample_stem),
        hypothesis=hypothesis,
    )


def _subjects(tmp_path, *sample_stems: str):
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=sample_stems
    )
    subjects = tuple(_member(fixture, sample_stem) for sample_stem in sample_stems)
    return fixture, subjects


def test_group_identity_is_order_invariant(tmp_path) -> None:
    from xic_extractor.alignment.cross_sample.peak_groups import (
        mint_cross_sample_group_identity,
    )

    fixture, members = _subjects(tmp_path, "sample-a", "sample-b")
    forward = mint_cross_sample_group_identity(member_subjects=members)
    reversed_group = mint_cross_sample_group_identity(
        member_subjects=tuple(reversed(members))
    )
    assert forward == reversed_group
