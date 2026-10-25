"""Final membership chooses with visible risk; bootstrap remains conservative."""

from dataclasses import replace

import numpy as np
import pytest

from tests.test_feature_membership_inference import _owner
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.feature_membership_inference import (
    infer_feature_memberships,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.peak_detection.ms1_shape_identity import prepare_local_peak_shape


def infer(owners, **kwargs):
    return infer_feature_memberships(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
        **kwargs,
    )


def test_weak_final_owner_is_selected_without_granting_quantitation():
    strong = _owner("a", "strong")
    weak = replace(
        _owner("b", "weak", evidence_score=1),
        is_provisional=True,
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


def test_soft_shape_contradiction_is_selected_and_recorded():
    x = np.linspace(-0.6, 0.6, 121)
    owners = tuple(
        replace(
            _owner(s, s),
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
        _owner("a", "early", apex_rt=10),
        _owner("a", "late", apex_rt=10.2),
        _owner("b", "bridge", apex_rt=10.1),
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
        _owner("a", "early", apex_rt=10),
        _owner("a", "late", apex_rt=10.2),
    )
    result = infer(owners)
    assert sorted(len(g.owner_ids) for g in result.groups) == [1, 1]
    for decision in result.sparse_decisions:
        risk = decision.accepted_risk
        assert risk.status == "no_feasible_alternative"
        assert risk.alternative_partition is None
        assert risk.equivalent_alternatives == ()
        assert "accepted_competition" not in risk.risk_flags


def test_non_equivalent_choice_does_not_depend_on_id_or_input_order():
    owners = (
        _owner("a", "early", apex_rt=10),
        _owner("a", "late", apex_rt=10.4),
        _owner("b", "bridge", apex_rt=10.05),
    )

    def selected(items):
        by_id = {o.owner_id: (o.sample_stem, o.owner_apex_rt) for o in items}
        return {frozenset(by_id[i] for i in g.owner_ids) for g in infer(items).groups}

    changed = tuple(
        replace(o, owner_id=str(10 - i)) for i, o in enumerate(reversed(owners))
    )
    assert selected(owners) == selected(changed)


def test_mass_chain_cannot_escape_full_group_tolerance():
    owners = tuple(
        _owner(str(i), str(i), precursor_mz=500 + 0.02 * i) for i in range(3)
    )
    result = infer(owners)
    assert sorted(len(g.owner_ids) for g in result.groups) == [1, 2]


def test_rt_chain_cannot_expand_the_declared_candidate_universe():
    owners = tuple(_owner(str(i), str(i), apex_rt=10 + i * 1.6) for i in range(3))
    result = infer(owners)
    assert max(len(g.owner_ids) for g in result.groups) < 3


def test_coherent_group_is_not_penalized_for_having_more_samples():
    owners = tuple(_owner(str(i), str(i), apex_rt=10 + i * 0.08) for i in range(8))
    assert len(infer(owners).groups) == 1


def test_complete_group_cost_prevents_blind_pair_chain_union():
    from xic_extractor.alignment.membership_selection import MembershipComparison
    from xic_extractor.alignment.peak_trajectories import PeakTrajectories

    owners = tuple(_owner(s, s) for s in "abc")
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
        _owner(s, s, apex_rt=rt)
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
    from tests.test_window_feature_membership import _synthetic_owner
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
