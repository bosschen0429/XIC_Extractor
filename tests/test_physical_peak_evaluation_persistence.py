from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from tests.discovery_physical_fact_persistence_fixtures import _population, _spec
from tests.physical_peak_evaluation_persistence_fixtures import (
    _context_sensitive_trace,
    _overlapping_triplet_trace,
    _short_peak_with_separate_noise_hits,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.physical_fact_persistence import (
    read_discovery_physical_fact_sidecar,
    write_discovery_physical_fact_sidecar,
)
from xic_extractor.peak_detection.background_shape import (
    is_provisional_peak_shape,
)
from xic_extractor.peak_detection.models import PeakCandidatesResult
from xic_extractor.peak_detection.physical_peak_candidate_adapter import (
    physical_candidates_result,
)
from xic_extractor.peak_detection.physical_peak_codec import (
    physical_evaluation_from_payload,
    physical_evaluation_payload,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    enumerate_physical_segment_universe,
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import (
    DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    PHYSICAL_PEAK_BACKFILL_POLICY,
    PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
    PHYSICAL_PEAK_NO_SELECTION_POLICY,
    PHYSICAL_PEAK_POINT_POLICY,
    PHYSICAL_PEAK_REFERENCE_POLICY,
    PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY,
    PHYSICAL_PEAK_WINDOW_POLICY,
    PhysicalPeakEvaluation,
)
from xic_extractor.peak_detection.scoring_metrics import noise_shape_severity


def _stored(evaluation: PhysicalPeakEvaluation) -> PhysicalPeakEvaluation:
    """Decode an evaluation from its persisted sidecar form, as a reader does."""
    prepared = evaluation.prepared
    return physical_evaluation_from_payload(
        json.loads(json.dumps(physical_evaluation_payload(evaluation))),
        profile=prepared.profile,
        rt=tuple(float(value) for value in prepared.rt),
        raw=tuple(float(value) for value in prepared.raw),
    )


@pytest.mark.parametrize(
    "height,expected", [(5.0, "unavailable"), (200.0, "available")]
)
def test_background_contrast_rejects_weak_peak_for_capture_and_backfill(
    height: float,
    expected: str,
) -> None:
    rt = np.linspace(0.0, 2.0, 401)
    raw = 100.0 + np.random.default_rng(19).normal(0.0, 10.0, len(rt))
    raw += height * np.exp(-0.5 * ((rt - 1.0) / 0.08) ** 2)
    prepared = prepare_physical_signal(rt, raw)
    question = dict(
        query_rt_min=1.0,
        search_rt_window=(0.85, 1.15),
    )
    detected = evaluate_physical_peak(
        prepared,
        **question,
        selection_policy_version=PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
    )
    assert detected.state == expected
    reference = evaluate_physical_peak(
        prepared,
        query_rt_min=1.0,
        selection_policy_version=PHYSICAL_PEAK_REFERENCE_POLICY,
    )
    # Targeted reference availability records a measurable body, not a
    # Discovery/background detection claim. Weak contrast remains uncertainty
    # for contextual identity and review; it does not erase the measurement.
    assert reference.state == "available"
    assert reference.integration is not None
    backfill = evaluate_physical_peak(
        prepared,
        **question,
        selection_policy_version=PHYSICAL_PEAK_BACKFILL_POLICY,
    )
    assert backfill.state == expected
    if expected == "unavailable":
        assert backfill.reason == "insufficient_backfill_peak_contrast"
        assert backfill.facts is None
    else:
        assert backfill.facts.area_counts_seconds == detected.facts.area_counts_seconds
    assert _stored(backfill) == backfill


@pytest.mark.parametrize("query_rt", [0.8, 1.2])
def test_backfill_recovers_the_complete_peak_containing_the_query(
    query_rt: float,
) -> None:
    rt = np.linspace(0.0, 3.0, 601)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 1.0) / 0.12) ** 2) + 9000.0 * np.exp(
        -0.5 * ((rt - 2.1) / 0.06) ** 2
    )
    prepared = prepare_physical_signal(rt, raw)
    window = (query_rt - 0.05, query_rt + 0.05)
    question = dict(
        query_rt_min=query_rt,
        search_rt_window=window,
    )
    ordinary = evaluate_physical_peak(
        prepared,
        **question,
        selection_policy_version=PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
    )
    assert ordinary.state == "unavailable" and ordinary.facts is None
    backfill = evaluate_physical_peak(
        prepared,
        **question,
        selection_policy_version=PHYSICAL_PEAK_BACKFILL_POLICY,
    )
    assert backfill.state == "available" and backfill.facts is not None
    assert backfill.question.search_rt_window == window
    assert backfill.facts.apex_rt_min == pytest.approx(1.0)
    assert not window[0] <= backfill.facts.apex_rt_min <= window[1]
    assert backfill.facts.left_rt_min < query_rt < backfill.facts.right_rt_min
    centered = evaluate_physical_peak(
        prepared,
        query_rt_min=1.0,
        search_rt_window=(0.95, 1.05),
        selection_policy_version=PHYSICAL_PEAK_BACKFILL_POLICY,
    )
    assert centered.facts is not None
    assert backfill.answer.selected_segment == centered.answer.selected_segment
    assert backfill.facts.area_counts_seconds == centered.facts.area_counts_seconds
    assert backfill.facts.left_rt_min == centered.facts.left_rt_min
    assert backfill.facts.right_rt_min == centered.facts.right_rt_min
    assert _stored(backfill) == backfill


def test_backfill_window_does_not_take_a_neighbor_outside_the_expected_rt() -> None:
    rt = np.linspace(0.0, 2.0, 401)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 0.4) / 0.08) ** 2)
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=1.0,
        search_rt_window=(0.8, 1.2),
        selection_policy_version=PHYSICAL_PEAK_BACKFILL_POLICY,
    )
    assert result.state == "unavailable"
    assert result.reason == "no_peak_in_sample_local_window"
    assert result.facts is None
    assert result.segment_universe.segments
    assert result.segment_universe.segments[0].apex_rt_min == pytest.approx(0.4)


def test_backfill_window_overlap_without_query_containment_is_not_enough() -> None:
    rt = np.linspace(0.0, 3.0, 601)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 1.0) / 0.12) ** 2)
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=1.5,
        search_rt_window=(1.2, 1.7),
        selection_policy_version=PHYSICAL_PEAK_BACKFILL_POLICY,
    )
    (segment,) = result.segment_universe.segments
    assert 1.2 < segment.right_rt_min < 1.5
    assert result.reason == "no_peak_in_sample_local_window"
    assert result.state == "unavailable" and result.facts is None


def test_backfill_does_not_choose_between_two_query_containing_segments() -> None:
    rt = np.linspace(0.0, 3.0, 601)
    raw = sum(
        1000.0 * np.exp(-0.5 * ((rt - center) / 0.12) ** 2) for center in (1.0, 1.5)
    )
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=1.25,
        search_rt_window=(1.2, 1.3),
        selection_policy_version=PHYSICAL_PEAK_BACKFILL_POLICY,
    )
    assert len(result.question.admissible_segments) == 2
    assert result.reason == "ambiguous_segment_candidates"
    assert result.state == "unavailable" and result.facts is None


def test_backfill_query_containment_does_not_bypass_background_qualification() -> None:
    rt = np.linspace(0.0, 2.0, 401)
    raw = 100.0 + np.random.default_rng(19).normal(0.0, 10.0, len(rt))
    raw += 5.0 * np.exp(-0.5 * ((rt - 1.0) / 0.08) ** 2)
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=0.95,
        search_rt_window=(0.93, 0.97),
        selection_policy_version=PHYSICAL_PEAK_BACKFILL_POLICY,
    )
    assert len(result.question.admissible_segments) == 1
    assert result.reason == "insufficient_backfill_peak_contrast"
    assert result.state == "unavailable" and result.facts is None


def test_backfill_query_containment_does_not_accept_a_clipped_peak() -> None:
    rt = np.linspace(0.0, 2.0, 401)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 0.1) / 0.12) ** 2)
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=0.3,
        search_rt_window=(0.28, 0.32),
        selection_policy_version=PHYSICAL_PEAK_BACKFILL_POLICY,
    )
    assert len(result.question.admissible_segments) == 1
    assert result.reason == "incomplete_peak_context"
    assert result.state == "unavailable" and result.facts is None


def test_backfill_retains_insufficient_support_reason_for_two_isolated_scans() -> None:
    rt = np.linspace(0.0, 3.0, 61)
    raw = np.zeros_like(rt)
    raw[[29, 31]] = (1000.0, 1100.0)
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=1.5,
        search_rt_window=(1.2, 1.8),
        selection_policy_version=PHYSICAL_PEAK_BACKFILL_POLICY,
    )
    assert result.state == "unavailable"
    assert result.reason == "insufficient_original_xic_continuity"
    assert result.facts is None
    assert result.segment_universe.segments


def test_unreliable_location_evaluates_unique_acquired_peak() -> None:
    rt = np.linspace(0.0, 2.0, 401)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 0.4) / 0.08) ** 2)
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=1.0,
        selection_policy_version=PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY,
    )
    assert result.state == "available"
    assert result.facts is not None
    assert result.facts.apex_rt_min == pytest.approx(0.4)
    restored = _stored(result)
    assert restored == result


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


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY]
)
def test_supported_short_peak_survives_noise_hits_and_persistence(
    reverse: bool,
    policy: str,
) -> None:
    rt, raw = _short_peak_with_separate_noise_hits()
    if reverse:
        raw = raw[::-1].copy()
    apex_index = int(np.argmax(raw))
    prepared = prepare_physical_signal(rt, raw)
    result = evaluate_physical_peak(
        prepared,
        query_rt_min=rt[apex_index],
        selection_policy_version=policy,
    )
    assert result.state == "available"
    assert result.facts is not None and result.integration is not None
    assert result.facts.apex_rt_min == pytest.approx(rt[apex_index])
    candidates = physical_candidates_result(prepared, result.segment_universe)
    assert any(c.peak.rt == result.facts.apex_rt_min for c in candidates.candidates)
    assert "near_detection_limit" not in result.integration.boundary_sources
    restored = _stored(result)
    assert restored == result


def test_exterior_context_change_keeps_observed_near_baseline_peak_available() -> None:
    rt, raw = _context_sensitive_trace()
    full_signal = prepare_physical_signal(rt, raw)
    cropped_signal = prepare_physical_signal(rt[:-1], raw[:-1])
    full = evaluate_physical_peak(full_signal, query_rt_min=rt[23])
    assert full.state == "available" and full.facts is not None
    selected = full.answer.selected_segment
    assert selected is not None
    assert selected.end_index + 7 <= len(rt) - 1
    assert full_signal.prepared is not None and cropped_signal.prepared is not None
    assert (
        full_signal.prepared.smoothed[selected.start_index : selected.end_index]
        == cropped_signal.prepared.smoothed[selected.start_index : selected.end_index]
    )

    cropped = evaluate_physical_peak(cropped_signal, query_rt_min=rt[23])
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
    result = evaluate_physical_peak(prepared, query_rt_min=0.9)

    assert result.state == "unavailable"
    assert result.reason == "incomplete_peak_context"
    assert result.facts is None
    assert result.integration is None


@pytest.mark.parametrize("target_present", [True, False])
def test_search_window_does_not_admit_context_only_peak(target_present: bool) -> None:
    rt = np.linspace(0.0, 6.0, 301)
    raw = 5000.0 * np.exp(-0.5 * ((rt - 4.7) / 0.16) ** 2)
    if target_present:
        raw += 1000.0 * np.exp(-0.5 * ((rt - 2.3) / 0.22) ** 2)
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=1.3,
        selection_policy_version=PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
        search_rt_window=(1.0, 3.0),
    )
    if target_present:
        assert result.state == "available"
        assert result.facts is not None
        assert result.facts.apex_rt_min == pytest.approx(2.3, abs=0.04)
    else:
        assert result.state == "unavailable"
        assert result.facts is None and result.integration is None


def test_search_window_keeps_complete_peak_and_round_trips() -> None:
    rt = np.linspace(0.0, 6.0, 301)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 3.0) / 0.4) ** 2)
    prepared = prepare_physical_signal(rt, raw)
    result = evaluate_physical_peak(
        prepared,
        query_rt_min=3.0,
        selection_policy_version=PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
        search_rt_window=(2.8, 3.2),
    )
    assert result.state == "available" and result.facts is not None
    assert result.facts.left_rt_min < 2.8 < 3.2 < result.facts.right_rt_min
    assert result.question.search_rt_window == (2.8, 3.2)
    assert result.question.context_rt_start_min == 0.0
    assert result.question.context_rt_end_min == 6.0
    assert _stored(result) == result


def test_search_window_does_not_resolve_two_eligible_peaks_by_strength() -> None:
    rt = np.linspace(0.0, 6.0, 301)
    raw = sum(
        height * np.exp(-0.5 * ((rt - center) / 0.1) ** 2)
        for center, height in [(2.0, 1000.0), (3.0, 2000.0), (5.0, 9000.0)]
    )
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=1.0,
        selection_policy_version=PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
        search_rt_window=(1.0, 3.5),
    )
    assert len(result.question.admissible_segments) == 2
    assert result.state == "unavailable" and result.facts is None
    assert result.integration is None


@pytest.mark.parametrize(
    "policy",
    [
        PHYSICAL_PEAK_POINT_POLICY,
        PHYSICAL_PEAK_WINDOW_POLICY,
    ],
)
@pytest.mark.parametrize("positive_points", [1, 2])
def test_isolated_one_or_two_scan_pulses_do_not_supply_complete_peak_evidence(
    policy: str, positive_points: int
) -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.zeros_like(rt)
    raw[15 : 15 + positive_points] = 1000.0
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        enumerate_physical_segment_universe,
        physical_peak_support_failures_at_reference,
    )

    prepared = prepare_physical_signal(rt, raw)
    universe = enumerate_physical_segment_universe(prepared)
    assert prepared.prepared is not None
    assert physical_peak_support_failures_at_reference(
        prepared.prepared, universe, 1.5
    ) == (
        "insufficient_original_xic_support"
        if positive_points == 1
        else "insufficient_local_peak_contrast",
    )
    assert (
        physical_peak_support_failures_at_reference(prepared.prepared, universe, 0.0)
        == ()
    )  # No containing segment is not proof of insufficient raw support.
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=1.5,
        selection_policy_version=policy,
    )

    assert result.state == "unavailable"
    assert result.reason == (
        "insufficient_original_xic_support"
        if positive_points == 1
        else "insufficient_local_peak_contrast"
    )
    assert result.answer.selected_segment is None
    assert result.facts is None and result.integration is None


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
        query_rt_min=query_rt,
        selection_policy_version=policy,
    )
    assert fresh.state == "unavailable"
    assert fresh.reason == "insufficient_original_xic_continuity"
    assert fresh.answer.selected_segment is None
    assert fresh.facts is None and fresh.integration is None


@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, PHYSICAL_PEAK_WINDOW_POLICY]
)
def test_adjacent_pair_with_an_isolated_point_still_requires_contrast(
    policy: str,
) -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.zeros_like(rt)
    raw[[14, 15, 17]] = 1000.0
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=1.5,
        selection_policy_version=policy,
    )
    assert result.state == "unavailable"
    assert result.reason == "insufficient_local_peak_contrast"
    assert result.facts is None


@pytest.mark.parametrize(
    "policy,query_rt",
    [
        (PHYSICAL_PEAK_POINT_POLICY, 5.0),
        (PHYSICAL_PEAK_WINDOW_POLICY, 0.5),
    ],
)
def test_heavily_interrupted_peak_without_contrast_persists_unavailable(
    policy: str,
    query_rt: float,
) -> None:
    rt = np.linspace(0.0, 10.0, 101)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 5.0) / 0.8) ** 2)
    raw[::2] = 0.0
    # Known Gaussian signal with acquisition gaps still supplies observed
    # adjacent support at its apex; missing detections do not prove absence.
    raw[50] = 1000.0
    profile = SignalProcessingProfile()
    prepared = prepare_physical_signal(rt, raw, profile=profile)
    fresh = evaluate_physical_peak(
        prepared,
        query_rt_min=query_rt,
        selection_policy_version=policy,
    )
    assert fresh.state == "unavailable"
    assert fresh.reason == "insufficient_local_peak_contrast"
    assert fresh.facts is None and fresh.integration is None
    restored_fresh = _stored(fresh)
    assert restored_fresh == fresh


@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY]
)
@pytest.mark.parametrize(
    "intensity_pattern",
    ["smooth", "irregular", "no_adjacent_hits", "sparse_hits", "brief_continuous_run"],
)
def test_censored_hit_cluster_uses_observed_area_and_persists_warning(
    policy: str,
    intensity_pattern: str,
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
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
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
        query_rt_min=2.0,
        selection_policy_version=(
            DISCOVERY_PHYSICAL_PEAK_POINT_POLICY
            if policy == PHYSICAL_PEAK_POINT_POLICY
            else PHYSICAL_PEAK_POINT_POLICY
        ),
    )
    assert other.state == result.state
    assert other.answer.selected_segment == result.answer.selected_segment
    assert other.integration == result.integration
    restored = _stored(result)
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
        prepared,
        query_rt_min=2.0,
        request_context_rt_start_min=0.5,
        request_context_rt_end_min=3.5,
        selection_policy_version=policy,
    )
    assert result.state == "available" and result.reason == ""
    assert result.facts is not None and result.integration is not None
    selected = result.answer.selected_segment
    assert selected is not None
    # The continuous Gaussian returns to baseline before the sparse tail.
    # Envelope/baseline revisions may change ordinary boundaries, but density
    # must not absorb the sparse tail beginning at 2.42 min.
    assert 1.6 <= selected.left_rt_min < 2.0
    assert 2.0 < selected.right_rt_min < rt[242]
    assert selected.evidence_sources == (
        "morphology_trace",
        "morphology_local_maximum",
        "baseline_return",
        "raw_area_asls",
    )
    assert prepared.prepared is not None
    interval = slice(selected.start_index, selected.end_index)
    ordinary_area = (
        np.trapezoid(
            np.asarray(prepared.prepared.positive_signal)[interval],
            rt[interval],
        )
        * 60.0
    )
    assert result.facts.area_counts_seconds == pytest.approx(ordinary_area)
    assert result.integration.area_raw_counts_seconds == pytest.approx(
        np.trapezoid(raw[interval], rt[interval]) * 60.0
    )
    assert "near_detection_limit" not in result.integration.boundary_sources


@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY]
)
@pytest.mark.parametrize("scan_spacing", [0.01, 0.02, 0.04])
def test_compact_continuous_doublet_keeps_component_areas(
    policy: str,
    scan_spacing: float,
) -> None:
    rt = np.arange(0.0, 6.0 + scan_spacing / 2.0, scan_spacing)
    sigma = 0.06
    components = ((1000.0, 2.2), (600.0, 3.1))
    raw = sum(
        height * np.exp(-0.5 * ((rt - center) / sigma) ** 2)
        for height, center in components
    )
    raw[raw < 15.0] = 0.0
    # A weak isolated hit connects the density windows, but supplies neither
    # a third chromatographic peak nor permission to merge the true doublet.
    raw[int(np.argmin(abs(rt - 2.65)))] = 20.0
    prepared = prepare_physical_signal(rt, raw)
    # Both contexts include complete acquired flanks for the same components.
    local = (rt >= 1.0) & (rt <= 4.5)
    focused_prepared = prepare_physical_signal(rt[local], raw[local])
    selected = []
    for height, center in components:
        result = evaluate_physical_peak(
            prepared,
            query_rt_min=center,
            selection_policy_version=policy,
        )
        assert result.state == "available"
        assert result.facts is not None and result.integration is not None
        facts = result.facts
        assert facts.apex_rt_min == pytest.approx(center, abs=scan_spacing)
        # The analytical tenth-height Gaussian interval retains erf(sqrt(log 10))
        # of its mass; neither smoothing nor the gap hit may add the other peak.
        expected_area = (
            height
            * sigma
            * np.sqrt(2.0 * np.pi)
            * 60.0
            * math.erf(math.sqrt(math.log(10.0)))
        )
        assert facts.area_counts_seconds == pytest.approx(expected_area, rel=0.03)
        assert "near_detection_limit" not in result.integration.boundary_sources
        focused = evaluate_physical_peak(
            focused_prepared,
            query_rt_min=center,
            selection_policy_version=policy,
        )
        assert focused.state == "available" and focused.facts is not None
        assert focused.facts.apex_rt_min == facts.apex_rt_min
        assert focused.facts.area_counts_seconds == pytest.approx(
            expected_area,
            rel=0.03,
        )
        assert _stored(result) == result
        selected.append(facts)
    assert selected[0].right_rt_min <= selected[1].left_rt_min


@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY]
)
@pytest.mark.parametrize("background", [0.0, 20.0])
def test_continuous_peak_with_isolated_dropouts_keeps_morphology_area(
    policy: str,
    background: float,
) -> None:
    rt = np.arange(801, dtype=float) / 100.0
    raw = background + 10000.0 * np.exp(-0.5 * ((rt - 4.0) / 0.35) ** 2)
    raw[raw < 1.0] = 0.0
    # Several isolated missing observations split the binary runs, while long
    # acquired stretches still support the ordinary chromatographic envelope.
    raw[[370, 389, 423, 439]] = 0.0
    prepared = prepare_physical_signal(rt, raw)
    result = evaluate_physical_peak(
        prepared,
        query_rt_min=4.0,
        selection_policy_version=policy,
    )
    assert result.state == "available"
    assert result.facts is not None and result.integration is not None
    assert 3.8 < result.facts.apex_rt_min < 4.2
    assert result.facts.area_source == "trapezoid_counts_seconds_on_canonical_signal"
    assert "near_detection_limit" not in result.integration.boundary_sources
    assert "local_hit_density_enrichment" not in result.integration.boundary_sources
    assert prepared.prepared is not None
    segment = result.answer.selected_segment
    assert segment is not None
    interval = slice(segment.start_index, segment.end_index)
    assert result.facts.area_counts_seconds == pytest.approx(
        np.trapezoid(
            np.asarray(prepared.prepared.positive_signal)[interval], rt[interval]
        )
        * 60.0
    )
    restored = _stored(result)
    assert restored == result


def test_tail_dropout_neither_splits_nor_merges_a_resolved_doublet() -> None:
    rt = np.arange(1201, dtype=float) / 200.0
    raw = 10000.0 * np.exp(-0.5 * ((rt - 2.0) / 0.7) ** 2) + 6000.0 * np.exp(
        -0.5 * ((rt - 4.3) / 0.45) ** 2
    )
    for with_dropout in (False, True):
        acquired = raw.copy()
        if with_dropout:
            acquired[568:573] = 0.0
        result = evaluate_physical_peak(
            prepare_physical_signal(rt, acquired),
            query_rt_min=2.0,
        )
        assert result.state == "available" and result.facts is not None
        # Zero scans are censored: the notch is no valley and cannot hide the
        # acquired valley that separates the pair.
        assert len(result.segment_universe.segments) == 2
        assert result.facts.apex_rt_min < 2.2
        assert result.facts.right_rt_min < 3.5


def _physical_candidates(rt: np.ndarray, raw: np.ndarray) -> PeakCandidatesResult:
    prepared = prepare_physical_signal(rt, raw)
    universe = enumerate_physical_segment_universe(
        prepared,
        request_context_rt_start_min=float(rt[0]),
        request_context_rt_end_min=float(rt[-1]),
    )
    return physical_candidates_result(prepared, universe)


@pytest.mark.parametrize("dropout", [False, True], ids=["intact", "three_dropouts"])
def test_shallow_valley_does_not_create_independent_components(dropout: bool) -> None:
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
    # The analytical valley is about 80% of either apex, not a clear split.
    # A brief missing-scan notch cannot turn this unresolved envelope into two.
    assert len(result.candidates) == 1
    peak = result.candidates[0].peak
    assert peak.peak_start < 2.0 < 2.54 < peak.peak_end
    envelope_mass = 2 * height * sigma * np.sqrt(2.0 * np.pi) * 60.0
    assert 0.90 * envelope_mass < peak.area < 1.01 * envelope_mass


@pytest.mark.parametrize("apex_scan_count", [1, 2])
def test_peak_top_requires_adjacent_acquired_support(apex_scan_count: int) -> None:
    rt = np.arange(101, dtype=float) * 0.04
    raw = 100.0 + 200.0 * np.exp(-0.5 * ((rt - 2.0) / 0.6) ** 2)
    raw[50 : 50 + apex_scan_count] += 3000.0
    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=2.0,
    )
    # Low acquired neighbors on the broad background cannot support a smoothed
    # impulse's apex. Two actual adjacent high observations remain sufficient.
    assert result.state == ("available" if apex_scan_count == 2 else "unavailable")
    if apex_scan_count == 1:
        assert result.reason == "insufficient_original_xic_support"


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
        candidate
        for candidate in result.candidates
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
    half_width = 0.40 * np.sqrt(2.0 * np.log(10.0))
    assert peak.peak_start == pytest.approx(2.27 - half_width, abs=0.02)
    assert peak.peak_end == pytest.approx(2.27 + half_width, abs=0.02)
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
    assert prepared.prepared is not None
    canonical = np.asarray(prepared.prepared.positive_signal)
    above_return = np.flatnonzero(canonical > 0.1 * np.max(canonical))
    answers = [
        evaluate_physical_peak(
            prepared,
            query_rt_min=5.0,
            selection_policy_version=policy,
        )
        for policy in (PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY)
    ]
    for answer in answers:
        assert answer.state == "available", answer.reason
        assert answer.facts is not None
        assert answer.facts.left_rt_min == rt[above_return[0] - 1]
        assert answer.facts.right_rt_min == rt[above_return[-1] + 1]
        observed_area = float(np.trapezoid(raw, rt)) * 60
        assert answer.facts.area_counts_seconds > 0.85 * observed_area
    assert answers[0].integration == answers[1].integration
    assert answers[0].answer.selected_segment == (answers[1].answer.selected_segment)


@pytest.mark.parametrize("pattern", ["alternating", "random", "random_amplitude"])
@pytest.mark.parametrize(
    "policy",
    [
        PHYSICAL_PEAK_POINT_POLICY,
        PHYSICAL_PEAK_WINDOW_POLICY,
        DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    ],
)
@pytest.mark.parametrize("points", [101, 301])
def test_sparse_stationary_background_is_not_a_peak(
    pattern: str,
    policy: str,
    points: int,
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
        prepare_physical_signal(rt, raw),
        query_rt_min=5.0,
        selection_policy_version=policy,
    )
    assert result.state == "unavailable"
    assert result.facts is None and result.integration is None


@pytest.mark.parametrize("background_fragments", [False, True])
@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY]
)
def test_acquired_peak_top_not_low_alternating_neighbors_supplies_support(
    background_fragments: bool,
    policy: str,
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
        prepared,
        query_rt_min=5.0,
        selection_policy_version=policy,
    )
    if not background_fragments:
        assert result.state == "unavailable"
        assert result.facts is None and result.integration is None
        if policy == DISCOVERY_PHYSICAL_PEAK_POINT_POLICY:
            # The query itself is the acquired 10%-height valley between two
            # alternating raw maxima, not a uniquely owned raw peak. This is
            # still non-numeric; no original-support gate has been relaxed.
            middle = len(raw) // 2
            assert raw[middle] < 0.5 * min(raw[middle - 1], raw[middle + 1])
            assert result.reason == "ambiguous_segment_candidates"
            assert len(result.question.admissible_segments) == 2
        else:
            assert result.reason == "insufficient_original_xic_continuity"
            selected = next(
                segment
                for segment in result.segment_universe.segments
                if segment.left_rt_min <= 5.0 <= segment.right_rt_min
            )
            original = raw[selected.start_index : selected.end_index]
            assert noise_shape_severity(original)[0] == 2
    else:
        assert result.state == "available" and result.integration is not None


@pytest.mark.parametrize("point_count", [1001, 2001])
@pytest.mark.parametrize("width", [0.65, 0.75])
def test_overlapping_components_keep_one_envelope_and_review(
    point_count: int,
    width: float,
) -> None:
    # Three analytically present components have elevated intervening valleys.
    # One unresolved envelope is measurable, not the individual constituents.
    rt, raw = _overlapping_triplet_trace(point_count, width)
    profile = SignalProcessingProfile()
    prepared = prepare_physical_signal(rt, raw, profile=profile)
    targeted, discovery = (
        evaluate_physical_peak(
            prepared,
            query_rt_min=5.0,
            selection_policy_version=policy,
        )
        for policy in (
            PHYSICAL_PEAK_POINT_POLICY,
            DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
        )
    )
    assert targeted.state == discovery.state == "available"
    assert discovery.facts is not None
    assert discovery.facts.apex_rt_min == pytest.approx(7.0, abs=0.15)
    assert discovery.facts.left_rt_min < 3.0
    assert discovery.facts.right_rt_min > 7.0
    assert discovery.integration == targeted.integration
    assert discovery.requires_overlap_review
    restored = _stored(discovery)
    assert restored == discovery
    assert restored.requires_overlap_review


@pytest.mark.parametrize("scale,background", [(1.0, 0.0), (1000.0, 100000.0)])
def test_background_fluctuation_cannot_borrow_height_from_its_baseline(
    scale: float,
    background: float,
) -> None:
    # Scaled acquired background with isolated impulses. The smoothed local
    # maximum has little relief above its valleys, despite its residual height.
    raw = (
        scale
        * np.asarray(
            [
                170.433,
                189.858,
                153.087,
                137.425,
                137.427,
                158.876,
                146.820,
                229.967,
                130.081,
                149.159,
                174.631,
                152.543,
                140.415,
                135.986,
                138.815,
                138.261,
                141.191,
                211.361,
                127.825,
                134.766,
                106.415,
                120.800,
                141.851,
                143.503,
                120.040,
                144.941,
                133.412,
                232.211,
                161.916,
            ]
        )
        + background
    )
    rt = np.arange(len(raw), dtype=float) / 10.0
    prepared = prepare_physical_signal(rt, raw)
    fresh = evaluate_physical_peak(
        prepared,
        query_rt_min=1.7,
    )
    assert fresh.state == "unavailable"
    assert fresh.reason == "insufficient_original_xic_support"
    assert fresh.facts is None and fresh.integration is None


@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, PHYSICAL_PEAK_WINDOW_POLICY]
)
@pytest.mark.parametrize("background", [1000.0, 100000.0])
def test_constant_background_cannot_supply_acquired_support_to_an_impulse(
    policy: str,
    background: float,
) -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.full_like(rt, background)
    raw[15] += 1000.0
    fresh = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=1.5,
        selection_policy_version=policy,
    )
    assert fresh.state == "unavailable"
    assert fresh.reason == "insufficient_original_xic_support"
    assert fresh.facts is None and fresh.integration is None


@pytest.mark.parametrize("width", [0.1, 0.5, 1.0])
@pytest.mark.parametrize("background,slope", [(0.0, 0.0), (100000.0, 500.0)])
@pytest.mark.parametrize(
    "policy", [PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY]
)
def test_background_component_generator_respects_query_ownership_policy(
    width: float,
    background: float,
    slope: float,
    policy: str,
) -> None:
    rt = np.linspace(0.0, 10.0, 101)
    if slope:
        rt += 0.01 * np.sin(np.arange(len(rt)))  # Uneven acquired RT spacing.
    raw = 1000.0 * np.exp(-0.5 * ((rt - 5.0) / width) ** 2) + background + slope * rt
    prepared = prepare_physical_signal(rt, raw)
    result = evaluate_physical_peak(
        prepared,
        query_rt_min=5.0,
        selection_policy_version=policy,
    )
    if policy == DISCOVERY_PHYSICAL_PEAK_POINT_POLICY and slope:
        # The generator reveals an injected Gaussian, but no measured NL or
        # baseline decomposition is available to a raw-basin question. On this
        # rising total signal the retained raw owner is the clipped right edge,
        # not an independently resolved component at the injected center.
        assert int(np.argmax(raw)) == len(raw) - 1
        from scipy.signal import find_peaks

        (local_maximum,) = find_peaks(raw)[0]
        valley = local_maximum + int(np.argmin(raw[local_maximum:]))
        # Independent input topology: its shallow raw dip does not meet the
        # fixed clear-valley split, nor the fixed envelope-relative prominence.
        assert raw[valley] > 0.5 * raw[local_maximum]
        assert raw[local_maximum] - raw[valley] < (
            prepared.prepared.profile.segment_min_prominence_fraction * max(raw)
        )
        assert result.state == "unavailable"
        assert result.reason == "incomplete_peak_context"
        assert result.facts is None and result.integration is None
    else:
        assert result.state == "available"
        assert result.integration is not None
        assert result.facts is not None


@pytest.mark.parametrize("positive_points", [2, 3])
def test_sparse_isolated_peak_has_the_same_contrast_limit_in_both_policies(
    positive_points: int,
) -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.zeros_like(rt)
    raw[14 : 14 + positive_points] = [100.0, 120.0, 80.0][:positive_points]
    prepared = prepare_physical_signal(rt, raw)
    old = evaluate_physical_peak(
        prepared,
        query_rt_min=1.5,
    )
    new = evaluate_physical_peak(
        prepared,
        query_rt_min=1.5,
        selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    )
    assert old.state == new.state == "unavailable"
    assert old.reason == new.reason == "insufficient_local_peak_contrast"
    assert new.integration == old.integration
    assert new.segment_universe == old.segment_universe


@pytest.mark.parametrize("background", [False, True])
def test_current_discovery_qualification_persists_exactly(
    background: bool,
) -> None:
    profile = SignalProcessingProfile()
    if background:
        fixture = json.loads(
            (
                Path(__file__).parent / "fixtures/discovery_non_peak_background.json"
            ).read_text()
        )
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
            prepared,
            query_rt_min=query,
            selection_policy_version=policy,
        )
        assert answer.state == ("unavailable" if background else "available")
        if background:
            # The whole background contour reaches the acquired edge. Neither
            # workflow may turn one local fluctuation into a measured peak.
            assert answer.reason == "incomplete_peak_context"
        restored = _stored(answer)
        assert restored == answer


def test_is_provisional_peak_shape() -> None:
    from xic_extractor.peak_detection.signal_quality import MAD_TO_SIGMA, SignalQuality

    sparse = np.array([0.0, 500.0, 0.0])
    assert not is_provisional_peak_shape(
        sparse,
        apex_index=1,
        signal_quality=SignalQuality("zero_noise", noise_sigma=0.0),
    )
    clear = np.array([100.0, 150.0, 400.0, 1000.0, 420.0, 160.0, 95.0])
    assert not is_provisional_peak_shape(
        clear,
        apex_index=3,
        signal_quality=SignalQuality("available", signal_to_noise=30.0 / MAD_TO_SIGMA),
    )
    noisy = np.array([100.0, 110.0, 120.0, 130.0, 115.0, 105.0, 100.0])
    assert is_provisional_peak_shape(
        noisy,
        apex_index=3,
        signal_quality=SignalQuality("available", signal_to_noise=3.0 / MAD_TO_SIGMA),
    )
    assert not is_provisional_peak_shape(noisy, apex_index=3, signal_quality=None)


@pytest.mark.parametrize(
    "policy,reason",
    [
        (PHYSICAL_PEAK_POINT_POLICY, "insufficient_original_xic_support"),
        (PHYSICAL_PEAK_REFERENCE_POLICY, "insufficient_original_xic_support"),
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
        query_rt_min=2.0,
        selection_policy_version=policy,
    )
    assert result.state == "unavailable"
    assert result.reason == reason
    assert result.facts is None
    assert result.integration is None


def test_wide_supported_peak_does_not_use_uncut_top_ripples_as_contrast_valleys() -> (
    None
):
    rt = np.linspace(0.0, 6.0, 601)
    center = 3.0
    sigma = 0.8
    amplitude = 12000.0
    rng = np.random.default_rng(479)
    analytic_envelope = amplitude * np.exp(-0.5 * ((rt - center) / sigma) ** 2)
    # Bounded raw measurement noise is at most 1.25% of the analytic apex.
    # The known broad positive is one resolved component. Its small surface
    # ripples cannot create contrast boundaries that segmentation did not cut.
    raw = 300.0 + analytic_envelope + rng.uniform(-150.0, 150.0, rt.size)

    result = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        query_rt_min=center,
    )

    assert result.state == "available"
    assert result.facts is not None
    analytic_area = amplitude * sigma * np.sqrt(2 * np.pi) * 60.0
    assert result.facts.area_counts_seconds == pytest.approx(analytic_area, rel=0.15)
    (segment,) = result.segment_universe.segments
    assert segment.apex_rt_min == pytest.approx(center, abs=0.03)
    assert segment.left_rt_min <= center - 1.5 * sigma
    assert segment.right_rt_min >= center + 1.5 * sigma


def test_clear_doublet_closes_at_relative_return_before_the_deeper_valley() -> None:
    rt = np.linspace(0.0, 6.0, 601)
    left_center, right_center = 2.5, 3.5
    sigma = 0.18
    analytic_doublet = 9000.0 * np.exp(
        -0.5 * ((rt - left_center) / sigma) ** 2
    ) + 8500.0 * np.exp(-0.5 * ((rt - right_center) / sigma) ** 2)
    rng = np.random.default_rng(2479)
    raw = 300.0 + analytic_doublet + rng.uniform(-25.0, 25.0, rt.size)
    prepared = prepare_physical_signal(rt, raw)
    valley_rt = (left_center + right_center) / 2.0

    left = evaluate_physical_peak(prepared, query_rt_min=left_center)
    right = evaluate_physical_peak(
        prepared,
        query_rt_min=right_center,
    )

    assert left.state == "available" and left.facts is not None
    assert right.state == "available" and right.facts is not None
    assert left.facts.apex_rt_min == pytest.approx(left_center, abs=0.03)
    assert right.facts.apex_rt_min == pytest.approx(right_center, abs=0.03)
    # Each lobe returns below 10% before the very deep inter-peak valley.
    assert left_center < left.facts.right_rt_min < valley_rt
    assert valley_rt < right.facts.left_rt_min < right_center
    assert left.facts.right_rt_min == pytest.approx(
        left_center + sigma * np.sqrt(2.0 * np.log(10.0)), abs=0.04
    )
    assert right.facts.left_rt_min == pytest.approx(
        right_center - sigma * np.sqrt(2.0 * np.log(10.0)), abs=0.04
    )
    assert left.answer.selected_segment != right.answer.selected_segment


@pytest.mark.parametrize("query_rt", [1.525, 2.475, 1.542])
def test_tail_beyond_relative_return_does_not_select_the_peak(
    query_rt: float,
) -> None:
    rt = np.linspace(0.0, 4.0, 161)
    raw = 100.0 + 10000.0 * np.exp(-0.5 * ((rt - 2.0) / 0.16) ** 2)
    prepared = prepare_physical_signal(rt, raw)

    result = evaluate_physical_peak(prepared, query_rt_min=query_rt)

    assert result.state == "unavailable"
    assert result.reason == "no_selection"
    assert result.facts is None
    peak = evaluate_physical_peak(prepared, query_rt_min=2.0)
    assert peak.state == "available" and peak.facts is not None
    assert peak.facts.apex_rt_min == pytest.approx(2.0)


@pytest.mark.parametrize("sigma", [0.02, 0.2])
def test_tenth_height_return_uses_canonical_apex_not_coarse_envelope(sigma) -> None:
    rt = np.linspace(0.0, 4.0, 4001)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 2.0) / sigma) ** 2)
    prepared = prepare_physical_signal(rt, raw)
    assert prepared.prepared is not None
    signal = np.asarray(prepared.prepared.positive_signal)
    # An isolated Gaussian has one contiguous region above its canonical 10%
    # level. Include exactly the acquired samples bracketing that return.
    above = np.flatnonzero(signal > 0.1 * np.max(signal))
    left, right = int(above[0]) - 1, int(above[-1]) + 1
    peak = evaluate_physical_peak(
        prepared,
        query_rt_min=2.0,
        selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    )
    assert peak.state == "available" and peak.facts is not None
    assert peak.facts.left_rt_min == rt[left]
    assert peak.facts.right_rt_min == rt[right]
    assert peak.facts.area_counts_seconds == pytest.approx(
        np.trapezoid(signal[left : right + 1], rt[left : right + 1]) * 60.0
    )
    tail = evaluate_physical_peak(
        prepared,
        query_rt_min=float(rt[right + 1]),
        selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    )
    # The analytical single Gaussian has no separating raw valley here.
    # Its canonical 10% return still controls area, not NL membership.
    assert tail.state == "available"
    assert tail.facts == peak.facts


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
            7.98338,
            8.02491,
            8.06641,
            8.10795,
            8.14947,
            8.19102,
            8.23267,
            8.27419,
            8.31562,
            8.35692,
            8.39808,
            8.43923,
            8.4806,
            8.52364,
            8.56349,
            8.60512,
            8.64677,
            8.6881,
            8.7269,
            8.76833,
            8.80978,
            8.8511,
            8.89234,
            8.93348,
            8.97471,
            9.01593,
            9.0571,
            9.09821,
            9.13928,
            9.17004,
            9.20908,
            9.23472,
            9.26752,
            9.29309,
            9.32694,
            9.35754,
            9.38397,
            9.4186,
            9.44303,
            9.48092,
            9.50344,
            9.53729,
            9.56287,
            9.59561,
            9.62834,
            9.65282,
            9.67837,
            9.70378,
            9.72409,
            9.74741,
            9.76464,
            9.79315,
            9.81452,
            9.84204,
            9.86449,
            9.89827,
            9.92376,
            9.9503,
            9.96861,
        ]
    )
    raw = np.asarray(
        [
            301.7,
            0.0,
            189.3,
            174.5,
            0.0,
            105.4,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1547.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            164.1,
            713.0,
            2952.1,
            6608.3,
            10918.3,
            11403.2,
            7619.3,
            3442.4,
            1510.6,
            417.8,
            159.1,
            0.0,
            98.9,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            153.1,
            115.9,
            0.0,
            0.0,
            0.0,
            131.6,
            0.0,
            0.0,
            221.3,
            0.0,
            0.0,
            0.0,
            98.8,
            0.0,
            0.0,
            159.5,
            176.4,
            0.0,
            113.7,
            0.0,
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
        raw += 100.0 * np.exp(-0.5 * ((rt - 0.8) / 0.2) ** 2)
    else:
        raw = np.zeros(len(rt))
    prepared = prepare_physical_signal(rt, raw, profile=profile)
    ordinary = evaluate_physical_peak(prepared, query_rt_min=0.8)
    evaluation = evaluate_physical_peak(
        prepared,
        query_rt_min=0.8,
        selection_policy_version=PHYSICAL_PEAK_NO_SELECTION_POLICY,
    )

    assert evaluation.prepared == ordinary.prepared
    assert evaluation.segment_universe == ordinary.segment_universe
    assert evaluation.question.query_rt_min == 0.8
    assert evaluation.question.universe.segments == ordinary.question.universe.segments
    assert evaluation.question.admissible_segments == ()
    assert evaluation.state == "unavailable"
    assert evaluation.facts is None
    assert evaluation.integration is None
    if has_signal:
        assert ordinary.state == "available"
        assert evaluation.segment_universe.segments
        assert evaluation.reason == "no_selection"
    else:
        assert evaluation.reason == ordinary.reason == "no_signal_above_baseline"

    assert _stored(evaluation) == evaluation


@pytest.mark.parametrize("terminal", ["available", "unavailable", "failed"])
def test_physical_evaluation_persistence_decodes_terminal_owner_state(
    terminal: str,
) -> None:
    profile = SignalProcessingProfile(window_points=3)
    if terminal == "available":
        rt = np.linspace(0.0, 3.0, 31)
        raw = np.full(31, 10.0)
        raw += 100.0 * np.exp(-0.5 * ((rt - 0.8) / 0.2) ** 2)
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
        query_rt_min=query_rt,
        request_context_rt_start_min=context[0],
        request_context_rt_end_min=context[1],
    )

    restored = _stored(evaluation)

    if terminal == "failed":
        np.testing.assert_array_equal(restored.prepared.rt, rt)
        np.testing.assert_array_equal(restored.prepared.raw, raw)
        assert restored.reason == evaluation.reason
        assert restored.segment_universe.segments == ()
        assert restored.facts is None
    else:
        assert restored == evaluation
    assert restored.state == terminal


def test_sidecar_read_returns_the_recorded_evaluations(tmp_path: Path) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    path = tmp_path / "facts.json"

    write_discovery_physical_fact_sidecar(
        path,
        run_spec=spec,
        acquisition_input=acquisition_input,
        work_manifest=manifest,
        records=records,
    )

    restored = read_discovery_physical_fact_sidecar(
        path,
        expected_run_spec=spec,
        expected_acquisition_input=acquisition_input,
        expected_work_manifest=manifest,
    )
    assert len(restored) == len(records)
    assert tuple(record.evaluation for record in restored) == tuple(
        record.evaluation for record in records
    )


@pytest.mark.parametrize("point_count", [1501, 3001])
def test_middle_query_selects_the_unresolved_envelope_not_a_component(
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
            prepared,
            query_rt_min=10.9,
            selection_policy_version=policy,
        )
        for policy in (PHYSICAL_PEAK_POINT_POLICY, DISCOVERY_PHYSICAL_PEAK_POINT_POLICY)
    ]
    for answer in answers:
        assert answer.state == "available", answer.reason
        assert answer.facts is not None
        assert answer.facts.apex_rt_min == pytest.approx(12.5, abs=0.15)
        # Only the left valley is clear. The middle and right lobes share one
        # envelope and cannot claim separate component-specific areas.
        assert 9.4 < answer.facts.left_rt_min < 10.3
        assert answer.facts.right_rt_min > 13.0
        assert answer.requires_overlap_review
    assert answers[0].integration == answers[1].integration
    # Measurement is unchanged; the Discovery interval is raw-valley-owned,
    # whereas ordinary point segmentation reports its canonical valley label.
    assert answers[0].facts == answers[1].facts
    assert (
        set(answers[0].answer.selected_segment.evidence_sources)
        - set(answers[1].answer.selected_segment.evidence_sources)
    ) <= {"local_minimum_valley"}


@pytest.mark.parametrize("point_count", [301, 1801])
def test_brief_apex_dropout_does_not_create_two_quantitative_components(
    point_count: int,
) -> None:
    rt = np.linspace(0.0, 10.0, point_count)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 5.0) / 0.8) ** 2)
    middle = point_count // 2
    raw[middle - 1 : middle + 2] = 0.0
    prepared = prepare_physical_signal(rt, raw)
    answer = evaluate_physical_peak(
        prepared,
        query_rt_min=5.0,
    )
    assert answer.state == "available"
    assert answer.facts is not None
    assert len(answer.segment_universe.segments) == 1
    assert answer.facts.left_rt_min < 3.4
    assert answer.facts.right_rt_min > 6.6
    # A 10%-height return retains about 96.8% of Gaussian mass, not the entire
    # positive tail. A false split would still lose about half the envelope.
    assert prepared.prepared is not None
    observed_mass = float(np.trapezoid(prepared.prepared.positive_signal, rt)) * 60.0
    assert 0.96 * observed_mass < answer.facts.area_counts_seconds < observed_mass
    assert prepared.prepared.raw[middle - 1 : middle + 2] == (0.0, 0.0, 0.0)


def test_optional_quality_round_trip_and_old_payload_decoding():
    from dataclasses import replace

    rt = np.linspace(0.0, 2.0, 101)
    raw = 10.0 + 200.0 * np.exp(-0.5 * ((rt - 1.0) / 0.1) ** 2)
    raw += np.random.default_rng(916).normal(0.0, 0.5, len(rt))
    profile = SignalProcessingProfile()
    current = evaluate_physical_peak(
        prepare_physical_signal(rt, raw, profile=profile),
        query_rt_min=1.0,
    )
    assert current.integration is not None
    assert current.integration.signal_quality.status == "available"
    assert _stored(current) == current
    old = replace(
        current, integration=replace(current.integration, signal_quality=None)
    )
    assert "signal_quality" not in json.dumps(physical_evaluation_payload(old))
    restored = _stored(old)
    assert restored.integration.signal_quality is None
    assert old.facts == current.facts
    assert old.answer == current.answer
