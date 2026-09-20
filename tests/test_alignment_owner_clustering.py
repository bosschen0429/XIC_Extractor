import csv
from dataclasses import replace
from pathlib import Path

from xic_extractor.alignment import cross_sample_peak_groups as peak_groups_module
from xic_extractor.alignment import owner_clustering as owner_clustering_module
from xic_extractor.alignment.backfill_scope import (
    backfill_request_sample_stems,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupMemberSubject,
)
from xic_extractor.alignment.drift_evidence import (
    DriftEvidenceLookup,
    build_targeted_istd_identity_anchor,
)
from xic_extractor.alignment.edge_scoring import evaluate_owner_edge
from xic_extractor.alignment.owner_clustering import (
    cluster_sample_local_owners,
    review_only_features_from_ambiguous_records,
)
from xic_extractor.alignment.owner_matrix import build_owner_alignment_matrix
from xic_extractor.alignment.ownership_models import (
    AmbiguousOwnerRecord,
    IdentityEvent,
    SampleLocalMS1Owner,
)
from xic_extractor.alignment.production_decisions import build_production_decisions
from xic_extractor.configuration.models import Target
from xic_extractor.configuration.target_identity import mint_target_list_snapshot
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)

_TEST_PROCESSING_REQUEST_ID = "xic.processing-request:" + "0" * 64


def test_raw_sufficient_group_skips_drift_in_admission_and_ranking() -> None:
    class UnneededDrift(_DriftLookup):
        def sample_delta_min(self, sample_stem: str) -> float | None:
            raise AssertionError("raw-sufficient grouping must not query correction")

    owners = (_owner("a", "main", apex_rt=10.0), _owner("b", "main", apex_rt=10.1))
    edges = []
    features = cluster_sample_local_owners(
        owners, config=AlignmentConfig(),
        drift_lookup=UnneededDrift(deltas={}, orders={}),
        edge_evidence_sink=edges,
    )
    assert [set(feature.event_cluster_ids) for feature in features] == [
        {owner.owner_id for owner in owners},
    ]
    assert len(edges) == 1
    assert edges[0].rt_basis == "raw"
    assert features[0].disposition == "established"
    assert features[0].review_only is False


def test_degraded_drift_uses_moderate_complete_link_admission() -> None:
    owners = tuple(
        _owner(sample, "main", apex_rt=10.0 + index * 0.05,
               evidence_score=45, seed_event_count=1)
        for index, sample in enumerate(("a", "b", "c"))
    )
    edges = []
    features = cluster_sample_local_owners(
        owners, config=AlignmentConfig(), sample_universe=frozenset({"a", "b", "c"}),
        injection_order={"a": 1, "b": 2, "c": 3},
        drift_lookup=_DriftLookup(
            deltas={"a": 0.0, "b": -0.3, "c": -0.6},
            orders={"a": 1, "b": 2, "c": 3},
        ),
        edge_evidence_sink=edges,
    )
    assert [set(feature.event_cluster_ids) for feature in features] == [
        {owner.owner_id for owner in owners},
    ]
    assert all(edge.rt_basis == "raw" and edge.score == 45 for edge in edges)
    assert all(edge.decision == "weak_edge" for edge in edges)
    assert features[0].evidence == "owner_complete_link_moderate;owner_count=3"
    assert features[0].disposition == "provisional"
    assert features[0].review_only is True


def test_owner_clustering_allows_plausible_rt_drift_with_complete_link() -> None:
    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", apex_rt=12.59),
            _owner("sample-b", "b", apex_rt=12.88),
        ),
        config=AlignmentConfig(identity_rt_candidate_window_sec=180.0),
    )

    assert len(features) == 1
    assert features[0].event_cluster_ids == ("OWN-sample-a-a", "OWN-sample-b-b")
    assert features[0].evidence == "owner_complete_link;owner_count=2"


def test_owner_clustering_collapses_5medc_like_class_drift() -> None:
    features = cluster_sample_local_owners(
        (
            _owner("tumor-a", "a", apex_rt=11.5674),
            _owner("qc-a", "b", apex_rt=12.1813),
            _owner("qc-b", "c", apex_rt=12.4051),
            _owner("normal-a", "d", apex_rt=12.6515),
            _owner("benign-a", "e", apex_rt=12.6673),
        ),
        config=AlignmentConfig(identity_rt_candidate_window_sec=180.0),
        drift_lookup=_DriftLookup(
            deltas={
                "tumor-a": -0.35,
                "qc-a": 0.0,
                "qc-b": 0.10,
                "normal-a": 0.35,
                "benign-a": 0.36,
            },
            orders={
                "tumor-a": 1,
                "qc-a": 20,
                "qc-b": 40,
                "normal-a": 60,
                "benign-a": 80,
            },
        ),
    )

    assert len(features) == 1
    assert len(features[0].event_cluster_ids) == 5
    assert features[0].evidence == "owner_complete_link;owner_count=5"


def test_owner_clustering_connects_dense_rt_chain_without_external_drift() -> None:
    edge_evidence = []
    owners = (
        _owner("sample-a", "a", apex_rt=10.00),
        _owner("sample-b", "b", apex_rt=10.75),
        _owner("sample-c", "c", apex_rt=11.50),
    )
    config = AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0)

    features = cluster_sample_local_owners(
        owners,
        config=config,
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c"}),
        edge_evidence_sink=edge_evidence,
    )

    assert (
        evaluate_owner_edge(
            owners[0],
            owners[1],
            config=config,
        ).decision
        == "strong_edge"
    )
    assert (
        evaluate_owner_edge(
            owners[1],
            owners[2],
            config=config,
        ).decision
        == "strong_edge"
    )
    grouped_owner_ids = [feature.event_cluster_ids for feature in features]
    assert grouped_owner_ids == [
        (
            "OWN-sample-a-a",
            "OWN-sample-b-b",
            "OWN-sample-c-c",
        )
    ]
    assert (
        _edge_decisions_by_pair(edge_evidence)[("OWN-sample-a-a", "OWN-sample-c-c")]
        == "weak_edge"
    )
    assert features[0].evidence == "owner_full_batch_continuity;owner_count=3"


def test_owner_clustering_keeps_bounded_incomplete_rt_chain() -> None:
    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", apex_rt=10.00),
            _owner("sample-b", "b", apex_rt=10.75),
            _owner("sample-c", "c", apex_rt=11.50),
        ),
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset(
            {"sample-a", "sample-b", "sample-c", "sample-d"},
        ),
    )

    assert [feature.event_cluster_ids for feature in features] == [
        ("OWN-sample-a-a", "OWN-sample-b-b", "OWN-sample-c-c"),
    ]
    assert features[0].evidence == "owner_bounded_continuity;owner_count=3"


def test_owner_clustering_splits_rt_chain_beyond_span_bound() -> None:
    features = cluster_sample_local_owners(
        tuple(
            _owner(f"sample-{index}", f"o{index}", apex_rt=10.00 + 0.9 * index)
            for index in range(7)
        ),
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset(
            {f"sample-{index}" for index in range(8)},
        ),
    )

    assert len(features) > 1
    assert all(
        "owner_bounded_continuity" not in feature.evidence for feature in features
    )


def test_owner_clustering_splits_chain_that_fails_hard_envelope() -> None:
    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", apex_rt=10.00, precursor_mz=100.0),
            _owner("sample-b", "b", apex_rt=10.10, precursor_mz=100.003),
            _owner("sample-c", "c", apex_rt=10.20, precursor_mz=100.006),
        ),
        config=AlignmentConfig(
            preferred_rt_sec=60.0,
            max_rt_sec=180.0,
            preferred_ppm=50.0,
            max_ppm=50.0,
        ),
        sample_universe=frozenset(
            {"sample-a", "sample-b", "sample-c", "sample-d"},
        ),
    )

    assert len(features) > 1
    assert all(
        "owner_bounded_continuity" not in feature.evidence for feature in features
    )


def test_owner_clustering_restores_incomplete_injection_trajectory() -> None:
    owners = (
        _owner("sample-a", "a", apex_rt=10.00),
        _owner("sample-b", "b", apex_rt=10.75),
        _owner("sample-c", "c", apex_rt=11.50),
        _owner("sample-d", "d", apex_rt=12.25),
    )
    config = AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0)
    sample_universe = frozenset(
        {"sample-a", "sample-b", "sample-c", "sample-d", "sample-e"},
    )

    without_order = cluster_sample_local_owners(
        owners,
        config=config,
        sample_universe=sample_universe,
    )
    with_order = cluster_sample_local_owners(
        owners,
        config=config,
        sample_universe=sample_universe,
        injection_order={
            "sample-a": 1,
            "sample-b": 2,
            "sample-c": 3,
            "sample-d": 4,
            "sample-e": 5,
        },
    )

    assert with_order[0].evidence == "owner_injection_trajectory;owner_count=4"
    assert without_order[0].evidence == "owner_bounded_continuity;owner_count=4"
    assert [feature.event_cluster_ids for feature in with_order] == [
        (
            "OWN-sample-a-a",
            "OWN-sample-b-b",
            "OWN-sample-c-c",
            "OWN-sample-d-d",
        )
    ]


def test_owner_clustering_requires_complete_unique_injection_order() -> None:
    owners = (
        _owner("sample-a", "a", apex_rt=10.00),
        _owner("sample-b", "b", apex_rt=10.75),
        _owner("sample-c", "c", apex_rt=11.50),
        _owner("sample-d", "d", apex_rt=12.25),
    )
    config = AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0)
    sample_universe = frozenset(
        {"sample-a", "sample-b", "sample-c", "sample-d", "sample-e"},
    )

    for injection_order in (
        {"sample-a": 1, "sample-b": 2, "sample-c": 3},
        {"sample-a": 1, "sample-b": 2, "sample-c": 2, "sample-d": 4},
    ):
        features = cluster_sample_local_owners(
            owners,
            config=config,
            sample_universe=sample_universe,
            injection_order=injection_order,
        )

        assert all(
            "owner_injection_trajectory" not in feature.evidence for feature in features
        )


def test_owner_clustering_restores_partial_trajectory() -> None:
    owners = tuple(
        _owner(
            f"sample-{index}",
            f"o{index}",
            apex_rt=10.00 + 0.9 * (index - 1),
        )
        for index in range(1, 8)
    )

    features = cluster_sample_local_owners(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset(f"sample-{index}" for index in range(1, 13)),
        injection_order={f"sample-{index}": index for index in range(1, 13)},
    )

    assert [feature.event_cluster_ids for feature in features] == [
        tuple(f"OWN-sample-{index}-o{index}" for index in range(1, 8)),
    ]
    assert features[0].evidence == "owner_injection_trajectory;owner_count=7"


def test_owner_clustering_does_not_use_drift_lookup_order_for_trajectory() -> None:
    owners = (
        _owner("sample-a", "a", apex_rt=10.00),
        _owner("sample-b", "b", apex_rt=10.75),
        _owner("sample-c", "c", apex_rt=11.50),
        _owner("sample-d", "d", apex_rt=12.25),
    )

    features = cluster_sample_local_owners(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset(
            {"sample-a", "sample-b", "sample-c", "sample-d", "sample-e"},
        ),
        drift_lookup=_DriftLookup(
            deltas={},
            orders={
                "sample-a": 1,
                "sample-b": 2,
                "sample-c": 3,
                "sample-d": 4,
            },
        ),
    )

    assert all(
        "owner_injection_trajectory" not in feature.evidence for feature in features
    )


def test_trajectory_hard_envelope_is_permutation_invariant_at_ppm_boundary() -> None:
    low = _owner("sample-a", "low", precursor_mz=100.0)
    high = _owner("sample-b", "high", precursor_mz=100.001000005)
    config = AlignmentConfig(preferred_ppm=10.0, max_ppm=10.0)

    assert not peak_groups_module._group_passes_hard_envelope(
        (low, high),
        config=config,
    )
    assert not peak_groups_module._group_passes_hard_envelope(
        (high, low),
        config=config,
    )


def test_owner_clustering_does_not_restore_trajectory_across_hard_bridge() -> None:
    sample_universe = frozenset({"sample-a", "sample-b", "sample-c", "sample-d"})
    injection_order = {
        "sample-a": 1,
        "sample-b": 2,
        "sample-c": 3,
        "sample-d": 4,
    }
    cases = (
        (
            (
                _owner("sample-a", "a", apex_rt=10.00, precursor_mz=500.0000),
                _owner("sample-b", "b", apex_rt=10.10, precursor_mz=500.0040),
                _owner("sample-c", "c", apex_rt=10.20, precursor_mz=500.0080),
            ),
            AlignmentConfig(preferred_ppm=10.0, max_ppm=10.0),
        ),
        (
            (
                _owner("sample-a", "a", apex_rt=10.00, product_mz=383.9526),
                _owner("sample-b", "b", apex_rt=10.10, product_mz=383.9566),
                _owner("sample-c", "c", apex_rt=10.20, product_mz=383.9606),
            ),
            AlignmentConfig(product_mz_tolerance_ppm=15.0),
        ),
        (
            (
                _owner("sample-a", "a", apex_rt=10.00, observed_loss=116.0474),
                _owner("sample-b", "b", apex_rt=10.10, observed_loss=116.0486),
                _owner("sample-c", "c", apex_rt=10.20, observed_loss=116.0498),
            ),
            AlignmentConfig(observed_loss_tolerance_ppm=15.0),
        ),
    )

    for owners, config in cases:
        features = cluster_sample_local_owners(
            owners,
            config=config,
            sample_universe=sample_universe,
            injection_order=injection_order,
        )

        assert [feature.event_cluster_ids for feature in features] == [
            ("OWN-sample-a-a", "OWN-sample-b-b"),
            ("OWN-sample-c-c",),
        ]


def test_owner_clustering_keeps_parallel_injection_trajectories_separate() -> None:
    owners = (
        _owner("sample-a", "low", apex_rt=10.00),
        _owner("sample-b", "low", apex_rt=10.75),
        _owner("sample-c", "low", apex_rt=11.50),
        _owner("sample-d", "low", apex_rt=12.25),
        _owner("sample-a", "high", apex_rt=12.50),
        _owner("sample-b", "high", apex_rt=13.25),
        _owner("sample-c", "high", apex_rt=14.00),
        _owner("sample-d", "high", apex_rt=14.75),
    )
    features = cluster_sample_local_owners(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset(
            {"sample-a", "sample-b", "sample-c", "sample-d", "sample-e"},
        ),
        injection_order={
            "sample-a": 1,
            "sample-b": 2,
            "sample-c": 3,
            "sample-d": 4,
            "sample-e": 5,
        },
    )

    assert [feature.event_cluster_ids for feature in features] == [
        (
            "OWN-sample-a-low",
            "OWN-sample-b-low",
            "OWN-sample-c-low",
            "OWN-sample-d-low",
        ),
        (
            "OWN-sample-a-high",
            "OWN-sample-b-high",
            "OWN-sample-c-high",
            "OWN-sample-d-high",
        ),
    ]
    assert {feature.evidence for feature in features} == {
        "owner_injection_trajectory;owner_count=4",
    }


def test_owner_clustering_separates_fixture_cohort_rt_outlier() -> None:
    owners = tuple(
        _owner(f"sample-{index}", "main", apex_rt=10.0 + index / 10.0)
        for index in range(1, 8)
    ) + (_owner("sample-8", "outlier", apex_rt=20.0),)
    features = cluster_sample_local_owners(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset(owner.sample_stem for owner in owners),
        injection_order={
            owner.sample_stem: index for index, owner in enumerate(owners)
        },
    )

    assert [len(feature.owners) for feature in features] == [7, 1]
    assert features[1].event_cluster_ids == ("OWN-sample-8-outlier",)


def test_owner_clustering_connects_full_batch_with_moderate_seed_edge() -> None:
    raw_owners = (
        _owner("sample-a", "a", apex_rt=10.00),
        _owner(
            "sample-b",
            "b",
            apex_rt=10.10,
            evidence_score=53,
            seed_event_count=1,
        ),
    )
    owners = tuple(
        replace(
            owner,
            selected_integration=replace(
                owner.selected_integration,
                baseline_residual_mad=1.0,
            ),
        )
        for owner in raw_owners
    )
    config = AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0)

    assert evaluate_owner_edge(owners[0], owners[1], config=config).decision == (
        "weak_edge"
    )
    initial_features = cluster_sample_local_owners(owners, config=config)
    assert [feature.event_cluster_ids for feature in initial_features] == [
        ("OWN-sample-a-a", "OWN-sample-b-b"),
    ]
    assert initial_features[0].evidence == (
        "owner_complete_link_moderate;owner_count=2"
    )
    assert initial_features[0].disposition == "established"
    assert initial_features[0].review_only is False

    features = cluster_sample_local_owners(
        owners,
        config=config,
        sample_universe=frozenset({"sample-a", "sample-b"}),
    )

    assert [feature.event_cluster_ids for feature in features] == [
        (
            "OWN-sample-a-a",
            "OWN-sample-b-b",
        )
    ]
    assert features[0].evidence == "owner_complete_link_moderate;owner_count=2"


def test_moderate_complete_link_keeps_incompatible_pairs_separate() -> None:
    base = _owner(
        "sample-a", "base", apex_rt=10.0, evidence_score=45, seed_event_count=1,
    )
    controls = (
        _owner(
            "sample-b", "weak-seed", apex_rt=10.1,
            evidence_score=35, seed_event_count=1,
        ),
        replace(
            _owner(
                "sample-c", "ambiguous", apex_rt=10.1,
                evidence_score=45, seed_event_count=1,
            ),
            assignment_reason="ambiguous_nearby",
        ),
        _owner(
            "sample-d", "rt-outside", apex_rt=11.2,
            evidence_score=45, seed_event_count=1,
        ),
        _owner(
            "sample-e", "mass-conflict", apex_rt=10.1, precursor_mz=501.0,
            evidence_score=45, seed_event_count=1,
        ),
        _owner(
            "sample-f", "product-conflict", apex_rt=10.1, product_mz=390.0,
            evidence_score=45, seed_event_count=1,
        ),
        _owner(
            "sample-a", "same-sample", apex_rt=10.1,
            evidence_score=45, seed_event_count=1,
        ),
    )

    for control in controls:
        features = cluster_sample_local_owners(
            (base, control),
            config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        )
        assert len(features) == 2, control.owner_id


def test_moderate_complete_link_label_reuses_cached_pair_evidence() -> None:
    owners = (
        _owner(
            "sample-a", "a", apex_rt=10.0,
            evidence_score=45, seed_event_count=1,
        ),
        _owner(
            "sample-b", "b", apex_rt=10.1,
            evidence_score=45, seed_event_count=1,
        ),
    )
    calls = 0

    def evaluator(left, right, *, config, drift_lookup):
        nonlocal calls
        calls += 1
        return evaluate_owner_edge(
            left,
            right,
            config=config,
            drift_lookup=drift_lookup,
        )

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(),
        edge_evaluator=evaluator,
    )

    assert calls == 1
    assert hypotheses[0].evidence == "owner_complete_link_moderate;owner_count=2"


def test_owner_clustering_keeps_complete_full_batch_on_complete_link() -> None:
    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", apex_rt=10.00),
            _owner("sample-b", "b", apex_rt=10.10),
        ),
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset({"sample-a", "sample-b"}),
    )

    assert [feature.event_cluster_ids for feature in features] == [
        (
            "OWN-sample-a-a",
            "OWN-sample-b-b",
        )
    ]
    assert features[0].evidence == "owner_complete_link;owner_count=2"


def test_owner_clustering_keeps_full_batch_split_across_blocked_mz_edge() -> None:
    owners = (
        _owner(
            "sample-a",
            "a",
            precursor_mz=500.000,
            product_mz=383.9526,
        ),
        _owner(
            "sample-b",
            "b",
            precursor_mz=500.004,
            product_mz=383.9566,
        ),
        _owner(
            "sample-c",
            "c",
            precursor_mz=500.008,
            product_mz=383.9606,
        ),
    )
    config = AlignmentConfig(
        preferred_ppm=10.0,
        max_ppm=10.0,
        product_mz_tolerance_ppm=15.0,
    )

    assert evaluate_owner_edge(owners[0], owners[1], config=config).decision == (
        "strong_edge"
    )
    assert evaluate_owner_edge(owners[1], owners[2], config=config).decision == (
        "strong_edge"
    )
    assert evaluate_owner_edge(owners[0], owners[2], config=config).decision == (
        "blocked_edge"
    )

    features = cluster_sample_local_owners(
        owners,
        config=config,
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c"}),
    )

    assert [feature.event_cluster_ids for feature in features] == [
        ("OWN-sample-a-a", "OWN-sample-b-b"),
        ("OWN-sample-c-c",),
    ]


def test_connected_link_grouping_is_invariant_to_precursor_bucket_origin() -> None:
    config = AlignmentConfig(
        preferred_ppm=10.0,
        max_ppm=10.0,
        product_mz_tolerance_ppm=15.0,
    )

    def connected_owner_ids(
        precursor_mzs: tuple[float, ...],
    ) -> tuple[tuple[str, ...], ...]:
        owners = [
            _owner(
                f"sample-{index}",
                str(index),
                precursor_mz=precursor_mz,
            )
            for index, precursor_mz in enumerate(precursor_mzs)
        ]
        groups = peak_groups_module._connected_link_groups(
            owners,
            config,
            drift_lookup=None,
            edge_evidence_sink=None,
            edge_cache={},
            edge_evaluator=evaluate_owner_edge,
        )
        return tuple(tuple(owner.owner_id for owner in group) for group in groups)

    expected = (tuple(f"OWN-sample-{index}-{index}" for index in range(5)),)
    assert (
        connected_owner_ids((499.992, 499.996, 500.000, 500.004, 500.008)) == expected
    )
    assert (
        connected_owner_ids((500.192, 500.196, 500.200, 500.204, 500.208)) == expected
    )


def test_owner_clustering_does_not_single_link_merge_bridge_groups() -> None:
    owners = (
        _owner(
            "sample-a",
            "a",
            precursor_mz=500.000,
            product_mz=383.9526,
            apex_rt=10.00,
        ),
        _owner(
            "sample-b",
            "b",
            precursor_mz=500.004,
            product_mz=383.9566,
            apex_rt=10.20,
        ),
        _owner(
            "sample-c",
            "c",
            precursor_mz=500.008,
            product_mz=383.9606,
            apex_rt=10.10,
        ),
    )

    features = cluster_sample_local_owners(
        owners,
        config=AlignmentConfig(
            preferred_rt_sec=60.0,
            max_rt_sec=180.0,
            preferred_ppm=10.0,
            max_ppm=10.0,
            product_mz_tolerance_ppm=15.0,
        ),
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c"}),
    )

    assert [feature.event_cluster_ids for feature in features] == [
        ("OWN-sample-a-a",),
        ("OWN-sample-b-b", "OWN-sample-c-c"),
    ]


def test_owner_clustering_merges_groups_when_owner_matches_both() -> None:
    owners = (
        _owner(
            "sample-a",
            "a",
            apex_rt=10.00,
        ),
        _owner(
            "sample-b",
            "b",
            apex_rt=10.10,
        ),
        _owner(
            "sample-c",
            "c",
            apex_rt=10.20,
        ),
    )
    config = AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0)

    def evaluator(left, right, *, config, drift_lookup):
        edge = evaluate_owner_edge(
            left,
            right,
            config=config,
            drift_lookup=drift_lookup,
        )
        if {left.owner_id, right.owner_id} == {
            "OWN-sample-a-a",
            "OWN-sample-b-b",
        }:
            return replace(
                edge,
                decision="weak_edge",
                owner_quality="weak",
                reason="weak: bridge admission withheld",
            )
        return edge

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=config,
        sample_universe=frozenset(
            {"sample-a", "sample-b", "sample-c"},
        ),
        edge_evaluator=evaluator,
    )

    assert [hypothesis.owner_ids for hypothesis in hypotheses] == [
        (
            "OWN-sample-a-a",
            "OWN-sample-b-b",
            "OWN-sample-c-c",
        )
    ]


def test_owner_clustering_keeps_incomplete_merge_on_complete_link_partition() -> None:
    owners = (
        _owner("sample-a", "a", apex_rt=10.00),
        _owner("sample-b", "b", apex_rt=10.10),
        _owner("sample-c", "c", apex_rt=10.20),
    )
    config = AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0)

    def evaluator(left, right, *, config, drift_lookup):
        edge = evaluate_owner_edge(
            left,
            right,
            config=config,
            drift_lookup=drift_lookup,
        )
        if {left.owner_id, right.owner_id} == {
            "OWN-sample-a-a",
            "OWN-sample-b-b",
        }:
            return replace(
                edge,
                decision="weak_edge",
                owner_quality="weak",
                reason="weak: complete-link partition boundary",
            )
        return edge

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=config,
        sample_universe=frozenset(
            {"sample-a", "sample-b", "sample-c", "sample-d"},
        ),
        edge_evaluator=evaluator,
    )

    assert [hypothesis.owner_ids for hypothesis in hypotheses] == [
        ("OWN-sample-a-a", "OWN-sample-b-b", "OWN-sample-c-c"),
    ]
    assert "owner_bounded_continuity" in hypotheses[0].evidence


def test_targeted_istd_anchor_binds_generic_injection_trajectory_provenance() -> None:
    owners = tuple(
        _owner(
            f"sample-{index}",
            f"o{index}",
            apex_rt=8.0 + 0.8 * index,
            neutral_loss_tag="DNA_dR",
        )
        for index in range(8)
    )
    config = AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0)
    sample_universe = frozenset({f"sample-{index}" for index in range(10)})
    injection_order = {f"sample-{index}": index + 1 for index in range(8)}

    def evaluator(left, right, *, config, drift_lookup):
        edge = evaluate_owner_edge(
            left,
            right,
            config=config,
            drift_lookup=drift_lookup,
        )
        if {left.sample_stem, right.sample_stem} == {
            "sample-3",
            "sample-4",
        }:
            return replace(
                edge,
                decision="weak_edge",
                failure_reason="",
                owner_quality="clean",
                seed_support_level="moderate",
                reason="weak: complete-link trajectory boundary",
            )
        return edge

    target = Target(
        label="synthetic-dna-istd",
        mz=500.0,
        rt_min=7.0,
        rt_max=20.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
    )
    anchor = build_targeted_istd_identity_anchor(
        mint_target_list_snapshot(
            (target,),
            identity_namespace="test-targeted-istd",
        ),
        observed_labels=(target.label,),
    )
    without_anchor = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=config,
        sample_universe=sample_universe,
        injection_order=injection_order,
        drift_lookup=DriftEvidenceLookup(points=()),
        edge_evaluator=evaluator,
    )
    with_anchor = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=config,
        sample_universe=sample_universe,
        injection_order=injection_order,
        drift_lookup=DriftEvidenceLookup(
            points=(),
            istd_identity_anchor=anchor,
        ),
        edge_evaluator=evaluator,
    )

    assert len(without_anchor) == 1
    assert without_anchor[0].evidence == "owner_injection_trajectory;owner_count=8"
    assert len(with_anchor) == 1
    assert [item.owner_ids for item in with_anchor] == [
        item.owner_ids for item in without_anchor
    ]
    assert with_anchor[0].evidence == "owner_targeted_istd_trajectory;owner_count=8"
    assert len(with_anchor[0].owners) == 8


def test_targeted_istd_anchor_reconciles_loss_fragmented_sample_disjoint_groups() -> (
    None
):
    owners = (
        _owner(
            "sample-a",
            "a",
            apex_rt=8.0,
            neutral_loss_tag="DNA_dR",
            product_mz=383.955,
            observed_loss=116.045,
        ),
        _owner(
            "sample-b",
            "b",
            apex_rt=8.2,
            neutral_loss_tag="DNA_dR",
            product_mz=383.952,
            observed_loss=116.048,
        ),
        _owner(
            "sample-c",
            "c",
            apex_rt=8.4,
            neutral_loss_tag="DNA_dR",
            product_mz=383.950,
            observed_loss=116.050,
        ),
    )
    target = Target(
        label="synthetic-dna-istd",
        mz=500.0,
        rt_min=7.0,
        rt_max=12.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
    )
    anchor = build_targeted_istd_identity_anchor(
        mint_target_list_snapshot(
            (target,),
            identity_namespace="test-targeted-istd-reconciliation",
        ),
        observed_labels=(target.label,),
    )
    config = AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0)
    sample_universe = frozenset({"sample-a", "sample-b", "sample-c", "sample-d"})
    injection_order = {"sample-a": 1, "sample-b": 2, "sample-c": 3}

    without_anchor = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=config,
        sample_universe=sample_universe,
        injection_order=injection_order,
        drift_lookup=DriftEvidenceLookup(points=()),
    )
    with_anchor = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=config,
        sample_universe=sample_universe,
        injection_order=injection_order,
        drift_lookup=DriftEvidenceLookup(
            points=(),
            istd_identity_anchor=anchor,
        ),
    )

    assert len(without_anchor) == 2
    assert [item.owner_ids for item in with_anchor] == [
        ("OWN-sample-a-a", "OWN-sample-b-b", "OWN-sample-c-c"),
    ]
    assert with_anchor[0].evidence == "owner_targeted_istd_trajectory;owner_count=3"


def test_targeted_istd_reconciliation_keeps_precursor_hard_conflict_separate() -> None:
    owners = (
        _owner(
            "sample-a",
            "a",
            apex_rt=8.0,
            neutral_loss_tag="DNA_dR",
            precursor_mz=499.991,
        ),
        _owner(
            "sample-b",
            "b",
            apex_rt=8.2,
            neutral_loss_tag="DNA_dR",
            precursor_mz=500.009,
        ),
    )
    target = Target(
        label="synthetic-dna-istd",
        mz=500.0,
        rt_min=7.0,
        rt_max=12.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
    )
    anchor = build_targeted_istd_identity_anchor(
        mint_target_list_snapshot(
            (target,),
            identity_namespace="test-targeted-istd-precursor-conflict",
        ),
        observed_labels=(target.label,),
    )

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(
            preferred_rt_sec=60.0,
            max_rt_sec=180.0,
            max_ppm=20.0,
        ),
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c"}),
        injection_order={"sample-a": 1, "sample-b": 2},
        drift_lookup=DriftEvidenceLookup(
            points=(),
            istd_identity_anchor=anchor,
        ),
    )

    assert len(hypotheses) == 2
    assert all(
        "owner_targeted_istd_trajectory" not in item.evidence for item in hypotheses
    )


def test_targeted_istd_reconciliation_keeps_rt_discontinuity_separate() -> None:
    owners = (
        _owner(
            "sample-a",
            "a",
            apex_rt=8.0,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.045,
        ),
        _owner(
            "sample-b",
            "b",
            apex_rt=9.5,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.050,
        ),
    )
    target = Target(
        label="synthetic-dna-istd",
        mz=500.0,
        rt_min=7.0,
        rt_max=12.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
    )
    anchor = build_targeted_istd_identity_anchor(
        mint_target_list_snapshot(
            (target,),
            identity_namespace="test-targeted-istd-rt-discontinuity",
        ),
        observed_labels=(target.label,),
    )

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c"}),
        injection_order={"sample-a": 1, "sample-b": 2},
        drift_lookup=DriftEvidenceLookup(
            points=(),
            istd_identity_anchor=anchor,
        ),
    )

    assert len(hypotheses) == 2
    assert all(
        "owner_targeted_istd_trajectory" not in item.evidence for item in hypotheses
    )


def test_targeted_istd_reconciliation_demotes_same_sample_secondary_group() -> None:
    owners = (
        _owner(
            "sample-a",
            "a",
            apex_rt=8.0,
            neutral_loss_tag="DNA_dR",
            product_mz=383.952,
            observed_loss=116.048,
        ),
        _owner(
            "sample-b",
            "b",
            apex_rt=8.1,
            neutral_loss_tag="DNA_dR",
            product_mz=383.952,
            observed_loss=116.048,
        ),
        _owner(
            "sample-c",
            "c",
            apex_rt=8.2,
            neutral_loss_tag="DNA_dR",
            product_mz=383.952,
            observed_loss=116.048,
        ),
        _owner(
            "sample-d",
            "d",
            apex_rt=8.3,
            neutral_loss_tag="DNA_dR",
            product_mz=383.955,
            observed_loss=116.045,
        ),
        _owner(
            "sample-e",
            "e",
            apex_rt=8.4,
            neutral_loss_tag="DNA_dR",
            product_mz=383.955,
            observed_loss=116.045,
        ),
        _owner(
            "sample-b",
            "b-secondary",
            apex_rt=8.8,
            neutral_loss_tag="DNA_dR",
            product_mz=383.948,
            observed_loss=116.052,
        ),
        _owner(
            "sample-a",
            "a-secondary",
            apex_rt=9.0,
            neutral_loss_tag="DNA_dR",
            product_mz=383.948,
            observed_loss=116.052,
        ),
    )
    target = Target(
        label="synthetic-dna-istd",
        mz=500.0,
        rt_min=7.0,
        rt_max=12.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
    )
    anchor = build_targeted_istd_identity_anchor(
        mint_target_list_snapshot(
            (target,),
            identity_namespace="test-targeted-istd-secondary-group",
        ),
        observed_labels=(target.label,),
    )

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset(
            {
                "sample-a",
                "sample-b",
                "sample-c",
                "sample-d",
                "sample-e",
                "sample-f",
            }
        ),
        injection_order={
            "sample-a": 1,
            "sample-b": 2,
            "sample-c": 3,
            "sample-d": 4,
            "sample-e": 5,
        },
        drift_lookup=DriftEvidenceLookup(
            points=(),
            istd_identity_anchor=anchor,
        ),
    )

    assert len(hypotheses) == 2
    winner = next(item for item in hypotheses if not item.review_only)
    loser = next(item for item in hypotheses if item.review_only)
    assert winner.owner_ids == (
        "OWN-sample-a-a",
        "OWN-sample-b-b",
        "OWN-sample-c-c",
        "OWN-sample-d-d",
        "OWN-sample-e-e",
    )
    assert loser.owner_ids == (
        "OWN-sample-b-b-secondary",
        "OWN-sample-a-a-secondary",
    )
    assert "owner_duplicate_group_resolved_loser" in loser.evidence


def test_targeted_istd_reconciliation_accepts_product_mz_measurement_split() -> None:
    owners = (
        _owner(
            "sample-a",
            "a",
            apex_rt=8.0,
            neutral_loss_tag="DNA_dR",
            product_mz=383.940,
        ),
        _owner(
            "sample-b",
            "b",
            apex_rt=8.2,
            neutral_loss_tag="DNA_dR",
            product_mz=383.960,
        ),
    )

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c"}),
        injection_order={"sample-a": 1, "sample-b": 2},
        drift_lookup=DriftEvidenceLookup(
            points=(),
            istd_identity_anchor=_targeted_istd_anchor(
                identity_namespace="test-targeted-istd-product-split"
            ),
        ),
    )

    assert len(hypotheses) == 1
    assert hypotheses[0].evidence == "owner_targeted_istd_trajectory;owner_count=2"


def test_targeted_istd_reconciliation_accepts_tail_supported_owner() -> None:
    owners = (
        _owner(
            "sample-a",
            "a",
            apex_rt=8.0,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.045,
        ),
        replace(
            _owner(
                "sample-b",
                "b",
                apex_rt=8.2,
                neutral_loss_tag="DNA_dR",
                observed_loss=116.050,
            ),
            assignment_reason="owner_tail_assignment",
        ),
    )

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c"}),
        injection_order={"sample-a": 1, "sample-b": 2},
        drift_lookup=DriftEvidenceLookup(
            points=(),
            istd_identity_anchor=_targeted_istd_anchor(
                identity_namespace="test-targeted-istd-tail-supported"
            ),
        ),
    )

    assert len(hypotheses) == 1
    assert hypotheses[0].evidence == "owner_targeted_istd_trajectory;owner_count=2"


def test_targeted_istd_reconciliation_accepts_moderate_tail_without_mass_split() -> (
    None
):
    owners = (
        _owner(
            "sample-a",
            "a",
            apex_rt=8.0,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.047,
        ),
        replace(
            _owner(
                "sample-b",
                "b",
                apex_rt=8.2,
                neutral_loss_tag="DNA_dR",
                observed_loss=116.047,
            ),
            assignment_reason="owner_tail_assignment",
        ),
    )

    def moderate_tail_evaluator(left, right, *, config, drift_lookup):
        return replace(
            evaluate_owner_edge(
                left,
                right,
                config=config,
                drift_lookup=drift_lookup,
            ),
            decision="weak_edge",
            failure_reason="",
            owner_quality="tail_supported",
            seed_support_level="moderate",
        )

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c"}),
        injection_order={"sample-a": 1, "sample-b": 2},
        drift_lookup=DriftEvidenceLookup(
            points=(),
            istd_identity_anchor=_targeted_istd_anchor(
                identity_namespace="test-targeted-istd-moderate-tail"
            ),
        ),
        edge_evaluator=moderate_tail_evaluator,
    )

    assert len(hypotheses) == 1
    assert hypotheses[0].evidence == "owner_targeted_istd_trajectory;owner_count=2"


def test_targeted_istd_reconciliation_rejects_weak_owner_edge() -> None:
    owners = (
        _owner(
            "sample-a",
            "a",
            apex_rt=8.0,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.045,
        ),
        _owner(
            "sample-b",
            "b",
            apex_rt=8.2,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.050,
        ),
    )

    def weak_evaluator(left, right, *, config, drift_lookup):
        return replace(
            evaluate_owner_edge(
                left,
                right,
                config=config,
                drift_lookup=drift_lookup,
            ),
            owner_quality="weak",
        )

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c"}),
        injection_order={"sample-a": 1, "sample-b": 2},
        drift_lookup=DriftEvidenceLookup(
            points=(),
            istd_identity_anchor=_targeted_istd_anchor(
                identity_namespace="test-targeted-istd-weak-owner"
            ),
        ),
        edge_evaluator=weak_evaluator,
    )

    assert len(hypotheses) == 2
    assert all(
        "owner_targeted_istd_trajectory" not in hypothesis.evidence
        for hypothesis in hypotheses
    )


def test_targeted_istd_reconciliation_preserves_multiple_compatible_tracks() -> None:
    owners = (
        _owner(
            "sample-a",
            "left",
            apex_rt=8.0,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.044,
        ),
        _owner(
            "sample-b",
            "left",
            apex_rt=8.05,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.044,
        ),
        _owner(
            "sample-c",
            "left",
            apex_rt=8.1,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.044,
        ),
        _owner(
            "sample-e",
            "ambiguous-a",
            apex_rt=8.75,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.047,
        ),
        _owner(
            "sample-f",
            "ambiguous-b",
            apex_rt=8.8,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.047,
        ),
        _owner(
            "sample-d",
            "right",
            apex_rt=9.5,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.050,
        ),
    )

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset(
            {
                "sample-a",
                "sample-b",
                "sample-c",
                "sample-d",
                "sample-e",
                "sample-f",
                "sample-g",
            }
        ),
        injection_order={
            "sample-a": 1,
            "sample-b": 2,
            "sample-c": 3,
            "sample-d": 4,
            "sample-e": 5,
            "sample-f": 6,
        },
        drift_lookup=DriftEvidenceLookup(
            points=(),
            istd_identity_anchor=_targeted_istd_anchor(
                identity_namespace="test-targeted-istd-multiple-tracks"
            ),
        ),
    )

    assert len(hypotheses) == 3
    assert sorted(len(hypothesis.owner_ids) for hypothesis in hypotheses) == [1, 2, 3]


def test_targeted_istd_reconciliation_uses_complete_union_for_continuity() -> None:
    groups = (
        (
            _owner(
                "sample-1",
                "a1",
                apex_rt=8.0,
                neutral_loss_tag="DNA_dR",
                observed_loss=116.044,
            ),
            _owner(
                "sample-4",
                "a4",
                apex_rt=9.8,
                neutral_loss_tag="DNA_dR",
                observed_loss=116.044,
            ),
            _owner(
                "sample-10",
                "a10",
                apex_rt=8.9,
                neutral_loss_tag="DNA_dR",
                observed_loss=116.044,
            ),
        ),
        (
            _owner(
                "sample-2",
                "b2",
                apex_rt=8.6,
                neutral_loss_tag="DNA_dR",
                observed_loss=116.047,
            ),
        ),
        (
            _owner(
                "sample-3",
                "c3",
                apex_rt=9.2,
                neutral_loss_tag="DNA_dR",
                observed_loss=116.050,
            ),
        ),
    )
    drift_lookup = DriftEvidenceLookup(
        points=(),
        istd_identity_anchor=_targeted_istd_anchor(
            identity_namespace="test-targeted-istd-complete-union"
        ),
    )

    reconciled, targeted_owner_sets, validated_owner_sets = (
        peak_groups_module._bind_targeted_istd_trajectory_provenance(
            groups,
            injection_trajectory_owner_sets=frozenset(),
            injection_order={
                "sample-1": 1,
                "sample-2": 2,
                "sample-3": 3,
                "sample-4": 4,
                "sample-10": 10,
            },
            config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
            drift_lookup=drift_lookup,
            edge_evidence_sink=None,
            edge_cache={},
            edge_evaluator=evaluate_owner_edge,
        )
    )

    assert len(reconciled) == 1
    expected_owner_set = frozenset(
        owner.owner_id for group in groups for owner in group
    )
    assert targeted_owner_sets == frozenset({expected_owner_set})
    assert validated_owner_sets == frozenset({expected_owner_set})


def test_targeted_istd_reconciliation_excludes_unconnected_hard_compatible_group() -> (
    None
):
    groups = (
        (
            _owner(
                "sample-1",
                "near-a",
                apex_rt=8.0,
                neutral_loss_tag="DNA_dR",
                observed_loss=116.044,
            ),
        ),
        (
            _owner(
                "sample-2",
                "near-b",
                apex_rt=8.2,
                neutral_loss_tag="DNA_dR",
                observed_loss=116.047,
            ),
        ),
        (
            _owner(
                "sample-3",
                "far",
                apex_rt=11.0,
                neutral_loss_tag="DNA_dR",
                observed_loss=116.050,
            ),
        ),
    )
    drift_lookup = DriftEvidenceLookup(
        points=(),
        istd_identity_anchor=_targeted_istd_anchor(
            identity_namespace="test-targeted-istd-unconnected-hard-compatible"
        ),
    )

    reconciled, _targeted_owner_sets, _validated_owner_sets = (
        peak_groups_module._bind_targeted_istd_trajectory_provenance(
            groups,
            injection_trajectory_owner_sets=frozenset(),
            injection_order={"sample-1": 1, "sample-2": 2, "sample-3": 3},
            config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
            drift_lookup=drift_lookup,
            edge_evidence_sink=None,
            edge_cache={},
            edge_evaluator=evaluate_owner_edge,
        )
    )

    assert sorted(len(group) for group in reconciled) == [1, 2]


def test_targeted_istd_duplicate_demotion_accepts_natural_primary_trajectory() -> None:
    owners = (
        _owner(
            "sample-a",
            "a",
            apex_rt=8.0,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.048,
        ),
        _owner(
            "sample-b",
            "b",
            apex_rt=8.1,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.048,
        ),
        _owner(
            "sample-c",
            "c",
            apex_rt=8.2,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.048,
        ),
        _owner(
            "sample-b",
            "b-secondary",
            apex_rt=8.8,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.052,
        ),
        _owner(
            "sample-c",
            "c-secondary",
            apex_rt=8.9,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.052,
        ),
    )

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c", "sample-d"}),
        injection_order={"sample-a": 1, "sample-b": 2, "sample-c": 3},
        drift_lookup=DriftEvidenceLookup(
            points=(),
            istd_identity_anchor=_targeted_istd_anchor(
                identity_namespace="test-targeted-istd-natural-primary"
            ),
        ),
    )

    assert len(hypotheses) == 2
    assert sum(hypothesis.review_only for hypothesis in hypotheses) == 1
    winner = next(hypothesis for hypothesis in hypotheses if not hypothesis.review_only)
    assert winner.owner_ids == (
        "OWN-sample-a-a",
        "OWN-sample-b-b",
        "OWN-sample-c-c",
    )


def test_targeted_istd_duplicate_demotion_requires_injection_trajectory() -> None:
    owners = (
        _owner(
            "sample-a",
            "a",
            apex_rt=8.0,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.045,
        ),
        _owner(
            "sample-b",
            "b",
            apex_rt=8.1,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.045,
        ),
        _owner(
            "sample-c",
            "c",
            apex_rt=8.2,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.045,
        ),
        _owner(
            "sample-b",
            "b-secondary",
            apex_rt=8.8,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.052,
        ),
        _owner(
            "sample-c",
            "c-secondary",
            apex_rt=8.9,
            neutral_loss_tag="DNA_dR",
            observed_loss=116.052,
        ),
    )

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c", "sample-d"}),
        injection_order=None,
        drift_lookup=DriftEvidenceLookup(
            points=(),
            istd_identity_anchor=_targeted_istd_anchor(
                identity_namespace="test-targeted-istd-no-injection-order"
            ),
        ),
    )

    assert len(hypotheses) == 2
    assert all(not hypothesis.review_only for hypothesis in hypotheses)


def test_targeted_istd_trajectory_keeps_same_sample_or_blocked_edge_separate() -> None:
    owners = (
        _owner("sample-a", "a", apex_rt=8.0, neutral_loss_tag="DNA_dR"),
        _owner("sample-a", "a2", apex_rt=8.8, neutral_loss_tag="DNA_dR"),
        _owner("sample-b", "b", apex_rt=9.6, neutral_loss_tag="DNA_dR"),
    )
    target = Target(
        label="synthetic-dna-istd",
        mz=500.0,
        rt_min=7.0,
        rt_max=12.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
    )
    anchor = build_targeted_istd_identity_anchor(
        mint_target_list_snapshot(
            (target,),
            identity_namespace="test-targeted-istd",
        ),
        observed_labels=(target.label,),
    )
    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c"}),
        injection_order={"sample-a": 1, "sample-b": 2},
        drift_lookup=DriftEvidenceLookup(
            points=(),
            istd_identity_anchor=anchor,
        ),
    )

    assert len(hypotheses) >= 2
    assert all(
        "owner_targeted_istd_trajectory" not in item.evidence for item in hypotheses
    )


def test_targeted_istd_anchor_keeps_blocked_trajectory_membership_and_label() -> None:
    owners = (
        _owner("sample-a", "a", apex_rt=8.0, neutral_loss_tag="DNA_dR"),
        _owner("sample-b", "b", apex_rt=8.4, neutral_loss_tag="DNA_dR"),
        _owner("sample-c", "c", apex_rt=8.8, neutral_loss_tag="DNA_dR"),
    )
    target = Target(
        label="synthetic-dna-istd",
        mz=500.0,
        rt_min=7.0,
        rt_max=12.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
    )
    anchor = build_targeted_istd_identity_anchor(
        mint_target_list_snapshot(
            (target,),
            identity_namespace="test-targeted-istd",
        ),
        observed_labels=(target.label,),
    )

    def evaluator(left, right, *, config, drift_lookup):
        edge = evaluate_owner_edge(
            left,
            right,
            config=config,
            drift_lookup=drift_lookup,
        )
        if {left.sample_stem, right.sample_stem} == {"sample-b", "sample-c"}:
            return replace(
                edge,
                decision="blocked_edge",
                failure_reason="identity_conflict",
                reason="blocked: crossing trajectory",
            )
        return replace(
            edge,
            decision="weak_edge",
            failure_reason="",
            owner_quality="clean",
            seed_support_level="moderate",
            reason="weak: crossing candidate",
        )

    without_anchor = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c", "sample-d"}),
        injection_order={
            "sample-a": 1,
            "sample-b": 2,
            "sample-c": 3,
        },
        drift_lookup=DriftEvidenceLookup(points=()),
        edge_evaluator=evaluator,
    )
    with_anchor = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c", "sample-d"}),
        injection_order={
            "sample-a": 1,
            "sample-b": 2,
            "sample-c": 3,
        },
        drift_lookup=DriftEvidenceLookup(
            points=(),
            istd_identity_anchor=anchor,
        ),
        edge_evaluator=evaluator,
    )

    assert [item.owner_ids for item in with_anchor] == [
        item.owner_ids for item in without_anchor
    ]
    assert all(
        "owner_targeted_istd_trajectory" not in item.evidence for item in with_anchor
    )


def test_owner_clustering_keeps_rt_discontinuity_separate() -> None:
    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", apex_rt=10.00),
            _owner("sample-b", "b", apex_rt=10.75),
            _owner("sample-c", "c", apex_rt=11.90),
        ),
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c"}),
        injection_order={"sample-a": 1, "sample-b": 2, "sample-c": 3},
    )

    assert [feature.event_cluster_ids for feature in features] == [
        ("OWN-sample-a-a", "OWN-sample-b-b"),
        ("OWN-sample-c-c",),
    ]


def test_owner_clustering_keeps_same_sample_recurrence_separate() -> None:
    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", apex_rt=10.00),
            _owner("sample-b", "b", apex_rt=10.75),
            _owner("sample-a", "c", apex_rt=11.50),
        ),
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset({"sample-a", "sample-b"}),
        injection_order={"sample-a": 1, "sample-b": 2},
    )

    assert [feature.event_cluster_ids for feature in features] == [
        ("OWN-sample-a-a", "OWN-sample-b-b"),
        ("OWN-sample-a-c",),
    ]


def test_owner_clustering_uses_drift_prior_for_strong_edge_over_strict_rt() -> None:
    edge_evidence = []

    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", apex_rt=10.0),
            _owner("sample-b", "b", apex_rt=11.2),
        ),
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        drift_lookup=_DriftLookup(
            deltas={"sample-a": 0.0, "sample-b": 0.25},
            orders={},
        ),
        edge_evidence_sink=edge_evidence,
    )

    assert len(features) == 1
    assert features[0].event_cluster_ids == ("OWN-sample-a-a", "OWN-sample-b-b")
    assert [edge.decision for edge in edge_evidence] == ["strong_edge"]


def test_owner_clustering_preserves_weak_no_drift_rt_split() -> None:
    edge_evidence = []

    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", apex_rt=10.0),
            _owner("sample-b", "b", apex_rt=11.2),
        ),
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        edge_evidence_sink=edge_evidence,
    )

    assert len(features) == 2
    assert [edge.decision for edge in edge_evidence] == ["weak_edge"]


def test_owner_clustering_edge_evidence_sink_keeps_unique_sorted_pairs() -> None:
    edge_evidence = []

    cluster_sample_local_owners(
        (
            _owner("sample-a", "a", apex_rt=10.00),
            _owner("sample-b", "b", apex_rt=10.20),
            _owner("sample-c", "c", apex_rt=10.40),
        ),
        config=AlignmentConfig(preferred_rt_sec=30.0, max_rt_sec=120.0),
        edge_evidence_sink=edge_evidence,
    )

    sorted_pairs = [
        tuple(sorted((edge.left_owner_id, edge.right_owner_id)))
        for edge in edge_evidence
    ]
    assert len(sorted_pairs) == len(set(sorted_pairs))
    assert all(left <= right for left, right in sorted_pairs)


def test_owner_clustering_skips_impossible_mz_group_without_edge_sink(
    monkeypatch,
) -> None:
    real_evaluate_owner_edge = owner_clustering_module.evaluate_owner_edge

    def guard_far_owner_scoring(left, right, *, config, drift_lookup=None):
        if any(owner.owner_id.endswith("-far") for owner in (left, right)):
            raise AssertionError("far owner should be rejected by group prefilter")
        return real_evaluate_owner_edge(
            left,
            right,
            config=config,
            drift_lookup=drift_lookup,
        )

    monkeypatch.setattr(
        owner_clustering_module,
        "evaluate_owner_edge",
        guard_far_owner_scoring,
    )

    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "near-a", precursor_mz=500.0000),
            _owner("sample-b", "near-b", precursor_mz=500.0050),
            _owner("sample-c", "far", precursor_mz=700.0000),
        ),
        config=AlignmentConfig(max_ppm=20.0),
    )

    assert [feature.event_cluster_ids for feature in features] == [
        ("OWN-sample-a-near-a", "OWN-sample-b-near-b"),
        ("OWN-sample-c-far",),
    ]


def test_owner_clustering_skips_impossible_mz_group_with_edge_sink(
    monkeypatch,
) -> None:
    real_evaluate_owner_edge = owner_clustering_module.evaluate_owner_edge

    def guard_far_owner_scoring(left, right, *, config, drift_lookup=None):
        if any(owner.owner_id.endswith("-far") for owner in (left, right)):
            raise AssertionError("far owner should be rejected by group prefilter")
        return real_evaluate_owner_edge(
            left,
            right,
            config=config,
            drift_lookup=drift_lookup,
        )

    monkeypatch.setattr(
        owner_clustering_module,
        "evaluate_owner_edge",
        guard_far_owner_scoring,
    )

    edge_evidence = []
    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "near-a", precursor_mz=500.0000),
            _owner("sample-b", "near-b", precursor_mz=500.0050),
            _owner("sample-c", "far", precursor_mz=700.0000),
        ),
        config=AlignmentConfig(max_ppm=20.0),
        edge_evidence_sink=edge_evidence,
    )

    assert [feature.event_cluster_ids for feature in features] == [
        ("OWN-sample-a-near-a", "OWN-sample-b-near-b"),
        ("OWN-sample-c-far",),
    ]


def test_owner_clustering_does_not_record_envelope_rejected_edge() -> None:
    edge_evidence = []

    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", neutral_loss_tag="NL116"),
            _owner("sample-b", "b", neutral_loss_tag="NL141"),
        ),
        config=AlignmentConfig(),
        edge_evidence_sink=edge_evidence,
    )

    assert len(features) == 2
    assert edge_evidence == []


def test_owner_clustering_rejects_product_or_observed_loss_conflict() -> None:
    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", product_mz=126.066, observed_loss=116.048),
            _owner("sample-b", "b", product_mz=126.066, observed_loss=116.500),
            _owner("sample-c", "c", product_mz=127.000, observed_loss=116.048),
        ),
        config=AlignmentConfig(
            product_mz_tolerance_ppm=20.0,
            observed_loss_tolerance_ppm=20.0,
        ),
    )

    assert len(features) == 3


def test_owner_clustering_range_hard_gate_keeps_ppm_boundary_inclusive() -> None:
    reference = 500.0
    tolerance_delta = reference * 20.0 / 1_000_000.0

    assert not peak_groups_module._range_exceeds_ppm(
        reference,
        reference,
        reference + tolerance_delta,
        20.0,
    )
    assert peak_groups_module._range_exceeds_ppm(
        reference,
        reference,
        reference + tolerance_delta + 1e-9,
        20.0,
    )


def test_owner_clustering_precursor_bucket_boundary_keeps_exact_hard_gate() -> None:
    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", precursor_mz=500.9999),
            _owner("sample-b", "b", precursor_mz=501.0001),
        ),
        config=AlignmentConfig(preferred_ppm=10.0, max_ppm=10.0),
    )

    assert [feature.event_cluster_ids for feature in features] == [
        ("OWN-sample-a-a", "OWN-sample-b-b"),
    ]


def test_owner_clustering_keeps_different_neutral_loss_tags_separate() -> None:
    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", neutral_loss_tag="NL116"),
            _owner("sample-b", "b", neutral_loss_tag="NL141"),
        ),
        config=AlignmentConfig(),
    )

    assert len(features) == 2
    assert [feature.neutral_loss_tag for feature in features] == ["NL116", "NL141"]


def test_identity_conflict_owner_becomes_review_only_feature() -> None:
    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "conflict", identity_conflict=True),
            _owner("sample-b", "clean"),
        ),
        config=AlignmentConfig(),
    )

    conflict, clean = features
    assert conflict.identity_conflict is True
    assert conflict.review_only is True
    assert conflict.evidence == "identity_conflict_review_only"
    assert clean.review_only is False


def test_implausible_rt_distance_does_not_merge_owner_clusters() -> None:
    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", apex_rt=8.0),
            _owner("sample-b", "b", apex_rt=12.0),
        ),
        config=AlignmentConfig(identity_rt_candidate_window_sec=180.0),
    )

    assert len(features) == 2


def test_owner_clustering_does_not_merge_two_owners_from_same_sample() -> None:
    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a1", apex_rt=8.0),
            _owner("sample-a", "a2", apex_rt=8.001),
            _owner("sample-b", "b", apex_rt=8.0),
        ),
        config=AlignmentConfig(),
    )

    assert len(features) == 2
    assert sorted(len(feature.owners) for feature in features) == [1, 2]


def test_owner_clustering_api_has_no_cell_status_input() -> None:
    assert cluster_sample_local_owners.__annotations__["owners"] != "AlignmentMatrix"


def test_ambiguous_records_become_review_only_features_without_owners() -> None:
    features = review_only_features_from_ambiguous_records(
        (
            AmbiguousOwnerRecord(
                ambiguity_id="AMB-sample-a-000001",
                sample_stem="sample-a",
                candidate_ids=("sample-a#1", "sample-a#2"),
                reason="owner_multiplet_ambiguity",
                neutral_loss_tag="NL116",
                precursor_mz=500.0,
                apex_rt=8.5,
                product_mz=383.9526,
                observed_neutral_loss_da=116.0474,
            ),
        ),
        start_index=10,
    )

    assert len(features) == 1
    assert features[0].feature_family_id == "FAM000010"
    assert features[0].review_only is True
    assert features[0].owners == ()
    assert features[0].ambiguous_sample_stem == "sample-a"
    assert features[0].ambiguous_candidate_ids == ("sample-a#1", "sample-a#2")


def test_owner_family_construction_is_writer_visible(tmp_path: Path) -> None:
    from xic_extractor.alignment.tsv_writer import (
        write_alignment_cells_tsv,
        write_alignment_matrix_tsv,
        write_alignment_review_tsv,
    )

    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", apex_rt=8.40),
            _owner("sample-b", "b", apex_rt=8.60),
        ),
        config=AlignmentConfig(identity_rt_candidate_window_sec=180.0),
    )
    matrix = build_owner_alignment_matrix(
        features,
        sample_order=("sample-a", "sample-b", "sample-c"),
        ambiguous_by_sample={},
        rescued_cells=(),
    )
    decisions = build_production_decisions(matrix, AlignmentConfig())

    matrix_rows = _tsv_rows(
        write_alignment_matrix_tsv(
            tmp_path / "matrix.tsv",
            matrix,
            production_decisions=decisions,
        )
    )
    cell_rows = _rows_by_feature_sample(
        write_alignment_cells_tsv(tmp_path / "cells.tsv", matrix),
    )
    review_rows = _rows_by_feature(
        write_alignment_review_tsv(
            tmp_path / "review.tsv",
            matrix,
            production_decisions=decisions,
        ),
    )

    assert len(matrix_rows) == 1
    assert list(matrix_rows[0]) == ["Mz", "RT", "sample-a", "sample-b", "sample-c"]
    assert matrix_rows[0]["Mz"] == "500.0000"
    assert matrix_rows[0]["RT"] == "8.5"
    assert matrix_rows[0]["sample-a"] == "9.00000e+02"
    assert matrix_rows[0]["sample-b"] == "9.00000e+02"
    assert matrix_rows[0]["sample-c"] == ""

    assert len(cell_rows) == 3
    assert cell_rows[("FAM000001", "sample-a")]["status"] == "detected"
    assert cell_rows[("FAM000001", "sample-a")]["source_candidate_id"] == "sample-a#a"
    assert (
        cell_rows[("FAM000001", "sample-a")]["trace_quality"]
        == "owner_exact_apex_match"
    )
    assert cell_rows[("FAM000001", "sample-b")]["status"] == "detected"
    assert cell_rows[("FAM000001", "sample-c")]["status"] == "absent"
    assert cell_rows[("FAM000001", "sample-c")]["reason"] == "no local MS1 owner"

    assert review_rows["FAM000001"]["event_cluster_count"] == "2"
    assert review_rows["FAM000001"]["event_cluster_ids"] == (
        "OWN-sample-a-a;OWN-sample-b-b"
    )
    assert review_rows["FAM000001"]["family_evidence"] == (
        "owner_complete_link;owner_count=2"
    )
    assert review_rows["FAM000001"]["include_in_primary_matrix"] == "TRUE"


class _DriftLookup:
    source = "targeted_istd_trend"

    def __init__(
        self,
        *,
        deltas: dict[str, float],
        orders: dict[str, int],
    ) -> None:
        self._deltas = deltas
        self._orders = orders

    def sample_delta_min(self, sample_stem: str) -> float | None:
        return self._deltas.get(sample_stem)

    def injection_order(self, sample_stem: str) -> int | None:
        return self._orders.get(sample_stem)


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
    member_basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject",
            "processing_request_id": _TEST_PROCESSING_REQUEST_ID,
            "canonical_sample_identity_id": f"test-sample:{sample_stem}",
            "subject": {"kind": "physical_observation", "id": physical_id},
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
        owner_area=1000.0,
        owner_height=100.0,
        primary_identity_event=event,
        supporting_events=(),
        identity_conflict=identity_conflict,
        assignment_reason="owner_exact_apex_match",
        selected_integration=_integration(
            raw_area=1000.0,
            asls_area=900.0,
            morphology_area=900.0,
        ),
        cross_sample_member_subject=CrossSampleGroupMemberSubject(
            member_subject_id=typed_identity_digest(
                "xic.group-member-subject:", member_basis
            ),
            processing_request_id=_TEST_PROCESSING_REQUEST_ID,
            canonical_sample_identity_id=f"test-sample:{sample_stem}",
            subject_kind="physical_observation",
            subject_id=physical_id,
            physical_observation_ids=(physical_id,),
            basis_bytes=member_basis,
        ),
        is_provisional=is_provisional,
    )


def _targeted_istd_anchor(
    *,
    identity_namespace: str,
):
    target = Target(
        label="synthetic-dna-istd",
        mz=500.0,
        rt_min=7.0,
        rt_max=12.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
    )
    return build_targeted_istd_identity_anchor(
        mint_target_list_snapshot(
            (target,),
            identity_namespace=identity_namespace,
        ),
        observed_labels=(target.label,),
    )


def _integration(
    *,
    raw_area: float,
    asls_area: float,
    morphology_area: float,
) -> IntegrationResult:
    return IntegrationResult(
        rt_left_min=8.45,
        rt_apex_min=8.5,
        rt_right_min=8.55,
        raw_apex_rt_min=8.5,
        rt_width_min=0.1,
        height_raw=100.0,
        height_smoothed=100.0,
        area_raw_counts_seconds=raw_area,
        area_baseline_corrected=asls_area,
        baseline_type="asls",
        boundary_sources=("test_owner",),
        area_ms1_morphology=morphology_area,
        ms1_morphology_area_source="gaussian15_positive_asls_residual",
    )


def _edge_decisions_by_pair(edge_evidence):
    return {
        tuple(sorted((edge.left_owner_id, edge.right_owner_id))): edge.decision
        for edge in edge_evidence
    }


def _rows_by_feature(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            row["feature_family_id"]: row
            for row in csv.DictReader(handle, delimiter="\t")
        }


def _tsv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _rows_by_feature_sample(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            (row["feature_family_id"], row["sample_stem"]): row
            for row in csv.DictReader(handle, delimiter="\t")
        }


def test_owner_clustering_preserves_parallel_group_without_typed_identity() -> None:
    owners = (
        _owner("sample-a", "a", apex_rt=10.00),
        _owner("sample-b", "b", apex_rt=10.05),
        _owner("sample-c", "c", apex_rt=10.10),
        _owner("sample-a", "a2", apex_rt=10.40),
        _owner("sample-b", "b2", apex_rt=10.45),
    )
    config = AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0)

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=config,
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c"}),
    )

    assert [hypothesis.owner_ids for hypothesis in hypotheses] == [
        ("OWN-sample-a-a", "OWN-sample-b-b", "OWN-sample-c-c"),
        ("OWN-sample-a-a2", "OWN-sample-b-b2"),
    ]
    assert all(not hypothesis.review_only for hypothesis in hypotheses)
    assert all(
        hypothesis.consolidation_state == "not_consolidated"
        for hypothesis in hypotheses
    )


def test_parallel_group_without_typed_identity_keeps_bounded_backfill() -> None:
    features = cluster_sample_local_owners(
        (
            _owner("sample-a", "a", apex_rt=10.00),
            _owner("sample-b", "b", apex_rt=10.05),
            _owner("sample-c", "c", apex_rt=10.10),
            _owner("sample-a", "a2", apex_rt=10.40),
            _owner("sample-b", "b2", apex_rt=10.45),
        ),
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset(
            {"sample-a", "sample-b", "sample-c", "sample-d"},
        ),
    )
    sample_order = ("sample-a", "sample-b", "sample-c", "sample-d")

    requests = {
        feature.feature_family_id: backfill_request_sample_stems(
            feature,
            sample_order=sample_order,
            raw_sample_stems=frozenset(sample_order),
            alignment_config=AlignmentConfig(),
        )
        for feature in features
    }

    assert all(requests[feature.feature_family_id] for feature in features)


def test_owner_clustering_keeps_group_that_adds_a_sample() -> None:
    owners = (
        _owner("sample-a", "a", apex_rt=10.00),
        _owner("sample-b", "b", apex_rt=10.05),
        _owner("sample-c", "c", apex_rt=10.10),
        _owner("sample-a", "a2", apex_rt=10.40),
        _owner("sample-d", "d2", apex_rt=10.45),
    )
    config = AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0)

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=config,
        sample_universe=frozenset(
            {"sample-a", "sample-b", "sample-c", "sample-d"},
        ),
    )

    assert all(not hypothesis.review_only for hypothesis in hypotheses)
    assert all(
        hypothesis.consolidation_state == "not_consolidated"
        for hypothesis in hypotheses
    )


def test_owner_clustering_keeps_duplicate_owner_group_outside_identity_window() -> None:
    owners = (
        _owner("sample-a", "a", apex_rt=10.00),
        _owner("sample-b", "b", apex_rt=10.05),
        _owner("sample-c", "c", apex_rt=10.10),
        _owner("sample-a", "a2", apex_rt=14.00),
        _owner("sample-b", "b2", apex_rt=14.05),
    )
    config = AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0)

    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=config,
        sample_universe=frozenset({"sample-a", "sample-b", "sample-c"}),
    )

    assert len(hypotheses) == 2
    assert hypotheses[0].owner_ids == (
        "OWN-sample-a-a",
        "OWN-sample-b-b",
        "OWN-sample-c-c",
    )
    assert hypotheses[1].owner_ids == ("OWN-sample-a-a2", "OWN-sample-b-b2")
    assert all(not hypothesis.review_only for hypothesis in hypotheses)
    assert all(
        hypothesis.consolidation_state == "not_consolidated"
        for hypothesis in hypotheses
    )


def test_cross_sample_group_provisional_singleton_quarantined_to_review_only() -> None:
    # Solitary single-sample provisional candidate without anchor support
    owners = (_owner("sample-a", "p1", apex_rt=10.0, is_provisional=True),)
    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(),
        sample_universe=frozenset({"sample-a", "sample-b"}),
    )
    assert len(hypotheses) == 1
    hyp = hypotheses[0]
    assert hyp.has_anchor is False
    assert hyp.review_only is True
    assert "provisional_singleton_review_only" in hyp.evidence


def test_cross_sample_group_definitive_singleton_retains_anchor_and_active() -> None:
    # Solitary single-sample definitive candidate (e.g. C010 sparse authentic peak)
    owners = (_owner("sample-a", "d1", apex_rt=10.0, is_provisional=False),)
    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(),
        sample_universe=frozenset({"sample-a", "sample-b"}),
    )
    assert len(hypotheses) == 1
    hyp = hypotheses[0]
    assert hyp.has_anchor is True
    assert hyp.review_only is False
    assert "provisional_singleton_review_only" not in hyp.evidence


def test_cross_sample_group_provisional_members_corroborate_across_samples() -> None:
    # Multi-sample provisional corroboration: 2+ samples corroborate the hypothesis
    owners = (
        _owner("sample-a", "p1", apex_rt=10.00, is_provisional=True),
        _owner("sample-b", "p2", apex_rt=10.05, is_provisional=True),
    )
    hypotheses = peak_groups_module.construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=180.0),
        sample_universe=frozenset({"sample-a", "sample-b"}),
    )
    assert len(hypotheses) == 1
    hyp = hypotheses[0]
    assert hyp.has_anchor is True
    assert hyp.review_only is False
    assert "provisional_singleton_review_only" not in hyp.evidence
