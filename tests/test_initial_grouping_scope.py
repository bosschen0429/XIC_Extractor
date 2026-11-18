from __future__ import annotations

from dataclasses import replace

from tests.group_fixtures import group_from_members
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupMemberSubject,
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.feature_membership_inference import (
    FeatureMembershipGroup,
)
from xic_extractor.alignment.grouping_bootstrap import InitialGroupingScope


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
