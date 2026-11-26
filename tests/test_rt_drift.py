"""Batch RT drift: anchor channels, frozen B and M, and two-coefficient groups."""

from __future__ import annotations

import math
from itertools import combinations
from statistics import median

import pytest

from xic_extractor.alignment.rt_drift import (
    DriftAnchor,
    DriftCoefficients,
    RtDriftModel,
    _drift_model,
    _row_voted_shift,
    candidate_coefficients,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration

SAMPLES = tuple(f"s{i:02}" for i in range(12))
ORDERS = {sample: index + 1 for index, sample in enumerate(SAMPLES)}
SHIFT = dict(
    zip(
        SAMPLES,
        (0.0, 0.6, -0.4, 0.9, -0.8, 0.3, -0.2, 0.8, -0.6, 0.5, -0.9, 0.2),
        strict=True,
    )
)
FLAT = dict.fromkeys(SAMPLES, 0.0)


def _frame(model: RtDriftModel) -> SampleRtFrame:
    identity = BatchRtCalibration.from_coordinate_knots(
        tuple((sample, ((0.0, 0.0), (60.0, 60.0))) for sample in SAMPLES)
    )
    return SampleRtFrame(
        injection_order=ORDERS,
        sample_cohorts=dict.fromkeys(SAMPLES, ""),
        sample_universe=frozenset(SAMPLES),
    ).with_sample_calibration(identity, model)


def _model(shift=SHIFT) -> RtDriftModel:
    anchor = DriftAnchor("anchor", 30.0, 0, FLAT)
    return RtDriftModel((anchor,), dict(shift), "samples")


def test_coefficients_need_enough_members_and_regressor_spread():
    few = [(10.0 + v, 0.0, v) for v in (0.0, 0.5, 1.0)]
    assert candidate_coefficients(few) == ()
    flat = [(10.0, 0.0, 0.0)] * 8
    assert candidate_coefficients(flat) == ()
    fitted = candidate_coefficients([(10.0 + 2 * v, None, v) for v in SHIFT.values()])
    assert fitted == (DriftCoefficients(sample_shift=pytest.approx(2.0)),)


@pytest.mark.parametrize("missing", (False, True))
def test_residual_fits_preserve_the_pairwise_median_slope(missing):
    """Independent scalar Theil-Sen oracle, including tied and missing regressors."""
    points = [
        (
            10.0 + 0.8 * (index // 2) + 1.1 * shift + (index % 3) * 0.01,
            None if missing and index == 3 else float(index // 2),
            None if missing and index == 7 else shift,
        )
        for index, shift in enumerate(SHIFT.values())
    ]

    def slope(xs, ys):
        return median(
            (ys[j] - ys[i]) / (xs[j] - xs[i])
            for i, j in combinations(range(len(xs)), 2)
            if xs[i] != xs[j]
        )

    with_b = [(y, b) for y, b, _ in points if b is not None]
    with_m = [(y, m) for y, _, m in points if m is not None]
    both = [(y, b, m) for y, b, m in points if b is not None and m is not None]
    ys, bs, ms = zip(*both, strict=True)
    a, c = slope(bs, ys), 0.0
    for _ in range(20):
        next_c = slope(ms, [y - a * b for y, b in zip(ys, bs, strict=True)])
        next_a = slope(bs, [y - next_c * m for y, m in zip(ys, ms, strict=True)])
        converged = abs(next_a - a) < 1e-9 and abs(next_c - c) < 1e-9
        a, c = next_a, next_c
        if converged:
            break
    expected = (
        DriftCoefficients(time=slope([b for _, b in with_b], [y for y, _ in with_b])),
        DriftCoefficients(
            sample_shift=slope([m for _, m in with_m], [y for y, _ in with_m])
        ),
        DriftCoefficients(time=a, sample_shift=c),
    )
    assert candidate_coefficients(points) == expected


def test_anchor_without_shift_response_keeps_samples_whose_shift_is_unknown():
    measured = SAMPLES[:-1]
    calibrated = {
        "a": {s: 10.0 + SHIFT[s] for s in measured},
        "b": {s: 20.0 + SHIFT[s] for s in measured},
        "flat": dict.fromkeys(SAMPLES, 30.0),
    }
    model = _drift_model(
        calibrated,
        injection_order=ORDERS,
        qc_samples=frozenset(),
        cohorts={},
        families={"a": 0, "b": 1, "flat": 2},
        owner_ids={},
    )
    assert "s11" not in model.sample_shift_min
    flat = next(anchor for anchor in model.anchors if anchor.channel == "flat")
    assert set(flat.shape) == set(SAMPLES)


def test_borrowed_batch_state_is_shared_only_by_queries_without_an_own_anchor():
    rising = {sample: index / 11 for index, sample in enumerate(SAMPLES)}
    falling = {sample: 1 - value for sample, value in rising.items()}
    model = RtDriftModel(
        (
            DriftAnchor("early", 10.0, 0, rising, owner_ids=frozenset({"own-early"})),
            DriftAnchor("late", 13.0, 1, falling, owner_ids=frozenset({"own-late"})),
        )
    )
    lookup = model.batch_state_lookup()
    borrowed = dict(lookup(("a",), 11.0))
    assert borrowed not in (rising, falling)
    assert lookup(("own-late", "b"), 11.0) == falling
    assert lookup(("b", "c"), 11.0) == borrowed
    reversed_lookup = model.batch_state_lookup()
    assert reversed_lookup(("own-early",), 11.0) == rising
    assert reversed_lookup(("d",), 11.0) == borrowed
    late_only = RtDriftModel((DriftAnchor("late", 13.0, 1, falling),))
    other = late_only.batch_state_lookup()(("a",), 11.0)
    assert other != borrowed
    assert other == late_only.batch_state(("e",), 11.0)
    assert lookup(("a",), 11.0) == borrowed


_ROW_SAMPLES = [f"S{index:02d}" for index in range(30)]
_ROW_ORDER = {sample: index for index, sample in enumerate(_ROW_SAMPLES)}
_TRUE_SHIFT = {
    sample: math.sin(1.7 * index) + 0.4 * math.cos(0.9 * index)
    for index, sample in enumerate(_ROW_SAMPLES)
}


def _shift_rows(responses):
    return [
        {
            sample: 10.0 + index + response * _TRUE_SHIFT[sample]
            for sample in _ROW_SAMPLES
        }
        for index, response in enumerate(responses)
    ]


def test_rows_correct_a_sample_shift_the_anchors_got_wrong():
    anchors = {**_TRUE_SHIFT, "S12": _TRUE_SHIFT["S12"] + 1.5}
    rows = _shift_rows((0.5, 0.8, 1.0, 1.2, 1.5))

    voted = _row_voted_shift(anchors, rows, _ROW_ORDER)

    assert voted == pytest.approx(_TRUE_SHIFT, abs=0.02)
    assert _row_voted_shift(anchors, rows[::-1], _ROW_ORDER) == voted


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param(_shift_rows((0.8, 1.2)), id="fewer rows than votes needed"),
        pytest.param(
            [
                {s: rt for s, rt in row.items() if _ROW_ORDER[s] < 15}
                for row in _shift_rows((0.5, 0.8, 1.0, 1.2, 1.5))
            ],
            id="rows observed in too few samples",
        ),
        pytest.param(
            [
                {
                    s: 10.0 + 0.05 * math.sin(3.1 * i + k)
                    for i, s in enumerate(_ROW_SAMPLES)
                }
                for k in range(5)
            ],
            id="rows that do not follow the shift",
        ),
    ],
)
def test_sample_shift_stays_with_the_anchors_without_enough_following_rows(rows):
    wrong = {**_TRUE_SHIFT, "S12": _TRUE_SHIFT["S12"] + 1.5}

    assert _row_voted_shift(wrong, rows, _ROW_ORDER) == wrong
