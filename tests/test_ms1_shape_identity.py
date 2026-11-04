from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from xic_extractor.peak_detection.ms1_shape_identity import (
    compare_local_peak_shapes,
    prepare_local_peak_shape,
)


def test_shape_comparison_requires_acquired_points_and_shared_context() -> None:
    complete = prepare_local_peak_shape(
        np.linspace(9.5, 10.5, 101),
        np.exp(-0.5 * (np.linspace(-0.5, 0.5, 101) / 0.07) ** 2),
        apex_rt_min=10,
    )
    sparse = prepare_local_peak_shape([9.7, 10, 10.3], [0, 1, 0], apex_rt_min=10)
    clipped = prepare_local_peak_shape(
        np.linspace(9.95, 10.05, 11),
        np.linspace(1, 0, 11),
        apex_rt_min=10,
    )
    assert compare_local_peak_shapes(complete, sparse).reason == (
        "too_few_acquired_shape_points"
    )
    result = compare_local_peak_shapes(complete, clipped)
    assert result.reason == "insufficient_shared_shape_context"
    assert result.similarity is None


def test_batch_shape_comparisons_preserve_partial_flat_and_sparse_evidence(monkeypatch):
    from xic_extractor.peak_detection.ms1_shape_identity import (
        LocalPeakShape,
        LocalPeakShapeComparisons,
    )

    rng = np.random.default_rng(42)
    shapes = [
        LocalPeakShape(0.35, tuple(rng.uniform(0, 1, 25)), 40, 10, (0.1, 0.2))
        for _ in range(10)
    ]
    shapes += [
        replace(shape, intensity=(None,) * 4 + shape.intensity[4:]) for shape in shapes
    ]
    shapes += [
        replace(shapes[0], intensity=(0.5,) * 25),
        replace(shapes[0], source_point_count=3, half_height_point_count=2),
        replace(shapes[0], half_height_point_count=None),
        replace(shapes[0], half_window_min=0.5),
        replace(shapes[0], intensity=(0.5,) * 20 + (0.1, 0.2, 0.3, 0.4, 0.5)),
        replace(shapes[0], intensity=(0.5,) * 20 + (None,) * 5),
    ]
    expected = [
        compare_local_peak_shapes(left, right) for left in shapes for right in shapes
    ]
    calls = 0
    original = np.corrcoef

    def correlation(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(np, "corrcoef", correlation)
    batch = LocalPeakShapeComparisons(shapes)
    actual = [batch.compare(left, right) for left in shapes for right in shapes]
    assert calls < len(expected) // 2
    for before, after in zip(expected, actual, strict=True):
        assert (after.status, after.reason, after.compared_point_count) == (
            before.status,
            before.reason,
            before.compared_point_count,
        )
        if before.similarity is None:
            assert after.similarity is None
        else:
            assert after.similarity == pytest.approx(before.similarity, abs=1e-14)


def test_interpolated_zeros_do_not_resolve_sparse_primary_shape() -> None:
    rt = np.linspace(-0.35, 0.35, 141)
    dense = np.exp(-0.5 * (rt / 0.03) ** 2)
    sparse = np.zeros_like(rt)
    sparse[70:72] = [1, 0.9]
    left = prepare_local_peak_shape(rt, sparse, apex_rt_min=0)
    right = prepare_local_peak_shape(rt, dense, apex_rt_min=0)

    assert left.source_point_count == 141
    assert left.half_height_point_count == 2
    for a, b in ((left, right), (right, left), (left, left)):
        result = compare_local_peak_shapes(a, b)
        assert result.reason == "insufficient_primary_shape_resolution"
        assert result.similarity is None


def test_sparse_localized_peak_still_contradicts_sustained_background() -> None:
    rt = np.linspace(-0.35, 0.35, 141)
    sparse = np.zeros_like(rt)
    sparse[70:72] = [1, 0.9]
    peak = prepare_local_peak_shape(rt, sparse, apex_rt_min=0)
    background = prepare_local_peak_shape(rt, 0.8 + 0.2 * rt, apex_rt_min=0)

    for a, b in ((peak, background), (background, peak)):
        result = compare_local_peak_shapes(a, b)
        assert result.status == "ok"
        assert result.reason == "localized_vs_sustained_shape_contrast"
        assert result.similarity is not None and result.similarity < 0.8


def test_zero_separated_neighbor_does_not_change_primary_shape_or_input() -> None:
    rt = np.linspace(-0.35, 0.35, 141)
    primary = np.where(abs(rt) < 0.08, np.exp(-0.5 * (rt / 0.03) ** 2), 0)
    with_neighbor = primary.copy()
    with_neighbor[115:125] = 2
    original = with_neighbor.copy()
    clean = prepare_local_peak_shape(rt, primary, apex_rt_min=0)
    contaminated = prepare_local_peak_shape(rt, with_neighbor, apex_rt_min=0)

    np.testing.assert_array_equal(with_neighbor, original)
    assert contaminated == clean
    assert compare_local_peak_shapes(clean, contaminated).similarity == pytest.approx(1)


def test_missing_resolution_metadata_does_not_invent_shape_evidence() -> None:
    rt = np.linspace(-0.35, 0.35, 141)
    shape = prepare_local_peak_shape(rt, np.exp(-0.5 * (rt / 0.03) ** 2), apex_rt_min=0)
    legacy = replace(shape, half_height_point_count=None, edge_intensity_ratio=None)
    assert compare_local_peak_shapes(legacy, shape).reason == (
        "unassessed_shape_resolution"
    )


def test_same_analytic_peak_at_sparse_and_dense_sampling_is_not_contradiction() -> None:
    shapes = [
        prepare_local_peak_shape(rt, np.exp(-0.5 * (rt / 0.03) ** 2), apex_rt_min=0)
        for rt in (np.linspace(-0.35, 0.35, 22), np.linspace(-0.35, 0.35, 141))
    ]
    assert shapes[0].half_height_point_count == 2
    result = compare_local_peak_shapes(*shapes)
    assert result.reason == "insufficient_primary_shape_resolution"
    assert result.similarity is None
