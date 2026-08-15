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
    deserialize_physical_peak_evaluation,
    evaluate_physical_peak,
    prepare_physical_signal,
    serialize_physical_peak_evaluation,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)


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


def test_physical_evaluation_persistence_rejects_resealed_owner_tamper() -> None:
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
    value["evaluation"]["answer"]["physical_answer_id"] = (
        "physical-answer.v1:" + "0" * 64
    )

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
