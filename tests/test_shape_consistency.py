"""A row enters the Matrix only when its members share one elution shape."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from tests.alignment_pipeline_helpers import _owner
from tests.owner_peak_fixtures import with_peak
from xic_extractor.alignment.cross_sample.peak_groups import (
    _hypothesis_from_owner_group,
)
from xic_extractor.alignment.shape_consistency import (
    reject_inconsistent_groups,
    shape_consistency,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    PHYSICAL_PEAK_POINT_POLICY,
    evaluate_physical_peak,
    prepare_physical_signal,
)

_RT = np.round(np.arange(5.0, 9.0, 0.02), 4)


def _member(sample: str, trace: np.ndarray, query: float):
    evaluation = evaluate_physical_peak(
        prepare_physical_signal(_RT, trace),
        query_rt_min=query,
        selection_policy_version=PHYSICAL_PEAK_POINT_POLICY,
    )
    segment = evaluation.answer.selected_segment
    assert segment is not None
    owner = with_peak(
        _owner(sample, "m", apex_rt=segment.apex_rt_min, precursor_mz=300.0),
        apex_rt=segment.apex_rt_min,
        start_rt=segment.left_rt_min,
        end_rt=segment.right_rt_min,
        area=segment.area_counts_seconds,
    )
    return replace(
        owner,
        hypothesis=replace(
            owner.hypothesis, peak=replace(owner.peak, physical_evaluation=evaluation)
        ),
    )


def _gaussian(apex: float, height: float, width: float = 0.06) -> np.ndarray:
    return height * np.exp(-0.5 * ((_RT - apex) / width) ** 2)


def _group(*owners):
    return _hypothesis_from_owner_group(owners, disposition="established", evidence="")


def test_one_compound_shape_across_injections_is_kept() -> None:
    group = _group(
        _member("S1", _gaussian(7.0, 1e5), 7.0),
        _member("S2", _gaussian(7.05, 3e4), 7.05),
        _member("S3", _gaussian(6.95, 6e5), 6.95),
    )

    assert shape_consistency(group) > 0.95
    assert reject_inconsistent_groups((group,)) == (group,)


def test_members_with_different_shapes_leave_the_matrix() -> None:
    narrow = _member("S1", _gaussian(7.0, 1e5), 7.0)
    plateau = _member(
        "S2", np.where((_RT > 6.5) & (_RT < 7.5), 1e5, 0.0) + _gaussian(7.0, 1e4), 7.0
    )
    late_shoulder = _member(
        "S3", _gaussian(7.0, 3e4, 0.2) + _gaussian(7.45, 1e5, 0.05), 7.0
    )
    group = _group(narrow, plateau, late_shoulder)

    (rejected,) = reject_inconsistent_groups((group,))

    assert shape_consistency(group) < 0.6
    assert rejected.disposition == "rejected"
    assert rejected.destination == "archive"
    assert "inconsistent_shape" in rejected.evidence


def test_a_single_member_has_no_consistency_and_is_kept() -> None:
    group = _group(_member("S1", _gaussian(7.0, 1e5), 7.0))

    assert shape_consistency(group) is None
    assert reject_inconsistent_groups((group,)) == (group,)
