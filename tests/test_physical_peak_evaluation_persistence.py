from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tests.test_discovery_physical_fact_persistence import _population, _spec
from xic_extractor.discovery.physical_fact_persistence import (
    read_discovery_physical_fact_sidecar,
    write_discovery_physical_fact_sidecar,
)
from xic_extractor.peak_detection.background_shape import (
    has_deficient_monotone_run,
    has_unresolved_valley_floors,
    is_background_like_peak,
    is_provisional_peak_shape,
    is_unresolved_shoulder_peak,
)
from xic_extractor.peak_detection.models import PeakCandidatesResult
from xic_extractor.peak_detection.physical_peak_candidate_adapter import (
    physical_candidates_result,
)
from xic_extractor.peak_detection.physical_peak_codec import (
    deserialize_physical_peak_evaluation,
    serialize_physical_peak_evaluation,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    enumerate_physical_segment_universe,
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import (
    DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
    PHYSICAL_PEAK_NO_SELECTION_POLICY,
    PHYSICAL_PEAK_POINT_POLICY,
    PHYSICAL_PEAK_WINDOW_POLICY,
)
from xic_extractor.peak_detection.scoring_metrics import (
    noise_shape_severity,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)


def test_unavailable_prepared_signal_keeps_live_asls_intermediate(monkeypatch) -> None:
    import xic_extractor.peak_detection.physical_peak_evaluation as owner

    rt = np.linspace(0.0, 2.0, 31)
    raw = np.ones(len(rt))
    baseline = np.full(len(rt), 2.0)
    monkeypatch.setattr(
        owner, "asls_baseline_on_rt", lambda *_args, **_kwargs: baseline
    )
    result = prepare_physical_signal(rt, raw)
    assert result.projection.state == "unavailable"
    assert result.projection.reason == "no_signal_above_baseline"
    assert result.prepared is not None
    np.testing.assert_array_equal(result.prepared.positive_signal, np.zeros(len(rt)))
    assert result.baseline_unclipped == tuple(baseline)


def _context_sensitive_trace() -> tuple[np.ndarray, np.ndarray]:
    # Scaled observed XIC, with RT in minutes at 1.5 seconds per scan.
    # Removing one exterior scan leaves the retained Gaussian peak exact.
    raw = np.asarray(
        [
            236.132, 164.179, 104.242, 84.744, 131.893, 59.936, 55.567, 29.262,
            33.487, 27.028, 20.902, 16.914, 23.171, 19.101, 11.874, 14.134,
            30.926, 117.955, 378.598, 1206.0, 3363.568, 7394.956, 10749.463,
            12201.848, 11247.332, 7386.286, 4304.834, 2474.821, 1514.42,
            1122.02, 913.297, 694.172, 528.272, 422.653, 387.785, 328.499,
            291.29, 249.678, 200.976, 190.326, 144.765, 163.245, 164.729,
            227.957, 399.121, 895.663, 2136.916, 4325.4,
        ]
    )
    return np.arange(len(raw), dtype=float) * 0.025, raw


def test_exterior_context_change_keeps_observed_near_baseline_peak_available() -> None:
    rt, raw = _context_sensitive_trace()
    full_signal = prepare_physical_signal(rt, raw)
    cropped_signal = prepare_physical_signal(rt[:-1], raw[:-1])
    full = evaluate_physical_peak(
        full_signal, selection_subject_id="exterior-context", query_rt_min=rt[23]
    )
    assert full.state == "available" and full.facts is not None
    selected = next(
        s for s in full.segment_universe.segments
        if s.physical_segment_id == full.facts.physical_segment_id
    )
    assert selected.end_index + 7 <= len(rt) - 1
    assert full_signal.prepared is not None and cropped_signal.prepared is not None
    assert (
        full_signal.prepared.smoothed[selected.start_index:selected.end_index]
        == cropped_signal.prepared.smoothed[selected.start_index:selected.end_index]
    )

    cropped = evaluate_physical_peak(
        cropped_signal, selection_subject_id="exterior-context", query_rt_min=rt[23]
    )
    assert cropped.state == "available"
    assert cropped.facts is not None and cropped.integration is not None
    assert cropped.facts.apex_rt_min == full.facts.apex_rt_min
    assert (
        rt[0] < cropped.facts.left_rt_min < rt[23] < cropped.facts.right_rt_min < rt[-2]
    )


def test_retired_point_policy_is_rejected() -> None:
    rt, raw = _context_sensitive_trace()
    prepared = prepare_physical_signal(rt, raw)
    with pytest.raises(ValueError, match="unsupported physical peak selection policy"):
        enumerate_physical_segment_universe(
            prepared, selection_policy_version="unsupported_point_policy"
        )


def test_truncated_physical_peak_has_no_numeric_facts() -> None:
    rt = np.linspace(0.0, 1.0, 31)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 1.05) / 0.18) ** 2)
    prepared = prepare_physical_signal(rt, raw)
    result = evaluate_physical_peak(
        prepared, selection_subject_id="truncated-peak", query_rt_min=0.9
    )

    assert result.state == "unavailable"
    assert result.reason == "incomplete_peak_context"
    assert result.facts is None
    assert result.integration is None


@pytest.mark.parametrize(
    "policy",
    [
        PHYSICAL_PEAK_POINT_POLICY,
        PHYSICAL_PEAK_WINDOW_POLICY,
    ],
)
@pytest.mark.parametrize("positive_points", [1, 2])
def test_original_xic_support_limits_physical_answers(
    policy: str, positive_points: int
) -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.zeros_like(rt)
    raw[15 : 15 + positive_points] = 1000.0
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        selection_subject_id="original-sampling-support",
        query_rt_min=1.5,
        selection_policy_version=policy,
    )

    if positive_points == 1:
        assert result.state == "unavailable"
        assert result.reason == "insufficient_original_xic_support"
        assert result.answer.physical_segment_id is None
        assert result.facts is None
        assert result.integration is None
    else:
        assert result.state == "available"
        assert result.facts is not None
        assert result.facts.area_counts_seconds > 0.0


@pytest.mark.parametrize(
    "policy,query_rt",
    [
        (PHYSICAL_PEAK_POINT_POLICY, 1.5),
        (PHYSICAL_PEAK_WINDOW_POLICY, 0.2),
    ],
)
@pytest.mark.parametrize("positions", [(14, 16), (12, 14, 16, 18)])
def test_discontinuous_original_points_cannot_mint_fresh_physical_facts(
    policy: str, query_rt: float, positions: tuple[int, ...]
) -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.zeros_like(rt)
    raw[list(positions)] = 1000.0
    prepared = prepare_physical_signal(rt, raw)
    fresh = evaluate_physical_peak(
        prepared,
        selection_subject_id="isolated-acquired-impulses",
        query_rt_min=query_rt,
        selection_policy_version=policy,
    )
    assert fresh.state == "unavailable"
    assert fresh.reason == "insufficient_original_xic_continuity"
    assert fresh.answer.physical_segment_id is None
    assert fresh.facts is None and fresh.integration is None


@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, PHYSICAL_PEAK_WINDOW_POLICY]
)
def test_original_continuity_accepts_a_pair_despite_another_isolated_point(
    policy: str,
) -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.zeros_like(rt)
    raw[[14, 15, 17]] = 1000.0
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        selection_subject_id="pair-with-a-sampling-gap",
        query_rt_min=1.5,
        selection_policy_version=policy,
    )
    assert result.state == "available"
    assert result.facts is not None and result.facts.area_counts_seconds > 0.0


@pytest.mark.parametrize(
    "policy,query_rt",
    [
        (PHYSICAL_PEAK_POINT_POLICY, 5.0),
        (PHYSICAL_PEAK_WINDOW_POLICY, 0.5),
    ],
)
def test_fragmented_original_peak_is_not_backfilled_by_smoothed_envelope(
    policy: str, query_rt: float,
) -> None:
    rt = np.linspace(0.0, 10.0, 101)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 5.0) / 0.8) ** 2)
    raw[::2] = 0.0
    raw[50] = 1000.0  # An adjacent pair does not repair repeated interruptions.
    profile = SignalProcessingProfile()
    prepared = prepare_physical_signal(rt, raw, profile=profile)
    fresh = evaluate_physical_peak(
        prepared, selection_subject_id="fragmented-envelope", query_rt_min=query_rt,
        selection_policy_version=policy,
    )
    assert fresh.state == "unavailable"
    assert fresh.reason == "insufficient_local_hit_enrichment"
    assert fresh.facts is None and fresh.integration is None
    assert fresh.answer.physical_segment_id is None
    fresh_payload = serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=fresh,
    )
    *_, restored_fresh = deserialize_physical_peak_evaluation(fresh_payload)
    assert restored_fresh == fresh
    assert serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=restored_fresh,
    ) == fresh_payload


@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY]
)
@pytest.mark.parametrize(
    "intensity_pattern",
    ["smooth", "irregular", "no_adjacent_hits", "sparse_hits", "brief_continuous_run"],
)
def test_censored_hit_cluster_uses_observed_area_and_persists_warning(
    policy: str, intensity_pattern: str,
) -> None:
    rt = np.arange(401, dtype=float) / 100.0
    raw = np.zeros(401)
    # One dense detection cluster, with no detected background. Intensity
    # valleys are censoring gaps, not evidence for separate chromatographic peaks.
    positions = np.arange(160, 241)
    positions = positions[(positions % 3 == 0) | (positions == 199)]
    raw[positions] = 100.0 + 200.0 * np.exp(-(((positions - 200) / 25.0) ** 2))
    if intensity_pattern == "irregular":
        raw[positions] = np.random.default_rng(531).uniform(
            100.0, 1000.0, len(positions)
        )
    elif intensity_pattern == "no_adjacent_hits":
        raw[199] = 0.0
    elif intensity_pattern == "sparse_hits":
        raw[:] = 0.0
        positions = np.arange(130, 271, 7)
        raw[positions] = 200.0
    elif intensity_pattern == "brief_continuous_run":
        # One fully acquired smoothing window inside an otherwise censored
        # cluster does not establish ordinary support across the whole core.
        raw[193:209] = 100.0 + 200.0 * np.exp(
            -(((np.arange(193, 209) - 200) / 25.0) ** 2)
        )
    profile = SignalProcessingProfile()
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        selection_subject_id="censored-cluster",
        query_rt_min=2.0,
        selection_policy_version=policy,
    )
    assert result.state == "available"
    assert result.facts is not None and result.integration is not None
    assert result.facts.left_rt_min <= rt[positions[0]]
    assert result.facts.right_rt_min >= rt[positions[-1]]
    assert result.facts.area_counts_seconds == pytest.approx(np.trapezoid(raw, rt) * 60)
    assert "near_detection_limit" in result.integration.boundary_sources
    other = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        selection_subject_id="censored-cluster",
        query_rt_min=2.0,
        selection_policy_version=(
            DISCOVERY_PHYSICAL_PEAK_POINT_POLICY
            if policy == PHYSICAL_PEAK_POINT_POLICY
            else PHYSICAL_PEAK_POINT_POLICY
        ),
    )
    assert other.state == result.state
    assert other.answer.physical_segment_id == result.answer.physical_segment_id
    assert other.integration == result.integration
    payload = serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=result
    )
    *_, restored = deserialize_physical_peak_evaluation(payload)
    assert restored == result


@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY]
)
def test_stationary_sparse_hits_do_not_establish_a_censored_peak(policy) -> None:
    rt = np.arange(401, dtype=float) / 100.0
    raw = np.zeros(401)
    raw[::3] = 200.0
    raw[199] = 200.0
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        selection_subject_id="stationary-hits",
        query_rt_min=2.0,
        selection_policy_version=policy,
    )
    assert result.state == "unavailable"
    assert result.facts is None


@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY]
)
def test_strong_continuous_peak_with_sparse_tail_keeps_original_segment(
    policy: str,
) -> None:
    rt = np.arange(401, dtype=float) / 100.0
    raw = 10000.0 * np.exp(-0.5 * ((rt - 2.0) / 0.1) ** 2)
    raw[raw < 1.0] = 0.0
    raw[[242, 249, 256, 263, 270]] = 20.0
    prepared = prepare_physical_signal(rt, raw)
    result = evaluate_physical_peak(
        prepared, selection_subject_id="strong-peak-with-sparse-tail",
        query_rt_min=2.0, request_context_rt_start_min=0.5,
        request_context_rt_end_min=3.5, selection_policy_version=policy,
    )
    assert result.state == "available" and result.reason == ""
    assert result.facts is not None and result.integration is not None
    selected = next(
        segment for segment in result.segment_universe.segments
        if segment.physical_segment_id == result.facts.physical_segment_id
    )
    # The continuous Gaussian returns to baseline before the sparse tail.
    # Envelope/baseline revisions may change ordinary boundaries, but density
    # must not absorb the sparse tail beginning at 2.42 min.
    assert 1.6 <= selected.left_rt_min < 2.0
    assert 2.0 < selected.right_rt_min < rt[242]
    assert selected.evidence_sources == (
        "morphology_trace", "morphology_local_maximum", "baseline_return",
        "raw_area_asls",
    )
    assert prepared.prepared is not None
    interval = slice(selected.start_index, selected.end_index)
    ordinary_area = np.trapezoid(
        np.asarray(prepared.prepared.positive_signal)[interval],
        rt[interval],
    ) * 60.0
    assert result.facts.area_counts_seconds == pytest.approx(ordinary_area)
    assert result.integration.area_raw_counts_seconds == pytest.approx(
        np.trapezoid(raw[interval], rt[interval]) * 60.0
    )
    assert "near_detection_limit" not in result.integration.boundary_sources


@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY]
)
@pytest.mark.parametrize("background", [0.0, 20.0])
def test_continuous_peak_with_isolated_dropouts_keeps_morphology_area(
    policy: str, background: float,
) -> None:
    rt = np.arange(801, dtype=float) / 100.0
    raw = background + 10000.0 * np.exp(-0.5 * ((rt - 4.0) / 0.35) ** 2)
    raw[raw < 1.0] = 0.0
    # Several isolated missing observations split the binary runs, while long
    # acquired stretches still support the ordinary chromatographic envelope.
    raw[[370, 389, 423, 439]] = 0.0
    prepared = prepare_physical_signal(rt, raw)
    result = evaluate_physical_peak(
        prepared, selection_subject_id="ordinary-peak-with-dropouts",
        query_rt_min=4.0, selection_policy_version=policy,
    )
    assert result.state == "available"
    assert result.facts is not None and result.integration is not None
    assert 3.8 < result.facts.apex_rt_min < 4.2
    assert result.facts.area_source == "trapezoid_counts_seconds_on_canonical_signal"
    assert "near_detection_limit" not in result.integration.boundary_sources
    assert "local_hit_density_enrichment" not in result.integration.boundary_sources
    assert prepared.prepared is not None
    segment = next(
        s for s in result.segment_universe.segments
        if s.physical_segment_id == result.facts.physical_segment_id
    )
    interval = slice(segment.start_index, segment.end_index)
    assert result.facts.area_counts_seconds == pytest.approx(
        np.trapezoid(
            np.asarray(prepared.prepared.positive_signal)[interval], rt[interval]
        )
        * 60.0
    )
    payload = serialize_physical_peak_evaluation(
        rt, raw, profile=SignalProcessingProfile(), evaluation=result
    )
    *_, restored = deserialize_physical_peak_evaluation(payload)
    assert restored == result


def test_tail_dropout_does_not_move_boundary_away_from_persistent_valley() -> None:
    rt = np.arange(1201, dtype=float) / 200.0
    raw = (
        10000.0 * np.exp(-0.5 * ((rt - 2.0) / 0.7) ** 2)
        + 6000.0 * np.exp(-0.5 * ((rt - 4.0) / 0.45) ** 2)
    )
    results = []
    for with_dropout in (False, True):
        acquired = raw.copy()
        if with_dropout:
            acquired[568:573] = 0.0
        result = evaluate_physical_peak(
            prepare_physical_signal(rt, acquired),
            selection_subject_id="two-lobes-with-tail-dropout", query_rt_min=2.0,
        )
        assert result.state == "available" and result.facts is not None
        # The persistent valley is between the two analytical Gaussian lobes,
        # after the narrow missing-scan notch on the first lobe's falling side.
        assert 3.1 < result.facts.right_rt_min < 3.5
        assert result.facts.apex_rt_min < 2.2
        results.append(result.facts)
    assert abs(results[0].right_rt_min - results[1].right_rt_min) < 0.1


def _physical_candidates(rt: np.ndarray, raw: np.ndarray) -> PeakCandidatesResult:
    prepared = prepare_physical_signal(rt, raw)
    universe = enumerate_physical_segment_universe(
        prepared,
        request_context_rt_start_min=float(rt[0]),
        request_context_rt_end_min=float(rt[-1]),
    )
    return physical_candidates_result(prepared, universe)


@pytest.mark.parametrize("dropout", [False, True], ids=["intact", "three_dropouts"])
def test_short_valley_dropout_preserves_two_quantitative_lobes(dropout: bool) -> None:
    rt = np.linspace(0.0, 5.0, 1001)
    height, sigma = 1000.0, 0.20
    raw = height * (
        np.exp(-0.5 * ((rt - 2.00) / sigma) ** 2)
        + np.exp(-0.5 * ((rt - 2.54) / sigma) ** 2)
    )
    if dropout:
        raw[453:456] = 0.0
    result = _physical_candidates(rt, raw)
    assert result.status == "OK"
    assert len(result.candidates) == 2
    peaks = sorted(
        (candidate.peak for candidate in result.candidates), key=lambda peak: peak.rt
    )
    assert [peak.rt for peak in peaks] == pytest.approx([2.015, 2.525], abs=0.04)
    # Equal Gaussian lobes have their analytical valley at the midpoint.
    assert peaks[0].peak_end == pytest.approx(2.27, abs=0.01)
    assert peaks[1].peak_start == pytest.approx(2.27, abs=0.01)
    one_lobe_mass = height * sigma * np.sqrt(2.0 * np.pi) * 60.0
    for peak in peaks:
        # Baseline and missing scans remove mass; neither candidate may absorb
        # the almost doubled mass of the complete two-lobe envelope.
        assert 0.85 * one_lobe_mass < peak.area < 1.05 * one_lobe_mass


@pytest.mark.parametrize("add_remote", [False, True], ids=["alone", "remote_strong"])
def test_supported_narrow_candidate_is_available_without_a_remote_seed(
    add_remote: bool,
) -> None:
    rt = np.linspace(0.0, 2.0, 2001)
    height, sigma = 20.0, 0.01
    raw = height * np.exp(-0.5 * ((rt - 1.00) / sigma) ** 2)
    if add_remote:
        raw += 1000.0 * np.exp(-0.5 * ((rt - 1.60) / 0.05) ** 2)
    result = _physical_candidates(rt, raw)
    assert result.status == "OK"
    local = [
        candidate for candidate in result.candidates
        if abs(candidate.selection_apex_rt - 1.0) < 0.03
    ]
    assert len(local) == 1
    peak = local[0].peak
    assert peak.rt == pytest.approx(1.0, abs=0.003)
    assert 0.95 < peak.peak_start < 1.0 < peak.peak_end < 1.05
    analytic_mass = height * sigma * np.sqrt(2.0 * np.pi) * 60.0
    assert 0.85 * analytic_mass < peak.area < 1.01 * analytic_mass


def test_short_apex_dropout_keeps_one_complete_quantitative_candidate() -> None:
    rt = np.linspace(0.0, 5.0, 1001)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 2.27) / 0.40) ** 2)
    raw[452:457] = 0.0
    result = _physical_candidates(rt, raw)
    assert result.status == "OK"
    assert len(result.candidates) == 1
    peak = result.candidates[0].peak
    assert peak.peak_start < 1.2
    assert peak.peak_end > 3.3
    analytic_mass = 1000.0 * 0.40 * np.sqrt(2.0 * np.pi) * 60.0
    assert 0.90 * analytic_mass < peak.area < 1.01 * analytic_mass


@pytest.mark.parametrize("point_count", [101, 301])
def test_whole_trend_has_matching_target_and_discovery_facts(
    point_count: int,
) -> None:
    rt = np.linspace(0.0, 10.0, point_count)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 5.0) / 0.8) ** 2)
    raw *= 1.0 + 0.08 * np.cos(rt * 12.0)
    prepared = prepare_physical_signal(rt, raw)
    answers = [evaluate_physical_peak(
        prepared, selection_subject_id="shared-whole-trend", query_rt_min=5.0,
        selection_policy_version=policy,
    ) for policy in (PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY)]
    for answer in answers:
        assert answer.state == "available", answer.reason
        assert answer.facts is not None
        assert answer.facts.left_rt_min <= 3.4
        assert answer.facts.right_rt_min >= 6.6
        observed_area = float(np.trapezoid(raw, rt)) * 60
        assert answer.facts.area_counts_seconds > 0.85 * observed_area
    assert answers[0].integration == answers[1].integration
    assert answers[0].answer.physical_segment_id == (
        answers[1].answer.physical_segment_id
    )





@pytest.mark.parametrize("pattern", ["alternating", "random", "random_amplitude"])
@pytest.mark.parametrize(
    "policy",
    [PHYSICAL_PEAK_POINT_POLICY, PHYSICAL_PEAK_WINDOW_POLICY,
     DISCOVERY_PHYSICAL_PEAK_POINT_POLICY],
)
@pytest.mark.parametrize("points", [101, 301])
def test_sparse_stationary_background_is_not_a_peak(
    pattern: str, policy: str, points: int,
) -> None:
    rt = np.linspace(0.0, 10.0, points)
    rng = np.random.default_rng(492)
    mask = (
        np.arange(len(rt)) % 2 == 1
        if pattern == "alternating"
        else rng.random(len(rt)) < 0.5
    )
    raw = 1000.0 * mask
    if pattern == "random_amplitude":
        raw = np.random.default_rng(493).uniform(100.0, 1000.0, len(rt)) * mask
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw), selection_subject_id="stationary-background",
        query_rt_min=5.0, selection_policy_version=policy,
    )
    assert result.state == "unavailable"
    assert result.facts is None and result.integration is None


@pytest.mark.parametrize("background_fragments", [False, True])
def test_original_jaggedness_does_not_replace_peak_evidence(
    background_fragments: bool,
) -> None:
    rt = np.linspace(0.0, 10.0, 101)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 5.0) / 0.5) ** 2)
    if background_fragments:
        raw[raw < 1.0] = 0.0
        raw[[25, 29, 32, 69, 73]] = 10.0
    else:
        raw[::2] *= 0.1  # Jagged, but original signal remains continuous.
    prepared = prepare_physical_signal(rt, raw)
    result = evaluate_physical_peak(
        prepared, selection_subject_id="supported-original-peak", query_rt_min=5.0,
    )
    assert result.state == "available"
    if not background_fragments:
        assert result.facts is not None
        selected = next(
            segment for segment in result.segment_universe.segments
            if segment.physical_segment_id == result.facts.physical_segment_id
        )
        original = raw[selected.start_index:selected.end_index]
        assert noise_shape_severity(original)[0] == 2
    assert result.integration is not None


@pytest.mark.parametrize("scale,background", [(1.0, 0.0), (1000.0, 100000.0)])
def test_background_fluctuation_cannot_borrow_height_from_its_baseline(
    scale: float, background: float,
) -> None:
    # Scaled acquired background with isolated impulses. The smoothed local
    # maximum has little relief above its valleys, despite its residual height.
    raw = scale * np.asarray([
        170.433, 189.858, 153.087, 137.425, 137.427, 158.876, 146.820,
        229.967, 130.081, 149.159, 174.631, 152.543, 140.415, 135.986,
        138.815, 138.261, 141.191, 211.361, 127.825, 134.766, 106.415,
        120.800, 141.851, 143.503, 120.040, 144.941, 133.412, 232.211,
        161.916,
    ]) + background
    rt = np.arange(len(raw), dtype=float) / 10.0
    prepared = prepare_physical_signal(rt, raw)
    fresh = evaluate_physical_peak(
        prepared, selection_subject_id="background-fluctuation", query_rt_min=1.7,
    )
    assert fresh.state == "unavailable"
    assert fresh.reason == "insufficient_local_peak_contrast"
    assert fresh.facts is None and fresh.integration is None


@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, PHYSICAL_PEAK_WINDOW_POLICY]
)
@pytest.mark.parametrize("background", [1000.0, 100000.0])
def test_constant_background_cannot_supply_acquired_support_to_an_impulse(
    policy: str, background: float,
) -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.full_like(rt, background)
    raw[15] += 1000.0
    fresh = evaluate_physical_peak(
        prepare_physical_signal(rt, raw), selection_subject_id="impulse-on-background",
        query_rt_min=1.5, selection_policy_version=policy,
    )
    assert fresh.state == "unavailable"
    assert fresh.reason == "insufficient_original_xic_support"
    assert fresh.facts is None and fresh.integration is None


@pytest.mark.parametrize("width", [0.1, 0.5, 1.0])
@pytest.mark.parametrize("background,slope", [(0.0, 0.0), (100000.0, 500.0)])
@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY]
)
def test_resolved_peak_on_background_keeps_its_canonical_integration(
    width: float, background: float, slope: float, policy: str,
) -> None:
    rt = np.linspace(0.0, 10.0, 101)
    if slope:
        rt += 0.01 * np.sin(np.arange(len(rt)))  # Uneven acquired RT spacing.
    raw = (
        1000.0 * np.exp(-0.5 * ((rt - 5.0) / width) ** 2)
        + background + slope * rt
    )
    prepared = prepare_physical_signal(rt, raw)
    result = evaluate_physical_peak(
        prepared, selection_subject_id="resolved-peak", query_rt_min=5.0,
        selection_policy_version=policy,
    )
    assert result.state == "available"
    assert result.integration is not None
    assert result.facts is not None


@pytest.mark.parametrize("positive_points", [2, 3])
def test_background_qualification_keeps_a_sparse_isolated_peak(
    positive_points: int,
) -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.zeros_like(rt)
    raw[14 : 14 + positive_points] = [100.0, 120.0, 80.0][:positive_points]
    prepared = prepare_physical_signal(rt, raw)
    old = evaluate_physical_peak(
        prepared, selection_subject_id="sparse-peak", query_rt_min=1.5,
    )
    new = evaluate_physical_peak(
        prepared, selection_subject_id="sparse-peak", query_rt_min=1.5,
        selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    )
    assert old.state == new.state == "available"
    assert new.integration == old.integration
    assert new.segment_universe == old.segment_universe


@pytest.mark.parametrize("background", [False, True])
def test_current_discovery_qualification_persists_exactly(
    background: bool,
) -> None:
    profile = SignalProcessingProfile()
    if background:
        fixture = json.loads((
            Path(__file__).parent / "fixtures/discovery_non_peak_background.json"
        ).read_text())
        rt = np.asarray([float.fromhex(v) for v in fixture["trace"]["rt"]])
        raw = np.asarray([float.fromhex(v) for v in fixture["trace"]["intensity"]])
        query = fixture["seed_rt"][0]
    else:
        rt = np.linspace(0.0, 3.0, 61)
        raw = 1000.0 * np.exp(-0.5 * ((rt - 1.5) / 0.15) ** 2)
        query = 1.5
    prepared = prepare_physical_signal(rt, raw, profile=profile)
    for policy in (PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY):
        answer = evaluate_physical_peak(
            prepared, selection_subject_id="policy-replay", query_rt_min=query,
            selection_policy_version=policy,
        )
        assert answer.state == ("unavailable" if background else "available")
        if background:
            # The whole background contour reaches the acquired edge. Neither
            # workflow may turn one local fluctuation into a measured peak.
            assert answer.reason == "incomplete_peak_context"
        payload = serialize_physical_peak_evaluation(
            rt, raw, profile=profile, evaluation=answer,
        )
        *_, restored = deserialize_physical_peak_evaluation(payload)
        assert restored == answer
        assert serialize_physical_peak_evaluation(
            rt, raw, profile=profile, evaluation=restored,
        ) == payload


def test_background_qualification_rejects_insufficient_edge_contrast() -> None:
    rt = np.linspace(0.0, 3.0, 31)
    # A shallow plateau whose apex is less than 1.2x above edge baseline in both views
    raw = 100.0 + np.array([
        0.0, 0.0, 5.0, 8.0, 10.0, 8.0, 5.0, 0.0, 0.0, 0.0,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    ])
    baseline = np.full_like(raw, 50.0)
    assert is_background_like_peak(
        rt, raw, baseline, apex_index=4, raw_noise_mad=1.0,
    )
    # A resolved peak with peak-to-edge >= 1.2 in both views remains available
    prominent_raw = 100.0 + 100.0 * np.exp(-0.5 * ((rt - 0.4) / 0.15) ** 2)
    assert not is_background_like_peak(
        rt, prominent_raw, baseline, apex_index=4, raw_noise_mad=1.0,
    )


def test_background_qualification_rejects_unresolved_shoulder_candidate() -> None:
    assert not is_unresolved_shoulder_peak(
        segment_class="separate_peak",
        apex_height=100.0,
        left_valley=40.0,
        right_valley=30.0,
    )
    assert is_unresolved_shoulder_peak(
        segment_class="shoulder_candidate",
        apex_height=100.0,
        left_valley=30.0,
        right_valley=40.0,
    )
    assert not is_unresolved_shoulder_peak(
        segment_class="shoulder_candidate",
        apex_height=100.0,
        left_valley=10.0,
        right_valley=50.0,
    )
    assert not is_unresolved_shoulder_peak(
        segment_class="shoulder_candidate",
        apex_height=100.0,
        left_valley=30.0,
        right_valley=20.0,
    )


def test_is_provisional_peak_shape() -> None:
    # Silent baseline (noise_mad == 0.0): genuine sparse peaks like C010 / C060
    rt_sparse = np.array([8.0, 8.1, 8.2])
    raw_sparse = np.array([0.0, 500.0, 0.0])
    assert not is_provisional_peak_shape(
        rt_sparse, raw_sparse, apex_index=1, raw_noise_mad=0.0
    )

    # High contrast and high SNR peak
    rt_clear = np.linspace(8.0, 8.6, 7)
    raw_clear = np.array([100.0, 150.0, 400.0, 1000.0, 420.0, 160.0, 95.0])
    assert not is_provisional_peak_shape(
        rt_clear, raw_clear, apex_index=3, raw_noise_mad=30.0
    )

    # Low contrast (< 1.5) and low SNR (< 5.0) in noisy baseline -> provisional
    rt_noisy = np.linspace(8.0, 8.6, 7)
    raw_noisy = np.array([100.0, 110.0, 120.0, 130.0, 115.0, 105.0, 100.0])
    # contrast = 130 / 100 = 1.3 (< 1.5), snr = (130 - 100) / 10 = 3.0 (< 5.0)
    assert is_provisional_peak_shape(
        rt_noisy, raw_noisy, apex_index=3, raw_noise_mad=10.0
    )


def test_has_deficient_monotone_run() -> None:
    # Sparse peak (< 6 points): exempted to preserve authentic sparse peaks
    short_raw = np.array([0.0, 500.0, 0.0])
    assert not has_deficient_monotone_run(short_raw, apex_index=1)

    # Authentic chromatographic peak: >= 2 consecutive monotonic steps on flank
    good_peak = np.array([10.0, 50.0, 200.0, 800.0, 300.0, 50.0, 10.0])
    # Up flank: 10 -> 50 -> 200 -> 800 (run of 3)
    assert not has_deficient_monotone_run(good_peak, apex_index=3)

    # High-frequency alternating chatter (jitter): max consecutive run <= 1
    # 7 points alternating back and forth around apex (index 4, 110.0):
    # Up: 100 -> 95 -> 105 -> 98 -> 110 (max up run = 1)
    # Down: 110 -> 102 -> 108 (max down run = 1)
    jitter = np.array([100.0, 95.0, 105.0, 98.0, 110.0, 102.0, 108.0])
    assert has_deficient_monotone_run(jitter, apex_index=4)


def test_has_unresolved_valley_floors() -> None:
    # Clean resolved peak with valley return to 0: passes
    assert not has_unresolved_valley_floors(
        apex_height=1000.0, left_valley=0.0, right_valley=0.0
    )

    # Asymmetric peak with one elevated flank (shoulder) but one clean baseline
    # return: passes
    assert not has_unresolved_valley_floors(
        apex_height=1000.0, left_valley=500.0, right_valley=50.0
    )

    # Both flanks elevated above 20% (unresolved saddle / floating feature): rejected
    assert has_unresolved_valley_floors(
        apex_height=1000.0, left_valley=250.0, right_valley=210.0
    )

    # Edge cases: zero or negative apex height rejected
    assert has_unresolved_valley_floors(
        apex_height=0.0, left_valley=0.0, right_valley=0.0
    )




@pytest.mark.parametrize(
    "policy,reason",
    [
        (PHYSICAL_PEAK_POINT_POLICY, "insufficient_original_xic_support"),
        (PHYSICAL_PEAK_WINDOW_POLICY, "ambiguous_segment_candidates"),
    ],
)
def test_other_peak_support_cannot_backfill_a_singleton_or_resolve_ambiguity(
    policy: str, reason: str
) -> None:
    rt = np.linspace(0.0, 8.0, 81)
    raw = np.zeros_like(rt)
    raw[20] = 1000.0
    raw[58:63] = [100.0, 600.0, 1000.0, 600.0, 100.0]
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        selection_subject_id="two-local-peaks",
        query_rt_min=2.0,
        selection_policy_version=policy,
    )
    assert result.state == "unavailable"
    assert result.reason == reason
    assert result.facts is None
    assert result.integration is None


def test_low_contrast_ripple_keeps_one_wide_gaussian_envelope_measurable() -> None:
    rt = np.linspace(0.0, 6.0, 601)
    center = 3.0
    sigma = 0.8
    amplitude = 12000.0
    rng = np.random.default_rng(479)
    analytic_envelope = amplitude * np.exp(-0.5 * ((rt - center) / sigma) ** 2)
    # Bounded raw measurement noise is at most 1.25% of the analytic apex.
    # Gaussian15 smoothing leaves low-contrast ripple inside one broad envelope.
    raw = 300.0 + analytic_envelope + rng.uniform(-150.0, 150.0, rt.size)

    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        selection_subject_id="wide-gaussian-with-ripple",
        query_rt_min=center,
    )

    assert result.state == "available", result.reason
    assert result.facts is not None
    assert result.facts.apex_rt_min == pytest.approx(center, abs=0.03)
    assert result.facts.left_rt_min <= center - 1.5 * sigma
    assert result.facts.right_rt_min >= center + 1.5 * sigma


def test_analytic_doublet_keeps_the_true_valley_as_two_peak_boundaries() -> None:
    rt = np.linspace(0.0, 6.0, 601)
    left_center, right_center = 2.5, 3.5
    sigma = 0.18
    analytic_doublet = (
        9000.0 * np.exp(-0.5 * ((rt - left_center) / sigma) ** 2)
        + 8500.0 * np.exp(-0.5 * ((rt - right_center) / sigma) ** 2)
    )
    rng = np.random.default_rng(2479)
    raw = 300.0 + analytic_doublet + rng.uniform(-25.0, 25.0, rt.size)
    prepared = prepare_physical_signal(rt, raw)
    valley_rt = (left_center + right_center) / 2.0

    left = evaluate_physical_peak(
        prepared, selection_subject_id="resolved-doublet-left", query_rt_min=left_center
    )
    right = evaluate_physical_peak(
        prepared,
        selection_subject_id="resolved-doublet-right",
        query_rt_min=right_center,
    )

    assert left.state == "available" and left.facts is not None
    assert right.state == "available" and right.facts is not None
    assert left.facts.apex_rt_min == pytest.approx(left_center, abs=0.03)
    assert right.facts.apex_rt_min == pytest.approx(right_center, abs=0.03)
    # Unequal amplitudes and measurement noise move the sampled valley away
    # from the midpoint; both boundaries must stay within one acquired scan.
    scan_spacing = float(np.max(np.diff(rt)))
    assert left.facts.right_rt_min == pytest.approx(valley_rt, abs=scan_spacing)
    assert right.facts.left_rt_min == pytest.approx(valley_rt, abs=scan_spacing)
    assert left.facts.physical_segment_id != right.facts.physical_segment_id


@pytest.mark.parametrize("query_rt", [1.525, 2.475, 1.542])
def test_visible_peak_tail_selects_complete_baseline_bounded_peak(
    query_rt: float,
) -> None:
    rt = np.linspace(0.0, 4.0, 161)
    raw = 100.0 + 10000.0 * np.exp(-0.5 * ((rt - 2.0) / 0.16) ** 2)
    prepared = prepare_physical_signal(rt, raw)

    result = evaluate_physical_peak(
        prepared, selection_subject_id="isolated-peak-tail", query_rt_min=query_rt
    )

    assert result.state == "available"
    assert result.facts is not None
    assert result.facts.apex_rt_min == pytest.approx(2.0)
    assert result.facts.left_rt_min < query_rt < result.facts.right_rt_min
    assert result.facts.area_counts_seconds > 0.0
    baseline = evaluate_physical_peak(
        prepared, selection_subject_id="outside-peak", query_rt_min=1.3
    )
    assert baseline.state == "unavailable"
    assert baseline.facts is None


@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_WINDOW_POLICY, PHYSICAL_PEAK_GROUP_WINDOW_POLICY]
)
@pytest.mark.parametrize(
    "centers,query_rt,expected",
    [
        ((2.0,), 1.0, "available"),
        ((1.2, 2.8), 2.0, "ambiguous"),
        ((2.0,), 4.5, "unavailable"),
        ((), 2.0, "unavailable"),
    ],
)
def test_group_window_selects_only_one_complete_peak(
    centers, query_rt, expected, policy
) -> None:
    rt = np.linspace(0.0, 4.0, 161)
    raw = np.full_like(rt, 100.0)
    for center in centers:
        raw += 10000.0 * np.exp(-0.5 * ((rt - center) / 0.12) ** 2)
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        selection_subject_id="bounded-group-window",
        query_rt_min=query_rt,
        request_context_rt_start_min=0.0,
        request_context_rt_end_min=4.0,
        selection_policy_version=policy,
    )
    assert result.state == ("unavailable" if expected == "ambiguous" else expected)
    if expected == "ambiguous":
        assert result.reason == "ambiguous_segment_candidates"
    if expected == "available":
        assert result.facts is not None
        assert result.facts.apex_rt_min == pytest.approx(2.0)
        assert result.facts.left_rt_min > query_rt
    else:
        assert result.facts is None
        assert result.integration is None


def _hmdc_tail_noise_trace() -> tuple[np.ndarray, np.ndarray]:
    # Observed d3-5-hmdC MS1 XIC (TumorBC2264, intensity / 100) in a group
    # window. Sparse tail noise keeps the smoothed signal above zero after the
    # peak, so an exact zero return never closes the right boundary.
    rt = np.asarray(
        [
            7.98338, 8.02491, 8.06641, 8.10795, 8.14947, 8.19102, 8.23267,
            8.27419, 8.31562, 8.35692, 8.39808, 8.43923, 8.4806, 8.52364,
            8.56349, 8.60512, 8.64677, 8.6881, 8.7269, 8.76833, 8.80978,
            8.8511, 8.89234, 8.93348, 8.97471, 9.01593, 9.0571, 9.09821,
            9.13928, 9.17004, 9.20908, 9.23472, 9.26752, 9.29309, 9.32694,
            9.35754, 9.38397, 9.4186, 9.44303, 9.48092, 9.50344, 9.53729,
            9.56287, 9.59561, 9.62834, 9.65282, 9.67837, 9.70378, 9.72409,
            9.74741, 9.76464, 9.79315, 9.81452, 9.84204, 9.86449, 9.89827,
            9.92376, 9.9503, 9.96861,
        ]
    )
    raw = np.asarray(
        [
            301.7, 0.0, 189.3, 174.5, 0.0, 105.4, 0.0, 0.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 0.0, 1547.0, 0.0, 0.0, 0.0, 0.0, 0.0, 164.1, 713.0,
            2952.1, 6608.3, 10918.3, 11403.2, 7619.3, 3442.4, 1510.6, 417.8,
            159.1, 0.0, 98.9, 0.0, 0.0, 0.0, 0.0, 0.0, 153.1, 115.9, 0.0, 0.0,
            0.0, 131.6, 0.0, 0.0, 221.3, 0.0, 0.0, 0.0, 98.8, 0.0, 0.0, 159.5,
            176.4, 0.0, 113.7, 0.0,
        ]
    )
    return rt, raw


@pytest.mark.parametrize(
    "policy,expected_reason",
    [
        (PHYSICAL_PEAK_WINDOW_POLICY, "ambiguous_segment_candidates"),
        (PHYSICAL_PEAK_GROUP_WINDOW_POLICY, ""),
    ],
)
def test_shared_owner_closes_observed_tail_at_measurement_noise(
    policy: str, expected_reason: str
) -> None:
    # The edge candidate remains part of a whole-context uniqueness question;
    # the group prior selects the complete central peak without hiding the edge.
    rt, raw = _hmdc_tail_noise_trace()
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        selection_subject_id="hmdc-tail-noise",
        query_rt_min=8.96988,
        request_context_rt_start_min=7.96988,
        request_context_rt_end_min=9.96988,
        selection_policy_version=policy,
    )
    assert result.reason == expected_reason
    if not expected_reason:
        assert result.state == "available" and result.facts is not None
        assert result.facts.apex_rt_min == pytest.approx(9.04, abs=0.05)
        assert rt[0] < result.facts.left_rt_min < 8.96988
        assert result.facts.right_rt_min < rt[-1]


@pytest.mark.parametrize(
    "policy,expected_reason",
    [
        (PHYSICAL_PEAK_WINDOW_POLICY, "ambiguous_segment_candidates"),
        (PHYSICAL_PEAK_GROUP_WINDOW_POLICY, ""),
    ],
)
def test_group_window_prefers_the_one_peak_containing_the_group_rt(
    policy: str, expected_reason: str
) -> None:
    rt = np.linspace(0.0, 4.0, 161)
    raw = 10000.0 * np.exp(-0.5 * ((rt - 2.0) / 0.12) ** 2) + 700.0 * np.exp(
        -0.5 * ((rt - 1.0) / 0.12) ** 2
    )
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        selection_subject_id="minor-neighbour",
        query_rt_min=2.0,
        request_context_rt_start_min=0.0,
        request_context_rt_end_min=4.0,
        selection_policy_version=policy,
    )
    assert result.reason == expected_reason
    if not expected_reason:
        assert result.facts is not None
        assert result.facts.apex_rt_min == pytest.approx(2.0)


@pytest.mark.parametrize("has_signal", [True, False])
def test_no_selection_policy_roundtrip_preserves_physical_context(
    has_signal: bool,
) -> None:
    profile = SignalProcessingProfile(window_points=3)
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.full(31, 10.0)
    if has_signal:
        raw[7:10] += [30.0, 100.0, 30.0]
    else:
        raw = np.zeros(len(rt))
    prepared = prepare_physical_signal(rt, raw, profile=profile)
    ordinary = evaluate_physical_peak(
        prepared, selection_subject_id="selection-state", query_rt_min=0.8
    )
    evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id="selection-state",
        query_rt_min=0.8,
        selection_policy_version=PHYSICAL_PEAK_NO_SELECTION_POLICY,
    )

    assert evaluation.prepared == ordinary.prepared
    assert evaluation.segment_universe == ordinary.segment_universe
    assert evaluation.question.query_rt_min == 0.8
    assert evaluation.question.segment_ids == ordinary.question.segment_ids
    assert evaluation.question.admissible_segment_ids == ()
    assert evaluation.state == "unavailable"
    assert evaluation.facts is None
    assert evaluation.integration is None
    if has_signal:
        assert ordinary.state == "available"
        assert evaluation.segment_universe.segments
        assert evaluation.reason == "no_selection"
    else:
        assert evaluation.reason == ordinary.reason == "no_signal_above_baseline"

    payload = serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=evaluation
    )
    retained_rt, retained_raw, retained_profile, retained = (
        deserialize_physical_peak_evaluation(payload, expected_profile=profile)
    )
    np.testing.assert_array_equal(retained_rt, rt)
    np.testing.assert_array_equal(retained_raw, raw)
    assert retained_profile == profile
    assert retained == evaluation


@pytest.mark.parametrize("terminal", ["available", "unavailable", "failed"])
def test_physical_evaluation_persistence_rehydrates_terminal_owner_state(
    terminal: str,
) -> None:
    profile = SignalProcessingProfile(window_points=3)
    if terminal == "available":
        rt = np.linspace(0.0, 3.0, 31)
        raw = np.full(31, 10.0)
        raw[7:10] += [30.0, 100.0, 30.0]
        query_rt = 0.8
        context = (0.0, 3.0)
    elif terminal == "unavailable":
        rt = np.asarray([1.0])
        raw = np.asarray([10.0])
        query_rt = 1.0
        context = (1.0, 1.0)
    else:
        rt = np.asarray([float("nan"), 1.0, 2.0])
        raw = np.asarray([1.0, 2.0, 1.0])
        query_rt = 1.5
        context = (1.0, 2.0)
    prepared = prepare_physical_signal(rt, raw, profile=profile)
    evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id=f"persisted-{terminal}",
        query_rt_min=query_rt,
        request_context_rt_start_min=context[0],
        request_context_rt_end_min=context[1],
    )

    payload = serialize_physical_peak_evaluation(
        rt,
        raw,
        profile=profile,
        evaluation=evaluation,
    )
    _, _, rehydrated_profile, rehydrated = deserialize_physical_peak_evaluation(
        payload,
        expected_profile=profile,
    )

    assert rehydrated_profile == profile
    assert rehydrated == evaluation
    assert rehydrated.state == terminal
    with pytest.raises(ValueError, match="not canonical"):
        deserialize_physical_peak_evaluation(payload + b" ")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["evaluation"]["answer"].__setitem__(
            "physical_answer_id", "physical-answer:" + "0" * 64
        ),
        lambda value: value["trace"]["rt"].__setitem__(0, "-0x0.0p+0"),
        lambda value: value["trace"]["rt"].__setitem__(0, "0x0p+0"),
        lambda value: value["trace"].__setitem__("trace_content_id", "wrong-trace"),
        lambda value: value["profile"].__setitem__("profile_hash", "0" * 64),
        lambda value: value["profile"]["content"].__setitem__("window_points", 3.0),
        lambda value: value.__setitem__(
            "profile", dict(reversed(tuple(value["profile"].items())))
        ),
        lambda value: value.__setitem__(
            "trace", dict(reversed(tuple(value["trace"].items())))
        ),
        lambda value: value.__setitem__(
            "evaluation", dict(reversed(tuple(value["evaluation"].items())))
        ),
    ],
)
def test_physical_evaluation_persistence_rejects_resealed_owner_tamper(mutate) -> None:
    profile = SignalProcessingProfile(window_points=3)
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.full(31, 10.0)
    raw[7:10] += [30.0, 100.0, 30.0]
    evaluation = evaluate_physical_peak(
        prepare_physical_signal(rt, raw, profile=profile),
        selection_subject_id="persisted-tamper",
        query_rt_min=0.8,
    )
    value = json.loads(
        serialize_physical_peak_evaluation(
            rt,
            raw,
            profile=profile,
            evaluation=evaluation,
        )
    )
    mutate(value)

    with pytest.raises(ValueError):
        deserialize_physical_peak_evaluation(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        )


def test_sidecar_write_is_pure_and_external_read_rehydrates_once_per_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.peak_detection import physical_peak_codec as owner

    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    calls = {"prepare": 0, "evaluate": 0}
    real_prepare = owner.prepare_physical_signal
    real_evaluate = owner.evaluate_physical_peak

    def counted_prepare(*args, **kwargs):
        calls["prepare"] += 1
        return real_prepare(*args, **kwargs)

    def counted_evaluate(*args, **kwargs):
        calls["evaluate"] += 1
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(owner, "prepare_physical_signal", counted_prepare)
    monkeypatch.setattr(owner, "evaluate_physical_peak", counted_evaluate)
    path = tmp_path / "facts.json"

    write_discovery_physical_fact_sidecar(
        path,
        run_spec=spec,
        acquisition_input=acquisition_input,
        work_manifest=manifest,
        records=records,
    )
    assert calls == {"prepare": 0, "evaluate": 0}

    restored = read_discovery_physical_fact_sidecar(
        path,
        expected_run_spec=spec,
        expected_acquisition_input=acquisition_input,
        expected_work_manifest=manifest,
    )
    assert len(restored) == len(records)
    assert calls == {"prepare": len(records), "evaluate": len(records)}


@pytest.mark.parametrize("point_count", [1501, 3001])
def test_middle_overlapping_peak_keeps_its_own_reference_component(
    point_count: int,
) -> None:
    rt = np.linspace(6.0, 16.0, point_count)
    raw = 100.0 + sum(
        height * np.exp(-0.5 * ((rt - center) / width) ** 2)
        for height, center, width in (
            (6000.0, 8.8, 0.48),
            (10000.0, 10.9, 0.45),
            (15000.0, 12.5, 0.50),
        )
    )
    prepared = prepare_physical_signal(rt, raw)
    answers = [
        evaluate_physical_peak(
            prepared, selection_subject_id="middle-overlapping-component",
            query_rt_min=10.9, selection_policy_version=policy,
        )
        for policy in (PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY)
    ]
    for answer in answers:
        assert answer.state == "available", answer.reason
        assert answer.facts is not None
        assert answer.facts.apex_rt_min == pytest.approx(10.9, abs=0.15)
        # The middle component remains measurable, without absorbing either
        # stronger neighbor or claiming deconvolved compound-specific area.
        assert 9.4 < answer.facts.left_rt_min < 10.3
        assert 11.3 < answer.facts.right_rt_min < 12.0
        assert answer.requires_overlap_review
    assert answers[0].integration == answers[1].integration
    assert answers[0].answer.physical_segment_id == (
        answers[1].answer.physical_segment_id
    )


@pytest.mark.parametrize("point_count", [301, 1801])
def test_brief_apex_dropout_does_not_create_two_quantitative_components(
    point_count: int,
) -> None:
    rt = np.linspace(0.0, 10.0, point_count)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 5.0) / 0.8) ** 2)
    middle = point_count // 2
    raw[middle - 1:middle + 2] = 0.0
    prepared = prepare_physical_signal(rt, raw)
    answer = evaluate_physical_peak(
        prepared, selection_subject_id="brief-observed-dropout", query_rt_min=5.0,
    )
    assert answer.state == "available"
    assert answer.facts is not None
    assert len(answer.segment_universe.segments) == 1
    assert answer.facts.left_rt_min < 3.4
    assert answer.facts.right_rt_min > 6.6
    # Baseline correction changes raw mass. Protect the complete observed
    # canonical mass, which a fragmented selection would lose about half of.
    assert prepared.prepared is not None
    observed_mass = float(np.trapezoid(prepared.prepared.positive_signal, rt)) * 60.0
    assert answer.facts.area_counts_seconds == pytest.approx(observed_mass, rel=0.001)
    assert prepared.prepared.raw[middle - 1:middle + 2] == (0.0, 0.0, 0.0)
