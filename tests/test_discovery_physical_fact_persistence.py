from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    TraceCoordinateKind,
    acquire_trace_batch,
    acquired_trace_content_id,
    mint_trace_acquisition_capability,
)
from xic_extractor.discovery.models import (
    DiscoveryPhysicalWorkItem,
    DiscoverySettings,
    NeutralLossProfile,
)
from xic_extractor.discovery.physical_fact_persistence import (
    DiscoveryPhysicalFactRecord,
    deserialize_discovery_physical_fact_sidecar,
    read_discovery_physical_fact_sidecar,
    serialize_discovery_physical_fact_sidecar,
    write_discovery_physical_fact_sidecar,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.peak_detection.evidence_facts import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.xic_models import XICRequest, XICTrace


def _settings() -> DiscoverySettings:
    return DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
    )


def _spec(tmp_path: Path, *, profile_points: int = 3):
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"sample acquisition")
    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=_settings(),
        signal_processing_profile=SignalProcessingProfile(window_points=profile_points),
    )
    return raw_path, spec, spec.acquisition_inputs[0]


def _work(
    ordinal: int,
    acquisition_input,
    *,
    terminal: str,
    acquired_trace: AcquiredTrace,
) -> DiscoveryPhysicalWorkItem:
    if terminal == "available":
        query = 0.8
        context = (0.0, 3.0)
    elif terminal == "unavailable":
        query = 1.0
        context = (1.0, 1.0)
    else:
        query = 1.5
        context = (1.0, 1.5)
    return DiscoveryPhysicalWorkItem(
        work_ordinal=ordinal,
        acquisition_source_id=(
            acquisition_input.acquisition_source.acquisition_source_id
        ),
        sample_stem=acquisition_input.sample_stem,
        best_ms2_scan_id=100 + ordinal,
        precursor_mz=500.0 + ordinal,
        product_mz=383.9526 + ordinal,
        query_rt_min=query,
        context_rt_start_min=context[0],
        context_rt_end_min=context[1],
        trace_coordinate_id=acquired_trace.coordinate.trace_coordinate_id,
        acquired_trace_content_id=acquired_trace_content_id(acquired_trace),
        xic_mz=acquired_trace.coordinate.request.mz,
        xic_rt_min=acquired_trace.coordinate.request.rt_min,
        xic_rt_max=acquired_trace.coordinate.request.rt_max,
        xic_ppm_tol=acquired_trace.coordinate.request.ppm_tol,
    )


def _record(
    work_item: DiscoveryPhysicalWorkItem,
    *,
    terminal: str,
    profile: SignalProcessingProfile,
    acquired_trace: AcquiredTrace,
) -> DiscoveryPhysicalFactRecord:
    rt = acquired_trace.trace.rt
    raw = acquired_trace.trace.intensity
    evaluation = evaluate_physical_peak(
        prepare_physical_signal(rt, raw, profile=profile),
        selection_subject_id=work_item.selection_subject_id,
        query_rt_min=work_item.query_rt_min,
        request_context_rt_start_min=work_item.context_rt_start_min,
        request_context_rt_end_min=work_item.context_rt_end_min,
    )
    return DiscoveryPhysicalFactRecord(
        work_item=work_item,
        acquired_trace=acquired_trace,
        evaluation=evaluation,
    )


def _acquired_trace(acquisition_input, *, terminal: str, ordinal: int) -> AcquiredTrace:
    if terminal == "available":
        rt = np.linspace(0.0, 3.0, 31)
        raw = np.full(31, 10.0)
        raw[7:10] += [30.0, 100.0, 30.0]
        request_rt = (0.0, 3.0)
    elif terminal == "unavailable":
        rt = np.asarray([1.0])
        raw = np.asarray([10.0])
        request_rt = (1.0, 1.0)
    else:
        rt = np.asarray([1.0, 2.0, 1.5])
        raw = np.asarray([1.0, 2.0, 1.0])
        request_rt = (1.0, 2.0)

    class Adapter:
        raw_chromatogram_call_count = 0

        def extract_xic_many(self, _requests):
            return (XICTrace.from_arrays(rt, raw),)

        def scan_window_for_request(self, _request):
            return (1000 + ordinal, 1010 + ordinal)

    capability = mint_trace_acquisition_capability(
        acquisition_input.acquisition_source,
        adapter_namespace="tests.discovery_physical_fact_persistence",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    request = XICRequest(
        mz=500.0 + ordinal,
        rt_min=request_rt[0],
        rt_max=request_rt[1],
        ppm_tol=20.0,
    )
    return acquire_trace_batch(capability, (request,), Adapter()).traces[0]


def _population(spec, acquisition_input):
    terminals = ("available", "unavailable", "failed")
    manifest = []
    records = []
    for index, terminal in enumerate(terminals):
        acquired = _acquired_trace(
            acquisition_input,
            terminal=terminal,
            ordinal=index,
        )
        item = _work(
            index,
            acquisition_input,
            terminal=terminal,
            acquired_trace=acquired,
        )
        manifest.append(item)
        records.append(
            _record(
                item,
                terminal=terminal,
                profile=spec.signal_processing_profile,
                acquired_trace=acquired,
            )
        )
    return tuple(manifest), tuple(records)


def test_discovery_sidecar_roundtrips_complete_independent_work_manifest(
    tmp_path: Path,
) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    path = tmp_path / "out" / "discovery_physical_facts.v1.json"

    assert (
        write_discovery_physical_fact_sidecar(
            path,
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=manifest,
            records=records,
        )
        == path
    )
    rehydrated = read_discovery_physical_fact_sidecar(
        path,
        expected_run_spec=spec,
        expected_acquisition_input=acquisition_input,
        expected_work_manifest=manifest,
    )

    assert tuple(record.work_item for record in rehydrated) == manifest
    assert tuple(record.candidate_id for record in rehydrated) == tuple(
        item.candidate_id for item in manifest
    )
    assert tuple(record.evaluation.state for record in rehydrated) == (
        "available",
        "unavailable",
        "failed",
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value.pop("sample"),
        lambda value: value["work_manifest"][0].pop("product_mz"),
        lambda value: value["work_manifest"][0].__setitem__("candidate_id", "swapped"),
        lambda value: value["work_manifest"][0].__setitem__(
            "selection_subject_id", "swapped"
        ),
        lambda value: value["work_manifest"][0].__setitem__(
            "acquisition_source_id", "cross-acquisition"
        ),
        lambda value: value["work_manifest"][0].__setitem__(
            "trace_coordinate_id", "xic.trace-coordinate.v1:" + "0" * 64
        ),
        lambda value: value["work_manifest"][0].__setitem__(
            "acquired_trace_content_id",
            "xic.acquired-trace-content.v1:" + "0" * 64,
        ),
        lambda value: value["work_manifest"][0].__setitem__(
            "xic_mz", float(999.0).hex()
        ),
        lambda value: value["records"][0].__setitem__(
            "candidate_id", value["records"][1]["candidate_id"]
        ),
        lambda value: value["records"][0].__setitem__(
            "selection_subject_id", value["records"][1]["selection_subject_id"]
        ),
        lambda value: value["records"][0]["acquired_trace"]["coordinate"].__setitem__(
            "acquisition_source_id", "cross-acquisition"
        ),
        lambda value: value["records"][0]["acquired_trace"]["coordinate"][
            "request"
        ].__setitem__("mz", float(999.0).hex()),
        lambda value: value["records"][0]["acquired_trace"]["coordinate"].__setitem__(
            "trace_coordinate_id", "xic.trace-coordinate.v1:" + "0" * 64
        ),
        lambda value: value["records"][0]["acquired_trace"]["coordinate"].__setitem__(
            "trace_point_count", 1
        ),
        lambda value: value["records"][0]["acquired_trace"]["trace"][
            "intensity"
        ].__setitem__(0, float(999.0).hex()),
        lambda value: value["records"][0]["acquired_trace"].__setitem__(
            "acquired_trace_content_id",
            "xic.acquired-trace-content.v1:" + "0" * 64,
        ),
        lambda value: value["records"][0]["physical_evaluation"]["trace"][
            "raw"
        ].__setitem__(0, float(999.0).hex()),
        lambda value: value["records"][0]["physical_evaluation"]["evaluation"][
            "question"
        ].__setitem__("query_rt_min", float(1.1).hex()),
        lambda value: value["records"][0]["physical_evaluation"]["evaluation"][
            "answer"
        ].__setitem__("physical_answer_id", "physical-answer.v1:" + "0" * 64),
        lambda value: value["records"][0]["physical_evaluation"]["evaluation"][
            "facts"
        ].__setitem__("area_counts_seconds", float(1.0).hex()),
        lambda value: value["records"][0]["physical_evaluation"]["evaluation"][
            "integration"
        ].__setitem__("height_raw", float(1.0).hex()),
        lambda value: value["records"][0]["physical_evaluation"][
            "evaluation"
        ].__setitem__("state", "failed"),
        lambda value: value["records"][0]["physical_evaluation"]["evaluation"][
            "prepared"
        ].__setitem__("prepared_signal_id", "prepared-signal.v1:" + "0" * 64),
        lambda value: value["records"][0]["physical_evaluation"]["profile"].__setitem__(
            "profile_id", "signal-profile.v1:" + "0" * 64
        ),
        lambda value: value["run_spec"].__setitem__(
            "run_spec_id", "processing-run.v1:" + "0" * 64
        ),
    ],
)
def test_discovery_sidecar_rejects_tamper_even_after_json_reseal(
    tmp_path: Path,
    mutate,
) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    value = json.loads(
        serialize_discovery_physical_fact_sidecar(
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=manifest,
            records=records,
        )
    )
    mutate(value)

    with pytest.raises((TypeError, ValueError)):
        deserialize_discovery_physical_fact_sidecar(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            expected_run_spec=spec,
            expected_acquisition_input=acquisition_input,
            expected_work_manifest=manifest,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["records"].pop(),
        lambda value: (value["work_manifest"].pop(), value["records"].pop()),
        lambda value: value["records"].reverse(),
        lambda value: value["work_manifest"].reverse(),
        lambda value: value["records"].__setitem__(1, value["records"][0]),
        lambda value: value["work_manifest"].__setitem__(1, value["work_manifest"][0]),
    ],
)
def test_discovery_sidecar_rejects_deleted_duplicate_or_reordered_work(
    tmp_path: Path,
    mutate,
) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    value = json.loads(
        serialize_discovery_physical_fact_sidecar(
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=manifest,
            records=records,
        )
    )
    mutate(value)

    with pytest.raises((TypeError, ValueError)):
        deserialize_discovery_physical_fact_sidecar(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            expected_run_spec=spec,
            expected_acquisition_input=acquisition_input,
            expected_work_manifest=manifest,
        )


def test_discovery_sidecar_rejects_duplicate_json_field(tmp_path: Path) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    payload = serialize_discovery_physical_fact_sidecar(
        run_spec=spec,
        acquisition_input=acquisition_input,
        work_manifest=manifest,
        records=records,
    ).decode("utf-8")
    duplicate = payload.replace(
        '{"schema_version":"discovery_physical_fact_sidecar_v1",',
        '{"schema_version":"discovery_physical_fact_sidecar_v1",'
        '"schema_version":"discovery_physical_fact_sidecar_v1",',
        1,
    )

    with pytest.raises(ValueError, match="duplicate"):
        deserialize_discovery_physical_fact_sidecar(
            duplicate,
            expected_run_spec=spec,
            expected_acquisition_input=acquisition_input,
            expected_work_manifest=manifest,
        )


def test_discovery_sidecar_rejects_fully_resealed_different_run(tmp_path: Path) -> None:
    _, expected_spec, expected_input = _spec(tmp_path / "expected")
    expected_manifest, _ = _population(expected_spec, expected_input)
    _, other_spec, other_input = _spec(tmp_path / "other", profile_points=5)
    other_manifest, other_records = _population(other_spec, other_input)
    resealed = serialize_discovery_physical_fact_sidecar(
        run_spec=other_spec,
        acquisition_input=other_input,
        work_manifest=other_manifest,
        records=other_records,
    )

    with pytest.raises(ValueError, match="expected run"):
        deserialize_discovery_physical_fact_sidecar(
            resealed,
            expected_run_spec=expected_spec,
            expected_acquisition_input=expected_input,
            expected_work_manifest=expected_manifest,
        )


def test_discovery_sidecar_rejects_fully_resealed_replacement_trace_record(
    tmp_path: Path,
) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    manifest, records = _population(spec, acquisition_input)
    original = json.loads(
        serialize_discovery_physical_fact_sidecar(
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=manifest,
            records=records,
        )
    )
    changed_trace = _acquired_trace(
        acquisition_input,
        terminal="available",
        ordinal=0,
    )
    changed_trace.trace.intensity[0] = 999.0
    changed_work = replace(
        manifest[0],
        acquired_trace_content_id=acquired_trace_content_id(changed_trace),
    )
    changed_record = _record(
        changed_work,
        terminal="available",
        profile=spec.signal_processing_profile,
        acquired_trace=changed_trace,
    )
    resealed = json.loads(
        serialize_discovery_physical_fact_sidecar(
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=(changed_work, *manifest[1:]),
            records=(changed_record, *records[1:]),
        )
    )
    original["records"][0] = resealed["records"][0]

    with pytest.raises(ValueError):
        deserialize_discovery_physical_fact_sidecar(
            json.dumps(original, ensure_ascii=False, separators=(",", ":")),
            expected_run_spec=spec,
            expected_acquisition_input=acquisition_input,
            expected_work_manifest=manifest,
        )
