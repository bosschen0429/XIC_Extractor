import numpy as np
import pytest

from xic_extractor.discovery.ms1_backfill import compute_ms1_scan_support_score
from xic_extractor.peak_detection.models import PeakResult


def test_scan_support_empty_rt_returns_zero() -> None:
    assert (
        compute_ms1_scan_support_score(
            np.asarray([], dtype=float),
            _peak(start=1.0, end=2.0),
            scans_target=10,
        )
        == 0.0
    )


def test_scan_support_no_scans_inside_peak_bounds_returns_zero() -> None:
    score = compute_ms1_scan_support_score(
        np.asarray([0.5, 0.8, 2.2], dtype=float),
        _peak(start=1.0, end=2.0),
        scans_target=10,
    )

    assert score == 0.0


def test_scan_support_counts_points_inside_inclusive_peak_bounds() -> None:
    score = compute_ms1_scan_support_score(
        np.asarray([0.9, 1.0, 1.2, 2.0, 2.1], dtype=float),
        _peak(start=1.0, end=2.0),
        scans_target=10,
    )

    assert score == 0.3


def test_scan_support_caps_at_one() -> None:
    score = compute_ms1_scan_support_score(
        np.asarray([1.0, 1.1, 1.2, 1.3, 1.4], dtype=float),
        _peak(start=1.0, end=2.0),
        scans_target=3,
    )

    assert score == 1.0


def test_scan_support_rejects_non_positive_target() -> None:
    with pytest.raises(ValueError, match="scans_target must be greater than 0"):
        compute_ms1_scan_support_score(
            np.asarray([1.0], dtype=float),
            _peak(start=1.0, end=2.0),
            scans_target=0,
        )


def _peak(*, start: float, end: float) -> PeakResult:
    return PeakResult(
        rt=(start + end) / 2.0,
        intensity=100.0,
        intensity_smoothed=100.0,
        area=50.0,
        peak_start=start,
        peak_end=end,
    )
