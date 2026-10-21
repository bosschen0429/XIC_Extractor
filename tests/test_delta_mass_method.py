"""Synthetic patterns exercise the method independently of provisional presets."""

import pytest

from xic_extractor.discovery.delta_mass_patterns import prepare_delta_mass_candidates
from xic_extractor.discovery.delta_mass_strategy import (
    DeltaMassPatternSettings,
    DeltaMassPosition,
)
from xic_extractor.discovery.detected_features import DetectedFeature


def feature(name, mz, height=100, rt=1):
    return DetectedFeature(
        "sample",
        name,
        None,
        mz,
        rt,
        None,
        None,
        height,
        None,
        "OK",
        None,
        "local_ms1_feature",
    )


def test_two_position_pattern_searches_both_anchor_roles_and_deduplicates():
    settings = DeltaMassPatternSettings(
        pattern_id="synthetic",
        method_label="synthetic",
        positions=(
            DeltaMassPosition("base", 0, 1, 1),
            DeltaMassPosition("heavy", 7, 0.4, 0.6),
        ),
        anchor_positions=("base", "heavy"),
        discriminating_positions=(),
    )
    result = prepare_delta_mass_candidates(
        (feature("light", 300), feature("heavy", 307, 50)),
        settings,
        scan_interval_sec=1,
    )
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.base_mz == 300
    assert candidate.status == "candidate"
    assert [p.feature_id for p in candidate.positions] == ["light", "heavy"]
    assert result.requests == ()


def test_missing_position_requests_raw_only_after_a_table_partner_passes():
    settings = DeltaMassPatternSettings(
        pattern_id="three",
        method_label="three",
        positions=(
            DeltaMassPosition("base", 0, 1, 1),
            DeltaMassPosition("middle", 7, 0.4, 0.6),
            DeltaMassPosition("end", 13, 0.1, 0.3),
        ),
        anchor_positions=("base",),
        discriminating_positions=("end",),
    )
    result = prepare_delta_mass_candidates(
        (feature("base", 300), feature("middle", 307, 50)),
        settings,
        scan_interval_sec=1,
    )
    assert len(result.requests) == 2
    assert {r.mz for r in result.requests} == {300, 313}
    alone = prepare_delta_mass_candidates(
        (feature("base", 300),), settings, scan_interval_sec=1
    )
    assert alone.candidates == ()
    assert alone.requests == ()


def test_raw_confirmation_and_discriminating_shape_complete_the_hypothesis():
    import numpy as np

    from xic_extractor.discovery.delta_mass_patterns import (
        complete_delta_mass_candidates,
    )
    from xic_extractor.xic_models import XICTrace

    settings = DeltaMassPatternSettings(
        pattern_id="three",
        method_label="three",
        positions=(
            DeltaMassPosition("base", 0, 1, 1),
            DeltaMassPosition("middle", 7, 0.4, 0.6),
            DeltaMassPosition("end", 13, 0.1, 0.3),
        ),
        anchor_positions=("base",),
        discriminating_positions=("end",),
    )
    prepared = prepare_delta_mass_candidates(
        (feature("base", 300), feature("middle", 307, 50)),
        settings,
        scan_interval_sec=1,
    )
    times = np.array([0.96, 0.98, 1, 1.02, 1.04])
    traces = (
        XICTrace(times, np.array([0, 50, 100, 50, 0])),
        XICTrace(times, np.array([0, 100, 200, 100, 0])),
    )
    completed = complete_delta_mass_candidates(prepared, traces, settings)
    assert completed[0].status == "candidate"
    assert completed[0].positions[2].partner_source == "raw"
    assert completed[0].positions[2].shape_r > 0.99
    assert completed[0].positions[2].height_ratio_to_base == 2
    assert completed[0].positions[2].ratio_status == "above_range"


def test_missing_base_measures_ratios_without_using_them_for_admission():
    import numpy as np

    from xic_extractor.discovery.delta_mass_patterns import (
        complete_delta_mass_candidates,
    )
    from xic_extractor.xic_models import XICTrace

    settings = DeltaMassPatternSettings(
        pattern_id="missing-base",
        method_label="missing-base",
        positions=(
            DeltaMassPosition("base", 0, 1, 1),
            DeltaMassPosition("anchor", 7, 0.4, 0.8),
            DeltaMassPosition("end", 13, 0.2, 0.4),
        ),
        anchor_positions=("anchor",),
        discriminating_positions=(),
    )
    prepared = prepare_delta_mass_candidates(
        (feature("anchor", 307, 80), feature("end", 313, 400)),
        settings,
        scan_interval_sec=1,
    )
    assert {r.mz for r in prepared.requests} == {300, 307}
    times = np.array([0.96, 0.98, 1, 1.02, 1.04])
    traces = tuple(
        XICTrace(
            times, np.array([0, 50, 100, 50, 0]) * (1 if request.mz == 300 else 0.8)
        )
        for request in prepared.requests
    )
    completed = complete_delta_mass_candidates(prepared, traces, settings)
    assert completed[0].status == "candidate"
    assert completed[0].base_height == 100
    assert completed[0].positions[1].height_ratio_to_base == 0.8
    assert completed[0].positions[2].height_ratio_to_base == 4
    assert completed[0].positions[2].ratio_status == "above_range"


def test_scan_aware_gate_and_anchor_role_are_method_parameters():
    from dataclasses import replace

    settings = DeltaMassPatternSettings(
        pattern_id="pair",
        method_label="pair",
        positions=(
            DeltaMassPosition("base", 0, 1, 1),
            DeltaMassPosition("heavy", 7, 0.4, 0.6),
        ),
        anchor_positions=("heavy",),
        discriminating_positions=(),
    )
    rows = (feature("base", 300, rt=1), feature("heavy", 307, 50, rt=1.03))
    assert (
        prepare_delta_mass_candidates(rows, settings, scan_interval_sec=1).candidates
        == ()
    )
    accepted = prepare_delta_mass_candidates(rows, settings, scan_interval_sec=2)
    assert len(accepted.candidates) == 1
    assert accepted.candidates[0].anchor_position == "heavy"
    assert accepted.candidates[0].positions[1].apex_threshold_sec == 2.5
    weak = (rows[0], replace(rows[1], trace_quality="WEAK_SUPPORT"))
    assert (
        prepare_delta_mass_candidates(weak, settings, scan_interval_sec=1).candidates
        == ()
    )


def test_outlying_ratios_do_not_prune_table_or_missing_raw_evidence():
    settings = DeltaMassPatternSettings(
        pattern_id="three",
        method_label="three",
        positions=(
            DeltaMassPosition("base", 0, 1, 1),
            DeltaMassPosition("middle", 7, 0.4, 0.6),
            DeltaMassPosition("end", 13, 0.1, 0.3),
        ),
        anchor_positions=("base",),
        discriminating_positions=("end",),
    )
    ratio = prepare_delta_mass_candidates(
        (feature("base", 300), feature("middle", 307, 80)),
        settings,
        scan_interval_sec=1,
    )
    assert ratio.candidates[0].status == "candidate"
    assert ratio.candidates[0].positions[1].ratio_status == "above_range"
    assert {r.mz for r in ratio.requests} == {300, 313}
    assert ratio.candidates[0].positions[1].position_status == "pass"


def test_only_discriminating_table_positions_need_shape_and_can_reject():
    import numpy as np

    from xic_extractor.discovery.delta_mass_patterns import (
        complete_delta_mass_candidates,
    )
    from xic_extractor.xic_models import XICTrace

    settings = DeltaMassPatternSettings(
        pattern_id="three",
        method_label="three",
        positions=(
            DeltaMassPosition("base", 0, 1, 1),
            DeltaMassPosition("middle", 7, 0.4, 0.6),
            DeltaMassPosition("end", 13, 0.1, 0.3),
        ),
        anchor_positions=("base",),
        discriminating_positions=("end",),
    )
    prepared = prepare_delta_mass_candidates(
        (feature("base", 300), feature("middle", 307, 80), feature("end", 313, 20)),
        settings,
        scan_interval_sec=1,
    )
    assert {r.mz for r in prepared.requests} == {300, 313}
    times = np.array([0.96, 0.98, 1, 1.02, 1.04])
    completed = complete_delta_mass_candidates(
        prepared,
        (
            XICTrace(times, np.array([0, 50, 100, 50, 0])),
            XICTrace(times, np.array([20, 10, 0, 10, 20])),
        ),
        settings,
    )
    assert completed[0].rejected_at_layer == 5
    assert completed[0].reject_reason == "discriminating_shape_below_threshold"
    assert completed[0].positions[1].shape_r is None


def test_one_feature_cannot_fill_both_positions_inside_a_ppm_window():
    settings = DeltaMassPatternSettings(
        pattern_id="close",
        method_label="close",
        positions=(
            DeltaMassPosition("base", 0, 1, 1),
            DeltaMassPosition("near", 0.001, 0.5, 2),
        ),
        anchor_positions=("base",),
        discriminating_positions=(),
    )
    assert (
        prepare_delta_mass_candidates(
            (feature("only", 300),), settings, scan_interval_sec=1
        ).candidates
        == ()
    )


def test_missing_raw_position_requires_an_aligned_apex():
    import numpy as np

    from xic_extractor.discovery.delta_mass_patterns import (
        complete_delta_mass_candidates,
    )
    from xic_extractor.xic_models import XICTrace

    settings = DeltaMassPatternSettings(
        pattern_id="three",
        method_label="three",
        positions=(
            DeltaMassPosition("base", 0, 1, 1),
            DeltaMassPosition("middle", 7, 0.4, 0.6),
            DeltaMassPosition("end", 13, 0.1, 0.3),
        ),
        anchor_positions=("base",),
        discriminating_positions=(),
        min_shape_r=-1,
    )
    prepared = prepare_delta_mass_candidates(
        (feature("base", 300), feature("middle", 307, 50)),
        settings,
        scan_interval_sec=1,
    )
    times = np.array([0.94, 0.97, 1, 1.03, 1.06])
    completed = complete_delta_mass_candidates(
        prepared,
        (
            XICTrace(times, np.array([0, 50, 100, 50, 0])),
            XICTrace(times, np.array([0, 0, 10, 20, 10])),
        ),
        settings,
    )
    assert completed[0].rejected_at_layer == 4
    assert completed[0].reject_reason == "partner_not_confirmed_in_raw"


def test_smoothing_seconds_convert_to_scan_points_without_changing_shared_default():
    from xic_extractor.discovery.delta_mass_pipeline import (
        detector_settings_for_interval,
    )
    from xic_extractor.discovery.delta_mass_strategy import DeltaMassDiscoveryStrategy
    from xic_extractor.discovery.local_ms1_features import LocalMs1FeatureSettings

    baseline = LocalMs1FeatureSettings().gaussian_window_points
    settings = DeltaMassPatternSettings(
        pattern_id="pair",
        method_label="pair",
        positions=(
            DeltaMassPosition("base", 0, 1, 1),
            DeltaMassPosition("heavy", 7, 0.4, 0.6),
        ),
        anchor_positions=("base",),
        discriminating_positions=(),
    )
    strategy = DeltaMassDiscoveryStrategy(
        "synthetic", settings, False, smoothing_window_sec=6.6
    )
    assert detector_settings_for_interval(strategy, 1.31).gaussian_window_points == 5
    assert detector_settings_for_interval(strategy, 0.245).gaussian_window_points == 27
    assert LocalMs1FeatureSettings().gaussian_window_points == baseline


def test_raw_base_apex_owns_reported_deltas_and_table_partner_gate():
    import numpy as np

    from xic_extractor.discovery.delta_mass_patterns import (
        complete_delta_mass_candidates,
    )
    from xic_extractor.xic_models import XICTrace

    settings = DeltaMassPatternSettings(
        pattern_id="missing-base",
        method_label="missing-base",
        positions=(
            DeltaMassPosition("base", 0, 1, 1),
            DeltaMassPosition("anchor", 7, 0.4, 0.8),
            DeltaMassPosition("end", 13, 0.2, 0.4),
        ),
        anchor_positions=("anchor",),
        discriminating_positions=(),
        min_shape_r=-1,
    )
    prepared = prepare_delta_mass_candidates(
        (feature("anchor", 307, 80), feature("end", 313, 40, rt=0.98)),
        settings,
        scan_interval_sec=1,
    )
    times = np.array([0.98, 0.99, 1, 1.01, 1.02])
    traces = (
        XICTrace(times, np.array([0, 30, 70, 100, 30])),
        XICTrace(times, np.array([0, 40, 80, 40, 0])),
    )
    candidate = complete_delta_mass_candidates(prepared, traces, settings)[0]
    assert candidate.base_rt_apex_min == 1.01
    assert candidate.positions[0].apex_delta_sec == 0
    assert candidate.positions[2].apex_delta_sec < -1.7
    assert candidate.rejected_at_layer == 4


def test_table_base_owns_partner_mass_windows_even_from_a_heavy_anchor():
    settings = DeltaMassPatternSettings(
        pattern_id="three",
        method_label="three",
        positions=(
            DeltaMassPosition("base", 0, 1, 1),
            DeltaMassPosition("anchor", 7, 0.4, 0.6),
            DeltaMassPosition("end", 13, 0.1, 0.3),
        ),
        anchor_positions=("anchor",),
        discriminating_positions=(),
    )
    prepared = prepare_delta_mass_candidates(
        (
            feature("base", 300),
            feature("anchor", 307.004, 50),
            feature("off-mass", 313.008, 20),
        ),
        settings,
        scan_interval_sec=1,
    )
    assert prepared.candidates[0].positions[2].partner_source == "missing"
    assert {r.mz for r in prepared.requests} == {300, 313}


@pytest.mark.parametrize(
    "heights,noise,expected_reason",
    [
        ([100, 80, 90, 30], 1, ""),
        ([100, 80, 90, 120], 1, "lowest_position_not_lowest"),
        ([100, 80, 90, 20], 0, "peak_not_above_background"),
        ([200, 250, 220, 100], 20, "peak_not_above_background"),
        ([100, 80, 90, 30], 1, "discriminating_shape_below_threshold"),
        ([100, 80, 90, 30], 1, "local_background_unavailable"),
    ],
)
def test_lowest_position_requires_measured_net_height_and_background_support(
    heights, noise, expected_reason
):
    from dataclasses import replace

    import numpy as np

    from xic_extractor.discovery.delta_mass_patterns import (
        complete_delta_mass_candidates,
    )
    from xic_extractor.xic_models import XICTrace

    settings = DeltaMassPatternSettings(
        pattern_id="signal",
        method_label="signal",
        positions=tuple(
            DeltaMassPosition(label, delta, 0.1, 2)
            for label, delta in [("base", 0), ("one", 1), ("two", 2), ("four", 4)]
        ),
        anchor_positions=("base",),
        discriminating_positions=("four",),
        lowest_position="four",
        min_height_above_background=25,
    )
    prepared = prepare_delta_mass_candidates(
        tuple(
            replace(
                feature(label, 300 + delta, height=100),
                trace_quality="OK" if delta == 0 else "WEAK_SUPPORT",
            )
            for label, delta in [("base", 0), ("one", 1), ("two", 2), ("four", 4)]
        ),
        settings,
        scan_interval_sec=1,
    )
    assert len(prepared.requests) == 4
    assert prepared.requests[0].rt_min == 0.5
    seconds = np.arange(-30, 31, dtype=float)
    times = 1 + seconds / 60
    traces = []
    for i, h in enumerate(heights):
        baseline = 1000 if i == 3 else 100
        sign = (
            -1
            if i == 3 and expected_reason == "discriminating_shape_below_threshold"
            else 1
        )
        y = baseline + sign * h * np.exp(-0.5 * (seconds / 3) ** 2)
        y[seconds <= -20] = baseline + np.arange(-5, 6) * noise
        # An adjacent peak must not become this peak's background or shape input.
        y[seconds >= 20] = 1e6
        mask = (
            abs(seconds) < 20
            if expected_reason == "local_background_unavailable"
            else np.ones(len(times), dtype=bool)
        )
        traces.append(XICTrace(times[mask], y[mask]))
    candidate = complete_delta_mass_candidates(prepared, traces, settings)[0]
    assert candidate.reject_reason == expected_reason
    assert candidate.status == ("rejected" if expected_reason else "candidate")
    four = candidate.positions[3]
    if expected_reason in {
        "local_background_unavailable",
        "discriminating_shape_below_threshold",
    }:
        assert four.height_above_background is None
        return
    assert four.height_above_background == pytest.approx(heights[3])
    assert four.local_background == 1000
    if not expected_reason:
        assert (
            candidate.positions[1].height_above_background
            < candidate.positions[0].height_above_background
        )
        assert four.lowest_status == "pass"
        assert four.signal_status == "pass"
        assert four.shape_r > 0.99
