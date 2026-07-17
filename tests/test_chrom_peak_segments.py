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
