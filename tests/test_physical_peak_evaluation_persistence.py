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
from xic_extractor.peak_detection.evidence_facts import (
    LEGACY_PHYSICAL_PEAK_SELECTION_POLICY,
    PHYSICAL_PEAK_POINT_POLICY,
    PHYSICAL_PEAK_WINDOW_POLICY,
    deserialize_physical_peak_evaluation,
    enumerate_physical_segment_universe,
    evaluate_physical_peak,
    mint_physical_peak_question,
    prepare_physical_signal,
    serialize_physical_peak_evaluation,
)
from xic_extractor.peak_detection.scoring_metrics import noise_shape_severity
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)


def _context_sensitive_trace() -> tuple[np.ndarray, np.ndarray]:
    # Scaled observed XIC with scan-ordinal RT: removing one exterior scan
    # changes the AsLS valley sign while the retained Gaussian peak is exact.
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
    return np.arange(len(raw), dtype=float), raw


def test_exterior_context_change_keeps_observed_near_baseline_peak_available() -> None:
    rt, raw = _context_sensitive_trace()
    full_signal = prepare_physical_signal(rt, raw)
    cropped_signal = prepare_physical_signal(rt[:-1], raw[:-1])
    full = evaluate_physical_peak(
        full_signal, selection_subject_id="exterior-context", query_rt_min=23.0
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
        cropped_signal, selection_subject_id="exterior-context", query_rt_min=23.0
    )
    assert cropped.state == "available"
    assert cropped.facts is not None and cropped.integration is not None
    assert cropped.facts.apex_rt_min == full.facts.apex_rt_min
    assert (
        rt[0] < cropped.facts.left_rt_min < 23.0 < cropped.facts.right_rt_min < rt[-2]
    )


@pytest.mark.parametrize(
    "policy", ["baseline_peak_contains_query_v4", "unique_baseline_peak_in_context_v3"]
)
def test_recorded_zero_return_policy_replays_without_edge_recovery(policy: str) -> None:
    rt, raw = _context_sensitive_trace()
    rt, raw = rt[:-1], raw[:-1]
    profile = SignalProcessingProfile()
    recorded = evaluate_physical_peak(
        prepare_physical_signal(rt, raw),
        selection_subject_id="recorded-zero-return",
        query_rt_min=23.0,
        selection_policy_version=policy,
    )
    assert recorded.state == "unavailable"
    assert recorded.reason == "incomplete_peak_context"
    payload = serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=recorded
    )
    *_, restored = deserialize_physical_peak_evaluation(payload)
    assert restored == recorded
    assert serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=restored
    ) == payload


def test_complete_peak_values_are_unchanged_by_edge_recovery() -> None:
    rt, raw = _context_sensitive_trace()
    prepared = prepare_physical_signal(rt, raw)
    recorded = evaluate_physical_peak(
        prepared, selection_subject_id="complete-context", query_rt_min=23.0,
        selection_policy_version="baseline_peak_contains_query_v4",
    )
    fresh = evaluate_physical_peak(
        prepared, selection_subject_id="complete-context", query_rt_min=23.0
    )
    assert recorded.state == fresh.state == "available"
    assert recorded.facts is not None and fresh.facts is not None
    assert fresh.facts.physical_segment_id == recorded.facts.physical_segment_id
    assert (
        fresh.facts.relative_shape_content_hash
        == recorded.facts.relative_shape_content_hash
    )
    assert fresh.facts.scan_support_count == recorded.facts.scan_support_count
    assert fresh.integration == recorded.integration


@pytest.mark.parametrize("fresh_universe", [False, True])
def test_point_question_rejects_a_different_boundary_policy_family(
    fresh_universe: bool,
) -> None:
    rt, raw = _context_sensitive_trace()
    prepared = prepare_physical_signal(rt, raw)
    old_policy = "baseline_peak_contains_query_v4"
    universe_policy = PHYSICAL_PEAK_POINT_POLICY if fresh_universe else old_policy
    query_policy = old_policy if fresh_universe else PHYSICAL_PEAK_POINT_POLICY
    universe = enumerate_physical_segment_universe(
        prepared, selection_policy_version=universe_policy
    )
    with pytest.raises(ValueError, match="crosses boundary policy"):
        mint_physical_peak_question(
            prepared.projection, universe,
            selection_subject_id="cross-boundary-policy", query_rt_min=23.0,
            selection_policy_version=query_policy,
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
        "baseline_peak_contains_query_v3",
        "unique_baseline_peak_in_context_v2",
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
    "policy,recorded_policy,query_rt",
    [
        (PHYSICAL_PEAK_POINT_POLICY, "baseline_peak_contains_query_v3", 1.5),
        (PHYSICAL_PEAK_WINDOW_POLICY, "unique_baseline_peak_in_context_v2", 0.2),
    ],
)
@pytest.mark.parametrize("positions", [(14, 16), (12, 14, 16, 18)])
def test_discontinuous_original_points_cannot_mint_fresh_physical_facts(
    policy: str, recorded_policy: str, query_rt: float, positions: tuple[int, ...]
) -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.zeros_like(rt)
    raw[list(positions)] = 1000.0
    profile = SignalProcessingProfile()
    prepared = prepare_physical_signal(rt, raw, profile=profile)
    recorded = evaluate_physical_peak(
        prepared,
        selection_subject_id="isolated-acquired-impulses",
        query_rt_min=query_rt,
        selection_policy_version=recorded_policy,
    )
    assert recorded.state == "available"
    payload = serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=recorded
    )
    *_, restored = deserialize_physical_peak_evaluation(payload)
    assert restored == recorded
    assert serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=restored
    ) == payload

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
    "policy,recorded_policy,query_rt",
    [
        (PHYSICAL_PEAK_POINT_POLICY, "baseline_peak_contains_query_v5", 5.0),
        (PHYSICAL_PEAK_WINDOW_POLICY, "unique_baseline_peak_in_context_v4", 0.5),
    ],
)
def test_fragmented_original_peak_is_not_rescued_by_smoothed_envelope(
    policy: str, recorded_policy: str, query_rt: float,
) -> None:
    rt = np.linspace(0.0, 10.0, 101)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 5.0) / 0.8) ** 2)
    raw[::2] = 0.0
    raw[50] = 1000.0  # An adjacent pair does not repair repeated interruptions.
    profile = SignalProcessingProfile()
    prepared = prepare_physical_signal(rt, raw, profile=profile)
    recorded = evaluate_physical_peak(
        prepared, selection_subject_id="fragmented-envelope", query_rt_min=query_rt,
        selection_policy_version=recorded_policy,
    )
    assert recorded.state == "available" and recorded.facts is not None
    payload = serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=recorded,
    )
    *_, restored = deserialize_physical_peak_evaluation(payload)
    assert restored == recorded
    assert serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=restored,
    ) == payload

    fresh = evaluate_physical_peak(
        prepared, selection_subject_id="fragmented-envelope", query_rt_min=query_rt,
        selection_policy_version=policy,
    )
    assert fresh.state == "unavailable"
    assert fresh.reason == "fragmented_original_xic_support"
    assert fresh.facts is None and fresh.integration is None
    assert fresh.answer.physical_segment_id is None
    assert fresh.segment_universe == recorded.segment_universe
    fresh_payload = serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=fresh,
    )
    *_, restored_fresh = deserialize_physical_peak_evaluation(fresh_payload)
    assert restored_fresh == fresh
    assert serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=restored_fresh,
    ) == fresh_payload


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
    recorded = evaluate_physical_peak(
        prepared, selection_subject_id="supported-original-peak", query_rt_min=5.0,
        selection_policy_version="baseline_peak_contains_query_v5",
    )
    fresh = evaluate_physical_peak(
        prepared, selection_subject_id="supported-original-peak", query_rt_min=5.0,
    )
    assert recorded.state == fresh.state == "available"
    if not background_fragments:
        assert recorded.facts is not None
        selected = next(
            segment for segment in recorded.segment_universe.segments
            if segment.physical_segment_id == recorded.facts.physical_segment_id
        )
        original = raw[selected.start_index:selected.end_index]
        assert noise_shape_severity(original)[0] == 2
    assert fresh.integration == recorded.integration
    assert fresh.segment_universe == recorded.segment_universe


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
    profile = SignalProcessingProfile()
    prepared = prepare_physical_signal(rt, raw, profile=profile)
    recorded = evaluate_physical_peak(
        prepared, selection_subject_id="background-fluctuation", query_rt_min=1.7,
        selection_policy_version="baseline_peak_contains_query_v6",
    )
    assert recorded.state == "available"
    payload = serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=recorded,
    )
    *_, restored = deserialize_physical_peak_evaluation(payload)
    assert restored == recorded
    fresh = evaluate_physical_peak(
        prepared, selection_subject_id="background-fluctuation", query_rt_min=1.7,
    )
    assert fresh.state == "unavailable"
    assert fresh.reason == "insufficient_local_peak_contrast"
    assert fresh.facts is None and fresh.integration is None
    assert fresh.segment_universe == recorded.segment_universe


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
def test_resolved_peak_on_background_keeps_its_canonical_integration(
    width: float, background: float, slope: float,
) -> None:
    rt = np.linspace(0.0, 10.0, 101)
    if slope:
        rt += 0.01 * np.sin(np.arange(len(rt)))  # Uneven acquired RT spacing.
    raw = (
        1000.0 * np.exp(-0.5 * ((rt - 5.0) / width) ** 2)
        + background + slope * rt
    )
    prepared = prepare_physical_signal(rt, raw)
    recorded = evaluate_physical_peak(
        prepared, selection_subject_id="resolved-peak", query_rt_min=5.0,
        selection_policy_version="baseline_peak_contains_query_v6",
    )
    fresh = evaluate_physical_peak(
        prepared, selection_subject_id="resolved-peak", query_rt_min=5.0,
    )
    assert recorded.state == fresh.state == "available"
    assert fresh.integration == recorded.integration
    assert fresh.segment_universe == recorded.segment_universe


@pytest.mark.parametrize(
    "policy,query_rt",
    [
        ("baseline_peak_contains_query_v2", 1.5),
        ("unique_baseline_peak_in_context_v1", 0.2),
    ],
)
def test_recorded_singleton_policy_replays_without_reinterpretation(
    policy: str, query_rt: float
) -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.zeros_like(rt)
    raw[15] = 1000.0
    profile = SignalProcessingProfile()
    recorded = evaluate_physical_peak(
        prepare_physical_signal(rt, raw, profile=profile),
        selection_subject_id="recorded-singleton",
        query_rt_min=query_rt,
        selection_policy_version=policy,
    )
    assert recorded.state == "available"
    assert recorded.facts is not None
    assert recorded.facts.scan_support_count > 1
    payload = serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=recorded
    )
    *_, restored = deserialize_physical_peak_evaluation(payload)
    assert restored == recorded
    assert serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=restored
    ) == payload


@pytest.mark.parametrize(
    "policy,reason",
    [
        (PHYSICAL_PEAK_POINT_POLICY, "insufficient_original_xic_support"),
        (PHYSICAL_PEAK_WINDOW_POLICY, "ambiguous_segment_candidates"),
    ],
)
def test_other_peak_support_cannot_rescue_a_singleton_or_resolve_ambiguity(
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


@pytest.mark.parametrize("query_rt", [1.525, 2.0])
def test_legacy_peak_policy_rehydrates_without_changing_saved_answer(query_rt) -> None:
    rt = np.linspace(0.0, 4.0, 161)
    raw = 100.0 + 10000.0 * np.exp(-0.5 * ((rt - 2.0) / 0.16) ** 2)
    profile = SignalProcessingProfile()
    legacy = evaluate_physical_peak(
        prepare_physical_signal(rt, raw, profile=profile),
        selection_subject_id="saved-legacy-query",
        query_rt_min=query_rt,
        selection_policy_version=LEGACY_PHYSICAL_PEAK_SELECTION_POLICY,
    )
    assert legacy.state == ("available" if query_rt == 2.0 else "unavailable")
    payload = serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=legacy
    )
    *_, restored = deserialize_physical_peak_evaluation(payload)
    assert restored == legacy
    assert serialize_physical_peak_evaluation(
        rt, raw, profile=profile, evaluation=restored
    ) == payload


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
    centers, query_rt, expected
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
        selection_policy_version=PHYSICAL_PEAK_WINDOW_POLICY,
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
            "physical_answer_id", "physical-answer.v1:" + "0" * 64
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
    from xic_extractor.peak_detection import evidence_facts as owner

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
