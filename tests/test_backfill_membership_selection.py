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
