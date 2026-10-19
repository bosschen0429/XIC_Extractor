from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from xic_extractor.peak_detection.ms1_shape_identity import (
    compare_local_peak_shapes,
    competing_peak_summary,
    local_own_max_shape_similarity,
    own_max_normalized_trace,
    prepare_local_peak_shape,
)


def test_own_max_normalized_trace_scales_gaussian_smoothed_trace_to_one() -> None:
    trace = own_max_normalized_trace(
        [0, 1, 2, 3, 4],
        [0, 2, 10, 2, 0],
        smooth_points=3,
    )

    assert trace.status == "ok"
    assert trace.reason == "own_max_normalized"
    assert max(trace.intensity) == pytest.approx(1.0)
    assert trace.max_intensity is not None
    assert trace.max_intensity > 0


def test_local_own_max_similarity_is_scale_invariant_for_same_peak_shape() -> None:
    rt = np.linspace(8.7, 9.5, 81)
    left = np.exp(-0.5 * ((rt - 9.12) / 0.07) ** 2)
    right = left * 250.0

    result = local_own_max_shape_similarity(
        candidate_rt_min=9.12,
        candidate_rt=rt,
        candidate_intensity=left,
        reference_rt_min=9.12,
        reference_rt=rt,
        reference_intensity=right,
        smooth_points=5,
    )

    assert result.status == "ok"
    assert result.compared_point_count > 5
    assert result.similarity == pytest.approx(1.0)


def test_local_own_max_similarity_reports_inconclusive_for_flat_trace() -> None:
    rt = np.linspace(8.7, 9.5, 81)

    result = local_own_max_shape_similarity(
        candidate_rt_min=9.12,
        candidate_rt=rt,
        candidate_intensity=np.zeros_like(rt),
        reference_rt_min=9.12,
        reference_rt=rt,
        reference_intensity=np.ones_like(rt),
        smooth_points=5,
    )

    assert result.status == "inconclusive"
    assert result.reason == "similarity_unavailable"
    assert result.similarity is None


def test_shape_comparison_requires_acquired_points_and_shared_context() -> None:
    complete = prepare_local_peak_shape(
        np.linspace(9.5, 10.5, 101),
        np.exp(-.5 * (np.linspace(-.5, .5, 101) / .07) ** 2),
        apex_rt_min=10,
    )
    sparse = prepare_local_peak_shape([9.7, 10, 10.3], [0, 1, 0], apex_rt_min=10)
    clipped = prepare_local_peak_shape(
        np.linspace(9.95, 10.05, 11), np.linspace(1, 0, 11), apex_rt_min=10,
    )
    assert compare_local_peak_shapes(complete, sparse).reason == (
        "too_few_acquired_shape_points"
    )
    result = compare_local_peak_shapes(complete, clipped)
    assert result.reason == "insufficient_shared_shape_context"
    assert result.similarity is None


def test_competing_peak_summary_reports_strongest_outside_candidate_window() -> None:
    rt = np.linspace(8.7, 9.8, 111)
    candidate = np.exp(-0.5 * ((rt - 9.12) / 0.04) ** 2)
    competitor = 0.42 * np.exp(-0.5 * ((rt - 9.62) / 0.04) ** 2)
    intensity = candidate + competitor

    summary = competing_peak_summary(
        rt,
        intensity,
        candidate_rt_min=9.12,
        exclusion_window_min=0.20,
        min_peak_ratio=0.05,
        smooth_points=5,
    )

    assert summary.status == "ok"
    assert summary.strongest_peak_rt_min == pytest.approx(9.12, abs=0.02)
    assert summary.strongest_peak_own_max_ratio == pytest.approx(1.0)
    assert summary.strongest_competing_peak_rt_min == pytest.approx(9.62, abs=0.02)
    assert summary.strongest_competing_peak_own_max_ratio == pytest.approx(
        0.42,
        rel=0.15,
    )


def test_own_max_normalized_trace_fails_closed_for_bad_inputs() -> None:
    length_mismatch = own_max_normalized_trace([1, 2], [1])
    non_positive = own_max_normalized_trace([1, 2, 3], [0, 0, 0])
    no_finite = own_max_normalized_trace([math.nan, math.nan], [math.nan, math.nan])

    assert length_mismatch.status == "inconclusive"
    assert length_mismatch.reason == "length_mismatch"
    assert non_positive.status == "inconclusive"
    assert non_positive.reason == "non_positive_signal"
    assert no_finite.status == "inconclusive"
    assert no_finite.reason == "no_finite_points"


def test_interpolated_zeros_do_not_resolve_sparse_primary_shape() -> None:
    rt = np.linspace(-.35, .35, 141)
    dense = np.exp(-.5 * (rt / .03) ** 2)
    sparse = np.zeros_like(rt)
    sparse[70:72] = [1, .9]
    left = prepare_local_peak_shape(rt, sparse, apex_rt_min=0)
    right = prepare_local_peak_shape(rt, dense, apex_rt_min=0)

    assert left.source_point_count == 141
    assert left.half_height_point_count == 2
    for a, b in ((left, right), (right, left), (left, left)):
        result = compare_local_peak_shapes(a, b)
        assert result.reason == "insufficient_primary_shape_resolution"
        assert result.similarity is None


def test_sparse_localized_peak_still_contradicts_sustained_background() -> None:
    rt = np.linspace(-.35, .35, 141)
    sparse = np.zeros_like(rt)
    sparse[70:72] = [1, .9]
    peak = prepare_local_peak_shape(rt, sparse, apex_rt_min=0)
    background = prepare_local_peak_shape(rt, .8 + .2 * rt, apex_rt_min=0)

    for a, b in ((peak, background), (background, peak)):
        result = compare_local_peak_shapes(a, b)
        assert result.is_usable
        assert result.reason == "localized_vs_sustained_shape_contrast"
        assert result.similarity is not None and result.similarity < .8


def test_zero_separated_neighbor_does_not_change_primary_shape_or_input() -> None:
    rt = np.linspace(-.35, .35, 141)
    primary = np.where(abs(rt) < .08, np.exp(-.5 * (rt / .03) ** 2), 0)
    with_neighbor = primary.copy()
    with_neighbor[115:125] = 2
    original = with_neighbor.copy()
    clean = prepare_local_peak_shape(rt, primary, apex_rt_min=0)
    contaminated = prepare_local_peak_shape(rt, with_neighbor, apex_rt_min=0)

    np.testing.assert_array_equal(with_neighbor, original)
    assert contaminated == clean
    assert compare_local_peak_shapes(clean, contaminated).similarity == pytest.approx(1)


def test_missing_resolution_metadata_does_not_invent_shape_evidence() -> None:
    rt = np.linspace(-.35, .35, 141)
    shape = prepare_local_peak_shape(rt, np.exp(-.5 * (rt / .03) ** 2), apex_rt_min=0)
    legacy = replace(shape, half_height_point_count=None, edge_intensity_ratio=None)
    assert compare_local_peak_shapes(legacy, shape).reason == (
        "unassessed_shape_resolution"
    )


def test_same_analytic_peak_at_sparse_and_dense_sampling_is_not_contradiction() -> None:
    shapes = [
        prepare_local_peak_shape(rt, np.exp(-.5 * (rt / .03) ** 2), apex_rt_min=0)
        for rt in (np.linspace(-.35, .35, 22), np.linspace(-.35, .35, 141))
    ]
    assert shapes[0].half_height_point_count == 2
    result = compare_local_peak_shapes(*shapes)
    assert result.reason == "insufficient_primary_shape_resolution"
    assert result.similarity is None
