from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
from typing import Any, cast

import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import KnownIstdOwnerLookup
from xic_extractor.alignment.feature_membership_inference import (
    FeatureMembershipInferenceResult,
    _Candidate,
    _solve_sample_assignment,
)
from xic_extractor.alignment.feature_membership_inference import (
    infer_feature_memberships as _infer_feature_memberships,
)
from xic_extractor.alignment.ownership_models import (
    IdentityEvent,
    SampleLocalMS1Owner,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult


def infer_feature_memberships(
    *args: Any, **kwargs: Any
) -> FeatureMembershipInferenceResult:
    """Exercise the retained directional bootstrap used only for warp anchors."""
    return _infer_feature_memberships(*args, bootstrap=True, **kwargs)


@dataclass(frozen=True, slots=True)
class _DriftLookup:
    deltas: dict[str, float | None]
    orders: dict[str, int]
    source: str = "batch_istd_trend"

    def sample_delta_min(self, sample_stem: str) -> float | None:
        return self.deltas.get(sample_stem)

    def injection_order(self, sample_stem: str) -> int | None:
        return self.orders.get(sample_stem)


def _owner(
    sample: str,
    name: str,
    *,
    precursor_mz: float = 500.0,
    apex_rt: float = 10.0,
    evidence_score: int = 80,
    seed_event_count: int = 2,
    supporting_events: tuple[IdentityEvent, ...] = (),
    identity_conflict: bool = False,
) -> SampleLocalMS1Owner:
    event = IdentityEvent(
        candidate_id=f"{sample}-{name}-primary",
        sample_stem=sample,
        raw_file=f"{sample}.raw",
        neutral_loss_tag="DNA_dR",
        precursor_mz=precursor_mz,
        product_mz=precursor_mz - 116.0474,
        observed_neutral_loss_da=116.0474,
        seed_rt=apex_rt,
        evidence_score=evidence_score,
        seed_event_count=seed_event_count,
    )
    return SampleLocalMS1Owner(
        owner_id=f"OWN-{sample}-{name}",
        sample_stem=sample,
        raw_file=f"{sample}.raw",
        precursor_mz=precursor_mz,
        owner_apex_rt=apex_rt,
        owner_peak_start_rt=apex_rt - 0.05,
        owner_peak_end_rt=apex_rt + 0.05,
        owner_area=1_000.0,
        owner_height=100.0,
        primary_identity_event=event,
        supporting_events=supporting_events,
        identity_conflict=identity_conflict,
        assignment_reason="same_apex_window",
    )


def _memberships(result: FeatureMembershipInferenceResult) -> set[frozenset[str]]:
    return {frozenset(group.owner_ids) for group in result.groups}


def test_ms1_only_owner_requires_physical_signal_quality() -> None:
    strong = replace(
        _owner("a", "ms1"),
        primary_identity_event=None,
        selected_integration=IntegrationResult(
            rt_left_min=9.95,
            rt_apex_min=10.0,
            rt_right_min=10.05,
            raw_apex_rt_min=10.0,
            rt_width_min=0.1,
            height_raw=100.0,
            height_smoothed=100.0,
            area_raw_counts_seconds=1000.0,
            baseline_residual_mad=1.0,
        ),
    )
    missing_quality = replace(_owner("b", "ms1"), primary_identity_event=None)
    result = infer_feature_memberships(
        (strong, missing_quality),
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 2},
        drift_lookup=None,
    )
    assert _memberships(result) == {
        frozenset((strong.owner_id,)),
        frozenset((missing_quality.owner_id,)),
    }
    assert all(group.disposition == "undecided" for group in result.groups)
    assert any(
        decision.owner_id == missing_quality.owner_id
        and decision.reason == "weak_identity_evidence"
        for decision in result.sparse_decisions
    )


def test_ms1_only_and_cid_evidence_do_not_form_a_mixed_group() -> None:
    ms1 = replace(
        _owner("a", "ms1"),
        primary_identity_event=None,
        selected_integration=IntegrationResult(
            rt_left_min=9.95,
            rt_apex_min=10.0,
            rt_right_min=10.05,
            raw_apex_rt_min=10.0,
            rt_width_min=0.1,
            height_raw=100.0,
            height_smoothed=100.0,
            area_raw_counts_seconds=1000.0,
            baseline_residual_mad=1.0,
        ),
    )
    cid = _owner("b", "cid")
    result = infer_feature_memberships(
        (ms1, cid),
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 2},
        drift_lookup=None,
    )
    assert _memberships(result) == {
        frozenset((ms1.owner_id,)),
        frozenset((cid.owner_id,)),
    }


def test_true_drift_is_stable_without_requiring_distant_pair_edges() -> None:
    owners = tuple(
        _owner(f"s{index}", "main", apex_rt=10.0 + 0.75 * index) for index in range(4)
    )
    order = {f"s{index}": index for index in range(4)}

    first = infer_feature_memberships(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0),
        injection_order=order,
        drift_lookup=None,
    )
    permuted = infer_feature_memberships(
        tuple(reversed(owners)),
        config=AlignmentConfig(preferred_rt_sec=60.0),
        injection_order=order,
        drift_lookup=None,
    )

    assert first.groups == permuted.groups
    assert first.groups[0].owner_ids == tuple(
        sorted(owner.owner_id for owner in owners)
    )
    assert first.groups[0].disposition == "assigned"
    assert first.groups[0].basis == "bidirectional_membership_agreement"
    assert first.operation_counts.owner_edge_evaluations < len(owners) ** 2 * 2


def test_exact_known_identity_is_indivisible_and_does_not_absorb_unbound_owner() -> (
    None
):
    bound_a = _owner("a", "known", apex_rt=8.0)
    bound_b = _owner("b", "known", apex_rt=12.0)
    unbound = _owner("c", "lookalike", apex_rt=8.0)
    lookup = KnownIstdOwnerLookup(
        reference=cast(Any, None),
        bindings=(
            (bound_a.owner_id, "d3-N6-medA", -1.0),
            (bound_b.owner_id, "d3-N6-medA", 3.0),
        ),
    )

    result = infer_feature_memberships(
        (bound_a, bound_b, unbound),
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 2, "c": 3},
        drift_lookup=lookup,
    )

    known = next(group for group in result.groups if group.known_identity_label)
    assert known.owner_ids == tuple(sorted((bound_a.owner_id, bound_b.owner_id)))
    assert known.disposition == "known_identity"
    assert unbound.owner_id not in known.owner_ids
    unknown = next(
        group for group in result.groups if unbound.owner_id in group.owner_ids
    )
    assert unknown.known_identity_label is None
    assert result.known_identity_constraints[0].status == "satisfied"


def test_known_identity_sample_conflict_is_reported_without_selection() -> None:
    left = _owner("same", "left")
    right = _owner("same", "right", apex_rt=10.2)
    lookup = KnownIstdOwnerLookup(
        reference=cast(Any, None),
        bindings=(
            (left.owner_id, "known", 0.0),
            (right.owner_id, "known", 0.0),
        ),
    )

    result = infer_feature_memberships(
        (left, right),
        config=AlignmentConfig(),
        injection_order={"same": 1},
        drift_lookup=lookup,
    )

    assert len(result.groups) == 1
    conflict = result.groups[0]
    assert conflict.owner_ids == tuple(sorted((left.owner_id, right.owner_id)))
    assert conflict.disposition == "unresolved_conflict"
    assert conflict.basis == "known_identity_input_conflict"
    assert conflict.reason == "known_identity_has_multiple_bound_owners_in_one_sample"
    assert conflict.known_identity_label == "known"
    assert result.known_identity_constraints[0].status == "unresolved_conflict"


def test_bridge_with_equal_competing_tracks_remains_undecided() -> None:
    left = _owner("a", "left", apex_rt=10.0)
    right = _owner("a", "right", apex_rt=11.0)
    bridge = _owner("b", "bridge", apex_rt=10.5)

    result = infer_feature_memberships(
        (left, bridge, right),
        config=AlignmentConfig(preferred_rt_sec=60.0),
        injection_order={"a": 1, "b": 2},
        drift_lookup=None,
    )

    assert all(group.disposition == "undecided" for group in result.groups)
    assert result.operation_counts.competition_components >= 1
    assert result.operation_counts.ambiguous_assignments >= 1
    assert any(
        decision.reason == "alternative_assignment_has_equal_objective"
        for decision in result.sparse_decisions
    )


@pytest.mark.parametrize(
    ("delta", "ambiguous"),
    [(0.0, True), (0.5e-9, True), (1.0e-9, True), (1.1e-9, False)],
)
def test_one_by_many_assignment_uses_sorted_feature_tie_break(
    delta: float, ambiguous: bool
) -> None:
    owner = _owner("a", "single")
    assignments, ambiguous_owners = _solve_sample_assignment(
        (owner,),
        {
            owner.owner_id: (
                _Candidate("feature-b", 0.25 + delta, "supported", "probe", ()),
                _Candidate("feature-a", 0.25, "supported", "probe", ()),
            )
        },
        Counter(),
    )

    assert assignments == {owner.owner_id: "feature-a"}
    assert ambiguous_owners == (
        frozenset((owner.owner_id,)) if ambiguous else frozenset()
    )


@pytest.mark.parametrize(
    ("delta", "selected_owner", "ambiguous"),
    [
        (0.0, "first", True),
        (0.5e-9, "first", True),
        (1.0e-9, "first", True),
        (1.1e-9, "first", False),
    ],
)
def test_many_by_one_assignment_uses_stable_owner_tie_representative(
    delta: float, selected_owner: str, ambiguous: bool
) -> None:
    first = _owner("a", "first")
    second = _owner("b", "second")
    assignments, ambiguous_owners = _solve_sample_assignment(
        (first, second),
        {
            first.owner_id: (_Candidate("feature-a", 0.25, "supported", "probe", ()),),
            second.owner_id: (
                _Candidate("feature-a", 0.25 + delta, "supported", "probe", ()),
            ),
        },
        Counter(),
    )

    selected = first if selected_owner == "first" else second
    assert assignments == {selected.owner_id: "feature-a"}
    assert ambiguous_owners == (
        frozenset((selected.owner_id,)) if ambiguous else frozenset()
    )


def _long_rt_same_mass_owners(
    *, evidence_score: int = 80, seed_event_count: int = 2
) -> tuple[SampleLocalMS1Owner, ...]:
    return tuple(
        _owner(
            f"s{index}",
            "long-rt",
            apex_rt=10.0 + index,
            evidence_score=evidence_score,
            seed_event_count=seed_event_count,
        )
        for index in range(3)
    )


def _long_rt_config() -> AlignmentConfig:
    return AlignmentConfig(preferred_rt_sec=30.0, max_rt_sec=120.0)


def test_sparse_long_rt_same_mass_without_drift_is_undecided() -> None:
    owners = _long_rt_same_mass_owners()
    result = infer_feature_memberships(
        owners,
        config=_long_rt_config(),
        injection_order={
            owner.sample_stem: index for index, owner in enumerate(owners)
        },
        drift_lookup=None,
    )

    assert {
        (group.owner_ids, group.disposition, group.basis, group.reason)
        for group in result.groups
    } == {
        (
            (owner.owner_id,),
            "undecided",
            "insufficient_cross_sample_evidence",
            "no_supported_cross_sample_membership",
        )
        for owner in owners
    }
    assert result.operation_counts.input_owner_rows == 3
    assert result.operation_counts.unique_owners == 3
    assert result.operation_counts.directional_passes == 2
    assert result.operation_counts.rt_candidate_features_pruned == 6
    assert result.operation_counts.candidate_features_considered == 0
    assert result.operation_counts.owner_edge_evaluations == 0
    assert result.operation_counts.supported_candidate_arcs == 0


def test_sparse_long_rt_same_mass_uses_drift_corrected_support() -> None:
    owners = _long_rt_same_mass_owners()
    result = infer_feature_memberships(
        owners,
        config=_long_rt_config(),
        injection_order={
            owner.sample_stem: index for index, owner in enumerate(owners)
        },
        drift_lookup=_DriftLookup(
            deltas={"s0": 0.0, "s1": 0.5, "s2": 1.0},
            orders={"s0": 1, "s1": 2, "s2": 3},
        ),
    )

    assert len(result.groups) == 1
    assert result.groups[0].owner_ids == tuple(owner.owner_id for owner in owners)
    assert result.groups[0].disposition == "assigned"
    assert result.groups[0].basis == "bidirectional_membership_agreement"
    assigned_decisions = [
        decision for decision in result.sparse_decisions if decision.state == "assigned"
    ]
    assert assigned_decisions
    assert all(decision.cost is not None for decision in assigned_decisions)
    assert any("strong_edge" in decision.reason for decision in result.sparse_decisions)


def test_sparse_same_mass_raw_fallback_remains_assignable() -> None:
    owners = (
        _owner(
            "s0", "raw-fallback", apex_rt=10.0, evidence_score=45, seed_event_count=1
        ),
        _owner(
            "s1", "raw-fallback", apex_rt=10.1, evidence_score=45, seed_event_count=1
        ),
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=120.0),
        injection_order={"s0": 1, "s1": 2},
        drift_lookup=_DriftLookup(
            deltas={"s0": 0.0, "s1": -0.3}, orders={"s0": 1, "s1": 2}
        ),
    )

    assert len(result.groups) == 1
    assert result.groups[0].owner_ids == tuple(owner.owner_id for owner in owners)
    assert result.groups[0].disposition == "assigned"
    assert any(
        "drift=raw_fallback" in decision.reason for decision in result.sparse_decisions
    )


def test_assigned_sparse_decisions_record_the_witness_and_rt_basis_used() -> None:
    """An audit must be able to tell which witness and which RT coordinate decided.

    ``witness_owner_ids`` names every candidate witness, not the one whose edge
    carried the cost, and without the basis an auditor cannot separate a
    membership that stood on raw RT from one that only held after the ISTD
    correction. The explanation serialises these decisions with ``asdict``, so
    whatever is absent here is absent from the delivered audit.
    """

    owners = _long_rt_same_mass_owners()
    corrected = infer_feature_memberships(
        owners,
        config=_long_rt_config(),
        injection_order={
            owner.sample_stem: index for index, owner in enumerate(owners)
        },
        drift_lookup=_DriftLookup(
            deltas={"s0": 0.0, "s1": 0.5, "s2": 1.0},
            orders={"s0": 1, "s1": 2, "s2": 3},
        ),
    )
    owner_ids = {owner.owner_id for owner in owners}
    assigned = [
        decision
        for decision in corrected.sparse_decisions
        if decision.state == "assigned"
    ]
    assert assigned
    for decision in assigned:
        assert decision.selected_witness_owner_id in owner_ids
        assert decision.selected_witness_owner_id != decision.owner_id
        assert decision.selected_witness_owner_id in decision.witness_owner_ids
        assert decision.rt_basis in {"raw", "drift_corrected"}
        assert decision.rt_raw_delta_sec is not None
    assert any(decision.rt_basis == "drift_corrected" for decision in assigned)
    assert all(
        decision.rt_drift_corrected_delta_sec is not None
        for decision in assigned
        if decision.rt_basis == "drift_corrected"
    )
    assert {
        "selected_witness_owner_id",
        "rt_basis",
        "rt_raw_delta_sec",
        "rt_drift_corrected_delta_sec",
    } <= asdict(assigned[0]).keys()

    fallback = infer_feature_memberships(
        (
            _owner(
                "s0",
                "raw-fallback",
                apex_rt=10.0,
                evidence_score=45,
                seed_event_count=1,
            ),
            _owner(
                "s1",
                "raw-fallback",
                apex_rt=10.1,
                evidence_score=45,
                seed_event_count=1,
            ),
        ),
        config=AlignmentConfig(preferred_rt_sec=60.0, max_rt_sec=120.0),
        injection_order={"s0": 1, "s1": 2},
        drift_lookup=_DriftLookup(
            deltas={"s0": 0.0, "s1": -0.3}, orders={"s0": 1, "s1": 2}
        ),
    )
    raw_assigned = [
        decision
        for decision in fallback.sparse_decisions
        if decision.state == "assigned"
    ]
    assert raw_assigned
    assert all(decision.rt_basis == "raw" for decision in raw_assigned)
    assert all(
        decision.selected_witness_owner_id is not None for decision in raw_assigned
    )


def test_sparse_long_rt_missing_drift_is_unassessable() -> None:
    owners = _long_rt_same_mass_owners(evidence_score=45, seed_event_count=1)[:2]
    result = infer_feature_memberships(
        owners,
        config=_long_rt_config(),
        injection_order={"s0": 1, "s1": 2},
        drift_lookup=_DriftLookup(
            deltas={"s0": 0.0, "s1": None}, orders={"s0": 1, "s1": 2}
        ),
    )

    assert {group.disposition for group in result.groups} == {"undecided"}
    assert {group.basis for group in result.groups} == {
        "insufficient_cross_sample_evidence"
    }
    assert any(
        "drift=unavailable" in decision.reason for decision in result.sparse_decisions
    )


def test_repeated_wrappers_and_duplicate_owner_rows_do_not_add_support() -> None:
    first = _owner("a", "main", apex_rt=10.0)
    assert first.primary_identity_event is not None
    support = replace(
        first.primary_identity_event,
        candidate_id="support-1",
        seed_event_count=1,
    )
    one_wrapper = _owner("b", "main", apex_rt=10.1, supporting_events=(support,))
    repeated_wrappers = replace(
        one_wrapper,
        supporting_events=(
            support,
            replace(support, candidate_id="support-2"),
            replace(support, candidate_id="support-3"),
        ),
    )

    one = infer_feature_memberships(
        (first, one_wrapper),
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 2},
        drift_lookup=None,
    )
    repeated = infer_feature_memberships(
        (first, first, repeated_wrappers),
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 2},
        drift_lookup=None,
    )

    assert _memberships(one) == _memberships(repeated)
    assert [group.disposition for group in one.groups] == [
        group.disposition for group in repeated.groups
    ]
    assert repeated.operation_counts.duplicate_owner_rows_ignored == 1
    one_costs = sorted(
        decision.cost for decision in one.sparse_decisions if decision.cost is not None
    )
    repeated_costs = sorted(
        decision.cost
        for decision in repeated.sparse_decisions
        if decision.cost is not None
    )
    assert one_costs == repeated_costs


def test_weak_evidence_is_not_promoted_by_nearby_strong_owner() -> None:
    strong = _owner("a", "strong", apex_rt=10.0)
    weak = _owner("b", "weak", apex_rt=10.0, evidence_score=10, seed_event_count=1)

    result = infer_feature_memberships(
        (strong, weak),
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 2},
        drift_lookup=None,
    )

    assert _memberships(result) == {
        frozenset((strong.owner_id,)),
        frozenset((weak.owner_id,)),
    }
    weak_group = next(
        group for group in result.groups if weak.owner_id in group.owner_ids
    )
    assert weak_group.disposition == "undecided"
    assert any(
        decision.owner_id == weak.owner_id
        and decision.reason == "weak_identity_evidence"
        for decision in result.sparse_decisions
    )


def test_irrelevant_sample_in_order_map_does_not_change_membership() -> None:
    owners = (
        _owner("a", "main", apex_rt=10.0),
        _owner("b", "main", apex_rt=10.2),
    )
    base = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 3},
        drift_lookup=None,
    )
    with_irrelevant = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        injection_order={"a": 1, "irrelevant": 2, "b": 3},
        drift_lookup=None,
    )

    assert base.groups == with_irrelevant.groups


def test_candidate_comparisons_scale_with_features_instead_of_owner_pairs() -> None:
    owner_count = 120
    owners = tuple(
        _owner(
            f"s{index:03d}",
            "main",
            apex_rt=10.0 + index * 0.1,
        )
        for index in range(owner_count)
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        injection_order={
            owner.sample_stem: index for index, owner in enumerate(owners)
        },
        drift_lookup=None,
    )

    assert len(result.groups) == 1
    assert result.operation_counts.feature_summary_comparisons < owner_count * 3
    assert result.operation_counts.owner_edge_evaluations < owner_count * 5
    assert result.operation_counts.feature_summary_comparisons < (
        owner_count * (owner_count - 1) // 2
    )
