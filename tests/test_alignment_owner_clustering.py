from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleContractError,
    CrossSampleGroupMemberSubject,
    construct_cross_sample_peak_group_hypotheses,
    review_only_peak_group_hypotheses_from_ambiguous_records,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.drift_evidence import KnownIstdOwnerLookup
from xic_extractor.alignment.owner_clustering import cluster_sample_local_owners
from xic_extractor.alignment.ownership_models import (
    AmbiguousOwnerRecord,
    IdentityEvent,
    SampleLocalMS1Owner,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)

_TEST_PROCESSING_REQUEST_ID = "xic.processing-request:" + "0" * 64


@dataclass(frozen=True)
class _DriftLookup:
    deltas: dict[str, float]
    orders: dict[str, int]
    source: str = "batch_istd_trend"

    def sample_delta_min(self, sample_stem: str) -> float | None:
        return self.deltas.get(sample_stem)

    def injection_order(self, sample_stem: str) -> int | None:
        return self.orders.get(sample_stem)


def _owner(
    sample_stem: str,
    suffix: str,
    *,
    apex_rt: float = 8.5,
    neutral_loss_tag: str = "NL116",
    precursor_mz: float = 500.0,
    product_mz: float = 383.9526,
    observed_loss: float = 116.0474,
    evidence_score: int = 80,
    seed_event_count: int = 2,
    identity_conflict: bool = False,
    is_provisional: bool = False,
) -> SampleLocalMS1Owner:
    event = IdentityEvent(
        candidate_id=f"{sample_stem}#{suffix}",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        neutral_loss_tag=neutral_loss_tag,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        observed_neutral_loss_da=observed_loss,
        seed_rt=apex_rt,
        evidence_score=evidence_score,
        seed_event_count=seed_event_count,
    )
    physical_id = typed_identity_digest(
        "xic.peak-observation-content:",
        canonical_identity_basis_bytes({"sample_stem": sample_stem, "suffix": suffix}),
    )
    hypothesis_id = typed_identity_digest(
        "xic.peak-hypothesis:",
        canonical_identity_basis_bytes({"sample_stem": sample_stem, "suffix": suffix}),
    )
    member_basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject",
            "processing_request_id": _TEST_PROCESSING_REQUEST_ID,
            "canonical_sample_identity_id": f"test-sample:{sample_stem}",
            "subject": {"kind": "peak_hypothesis", "id": hypothesis_id},
            "physical_observation_ids": [physical_id],
        }
    )
    return SampleLocalMS1Owner(
        owner_id=f"OWN-{sample_stem}-{suffix}",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        precursor_mz=precursor_mz,
        owner_apex_rt=apex_rt,
        owner_peak_start_rt=apex_rt - 0.05,
        owner_peak_end_rt=apex_rt + 0.05,
        owner_area=1_000.0,
        owner_height=100.0,
        primary_identity_event=event,
        supporting_events=(),
        identity_conflict=identity_conflict,
        assignment_reason="owner_exact_apex_match",
        selected_integration=_integration(apex_rt),
        cross_sample_member_subject=CrossSampleGroupMemberSubject(
            member_subject_id=typed_identity_digest(
                "xic.group-member-subject:", member_basis
            ),
            processing_request_id=_TEST_PROCESSING_REQUEST_ID,
            canonical_sample_identity_id=f"test-sample:{sample_stem}",
            subject_kind="peak_hypothesis",
            subject_id=hypothesis_id,
            physical_observation_ids=(physical_id,),
            basis_bytes=member_basis,
        ),
        is_provisional=is_provisional,
    )


def _integration(apex_rt: float) -> IntegrationResult:
    return IntegrationResult(
        rt_left_min=apex_rt - 0.05,
        rt_apex_min=apex_rt,
        rt_right_min=apex_rt + 0.05,
        raw_apex_rt_min=apex_rt,
        rt_width_min=0.1,
        height_raw=100.0,
        height_smoothed=100.0,
        area_raw_counts_seconds=1_000.0,
        area_baseline_corrected=900.0,
        baseline_type="asls",
        boundary_sources=("test_owner",),
        area_ms1_morphology=900.0,
        ms1_morphology_area_source="gaussian15_positive_asls_residual",
    )


def _member_sets(groups) -> set[frozenset[str]]:
    return {frozenset(group.owner_ids) for group in groups}


def test_public_inference_groups_confirmed_true_drift() -> None:
    owners = tuple(
        _owner(f"sample-{index}", "main", apex_rt=10.0 + 0.75 * index)
        for index in range(4)
    )
    groups = construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0),
        sample_universe=frozenset(owner.sample_stem for owner in owners),
        injection_order={
            owner.sample_stem: index for index, owner in enumerate(owners)
        },
        bootstrap=True,
    )

    assert len(groups) == 1
    assert groups[0].owner_ids == tuple(sorted(owner.owner_id for owner in owners))
    assert groups[0].review_only is False
    assert groups[0].inference_basis is not None
    assert groups[0].inference_basis.basis == "bidirectional_membership_agreement"


def test_parallel_tracks_remain_distinct_without_order_dependent_labels() -> None:
    owners = (
        _owner("sample-a", "low", apex_rt=10.00),
        _owner("sample-b", "low", apex_rt=10.20),
        _owner("sample-c", "low", apex_rt=10.40),
        _owner("sample-a", "high", apex_rt=12.00),
        _owner("sample-b", "high", apex_rt=12.20),
        _owner("sample-c", "high", apex_rt=12.40),
    )
    groups = construct_cross_sample_peak_group_hypotheses(
        tuple(reversed(owners)),
        config=AlignmentConfig(),
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c"}),
        injection_order={"sample-a": 1, "sample-b": 2, "sample-c": 3},
    )

    assert _member_sets(groups) == {
        frozenset(owner.owner_id for owner in owners if "low" in owner.owner_id),
        frozenset(owner.owner_id for owner in owners if "high" in owner.owner_id),
    }
    assert all(not group.review_only for group in groups)


def test_equal_bridge_competition_is_undecided_instead_of_false_merge() -> None:
    owners = (
        _owner("sample-a", "left", apex_rt=10.0),
        _owner("sample-a", "right", apex_rt=11.0),
        _owner("sample-b", "bridge", apex_rt=10.5),
    )
    groups = construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0),
        injection_order={"sample-a": 1, "sample-b": 2},
    )

    assert _member_sets(groups) == {frozenset((owner.owner_id,)) for owner in owners}
    assert all(group.review_only for group in groups)
    assert all(
        group.inference_basis is not None
        and group.inference_basis.disposition == "undecided"
        for group in groups
    )


def test_mass_noise_bridge_is_scored_inside_candidate_neighborhood() -> None:
    first = _owner("sample-a", "first", precursor_mz=500.000)
    bridge = _owner("sample-b", "bridge", precursor_mz=500.004)
    last = _owner("sample-c", "last", precursor_mz=500.008)
    groups = construct_cross_sample_peak_group_hypotheses(
        (first, bridge, last),
        config=AlignmentConfig(preferred_ppm=10.0, max_ppm=10.0),
        injection_order={"sample-a": 1, "sample-b": 2, "sample-c": 3},
    )

    # #437 D1 removes the accumulated mass envelope as a rejection: each
    # neighbouring pair is within the lookup window even though the full
    # band's precursor span is 16 ppm against a 10 ppm candidate radius.
    assert len(groups) == 1
    assert not groups[0].review_only
    assert set(groups[0].owner_ids) == {
        first.owner_id,
        bridge.owner_id,
        last.owner_id,
    }


def test_irrelevant_sample_does_not_change_existing_membership() -> None:
    owners = (
        _owner("sample-a", "main", apex_rt=10.0),
        _owner("sample-b", "main", apex_rt=10.2),
    )
    base = construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(),
        sample_universe=frozenset({"sample-a", "sample-b"}),
        injection_order={"sample-a": 1, "sample-b": 3},
    )
    expanded = construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(),
        sample_universe=frozenset({"sample-a", "irrelevant", "sample-b"}),
        injection_order={"sample-a": 1, "irrelevant": 2, "sample-b": 3},
    )

    assert [group.owner_ids for group in base] == [
        group.owner_ids for group in expanded
    ]
    assert [group.inference_basis for group in base] == [
        group.inference_basis for group in expanded
    ]


def test_drift_prior_can_support_a_pair_outside_raw_rt_preference() -> None:
    left = _owner("sample-a", "main", apex_rt=10.0)
    right = _owner("sample-b", "main", apex_rt=11.5)
    groups = construct_cross_sample_peak_group_hypotheses(
        (left, right),
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        injection_order={"sample-a": 1, "sample-b": 2},
        drift_lookup=_DriftLookup(
            deltas={"sample-a": 0.0, "sample-b": 1.5},
            orders={"sample-a": 1, "sample-b": 2},
        ),
        bootstrap=True,
    )

    assert len(groups) == 1
    assert groups[0].owner_ids == (left.owner_id, right.owner_id)
    assert groups[0].review_only is False


def test_weak_or_conflicted_evidence_remains_review_only() -> None:
    clean = _owner("sample-a", "clean")
    weak = _owner("sample-b", "weak", evidence_score=10)
    conflict = _owner("sample-c", "conflict", identity_conflict=True)
    groups = construct_cross_sample_peak_group_hypotheses(
        (clean, weak, conflict), config=AlignmentConfig()
    )

    by_owner = {group.owner_ids[0]: group for group in groups}
    assert by_owner[weak.owner_id].review_only is True
    assert by_owner[conflict.owner_id].review_only is True
    assert by_owner[conflict.owner_id].identity_conflict is True
    assert all(len(group.owner_ids) == 1 for group in groups)


def test_same_sample_observations_never_share_a_feature() -> None:
    left = _owner("same", "left", apex_rt=10.0)
    right = _owner("same", "right", apex_rt=10.1)
    groups = construct_cross_sample_peak_group_hypotheses(
        (left, right), config=AlignmentConfig()
    )

    assert _member_sets(groups) == {
        frozenset((left.owner_id,)),
        frozenset((right.owner_id,)),
    }


def test_exact_known_identity_is_one_group_without_unbound_absorption() -> None:
    early = _owner(
        "early", "known", apex_rt=8.0, product_mz=383.90, observed_loss=116.10
    )
    late = _owner(
        "late", "known", apex_rt=14.0, product_mz=384.10, observed_loss=115.90
    )
    lookalike = _owner("other", "lookalike", apex_rt=8.0)
    lookup = KnownIstdOwnerLookup(
        reference=cast(Any, None),
        bindings=(
            (early.owner_id, "d3-N6-medA", -1.0),
            (late.owner_id, "d3-N6-medA", 5.0),
        ),
    )
    groups = construct_cross_sample_peak_group_hypotheses(
        (early, late, lookalike),
        config=AlignmentConfig(),
        injection_order={"early": 1, "late": 2, "other": 3},
        drift_lookup=lookup,
    )

    known = next(
        group
        for group in groups
        if group.inference_basis is not None
        and group.inference_basis.known_identity_label == "d3-N6-medA"
    )
    assert known.owner_ids == tuple(sorted((early.owner_id, late.owner_id)))
    assert lookalike.owner_id not in known.owner_ids
    assert known.review_only is False


def test_contradictory_known_identity_sample_occupancy_fails_closed() -> None:
    left = _owner("same", "left")
    right = _owner("same", "right", apex_rt=10.2)
    lookup = KnownIstdOwnerLookup(
        reference=cast(Any, None),
        bindings=((left.owner_id, "known", 0.0), (right.owner_id, "known", 0.0)),
    )

    with pytest.raises(
        CrossSampleContractError,
        match="selection is unresolved and identity cannot be split",
    ):
        construct_cross_sample_peak_group_hypotheses(
            (left, right), config=AlignmentConfig(), drift_lookup=lookup
        )


def test_constructor_retains_canonical_identity_and_inference_basis() -> None:
    owners = (
        _owner("sample-a", "main", apex_rt=10.0),
        _owner("sample-b", "main", apex_rt=10.1),
    )
    groups = construct_cross_sample_peak_group_hypotheses(
        tuple(reversed(owners)), config=AlignmentConfig()
    )

    assert len(groups) == 1
    identity = validate_cross_sample_peak_group_identity(
        groups[0], require_canonical=True
    )
    assert identity is not None
    assert identity.group_hypothesis_id == groups[0].group_hypothesis_id
    assert {subject.subject_id for subject in identity.member_subjects} == {
        owner.cross_sample_member_subject.subject_id
        for owner in owners
        if owner.cross_sample_member_subject is not None
    }
    assert groups[0].inference_basis is not None
    assert groups[0].inference_basis.owner_ids == groups[0].owner_ids


def test_legacy_pair_sink_stays_empty_and_absence_is_not_exclusion() -> None:
    edge_evidence = []
    groups = construct_cross_sample_peak_group_hypotheses(
        (
            _owner("sample-a", "main", apex_rt=10.0),
            _owner("sample-b", "main", apex_rt=10.1),
        ),
        config=AlignmentConfig(),
        edge_evidence_sink=edge_evidence,
        bootstrap=True,
    )

    assert edge_evidence == []
    assert groups[0].inference_basis is not None
    assert groups[0].inference_basis.reason == (
        "both_finite_directional_passes_support_the_same_membership"
    )


def test_singletons_are_explicitly_unresolved_cross_sample_membership() -> None:
    definitive = _owner("sample-a", "definitive")
    provisional = _owner("sample-b", "provisional", is_provisional=True)
    groups = construct_cross_sample_peak_group_hypotheses(
        (definitive, provisional), config=AlignmentConfig()
    )

    by_owner = {group.owner_ids[0]: group for group in groups}
    assert by_owner[definitive.owner_id].has_anchor is True
    assert by_owner[definitive.owner_id].review_only is True
    assert by_owner[provisional.owner_id].has_anchor is False
    assert by_owner[provisional.owner_id].review_only is True
    assert all(
        group.inference_basis is not None
        and group.inference_basis.disposition == "undecided"
        for group in groups
    )


def test_ambiguous_records_remain_review_only_without_fabricated_owner() -> None:
    groups = review_only_peak_group_hypotheses_from_ambiguous_records(
        (
            AmbiguousOwnerRecord(
                ambiguity_id="amb-1",
                sample_stem="sample-a",
                candidate_ids=("candidate-a", "candidate-b"),
                reason="nearby_peaks",
                neutral_loss_tag="NL116",
                precursor_mz=500.0,
                apex_rt=10.0,
                product_mz=383.9526,
                observed_neutral_loss_da=116.0474,
            ),
        ),
        start_index=1,
    )

    assert len(groups) == 1
    assert groups[0].owner_ids == ()
    assert groups[0].review_only is True
    assert groups[0].ambiguous_candidate_ids == ("candidate-a", "candidate-b")


def test_cluster_adapter_preserves_population_exactly_once() -> None:
    owners = (
        _owner("sample-a", "left", apex_rt=10.0),
        _owner("sample-a", "right", apex_rt=12.0),
        _owner("sample-b", "left", apex_rt=10.1),
        _owner("sample-b", "right", apex_rt=12.1),
    )
    features = cluster_sample_local_owners(
        owners,
        config=AlignmentConfig(),
        injection_order={"sample-a": 1, "sample-b": 2},
    )

    projected = tuple(
        owner_id for feature in features for owner_id in feature.event_cluster_ids
    )
    assert sorted(projected) == sorted(owner.owner_id for owner in owners)
    assert len(projected) == len(set(projected))
