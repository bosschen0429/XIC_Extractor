"""Fixed-group Backfill compares actual alternatives, including leaving blank."""

import pytest

from tests.membership_fixtures import cid_nl_owner
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
        cid_nl_owner("a", "A", apex_rt=10.0),
        cid_nl_owner("b", "B", apex_rt=10.0),
        cid_nl_owner("c", "C", apex_rt=second_rt),
        cid_nl_owner("d", "D", apex_rt=second_rt),
        cid_nl_owner("s", "S", apex_rt=10.0),
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
        cid_nl_owner("a", "A", apex_rt=10),
        cid_nl_owner("b", "B", apex_rt=10),
        cid_nl_owner("s", "S", apex_rt=11.5),
    )
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(preferred_rt_sec=30),
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


@pytest.mark.parametrize(
    "seconds",
    [(0, 2, 4, 30, 20), (0, 0, 90, 30, 60), (0, 0, 90, 30, 60, 1000)],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_complete_backfill_configuration_can_rescue_rejected_prefix(seconds, reverse):
    owners = tuple(
        cid_nl_owner(str(i), str(i), apex_rt=10 + second / 60)
        for i, second in enumerate(seconds)
    )
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(preferred_rt_sec=120),
        rt_frame=SampleRtFrame().with_coordinate_override(
            lambda owner: owner.peak.integration.rt_apex_min
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
    assert {proposals[index][1] for index in selected} == set(ids[3:5])
    assert all(risk.cost_gap >= 0 for risk in risks.values())


@pytest.mark.parametrize("candidate_conflicts", [False, True])
def test_fixed_original_conflict_does_not_poison_a_compatible_extension(
    candidate_conflicts,
):
    owners = tuple(
        cid_nl_owner(str(i), str(i), precursor_mz=mz, apex_rt=10 + i / 100)
        for i, mz in enumerate(
            (500.0, 500.008, 500.008 if candidate_conflicts else 500.004)
        )
    )
    a, b, candidate = (owner.owner_id for owner in owners)
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
        trajectories=PeakTrajectories(),
    )
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
        cid_nl_owner(str(i), str(i), apex_rt=10 + second / 60)
        for i, second in enumerate((0, 2, 4, 30, 32))
    )
    comparison = MembershipComparison(
        owners,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame().with_coordinate_override(
            lambda owner: owner.peak.integration.rt_apex_min
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


@pytest.mark.parametrize("reverse", [False, True])
def test_order_support_can_choose_a_backfill_peak_but_cannot_exclude_one(reverse):
    """S's two peaks sit equally far from the fixed later peaks.

    Doublets in the other injections order the two tracks; S's earlier peak
    still remains a feasible member when it is the only proposal.
    """
    from tests.alignment_pipeline_helpers import resolved_elution_context
    from xic_extractor.alignment.peak_trajectories import resolve_peak_trajectories

    apices = {"A": 10.0, "B": 10.0, "S": 10.07, "W1": 10.0, "W2": 10.0}
    first, second = ("second", "first") if reverse else ("first", "second")
    contexts, owners = [], {}
    for sample, rt in apices.items():
        contexts.append(
            resolved_elution_context(sample, ((rt, 100.0), (rt + 0.14, 60.0)))
        )
        names = (first, second) if sample == "S" else ("early", "late")
        for track, name, apex in zip(
            ("early", "late"), names, (rt, rt + 0.14), strict=True
        ):
            owners[sample, track] = cid_nl_owner(sample, name, apex_rt=apex)
    order = {sample: i for i, sample in enumerate(apices)}
    fixed = (owners["A", "late"], owners["B", "late"])
    match, other = owners["S", "late"], owners["S", "early"]
    compared = (*fixed, match, other)
    comparison = MembershipComparison(
        compared[::-1] if reverse else compared,
        rules=CID_NL_GROUPING_RULES,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=order),
        trajectories=resolve_peak_trajectories(
            tuple(owners.values()),
            contexts,
            config=AlignmentConfig(),
            injection_order=order,
        ),
    )
    original = {"fixed": tuple(owner.owner_id for owner in fixed)}
    for candidates in ((other,), (other, match) if reverse else (match, other)):
        proposals = [("fixed", o.owner_id) for o in candidates]
        selected, _ = select_backfill_memberships(
            comparison=comparison,
            original_groups=original,
            proposals=proposals,
            conflicts=frozenset({(0, 1)}) if len(proposals) > 1 else frozenset(),
        )
        assert {proposals[i][1] for i in selected} == {
            (match if len(candidates) > 1 else other).owner_id
        }
