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
    evaluate_physical_peak,
    prepare_physical_signal,
    serialize_physical_peak_evaluation,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
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
