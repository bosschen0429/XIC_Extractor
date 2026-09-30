from collections.abc import Iterator

import numpy as np
import pytest

from xic_extractor.acquisition.ms2_windows import acquire_ms2_window
from xic_extractor.evidence import neutral_loss as neutral_loss_module
from xic_extractor.evidence.neutral_loss import (
    candidate_ms2_geometry,
    check_nl,
    find_nl_anchor_rt,
)
from xic_extractor.raw_reader import Ms2Scan, Ms2ScanEvent

PRECURSOR_MZ = 258.0969
NEUTRAL_LOSS_DA = 131.0405
EXPECTED_PRODUCT_MZ = PRECURSOR_MZ - NEUTRAL_LOSS_DA


def test_two_sample_windows_keep_attribution_and_bounded_reader_calls() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.3, apex_rt=8.1)
    geometry = candidate_ms2_geometry(candidate)
    observed = {}
    for sample_key, rt, ppm in (("sample-a", 8.1, 5.0), ("sample-b", 8.2, 12.0)):
        raw = _FakeRaw(
            [
                _scan_event(
                    precursor_mz=PRECURSOR_MZ,
                    rt=rt,
                    masses=[_product_for_loss_ppm(PRECURSOR_MZ, ppm)],
                    intensities=[100.0],
                )
            ]
        )
        shared_window = acquire_ms2_window(
            raw, sample_key=sample_key, rt_min=8.0, rt_max=9.0
        )
        anchor = find_nl_anchor_rt(
            shared_window,
            precursor_mz=PRECURSOR_MZ,
            neutral_loss_da=NEUTRAL_LOSS_DA,
            nl_ppm_max=20.0,
            ms2_precursor_tol_da=0.5,
            nl_min_intensity_ratio=0.05,
        )
        result = check_nl(
            shared_window,
            precursor_mz=PRECURSOR_MZ,
            neutral_loss_da=NEUTRAL_LOSS_DA,
            nl_ppm_warn=10.0,
            nl_ppm_max=20.0,
            ms2_precursor_tol_da=0.5,
            nl_min_intensity_ratio=0.05,
        )
        candidate_window = acquire_ms2_window(
            raw,
            sample_key=sample_key,
            rt_min=geometry.rt_min,
            rt_max=geometry.rt_max,
        )
        evidence = neutral_loss_module.collect_candidate_ms2_evidence(
            candidate_window,
            geometry=geometry,
            precursor_mz=PRECURSOR_MZ,
            neutral_loss_da=NEUTRAL_LOSS_DA,
            nl_ppm_warn=10.0,
            nl_ppm_max=20.0,
            ms2_precursor_tol_da=0.5,
            nl_min_intensity_ratio=0.05,
        )
        observed[sample_key] = (shared_window, anchor, result, evidence, raw.calls)

    first_window, first_anchor, first_nl, first_evidence, first_calls = observed[
        "sample-a"
    ]
    second_window, second_anchor, second_nl, second_evidence, second_calls = (
        observed["sample-b"]
    )
    assert (first_window.sample_key, second_window.sample_key) == (
        "sample-a",
        "sample-b",
    )
    assert (len(first_window.events), len(second_window.events)) == (1, 1)
    assert (first_anchor, second_anchor) == pytest.approx((8.1, 8.2))
    assert (first_nl.status, second_nl.status) == ("OK", "WARN")
    assert (first_nl.to_token(), second_nl.to_token()) == ("OK", "WARN_12.0ppm")
    assert (first_nl.best_ppm, second_nl.best_ppm) == pytest.approx((5.0, 12.0))
    assert (first_nl.valid_ms2_scan_count, second_nl.valid_ms2_scan_count) == (1, 1)
    assert (first_nl.parse_error_count, second_nl.parse_error_count) == (0, 0)
    assert (first_nl.matched_scan_count, second_nl.matched_scan_count) == (1, 1)
    assert (first_evidence.to_token(), second_evidence.to_token()) == (
        "OK",
        "WARN_12.0ppm",
    )
    assert (first_evidence.trigger_scan_count, second_evidence.trigger_scan_count) == (
        1,
        1,
    )
    assert (
        first_evidence.strict_nl_scan_count,
        second_evidence.strict_nl_scan_count,
    ) == (
        1,
        1,
    )
    assert (first_evidence.alignment_source, second_evidence.alignment_source) == (
        "region",
        "region",
    )
    assert (
        first_evidence.trace.product_point_count,
        second_evidence.trace.product_point_count,
    ) == (1, 1)
    assert first_calls == second_calls == [
        (8.0, 9.0),
        (geometry.rt_min, geometry.rt_max),
    ]


def test_check_nl_returns_ok_when_best_ppm_is_at_or_below_warn() -> None:
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                masses=[120.0, _product_for_loss_ppm(PRECURSOR_MZ, 5.0)],
                intensities=[100.0, 80.0],
            )
        ]
    )

    result = _check(raw)

    assert result.status == "OK"
    assert result.best_ppm == pytest.approx(5.0)
    assert result.valid_ms2_scan_count == 1
    assert result.matched_scan_count == 1
    assert result.parse_error_count == 0
    assert result.to_token() == "OK"


def test_check_nl_returns_warn_token_when_ppm_is_between_warn_and_max() -> None:
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                masses=[_product_for_loss_ppm(PRECURSOR_MZ, 12.34)],
                intensities=[100.0],
            )
        ]
    )

    result = _check(raw)

    assert result.status == "WARN"
    assert result.best_ppm == pytest.approx(12.34)
    assert result.to_token() == "WARN_12.3ppm"


def test_check_nl_returns_fail_with_best_ppm_when_product_is_only_diagnostic() -> None:
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                masses=[_product_for_loss_ppm(PRECURSOR_MZ, 55.0)],
                intensities=[100.0],
            )
        ]
    )

    result = _check(raw)

    assert result.status == "NL_FAIL"
    assert result.best_ppm == pytest.approx(55.0)
    assert result.to_token() == "NL_FAIL"


def test_targeted_check_accepts_loss_error_inside_ion_window() -> None:
    raw = _FakeRaw([_scan_event(
        precursor_mz=PRECURSOR_MZ,
        masses=[_product_for_loss_ppm(PRECURSOR_MZ, 40.0)],
        intensities=[100.0],
    )])

    result = _check(raw)

    assert result.status == "WARN"
    assert result.best_ppm == pytest.approx(40.0)


def test_check_nl_returns_fail_without_best_ppm_when_no_product_is_diagnostic() -> None:
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                masses=[_mass_at_ppm(EXPECTED_PRODUCT_MZ, 1200.0)],
                intensities=[100.0],
            )
        ]
    )

    result = _check(raw)

    assert result.status == "NL_FAIL"
    assert result.best_ppm is None
    assert result.matched_scan_count == 1


def test_check_nl_rejects_target_product_when_observed_loss_is_wrong() -> None:
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=262.156006,
                masses=[145.079849],
                intensities=[100.0],
            )
        ]
    )

    result = check_nl(
        _window(raw, 8.0, 10.0),
        precursor_mz=261.127276,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
    )

    assert result.status == "NL_FAIL"
    assert result.best_ppm is None
    assert result.matched_scan_count == 1


def test_check_nl_accepts_observed_loss_within_threshold() -> None:
    precursor_mz = 258.110077
    neutral_loss_da = 116.0474
    product_mz = 142.061813
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=precursor_mz,
                masses=[product_mz],
                intensities=[100.0],
            )
        ]
    )

    result = check_nl(
        _window(raw, 8.0, 10.0),
        precursor_mz=258.1085,
        neutral_loss_da=neutral_loss_da,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
    )

    observed_loss_ppm = abs((precursor_mz - product_mz) - neutral_loss_da)
    observed_loss_ppm = observed_loss_ppm / neutral_loss_da * 1_000_000.0
    assert result.status == "OK"
    assert result.best_ppm == pytest.approx(observed_loss_ppm)


def test_check_nl_warns_on_observed_loss_between_warn_and_max() -> None:
    precursor_mz = PRECURSOR_MZ + 0.04
    observed_loss = NEUTRAL_LOSS_DA * (1.0 + 12.34 / 1_000_000.0)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=precursor_mz,
                masses=[precursor_mz - observed_loss],
                intensities=[100.0],
            )
        ]
    )

    result = _check(raw)

    assert result.status == "WARN"
    assert result.best_ppm == pytest.approx(12.34)
    assert result.to_token() == "WARN_12.3ppm"


def test_check_nl_returns_no_ms2_when_no_scan_matches_precursor() -> None:
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ + 2.0,
                masses=[_mass_at_ppm(EXPECTED_PRODUCT_MZ, 1.0)],
                intensities=[100.0],
            )
        ]
    )

    result = _check(raw)

    assert result.status == "NO_MS2"
    assert result.best_ppm is None
    assert result.valid_ms2_scan_count == 1
    assert result.matched_scan_count == 0
    assert result.to_token() == "NO_MS2"


def test_check_nl_skips_parse_errors_but_counts_them() -> None:
    raw = _FakeRaw(
        [
            Ms2ScanEvent(scan=None, parse_error="bad filter", scan_number=7),
            _scan_event(
                scan_number=8,
                precursor_mz=PRECURSOR_MZ,
                masses=[_mass_at_ppm(EXPECTED_PRODUCT_MZ, 1.0)],
                intensities=[100.0],
            ),
        ]
    )

    result = _check(raw)

    assert result.status == "OK"
    assert result.parse_error_count == 1
    assert result.valid_ms2_scan_count == 1
    assert result.matched_scan_count == 1


def test_check_nl_ignores_product_below_intensity_ratio() -> None:
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                masses=[EXPECTED_PRODUCT_MZ, 150.0],
                intensities=[4.0, 100.0],
            )
        ]
    )

    result = _check(raw)

    assert result.status == "NL_FAIL"
    assert result.best_ppm is None


def test_check_nl_ignores_all_zero_spectrum_without_divide_by_zero() -> None:
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                masses=[EXPECTED_PRODUCT_MZ],
                intensities=[0.0],
            )
        ]
    )

    result = _check(raw)

    assert result.status == "NL_FAIL"
    assert result.best_ppm is None
    assert result.matched_scan_count == 1


def test_find_nl_anchor_rt_without_reference_uses_strongest_base_peak() -> None:
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.10,
                masses=[EXPECTED_PRODUCT_MZ, 150.0],
                intensities=[100.0, 250.0],
            ),
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.90,
                masses=[EXPECTED_PRODUCT_MZ, 150.0],
                intensities=[80.0, 900.0],
            ),
        ]
    )

    anchor_rt = _anchor(raw)

    assert anchor_rt == pytest.approx(8.90)


def test_find_nl_anchor_rt_ignores_wrong_observed_loss_with_target_product() -> None:
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=262.156006,
                rt=8.94,
                masses=[145.079849],
                intensities=[100.0],
            )
        ]
    )

    anchor_rt = find_nl_anchor_rt(
        _window(raw, 8.0, 10.0),
        precursor_mz=261.127276,
        neutral_loss_da=116.0474,
        nl_ppm_max=50.0,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
        reference_rt=None,
    )

    assert anchor_rt is None


def test_find_nl_anchor_rt_accepts_shifted_precursor_observed_loss() -> None:
    precursor_mz = PRECURSOR_MZ + 0.04
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=precursor_mz,
                rt=8.75,
                masses=[_product_for_loss_ppm(precursor_mz, 5.0)],
                intensities=[100.0],
            )
        ]
    )

    anchor_rt = _anchor(raw)

    assert anchor_rt == pytest.approx(8.75)


def test_find_nl_anchor_rt_with_reference_uses_nearest_scan_even_when_weaker() -> None:
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.10,
                masses=[EXPECTED_PRODUCT_MZ, 150.0],
                intensities=[80.0, 900.0],
            ),
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.75,
                masses=[EXPECTED_PRODUCT_MZ, 150.0],
                intensities=[100.0, 250.0],
            ),
        ]
    )

    anchor_rt = _anchor(raw, reference_rt=8.80)

    assert anchor_rt == pytest.approx(8.75)


def test_find_nl_anchor_rt_with_reference_breaks_distance_tie_by_base_peak() -> None:
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.00,
                masses=[EXPECTED_PRODUCT_MZ, 150.0],
                intensities=[100.0, 250.0],
            ),
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=9.00,
                masses=[EXPECTED_PRODUCT_MZ, 150.0],
                intensities=[80.0, 900.0],
            ),
        ]
    )

    anchor_rt = _anchor(raw, reference_rt=8.50)

    assert anchor_rt == pytest.approx(9.00)


def test_candidate_evidence_counts_trigger_inside_peak_region() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.1)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.1,
                masses=[150.0],
                intensities=[100.0],
            )
        ]
    )

    evidence = _candidate_evidence(raw, candidate)

    assert evidence.ms2_present is True
    assert evidence.nl_match is False
    assert evidence.trigger_scan_count == 1
    assert evidence.strict_nl_scan_count == 0
    assert evidence.alignment_source == "region"


def test_candidate_evidence_does_not_borrow_other_region_trigger() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.1)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.9,
                masses=[150.0],
                intensities=[100.0],
            )
        ]
    )

    evidence = _candidate_evidence(raw, candidate)

    assert evidence.ms2_present is False
    assert evidence.nl_match is False
    assert evidence.trigger_scan_count == 0
    assert evidence.alignment_source == "none"


def test_candidate_evidence_uses_original_ms2_window_for_promoted_candidate() -> None:
    candidate = _candidate(
        peak_start=8.0,
        peak_end=9.0,
        apex_rt=8.1,
        ms2_evidence_peak_start=8.0,
        ms2_evidence_peak_end=8.2,
    )
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.9,
                masses=[150.0],
                intensities=[100.0],
            )
        ]
    )

    evidence = _candidate_evidence(raw, candidate)

    assert evidence.ms2_present is False
    assert evidence.nl_status == "NO_MS2"
    assert evidence.trigger_scan_count == 0
    assert evidence.alignment_source == "none"


def test_candidate_evidence_uses_apex_fallback_for_sparse_ms2() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.18)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.25,
                masses=[150.0],
                intensities=[100.0],
            )
        ]
    )

    evidence = _candidate_evidence(raw, candidate)

    assert evidence.ms2_present is True
    assert evidence.nl_match is False
    assert evidence.trigger_scan_count == 1
    assert evidence.alignment_source == "apex_fallback"


def test_candidate_evidence_reports_strict_nl_match() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.1)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.1,
                masses=[_product_for_loss_ppm(PRECURSOR_MZ, 5.0)],
                intensities=[100.0],
            )
        ]
    )

    evidence = _candidate_evidence(raw, candidate)

    assert evidence.ms2_present is True
    assert evidence.nl_match is True
    assert evidence.nl_status == "OK"
    assert evidence.strict_nl_scan_count == 1
    assert evidence.best_loss_ppm == pytest.approx(5.0)
    assert evidence.best_scan_rt == pytest.approx(8.1)
    assert evidence.best_product_base_ratio == pytest.approx(1.0)


def test_candidate_evidence_scopes_strict_nl_to_gaussian15_ms1_peak_group() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.1)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.1,
                masses=[_product_for_loss_ppm(PRECURSOR_MZ, 5.0)],
                intensities=[100.0],
            )
        ]
    )

    evidence = _candidate_evidence(
        raw,
        candidate,
        ms1_peak_group_rt_min=8.0,
        ms1_peak_group_rt_max=8.2,
        ms1_peak_group_source="gaussian15_ms1_peak_group",
    )

    assert evidence.nl_status == "OK"
    assert evidence.ms1_peak_group_source == "gaussian15_ms1_peak_group"
    assert evidence.ms1_peak_group_rt_min == pytest.approx(8.0)
    assert evidence.ms1_peak_group_rt_max == pytest.approx(8.2)
    assert evidence.ms1_peak_group_trigger_scan_count == 1
    assert evidence.ms1_peak_group_strict_nl_scan_count == 1
    assert evidence.ms1_peak_group_strict_nl_event_count == 1
    assert evidence.outside_ms1_peak_group_trigger_scan_count == 0
    assert evidence.outside_ms1_peak_group_strict_nl_scan_count == 0


def test_candidate_evidence_rejects_strict_nl_outside_gaussian15_group() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.1)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.29,
                masses=[_product_for_loss_ppm(PRECURSOR_MZ, 5.0)],
                intensities=[100.0],
            )
        ]
    )

    evidence = _candidate_evidence(
        raw,
        candidate,
        ms1_peak_group_rt_min=8.0,
        ms1_peak_group_rt_max=8.2,
        ms1_peak_group_source="gaussian15_ms1_peak_group",
    )

    assert evidence.ms2_present is False
    assert evidence.nl_match is False
    assert evidence.nl_status == "NO_MS2"
    assert evidence.trigger_scan_count == 0
    assert evidence.strict_nl_scan_count == 0
    assert evidence.ms1_peak_group_trigger_scan_count == 0
    assert evidence.ms1_peak_group_strict_nl_scan_count == 0
    assert evidence.ms1_peak_group_strict_nl_event_count == 0
    assert evidence.outside_ms1_peak_group_trigger_scan_count == 1
    assert evidence.outside_ms1_peak_group_strict_nl_scan_count == 1
    assert evidence.diagnostic_product_absence_reason == (
        "strict_nl_outside_ms1_peak_group"
    )


def test_repeated_strict_nl_inside_gaussian15_group_is_one_peak_group_event() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.1)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.05,
                masses=[_product_for_loss_ppm(PRECURSOR_MZ, 5.0)],
                intensities=[100.0],
            ),
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.10,
                masses=[_product_for_loss_ppm(PRECURSOR_MZ, 4.0)],
                intensities=[200.0],
            ),
        ]
    )

    evidence = _candidate_evidence(
        raw,
        candidate,
        ms1_peak_group_rt_min=8.0,
        ms1_peak_group_rt_max=8.2,
        ms1_peak_group_source="gaussian15_ms1_peak_group",
    )

    assert evidence.strict_nl_scan_count == 2
    assert evidence.ms1_peak_group_strict_nl_scan_count == 2
    assert evidence.ms1_peak_group_strict_nl_event_count == 1


def test_candidate_evidence_populates_strict_nl_product_trace() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.1)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.05,
                masses=[_product_for_loss_ppm(PRECURSOR_MZ, 5.0)],
                intensities=[100.0],
            ),
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.10,
                masses=[_product_for_loss_ppm(PRECURSOR_MZ, 4.0)],
                intensities=[200.0],
            ),
        ]
    )

    evidence = _candidate_evidence(raw, candidate)

    assert evidence.trace.product_point_count == 2
    assert evidence.trace.product_apex_rt == pytest.approx(8.10)
    assert evidence.trace.product_apex_delta_min == pytest.approx(0.0)
    assert evidence.trace.product_height == pytest.approx(200.0)
    assert evidence.trace.product_area == pytest.approx(7.5)
    assert evidence.trace.trace_continuity == pytest.approx(1.0)
    assert evidence.trace.strength == "strong"


def test_candidate_evidence_rescues_strict_nl_just_outside_peak_boundary() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.1)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.29,
                masses=[_product_for_loss_ppm(PRECURSOR_MZ, 5.0)],
                intensities=[100.0],
            )
        ]
    )

    evidence = _candidate_evidence(raw, candidate)

    assert evidence.ms2_present is True
    assert evidence.nl_match is True
    assert evidence.nl_status == "OK"
    assert evidence.strict_nl_scan_count == 1
    assert evidence.best_scan_rt == pytest.approx(8.29)
    assert evidence.alignment_source == "boundary_rescue"


def test_boundary_rescue_strict_nl_trace_strength_reflects_apex_delta() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.0)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.29,
                masses=[_product_for_loss_ppm(PRECURSOR_MZ, 5.0)],
                intensities=[100.0],
            )
        ]
    )

    evidence = _candidate_evidence(raw, candidate)

    assert evidence.nl_status == "OK"
    assert evidence.alignment_source == "boundary_rescue"
    assert evidence.trace.product_point_count == 1
    assert evidence.trace.product_apex_delta_min == pytest.approx(0.29)
    assert evidence.trace.strength == "weak"


def test_candidate_evidence_does_not_rescue_trigger_only_boundary_scan() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.1)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.29,
                masses=[150.0],
                intensities=[100.0],
            )
        ]
    )

    evidence = _candidate_evidence(raw, candidate)

    assert evidence.ms2_present is False
    assert evidence.nl_match is False
    assert evidence.nl_status == "NO_MS2"
    assert evidence.trigger_scan_count == 0
    assert evidence.strict_nl_scan_count == 0
    assert evidence.alignment_source == "none"


def test_trigger_only_candidate_has_no_product_trace_support() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.1)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.1,
                masses=[150.0],
                intensities=[100.0],
            )
        ]
    )

    evidence = _candidate_evidence(raw, candidate)

    assert evidence.ms2_present is True
    assert evidence.nl_status == "NL_FAIL"
    assert evidence.trace.product_point_count == 0
    assert evidence.trace.trace_continuity == pytest.approx(0.0)
    assert evidence.trace.strength == "none"


def test_candidate_evidence_reports_product_below_intensity_floor() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.1)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.1,
                masses=[
                    _product_for_loss_ppm(PRECURSOR_MZ, 5.0),
                    _product_for_loss_ppm(PRECURSOR_MZ, 900.0),
                ],
                intensities=[1.0, 100.0],
            )
        ]
    )

    evidence = _candidate_evidence(raw, candidate)

    assert evidence.nl_status == "NL_FAIL"
    assert evidence.best_loss_ppm is None
    assert evidence.diagnostic_product_absence_reason == (
        "product_below_intensity_floor"
    )
    assert evidence.nearest_product_loss_ppm == pytest.approx(5.0)
    assert evidence.nearest_product_base_ratio == pytest.approx(0.01)


def test_candidate_evidence_reports_nearest_product_outside_diagnostic_window() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.1)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.1,
                masses=[_product_for_loss_ppm(PRECURSOR_MZ, 900.0)],
                intensities=[100.0],
            )
        ]
    )

    evidence = _candidate_evidence(raw, candidate)

    assert evidence.nl_status == "NL_FAIL"
    assert evidence.best_loss_ppm is None
    assert evidence.diagnostic_product_absence_reason == (
        "product_outside_diagnostic_window"
    )
    assert evidence.nearest_product_loss_ppm == pytest.approx(900.0)
    assert evidence.nearest_product_base_ratio == pytest.approx(1.0)


def test_boundary_strict_nl_overrides_region_trigger_failure() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.1)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.1,
                masses=[150.0],
                intensities=[100.0],
            ),
            _scan_event(
                precursor_mz=PRECURSOR_MZ,
                rt=8.29,
                masses=[_product_for_loss_ppm(PRECURSOR_MZ, 5.0)],
                intensities=[100.0],
            ),
        ]
    )

    evidence = _candidate_evidence(raw, candidate)

    assert evidence.ms2_present is True
    assert evidence.nl_match is True
    assert evidence.nl_status == "OK"
    assert evidence.trigger_scan_count == 2
    assert evidence.strict_nl_scan_count == 1
    assert evidence.best_scan_rt == pytest.approx(8.29)
    assert evidence.alignment_source == "boundary_rescue"


def test_candidate_evidence_separates_trigger_from_failed_nl() -> None:
    candidate = _candidate(peak_start=8.0, peak_end=8.2, apex_rt=8.1)
    raw = _FakeRaw(
        [
            _scan_event(
                precursor_mz=262.156006,
                rt=8.1,
                masses=[145.079849],
                intensities=[100.0],
            )
        ]
    )

    evidence = neutral_loss_module.collect_candidate_ms2_evidence(
        _window(raw, 7.9, 8.3),
        geometry=candidate_ms2_geometry(candidate),
        precursor_mz=261.127276,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
    )

    assert evidence.ms2_present is True
    assert evidence.nl_match is False
    assert evidence.nl_status == "NL_FAIL"
    assert evidence.trigger_scan_count == 1
    assert evidence.strict_nl_scan_count == 0


def _check(raw: "_FakeRaw"):
    return check_nl(
        _window(raw, 8.0, 9.0),
        precursor_mz=PRECURSOR_MZ,
        neutral_loss_da=NEUTRAL_LOSS_DA,
        nl_ppm_warn=10.0,
        nl_ppm_max=20.0,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.05,
    )


def _anchor(raw: "_FakeRaw", reference_rt: float | None = None) -> float | None:
    return find_nl_anchor_rt(
        _window(raw, 7.5, 9.5),
        precursor_mz=PRECURSOR_MZ,
        neutral_loss_da=NEUTRAL_LOSS_DA,
        nl_ppm_max=20.0,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.05,
        reference_rt=reference_rt,
    )


def _candidate_evidence(
    raw: "_FakeRaw",
    candidate: "_FakeCandidate",
    *,
    ms1_peak_group_rt_min: float | None = None,
    ms1_peak_group_rt_max: float | None = None,
    ms1_peak_group_source: str = "",
):
    geometry = candidate_ms2_geometry(
        candidate,
        ms1_peak_group_rt_min=ms1_peak_group_rt_min,
        ms1_peak_group_rt_max=ms1_peak_group_rt_max,
    )
    return neutral_loss_module.collect_candidate_ms2_evidence(
        _window(raw, geometry.rt_min, geometry.rt_max),
        geometry=geometry,
        precursor_mz=PRECURSOR_MZ,
        neutral_loss_da=NEUTRAL_LOSS_DA,
        nl_ppm_warn=10.0,
        nl_ppm_max=20.0,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.05,
        ms1_peak_group_source=ms1_peak_group_source,
    )


def _window(raw: "_FakeRaw", rt_min: float, rt_max: float):
    return acquire_ms2_window(
        raw, sample_key="fixture", rt_min=rt_min, rt_max=rt_max
    )


def _mass_at_ppm(mass: float, ppm: float) -> float:
    return mass * (1.0 + ppm / 1_000_000.0)


def _product_for_loss_ppm(precursor_mz: float, ppm: float) -> float:
    observed_loss = NEUTRAL_LOSS_DA * (1.0 + ppm / 1_000_000.0)
    return precursor_mz - observed_loss


def _scan_event(
    *,
    precursor_mz: float,
    masses: list[float],
    intensities: list[float],
    scan_number: int = 1,
    rt: float = 8.5,
) -> Ms2ScanEvent:
    return Ms2ScanEvent(
        scan=Ms2Scan(
            scan_number=scan_number,
            rt=rt,
            precursor_mz=precursor_mz,
            masses=np.asarray(masses, dtype=float),
            intensities=np.asarray(intensities, dtype=float),
            base_peak=max(intensities) if intensities else 0.0,
        ),
        parse_error=None,
        scan_number=scan_number,
    )


class _FakeRaw:
    def __init__(self, events: list[Ms2ScanEvent]) -> None:
        self.events = events
        self.requested_window: tuple[float, float] | None = None
        self.calls: list[tuple[float, float]] = []

    def iter_ms2_scans(self, rt_min: float, rt_max: float) -> Iterator[Ms2ScanEvent]:
        self.requested_window = (rt_min, rt_max)
        self.calls.append((rt_min, rt_max))
        yield from self.events


def _candidate(
    *,
    peak_start: float,
    peak_end: float,
    apex_rt: float,
    ms2_evidence_peak_start: float | None = None,
    ms2_evidence_peak_end: float | None = None,
) -> "_FakeCandidate":
    return _FakeCandidate(
        peak=_FakePeak(peak_start=peak_start, peak_end=peak_end),
        selection_apex_rt=apex_rt,
        ms2_evidence_peak_start=ms2_evidence_peak_start,
        ms2_evidence_peak_end=ms2_evidence_peak_end,
    )


class _FakePeak:
    def __init__(self, *, peak_start: float, peak_end: float) -> None:
        self.peak_start = peak_start
        self.peak_end = peak_end


class _FakeCandidate:
    def __init__(
        self,
        *,
        peak: _FakePeak,
        selection_apex_rt: float,
        ms2_evidence_peak_start: float | None = None,
        ms2_evidence_peak_end: float | None = None,
    ) -> None:
        self.peak = peak
        self.selection_apex_rt = selection_apex_rt
        self.ms2_evidence_peak_start = ms2_evidence_peak_start
        self.ms2_evidence_peak_end = ms2_evidence_peak_end
