from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from tests.test_discovery_ms1_backfill import (
    _group,
    _multi_tag_settings,
    _OwnerBoundBatchRaw,
    _seed,
)
from tests.test_discovery_ms1_backfill import (
    _peak_config as _backfill_peak_config,
)
from tests.test_discovery_ms1_backfill import (
    _settings as _backfill_settings,
)
from tests.test_discovery_pipeline import (
    _OwnerBoundFakeRawHandle,
    _peak_config,
    _read_csv,
    _scan_event,
    _settings,
)
from xic_extractor.discovery.csv_writer import build_discovery_review_note
from xic_extractor.discovery.models import DiscoveryPhysicalWorkItem
from xic_extractor.discovery.ms1_backfill import (
    _backfill_ms1_candidates_with_acquisition,
)
from xic_extractor.discovery.physical_fact_persistence import (
    read_discovery_physical_fact_sidecar,
    write_discovery_physical_fact_sidecar,
)
from xic_extractor.discovery.pipeline import run_discovery
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.execution_runtime_owner import derive_runtime_acquisition_metrics
from xic_extractor.peak_detection.evidence_facts import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.observations import mint_peak_observation_ref
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    build_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.sample_metadata import (
    SampleMetadata,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)
from xic_extractor.xic_models import XICTrace

_TEST_RUN_SPEC_ID = "processing-run.v1:" + ("0" * 64)


class _CadenceBoundRaw:
    def __init__(self, source, rt: np.ndarray, intensity: np.ndarray) -> None:
        self.acquisition_source = source
        self.rt = rt
        self.intensity = intensity
        self.raw_chromatogram_call_count = 0
        self.request_batches = []

    def extract_xic_many(self, requests):
        self.request_batches.append(tuple(requests))
        self.raw_chromatogram_call_count += 1
        traces = []
        for request in requests:
            mask = (self.rt >= request.rt_min) & (self.rt <= request.rt_max)
            traces.append(
                XICTrace.from_arrays(self.rt[mask], self.intensity[mask])
            )
        return tuple(traces)

    def scan_window_for_request(self, request):
        return (int(request.rt_min * 100), int(request.rt_max * 100))


def test_spec_bound_single_run_uses_compiled_acquisition_and_writes_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "Sample.raw"
    raw_path.write_bytes(b"compiled acquisition")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    handle_source = spec.acquisition_inputs[0].acquisition_source
    trace_rt = np.linspace(7.20, 8.40, 25)
    raw = _OwnerBoundFakeRawHandle(
        handle_source,
        [_scan_event(scan_number=101, rt=7.80)],
        rt=trace_rt,
        intensity=100.0 + 500.0 * np.exp(-0.5 * ((trace_rt - 7.80) / 0.08) ** 2),
    )
    from xic_extractor.peak_detection import evidence_facts as physical_owner

    physical_calls = {"prepare": 0, "evaluate": 0}
    real_prepare = physical_owner.prepare_physical_signal
    real_evaluate = physical_owner.evaluate_physical_peak

    def counted_prepare(*args, **kwargs):
        physical_calls["prepare"] += 1
        return real_prepare(*args, **kwargs)

    def counted_evaluate(*args, **kwargs):
        physical_calls["evaluate"] += 1
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.prepare_physical_signal",
        counted_prepare,
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.evaluate_physical_peak",
        counted_evaluate,
    )
    monkeypatch.setattr(physical_owner, "prepare_physical_signal", counted_prepare)
    monkeypatch.setattr(physical_owner, "evaluate_physical_peak", counted_evaluate)
    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline._acquisition_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("spec-bound run fingerprinted the RAW twice")
        ),
    )
    captured_manifest: tuple[DiscoveryPhysicalWorkItem, ...] = ()

    def capture_sidecar(path, **kwargs):
        nonlocal captured_manifest
        captured_manifest = tuple(kwargs["work_manifest"])
        return write_discovery_physical_fact_sidecar(path, **kwargs)

    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline.write_discovery_physical_fact_sidecar",
        capture_sidecar,
    )

    outputs = run_discovery(
        raw_path,
        output_dir=tmp_path / "out",
        settings=settings,
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda path, dll_dir: raw,
        run_spec=spec,
    )

    assert outputs.physical_facts_json == (
        tmp_path / "out" / "discovery_physical_facts.v1.json"
    )
    assert outputs.physical_facts_json is not None
    assert physical_calls == {"prepare": 1, "evaluate": 1}
    expected_work_manifest = captured_manifest
    assert len(expected_work_manifest) == 1
    records = read_discovery_physical_fact_sidecar(
        outputs.physical_facts_json,
        expected_run_spec=spec,
        expected_acquisition_input=spec.acquisition_inputs[0],
        expected_work_manifest=expected_work_manifest,
    )
    assert len(records) == 1
    assert (
        records[0].candidate_id == _read_csv(outputs.candidates_csv)[0]["candidate_id"]
    )
    assert records[0].evaluation.prepared.profile_id == spec.profile_id
    assert physical_calls == {"prepare": 2, "evaluate": 2}
    assert raw.raw_chromatogram_call_count == 1


@pytest.mark.parametrize("binding", ["missing", "crossed"])
def test_spec_bound_handle_closes_source_before_ms2_or_xic(
    tmp_path: Path,
    binding: str,
) -> None:
    raw_path = tmp_path / "Sample.raw"
    raw_path.write_bytes(b"compiled acquisition")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,), discovery_settings=settings
    )
    if binding == "missing":
        source = None
    else:
        other = tmp_path / "Other.raw"
        other.write_bytes(b"different acquisition")
        source = build_acquisition_source_identity(
            other,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )

    class CountingHandle:
        acquisition_source = source
        raw_chromatogram_call_count = 0

        def __init__(self) -> None:
            self.ms2_calls = 0
            self.xic_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_ms2_scans(self, *_args):
            self.ms2_calls += 1
            return iter(())

        def extract_xic_many(self, requests):
            self.xic_calls += 1
            return tuple(XICTrace.empty() for _request in requests)

        def scan_window_for_request(self, _request):
            return (0, 0)

    handle = CountingHandle()

    with pytest.raises(ValueError, match="lacks|crosses"):
        run_discovery(
            raw_path,
            output_dir=tmp_path / "out",
            settings=settings,
            peak_config=_peak_config(tmp_path),
            raw_opener=lambda *_args: handle,
            run_spec=spec,
        )

    assert handle.ms2_calls == 0
    assert handle.xic_calls == 0


def test_sparse_trace_outside_actual_context_persists_truthful_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "Sample.raw"
    raw_path.write_bytes(b"sparse actual context")
    settings = _settings()
    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=settings,
    )
    raw = _OwnerBoundFakeRawHandle(
        spec.acquisition_inputs[0].acquisition_source,
        [_scan_event(scan_number=101, rt=7.80)],
        rt=np.asarray([7.90]),
        intensity=np.asarray([100.0]),
    )
    captured_manifest: tuple[DiscoveryPhysicalWorkItem, ...] = ()

    def capture_sidecar(path, **kwargs):
        nonlocal captured_manifest
        captured_manifest = tuple(kwargs["work_manifest"])
        return write_discovery_physical_fact_sidecar(path, **kwargs)

    monkeypatch.setattr(
        "xic_extractor.discovery.pipeline.write_discovery_physical_fact_sidecar",
        capture_sidecar,
    )

    outputs = run_discovery(
        raw_path,
        output_dir=tmp_path / "out",
        settings=settings,
        peak_config=_peak_config(tmp_path),
        raw_opener=lambda *_args: raw,
        run_spec=spec,
    )

    row = _read_csv(outputs.candidates_csv)[0]
    assert row["discovery_candidate_state"] == "review_only_orphan_nl"
    assert row["ms1_peak_found"] == "FALSE"
    assert row["ms1_area"] == ""
    assert row["ms1_trace_quality"] == "physical_unavailable"
    assert "physical MS1 evaluation unavailable" in row["reason"]
    assert len(captured_manifest) == 1
    work = captured_manifest[0]
    assert work.query_rt_min == 7.80
    assert work.context_rt_start_min == work.context_rt_end_min == 7.90
    assert work.xic_rt_min <= work.query_rt_min <= work.xic_rt_max
    assert outputs.physical_facts_json is not None
    records = read_discovery_physical_fact_sidecar(
        outputs.physical_facts_json,
        expected_run_spec=spec,
        expected_acquisition_input=spec.acquisition_inputs[0],
        expected_work_manifest=captured_manifest,
    )
    assert len(records) == 1
    assert records[0].evaluation.state == "unavailable"
    assert records[0].evaluation.facts is None
    assert raw.raw_chromatogram_call_count == 1


def test_short_discovery_request_reacquires_enough_points_for_physical_owner(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    full_rt = np.arange(11.55, 13.25, 0.10, dtype=float)
    full_intensity = 100.0 + 10_000.0 * np.exp(-0.5 * ((full_rt - 12.15) / 0.08) ** 2)

    raw = _CadenceBoundRaw(source, full_rt, full_intensity)
    profile = SignalProcessingProfile(window_points=15)
    group = _group(
        (
            _seed(scan_number=10, rt=12.063),
            replace(
                _seed(scan_number=11, rt=12.181),
                precursor_mz_basis="product_plus_neutral_loss",
            ),
        )
    )
    assert group.precursor_mz_basis == "mixed"

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (group,),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )

    assert len(result.measurements.acquisition_batches) == 2
    assert result.measurements.initial_request_count == 1
    assert result.measurements.reacquisition_request_count == 1
    first, reacquired = result.measurements.acquisition_batches
    assert first.traces[0].coordinate.trace_point_count < profile.window_points
    assert reacquired.traces[0].coordinate.trace_point_count >= profile.window_points
    assert result.physical_evaluations[0].evaluation.state == "available"
    assert (
        result.physical_evaluations[0].evaluation.prepared.profile_id
        == profile.profile_id
    )
    candidate = result.candidates[0]
    assert candidate.ms1_peak_found is True
    assert candidate.ms1_apex_rt == pytest.approx(12.15, abs=0.04)
    final_request = reacquired.traces[0].coordinate.request
    assert candidate.ms1_search_rt_min == final_request.rt_min
    assert candidate.ms1_search_rt_max == final_request.rt_max


def test_single_seed_discovery_acquires_complete_physical_context(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    full_rt = np.arange(11.50, 12.80, 0.04, dtype=float)
    full_intensity = 100.0 + 10_000.0 * np.exp(
        -0.5 * ((full_rt - 12.15) / 0.15) ** 2
    )

    raw = _CadenceBoundRaw(source, full_rt, full_intensity)
    profile = SignalProcessingProfile(window_points=15)
    group = _group((_seed(scan_number=10, rt=12.063),))

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (group,),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )

    assert result.measurements.reacquisition_request_count == 0
    candidate = result.candidates[0]
    assert candidate.ms1_peak_found is True
    assert candidate.ms1_apex_rt == pytest.approx(12.15, abs=0.04)
    assert candidate.ms1_peak_rt_end is not None
    assert candidate.ms1_search_rt_max - candidate.ms1_peak_rt_end >= 0.04
    full_trace_evaluation = evaluate_physical_peak(
        prepare_physical_signal(full_rt, full_intensity, profile=profile),
        selection_subject_id="full-trace-oracle",
        query_rt_min=12.063,
        request_context_rt_start_min=float(full_rt[0]),
        request_context_rt_end_min=float(full_rt[-1]),
    )
    assert full_trace_evaluation.facts is not None
    assert candidate.ms1_peak_rt_start == pytest.approx(
        full_trace_evaluation.facts.left_rt_min,
        abs=0.04,
    )
    assert candidate.ms1_peak_rt_end == pytest.approx(
        full_trace_evaluation.facts.right_rt_min,
        abs=0.04,
    )
    assert candidate.ms1_area == pytest.approx(
        full_trace_evaluation.facts.area_counts_seconds,
        rel=0.10,
    )


def test_product_inferred_single_seed_uses_widened_initial_context(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    full_rt = np.arange(11.50, 12.80, 0.04, dtype=float)
    full_intensity = 100.0 + 10_000.0 * np.exp(
        -0.5 * ((full_rt - 12.15) / 0.15) ** 2
    )
    raw = _CadenceBoundRaw(source, full_rt, full_intensity)
    profile = SignalProcessingProfile(window_points=15)
    group = _group(
        (
            replace(
                _seed(
                    scan_number=10,
                    rt=12.063,
                    product_intensity=1_000_000.0,
                ),
                precursor_mz_basis="product_plus_neutral_loss",
            ),
        )
    )
    assert group.precursor_mz_basis == "product_plus_neutral_loss"

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (group,),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )

    assert result.measurements.reacquisition_request_count == 0
    candidate = result.candidates[0]
    assert candidate.ms1_peak_found is True
    assert candidate.ms1_trace_quality == "clean"
    assert candidate.ms1_search_rt_min == pytest.approx(11.713)
    assert candidate.ms1_search_rt_max == pytest.approx(12.413)
    assert candidate.ms1_apex_rt == pytest.approx(12.14)


def test_strong_product_inferred_seed_group_uses_widened_initial_context(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    full_rt = np.arange(11.50, 12.80, 0.04, dtype=float)
    full_intensity = 100.0 + 10_000.0 * np.exp(
        -0.5 * ((full_rt - 12.15) / 0.15) ** 2
    )
    raw = _CadenceBoundRaw(source, full_rt, full_intensity)
    profile = SignalProcessingProfile(window_points=15)
    group = _group(
        tuple(
            replace(
                _seed(
                    scan_number=scan_number,
                    rt=rt,
                    product_intensity=1_000_000.0,
                ),
                precursor_mz_basis="product_plus_neutral_loss",
            )
            for scan_number, rt in ((10, 12.063), (11, 12.100))
        )
    )
    assert group.precursor_mz_basis == "product_plus_neutral_loss"

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (group,),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )

    assert len(result.measurements.acquisition_batches) == 1
    assert result.measurements.reacquisition_request_count == 0
    initial = result.measurements.acquisition_batches[0]
    assert initial.traces[0].coordinate.trace_point_count >= profile.window_points
    initial_request = initial.traces[0].coordinate.request
    assert initial_request.rt_min == pytest.approx(11.713)
    assert initial_request.rt_max == pytest.approx(12.450)
    work = result.physical_work_manifest[0]
    assert work.query_rt_min == pytest.approx(12.063)
    assert work.xic_rt_min == initial_request.rt_min
    assert work.xic_rt_max == initial_request.rt_max
    assert work.xic_rt_min <= work.context_rt_start_min <= work.query_rt_min
    assert work.query_rt_min <= work.context_rt_end_min <= work.xic_rt_max
    candidate = result.candidates[0]
    assert candidate.ms1_peak_found is True
    assert candidate.ms1_apex_rt == pytest.approx(12.15, abs=0.04)


def test_spec_bound_physical_owner_reuses_preparation_without_extra_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    first = _group((_seed(scan_number=10, rt=7.80),))
    duplicate_trace = replace(
        first,
        seeds=(_seed(scan_number=20, rt=7.80),),
    )
    raw = _OwnerBoundBatchRaw(source)
    preparation_count = 0

    def _count_prepare(*args, **kwargs):
        nonlocal preparation_count
        preparation_count += 1
        return prepare_physical_signal(*args, **kwargs)

    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.prepare_physical_signal",
        _count_prepare,
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.find_peak_and_area",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("spec-bound route used legacy peak detection")
        ),
    )

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (first, duplicate_trace),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
    )

    metrics = derive_runtime_acquisition_metrics(
        result.measurements.acquisition_batches
    )
    assert preparation_count == 1
    assert len(result.physical_evaluations) == 2
    assert {
        record.evaluation.prepared.prepared_signal_id
        for record in result.physical_evaluations
    } == {result.physical_evaluations[0].evaluation.prepared.prepared_signal_id}
    assert metrics.logical_xic_request_count == 2
    assert metrics.xic_batch_count == 1
    assert metrics.adapter_chromatogram_call_count == 1
    assert [len(batch) for batch in raw.request_batches] == [2]


def test_alignment_builds_owner_from_discovery_binding_without_resolver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.ownership import (
        build_sample_local_owners_from_discovery_bindings,
    )

    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    raw = _OwnerBoundBatchRaw(source)
    discovery = _backfill_ms1_candidates_with_acquisition(
        raw,
        (_group((_seed(scan_number=10, rt=7.80),)),),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
    )
    discovery_xic_calls = raw.raw_chromatogram_call_count
    monkeypatch.setattr(
        "xic_extractor.alignment.ownership._default_peak_resolver",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("canonical binding called the Alignment resolver")
        ),
    )

    ownership = build_sample_local_owners_from_discovery_bindings(
        discovery.physical_bindings,
        alignment_config=AlignmentConfig(),
    )

    assert len(ownership.owners) == 1
    assert ownership.owners[0].selected_integration is (
        discovery.physical_bindings[0].physical_evaluation.integration
    )
    assert raw.raw_chromatogram_call_count == discovery_xic_calls

    binding = discovery.physical_bindings[0]
    with pytest.raises(ValueError, match="crosses terminal state"):
        replace(
            binding,
            physical_fact_record=replace(
                binding.physical_fact_record,
                evaluation=replace(
                    binding.physical_evaluation,
                    reason="tampered_terminal_reason",
                ),
            ),
        )


def test_discovery_receipt_and_physical_binding_share_cross_sample_subject(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        group_member_subject_from_discovery_physical_selection,
    )

    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    discovery = _backfill_ms1_candidates_with_acquisition(
        _OwnerBoundBatchRaw(source),
        (_group((_seed(scan_number=10, rt=7.80),)),),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
    )
    binding = discovery.physical_bindings[0]
    request = build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key=binding.candidate.sample_stem,
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="cross-sample-binding-test-v1",
        processing_algorithm_version="cross-sample-binding-test-v1",
        processing_contract_version="cross-sample-binding-test-v1",
        scientific_inputs={"candidate_id": binding.candidate.candidate_id},
    )
    snapshot = mint_sample_metadata_snapshot(
        (
            SampleMetadata(
                sample_name=binding.candidate.sample_stem,
                raw_stem=binding.candidate.sample_stem,
                injection_order=1,
            ),
        ),
        identity_namespace="cross-sample-binding-test-v1",
    )
    sample = resolve_canonical_sample_identity(snapshot, binding.candidate.sample_stem)
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=snapshot,
        sample_resolutions=(sample,),
    )
    acquired_trace = binding.physical_fact_record.acquired_trace
    trace_request = acquired_trace.coordinate.request
    raw_rt = np.asarray(acquired_trace.trace.rt, dtype=float)
    raw_intensity = np.asarray(acquired_trace.trace.intensity, dtype=float)
    raw_apex_index = int(np.argmax(raw_intensity))
    observation = mint_peak_observation_ref(
        processing_owners=owners,
        sample=sample,
        trace=Trace.from_arrays(
            sample_name=sample.metadata.raw_stem,
            mz=trace_request.mz,
            rt=acquired_trace.trace.rt,
            intensity=acquired_trace.trace.intensity,
            rt_min=trace_request.rt_min,
            rt_max=trace_request.rt_max,
            ppm_tol=trace_request.ppm_tol,
            source="discovery_physical_binding_test",
            acquisition_source_id=source.acquisition_source_id,
        ),
        integration=IntegrationResult(
            rt_left_min=float(raw_rt[0]),
            rt_apex_min=float(raw_rt[raw_apex_index]),
            rt_right_min=float(raw_rt[-1]),
            raw_apex_rt_min=float(raw_rt[raw_apex_index]),
            rt_width_min=float(raw_rt[-1] - raw_rt[0]),
            height_raw=float(raw_intensity[raw_apex_index]),
            height_smoothed=float(raw_intensity[raw_apex_index]),
            area_raw_counts_seconds=float(np.trapezoid(raw_intensity, raw_rt) * 60.0),
            raw_scan_indices=tuple(range(len(raw_rt))),
        ),
        resolver_mode="discovery_physical_binding_test",
        proposal_sources=("discovery_physical_binding_test",),
    )

    receipt_subject = group_member_subject_from_discovery_physical_selection(
        processing_owners=owners,
        sample=sample,
        candidate=binding.candidate,
        observation=observation,
    )
    bound_subject = group_member_subject_from_discovery_physical_selection(
        processing_owners=owners,
        sample=sample,
        candidate=binding.candidate,
        physical_binding=binding,
    )

    assert bound_subject.member_subject_id == receipt_subject.member_subject_id
    assert bound_subject.basis_bytes == receipt_subject.basis_bytes
    assert bound_subject.subject_id == binding.selection_subject_id


def test_alignment_review_only_discovery_binding_does_not_build_owner(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.ownership import (
        build_sample_local_owners_from_discovery_bindings,
    )

    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    discovery = _backfill_ms1_candidates_with_acquisition(
        _OwnerBoundBatchRaw(source),
        (_group((_seed(scan_number=10, rt=7.80),)),),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
    )
    binding = discovery.physical_bindings[0]
    review_only_binding = replace(
        binding,
        candidate=replace(
            binding.candidate,
            discovery_candidate_state="review_only_ambiguous_coisolation",
        ),
    )

    ownership = build_sample_local_owners_from_discovery_bindings(
        (review_only_binding,),
        alignment_config=AlignmentConfig(),
    )

    assert ownership.owners == ()
    assert ownership.assignments == ()
    assert ownership.ambiguous_records == ()


def test_physical_fact_owner_rejects_cross_acquisition_reassembly(
    tmp_path: Path,
) -> None:
    current_dir = tmp_path / "current"
    foreign_dir = tmp_path / "foreign"
    current_dir.mkdir()
    foreign_dir.mkdir()
    current_path = current_dir / "sample.raw"
    foreign_path = foreign_dir / "sample.raw"
    current_path.write_bytes(b"current-acquisition")
    foreign_path.write_bytes(b"foreign-acquisition")
    current_source = build_acquisition_source_identity(
        current_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    foreign_source = build_acquisition_source_identity(
        foreign_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    group = _group((_seed(scan_number=10, rt=7.80),))
    profile = SignalProcessingProfile(window_points=3)
    current = _backfill_ms1_candidates_with_acquisition(
        _OwnerBoundBatchRaw(current_source),
        (group,),
        acquisition_source=current_source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )
    foreign_raw = _OwnerBoundBatchRaw(foreign_source)
    foreign_raw.intensity = np.asarray([5.0, 500.0, 5.0], dtype=float)
    foreign = _backfill_ms1_candidates_with_acquisition(
        foreign_raw,
        (group,),
        acquisition_source=foreign_source,
        run_spec_id="processing-run.v1:" + ("1" * 64),
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )
    current_record = current.physical_evaluations[0]
    foreign_record = foreign.physical_evaluations[0]

    with pytest.raises(ValueError, match="crosses acquired trace content"):
        replace(current_record, evaluation=foreign_record.evaluation)
    with pytest.raises(ValueError, match="crosses acquisition record"):
        replace(
            current.physical_bindings[0],
            physical_fact_record=foreign_record,
        )


@pytest.mark.parametrize(
    ("rt", "intensity", "physical_state", "physical_reason"),
    [
        (
            np.asarray([7.8]),
            np.asarray([10.0]),
            "unavailable",
            "insufficient_points_for_profile",
        ),
        (
            np.asarray([7.8, 7.7, 7.9]),
            np.asarray([1.0, 2.0, 1.0]),
            "failed",
            "non_monotonic_rt",
        ),
    ],
)
def test_spec_bound_terminal_physical_truth_remains_distinct_hold_state(
    tmp_path: Path,
    rt: np.ndarray,
    intensity: np.ndarray,
    physical_state: str,
    physical_reason: str,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    raw = _OwnerBoundBatchRaw(source)
    raw.rt = rt
    raw.intensity = intensity

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (_group((_seed(scan_number=10, rt=7.80),)),),
        acquisition_source=source,
        run_spec_id=_TEST_RUN_SPEC_ID,
        settings=_backfill_settings(),
        peak_config=_backfill_peak_config(),
        signal_processing_profile=SignalProcessingProfile(window_points=3),
    )

    candidate = result.candidates[0]
    assert result.physical_evaluations[0].evaluation.state == physical_state
    assert result.physical_evaluations[0].evaluation.reason == physical_reason
    assert candidate.discovery_candidate_state == "review_only_orphan_nl"
    assert candidate.review_priority == "LOW"
    assert candidate.reason == (
        f"physical MS1 evaluation {physical_state}: {physical_reason}"
    )
    assert candidate.ms1_support == "missing"
    assert candidate.ms1_trace_quality == f"physical_{physical_state}"
    assert build_discovery_review_note(candidate) == (
        f"physical MS1 {physical_state}: {physical_reason}; missing RT"
    )
    assert candidate.ms1_peak_found is False
    assert candidate.ms1_apex_rt is None
    assert candidate.ms1_area is None
    assert candidate.ms1_height is None
    assert candidate.ms1_scan_support_score is None
    assert candidate.discovery_candidate_state not in {
        "ms1_feature_nl_supported",
        "ms1_feature_nl_rescued",
        "rejected_noise_or_outside_rt",
    }


@pytest.mark.parametrize("same_question", [True, False])
def test_physical_fact_sharing_uses_exact_owner_question(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    same_question: bool,
) -> None:
    raw_path = tmp_path / "TumorBC2312_DNA.raw"
    raw_path.write_bytes(b"owner-bound-discovery")
    settings = _multi_tag_settings()
    profile = SignalProcessingProfile(window_points=3)
    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=settings,
        signal_processing_profile=profile,
    )
    source = spec.acquisition_inputs[0].acquisition_source
    first = replace(
        _group((_seed(scan_number=10, rt=7.80, precursor_mz=500.0000001),)),
        neutral_loss_tag="dR",
        configured_neutral_loss_da=116.047344,
        matched_tag_names=("dR",),
    )
    second = (
        replace(
            first,
            neutral_loss_tag="R",
            configured_neutral_loss_da=132.0423,
            matched_tag_names=("R",),
        )
        if same_question
        else replace(
            _group((_seed(scan_number=10, rt=7.80, precursor_mz=500.0000002),)),
            neutral_loss_tag="dR",
            configured_neutral_loss_da=116.047344,
            matched_tag_names=("dR",),
        )
    )
    raw = _OwnerBoundBatchRaw(source)
    prepare = Mock(wraps=prepare_physical_signal)
    evaluate = Mock(wraps=evaluate_physical_peak)
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.prepare_physical_signal", prepare
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.ms1_backfill.evaluate_physical_peak", evaluate
    )

    result = _backfill_ms1_candidates_with_acquisition(
        raw,
        (first, second),
        acquisition_source=source,
        run_spec_id=spec.run_spec_id,
        settings=settings,
        peak_config=_backfill_peak_config(),
        signal_processing_profile=profile,
    )

    expected_count = 1 if same_question else 2
    assert [len(batch) for batch in raw.request_batches] == [expected_count]
    assert prepare.call_count == 1
    assert evaluate.call_count == expected_count
    assert len(result.physical_work_manifest) == expected_count
    assert len(result.physical_evaluations) == expected_count
    assert tuple(work.work_ordinal for work in result.physical_work_manifest) == tuple(
        range(expected_count)
    )
    if same_question:
        candidate = result.candidates[0]
        assert candidate.matched_tag_names == ("dR", "R")
        assert candidate.review_priority == "HIGH"
        assert candidate.discovery_candidate_state == "ms1_feature_nl_supported"
    else:
        first_work, second_work = result.physical_work_manifest
        assert first_work.candidate_id == second_work.candidate_id
        assert first_work.selection_subject_id != second_work.selection_subject_id
        path = tmp_path / "discovery_physical_facts.v1.json"
        write_discovery_physical_fact_sidecar(
            path,
            run_spec=spec,
            acquisition_input=spec.acquisition_inputs[0],
            work_manifest=result.physical_work_manifest,
            records=result.physical_evaluations,
        )

        restored = read_discovery_physical_fact_sidecar(
            path,
            expected_run_spec=spec,
            expected_acquisition_input=spec.acquisition_inputs[0],
            expected_work_manifest=result.physical_work_manifest,
        )

        assert tuple(record.work_item for record in restored) == (
            result.physical_work_manifest
        )
        assert raw.raw_chromatogram_call_count == 1
