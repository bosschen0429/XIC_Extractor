from __future__ import annotations

import csv
import json
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from multiprocessing import get_context
from pathlib import Path

import pytest

import xic_extractor.acquisition_contract as acquisition_contract_module
import xic_extractor.package0_successor_comparator as comparator_module
from tests.runtime_observability_fixtures import (
    build_runtime_observability_ledger,
)
from xic_extractor.acquisition_contract import (
    AcquisitionContractError,
    TraceCoordinate,
    TraceCoordinateKind,
    mint_trace_acquisition_capability,
)
from xic_extractor.alignment.runtime_adapter import (
    AlignmentRuntimeAdapterError,
    AlignmentRuntimeApplicationResult,
    AlignmentRuntimeProcessResult,
    execute_alignment_process_runtime,
    mint_alignment_process_result_artifact,
)
from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.execution_runtime import (
    AttemptLifecycleEventKind,
    AttemptLifecycleHistory,
    AttemptRuntime,
    WorkUnitSettlement,
    WorkUnitTerminalOutcome,
    append_attempt_lifecycle_event,
    preflight_required_work_manifest,
    settle_work_unit,
    start_attempt_runtime,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeArtifactDisposition,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    begin_attempt_finalization,
    canonical_coordinator_terminal_record_bytes,
    mint_supervisor_finalization,
    propose_attempt_terminal,
    register_runtime_artifact,
    reserve_runtime_resource,
    settle_runtime_resource,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.extraction.targeted_runtime_adapter import (
    TargetedProcessResourceReport,
    TargetedProcessWorkerResultEnvelope,
    TargetedRuntimeAdapterError,
    TargetedRuntimeWorkerBatch,
    mint_targeted_process_result_ref,
    settle_targeted_process_worker_results,
)
from xic_extractor.extraction.targeted_service import (
    TargetedApplicationService,
    build_targeted_application_request,
)
from xic_extractor.package0_successor_comparator import (
    Package0ComparatorError,
    mint_package1_difference_observation,
    validate_package1_difference_manifest,
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
    ProcessingAttemptIdentity,
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

DIFFERENCE_PACKET = Path(
    "docs/superpowers/validation/package1_runtime_observation_difference_v1"
)
FROZEN_COMPARATOR = Path(
    "docs/superpowers/validation/package0_smallest_strangler_contract_v1/contract.json"
)


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


@dataclass(frozen=True)
class _ObservationJob:
    owners: ProcessingOwnerReferences
    sample: ResolvedCanonicalSampleIdentity
    work_unit_key: str
    resource_key: str
    coordinate_kind: TraceCoordinateKind
    plans: tuple[PeakObservationPlan, ...]
    delay_sec: float = 0.0


@dataclass(frozen=True)
class _AlignmentReplayJob:
    job: _ObservationJob
    fail: bool


def _produce_batch(job: _ObservationJob) -> PeakObservationBatch:
    if job.delay_sec:
        time.sleep(job.delay_sec)
    source = job.owners.request.acquisition_sources[0]
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace="synthetic.package1.no_raw",
        coordinate_kind=job.coordinate_kind,
    )
    return produce_peak_observation_batch(
        processing_owners=job.owners,
        sample=job.sample,
        capability=capability,
        plans=job.plans,
        adapter=_NoRawBatchAdapter(),
    )


def _run_targeted_job(
    job: _ObservationJob,
) -> tuple[
    TargetedProcessWorkerResultEnvelope,
    TargetedProcessResourceReport,
]:
    batch = _produce_batch(job)
    payload = canonical_peak_observation_batch_bytes(batch)
    reference = mint_targeted_process_result_ref(
        processing_attempt=job.owners.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=payload,
        upstream_artifacts=batch.observation_artifacts,
    )
    return (
        TargetedProcessWorkerResultEnvelope(
            attempt=job.owners.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=reference,
            peak_observation_batch=batch,
        ),
        TargetedProcessResourceReport(
            attempt=job.owners.attempt,
            resource_key=job.resource_key,
            open_outcome=RuntimeResourceOpenOutcome.OPENED,
            cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
        ),
    )


def _run_alignment_job(job: _ObservationJob) -> AlignmentRuntimeProcessResult:
    batch = _produce_batch(job)
    payload = canonical_peak_observation_batch_bytes(batch)
    artifact = mint_alignment_process_result_artifact(
        processing_attempt=job.owners.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=payload,
        upstream_artifacts=batch.observation_artifacts,
    )
    return AlignmentRuntimeProcessResult(
        settlement=WorkUnitSettlement(
            attempt=job.owners.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=artifact,
        ),
        resource_key=job.resource_key,
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
        runtime_artifacts=(artifact, *batch.observation_artifacts),
        peak_observation_batch=batch,
    )


def _alignment_error(
    job: _ObservationJob,
    _error: Exception,
) -> AlignmentRuntimeProcessResult:
    return AlignmentRuntimeProcessResult(
        settlement=WorkUnitSettlement(
            attempt=job.owners.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.FAILED,
        ),
        resource_key=job.resource_key,
        open_outcome=RuntimeResourceOpenOutcome.NOT_OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )


def _run_alignment_replay_job(
    replay: _AlignmentReplayJob,
) -> AlignmentRuntimeProcessResult:
    if replay.fail:
        return _alignment_error(replay.job, RuntimeError("replayed failure"))
    return _run_alignment_job(replay.job)


def _alignment_replay_error(
    replay: _AlignmentReplayJob,
    error: Exception,
) -> AlignmentRuntimeProcessResult:
    return _alignment_error(replay.job, error)


def test_targeted_and_alignment_close_same_spawned_observation_batches(
    tmp_path: Path,
) -> None:
    owners, sample = _identity_fixture(tmp_path)
    jobs = _jobs(owners, sample)
    runtime = _runtime(owners.attempt, tuple(job.work_unit_key for job in jobs))
    reservations = _reservations(owners.attempt, jobs)

    targeted_serial = _run_targeted_service(
        runtime,
        jobs,
        reservations,
        max_workers=1,
        marker_path=tmp_path / "targeted-serial.runtime.json",
    )
    targeted_reordered = _run_targeted_service(
        runtime,
        tuple(reversed(jobs)),
        reservations,
        max_workers=2,
        marker_path=tmp_path / "targeted-reordered.runtime.json",
    )
    alignment_serial = execute_alignment_process_runtime(
        runtime,
        jobs,
        worker=_run_alignment_job,
        error_factory=_alignment_error,
        max_workers=1,
        resource_reservations=reservations,
        observability_ledger=_ledger(runtime, reservations),
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "alignment-serial.runtime.json",
            "alignment-observation-runtime:test",
        ),
    )
    alignment_reordered = execute_alignment_process_runtime(
        runtime,
        tuple(reversed(jobs)),
        worker=_run_alignment_job,
        error_factory=_alignment_error,
        max_workers=2,
        resource_reservations=reservations,
        observability_ledger=_ledger(runtime, reservations),
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "alignment-reordered.runtime.json",
            "alignment-observation-runtime:test",
        ),
    )

    targeted_batches = targeted_serial.settlement.successful_observation_batches
    reordered_targeted_batches = (
        targeted_reordered.settlement.successful_observation_batches
    )
    alignment_batches = alignment_serial.successful_observation_batches
    reordered_alignment_batches = alignment_reordered.successful_observation_batches
    assert _canonical_batches(targeted_batches) == _canonical_batches(
        reordered_targeted_batches
    )
    assert _canonical_batches(targeted_batches) == _canonical_batches(
        alignment_batches
    )
    assert _canonical_batches(alignment_batches) == _canonical_batches(
        reordered_alignment_batches
    )
    assert tuple(key for key, _batch in targeted_batches) == (
        "sample:native",
        "sample:rt-only",
    )
    native = targeted_batches[0][1]
    rt_only = targeted_batches[1][1]
    assert native.acquisition.logical_xic_request_count == 2
    assert native.acquisition.xic_batch_count == 1
    assert native.acquisition.adapter_chromatogram_call_count == 0
    assert native.acquisition.trace_point_count == 10
    assert all(
        item.coordinate.first_scan is not None
        for item in native.acquisition.traces
    )
    assert all(
        item.coordinate.first_scan is None
        for item in rt_only.acquisition.traces
    )
    assert canonical_coordinator_terminal_record_bytes(
        targeted_serial.coordinator_record
    ) == canonical_coordinator_terminal_record_bytes(
        targeted_reordered.coordinator_record
    )
    assert canonical_coordinator_terminal_record_bytes(
        alignment_serial.coordinator_record
    ) == canonical_coordinator_terminal_record_bytes(
        alignment_reordered.coordinator_record
    )
    assert _offered_count(targeted_serial.finalization_record) == 5
    assert _offered_count(alignment_serial.finalization_record) == 5


def test_runtime_carriers_reject_unclosed_observation_lineage(tmp_path: Path) -> None:
    owners, sample = _identity_fixture(tmp_path)
    job = _jobs(owners, sample)[0]
    batch = _produce_batch(job)
    payload = canonical_peak_observation_batch_bytes(batch)
    unclosed_targeted = mint_targeted_process_result_ref(
        processing_attempt=owners.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=payload,
    )
    with pytest.raises(TargetedRuntimeAdapterError, match="exact observation"):
        TargetedProcessWorkerResultEnvelope(
            attempt=owners.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=unclosed_targeted,
            peak_observation_batch=batch,
        )

    unclosed_alignment = mint_alignment_process_result_artifact(
        processing_attempt=owners.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=payload,
    )
    with pytest.raises(AlignmentRuntimeAdapterError, match="exact observation"):
        AlignmentRuntimeProcessResult(
            settlement=WorkUnitSettlement(
                attempt=owners.attempt,
                work_unit_key=job.work_unit_key,
                outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                owner_result_ref=unclosed_alignment,
            ),
            resource_key=job.resource_key,
            open_outcome=RuntimeResourceOpenOutcome.OPENED,
            cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
            runtime_artifacts=(unclosed_alignment, *batch.observation_artifacts),
            peak_observation_batch=batch,
        )

    foreign_owner = mint_targeted_process_result_ref(
        processing_attempt=owners.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=payload,
        upstream_artifacts=batch.observation_artifacts,
    ).artifact
    with pytest.raises(AlignmentRuntimeAdapterError, match="Alignment-owned"):
        AlignmentRuntimeProcessResult(
            settlement=WorkUnitSettlement(
                attempt=owners.attempt,
                work_unit_key=job.work_unit_key,
                outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                owner_result_ref=foreign_owner,
            ),
            resource_key=job.resource_key,
            open_outcome=RuntimeResourceOpenOutcome.OPENED,
            cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
            runtime_artifacts=(foreign_owner, *batch.observation_artifacts),
            peak_observation_batch=batch,
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
            processing_attempt_id=(
                "xic.processing-attempt.v1:" + "0" * 64
            ),
        )


def test_alignment_public_result_rejects_unclosed_observation_upstreams(
    tmp_path: Path,
) -> None:
    owners, sample = _identity_fixture(tmp_path)
    job = _jobs(owners, sample)[0]
    batch = _produce_batch(job)
    payload = canonical_peak_observation_batch_bytes(batch)
    unclosed = mint_alignment_process_result_artifact(
        processing_attempt=owners.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=payload,
    )
    runtime = settle_work_unit(
        _runtime(owners.attempt, (job.work_unit_key,)),
        work_unit_key=job.work_unit_key,
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=unclosed,
    )
    reservation = _reservations(owners.attempt, (job,))[0]
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        reservation,
    )
    for artifact in (unclosed, *batch.observation_artifacts):
        state = register_runtime_artifact(
            state,
            work_unit_key=job.work_unit_key,
            artifact=artifact,
        )
    state = settle_runtime_resource(
        state,
        resource_key=job.resource_key,
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )
    coordinator = propose_attempt_terminal(state)

    with pytest.raises(AlignmentRuntimeAdapterError, match="exact observation"):
        AlignmentRuntimeApplicationResult(
            runtime=runtime,
            coordinator_record=coordinator,
            finalization_record=mint_supervisor_finalization(
                coordinator,
                observability_ledger=_ledger(runtime, (reservation,)),
            ),
            successful_observation_batches=((job.work_unit_key, batch),),
        )


def test_batch_production_rejects_selection_trace_length_mismatch(
    tmp_path: Path,
) -> None:
    owners, sample = _identity_fixture(tmp_path)
    job = _jobs(owners, sample)[0]
    bad_plan = replace(job.plans[0], selection_intensity=(1.0, 2.0))

    with pytest.raises(PeakObservationBatchError, match="trace length"):
        _produce_batch(replace(job, plans=(bad_plan,)))


def test_runtime_consumers_coalesce_identical_spawned_result_replay(
    tmp_path: Path,
) -> None:
    owners, sample = _identity_fixture(tmp_path)
    job = _jobs(owners, sample)[0]
    runtime = _runtime(owners.attempt, (job.work_unit_key,))
    reservations = _reservations(owners.attempt, (job,))

    targeted = _run_targeted_service(
        runtime,
        (job, job),
        reservations,
        max_workers=2,
        marker_path=tmp_path / "targeted-replay.runtime.json",
    )
    alignment = execute_alignment_process_runtime(
        runtime,
        (job, job),
        worker=_run_alignment_job,
        error_factory=_alignment_error,
        max_workers=2,
        resource_reservations=reservations,
        observability_ledger=_ledger(runtime, reservations),
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "alignment-replay.runtime.json",
            "alignment-observation-replay:test",
        ),
    )

    assert len(targeted.settlement.successful_observation_batches) == 1
    assert len(alignment.successful_observation_batches) == 1
    assert _offered_count(targeted.finalization_record) == 3
    assert _offered_count(alignment.finalization_record) == 3


def test_replay_coalescing_keeps_late_carrier_and_hides_conflicting_success(
    tmp_path: Path,
) -> None:
    owners, sample = _identity_fixture(tmp_path)
    job = _jobs(owners, sample)[0]
    batch = _produce_batch(job)
    payload = canonical_peak_observation_batch_bytes(batch)
    reference = mint_targeted_process_result_ref(
        processing_attempt=owners.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=payload,
        upstream_artifacts=batch.observation_artifacts,
    )
    without_carrier = TargetedProcessWorkerResultEnvelope(
        attempt=owners.attempt,
        work_unit_key=job.work_unit_key,
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=reference,
    )
    with_carrier = replace(without_carrier, peak_observation_batch=batch)
    for envelopes in (
        (without_carrier, with_carrier),
        (with_carrier, without_carrier),
    ):
        settled = settle_targeted_process_worker_results(
            _runtime(owners.attempt, (job.work_unit_key,)),
            envelopes,
        )
        assert settled.successful_observation_batches == (
            (job.work_unit_key, batch),
        )

    targeted_conflict = settle_targeted_process_worker_results(
        _runtime(owners.attempt, (job.work_unit_key,)),
        (
            with_carrier,
            TargetedProcessWorkerResultEnvelope(
                attempt=owners.attempt,
                work_unit_key=job.work_unit_key,
                outcome=WorkUnitTerminalOutcome.FAILED,
                failure_message="late conflicting failure",
            ),
        ),
    )
    assert targeted_conflict.runtime.integrity_failure is not None
    assert targeted_conflict.successful_observation_batches == ()

    for index, replay_jobs in enumerate(
        (
            (
                _AlignmentReplayJob(job=job, fail=True),
                _AlignmentReplayJob(job=job, fail=False),
            ),
            (
                _AlignmentReplayJob(job=job, fail=False),
                _AlignmentReplayJob(job=job, fail=True),
            ),
        )
    ):
        conflict_runtime = _runtime(owners.attempt, (job.work_unit_key,))
        conflict_reservations = _reservations(owners.attempt, (job,))
        conflicting = execute_alignment_process_runtime(
            conflict_runtime,
            replay_jobs,
            worker=_run_alignment_replay_job,
            error_factory=_alignment_replay_error,
            max_workers=1,
            resource_reservations=conflict_reservations,
            observability_ledger=_ledger(
                conflict_runtime,
                conflict_reservations,
            ),
            recovery_marker=OwnerPathRuntimeMarker(
                tmp_path / f"alignment-conflict-{index}.runtime.json",
                "alignment-observation-conflict:test",
            ),
        )
        assert conflicting.runtime.integrity_failure is not None
        assert conflicting.successful_observation_batches == ()


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


def test_acquisition_batch_rejects_coordinate_source_or_kind_substitution(
    tmp_path: Path,
) -> None:
    owners, sample = _identity_fixture(tmp_path)
    acquisition = _produce_batch(_jobs(owners, sample)[0]).acquisition
    acquired = acquisition.traces[0]
    other_path = tmp_path / "sample-b.raw"
    other_path.write_bytes(b"synthetic-no-raw-package1-other-source")
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


def test_package1_difference_manifest_reuses_frozen_exact_zero_comparator(
    tmp_path: Path,
) -> None:
    contract = json.loads(FROZEN_COMPARATOR.read_text(encoding="utf-8"))
    with (DIFFERENCE_PACKET / "difference_manifest.tsv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    owners, sample = _identity_fixture(tmp_path)
    job = _jobs(owners, sample)[0]
    runtime = _runtime(owners.attempt, (job.work_unit_key,))
    reservations = _reservations(owners.attempt, (job,))
    legacy_calls: list[str] = []
    before = _product_surface_paths(tmp_path)
    targeted_result = _run_targeted_service(
        runtime,
        (job,),
        reservations,
        max_workers=1,
        marker_path=tmp_path / "targeted-difference.runtime.json",
        legacy_call_log=legacy_calls,
    )
    alignment_result = execute_alignment_process_runtime(
        runtime,
        (job,),
        worker=_run_alignment_job,
        error_factory=_alignment_error,
        max_workers=1,
        resource_reservations=reservations,
        observability_ledger=_ledger(runtime, reservations),
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "alignment-difference.runtime.json",
            "alignment-observation-difference:test",
        ),
    )
    emitted = tuple(sorted(_product_surface_paths(tmp_path) - before))
    observation = mint_package1_difference_observation(
        targeted=targeted_result,
        alignment=alignment_result,
        targeted_legacy_runner_call_count=len(legacy_calls),
        successor_product_output_paths=emitted,
    )

    digest = validate_package1_difference_manifest(
        contract,
        rows,
        observation=observation,
    )
    assert len(digest) == 64
    tampered = [dict(row) for row in rows]
    tampered[1]["new_value"] = "sha256:" + "F" * 64
    with pytest.raises(Package0ComparatorError, match="frozen exact-zero"):
        validate_package1_difference_manifest(
            contract,
            tampered,
            observation=observation,
        )

    reset_contract = json.loads(json.dumps(contract))
    reset_contract["canonical_output_inventory_sha256"] = "F" * 64
    reset_digest = comparator_module._package1_frozen_surface_digest(
        reset_contract
    )
    consistently_reset = [dict(row) for row in rows]
    consistently_reset[1]["old_value"] = f"sha256:{reset_digest}"
    consistently_reset[1]["new_value"] = f"sha256:{reset_digest}"
    with pytest.raises(Package0ComparatorError, match="immutable baseline"):
        validate_package1_difference_manifest(
            reset_contract,
            consistently_reset,
            observation=replace(
                observation,
                frozen_surface_sha256=reset_digest,
            ),
        )


def _run_targeted_service(
    runtime: AttemptRuntime,
    jobs: tuple[_ObservationJob, ...],
    reservations: tuple[RuntimeResourceReservation, ...],
    *,
    max_workers: int,
    marker_path: Path,
    legacy_call_log: list[str] | None = None,
):
    def legacy_runner(*_args, **_kwargs):
        if legacy_call_log is not None:
            legacy_call_log.append("legacy_runner")
        raise AssertionError(
            "observation runtime must not invoke the legacy writer path"
        )

    def lifecycle_runner(_request, _state):
        with ProcessPoolExecutor(
            max_workers=max_workers,
            mp_context=get_context("spawn"),
        ) as executor:
            futures = tuple(
                executor.submit(_run_targeted_job, job) for job in jobs
            )
            results = tuple(future.result(timeout=15) for future in futures)
        envelopes = tuple(envelope for envelope, _report in results)
        return TargetedRuntimeWorkerBatch(
            result_envelopes=envelopes,
            resource_reports=tuple(report for _envelope, report in results),
            runtime_artifact_refs=tuple(
                envelope.owner_result_ref
                for envelope in envelopes
                if envelope.owner_result_ref is not None
            ),
        )

    service = TargetedApplicationService(
        legacy_runner,
        runtime_lifecycle_runner=lifecycle_runner,
        runtime_recovery_marker=OwnerPathRuntimeMarker(
            marker_path,
            "targeted-observation-runtime:test",
        ),
    )
    return service.execute_runtime_lifecycle(
        build_targeted_application_request(_config(), [_target()]),
        runtime,
        resource_reservations=reservations,
        observability_ledger=_ledger(runtime, reservations),
    )


def _ledger(
    runtime: AttemptRuntime,
    reservations: tuple[RuntimeResourceReservation, ...],
):
    return build_runtime_observability_ledger(
        runtime.manifest.attempt,
        work_unit_keys=runtime.manifest.work_unit_keys,
        worker_keys=tuple(
            dict.fromkeys(
                reservation.worker_key for reservation in reservations
            )
        ),
    )


def _identity_fixture(
    tmp_path: Path,
) -> tuple[ProcessingOwnerReferences, ResolvedCanonicalSampleIdentity]:
    source_path = tmp_path / "sample-a.raw"
    source_path.write_bytes(b"synthetic-no-raw-package1-observation")
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
        scientific_input_schema_version="package1-observation-input-v1",
        processing_algorithm_version="package1-observation-algorithm-v1",
        processing_contract_version="package1-observation-contract-v1",
        scientific_inputs={"fixture": "synthetic-no-raw-package1"},
    )
    attempt = issue_processing_attempt(request)
    snapshot = mint_sample_metadata_snapshot(
        (SampleMetadata(sample_name="Sample A", raw_stem="sample-a"),),
        identity_namespace="study:package1-observation",
    )
    sample = resolve_canonical_sample_identity(snapshot, "sample-a")
    owners = compose_processing_owner_references(
        request=request,
        attempt=attempt,
        sample_snapshot=snapshot,
        sample_resolutions=(sample,),
    )
    return owners, sample


def _jobs(
    owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> tuple[_ObservationJob, ...]:
    return (
        _ObservationJob(
            owners=owners,
            sample=sample,
            work_unit_key="sample:native",
            resource_key="process:sample:native",
            coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
            plans=(_plan(500.0, 500.0, 3300.0), _plan(501.0, 200.0, 1320.0)),
            delay_sec=0.03,
        ),
        _ObservationJob(
            owners=owners,
            sample=sample,
            work_unit_key="sample:rt-only",
            resource_key="process:sample:rt-only",
            coordinate_kind=TraceCoordinateKind.RT_ONLY,
            plans=(_plan(500.0, 500.0, 3300.0),),
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


def _runtime(
    attempt: ProcessingAttemptIdentity,
    work_unit_keys: tuple[str, ...],
) -> AttemptRuntime:
    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    return start_attempt_runtime(
        preflight_required_work_manifest(
            queued,
            attempt=attempt,
            work_unit_keys=work_unit_keys,
            owner_completeness_attestation="package1-observation-runtime:v1",
        )
    )


def _reservations(
    attempt: ProcessingAttemptIdentity,
    jobs: tuple[_ObservationJob, ...],
) -> tuple[RuntimeResourceReservation, ...]:
    return tuple(
        RuntimeResourceReservation(
            attempt=attempt,
            resource_key=job.resource_key,
            worker_key=f"worker:{index}",
            source_reference=f"no-raw:{job.work_unit_key}",
            compatibility_token="package1-observation-runtime:v1",
        )
        for index, job in enumerate(jobs)
    )


def _canonical_batches(
    batches: tuple[tuple[str, PeakObservationBatch], ...],
) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (key, canonical_peak_observation_batch_bytes(batch))
        for key, batch in batches
    )


def _product_surface_paths(root: Path) -> set[str]:
    product_suffixes = {".csv", ".tsv", ".xls", ".xlsx"}
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in product_suffixes
    }


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
            "xic.trace-coordinate.v1:",
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


def _offered_count(finalization_record) -> int:
    dispositions = tuple(
        disposition
        for _artifact_id, disposition in finalization_record.artifact_dispositions
    )
    assert set(dispositions) == {RuntimeArtifactDisposition.OFFERED_TO_OWNER}
    return len(dispositions)


def _config() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("data"),
        dll_dir=Path("dll"),
        output_csv=Path("output/xic_results.csv"),
        diagnostics_csv=Path("output/xic_diagnostics.csv"),
        smooth_window=9,
        smooth_polyorder=2,
        peak_rel_height=0.02,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _target() -> Target:
    return Target(
        label="Analyte",
        mz=250.0,
        rt_min=1.0,
        rt_max=2.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0,
        nl_ppm_warn=20.0,
        nl_ppm_max=40.0,
        is_istd=False,
        istd_pair="",
    )
