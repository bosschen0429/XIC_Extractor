from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.grouping_bootstrap import InitialGroupingScope


def _group(
    group_id: str,
    *,
    owner_ids: tuple[str, ...] | None = None,
    center_mz: float = 500.0,
    center_rt: float = 8.0,
) -> CrossSamplePeakGroupHypothesis:
    return CrossSamplePeakGroupHypothesis(
        group_hypothesis_id=group_id,
        public_family_id="display-family",
        owner_ids=owner_ids or (f"owner:{group_id}",),
        event_ids=(f"event:{group_id}",),
        event_member_count=1,
        group_center_mz=center_mz,
        group_center_rt=center_rt,
    )


def test_initial_grouping_scope_is_a_minimal_immutable_canonical_value() -> None:
    first = _group("group-a")
    second = _group("group-b", center_rt=8.4)

    scope = InitialGroupingScope(
        mz_interval=(499, 501),
        rt_interval=(-0.0, 8.8),
        groups=(second, first),
    )

    assert {field.name for field in fields(scope)} == {
        "mz_interval",
        "rt_interval",
        "groups",
        "peak_trajectories",
    }
    assert scope.mz_interval == (499.0, 501.0)
    assert scope.rt_interval[0].hex() == "0x0.0p+0"
    assert scope.groups == (first, second)
    assert scope.peak_trajectories.tracks == ()
    with pytest.raises(FrozenInstanceError):
        scope.groups = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"mz_interval": (500.1, 499.9)}, "mz_interval"),
        ({"rt_interval": (8.8, 7.5)}, "rt_interval"),
        (
            {"groups": (_group("group-a"), _group("group-a"))},
            "duplicate group_hypothesis_id",
        ),
        (
            {"groups": (_group("group-a", center_mz=501.1),)},
            "outside the grouping scope m/z interval",
        ),
        (
            {"groups": (_group("group-a", center_rt=9.0),)},
            "outside the grouping scope RT interval",
        ),
    ),
)
def test_initial_grouping_scope_rejects_invalid_bounds_or_group_membership(
    kwargs: dict[str, object],
    message: str,
) -> None:
    arguments = {
        "mz_interval": (499.9, 500.1),
        "rt_interval": (7.5, 8.8),
        "groups": (),
    }
    arguments.update(kwargs)

    with pytest.raises(ValueError, match=message):
        InitialGroupingScope(**arguments)  # type: ignore[arg-type]


def test_initial_grouping_scope_rejects_shared_or_malformed_owner_ids() -> None:
    with pytest.raises(ValueError, match="must not share owner ids"):
        InitialGroupingScope(
            mz_interval=(499.9, 500.1),
            rt_interval=(7.5, 8.8),
            groups=(
                _group("group-a", owner_ids=("owner:shared",)),
                _group("group-b", owner_ids=("owner:shared",), center_rt=8.4),
            ),
        )

    malformed = _group("group-a")
    object.__setattr__(malformed, "owner_ids", ["owner:group-a"])
    with pytest.raises(ValueError, match="owner_ids must be a tuple"):
        InitialGroupingScope(
            mz_interval=(499.9, 500.1),
            rt_interval=(7.5, 8.8),
            groups=(malformed,),
        )
