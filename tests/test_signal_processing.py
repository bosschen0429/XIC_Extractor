import warnings
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import savgol_filter

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.integration import peak_bounds
from xic_extractor.peak_detection.local_minimum import _local_minimum_regions
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidatesResult,
    PeakResult,
)
from xic_extractor.peak_detection.peak_integration import find_peak_candidates
from xic_extractor.signal_processing import find_peak_and_area


def _config(
    *,
    smooth_window: int = 15,
    smooth_polyorder: int = 3,
    peak_rel_height: float = 0.95,
    peak_min_prominence_ratio: float = 0.10,
    resolver_mode: str = "savgol",
    resolver_chrom_threshold: float = 0.05,
    resolver_min_search_range_min: float = 0.04,
    resolver_min_relative_height: float = 0.05,
    resolver_min_absolute_height: float = 25.0,
    resolver_min_ratio_top_edge: float = 1.3,
    resolver_peak_duration_min: float = 0.03,
    resolver_peak_duration_max: float = 1.00,
    resolver_min_scans: int = 5,
) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("raw"),
        dll_dir=Path("dll"),
        output_csv=Path("output/xic_results.csv"),
        diagnostics_csv=Path("output/xic_diagnostics.csv"),
        smooth_window=smooth_window,
        smooth_polyorder=smooth_polyorder,
        peak_rel_height=peak_rel_height,
        peak_min_prominence_ratio=peak_min_prominence_ratio,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        count_no_ms2_as_detected=False,
        resolver_mode=resolver_mode,
        resolver_chrom_threshold=resolver_chrom_threshold,
        resolver_min_search_range_min=resolver_min_search_range_min,
        resolver_min_relative_height=resolver_min_relative_height,
        resolver_min_absolute_height=resolver_min_absolute_height,
        resolver_min_ratio_top_edge=resolver_min_ratio_top_edge,
        resolver_peak_duration_min=resolver_peak_duration_min,
        resolver_peak_duration_max=resolver_peak_duration_max,
        resolver_min_scans=resolver_min_scans,
    )


def _gaussian(
    rt: np.ndarray, *, center: float, sigma: float, height: float
) -> np.ndarray:
    return height * np.exp(-0.5 * ((rt - center) / sigma) ** 2)


def test_clean_gaussian_peak_returns_raw_apex_and_area() -> None:
    rt = np.linspace(8.0, 10.0, 401)
    intensity = _gaussian(rt, center=9.0, sigma=0.08, height=1000.0)
    # rt is in minutes, area is reported in counts·seconds (LC-MS convention).
    expected_area = 1000.0 * 0.08 * np.sqrt(2 * np.pi) * 60.0

    result = find_peak_and_area(rt, intensity, _config())

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.0, abs=0.01)
    assert result.peak.intensity == pytest.approx(1000.0, rel=0.02)
    assert result.peak.area == pytest.approx(expected_area, rel=0.02)
    assert result.n_points == len(rt)
    assert result.n_prominent_peaks == 1


def test_savgol_candidates_use_current_source_name_and_reject_old_mode() -> None:
    from xic_extractor.peak_detection.candidate_search import find_peak_candidates

    rt = np.linspace(8.0, 10.0, 401)
    intensity = _gaussian(rt, center=9.0, sigma=0.08, height=1000.0)
    result = find_peak_candidates(rt, intensity, _config(resolver_mode="savgol"))
    assert len(result.candidates) == 1
    assert result.candidates[0].proposal_sources == ("savgol",)
    with pytest.raises(ValueError, match="unsupported resolver mode"):
        find_peak_candidates(rt, intensity, _config(resolver_mode="legacy_savgol"))


def test_noisy_peak_recovers_raw_area_within_noise_tolerance() -> None:
    # Hard-shape oracle: a peak in real noise (S/N ~20) must still report a raw
    # area close to the true Gaussian integral. The S/N is chosen so the
    # recovered area stays well inside rel=0.05 across seeds (max ~4% over a
    # 300-seed sweep), not merely for this one seed, keeping the test robust to
    # platform float drift rather than relying on a lucky draw.
    rt = np.linspace(8.0, 10.0, 401)
    rng = np.random.default_rng(20260614)
    intensity = _gaussian(rt, center=9.0, sigma=0.08, height=300.0)
    intensity = intensity + rng.normal(0.0, 15.0, rt.size)
    expected_area = 300.0 * 0.08 * np.sqrt(2 * np.pi) * 60.0

    result = find_peak_and_area(rt, intensity, _config())

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.0, abs=0.05)
    assert result.peak.area == pytest.approx(expected_area, rel=0.05)


def test_resolved_twin_area_does_not_bleed_into_neighbor() -> None:
    # Hard-shape oracle: with two well-resolved peaks, the selected (taller)
    # peak's raw area must match its own analytic area, not be inflated by the
    # neighbour 0.7 min away.
    rt = np.linspace(8.0, 10.0, 401)
    intensity = _gaussian(rt, center=8.7, sigma=0.05, height=500.0)
    intensity += _gaussian(rt, center=9.4, sigma=0.06, height=1200.0)
    expected_area = 1200.0 * 0.06 * np.sqrt(2 * np.pi) * 60.0

    result = find_peak_and_area(rt, intensity, _config())

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.4, abs=0.01)
    assert result.peak.area == pytest.approx(expected_area, rel=0.03)


def test_overlapping_peaks_conserve_total_area_when_merged() -> None:
    # Hard-shape oracle: two heavily-overlapping peaks (centres 0.10 min apart,
    # sigma 0.06) merge into one envelope. The integrated raw area must conserve
    # the total of both components (no area lost or double-counted). The 0.10 min
    # separation sits well below the resolver's ~0.17 min split threshold, so the
    # single-envelope expectation is robust to minor float drift; if the resolver
    # is later changed to split such shoulders, update n_prominent_peaks.
    rt = np.linspace(8.0, 10.0, 401)
    intensity = _gaussian(rt, center=9.0, sigma=0.06, height=1000.0)
    intensity += _gaussian(rt, center=9.10, sigma=0.06, height=800.0)
    total_area = (1000.0 + 800.0) * 0.06 * np.sqrt(2 * np.pi) * 60.0

    result = find_peak_and_area(rt, intensity, _config())

    assert result.status == "OK"
    assert result.peak is not None
    assert result.n_prominent_peaks == 1
    assert result.peak.area == pytest.approx(total_area, rel=0.03)


def test_peak_bounds_handles_non_peak_apex_without_warning() -> None:
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        left, right = peak_bounds(np.ones(5), 2, 0.95, 5)

    assert captured == []
    assert (left, right) == (2, 3)


def test_two_peak_signal_chooses_highest_smoothed_peak() -> None:
    rt = np.linspace(8.0, 10.0, 401)
    intensity = _gaussian(rt, center=8.7, sigma=0.05, height=500.0)
    intensity += _gaussian(rt, center=9.4, sigma=0.06, height=1200.0)

    result = find_peak_and_area(rt, intensity, _config())

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.4, abs=0.01)
    assert result.n_prominent_peaks == 2


def test_candidate_enumeration_returns_all_prominent_peaks() -> None:
    rt = np.linspace(8.0, 10.0, 401)
    intensity = _gaussian(rt, center=8.7, sigma=0.05, height=500.0)
    intensity += _gaussian(rt, center=9.4, sigma=0.06, height=1200.0)

    result = find_peak_candidates(rt, intensity, _config())

    assert result.status == "OK"
    assert len(result.candidates) == 2
    assert [candidate.peak.rt for candidate in result.candidates] == pytest.approx(
        [8.7, 9.4],
        abs=0.01,
    )
    assert result.candidates[1].peak.intensity > result.candidates[0].peak.intensity


def test_local_minimum_split_reanchors_second_region_after_flat_valley() -> None:
    rt = np.arange(9, dtype=float) * 0.1
    intensity = np.array([0, 50, 100, 5, 0, 5, 80, 50, 0], dtype=float)

    regions = _local_minimum_regions(
        rt,
        intensity,
        peak_indices=[2, 6],
        threshold=10.0,
        config=_config(
            resolver_min_search_range_min=0.01,
            resolver_min_ratio_top_edge=1.3,
        ),
    )

    assert regions == [(1, 5), (5, 8)]


def test_raw_apex_reporting_does_not_return_zero_intensity_when_area_is_positive() -> (
    None
):
    rt = np.linspace(8.0, 10.0, 401)
    intensity = _gaussian(rt, center=9.0, sigma=0.08, height=1000.0)
    intensity[int(np.argmin(np.abs(rt - 9.0)))] = 0.0

    result = find_peak_and_area(rt, intensity, _config(smooth_window=31))

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.area > 0.0
    assert result.peak.intensity > 900.0
    assert result.peak.rt == pytest.approx(9.0, abs=0.01)
    assert result.peak.intensity_smoothed > 900.0
    assert result.candidates[0].raw_apex_rt == pytest.approx(8.995, abs=0.01)


def test_raw_spike_inside_peak_window_does_not_move_reported_peak_rt() -> None:
    rt = np.linspace(8.0, 10.0, 401)
    intensity = _gaussian(rt, center=9.0, sigma=0.08, height=1000.0)
    spike_idx = int(np.argmin(np.abs(rt - 8.88)))
    intensity[spike_idx] = 1400.0

    result = find_peak_and_area(rt, intensity, _config(smooth_window=31))

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.0, abs=0.01)
    assert result.peak.intensity == pytest.approx(1400.0)
    assert result.candidates[0].selection_apex_rt == pytest.approx(9.0, abs=0.01)
    assert result.candidates[0].raw_apex_rt == pytest.approx(8.88, abs=0.01)


def test_strict_preferred_rt_chooses_anchor_peak_even_when_neighbor_is_higher() -> None:
    rt = np.linspace(12.7, 14.8, 421)
    intensity = _gaussian(rt, center=13.06, sigma=0.06, height=330000.0)
    intensity += _gaussian(rt, center=13.79, sigma=0.05, height=38000.0)

    result = find_peak_and_area(
        rt,
        intensity,
        _config(),
        preferred_rt=13.75,
        strict_preferred_rt=True,
    )

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(13.79, abs=0.02)
    assert result.n_prominent_peaks == 2


def test_relaxed_search_finds_weaker_anchor_peak_when_strong_peak_is_far() -> None:
    rt = np.linspace(8.0, 10.0, 401)
    intensity = _gaussian(rt, center=8.48, sigma=0.04, height=1000.0)
    intensity += _gaussian(rt, center=9.03, sigma=0.05, height=80.0)

    result = find_peak_and_area(
        rt,
        intensity,
        _config(),
        preferred_rt=9.03,
    )

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.03, abs=0.02)


def test_relaxed_search_finds_peak_below_standard_prominence_threshold() -> None:
    rt = np.linspace(8.0, 10.0, 401)
    intensity = np.full_like(rt, 100000.0)
    intensity += _gaussian(rt, center=9.05, sigma=0.05, height=5000.0)

    result = find_peak_and_area(
        rt,
        intensity,
        _config(),
        preferred_rt=9.05,
    )

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.05, abs=0.02)


def test_strict_preferred_rt_does_not_relax_prominence_threshold() -> None:
    rt = np.linspace(8.0, 10.0, 401)
    intensity = np.full_like(rt, 100000.0)
    intensity += _gaussian(rt, center=9.05, sigma=0.05, height=5000.0)

    result = find_peak_and_area(
        rt,
        intensity,
        _config(),
        preferred_rt=9.05,
        strict_preferred_rt=True,
    )

    assert result.status == "PEAK_NOT_FOUND"
    assert result.peak is None


def test_positive_flat_noise_returns_peak_not_found() -> None:
    rt = np.linspace(0.0, 1.0, 60)
    intensity = np.full_like(rt, 12.0)

    result = find_peak_and_area(rt, intensity, _config())

    assert result.status == "PEAK_NOT_FOUND"
    assert result.peak is None
    assert result.max_smoothed == pytest.approx(12.0)
    assert result.n_prominent_peaks == 0


def test_deterministic_random_noise_returns_peak_not_found() -> None:
    rng = np.random.default_rng(20260414)
    rt = np.linspace(0.0, 1.0, 240)
    intensity = np.clip(rng.normal(loc=8.0, scale=3.0, size=len(rt)), 0.0, None)

    result = find_peak_and_area(rt, intensity, _config())

    assert result.status == "PEAK_NOT_FOUND"
    assert result.peak is None


def test_negative_baseline_noise_does_not_return_ok_peak() -> None:
    rng = np.random.default_rng(20260414)
    rt = np.linspace(0.0, 1.0, 240)
    intensity = rng.normal(loc=-5.0, scale=2.0, size=len(rt))

    result = find_peak_and_area(rt, intensity, _config())

    assert result.status in {"NO_SIGNAL", "PEAK_NOT_FOUND"}
    assert result.peak is None


def test_zero_signal_returns_no_signal() -> None:
    rt = np.linspace(0.0, 1.0, 60)
    intensity = np.zeros_like(rt)

    result = find_peak_and_area(rt, intensity, _config())

    assert result.status == "NO_SIGNAL"
    assert result.peak is None
    assert result.max_smoothed == pytest.approx(0.0)


def test_local_minimum_rejects_zero_duration_candidate() -> None:
    rt = np.array([8.0, 8.1, 8.2], dtype=float)
    intensity = np.array([0.0, 1000.0, 0.0], dtype=float)

    result = find_peak_and_area(
        rt,
        intensity,
        _config(
            resolver_mode="local_minimum",
            resolver_min_scans=1,
            resolver_peak_duration_min=0.0,
        ),
    )

    assert result.status == "PEAK_NOT_FOUND"
    assert result.peak is None


def test_empty_signal_returns_no_signal() -> None:
    result = find_peak_and_area(np.array([]), np.array([]), _config())

    assert result.status == "NO_SIGNAL"
    assert result.peak is None
    assert result.n_points == 0
    assert result.max_smoothed is None


def test_mismatched_rt_and_intensity_lengths_raise_value_error() -> None:
    with pytest.raises(ValueError, match="same length"):
        find_peak_and_area(np.array([0.0, 1.0]), np.array([1.0]), _config())


def test_short_signal_returns_window_too_short() -> None:
    rt = np.linspace(0.0, 1.0, 7)
    intensity = _gaussian(rt, center=0.5, sigma=0.1, height=100.0)

    result = find_peak_and_area(rt, intensity, _config(smooth_window=15))

    assert result.status == "WINDOW_TOO_SHORT"
    assert result.peak is None
    assert result.n_points == 7
    assert result.max_smoothed is None


def test_edge_peak_clamps_boundaries_inside_signal() -> None:
    rt = np.linspace(8.0, 10.0, 101)
    intensity = _gaussian(rt, center=8.08, sigma=0.04, height=900.0)

    result = find_peak_and_area(rt, intensity, _config(smooth_window=11))

    assert result.status == "OK"
    assert result.peak is not None
    assert rt[0] <= result.peak.peak_start <= result.peak.rt
    assert result.peak.rt <= result.peak.peak_end <= rt[-1]
    assert result.peak.peak_end > result.peak.peak_start


def test_area_is_computed_from_raw_intensity_not_smoothed() -> None:
    rt = np.linspace(0.0, 1.0, 301)
    intensity = _gaussian(rt, center=0.5, sigma=0.06, height=800.0)
    intensity += 40.0 * np.sin(np.linspace(0.0, 65.0 * np.pi, len(rt)))
    intensity = np.clip(intensity, 0.0, None)
    config = _config(smooth_window=21)

    result = find_peak_and_area(rt, intensity, config)

    assert result.status == "OK"
    assert result.peak is not None

    smoothed = savgol_filter(intensity, config.smooth_window, config.smooth_polyorder)
    left = int(np.searchsorted(rt, result.peak.peak_start, side="left"))
    right = int(np.searchsorted(rt, result.peak.peak_end, side="right"))
    # rt is in minutes; area is reported in counts·seconds, so scale both
    # reference integrals by 60 to match the convention used by find_peak_and_area.
    raw_area = np.trapezoid(intensity[left:right], rt[left:right]) * 60.0
    smoothed_area = np.trapezoid(smoothed[left:right], rt[left:right]) * 60.0
    apex_idx = int(np.argmin(np.abs(rt - result.peak.rt)))
    raw_apex_idx = result.candidates[0].raw_apex_index

    assert result.peak.area == pytest.approx(raw_area)
    assert result.peak.area != pytest.approx(smoothed_area, rel=1e-4)
    assert result.peak.intensity == pytest.approx(intensity[raw_apex_idx])
    assert result.peak.intensity_smoothed == pytest.approx(smoothed[apex_idx])


def test_area_is_reported_in_counts_seconds_not_counts_minutes() -> None:
    """
    The Thermo RawFileReader returns retention time in minutes, but LC-MS
    convention (Xcalibur, MassHunter, manual integration) reports peak area in
    counts·seconds. `find_peak_and_area` must convert so downstream numbers match
    what chemists see in Xcalibur.
    """
    rt_minutes = np.linspace(8.0, 10.0, 401)
    intensity = _gaussian(rt_minutes, center=9.0, sigma=0.08, height=1000.0)

    result = find_peak_and_area(rt_minutes, intensity, _config())

    assert result.status == "OK"
    assert result.peak is not None

    # Reference integral in the native rt units (minutes).
    left = int(np.searchsorted(rt_minutes, result.peak.peak_start, side="left"))
    right = int(np.searchsorted(rt_minutes, result.peak.peak_end, side="right"))
    area_counts_minutes = np.trapezoid(intensity[left:right], rt_minutes[left:right])

    # The output must be 60× the raw-minutes integral — anything else means we
    # accidentally reverted to counts·minutes and area will look ~60× too small.
    assert result.peak.area == pytest.approx(area_counts_minutes * 60.0, rel=1e-6)


def test_savgol_resolver_mode_preserves_existing_candidate_behavior() -> None:
    rt = np.linspace(8.0, 10.0, 401)
    intensity = _gaussian(rt, center=8.7, sigma=0.05, height=500.0)
    intensity += _gaussian(rt, center=9.4, sigma=0.06, height=1200.0)

    result = find_peak_candidates(
        rt,
        intensity,
        _config(resolver_mode="savgol"),
    )

    assert result.status == "OK"
    assert len(result.candidates) == 2
    assert [candidate.peak.rt for candidate in result.candidates] == pytest.approx(
        [8.7, 9.4],
        abs=0.01,
    )


def test_local_minimum_resolver_splits_shoulder_peaks_with_visible_valley() -> None:
    rt = np.linspace(8.7, 9.3, 601)
    intensity = _gaussian(rt, center=8.93, sigma=0.035, height=950.0)
    intensity += _gaussian(rt, center=9.08, sigma=0.03, height=520.0)
    intensity += 20.0

    result = find_peak_candidates(
        rt,
        intensity,
        _config(
            resolver_mode="local_minimum",
            resolver_chrom_threshold=0.02,
            resolver_min_search_range_min=0.03,
            resolver_min_relative_height=0.08,
            resolver_min_absolute_height=80.0,
            resolver_min_ratio_top_edge=1.2,
            resolver_peak_duration_min=0.02,
            resolver_peak_duration_max=0.35,
            resolver_min_scans=7,
        ),
    )

    assert result.status == "OK"
    assert len(result.candidates) == 2
    assert [candidate.peak.rt for candidate in result.candidates] == pytest.approx(
        [8.93, 9.08],
        abs=0.02,
    )
    assert result.candidates[0].quality_flags == ()


def test_local_minimum_low_scan_support_is_soft_quality_flag() -> None:
    rt = np.linspace(8.0, 8.8, 17)
    intensity = _gaussian(rt, center=8.4, sigma=0.05, height=600.0) + 10.0

    result = find_peak_candidates(
        rt,
        intensity,
        _config(
            resolver_mode="local_minimum",
            resolver_chrom_threshold=0.05,
            resolver_min_search_range_min=0.04,
            resolver_min_relative_height=0.0,
            resolver_min_absolute_height=25.0,
            resolver_min_ratio_top_edge=1.3,
            resolver_peak_duration_min=0.0,
            resolver_peak_duration_max=10.0,
            resolver_min_scans=15,
        ),
    )

    assert result.status == "OK"
    assert len(result.candidates) == 1
    assert "low_scan_support" in result.candidates[0].quality_flags
    assert "low_scan_count" not in result.candidates[0].quality_flags


def test_local_minimum_jagged_trace_is_soft_continuity_flag() -> None:
    rt = np.linspace(8.0, 8.8, 81)
    intensity = _gaussian(rt, center=8.4, sigma=0.12, height=900.0) + 20.0
    jagged = intensity.copy()
    mask = (rt > 8.25) & (rt < 8.55)
    jagged[mask & ((np.arange(len(rt)) % 4) == 0)] *= 0.45

    result = find_peak_candidates(
        rt,
        jagged,
        _config(
            resolver_mode="local_minimum",
            resolver_chrom_threshold=0.02,
            resolver_min_search_range_min=0.20,
            resolver_min_relative_height=0.0,
            resolver_min_absolute_height=25.0,
            resolver_min_ratio_top_edge=1.3,
            resolver_peak_duration_min=0.0,
            resolver_peak_duration_max=10.0,
            resolver_min_scans=5,
        ),
    )

    assert result.status == "OK"
    assert len(result.candidates) == 1
    assert "low_trace_continuity" in result.candidates[0].quality_flags


def test_local_minimum_resolver_keeps_single_broad_peak_without_valid_valley() -> None:
    rt = np.linspace(8.7, 9.3, 601)
    intensity = _gaussian(rt, center=9.0, sigma=0.08, height=900.0)
    intensity += _gaussian(rt, center=9.05, sigma=0.07, height=280.0)
    intensity += 25.0

    result = find_peak_candidates(
        rt,
        intensity,
        _config(
            resolver_mode="local_minimum",
            resolver_chrom_threshold=0.02,
            resolver_min_search_range_min=0.03,
            resolver_min_relative_height=0.08,
            resolver_min_absolute_height=80.0,
            resolver_min_ratio_top_edge=1.6,
            resolver_peak_duration_min=0.02,
            resolver_peak_duration_max=0.60,
            resolver_min_scans=7,
        ),
    )

    assert result.status == "OK"
    assert len(result.candidates) == 1


def test_local_minimum_resolver_uses_valley_minimum_as_region_boundary() -> None:
    rt = np.linspace(8.8, 9.2, 401)
    intensity = _gaussian(rt, center=8.92, sigma=0.03, height=850.0)
    intensity += _gaussian(rt, center=9.08, sigma=0.03, height=620.0)
    intensity += 15.0

    valley_mask = (rt > 8.97) & (rt < 9.03)
    valley_index = int(np.argmin(intensity[valley_mask]))
    valley_rt = float(rt[valley_mask][valley_index])

    local_result = find_peak_candidates(
        rt,
        intensity,
        _config(
            resolver_mode="local_minimum",
            resolver_chrom_threshold=0.02,
            resolver_min_search_range_min=0.03,
            resolver_min_relative_height=0.08,
            resolver_min_absolute_height=80.0,
            resolver_min_ratio_top_edge=1.2,
            resolver_peak_duration_min=0.02,
            resolver_peak_duration_max=0.25,
            resolver_min_scans=7,
        ),
    )
    legacy_result = find_peak_candidates(
        rt,
        intensity,
        _config(resolver_mode="savgol"),
    )

    assert local_result.status == "OK"
    assert len(local_result.candidates) == 2
    assert local_result.candidates[0].peak.peak_end == pytest.approx(
        valley_rt,
        abs=0.01,
    )
    assert local_result.candidates[1].peak.peak_start == pytest.approx(
        valley_rt,
        abs=0.01,
    )
    assert legacy_result.status == "OK"


def test_local_minimum_retains_broad_region_as_flagged_candidate() -> None:
    rt = np.linspace(8.0, 10.0, 401)
    intensity = _gaussian(rt, center=9.0, sigma=0.30, height=320000.0) + 500.0

    result = find_peak_candidates(
        rt,
        intensity,
        _config(
            resolver_mode="local_minimum",
            resolver_chrom_threshold=0.05,
            resolver_min_search_range_min=0.04,
            resolver_min_relative_height=0.05,
            resolver_min_absolute_height=25.0,
            resolver_min_ratio_top_edge=1.3,
            resolver_peak_duration_min=0.03,
            resolver_peak_duration_max=1.00,
            resolver_min_scans=5,
        ),
    )

    assert result.status == "OK"
    assert len(result.candidates) == 1
    assert result.candidates[0].peak.rt == pytest.approx(9.0, abs=0.03)
    assert "too_broad" in result.candidates[0].quality_flags
    assert result.candidates[0].region_duration_min is not None
    assert result.candidates[0].region_duration_min > 1.0


def test_local_minimum_retains_edge_clipped_region_as_flagged_candidate() -> None:
    rt = np.linspace(8.0, 8.30, 31)
    intensity = _gaussian(rt, center=7.96, sigma=0.06, height=1500.0) + 5.0

    result = find_peak_candidates(
        rt,
        intensity,
        _config(
            resolver_mode="local_minimum",
            resolver_chrom_threshold=0.05,
            resolver_min_search_range_min=0.04,
            resolver_min_relative_height=0.05,
            resolver_min_absolute_height=25.0,
            resolver_min_ratio_top_edge=1.3,
            resolver_peak_duration_min=0.03,
            resolver_peak_duration_max=0.50,
            resolver_min_scans=5,
        ),
    )

    assert result.status == "OK"
    assert len(result.candidates) == 1
    assert result.candidates[0].peak.rt == pytest.approx(8.0, abs=0.01)
    assert "edge_clipped" in result.candidates[0].quality_flags
    assert "poor_edge_recovery" in result.candidates[0].quality_flags
    assert "low_top_edge_ratio" not in result.candidates[0].quality_flags


def test_arbitrated_resolver_mode_is_retired() -> None:
    rt = np.linspace(8.0, 8.30, 31)
    intensity = _gaussian(rt, center=7.96, sigma=0.06, height=1500.0) + 5.0

    with pytest.raises(ValueError, match="arbitrated resolver mode is retired"):
        find_peak_candidates(
            rt,
            intensity,
            _config(
                resolver_mode="arbitrated",
                resolver_chrom_threshold=0.05,
                resolver_min_search_range_min=0.04,
                resolver_min_relative_height=0.05,
                resolver_min_absolute_height=25.0,
                resolver_min_ratio_top_edge=1.3,
                resolver_peak_duration_min=0.03,
                resolver_peak_duration_max=0.50,
                resolver_min_scans=5,
            ),
        )


def test_unknown_resolver_mode_is_rejected() -> None:
    rt = np.linspace(8.0, 8.30, 31)
    intensity = _gaussian(rt, center=8.12, sigma=0.04, height=1500.0) + 5.0

    with pytest.raises(ValueError, match="unsupported resolver mode 'wavelet'"):
        find_peak_candidates(
            rt,
            intensity,
            _config(
                resolver_mode="wavelet",
                resolver_chrom_threshold=0.05,
                resolver_min_search_range_min=0.04,
                resolver_min_relative_height=0.05,
                resolver_min_absolute_height=25.0,
                resolver_min_ratio_top_edge=1.3,
                resolver_peak_duration_min=0.03,
                resolver_peak_duration_max=0.50,
                resolver_min_scans=5,
            ),
        )


def test_preferred_rt_relaxed_search_candidate_preserves_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.peak_detection import peak_integration

    far_candidate = _candidate_for_provenance(
        9.50,
        proposal_sources=("savgol",),
    )
    recovered_candidate = _candidate_for_provenance(
        8.95,
        proposal_sources=("savgol",),
    )
    calls = 0

    def _fake_candidates(*_args, **_kwargs) -> PeakCandidatesResult:
        nonlocal calls
        calls += 1
        candidate = far_candidate if calls == 1 else recovered_candidate
        return PeakCandidatesResult(
            status="OK",
            candidates=(candidate,),
            n_points=20,
            max_smoothed=1200.0,
            n_prominent_peaks=1,
        )

    monkeypatch.setattr(peak_integration, "find_peak_candidates", _fake_candidates)

    result = peak_integration.find_peak_and_area(
        np.asarray([8.0, 8.5, 9.0, 9.5], dtype=float),
        np.asarray([1.0, 10.0, 4.0, 9.0], dtype=float),
        _config(),
        preferred_rt=9.0,
    )

    assert result.peak == recovered_candidate.peak
    assert any(
        "preferred_rt_recovery" in candidate.proposal_sources
        for candidate in result.candidates
    )


def test_preferred_rt_relaxed_search_merges_existing_peak_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.peak_detection import peak_integration

    near_candidate = _candidate_for_provenance(
        8.95,
        proposal_sources=("savgol",),
        selection_apex_intensity=20.0,
    )
    strong_far_candidate = _candidate_for_provenance(
        9.50,
        proposal_sources=("savgol",),
        selection_apex_intensity=2000.0,
    )
    recovered_duplicate = _candidate_for_provenance(
        8.95,
        proposal_sources=("savgol",),
        selection_apex_intensity=20.0,
    )
    calls = 0

    def _fake_candidates(*_args, **_kwargs) -> PeakCandidatesResult:
        nonlocal calls
        calls += 1
        candidates = (
            (near_candidate, strong_far_candidate)
            if calls == 1
            else (recovered_duplicate,)
        )
        return PeakCandidatesResult(
            status="OK",
            candidates=candidates,
            n_points=20,
            max_smoothed=2000.0,
            n_prominent_peaks=len(candidates),
        )

    monkeypatch.setattr(peak_integration, "find_peak_candidates", _fake_candidates)

    result = peak_integration.find_peak_and_area(
        np.asarray([8.0, 8.5, 9.0, 9.5], dtype=float),
        np.asarray([1.0, 10.0, 4.0, 2000.0], dtype=float),
        _config(),
        preferred_rt=8.95,
    )

    assert len(result.candidates) == 2
    recovered_rows = [
        candidate
        for candidate in result.candidates
        if candidate.selection_apex_rt == pytest.approx(8.95)
    ]
    assert len(recovered_rows) == 1
    assert recovered_rows[0].proposal_sources == (
        "savgol",
        "preferred_rt_recovery",
    )


def test_selection_reference_rt_is_empty_when_preferred_rt_is_not_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.peak_detection import peak_integration

    weak_near_candidate = _candidate_for_provenance(
        8.95,
        proposal_sources=("savgol",),
        selection_apex_intensity=1.0,
    )
    strong_far_candidate = _candidate_for_provenance(
        9.50,
        proposal_sources=("savgol",),
        selection_apex_intensity=100.0,
    )
    monkeypatch.setattr(
        peak_integration,
        "find_peak_candidates",
        lambda *_args, **_kwargs: PeakCandidatesResult(
            status="OK",
            candidates=(weak_near_candidate, strong_far_candidate),
            n_points=20,
            max_smoothed=1200.0,
            n_prominent_peaks=2,
        ),
    )

    result = peak_integration.find_peak_and_area(
        np.asarray([8.0, 8.5, 9.0, 9.5], dtype=float),
        np.asarray([1.0, 10.0, 4.0, 9.0], dtype=float),
        _config(),
        preferred_rt=8.95,
    )

    assert result.peak == strong_far_candidate.peak
    assert result.selection_reference_rt is None


def test_selection_reference_rt_records_preferred_rt_when_it_controls_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.peak_detection import peak_integration

    near_candidate = _candidate_for_provenance(
        8.95,
        proposal_sources=("savgol",),
        selection_apex_intensity=30.0,
    )
    strong_far_candidate = _candidate_for_provenance(
        9.50,
        proposal_sources=("savgol",),
        selection_apex_intensity=100.0,
    )
    monkeypatch.setattr(
        peak_integration,
        "find_peak_candidates",
        lambda *_args, **_kwargs: PeakCandidatesResult(
            status="OK",
            candidates=(near_candidate, strong_far_candidate),
            n_points=20,
            max_smoothed=1200.0,
            n_prominent_peaks=2,
        ),
    )

    result = peak_integration.find_peak_and_area(
        np.asarray([8.0, 8.5, 9.0, 9.5], dtype=float),
        np.asarray([1.0, 10.0, 4.0, 9.0], dtype=float),
        _config(),
        preferred_rt=8.95,
    )

    assert result.peak == near_candidate.peak
    assert result.selection_reference_rt == pytest.approx(8.95)


def _candidate_for_provenance(
    rt: float,
    *,
    left: float | None = None,
    right: float | None = None,
    proposal_sources: tuple[str, ...],
    region_scan_count: int | None = None,
    selection_apex_intensity: float = 1100.0,
) -> PeakCandidate:
    peak = PeakResult(
        rt=rt,
        intensity=selection_apex_intensity,
        intensity_smoothed=selection_apex_intensity,
        area=4000.0,
        peak_start=rt - 0.2 if left is None else left,
        peak_end=rt + 0.2 if right is None else right,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=rt,
        selection_apex_intensity=selection_apex_intensity,
        selection_apex_index=7,
        raw_apex_rt=rt,
        raw_apex_intensity=selection_apex_intensity,
        raw_apex_index=7,
        prominence=700.0,
        proposal_sources=proposal_sources,
        source_apex_rank=1,
        region_scan_count=region_scan_count,
    )
