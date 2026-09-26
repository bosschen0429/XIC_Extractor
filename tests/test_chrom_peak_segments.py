import numpy as np
import pytest

from xic_extractor.peak_detection.chrom_peak_segments import (
    ChromPeakSegmentPolicy,
    enumerate_chrom_peak_segments,
    segments_containing_rt,
    select_segment_by_apex_rt,
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


def test_select_segment_by_apex_rt_keeps_targeted_choice_separate() -> None:
    rt, intensity, baseline = _trace([0, 0, 8, 60, 8, 0, 0, 20, 80, 20, 0, 0])
    result = enumerate_chrom_peak_segments(rt, intensity, baseline)

    selected = select_segment_by_apex_rt(result.segments, 3.0)
    stronger_context = select_segment_by_apex_rt(result.segments, 8.0)

    assert selected is not None
    assert stronger_context is not None
    assert selected.apex_rt_min == pytest.approx(3.0)
    assert stronger_context.apex_rt_min == pytest.approx(8.0)
    assert selected.segment_id != stronger_context.segment_id


def test_segments_containing_rt_does_not_fall_back_to_nearest_peak() -> None:
    rt, intensity, baseline = _trace([0, 0, 8, 60, 8, 0, 0, 20, 80, 20, 0, 0])
    result = enumerate_chrom_peak_segments(rt, intensity, baseline)

    first_peak = segments_containing_rt(result.segments, 3.0)
    between_peaks = segments_containing_rt(result.segments, 5.5)

    assert len(first_peak) == 1
    assert first_peak[0].apex_rt_min == pytest.approx(3.0)
    assert between_peaks == ()


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
            min_prominence_noise_mad=2.0,
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
            min_prominence_noise_mad=2.0,
        ),
        acquired_intensity_values=signal,
    )

    assert [segment.apex_rt_min for segment in result.segments] == pytest.approx(
        [1.4, 1.992, 2.7]
    )
    central = result.segments[1]
    assert central.interval.rt_start_min == pytest.approx(1.73)
    assert central.interval.rt_end_min == pytest.approx(2.26)


def test_envelope_support_preserves_doublet_and_weak_isolated_peak() -> None:
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
            min_prominence_noise_mad=2.0,
        ),
        acquired_intensity_values=signal,
    )

    assert len(result.segments) == 3
    assert [peak.apex_rt_min for peak in result.segments] == pytest.approx(
        [3.0, 4.8, 9.0], abs=0.04
    )
    assert all(
        "shoulder_overlap" in peak.evidence_sources for peak in result.segments[:2]
    )
    assert result.segments[0].interval.rt_end_min < 4.2
    assert result.segments[1].interval.rt_start_min > 3.6


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
                min_prominence_noise_mad=2.0,
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
    assert segment.morphology_apex_residual == pytest.approx(
        signal[segment.apex_index]
    )


@pytest.mark.parametrize("sigma", (0.01, 0.02))
def test_rt_envelope_preserves_canonical_clear_valley_between_supported_peaks(
    sigma: float,
) -> None:
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        enumerate_physical_segment_universe,
        evaluate_physical_peak,
        prepare_physical_signal,
    )
    from xic_extractor.peak_detection.signal_processing_profile import (
        SignalProcessingProfile,
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
    assert [left.area_counts_seconds, right.area_counts_seconds] == pytest.approx(
        [analytic_area, analytic_area], rel=0.01
    )

    evaluations = tuple(
        evaluate_physical_peak(
            prepared,
            selection_subject_id=f"peak-{query_rt}",
            query_rt_min=query_rt,
            request_context_rt_start_min=0.0,
            request_context_rt_end_min=2.0,
        )
        for query_rt in (0.94, 1.06)
    )
    assert all(evaluation.state == "available" for evaluation in evaluations)
    assert len(
        {
            evaluation.facts.physical_segment_id
            for evaluation in evaluations
            if evaluation.facts is not None
        }
    ) == 2


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
        answer = evaluate_physical_peak(
            prepared, selection_subject_id="resolved-doublet", query_rt_min=center
        )
        assert answer.state == "available"
        assert len(answer.segment_universe.segments) == 2
        # The analytical valley is ~22% of either apex, below the 30%
        # clear-valley limit, even though envelope smoothing lowers the apex.
        assert not answer.requires_overlap_review
