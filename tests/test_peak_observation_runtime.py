from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

import xic_extractor.acquisition_contract as acquisition_contract_module
from xic_extractor.acquisition_contract import (
    AcquisitionContractError,
    TraceCoordinate,
    TraceCoordinateKind,
    mint_trace_acquisition_capability,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.observation_batch import (
    PeakObservationBatch,
    PeakObservationBatchError,
    PeakObservationPlan,
    canonical_peak_observation_batch_bytes,
    produce_peak_observation_batch,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    ProcessingOwnerReferences,
    build_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)
from xic_extractor.xic_models import XICRequest, XICTrace


@dataclass(frozen=True, slots=True)
class _ObservationJob:
    owners: ProcessingOwnerReferences
    sample: ResolvedCanonicalSampleIdentity
    work_unit_key: str
    resource_key: str
    coordinate_kind: TraceCoordinateKind
    plans: tuple[PeakObservationPlan, ...]
    delay_sec: float = 0.0


@dataclass(frozen=True)
class _NoRawBatchAdapter:
    @property
    def raw_chromatogram_call_count(self) -> int:
        return 0

    def extract_xic_many(
        self, requests: tuple[XICRequest, ...]
    ) -> tuple[XICTrace, ...]:
        return tuple(_trace_for_request(request) for request in requests)

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        offset = int(request.mz - 500.0) * 10
        return 100 + offset, 104 + offset


@dataclass
class _RecordingNoRawBatchAdapter:
    extract_many_call_count: int = 0

    @property
    def raw_chromatogram_call_count(self) -> int:
        return 0

    def extract_xic_many(
        self, requests: tuple[XICRequest, ...]
    ) -> tuple[XICTrace, ...]:
        self.extract_many_call_count += 1
        return tuple(_trace_for_request(request) for request in requests)

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        offset = int(request.mz - 500.0) * 10
        return 100 + offset, 104 + offset


def _produce_batch(job: _ObservationJob) -> PeakObservationBatch:
    source = job.owners.request.acquisition_sources[0]
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace="synthetic.observation-runtime.no_raw",
        coordinate_kind=job.coordinate_kind,
    )
    return produce_peak_observation_batch(
        processing_owners=job.owners,
        sample=job.sample,
        capability=capability,
        plans=job.plans,
        adapter=_NoRawBatchAdapter(),
    )


def test_batch_receipt_is_stable_and_coordinate_truth_changes_identity(
    tmp_path: Path,
) -> None:
    owners, sample = _identity_fixture(tmp_path)
    native_job = _jobs(owners, sample)[0]
    first = _produce_batch(native_job)
    repeated = _produce_batch(native_job)
    rt_only = _produce_batch(
        replace(native_job, coordinate_kind=TraceCoordinateKind.RT_ONLY)
    )

    assert canonical_peak_observation_batch_bytes(first) == (
        canonical_peak_observation_batch_bytes(repeated)
    )
    assert canonical_peak_observation_batch_bytes(first) != (
        canonical_peak_observation_batch_bytes(rt_only)
    )
    with pytest.raises(PeakObservationBatchError, match="different processing"):
        replace(
            first,
            processing_attempt_id=("xic.processing-attempt:" + "0" * 64),
        )


def test_batch_production_rejects_selection_trace_length_mismatch(
    tmp_path: Path,
) -> None:
    owners, sample = _identity_fixture(tmp_path)
    job = _jobs(owners, sample)[0]
    bad_plan = replace(job.plans[0], selection_intensity=(1.0, 2.0))

    with pytest.raises(PeakObservationBatchError, match="trace length"):
        _produce_batch(
            replace(
                job,
                plans=(bad_plan,),
            )
        )


def test_batch_freezes_signal_and_detects_post_mint_mutation(tmp_path: Path) -> None:
    owners, sample = _identity_fixture(tmp_path)
    batch = _produce_batch(_jobs(owners, sample)[0])
    intensity = batch.acquisition.traces[0].trace.intensity

    assert intensity.flags.writeable is False
    with pytest.raises(ValueError):
        intensity[0] = 123.0
    intensity.setflags(write=True)
    intensity[0] = 123.0
    with pytest.raises(PeakObservationBatchError, match="changed after"):
        canonical_peak_observation_batch_bytes(batch)


def test_observation_batch_rejects_cross_sample_capability_before_acquisition(
    tmp_path: Path,
) -> None:
    owners, first, second = _two_sample_identity_fixture(tmp_path)
    second_binding = next(
        binding
        for binding in owners.request.acquisition_bindings
        if binding.adapter_input_key == second.lookup_token
    )
    second_source = next(
        source
        for source in owners.request.acquisition_sources
        if source.acquisition_source_id == second_binding.acquisition_source_id
    )
    capability = mint_trace_acquisition_capability(
        second_source,
        adapter_namespace="synthetic.observation-runtime.no_raw",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    adapter = _RecordingNoRawBatchAdapter()

    with pytest.raises(PeakObservationBatchError, match="request binding"):
        produce_peak_observation_batch(
            processing_owners=owners,
            sample=first,
            capability=capability,
            plans=(_plan(500.0, 500.0, 3300.0),),
            adapter=adapter,
        )

    assert first.lookup_token != second.lookup_token
    assert adapter.extract_many_call_count == 0


def test_acquisition_batch_rejects_coordinate_source_or_kind_substitution(
    tmp_path: Path,
) -> None:
    owners, sample = _identity_fixture(tmp_path)
    acquisition = _produce_batch(_jobs(owners, sample)[0]).acquisition
    acquired = acquisition.traces[0]
    other_path = tmp_path / "sample-b.raw"
    other_path.write_bytes(b"synthetic-no-raw-observation-runtime-other-source")
    other_source = build_acquisition_source_identity(
        other_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo.raw.ms_controller_1",
    )

    wrong_source = _replace_coordinate(
        acquired.coordinate,
        acquisition_source_id=other_source.acquisition_source_id,
    )
    with pytest.raises(AcquisitionContractError, match="capability source"):
        replace(
            acquisition,
            traces=(replace(acquired, coordinate=wrong_source),),
            logical_xic_request_count=1,
            trace_point_count=len(acquired.trace.rt),
        )

    wrong_kind = _replace_coordinate(
        acquired.coordinate,
        coordinate_kind=TraceCoordinateKind.CONVERSION_SCAN,
    )
    with pytest.raises(AcquisitionContractError, match="coordinate kind"):
        replace(
            acquisition,
            traces=(replace(acquired, coordinate=wrong_kind),),
            logical_xic_request_count=1,
            trace_point_count=len(acquired.trace.rt),
        )


def _identity_fixture(
    tmp_path: Path,
) -> tuple[ProcessingOwnerReferences, ResolvedCanonicalSampleIdentity]:
    source_path = tmp_path / "sample-a.raw"
    source_path.write_bytes(b"synthetic-no-raw-observation-runtime-observation")
    source = build_acquisition_source_identity(
        source_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo.raw.ms_controller_1",
    )
    request = build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key="sample-a",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="observation-runtime-observation-input",
        processing_algorithm_version="observation-runtime-observation-algorithm",
        processing_contract_version="observation-runtime-observation-contract",
        scientific_inputs={"fixture": "synthetic-no-raw-observation-runtime"},
    )
    attempt = issue_processing_attempt(request)
    snapshot = mint_sample_metadata_snapshot(
        (SampleMetadata(sample_name="Sample A", raw_stem="sample-a"),),
        identity_namespace="study:observation-runtime-observation",
    )
    sample = resolve_canonical_sample_identity(snapshot, "sample-a")
    owners = compose_processing_owner_references(
        request=request,
        attempt=attempt,
        sample_snapshot=snapshot,
        sample_resolutions=(sample,),
    )
    return owners, sample


def _two_sample_identity_fixture(
    tmp_path: Path,
) -> tuple[
    ProcessingOwnerReferences,
    ResolvedCanonicalSampleIdentity,
    ResolvedCanonicalSampleIdentity,
]:
    source_paths = (tmp_path / "sample-a.raw", tmp_path / "sample-b.raw")
    source_paths[0].write_bytes(b"synthetic-observation-runtime-source-a")
    source_paths[1].write_bytes(b"synthetic-observation-runtime-source-b")
    sources = tuple(
        sorted(
            (
                build_acquisition_source_identity(
                    path,
                    acquisition_format="thermo_raw",
                    observation_namespace="thermo.raw.ms_controller_1",
                )
                for path in source_paths
            ),
            key=lambda source: source.acquisition_source_id,
        )
    )
    source_by_content = {source.content_sha256: source for source in sources}
    source_a = source_by_content[
        hashlib.sha256(b"synthetic-observation-runtime-source-a").hexdigest()
    ]
    source_b = source_by_content[
        hashlib.sha256(b"synthetic-observation-runtime-source-b").hexdigest()
    ]
    bindings = tuple(
        sorted(
            (
                AcquisitionInputBinding(
                    acquisition_source_id=source_a.acquisition_source_id,
                    adapter_input_key="sample-a",
                    injection_order=1,
                ),
                AcquisitionInputBinding(
                    acquisition_source_id=source_b.acquisition_source_id,
                    adapter_input_key="sample-b",
                    injection_order=2,
                ),
            ),
            key=lambda binding: (
                binding.acquisition_source_id,
                binding.adapter_input_key,
                binding.injection_order,
            ),
        )
    )
    request = build_processing_request_identity(
        acquisition_sources=sources,
        acquisition_bindings=bindings,
        scientific_input_schema_version="observation-runtime-observation-input",
        processing_algorithm_version="observation-runtime-observation-algorithm",
        processing_contract_version="observation-runtime-observation-contract",
        scientific_inputs={
            "fixture": "synthetic-no-raw-observation-runtime-two-sample"
        },
    )
    attempt = issue_processing_attempt(request)
    snapshot = mint_sample_metadata_snapshot(
        (
            SampleMetadata(
                sample_name="Sample A",
                raw_stem="sample-a",
                injection_order=1,
            ),
            SampleMetadata(
                sample_name="Sample B",
                raw_stem="sample-b",
                injection_order=2,
            ),
        ),
        identity_namespace="study:observation-runtime-observation",
    )
    first = resolve_canonical_sample_identity(snapshot, "sample-a")
    second = resolve_canonical_sample_identity(snapshot, "sample-b")
    owners = compose_processing_owner_references(
        request=request,
        attempt=attempt,
        sample_snapshot=snapshot,
        sample_resolutions=(first, second),
    )
    return owners, first, second


def _jobs(
    owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> tuple[_ObservationJob, ...]:
    native_plans = (
        _plan(500.0, 500.0, 3300.0),
        _plan(501.0, 200.0, 1320.0),
    )
    rt_only_plans = (_plan(500.0, 500.0, 3300.0),)
    return (
        _ObservationJob(
            owners=owners,
            sample=sample,
            work_unit_key="sample:native",
            resource_key="process:sample:native",
            coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
            plans=native_plans,
        ),
        _ObservationJob(
            owners=owners,
            sample=sample,
            work_unit_key="sample:rt-only",
            resource_key="process:sample:rt-only",
            coordinate_kind=TraceCoordinateKind.RT_ONLY,
            plans=rt_only_plans,
        ),
    )


def _plan(mz: float, height: float, area: float) -> PeakObservationPlan:
    return PeakObservationPlan(
        request=XICRequest(mz=mz, rt_min=7.7, rt_max=8.3, ppm_tol=10.0),
        integration=IntegrationResult(
            rt_left_min=7.9,
            rt_apex_min=8.0,
            rt_right_min=8.1,
            raw_apex_rt_min=8.0,
            rt_width_min=0.2,
            height_raw=height,
            height_smoothed=height,
            area_raw_counts_seconds=area,
            integration_method="raw_trapezoid",
            boundary_sources=("candidate_interval",),
            raw_scan_indices=(1, 2, 3),
        ),
        resolver_mode="owner_supplied_characterization",
        proposal_sources=("owner_supplied_characterization",),
    )


def _trace_for_request(request: XICRequest) -> XICTrace:
    scale = 1.0 if request.mz == 500.0 else 0.4
    return XICTrace.from_arrays(
        (7.7, 7.9, 8.0, 8.1, 8.3),
        tuple(value * scale for value in (0.0, 50.0, 500.0, 50.0, 0.0)),
    )


def _replace_coordinate(
    coordinate: TraceCoordinate,
    *,
    acquisition_source_id: str | None = None,
    coordinate_kind: TraceCoordinateKind | None = None,
) -> TraceCoordinate:
    source_id = acquisition_source_id or coordinate.acquisition_source_id
    kind = coordinate_kind or coordinate.coordinate_kind
    basis = acquisition_contract_module._trace_coordinate_basis(
        capability_id=coordinate.capability_id,
        acquisition_source_id=source_id,
        request=coordinate.request,
        coordinate_kind=kind,
        first_scan=coordinate.first_scan,
        last_scan=coordinate.last_scan,
        trace_point_count=coordinate.trace_point_count,
    )
    return TraceCoordinate(
        trace_coordinate_id=acquisition_contract_module._typed_id(
            "xic.trace-coordinate:",
            basis,
        ),
        capability_id=coordinate.capability_id,
        acquisition_source_id=source_id,
        request=coordinate.request,
        coordinate_kind=kind,
        first_scan=coordinate.first_scan,
        last_scan=coordinate.last_scan,
        trace_point_count=coordinate.trace_point_count,
    )
