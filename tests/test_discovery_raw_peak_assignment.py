"""CID-NL events belong to acquired valley basins, not integration returns."""

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import TEST_PROFILE
from tests.discovery_ms1_capture_fixtures import (
    _BoundedRaw,
    _gaussian,
    _seed,
    _settings,
)
from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.ms1_capture import capture_ms1_candidates
from xic_extractor.peak_detection.chrom_peak_segments import local_apex_prominence
from xic_extractor.peak_detection.physical_peak_evaluation import (
    discovery_raw_peak_context_edges,
    evaluate_physical_peak,
    prepare_physical_signal,
    unique_physical_segment_at_rt,
)
from xic_extractor.peak_detection.physical_peak_models import (
    DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
)


def test_discovery_tail_event_selects_its_raw_peak_beyond_quantitative_return():
    rt = np.linspace(3.0, 8.0, 501)
    raw = _gaussian(rt, center=5.0, sigma=0.3, height=1000.0)
    prepared = prepare_physical_signal(rt, raw, profile=TEST_PROFILE)
    result = evaluate_physical_peak(
        prepared,
        query_rt_min=5.8,
        selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    )
    assert result.facts is not None
    assert result.facts.apex_rt_min == pytest.approx(5.0, abs=0.02)
    assert result.facts.right_rt_min < 5.8
    analytic_area = 1000.0 * 0.3 * np.sqrt(2 * np.pi) * 60.0
    assert result.facts.area_counts_seconds == pytest.approx(analytic_area, rel=0.15)


def test_bounded_capture_recovers_a_tail_window_and_keeps_its_nl_admission():
    rt = np.linspace(1.0, 10.0, 901)
    raw_values = _gaussian(rt, center=5.0, sigma=0.5, height=1000.0)
    raw = _BoundedRaw(rt=rt, intensity=raw_values)
    settings = _settings()
    groups = group_discovery_seeds((_seed(scan_number=10, rt=6.3),), settings=settings)
    result = capture_ms1_candidates(
        raw,
        groups,
        acquisition_source=raw.acquisition_source,
        settings=settings,
        signal_processing_profile=TEST_PROFILE,
        ms1_centroid_mz=lambda *_: None,
    )
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.ms1_peak_found
    assert candidate.ms1_apex_rt == pytest.approx(5.0, abs=0.03)
    assert candidate.ms1_peak_rt_end < candidate.best_seed_rt
    assert candidate.discovery_candidate_state == "ms1_feature_nl_supported"
    assert result.measurements.reacquisition_request_count == 1


def test_raw_zero_valley_keeps_a_smaller_nl_peak_separate_from_bigger_neighbor():
    rt = np.arange(13.8, 14.9, 0.04)
    raw = 250000.0 * np.maximum(1.0 - abs(rt - 14.14) / 0.12, 0.0)
    raw += 200000.0 * np.maximum(1.0 - abs(rt - 14.48) / 0.12, 0.0)
    prepared = prepare_physical_signal(rt, raw, profile=TEST_PROFILE)
    result = evaluate_physical_peak(
        prepared,
        query_rt_min=14.51,
        selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    )
    assert result.facts is not None
    assert result.facts.apex_rt_min == pytest.approx(14.48, abs=0.04)
    assert result.facts.left_rt_min > 14.26
    assert result.facts.right_rt_min < 14.75


def test_component_contrast_ignores_uncut_ripple_but_never_uses_interior_minimum():
    # One resolved component with an uncut top ripple and a lower interior point.
    # Both measured endpoints, not that minimum, limit its contrast.
    signal = np.asarray([60.0, 10.0, 100.0, 99.0, 101.0, 55.0])
    assert local_apex_prominence(signal, 4) == 41.0
    # A true split places the accepted valley at the component endpoint.
    assert local_apex_prominence(np.asarray([60.0, 100.0, 55.0]), 1) == 40.0


def test_shared_valley_does_not_lend_a_noise_components_segment_to_another_peak():
    rt = np.arange(0.0, 6.005, 0.01)
    raw = _gaussian(rt, center=2.2, sigma=0.06, height=1000.0)
    raw += _gaussian(rt, center=3.1, sigma=0.06, height=600.0)
    raw[raw < 15.0] = 0.0
    raw[int(np.argmin(abs(rt - 2.65)))] = 20.0
    prepared = prepare_physical_signal(rt, raw, profile=TEST_PROFILE)
    answers = []
    for query in (2.2, 3.1):
        answer = evaluate_physical_peak(
            prepared,
            query_rt_min=query,
            selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
        )
        assert answer.facts is not None
        assert answer.facts.apex_rt_min == pytest.approx(query, abs=0.01)
        segment = answer.answer.selected_segment
        assert segment.raw_component is not None
        assert rt[segment.raw_component.apex_index] == pytest.approx(query)
        assert (
            unique_physical_segment_at_rt(
                answer.segment_universe,
                query,
                selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
            )
            is segment
        )
        assert discovery_raw_peak_context_edges(answer.segment_universe, query) == (
            False,
            False,
        )
        answers.append(answer)
    assert answers[0].answer.selected_segment != answers[1].answer.selected_segment
