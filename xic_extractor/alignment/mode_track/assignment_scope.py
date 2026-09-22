"""Minimal producer-owned scope for ModeTrack assignment."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import final

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)


@final
@dataclass(frozen=True, slots=True)
class ModeTrackAssignmentScope:
    """Immutable bounds and exact groups handed from the producer to fitting."""

    mz_interval: tuple[float, float]
    rt_interval: tuple[float, float]
    groups: tuple[CrossSamplePeakGroupHypothesis, ...]

    def __post_init__(self) -> None:
        mz_interval = _validated_interval(self.mz_interval, field="mz_interval")
        rt_interval = _validated_interval(self.rt_interval, field="rt_interval")
        groups = _canonical_groups(self.groups)
        _validate_group_owners(groups)
        _validate_group_bounds(
            groups,
            mz_interval=mz_interval,
            rt_interval=rt_interval,
        )
        object.__setattr__(self, "mz_interval", mz_interval)
        object.__setattr__(self, "rt_interval", rt_interval)
        object.__setattr__(self, "groups", groups)


def _canonical_groups(
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    if not isinstance(groups, tuple):
        raise ValueError("groups must be an immutable tuple")
    if any(not isinstance(group, CrossSamplePeakGroupHypothesis) for group in groups):
        raise ValueError("groups must contain existing group hypotheses")
    group_ids = tuple(
        _required_text(group.group_hypothesis_id, field="group_hypothesis_id")
        for group in groups
    )
    if len(group_ids) != len(set(group_ids)):
        raise ValueError("groups contain duplicate group_hypothesis_id")
    return tuple(sorted(groups, key=lambda group: group.group_hypothesis_id))


def _validate_group_owners(
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
) -> None:
    owner_ids: list[str] = []
    for group in groups:
        if not isinstance(group.owner_ids, tuple):
            raise ValueError("group owner_ids must be a tuple")
        group_owner_ids = tuple(
            _required_text(owner_id, field="group owner_ids")
            for owner_id in group.owner_ids
        )
        if len(group_owner_ids) != len(set(group_owner_ids)):
            raise ValueError("group owner_ids must be unique")
        owner_ids.extend(group_owner_ids)
    if len(owner_ids) != len(set(owner_ids)):
        raise ValueError("ModeTrack groups must not share owner ids")


def _validate_group_bounds(
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    *,
    mz_interval: tuple[float, float],
    rt_interval: tuple[float, float],
) -> None:
    for group in groups:
        center_mz = _finite_float(group.group_center_mz, field="group_center_mz")
        center_rt = _finite_float(group.group_center_rt, field="group_center_rt")
        if not mz_interval[0] <= center_mz <= mz_interval[1]:
            raise ValueError(
                f"ModeTrack {group.group_hypothesis_id!r} is outside the assignment "
                "scope m/z interval"
            )
        if not rt_interval[0] <= center_rt <= rt_interval[1]:
            raise ValueError(
                f"ModeTrack {group.group_hypothesis_id!r} is outside the assignment "
                "scope RT interval"
            )


def _validated_interval(
    value: tuple[float, float],
    *,
    field: str,
) -> tuple[float, float]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError(f"{field} must contain exactly two bounds")
    lower = _finite_float(value[0], field=f"{field}[0]")
    upper = _finite_float(value[1], field=f"{field}[1]")
    if lower >= upper:
        raise ValueError(f"{field} lower bound must be less than upper bound")
    return (lower, upper)


def _finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{field} must be finite")
    return 0.0 if normalized == 0.0 else normalized


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    if value != value.strip():
        raise ValueError(f"{field} must not contain surrounding whitespace")
    return value


__all__ = ("ModeTrackAssignmentScope",)
