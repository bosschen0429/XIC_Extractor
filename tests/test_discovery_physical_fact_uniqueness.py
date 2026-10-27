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
    mint_trace_acquisition_capability,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.models import (
    DiscoveryPhysicalWorkItem,
    DiscoverySettings,
    NeutralLossProfile,
)
from xic_extractor.discovery.physical_fact_persistence import (
    DiscoveryPhysicalFactRecord,
    deserialize_discovery_physical_fact_sidecar,
    serialize_discovery_physical_fact_sidecar,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
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


def test_discovery_sidecar_accepts_candidate_id_collision_between_questions(
    tmp_path: Path,
) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    acquired = _acquired_trace(
        acquisition_input,
        terminal="available",
        ordinal=0,
    )
    first_work = replace(
        _work(
            0,
            acquisition_input,
            terminal="available",
            acquired_trace=acquired,
        ),
        precursor_mz=500.0000001,
    )
    second_work = replace(
        first_work,
        work_ordinal=1,
        precursor_mz=500.0000002,
    )
    manifest = (first_work, second_work)
    records = tuple(
        _record(
            work,
            terminal="available",
            profile=spec.signal_processing_profile,
            acquired_trace=acquired,
        )
        for work in manifest
    )

    assert first_work.candidate_id == second_work.candidate_id
    assert first_work.selection_subject_id != second_work.selection_subject_id

    payload = serialize_discovery_physical_fact_sidecar(
        run_spec=spec,
        acquisition_input=acquisition_input,
        work_manifest=manifest,
        records=records,
    )
    restored = deserialize_discovery_physical_fact_sidecar(
        payload,
        expected_run_spec=spec,
        expected_acquisition_input=acquisition_input,
        expected_work_manifest=manifest,
    )

    assert tuple(record.work_item for record in restored) == manifest


def test_discovery_sidecar_accepts_same_subject_with_different_xic_request(
    tmp_path: Path,
) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    first_acquired = _acquired_trace(
        acquisition_input,
        terminal="available",
        ordinal=0,
    )
    second_acquired = _acquired_trace(
        acquisition_input,
        terminal="available",
        ordinal=1,
    )
    first_work = _work(
        0,
        acquisition_input,
        terminal="available",
        acquired_trace=first_acquired,
    )
    second_work = replace(
        _work(
            1,
            acquisition_input,
            terminal="available",
            acquired_trace=second_acquired,
        ),
        best_ms2_scan_id=first_work.best_ms2_scan_id,
        precursor_mz=first_work.precursor_mz,
        product_mz=first_work.product_mz,
    )
    manifest = (first_work, second_work)
    records = (
        _record(
            first_work,
            terminal="available",
            profile=spec.signal_processing_profile,
            acquired_trace=first_acquired,
        ),
        _record(
            second_work,
            terminal="available",
            profile=spec.signal_processing_profile,
            acquired_trace=second_acquired,
        ),
    )

    assert first_work.selection_subject_id == second_work.selection_subject_id
    assert first_work.xic_mz != second_work.xic_mz

    payload = serialize_discovery_physical_fact_sidecar(
        run_spec=spec,
        acquisition_input=acquisition_input,
        work_manifest=manifest,
        records=records,
    )
    restored = deserialize_discovery_physical_fact_sidecar(
        payload,
        expected_run_spec=spec,
        expected_acquisition_input=acquisition_input,
        expected_work_manifest=manifest,
    )

    assert tuple(record.work_item for record in restored) == manifest


def test_discovery_sidecar_rejects_exact_duplicate_physical_questions(
    tmp_path: Path,
) -> None:
    _, spec, acquisition_input = _spec(tmp_path)
    acquired = _acquired_trace(
        acquisition_input,
        terminal="available",
        ordinal=0,
    )
    first_work = replace(
        _work(
            0,
            acquisition_input,
            terminal="available",
            acquired_trace=acquired,
        ),
        precursor_mz=500.0000001,
    )
    duplicate_work = replace(first_work, work_ordinal=1)
    duplicate_manifest = (first_work, duplicate_work)
    duplicate_records = tuple(
        _record(
            work,
            terminal="available",
            profile=spec.signal_processing_profile,
            acquired_trace=acquired,
        )
        for work in duplicate_manifest
    )

    with pytest.raises(ValueError, match="physical questions must be unique"):
        serialize_discovery_physical_fact_sidecar(
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=duplicate_manifest,
            records=duplicate_records,
        )

    distinct_work = replace(duplicate_work, precursor_mz=500.0000002)
    valid_manifest = (first_work, distinct_work)
    valid_records = (
        duplicate_records[0],
        _record(
            distinct_work,
            terminal="available",
            profile=spec.signal_processing_profile,
            acquired_trace=acquired,
        ),
    )
    value = json.loads(
        serialize_discovery_physical_fact_sidecar(
            run_spec=spec,
            acquisition_input=acquisition_input,
            work_manifest=valid_manifest,
            records=valid_records,
        )
    )
    for field in (
        "best_ms2_scan_id",
        "precursor_mz",
        "product_mz",
        "candidate_id",
        "selection_subject_id",
    ):
        value["work_manifest"][1][field] = value["work_manifest"][0][field]

    with pytest.raises(ValueError, match="physical questions must be unique"):
        deserialize_discovery_physical_fact_sidecar(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            expected_run_spec=spec,
            expected_acquisition_input=acquisition_input,
            expected_work_manifest=valid_manifest,
        )
