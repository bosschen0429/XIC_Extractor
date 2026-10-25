"""Short-scale measurement quality is descriptive, never a peak/write gate."""

import math

import numpy as np
import pytest

from xic_extractor.peak_detection.signal_quality import (
    measure_signal_quality,
    short_scale_noise_sigma,
)


def test_three_point_noise_uses_rt_weights_and_sigma_units():
    rt = np.array([0.0, 1.0, 3.0, 4.0, 6.0, 7.0])
    raw = np.array([5.0, 8.0, 4.0, 12.0, 6.0, 10.0])
    # Independent scalar interpolation and error propagation oracle.
    residuals = []
    for i in range(1, len(rt) - 1):
        left = (rt[i + 1] - rt[i]) / (rt[i + 1] - rt[i - 1])
        right = 1.0 - left
        residuals.append(
            (left * raw[i - 1] + right * raw[i + 1] - raw[i])
            / math.sqrt(1.0 + left * left + right * right)
        )
    centre = float(np.median(residuals))
    mad = float(np.median([abs(value - centre) for value in residuals]))
    sigma = short_scale_noise_sigma(rt, raw)
    assert sigma == pytest.approx(1.4826 * mad)
    assert short_scale_noise_sigma(rt, raw + 50 + 7 * rt) == pytest.approx(sigma)
    # Unit conversion preserves the old contrast boundary on both sides.
    for old_threshold in (4.0, 3.0 * 1.4826, 5.0):
        for relative_height in (0.99, 1.01):
            height = old_threshold * mad * relative_height
            assert (height >= old_threshold * mad) == (
                height >= (old_threshold / 1.4826) * sigma
            )


def test_peak_snr_subtracts_baseline_and_does_not_change_area_inputs():
    rt = np.arange(7, dtype=float)
    raw = np.array([10.0, 12.0, 9.0, 35.0, 14.0, 8.0, 11.0])
    baseline = np.full(7, 10.0)
    before = raw.copy()
    quality = measure_signal_quality(rt, raw, baseline=baseline, apex_index=3)
    shifted = measure_signal_quality(
        rt, raw + 100, baseline=baseline + 100, apex_index=3
    )
    assert quality == shifted
    assert quality.status == "available"
    assert quality.signal_to_noise == pytest.approx(25.0 / quality.noise_sigma)
    assert quality.low_signal_to_noise == (quality.signal_to_noise < 10.0 / 1.4826)
    np.testing.assert_array_equal(raw, before)


@pytest.mark.parametrize(
    "rt,raw,status",
    [
        ([0, 1], [0, 1], "insufficient_points"),
        ([0, 1, 2, 3], [10, 10, 10, 10], "zero_noise"),
        ([0, 1, 1, 2], [10, 20, 30, 10], "invalid_trace"),
        ([0, 1, 2, 3], [10, math.nan, 30, 10], "invalid_trace"),
    ],
)
def test_unavailable_noise_is_not_infinite_snr_or_good_quality(rt, raw, status):
    quality = measure_signal_quality(
        np.asarray(rt, dtype=float),
        np.asarray(raw, dtype=float),
        baseline=np.zeros(len(raw)),
        apex_index=1,
    )
    assert quality.status == status
    assert quality.signal_to_noise is None
    assert quality.low_signal_to_noise is None
    assert quality.scoring_quality == "unknown"


def test_missing_baseline_does_not_use_raw_height_as_signal():
    quality = measure_signal_quality(
        np.arange(5, dtype=float),
        np.array([10.0, 11.0, 20.0, 13.0, 10.0]),
        baseline=None,
        apex_index=2,
    )
    assert quality.status == "missing_baseline"
    assert quality.signal_to_noise is None
    assert quality.low_signal_to_noise is None
