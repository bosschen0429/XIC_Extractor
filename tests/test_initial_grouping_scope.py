from __future__ import annotations

from dataclasses import replace
from typing import get_args

import pytest

from tests.group_fixtures import group_from_members
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupMemberSubject,
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.feature_membership_inference import (
    FeatureMembershipBasis,
    FeatureMembershipGroup,
)
from xic_extractor.alignment.grouping_bootstrap import InitialGroupingScope

# Only memberships that both directional passes, an ordered pattern, or an
# exact known identity agree on may calibrate batch RT.
_SUPPLIES_RT_CALIBRATION: dict[FeatureMembershipBasis, bool] = {
    "bidirectional_membership_agreement": True,
    "known_identity_constraint": True,
    "peak_trajectory": True,
    "window_level_rt_band": False,
    "directional_membership_disagreement": False,
    "insufficient_cross_sample_evidence": False,
    "known_identity_input_conflict": False,
}


def _group(
    group_id: str,
    *,
    center_mz: float = 500.0,
    center_rt: float = 8.0,
    inference_basis: FeatureMembershipGroup | None = None,
) -> CrossSamplePeakGroupHypothesis:
    return replace(
        group_from_members(
            member_subjects=(
                CrossSampleGroupMemberSubject(
                    sample_key="sample",
                    subject_kind="peak_hypothesis",
                    subject_id=group_id,
                    physical_peaks=(),
                ),
            ),
            disposition="established",
            center=(center_mz, center_rt),
        ),
        inference_basis=inference_basis,
    )


@pytest.mark.parametrize("basis", get_args(FeatureMembershipBasis))
def test_only_agreed_bootstrap_groups_supply_rt_calibration_anchors(
    basis: FeatureMembershipBasis,
) -> None:
    group = _group(
        "group-a",
        inference_basis=FeatureMembershipGroup(
            owner_ids=("owner:group-a",),
            disposition="assigned",
            basis=basis,
            reason="",
        ),
    )
    scope = InitialGroupingScope(
        mz_interval=(499.9, 500.1),
        rt_interval=(7.5, 8.8),
        groups=(group, _group("group-b", center_rt=8.4)),
    )

    expected = 1 if _SUPPLIES_RT_CALIBRATION[basis] else 0
    assert len(scope.calibration_anchor_groups) == expected


def test_initial_grouping_scope_is_a_minimal_immutable_canonical_value() -> None:
    first = _group("group-a")
    second = _group("group-b", center_rt=8.4)

    scope = InitialGroupingScope(
        mz_interval=(499, 501),
        rt_interval=(-0.0, 8.8),
        groups=(second, first),
    )

    assert scope.mz_interval == (499.0, 501.0)
    assert scope.rt_interval[0].hex() == "0x0.0p+0"
    assert scope.groups == (first, second)
    assert scope.peak_trajectories.tracks == ()
