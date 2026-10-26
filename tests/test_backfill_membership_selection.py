"""Fixed-group Backfill compares actual alternatives, including leaving blank."""

import pytest

from tests.test_feature_membership_inference import _owner
from xic_extractor.alignment.backfill.membership_selection import (
    select_backfill_memberships,
)
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.membership_selection import MembershipComparison
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_frame import SampleRtFrame


@pytest.mark.parametrize("reverse", (False, True))
@pytest.mark.parametrize("second_rt", (10.0, 10.3))
def test_competing_peak_selects_best_group_and_retains_nearest_alternative(
    reverse, second_rt
):
    owners = (
        _owner("a", "A", apex_rt=10.0),
        _owner("b", "B", apex_rt=10.0),
        _owner("c", "C", apex_rt=second_rt),
        _owner("d", "D", apex_rt=second_rt),
        _owner("s", "S", apex_rt=10.0),
    )
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
        trajectories=PeakTrajectories(),
    )
    a, b, c, d, candidate = (owner.owner_id for owner in owners)
    proposals = [("near", candidate), ("other", candidate)]
    if reverse:
        proposals.reverse()
    selected, risks = select_backfill_memberships(
        comparison=comparison,
        original_groups={"near": (a, b), "other": (c, d)},
        proposals=proposals,
        conflicts=frozenset({(0, 1)}),
    )
    assert len(selected) == 1
    (index,) = selected
    risk = risks[index]
    assert risk.alternative_partition is not None
    assert risk.phase == "backfill"
    assert "backfill_competition" in risk.risk_flags
    if second_rt > 10:
        assert proposals[index][0] == "near"
        assert risk.cost_gap > 0
    else:
        assert risk.cost_gap == pytest.approx(0)
        assert risk.equivalent_alternatives


def test_blank_wins_when_finite_assignment_cost_exceeds_separation():
    owners = (
        _owner("a", "A", apex_rt=10),
        _owner("b", "B", apex_rt=10),
        _owner("s", "S", apex_rt=11.5),
    )
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(max_rt_sec=180, preferred_rt_sec=30),
        rt_frame=SampleRtFrame(),
        trajectories=PeakTrajectories(),
    )
    a, b, candidate = (owner.owner_id for owner in owners)
    assert comparison.group_cost((a, b, candidate)) > 2
    selected, risks = select_backfill_memberships(
        comparison=comparison,
        original_groups={"fixed": (a, b)},
        proposals=(("fixed", candidate),),
        conflicts=frozenset(),
    )
    assert not selected
    assert not risks


@pytest.mark.parametrize("seconds", [(0, 2, 4, 30, 20), (0, 0, 90, 30, 60)])
@pytest.mark.parametrize("reverse", [False, True])
def test_complete_backfill_configuration_can_rescue_rejected_prefix(seconds, reverse):
    owners = tuple(
        _owner(str(i), str(i), apex_rt=10 + second / 60)
        for i, second in enumerate(seconds)
    )
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(preferred_rt_sec=120),
        rt_frame=SampleRtFrame().with_coordinate_override(
            lambda owner: owner.owner_apex_rt
        ),
        trajectories=PeakTrajectories(),
    )
    ids = tuple(owner.owner_id for owner in owners)
    proposals = [("fixed", owner) for owner in ids[3:]]
    if reverse:
        proposals.reverse()
    selected, risks = select_backfill_memberships(
        comparison=comparison,
        original_groups={"fixed": ids[:3]},
        proposals=proposals,
        conflicts=frozenset(),
    )
    assert selected == frozenset({0, 1})
    assert all(risk.cost_gap >= 0 for risk in risks.values())


@pytest.mark.parametrize("candidate_conflicts", [False, True])
def test_fixed_original_conflict_does_not_poison_a_compatible_extension(
    candidate_conflicts,
):
    from xic_extractor.alignment.peak_trajectories import PeakTrajectory

    owners = tuple(_owner(str(i), str(i), apex_rt=10 + i / 100) for i in range(3))
    a, b, candidate = (owner.owner_id for owner in owners)
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
        trajectories=PeakTrajectories(
            tracks=(
                PeakTrajectory((a,), ()),
                PeakTrajectory((b, candidate) if candidate_conflicts else (b,), ()),
            ),
            physical_order=((a, b),),
        ),
    )
    # The earlier accepted partition is fixed; this pass cannot repartition it.
    assert comparison.group_cost((a, b)) == float("inf")
    assert (comparison.pair(a, candidate)[0] < 1) is not candidate_conflicts
    assert comparison.pair(b, candidate)[0] < 1
    selected, risks = select_backfill_memberships(
        comparison=comparison,
        original_groups={"fixed": (a, b)},
        proposals=(("fixed", candidate),),
        conflicts=frozenset(),
    )
    if candidate_conflicts:
        assert not selected
    else:
        assert selected == frozenset({0})
        assert risks[0].cost_gap > 0
        assert "fixed_original_conflict" in risks[0].risk_flags


def test_complete_extension_still_rejects_two_rt_populations():
    owners = tuple(
        _owner(str(i), str(i), apex_rt=10 + second / 60)
        for i, second in enumerate((0, 2, 4, 30, 32))
    )
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame().with_coordinate_override(
            lambda owner: owner.owner_apex_rt
        ),
        trajectories=PeakTrajectories(),
    )
    ids = tuple(o.owner_id for o in owners)
    selected, _ = select_backfill_memberships(
        comparison=comparison,
        original_groups={"fixed": ids[:3]},
        proposals=tuple(("fixed", oid) for oid in ids[3:]),
        conflicts=frozenset(),
    )
    assert not selected
