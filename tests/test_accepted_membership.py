"""Final membership chooses with visible risk; bootstrap remains conservative."""

from dataclasses import replace

import numpy as np
import pytest

from tests.membership_fixtures import cid_nl_owner
from tests.owner_peak_fixtures import with_peak
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.feature_membership_inference import (
    infer_feature_memberships,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import (
    RtCalibrationPolicy,
    build_batch_rt_calibration,
)
from xic_extractor.peak_detection.ms1_shape_identity import prepare_local_peak_shape


def infer(owners, **kwargs):
    return infer_feature_memberships(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
        **kwargs,
    )


@pytest.mark.parametrize("max_rt_sec", [30.0, 120.0, 180.0])
@pytest.mark.parametrize("corrected", [False, True])
def test_local_density_preserves_closed_radius_and_duplicate_coordinates(
    max_rt_sec, corrected
):
    from xic_extractor.alignment.membership_selection import MembershipComparison
    from xic_extractor.alignment.peak_trajectories import PeakTrajectories

    radius = min(120.0, max_rt_sec) / 60
    edge = 10.0 + radius
    rts = [
        10.0,
        10.0,
        edge,
        np.nextafter(edge, -np.inf),
        np.nextafter(edge, np.inf),
        10.0 - radius,
        20.0,
    ]
    owners = tuple(cid_nl_owner(str(i), str(i), apex_rt=rt) for i, rt in enumerate(rts))
    frame = SampleRtFrame()
    if corrected:
        frame = frame.with_coordinate_override(
            lambda owner: owner.peak.integration.rt_apex_min / 2
        )
    for population in (owners, owners[::-1], ()):
        comparison = MembershipComparison(
            population,
            rules=CID_NL_GROUPING_RULES,
            config=AlignmentConfig(max_rt_sec=max_rt_sec, preferred_rt_sec=30.0),
            rt_frame=frame,
            trajectories=PeakTrajectories(),
        )
        # Independent direct definition retains subtraction/multiplication
        # order: rounded rt +/- radius bounds can change edge inclusion.
        assert comparison.density == {
            key: sum(
                abs(rt - other) * 60 <= min(120.0, max_rt_sec)
                for other in comparison.rts.values()
            )
            for key, rt in comparison.rts.items()
        }


def _infer_common_rt(owners, injection_order):
    return infer_feature_memberships(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(
            injection_order=injection_order
        ).with_coordinate_override(lambda owner: owner.peak.integration.rt_apex_min),
    )


# Each four-owner split below puts injections from both ends of the batch on
# both sides, so drift between earlier and later injections cannot explain it.
_INTERLEAVED_FOUR = (1, 3, 0, 2)


@pytest.mark.parametrize(
    ("seconds", "expected_sizes"),
    [
        ((0, 30), [2]),
        ((0, 2, 30), [3]),
        ((0, 2, 30, 32), [2, 2]),
        ((0, 2, 4, 30), [1, 3]),
        ((0, 5, 10, 15, 20, 25, 30), [7]),
        ((0, 20, 40, 60), [4]),
        ((0, 2, 30, 32, 60, 62), [6]),
        ((0, 20, 40, 120), [1, 3]),
        ((0, 11, 31, 42), [4]),
        ((0, 10, 30, 40), [2, 2]),
        ((0, 2, 21, 23), [4]),
        ((0, 2, 22, 24), [2, 2]),
    ],
)
def test_final_membership_rejects_separated_rt_populations(seconds, expected_sizes):
    owners = tuple(
        cid_nl_owner(str(i), str(i), apex_rt=10 + second / 60)
        for i, second in enumerate(seconds)
    )
    orders = _INTERLEAVED_FOUR if len(owners) == 4 else range(len(owners))
    result = _infer_common_rt(owners, {str(i): order for i, order in enumerate(orders)})
    assert sorted(len(group.owner_ids) for group in result.groups) == expected_sizes
    assert sorted(key for group in result.groups for key in group.owner_ids) == sorted(
        owner.owner_id for owner in owners
    )


@pytest.mark.parametrize(
    "seconds", [(0, 2, 30, 32), (0, 2, 4, 30), (0, 10, 30, 40), (0, 2, 22, 24)]
)
def test_rt_populations_split_by_injection_order_remain_a_cost(seconds):
    # Earlier injections elute together before later ones: residual drift
    # explains the gap as well as two compounds do, so the gap is not a veto.
    owners = tuple(
        cid_nl_owner(str(i), str(i), apex_rt=10 + second / 60)
        for i, second in enumerate(seconds)
    )
    for orders in (range(len(owners)), reversed(range(len(owners)))):
        result = _infer_common_rt(
            owners, {str(i): order for i, order in enumerate(orders)}
        )
        assert [len(group.owner_ids) for group in result.groups] == [len(owners)]


def test_population_gap_without_injection_order_keeps_the_veto():
    owners = tuple(
        cid_nl_owner(str(i), str(i), apex_rt=10 + second / 60)
        for i, second in enumerate((0, 2, 30, 32))
    )
    result = _infer_common_rt(owners, {"0": 0, "1": 1, "2": 2})
    assert sorted(len(group.owner_ids) for group in result.groups) == [2, 2]


# One compound in seven samples whose four earlier injections elute together
# before the three later ones; the gap (50.01 s) sits 0.87 s below twice the
# earlier side's range (25.44 s). Moving the earliest coordinate by 1 s moves
# that threshold by 2 s, the size of a LOESS anchor change.
_DRIFT_SECONDS = (0.0, 14.0, 17.5, 25.44, 75.45, 78.0, 82.0)


def _drift_owners(shift_sec, names=None):
    names = names or [f"peak{i}" for i in range(len(_DRIFT_SECONDS))]
    return tuple(
        cid_nl_owner(
            f"s{i}",
            name,
            apex_rt=29.0 + (second + (shift_sec if i == 0 else 0.0)) / 60,
        )
        for i, (second, name) in enumerate(zip(_DRIFT_SECONDS, names, strict=True))
    )


def _membership(owners):
    result = _infer_common_rt(owners, {f"s{i}": i for i in range(len(_DRIFT_SECONDS))})
    measured = {o.owner_id: o.sample_stem for o in owners}
    return {frozenset(measured[key] for key in g.owner_ids) for g in result.groups}


@pytest.mark.parametrize("shift_sec", (-1.0, 0.0, 1.0))
def test_small_calibration_change_near_population_threshold_keeps_membership(
    shift_sec,
):
    assert _membership(_drift_owners(shift_sec)) == {
        frozenset(f"s{i}" for i in range(len(_DRIFT_SECONDS)))
    }


def test_population_membership_ignores_ids_input_order_and_small_rt_changes():
    variants = []
    for shift_sec in (-1.0, 0.0, 1.0):
        owners = _drift_owners(shift_sec)
        renamed = _drift_owners(
            shift_sec, names=[f"z{9 - i}" for i in range(len(_DRIFT_SECONDS))]
        )
        variants += [owners, owners[::-1], renamed, renamed[3:] + renamed[:3]]
    assert len({frozenset(_membership(owners)) for owners in variants}) == 1


def test_population_gap_does_not_use_raw_rt_when_a_member_lacks_coordinates():
    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration

    owners = tuple(
        cid_nl_owner(sample, sample, apex_rt=rt)
        for sample, rt in (("a", 10), ("b", 10.02), ("c", 10.04), ("d", 10.5))
    )
    frame = SampleRtFrame().with_sample_calibration(
        BatchRtCalibration.from_coordinate_knots(
            tuple((sample, ((8.0, 8.0), (17.0, 17.0))) for sample in "abc")
        )
    )
    result = infer_feature_memberships(
        owners, rules=CID_NL_GROUPING_RULES, config=AlignmentConfig(), rt_frame=frame
    )
    assert len(result.groups) == 1


def test_population_gap_uses_corrected_not_measured_rt():
    owners = tuple(
        cid_nl_owner(str(i), str(i), apex_rt=rt)
        for i, rt in enumerate((10, 10.02, 10.5, 10.52))
    )
    result = infer_feature_memberships(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame().with_coordinate_override(
            lambda owner: 10 + int(owner.sample_stem) / 60
        ),
    )
    assert len(result.groups) == 1


@pytest.mark.parametrize("missing_sample", (False, True))
@pytest.mark.parametrize("strong_peers", (False, True))
def test_final_membership_uses_weak_common_frame_without_mixing_raw(
    missing_sample, strong_peers
):
    samples = ("a", "b", "c", "d", "e")
    anchors = {
        "early": {"a": 10, "b": 15, "c": 15},
        "late": {"a": 20, "b": 25, "c": 25},
    }
    if strong_peers:
        anchors.update({f"extra{i}": dict.fromkeys("bce", 30 + i) for i in range(6)})
    calibration = build_batch_rt_calibration(
        anchors,
        sample_stems=samples,
        prior_injections={},
        policy=RtCalibrationPolicy(),
    )
    frame = SampleRtFrame().with_sample_calibration(calibration)
    owners = (
        cid_nl_owner("a", "shifted", apex_rt=11),
        cid_nl_owner("b", "reference", apex_rt=16),
        cid_nl_owner("d" if missing_sample else "c", "last", apex_rt=16),
    )
    result = infer_feature_memberships(
        owners, rules=CID_NL_GROUPING_RULES, config=AlignmentConfig(), rt_frame=frame
    )
    assert len(result.groups) == (2 if missing_sample else 1)
    for decision in result.sparse_decisions:
        risk = decision.accepted_risk
        assert risk is not None
        assert risk.rt_basis == ("raw" if missing_sample else "drift_corrected")
        if missing_sample:
            assert risk.rt_estimate is None
        else:
            assert risk.rt_estimate.method == (
                "loess"
                if strong_peers and decision.owner_id != owners[0].owner_id
                else "median_shift"
            )
            assert risk.rt_estimate.rt_min == 16
            assert "low_rt_support" in risk.risk_flags
    assert not frame.has_support("a")  # No promotion of bootstrap support.


def test_weak_final_owner_is_selected_without_granting_quantitation():
    strong = cid_nl_owner("a", "strong")
    weak = replace(
        cid_nl_owner("b", "weak", evidence_score=1),
        quantification_eligible=False,
    )
    result = infer((strong, weak))
    assert len(result.groups) == 1
    assert result.groups[0].disposition == "assigned"
    risk = next(
        d.accepted_risk for d in result.sparse_decisions if d.owner_id == weak.owner_id
    )
    assert "weak_identity" in risk.risk_flags
    assert not weak.quantification_eligible
    assert len(infer((strong, weak), bootstrap=True).groups) == 2


def test_weak_calibration_halves_only_rt_cost_not_mass_or_shape():
    from xic_extractor.alignment.membership_selection import MembershipComparison
    from xic_extractor.alignment.peak_trajectories import PeakTrajectories

    x = np.linspace(-0.6, 0.6, 121)
    owners = tuple(
        replace(
            with_peak(
                cid_nl_owner(s, s, apex_rt=rt, precursor_mz=mz),
                start_rt=9.0,
                end_rt=11.0,
            ),
            peak_shape=prepare_local_peak_shape(
                x + rt, np.exp(-0.5 * (x / width) ** 2), apex_rt_min=rt
            ),
        )
        for s, rt, mz, width in (("a", 10, 500, 0.03), ("b", 10.5, 500.001, 0.25))
    )
    calibration = build_batch_rt_calibration(
        {"early": dict.fromkeys("abc", 8), "late": dict.fromkeys("abc", 20)},
        sample_stems=tuple("abc"),
        prior_injections={},
        policy=RtCalibrationPolicy(),
    )
    config = AlignmentConfig()
    costs = []
    for frame in (
        SampleRtFrame(),
        SampleRtFrame().with_sample_calibration(calibration),
    ):
        comparison = MembershipComparison(
            owners,
            rules=CID_NL_GROUPING_RULES,
            config=config,
            rt_frame=frame,
            trajectories=PeakTrajectories(),
        )
        cost, contradictions = comparison.pair(*(o.owner_id for o in owners))
        assert "shape_disagreement" in contradictions
        costs.append(cost)
    rt_cost = 0.70 * 0.75 * 30 / config.preferred_rt_sec
    assert costs[0] - costs[1] == pytest.approx(rt_cost / 2)
    assert costs[1] > rt_cost / 2  # Independent mass/shape penalties remain.


def test_soft_shape_contradiction_is_selected_and_recorded():
    x = np.linspace(-0.6, 0.6, 121)
    owners = tuple(
        replace(
            cid_nl_owner(s, s),
            peak_shape=prepare_local_peak_shape(
                x + 10,
                np.exp(-0.5 * (x / width) ** 2),
                apex_rt_min=10,
            ),
        )
        for s, width in (("a", 0.03), ("b", 0.25))
    )
    result = infer(owners)
    assert len(result.groups) == 1
    assert any(
        "accepted_contradiction" in d.accepted_risk.risk_flags
        for d in result.sparse_decisions
    )


def test_competition_selects_one_without_losing_other_owners():
    owners = (
        cid_nl_owner("a", "early", apex_rt=10),
        cid_nl_owner("a", "late", apex_rt=10.2),
        cid_nl_owner("b", "bridge", apex_rt=10.1),
    )
    result = infer(owners)
    assert sorted(len(g.owner_ids) for g in result.groups) == [1, 2]
    decision = next(d for d in result.sparse_decisions if d.sample_stem == "b")
    assert decision.accepted_risk.alternative_partition
    assert "accepted_competition" in decision.accepted_risk.risk_flags
    assert sorted(o for g in result.groups for o in g.owner_ids) == sorted(
        o.owner_id for o in owners
    )


def test_same_sample_singleton_swap_is_not_a_competing_membership():
    owners = (
        cid_nl_owner("a", "early", apex_rt=10),
        cid_nl_owner("a", "late", apex_rt=10.2),
    )
    result = infer(owners)
    assert sorted(len(g.owner_ids) for g in result.groups) == [1, 1]
    for decision in result.sparse_decisions:
        risk = decision.accepted_risk
        assert risk.status == "no_feasible_alternative"
        assert risk.alternative_partition is None
        assert risk.equivalent_alternatives == ()
        assert "accepted_competition" not in risk.risk_flags


@pytest.mark.parametrize("reverse", (False, True))
def test_equal_worst_pair_cost_selects_better_average_peer_fit(reverse):
    # Fixed extreme peers set the same maximum pair cost before and after
    # swapping s5's peaks. The earlier peak fits the earlier band better.
    specification = (
        ("s1", "early", 10.4),
        ("s1", "late", 10.65),
        ("s2", "anchor", 10.15),
        ("s4", "early", 11.35),
        ("s4", "late", 11.45),
        ("s5", "early", 10.75),
        ("s5", "late", 11.25),
    )
    owners = tuple(cid_nl_owner(s, name, apex_rt=rt) for s, name, rt in specification)
    if reverse:
        owners = tuple(
            replace(owner, owner_id=f"renamed-{i}")
            for i, owner in enumerate(reversed(owners))
        )
    measured = {
        o.owner_id: (o.sample_stem, o.peak.integration.rt_apex_min) for o in owners
    }
    result = infer(owners)
    earlier = next(
        {measured[key] for key in g.owner_ids}
        for g in result.groups
        if any(measured[key][0] == "s2" for key in g.owner_ids)
    )
    assert ("s5", 10.75) in earlier
    assert ("s5", 11.25) not in earlier


def test_non_equivalent_choice_does_not_depend_on_id_or_input_order():
    owners = (
        cid_nl_owner("a", "early", apex_rt=10),
        cid_nl_owner("a", "late", apex_rt=10.4),
        cid_nl_owner("b", "bridge", apex_rt=10.05),
    )

    def selected(items):
        by_id = {
            o.owner_id: (o.sample_stem, o.peak.integration.rt_apex_min) for o in items
        }
        return {frozenset(by_id[i] for i in g.owner_ids) for g in infer(items).groups}

    changed = tuple(
        replace(o, owner_id=str(10 - i)) for i, o in enumerate(reversed(owners))
    )
    assert selected(owners) == selected(changed)


def test_mass_chain_cannot_escape_full_group_tolerance():
    owners = tuple(
        cid_nl_owner(str(i), str(i), precursor_mz=500 + 0.02 * i) for i in range(3)
    )
    result = infer(owners)
    assert sorted(len(g.owner_ids) for g in result.groups) == [1, 2]


def test_rt_chain_cannot_expand_the_declared_candidate_universe():
    owners = tuple(cid_nl_owner(str(i), str(i), apex_rt=10 + i * 1.6) for i in range(3))
    result = infer(owners)
    assert max(len(g.owner_ids) for g in result.groups) < 3


def test_coherent_group_is_not_penalized_for_having_more_samples():
    owners = tuple(
        cid_nl_owner(str(i), str(i), apex_rt=10 + i * 0.08) for i in range(8)
    )
    assert len(infer(owners).groups) == 1


def test_complete_group_cost_prevents_blind_pair_chain_union():
    from xic_extractor.alignment.membership_selection import MembershipComparison
    from xic_extractor.alignment.peak_trajectories import PeakTrajectories

    owners = tuple(cid_nl_owner(s, s) for s in "abc")
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
        trajectories=PeakTrajectories(),
    )
    a, b, c = (o.owner_id for o in owners)
    # Cost-layer oracle: the complete group must consume the endpoint fact,
    # not just the two favorable edges used to propose a union.
    comparison.pairs.update({(a, b): (0.2, ()), (b, c): (0.2, ()), (a, c): (1.3, ())})
    assert comparison.cost(((a, b, c),)) == pytest.approx(2.3)
    assert comparison.cost(((a, b), (c,))) == pytest.approx(2.2)
    assert sorted(map(len, comparison.merge(((a,), (b,), (c,))))) == [1, 2]


@pytest.mark.parametrize("span_min", (0.0, 1.49, 1.5, 1.51, 2.0))
def test_group_trend_cost_preserves_ninety_second_boundary(span_min):
    from xic_extractor.alignment.membership_selection import MembershipComparison
    from xic_extractor.alignment.peak_trajectories import PeakTrajectories

    owners = tuple(
        cid_nl_owner(s, s, apex_rt=rt)
        for s, rt in (("a", 10.0), ("b", 10.0), ("c", 10.0 + span_min))
    )
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order={"a": 1, "b": 2, "c": 3}),
        trajectories=PeakTrajectories(),
    )
    a, b, c = (o.owner_id for o in owners)
    comparison.pairs.update({(a, b): (0.2, ()), (b, c): (0.2, ()), (a, c): (0.2, ())})
    # The last owner's two neighbors both have RT 10; it has the largest
    # residual. The other two residuals are half as large.
    expected = 1.2 + 0.2 * max(0.0, span_min * 60 / 90 - 1)
    assert comparison.group_cost((a, b, c)) == pytest.approx(expected)
    assert comparison.group_cost((c, a, b)) == pytest.approx(expected)


@pytest.mark.parametrize("direct", (False, True))
def test_mixed_trajectory_retains_only_independent_raw_pairs(direct):
    from tests.alignment_pipeline_helpers import resolved_elution_context
    from tests.window_feature_membership_fixtures import _synthetic_owner
    from xic_extractor.alignment.peak_trajectories import resolve_peak_trajectories
    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration

    apices = {"a": 10.0, "c": 10.1 if direct else 12.5, "b": 10.2 if direct else 15.0}
    owners = tuple(_synthetic_owner(s, s, rt) for s, rt in apices.items())
    orders = {"a": 1, "c": 2, "b": 3}
    frame = SampleRtFrame(injection_order=orders).with_sample_calibration(
        BatchRtCalibration.from_coordinate_knots((("c", ((8.0, 8.0), (17.0, 17.0))),))
    )
    geometry = resolve_peak_trajectories(
        owners,
        tuple(
            resolved_elution_context(s, ((rt, 30.0), (rt + 0.7, 100.0)))
            for s, rt in apices.items()
        ),
        config=AlignmentConfig(),
        injection_order=orders,
        rt_frame=frame,
    )
    assert any(set(t.owner_ids) == set(apices) for t in geometry.tracks)
    projected = geometry.common_frame_membership(("c",), preserve_independent=True)
    assert any(set(t.owner_ids) == {"a", "b"} for t in projected.tracks) is direct
    assert all("c" not in t.owner_ids for t in projected.tracks)
