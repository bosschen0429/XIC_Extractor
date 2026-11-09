import numpy as np
import pytest

from xic_extractor.peak_detection.chrom_peak_segments import (
    ChromPeakSegmentPolicy,
    enumerate_chrom_peak_segments,
)


def _trace(residual: list[float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rt = np.arange(float(len(residual)), dtype=float)
    baseline = np.full(len(residual), 10.0)
    intensity = baseline + np.asarray(residual, dtype=float)
    return rt, intensity, baseline


def test_segment_enumeration_recovers_clean_full_peak_segment() -> None:
    rt, intensity, baseline = _trace([0, 0, 2, 10, 30, 50, 30, 10, 2, 0, 0])

    result = enumerate_chrom_peak_segments(rt, intensity, baseline)

    assert result.status == "OK"
    assert result.morphology_trace_method == "gaussian_15"
    assert len(result.segments) == 1
    segment = result.segments[0]
    assert segment.segment_class == "isolated_peak"
    assert segment.apex_rt_min == pytest.approx(5.0)
    assert segment.interval.rt_start_min == pytest.approx(2.0)
    assert segment.interval.rt_end_min == pytest.approx(8.0)
    assert segment.interval.scan_count == 7
    assert segment.area_baseline_corrected > 0.0
    assert "morphology_local_maximum" in segment.evidence_sources
    assert "baseline_return" in segment.evidence_sources
    assert "raw_area_asls" in segment.evidence_sources


def test_segment_enumeration_splits_baseline_separated_neighbor_peaks() -> None:
    rt, intensity, baseline = _trace([0, 0, 10, 50, 10, 0, 0, 8, 40, 8, 0, 0])

    result = enumerate_chrom_peak_segments(rt, intensity, baseline)

    assert result.status == "OK"
    assert [segment.segment_class for segment in result.segments] == [
        "separate_peak",
        "separate_peak",
    ]
    assert [segment.apex_rt_min for segment in result.segments] == pytest.approx(
        [3.0, 8.0]
    )
    assert result.segments[0].interval.rt_end_min < 6.0
    assert result.segments[1].interval.rt_start_min > 5.0
    assert "local_minimum_valley" in result.segments[0].evidence_sources
    assert "local_minimum_valley" in result.segments[1].evidence_sources


def test_segment_enumeration_marks_high_valley_shoulder_for_review() -> None:
    rt, intensity, baseline = _trace([0, 0, 10, 50, 40, 45, 30, 0, 0])

    result = enumerate_chrom_peak_segments(rt, intensity, baseline)

    assert result.status == "OK"
    assert len(result.segments) == 2
    assert {segment.segment_class for segment in result.segments} == {
        "shoulder_candidate"
    }
    assert all(
        segment.boundary_stop_reason == "shoulder_overlap_review"
        for segment in result.segments
    )
    assert all(
        "shoulder_overlap" in segment.evidence_sources for segment in result.segments
    )


def test_segment_area_uses_raw_trace_not_morphology_area() -> None:
    residual = [0] * 12 + [0, 4, 100, 4, 0] + [0] * 12
    rt, intensity, baseline = _trace(residual)

    result = enumerate_chrom_peak_segments(
        rt,
        intensity,
        baseline,
        policy=ChromPeakSegmentPolicy(morphology_trace_window_points=15),
    )

    assert result.status == "OK"
    segment = result.segments[0]
    raw_segment = np.maximum(
        intensity[segment.interval.start_index : segment.interval.end_index]
        - baseline[segment.interval.start_index : segment.interval.end_index],
        0.0,
    )
    raw_rt = rt[segment.interval.start_index : segment.interval.end_index]
    expected_raw_area = float(np.trapezoid(raw_segment, raw_rt)) * 60.0
    assert segment.area_baseline_corrected == pytest.approx(expected_raw_area)
    assert segment.morphology_area_shadow != pytest.approx(expected_raw_area)


def test_supported_broad_peak_does_not_quantify_each_surface_ripple() -> None:
    rt = np.linspace(0.0, 8.0, 2401)
    envelope = 10000.0 * np.exp(-0.5 * ((rt - 4.0) / 0.7) ** 2)
    signal = envelope * (1.0 + 0.055 * np.cos(2 * np.pi * (rt - 4.0) / 0.12))
    result = enumerate_chrom_peak_segments(
        rt,
        signal,
        np.zeros_like(signal),
        policy=ChromPeakSegmentPolicy(
            morphology_trace_method="rt_envelope",
            morphology_min_sigma_rt=0.08,
            min_prominence_noise_sigma=2.0 / 1.4826,
        ),
        acquired_intensity_values=signal,
    )

    assert len(result.segments) == 1
    peak = result.segments[0]
    assert peak.apex_rt_min == pytest.approx(4.0, abs=0.01)
    assert peak.interval.rt_start_min < 2.1
    assert peak.interval.rt_end_min > 5.9
    known_area = 10000.0 * 0.7 * np.sqrt(2 * np.pi) * 60.0
    assert peak.area_baseline_corrected == pytest.approx(known_area, rel=0.015)


def test_clear_separated_groups_restore_one_representative_per_lobe() -> None:
    rt = np.linspace(0.0, 4.0, 2001)
    central_lobe = sum(
        amplitude * np.exp(-0.5 * ((rt - center) / 0.055) ** 2)
        for amplitude, center in ((100.0, 1.88), (105.0, 2.0), (65.0, 2.12))
    )
    signal = (
        central_lobe
        + 25.0 * np.exp(-0.5 * ((rt - 1.4) / 0.015) ** 2)
        + 25.0 * np.exp(-0.5 * ((rt - 2.7) / 0.015) ** 2)
    )

    result = enumerate_chrom_peak_segments(
        rt,
        signal,
        np.zeros_like(signal),
        policy=ChromPeakSegmentPolicy(
            min_apex_fraction_of_context=0.0,
            morphology_trace_method="rt_envelope",
            morphology_min_sigma_rt=0.08,
            min_prominence_noise_sigma=2.0 / 1.4826,
        ),
        acquired_intensity_values=signal,
    )

    assert [segment.apex_rt_min for segment in result.segments] == pytest.approx(
        [1.4, 1.992, 2.7]
    )
    central = result.segments[1]
    assert central.interval.rt_start_min == pytest.approx(1.73)
    assert central.interval.rt_end_min == pytest.approx(2.26)


def test_shoulder_envelope_preserves_mass_and_weak_isolated_peak() -> None:
    rt = np.linspace(0.0, 12.0, 3601)
    signal = (
        10000.0 * np.exp(-0.5 * ((rt - 3.0) / 0.6) ** 2)
        + 8000.0 * np.exp(-0.5 * ((rt - 4.8) / 0.6) ** 2)
        + 800.0 * np.exp(-0.5 * ((rt - 9.0) / 0.25) ** 2)
    )
    result = enumerate_chrom_peak_segments(
        rt,
        signal,
        np.zeros_like(signal),
        policy=ChromPeakSegmentPolicy(
            morphology_trace_method="rt_envelope",
            morphology_min_sigma_rt=0.08,
            min_prominence_noise_sigma=2.0 / 1.4826,
        ),
        acquired_intensity_values=signal,
    )

    assert len(result.segments) == 2
    envelope, isolated = result.segments
    assert "shoulder_overlap" in envelope.evidence_sources
    assert envelope.apex_rt_min == pytest.approx(3.0, abs=0.08)
    assert envelope.interval.rt_start_min < 3.0 - 1.2
    assert envelope.interval.rt_end_min > 4.8 + 1.2
    # The unresolved envelope preserves the observed sum, not two claimed
    # deconvolved, compound-specific Gaussian areas.
    known_overlap_area = (10000.0 + 8000.0) * 0.6 * np.sqrt(2 * np.pi) * 60.0
    assert envelope.area_baseline_corrected == (
        pytest.approx(known_overlap_area, rel=0.01)
    )
    assert isolated.apex_rt_min == pytest.approx(9.0, abs=0.04)
    assert "shoulder_overlap" not in isolated.evidence_sources


@pytest.mark.parametrize("reverse", (False, True))
def test_remote_small_pulses_do_not_split_an_unresolved_shoulder(
    reverse: bool,
) -> None:
    from xic_extractor.peak_detection.ms1_morphology import (
        gaussian15_morphology_trace,
    )

    rt = np.linspace(0.0, 16.0, 8001)
    signal = 10000.0 * np.exp(-0.5 * ((rt - 7.5) / 0.6) ** 2) + 14000.0 * np.exp(
        -0.5 * ((rt - 9.1) / 0.6) ** 2
    )
    for center in (12.5, 13.5):
        signal += 20.0 * np.exp(-0.5 * ((rt - center) / 0.003) ** 2)
    if reverse:
        signal = signal[::-1]
    canonical = gaussian15_morphology_trace(signal, window_points=15)
    result = enumerate_chrom_peak_segments(
        rt,
        canonical,
        np.zeros_like(signal),
        policy=ChromPeakSegmentPolicy(
            morphology_trace_method="rt_envelope",
            morphology_min_sigma_rt=0.08,
            min_apex_fraction_of_context=0.0,
            min_prominence_noise_sigma=2.0 / 1.4826,
            baseline_return_fraction=0.0,
            baseline_return_min_residual=0.0,
            include_baseline_return_samples=True,
        ),
        acquired_intensity_values=signal,
    )

    # A persistent but shallow valley does not establish independent peaks.
    # Remote pulses cannot change that relation or erase overlap review.
    principal = [s for s in result.segments if 6.0 < s.apex_rt_min < 11.0]
    assert len(principal) == 1
    assert all("shoulder_overlap" in s.evidence_sources for s in principal)
    centers = (6.9, 8.5) if reverse else (7.5, 9.1)
    assert principal[0].apex_rt_min == pytest.approx(6.9 if reverse else 9.1, abs=0.12)
    assert principal[0].interval.rt_start_min < min(centers) - 1.2
    assert principal[-1].interval.rt_end_min > max(centers) + 1.2
    known_area = (10000.0 + 14000.0) * 0.6 * np.sqrt(2 * np.pi) * 60.0
    assert sum(s.area_baseline_corrected for s in principal) == pytest.approx(
        known_area, rel=0.01
    )


def test_rt_envelope_peak_count_and_apex_are_stable_across_rt_density() -> None:
    observations: list[tuple[int, float]] = []
    for point_count in (1201, 2401):
        rt = np.linspace(0.0, 8.0, point_count)
        envelope = 10000.0 * np.exp(-0.5 * ((rt - 4.0) / 0.7) ** 2)
        signal = envelope * (1.0 + 0.055 * np.cos(2 * np.pi * (rt - 4.0) / 0.12))
        result = enumerate_chrom_peak_segments(
            rt,
            signal,
            np.zeros_like(signal),
            policy=ChromPeakSegmentPolicy(
                morphology_trace_method="rt_envelope",
                morphology_min_sigma_rt=0.08,
                min_prominence_noise_sigma=2.0 / 1.4826,
            ),
            acquired_intensity_values=signal,
        )
        observations.append((len(result.segments), result.segments[0].apex_rt_min))

    assert [count for count, _ in observations] == [1, 1]
    assert [apex for _, apex in observations] == pytest.approx([4.0, 4.0], abs=0.01)


def test_rt_envelope_reports_original_signal_apex_within_smoothed_bounds() -> None:
    rt = np.linspace(0.0, 8.0, 2401)
    broad_peak = 100.0 * np.exp(-0.5 * ((rt - 4.0) / 0.5) ** 2)
    narrow_apex = 75.0 * np.exp(-0.5 * ((rt - 4.18) / 0.004) ** 2)
    signal = broad_peak + narrow_apex

    result = enumerate_chrom_peak_segments(
        rt,
        signal,
        np.zeros_like(signal),
        policy=ChromPeakSegmentPolicy(
            morphology_trace_method="rt_envelope",
            morphology_min_sigma_rt=0.08,
        ),
    )

    assert len(result.segments) == 1
    segment = result.segments[0]
    assert segment.apex_index == int(np.argmax(signal))
    assert segment.apex_rt_min == pytest.approx(4.18, abs=0.005)
    assert int(np.argmax(result.morphology_trace_values)) != segment.apex_index
    assert segment.morphology_apex_residual == pytest.approx(signal[segment.apex_index])


@pytest.mark.parametrize("sigma", (0.01, 0.02))
def test_rt_envelope_preserves_canonical_clear_valley_between_supported_peaks(
    sigma: float,
) -> None:
    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        enumerate_physical_segment_universe,
        evaluate_physical_peak,
        prepare_physical_signal,
    )

    rt = np.linspace(0.0, 2.0, 1001)
    raw = (
        10.0
        + 1000.0 * np.exp(-0.5 * ((rt - 0.94) / sigma) ** 2)
        + 1000.0 * np.exp(-0.5 * ((rt - 1.06) / sigma) ** 2)
    )
    profile = SignalProcessingProfile()
    prepared = prepare_physical_signal(rt, raw, profile=profile)
    universe = enumerate_physical_segment_universe(
        prepared,
        request_context_rt_start_min=0.0,
        request_context_rt_end_min=2.0,
    )

    assert len(universe.segments) == 2
    left, right = universe.segments
    assert left.right_rt_min <= right.left_rt_min
    assert [left.apex_rt_min, right.apex_rt_min] == pytest.approx([0.94, 1.06])
    analytic_area = 1000.0 * sigma * np.sqrt(2.0 * np.pi) * 60.0
    # A 10%-height return retains about 96.8% of an isolated Gaussian mass;
    # sampled bracketing and the shared valley can retain a little more.
    assert all(
        0.96 * analytic_area < segment.area_counts_seconds < 1.01 * analytic_area
        for segment in (left, right)
    )

    evaluations = tuple(
        evaluate_physical_peak(
            prepared,
            query_rt_min=query_rt,
            request_context_rt_start_min=0.0,
            request_context_rt_end_min=2.0,
        )
        for query_rt in (0.94, 1.06)
    )
    assert all(evaluation.state == "available" for evaluation in evaluations)
    assert (
        len(
            {
                evaluation.answer.selected_segment
                for evaluation in evaluations
                if evaluation.facts is not None
            }
        )
        == 2
    )


def test_clear_valley_classification_uses_one_signal_scale() -> None:
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        evaluate_physical_peak,
        prepare_physical_signal,
    )

    rt = np.linspace(0.0, 2.0, 1001)
    raw = 10.0 + sum(
        1000.0 * np.exp(-0.5 * ((rt - center) / 0.055) ** 2)
        for center in (0.885, 1.115)
    )
    prepared = prepare_physical_signal(rt, raw)
    for center in (0.885, 1.115):
        answer = evaluate_physical_peak(prepared, query_rt_min=center)
        assert answer.state == "available"
        assert len(answer.segment_universe.segments) == 2
        # The analytical valley is ~22% of either apex, below the 30%
        # clear-valley limit, even though envelope smoothing lowers the apex.
        assert not answer.requires_overlap_review


@pytest.mark.parametrize("reverse", (False, True))
def test_clear_split_survives_asymmetric_envelope_apex_shift(reverse: bool) -> None:
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        evaluate_physical_peak,
        prepare_physical_signal,
    )

    rt = np.linspace(0.0, 2.0, 1001)
    width = np.where(rt < 1.0, 0.08, 0.005)
    raw = (
        10.0
        + 1000.0 * np.exp(-0.5 * ((rt - 1.0) / width) ** 2)
        + 1000.0 * np.exp(-0.5 * ((rt - 1.1) / 0.005) ** 2)
    )
    if reverse:
        raw = raw[::-1]
    query = 0.9 if reverse else 1.1
    answer = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=query,
    )

    assert answer.state == "available"
    assert answer.facts is not None
    assert answer.facts.apex_rt_min == pytest.approx(query, abs=0.002)
    assert len(answer.segment_universe.segments) == 2
    assert not answer.requires_overlap_review


@pytest.mark.parametrize("reverse", (False, True))
@pytest.mark.parametrize("trailing_ripple", (False, True))
def test_local_doublet_and_weak_peak_survive_remote_connected_ripple(
    reverse: bool,
    trailing_ripple: bool,
) -> None:
    rt = np.linspace(0.0, 5.0, 2501)
    width = np.where(rt < 2.35, 0.10, 0.32)
    signal = 100.0 * np.exp(-0.5 * ((rt - 2.08) / 0.055) ** 2) + 150.0 * np.exp(
        -0.5 * ((rt - 2.35) / width) ** 2
    )
    if trailing_ripple:
        # Brief tail troughs keep these fine-scale maxima connected to the
        # second lobe, even across the next coarse basin's boundary.
        signal *= 1.0 + (0.3 * np.cos(2 * np.pi * (rt - 2.35) / 0.04) * (rt > 2.8))
        signal += 20.0 * np.exp(-0.5 * ((rt - 3.4) / 0.12) ** 2)
    signal += 8.0 * np.exp(-0.5 * ((rt - 1.6) / 0.025) ** 2)
    if reverse:
        signal = signal[::-1]

    result = enumerate_chrom_peak_segments(
        rt,
        signal,
        np.zeros_like(signal),
        policy=ChromPeakSegmentPolicy(
            min_apex_fraction_of_context=0.0,
            baseline_return_fraction=0.1,
            baseline_return_min_residual=0.0,
            include_baseline_return_samples=True,
            clear_valley_max_fraction=0.85,
            morphology_trace_method="rt_envelope",
            morphology_trace_window_points=9,
            morphology_min_sigma_rt=0.08,
            min_prominence_fraction_of_apex=0.15,
            valley_min_duration_min=0.08,
        ),
    )

    apexes = [s.apex_rt_min for s in result.segments]
    expected = [1.6, 2.08, 2.35] + ([3.4] if trailing_ripple else [])
    if reverse:
        expected = sorted(5.0 - center for center in expected)
    # Restore the unresolved pair, not a candidate for every tail ripple.
    # The independently generated weak peak is below 10% of the main peak;
    # it must not be erased by a main-relative restoration cutoff.
    assert apexes == pytest.approx(expected, abs=0.015)
    weak_center = 3.4 if reverse else 1.6
    weak = min(result.segments, key=lambda s: abs(s.apex_rt_min - weak_center))
    analytic_weak_area = 8.0 * 0.025 * np.sqrt(2 * np.pi) * 60.0
    assert weak.area_baseline_corrected == pytest.approx(analytic_weak_area, rel=0.04)


def _values(text: str) -> list[float]:
    return [float(value) for value in text.split()]


# Acquired excerpts (kilocounts) of regions reviewed in #1049 (case IDs in
# output/1049_phase_b/b_classification.csv); the oracle is the reviewed class.
_DOUBLET_553_RT = _values(
    """
    14.992 15.013 15.038 15.076 15.118 15.159 15.201 15.242 15.284 15.325 15.363
    15.404 15.446 15.487 15.528 15.57 15.611 15.653 15.694
    """
)  # O29 (B): 553.141 in NormalBC2312, raw valley ~0.36 of the smaller lobe
_DOUBLET_553 = _values(
    """
    0 0 22.7 195 505.8 503.1 395.2 235.6 181.3 209.1 236.7 240.6 376.5 708.7
    339.9 141 0 21 11.3
    """
)
_DROPOUT_328_RT = _values(
    """
    14.001 14.03 14.065 14.107 14.148 14.19 14.231 14.273 14.314 14.356 14.397
    14.438 14.48 14.521 14.563 14.604 14.646
    """
)  # O07 (A): one component whose raw alternates with zero dropouts
_DROPOUT_328 = _values(
    """
    0 0 15.6 30.3 72.4 43.8 73.3 0 63.5 0 60.2 55.1 52.3 25.7 16.8 0 0
    """
)
# O28 (W): 359.045 in NormalBC2312, a main peak at 6.13 after a 3 min plateau.
# A real dip separates the last plateau hump, but that hump is no peak of its
# own: the plateau never returns below the valley level.
_PLATEAU_359_RT = _values(
    """
    3.266 3.307 3.349 3.39 3.432 3.473 3.506 3.54 3.57 3.609 3.631 3.657 3.681
    3.699 3.709 3.726 3.735 3.754 3.77 3.788 3.804 3.82 3.829 3.844 3.854 3.875
    3.886 3.904 3.918 3.925 3.946 3.963 3.976 3.993 4.006 4.021 4.037 4.044
    4.059 4.066 4.083 4.088 4.103 4.113 4.132 4.136 4.154 4.161 4.191 4.197
    4.223 4.234 4.251 4.26 4.274 4.286 4.291 4.303 4.316 4.325 4.34 4.351 4.368
    4.376 4.387 4.399 4.41 4.418 4.435 4.441 4.453 4.46 4.48 4.492 4.497 4.516
    4.522 4.536 4.541 4.557 4.561 4.581 4.587 4.602 4.608 4.624 4.634 4.651
    4.657 4.669 4.674 4.694 4.705 4.733 4.742 4.772 4.787 4.809 4.836 4.851
    4.876 4.896 4.918 4.944 4.964 4.989 5.017 5.051 5.069 5.101 5.121 5.162
    5.175 5.216 5.235 5.276 5.299 5.34 5.362 5.403 5.437 5.469 5.51 5.546 5.582
    5.62 5.651 5.677 5.709 5.734 5.759 5.788 5.814 5.85 5.882 5.909 5.946 5.969
    5.995 6.023 6.041 6.069 6.087 6.103 6.111 6.125 6.132 6.141 6.15 6.159 6.164
    6.169 6.176 6.181 6.186 6.193 6.198 6.209 6.221 6.231 6.239 6.25 6.261 6.273
    6.285 6.301 6.313 6.328 6.341 6.358 6.362 6.376 6.385 6.4 6.41 6.424 6.434
    6.443 6.461 6.477 6.49 6.504 6.526 6.543 6.574 6.594 6.629 6.646 6.687 6.717
    """
)
_PLATEAU_359 = _values(
    """
    26.2 0 0 32.6 40.4 41.2 36.4 28.5 28.7 25.4 37.9 41.8 33.9 33.5 46.3 32.3
    50.9 36.1 27.2 32.4 38.7 29.3 36.7 42.2 31.6 47.1 49.8 41.4 47 47 43.2 43.4
    35.6 33.5 41.5 54.8 38.3 39.2 42.4 53.5 61.4 46.8 33 38.3 46.3 53.8 62.4
    38.5 50.5 68.2 56 46.3 51.2 52.4 55.2 71 48.4 47.1 46.9 46.9 66.9 53.1 47
    42.3 47.5 49.7 52.2 58.5 53.9 44.3 66.1 79.9 53.4 57.7 66.1 59.5 70.8 64.4
    52.5 74.8 64.1 69.7 67.6 53.4 59.1 49.5 58.2 69.8 72.5 79.5 68.1 80.4 51.1
    72.8 79.1 59.9 97.8 69.7 67.3 64.6 58.5 41.9 56.3 34.4 54.1 50.1 34.6 48.4
    49.3 39.4 45.9 59.4 48.7 72.1 66.3 70.4 47.2 73.8 58.9 66.5 62.7 60.6 61.4
    113.4 86.3 75.1 85.2 94.9 82.2 74.9 71.4 52.6 52.7 46.2 39.6 45 52 61.5 68.5
    117.4 83 91.7 111.5 136.1 131.9 148.5 143.5 100.4 111.2 112 122.3 113.1
    102.9 103.4 112.1 94.4 105.7 89.3 72.6 101.3 95.8 81.3 77.7 69.4 66.7 48.5
    53.8 51.9 39 56.8 64.8 61.9 57.6 45.8 51 47.9 51.3 40.8 45.7 53.4 49.9 51.8
    71.8 76.3 60.9 49.9 26.2 31.6 0 0
    """
)


def _physical_policy() -> ChromPeakSegmentPolicy:
    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        _physical_segment_policy,
    )

    return _physical_segment_policy(SignalProcessingProfile())


@pytest.mark.parametrize(
    ("rt", "raw", "expected_valley_rt"),
    (
        (_DOUBLET_553_RT, _DOUBLET_553, (15.284,)),
        (_DROPOUT_328_RT, _DROPOUT_328, ()),
        (_PLATEAU_359_RT, _PLATEAU_359, ()),
    ),
    ids=("doublet", "zero_dropouts", "plateau_hump"),
)
def test_separable_valleys_follow_reviewed_regions(rt, raw, expected_valley_rt):
    from xic_extractor.peak_detection.chrom_peak_segments import separable_valleys

    rt_values, raw_values = np.asarray(rt), np.asarray(raw)
    cuts = separable_valleys(rt_values, raw_values, 0, len(rt), _physical_policy())

    assert tuple(float(rt_values[cut]) for cut in cuts) == expected_valley_rt


def test_separable_valley_does_not_depend_on_scan_order():
    from xic_extractor.peak_detection.chrom_peak_segments import separable_valleys

    raw = np.asarray(_DOUBLET_553)
    forward = separable_valleys(
        np.asarray(_DOUBLET_553_RT), raw, 0, len(raw), _physical_policy()
    )
    mirrored_rt = -np.asarray(_DOUBLET_553_RT)[::-1]
    backward = separable_valleys(
        mirrored_rt, raw[::-1], 0, len(raw), _physical_policy()
    )

    assert [len(raw) - 1 - cut for cut in backward] == list(forward)


def test_discovery_components_split_the_reviewed_doublet():
    from xic_extractor.configuration.signal_processing_profile import (
        SignalProcessingProfile,
    )
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        discovery_raw_peak_components,
    )

    rt = np.asarray(_DOUBLET_553_RT)
    components = discovery_raw_peak_components(
        rt, np.asarray(_DOUBLET_553), SignalProcessingProfile()
    )

    assert [float(rt[c.apex_index]) for c in components] == [15.118, 15.487]
