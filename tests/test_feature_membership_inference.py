from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
from typing import Any, cast

import pytest

from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import KnownIstdOwnerLookup
from xic_extractor.alignment.feature_membership_inference import (
    BatchRtWarp,
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
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibrationAccumulator
from xic_extractor.peak_detection.hypotheses import IntegrationResult


def infer_feature_memberships(
    *args: Any, **kwargs: Any
) -> FeatureMembershipInferenceResult:
    """Exercise the retained directional bootstrap used only for warp anchors."""
    return _infer_feature_memberships(
        *args, bootstrap=True, **kwargs, rules=CID_NL_GROUPING_RULES
    )


@pytest.mark.parametrize("bootstrap", (False, True))
def test_membership_consumes_the_composed_support_rule(bootstrap: bool) -> None:
    owners = (_owner("a", "first"), _owner("b", "second"))
    frame = SampleRtFrame(injection_order={"a": 1, "b": 2})
    default = _infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=frame,
        bootstrap=bootstrap,
        rules=CID_NL_GROUPING_RULES,
    )
    withheld = _infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=frame,
        bootstrap=bootstrap,
        rules=replace(CID_NL_GROUPING_RULES, support_level=lambda *_: "weak"),
    )
    assert _memberships(default) == {frozenset(owner.owner_id for owner in owners)}
    assert _memberships(withheld) == {frozenset((owner.owner_id,)) for owner in owners}
    assert all(group.disposition == "undecided" for group in withheld.groups)


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


def test_rt_calibration_accumulates_support_across_partition_boundaries() -> None:
    samples = tuple(f"s{i:02}" for i in range(24))
    groups = tuple(
        tuple(
            _owner(
                sample, f"f{feature}", apex_rt=8.0 + feature + (0.2 if i == 12 else 0)
            )
            for i, sample in enumerate(samples)
        )
        for feature in range(12)
    )
    whole = BatchRtCalibrationAccumulator(
        sample_stems=samples, sample_cohorts=dict.fromkeys(samples, "batch")
    )
    whole.add_groups(groups)
    partitioned = BatchRtCalibrationAccumulator(
        sample_stems=samples, sample_cohorts=dict.fromkeys(samples, "batch")
    )
    for group in reversed(groups):
        partitioned.add_groups((group,))
    assert partitioned.freeze() == whole.freeze()
    assert whole.freeze().offset_min("s12", 15.0) == pytest.approx(0.2)
    assert whole.freeze().has_support("s12")
    assert not whole.freeze().has_support("missing")


def test_rt_calibration_does_not_pool_incompatible_cohort_support() -> None:
    samples = tuple(f"s{i:02}" for i in range(24))
    calibration = BatchRtCalibrationAccumulator(
        sample_stems=samples,
        sample_cohorts={sample: str(i // 12) for i, sample in enumerate(samples)},
    )
    calibration.add_groups(
        tuple(
            tuple(
                _owner(
                    sample, f"f{feature}", apex_rt=8.0 + feature + (5 if i >= 12 else 0)
                )
                for i, sample in enumerate(samples)
            )
            for feature in range(12)
        )
    )
    result = calibration.freeze()
    assert result.common_rt_min("s00", 10) == pytest.approx(10)
    assert result.common_rt_min("s12", 15) == pytest.approx(15)
    assert all(result.has_support(sample) for sample in samples)


@pytest.mark.parametrize(
    "untrusted",
    (
        {"identity_conflict": True},
        {"is_provisional": True},
        {"quantification_eligible": False},
        {"primary_identity_event": None},
    ),
)
def test_observations_without_direct_clean_support_do_not_calibrate_others(
    untrusted,
) -> None:
    samples = ("a", "b", "c", "d", "e")
    groups = tuple(
        tuple(
            replace(_owner(sample, f"f{i}", apex_rt=8.0 + i), **untrusted)
            if sample == "e"
            else _owner(sample, f"f{i}", apex_rt=8.0 + i)
            for sample in samples
        )
        for i in range(12)
    )
    accumulator = BatchRtCalibrationAccumulator(
        sample_stems=samples, sample_cohorts=dict.fromkeys(samples, "batch")
    )
    accumulator.add_groups(groups)
    calibration = accumulator.freeze()
    assert not calibration.has_support("e")
    assert calibration.samples["e"].anchor_count == 0
    assert all(calibration.has_support(sample) for sample in samples[:-1])
    assert all(len(group) == 5 for group in groups)


def test_rt_warp_round_trip_uses_the_same_coordinate_system() -> None:
    warp = BatchRtWarp((("a", ((7.0, 0.1), (20.0, 0.5))),), 12)
    common_rt = 12.0
    observed = common_rt + warp.offset_min("a", common_rt)
    assert warp.corrected_rt_min(_owner("a", "f", apex_rt=observed)) == pytest.approx(
        common_rt
    )


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
        rt_frame=SampleRtFrame(injection_order=({"a": 1, "b": 2})),
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
        rt_frame=SampleRtFrame(injection_order=({"a": 1, "b": 2})),
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
        rt_frame=SampleRtFrame(injection_order=(order or {})),
    )
    permuted = infer_feature_memberships(
        tuple(reversed(owners)),
        config=AlignmentConfig(preferred_rt_sec=60.0),
        rt_frame=SampleRtFrame(injection_order=(order or {})),
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
        rt_frame=SampleRtFrame(injection_order=({"a": 1, "b": 2, "c": 3})).with_sources(
            drift_lookup=lookup
        ),
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
        rt_frame=SampleRtFrame(injection_order=({"same": 1})).with_sources(
            drift_lookup=lookup
        ),
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
        rt_frame=SampleRtFrame(injection_order=({"a": 1, "b": 2})),
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
        rt_frame=SampleRtFrame(
            injection_order=(
                {owner.sample_stem: index for index, owner in enumerate(owners)}
            )
        ),
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
        rt_frame=SampleRtFrame(
            injection_order=(
                {owner.sample_stem: index for index, owner in enumerate(owners)}
            )
        ).with_sources(
            drift_lookup=_DriftLookup(
                deltas={"s0": 0.0, "s1": 0.5, "s2": 1.0},
                orders={"s0": 1, "s1": 2, "s2": 3},
            )
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
        rt_frame=SampleRtFrame(injection_order=({"s0": 1, "s1": 2})).with_sources(
            drift_lookup=_DriftLookup(
                deltas={"s0": 0.0, "s1": -0.3}, orders={"s0": 1, "s1": 2}
            )
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
        rt_frame=SampleRtFrame(
            injection_order=(
                {owner.sample_stem: index for index, owner in enumerate(owners)}
            )
        ).with_sources(
            drift_lookup=_DriftLookup(
                deltas={"s0": 0.0, "s1": 0.5, "s2": 1.0},
                orders={"s0": 1, "s1": 2, "s2": 3},
            )
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
        rt_frame=SampleRtFrame(injection_order=({"s0": 1, "s1": 2})).with_sources(
            drift_lookup=_DriftLookup(
                deltas={"s0": 0.0, "s1": -0.3}, orders={"s0": 1, "s1": 2}
            )
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
        rt_frame=SampleRtFrame(injection_order=({"s0": 1, "s1": 2})).with_sources(
            drift_lookup=_DriftLookup(
                deltas={"s0": 0.0, "s1": None}, orders={"s0": 1, "s1": 2}
            )
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
        rt_frame=SampleRtFrame(injection_order=({"a": 1, "b": 2})),
    )
    repeated = infer_feature_memberships(
        (first, first, repeated_wrappers),
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=({"a": 1, "b": 2})),
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
        rt_frame=SampleRtFrame(injection_order=({"a": 1, "b": 2})),
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
        rt_frame=SampleRtFrame(injection_order=({"a": 1, "b": 3})),
    )
    with_irrelevant = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=({"a": 1, "irrelevant": 2, "b": 3})),
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
        rt_frame=SampleRtFrame(
            injection_order=(
                {owner.sample_stem: index for index, owner in enumerate(owners)}
            )
        ),
    )

    assert len(result.groups) == 1
    assert result.operation_counts.feature_summary_comparisons < owner_count * 3
    assert result.operation_counts.owner_edge_evaluations < owner_count * 5
    assert result.operation_counts.feature_summary_comparisons < (
        owner_count * (owner_count - 1) // 2
    )


@pytest.mark.parametrize("unsupported_edge", ["start", "end"])
@pytest.mark.parametrize("known_edge_excludes_other_apex", [False, True])
def test_supported_apices_do_not_fall_back_to_raw_shapes_at_calibration_edges(
    unsupported_edge: str, known_edge_excludes_other_apex: bool
) -> None:
    import numpy as np

    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration
    from xic_extractor.peak_detection.ms1_shape_identity import (
        compare_local_peak_shapes,
        prepare_local_peak_shape,
    )

    owners = []
    for sample, apex, width in (("a", 10.0, 0.035), ("b", 10.05, 0.19)):
        rt = np.linspace(apex - 0.4, apex + 0.4, 161)
        shape = prepare_local_peak_shape(
            rt, np.exp(-0.5 * ((rt - apex) / width) ** 2), apex_rt_min=apex
        )
        start, end = apex - 0.4, apex + 0.4
        if known_edge_excludes_other_apex:
            if unsupported_edge == "start" and sample == "a":
                end = 10.02
            elif unsupported_edge == "end" and sample == "b":
                start = 10.03
        owners.append(
            replace(
                _owner(sample, "peak", apex_rt=apex),
                owner_peak_start_rt=start,
                owner_peak_end_rt=end,
                peak_shape=shape,
            )
        )
    assert (
        compare_local_peak_shapes(owners[0].peak_shape, owners[1].peak_shape).similarity
        < 0.8
    )
    ranges = (
        {"a": (10.0, 12.0), "b": (10.03, 12.0)}
        if unsupported_edge == "start"
        else {"a": (8.0, 10.0), "b": (8.0, 10.05)}
    )
    calibration = BatchRtCalibration.from_coordinate_knots(
        tuple(
            (sample, tuple((rt, rt) for rt in bounds))
            for sample, bounds in ranges.items()
        )
    )
    frame = SampleRtFrame().with_sample_calibration(calibration)
    result = _infer_feature_memberships(
        owners, config=AlignmentConfig(), rules=CID_NL_GROUPING_RULES, rt_frame=frame
    )
    # An edge excluding the other apex does not prove disjoint regions when
    # the opposite boundary is unknown. It also cannot activate a shape veto.
    assert _memberships(result) == {frozenset(owner.owner_id for owner in owners)}
    for owner in owners:
        boundary = (
            owner.owner_peak_start_rt
            if unsupported_edge == "start"
            else owner.owner_peak_end_rt
        )
        assert frame.calibrated_rt_min(owner.sample_stem, boundary) is None
