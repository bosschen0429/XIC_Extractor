from __future__ import annotations

import gc
import json
import weakref
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from tests.runtime_observability_fixtures import build_runtime_observability_ledger
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.runtime_telemetry_fixtures import (
    mint_test_process_result_ref as mint_targeted_process_result_ref,
)
from tests.test_acquisition_contract import _NoRawBatchAdapter, _source
from xic_extractor.acquisition_contract import (
    TraceCoordinateKind,
    _mint_raw_acquisition_session_receipt,
    acquire_trace_batch,
    mint_raw_acquisition_session_capability,
    mint_trace_acquisition_capability,
)
from xic_extractor.execution._finalization import (
    AttemptFinalizationState,
    RuntimeFinalizationContractError,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    canonical_coordinator_terminal_record_bytes,
    record_runtime_resource_cleanup,
    record_runtime_resource_open,
    register_runtime_artifact,
)
from xic_extractor.execution._lifecycle import (
    AttemptLifecycleContractError,
    AttemptLifecycleEventKind,
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitTerminalOutcome,
    settle_work_unit,
)
from xic_extractor.execution._observability import (
    RuntimeMetricAvailability,
    _index_event_offsets,
    canonical_runtime_observability_ledger_bytes,
)
from xic_extractor.execution.attempt import (
    RuntimeAcquisitionMetrics,
    RuntimeAcquisitionReceipt,
    RuntimeArtifactIoSample,
    RuntimeArtifactRegistrationReceipt,
    RuntimeAttemptAggregate,
    RuntimeCancellationRequest,
    RuntimeExecutionPhase,
    RuntimeExecutionPhaseError,
    RuntimeFinalizationReceipt,
    RuntimePeakFactCostMetrics,
    RuntimePeakFactIdentityStatus,
    RuntimePeakFactOperation,
    RuntimePeakFactWorkReceipt,
    RuntimeRawOpenMetrics,
    RuntimeResourceSettlementReceipt,
    RuntimeTelemetrySamples,
    RuntimeWorkerBatchReceipt,
    _apply_runtime_worker_batch,
    _derive_runtime_observability_ledger,
    _event_offset,
    _finalize_runtime_attempt,
    _mint_runtime_attempt_aggregate,
    _prepare_runtime_attempt,
    compact_runtime_acquisition_receipt,
    derive_runtime_acquisition_metrics,
    derive_runtime_peak_fact_cost_metrics,
    derive_runtime_raw_open_metrics,
    execute_runtime_attempt,
    mint_work_unit_settlement,
    start_runtime_attempt,
)
from xic_extractor.execution.recovery import OwnerPathRuntimeMarker
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    ProcessingAttemptIdentity,
    build_acquisition_source_identity,
    build_processing_request_identity,
    issue_processing_attempt,
)


def test_runtime_peak_fact_operation_vocabulary_is_exact() -> None:
    assert tuple(operation.value for operation in RuntimePeakFactOperation) == (
        "trace_evaluation",
        "asls_baseline",
        "gaussian_smoothing",
        "segment_enumeration",
        "integration",
        "normalization",
        "raw_descriptor_projection",
        "region_audit_projection",
    )


def test_runtime_event_offsets_preserve_scan_values_and_exactly_one_errors(
    tmp_path: Path,
) -> None:
    ledger = build_runtime_observability_ledger(
        _attempt(tmp_path), work_unit_keys=("raw:a", "raw:b"), worker_keys=()
    )
    queried = next(
        event for event in ledger.events if event.kind == "work_unit_started"
    )
    unqueried = next(
        event for event in ledger.events if event.kind == "work_unit_submitted"
    )
    for events in (
        ledger.events,
        tuple(event for event in ledger.events if event is not queried),
        (*ledger.events, queried),
        (*ledger.events, unqueried),
    ):
        offsets = _index_event_offsets(events)
        for kind, key in (
            ("work_unit_started", "raw:a"),
            ("work_unit_started", "raw:b"),
            ("terminal_outcome", "attempt"),
            ("work_unit_started", "missing"),
        ):
            expected = tuple(
                event.monotonic_offset_ns
                for event in events
                if event.kind == kind and event.subject_key == key
            )
            if len(expected) == 1:
                assert _event_offset(offsets, kind, key) == expected[0]
            else:
                with pytest.raises(
                    RuntimeFinalizationContractError,
                    match="owner-derived elapsed metric lacks one correlated event",
                ):
                    _event_offset(offsets, kind, key)


def test_runtime_owner_derives_raw_open_only_from_matching_unique_receipt(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"raw-open-owner")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    capability = mint_raw_acquisition_session_capability(source)
    receipt = _mint_raw_acquisition_session_receipt(capability)

    measured = derive_runtime_raw_open_metrics(capability, (receipt,))
    unavailable = derive_runtime_raw_open_metrics(capability, ())

    assert measured.raw_open_count == 1
    assert measured.unavailable_reason == ""
    assert unavailable.raw_open_count is None
    assert unavailable.unavailable_reason
    with pytest.raises(RuntimeFinalizationContractError, match="duplicate"):
        derive_runtime_raw_open_metrics(capability, (receipt, receipt))

    foreign = mint_raw_acquisition_session_capability(source)
    with pytest.raises(RuntimeFinalizationContractError, match="capability"):
        derive_runtime_raw_open_metrics(foreign, (receipt,))

    with pytest.raises(
        RuntimeFinalizationContractError,
        match="minted by the runtime owner",
    ):
        RuntimeRawOpenMetrics(
            raw_open_count=999,
            unavailable_reason="",
            _mint_token=object(),
        )


def test_runtime_owner_derives_peak_fact_cost_from_typed_work_and_telemetry(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"peak-fact-runtime-owner")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    capability = mint_raw_acquisition_session_capability(source)
    raw_receipt = _mint_raw_acquisition_session_receipt(capability)
    work = (
        RuntimePeakFactWorkReceipt(
            work_unit_key="sealed:1",
            sample_stem="sample-a",
            operations=(
                RuntimePeakFactOperation.TRACE_EVALUATION,
                RuntimePeakFactOperation.ASLS_BASELINE,
                RuntimePeakFactOperation.GAUSSIAN_SMOOTHING,
                RuntimePeakFactOperation.SEGMENT_ENUMERATION,
                RuntimePeakFactOperation.INTEGRATION,
                RuntimePeakFactOperation.NORMALIZATION,
                RuntimePeakFactOperation.RAW_DESCRIPTOR_PROJECTION,
                RuntimePeakFactOperation.REGION_AUDIT_PROJECTION,
            ),
            identity_status=RuntimePeakFactIdentityStatus.SEALED,
            owner_request_identity="owner-request:1",
            group_hypothesis_id="GROUP:1",
            acquisition_source=source,
            prepared_signal_id="prepared-signal:" + "1" * 64,
            elapsed_seconds=0.25,
        ),
        RuntimePeakFactWorkReceipt(
            work_unit_key="unsealed:2",
            sample_stem="sample-a",
            operations=(RuntimePeakFactOperation.TRACE_EVALUATION,),
            identity_status=RuntimePeakFactIdentityStatus.UNAVAILABLE,
        ),
    )

    metrics = derive_runtime_peak_fact_cost_metrics(
        raw_session_capability=capability,
        raw_open_receipts=(raw_receipt,),
        work_receipts=work,
        artifact_io=RuntimeArtifactIoSample(
            read_count=0,
            read_bytes=0,
            write_count=0,
            write_bytes=0,
        ),
    )

    assert metrics.raw_open_count == 1
    assert metrics.trace_evaluation_count == 2
    assert metrics.smoothing_count == 1
    assert metrics.asls_baseline_count == 1
    assert metrics.segment_enumeration_count == 1
    assert metrics.integration_count == 1
    assert metrics.normalization_count == 1
    assert metrics.raw_descriptor_projection_count == 1
    assert metrics.region_audit_projection_count == 1
    assert metrics.artifact_round_trip_count == 0
    assert metrics.sealed_observation_count == 1
    assert metrics.identity_unavailable_count == 1
    assert metrics.elapsed_seconds == pytest.approx(0.25)
    assert metrics.measurement_source == "execution_runtime_owner"
    assert metrics.count_scope == "owner_backfill_worker_peak_fact_runtime"

    with pytest.raises(
        RuntimeFinalizationContractError,
        match="requires prepared_signal_id",
    ):
        replace(work[0], prepared_signal_id=None)

    with pytest.raises(RuntimeFinalizationContractError, match="duplicate"):
        derive_runtime_peak_fact_cost_metrics(
            raw_session_capability=capability,
            raw_open_receipts=(raw_receipt,),
            work_receipts=(work[0], work[0]),
            artifact_io=None,
        )
    foreign_path = tmp_path / "foreign.raw"
    foreign_path.write_bytes(b"foreign-peak-fact-runtime-owner")
    foreign_source = build_acquisition_source_identity(
        foreign_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    with pytest.raises(RuntimeFinalizationContractError, match="RAW session"):
        derive_runtime_peak_fact_cost_metrics(
            raw_session_capability=capability,
            raw_open_receipts=(raw_receipt,),
            work_receipts=(
                replace(
                    work[0],
                    work_unit_key="sealed:foreign",
                    acquisition_source=foreign_source,
                ),
            ),
            artifact_io=None,
        )
    with pytest.raises(RuntimeFinalizationContractError, match="trace evaluation"):
        RuntimePeakFactWorkReceipt(
            work_unit_key="unsealed-without-work",
            sample_stem="sample-a",
            operations=(),
            identity_status=RuntimePeakFactIdentityStatus.UNAVAILABLE,
        )
    with pytest.raises(RuntimeFinalizationContractError, match="at most once"):
        RuntimePeakFactWorkReceipt(
            work_unit_key="duplicate-asls",
            sample_stem="sample-a",
            operations=(
                RuntimePeakFactOperation.TRACE_EVALUATION,
                RuntimePeakFactOperation.ASLS_BASELINE,
                RuntimePeakFactOperation.ASLS_BASELINE,
            ),
            identity_status=RuntimePeakFactIdentityStatus.SEALED,
            owner_request_identity="owner-request:duplicate-asls",
            group_hypothesis_id="GROUP:duplicate-asls",
            acquisition_source=source,
        )
    with pytest.raises(RuntimeFinalizationContractError, match="canonical order"):
        RuntimePeakFactWorkReceipt(
            work_unit_key="out-of-order",
            sample_stem="sample-a",
            operations=(
                RuntimePeakFactOperation.TRACE_EVALUATION,
                RuntimePeakFactOperation.GAUSSIAN_SMOOTHING,
                RuntimePeakFactOperation.ASLS_BASELINE,
            ),
            identity_status=RuntimePeakFactIdentityStatus.SEALED,
            owner_request_identity="owner-request:out-of-order",
            group_hypothesis_id="GROUP:out-of-order",
            acquisition_source=source,
        )
    with pytest.raises(RuntimeFinalizationContractError, match="finite and positive"):
        RuntimePeakFactWorkReceipt(
            work_unit_key="non-finite",
            sample_stem="sample-a",
            operations=(
                RuntimePeakFactOperation.TRACE_EVALUATION,
                RuntimePeakFactOperation.ASLS_BASELINE,
                RuntimePeakFactOperation.GAUSSIAN_SMOOTHING,
            ),
            identity_status=RuntimePeakFactIdentityStatus.SEALED,
            owner_request_identity="owner-request:non-finite",
            group_hypothesis_id="GROUP:non-finite",
            acquisition_source=source,
            prepared_signal_id="prepared-signal:" + "2" * 64,
            elapsed_seconds=float("nan"),
        )
    with pytest.raises(
        RuntimeFinalizationContractError,
        match="minted by the runtime owner",
    ):
        RuntimePeakFactCostMetrics(
            raw_open_count=1,
            raw_open_unavailable_reason="",
            trace_evaluation_count=2,
            asls_baseline_count=1,
            smoothing_count=1,
            segment_enumeration_count=1,
            integration_count=1,
            normalization_count=1,
            raw_descriptor_projection_count=1,
            region_audit_projection_count=1,
            artifact_round_trip_count=0,
            artifact_round_trip_unavailable_reason="",
            sealed_observation_count=1,
            identity_unavailable_count=1,
            elapsed_seconds=0.25,
            elapsed_unavailable_reason="",
            _mint_token=object(),
        )


def test_runtime_owner_counts_preparation_once_per_prepared_signal(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "shared.raw"
    raw_path.write_bytes(b"shared-prepared-signal")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    shared_prepared_signal_id = "prepared-signal:" + "a" * 64
    operations = (
        RuntimePeakFactOperation.TRACE_EVALUATION,
        RuntimePeakFactOperation.ASLS_BASELINE,
        RuntimePeakFactOperation.GAUSSIAN_SMOOTHING,
        RuntimePeakFactOperation.SEGMENT_ENUMERATION,
        RuntimePeakFactOperation.INTEGRATION,
        RuntimePeakFactOperation.NORMALIZATION,
        RuntimePeakFactOperation.RAW_DESCRIPTOR_PROJECTION,
        RuntimePeakFactOperation.REGION_AUDIT_PROJECTION,
    )
    receipts = tuple(
        RuntimePeakFactWorkReceipt(
            work_unit_key=f"sealed:{index}",
            sample_stem="sample-a",
            operations=operations,
            identity_status=RuntimePeakFactIdentityStatus.SEALED,
            owner_request_identity=f"owner-request:{index}",
            group_hypothesis_id=f"GROUP:{index}",
            acquisition_source=source,
            prepared_signal_id=shared_prepared_signal_id,
            elapsed_seconds=0.25,
        )
        for index in range(2)
    )

    metrics = derive_runtime_peak_fact_cost_metrics(
        raw_session_capability=None,
        raw_open_receipts=(),
        work_receipts=receipts,
        artifact_io=None,
    )

    assert metrics.trace_evaluation_count == 1
    assert metrics.asls_baseline_count == 1
    assert metrics.smoothing_count == 1
    assert metrics.segment_enumeration_count == 2
    assert metrics.integration_count == 2
    assert metrics.normalization_count == 2
    assert metrics.raw_descriptor_projection_count == 2
    assert metrics.region_audit_projection_count == 2
    assert metrics.sealed_observation_count == 2

    distinct_metrics = derive_runtime_peak_fact_cost_metrics(
        raw_session_capability=None,
        raw_open_receipts=(),
        work_receipts=(
            receipts[0],
            replace(
                receipts[1],
                prepared_signal_id="prepared-signal:" + "b" * 64,
            ),
        ),
        artifact_io=None,
    )
    assert distinct_metrics.trace_evaluation_count == 2
    assert distinct_metrics.asls_baseline_count == 2
    assert distinct_metrics.smoothing_count == 2
    assert distinct_metrics.segment_enumeration_count == 2

    unavailable_metrics = derive_runtime_peak_fact_cost_metrics(
        raw_session_capability=None,
        raw_open_receipts=(),
        work_receipts=tuple(
            replace(
                receipt,
                operations=(RuntimePeakFactOperation.TRACE_EVALUATION,),
                elapsed_seconds=None,
            )
            for receipt in receipts
        ),
        artifact_io=None,
    )
    assert unavailable_metrics.trace_evaluation_count == 1
    assert unavailable_metrics.asls_baseline_count == 0
    assert unavailable_metrics.smoothing_count == 0
    assert unavailable_metrics.segment_enumeration_count == 0
    assert unavailable_metrics.integration_count == 0
    assert unavailable_metrics.normalization_count == 0
    assert unavailable_metrics.raw_descriptor_projection_count == 0

    with pytest.raises(RuntimeFinalizationContractError, match="prepared_signal_id"):
        replace(receipts[0], prepared_signal_id="prepared-signal:not-a-digest")

    with pytest.raises(
        RuntimeFinalizationContractError,
        match="inconsistent preparation operations",
    ):
        derive_runtime_peak_fact_cost_metrics(
            raw_session_capability=None,
            raw_open_receipts=(),
            work_receipts=(
                receipts[0],
                replace(
                    receipts[1],
                    operations=(RuntimePeakFactOperation.TRACE_EVALUATION,),
                    elapsed_seconds=None,
                ),
            ),
            artifact_io=None,
        )


def test_runtime_owner_keeps_missing_peak_fact_timing_and_artifacts_unavailable(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "missing.raw"
    raw_path.write_bytes(b"missing-peak-fact-runtime-owner")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    capability = mint_raw_acquisition_session_capability(source)
    metrics = derive_runtime_peak_fact_cost_metrics(
        raw_session_capability=capability,
        raw_open_receipts=(),
        work_receipts=(
            RuntimePeakFactWorkReceipt(
                work_unit_key="sealed:missing-time",
                sample_stem="sample-a",
                operations=(
                    RuntimePeakFactOperation.TRACE_EVALUATION,
                    RuntimePeakFactOperation.ASLS_BASELINE,
                    RuntimePeakFactOperation.GAUSSIAN_SMOOTHING,
                    RuntimePeakFactOperation.SEGMENT_ENUMERATION,
                    RuntimePeakFactOperation.INTEGRATION,
                    RuntimePeakFactOperation.NORMALIZATION,
                    RuntimePeakFactOperation.RAW_DESCRIPTOR_PROJECTION,
                ),
                identity_status=RuntimePeakFactIdentityStatus.SEALED,
                owner_request_identity="owner-request:missing-time",
                group_hypothesis_id="GROUP:missing-time",
                acquisition_source=source,
                prepared_signal_id="prepared-signal:" + "3" * 64,
            ),
        ),
        artifact_io=None,
    )

    assert metrics.raw_open_count is None
    assert metrics.raw_open_unavailable_reason
    assert metrics.artifact_round_trip_count is None
    assert metrics.artifact_round_trip_unavailable_reason
    assert metrics.elapsed_seconds is None
    assert metrics.elapsed_unavailable_reason


def test_owner_start_accepts_exact_typed_cancellation_request(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="cancelled-start")

    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test",
        cancellation_request=RuntimeCancellationRequest(attempt=attempt),
    )

    assert runtime.history.cancellation_accepted is True
    assert runtime.history.events[-1].kind is (
        AttemptLifecycleEventKind.CANCELLATION_ACCEPTED
    )


def test_owner_start_rejects_foreign_cancellation_request(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path, stem="cancelled-start")
    foreign = _attempt(tmp_path, stem="foreign-cancellation")

    with pytest.raises(
        RuntimeFinalizationContractError,
        match="different runtime attempt",
    ):
        start_runtime_attempt(
            attempt=attempt,
            work_unit_keys=("raw:a",),
            owner_completeness_attestation="runtime-owner-test",
            cancellation_request=RuntimeCancellationRequest(attempt=foreign),
        )


def test_owner_start_does_not_relabel_rejected_manifest_as_cancelled(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="cancelled-invalid-manifest")

    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=(),
        owner_completeness_attestation="runtime-owner-test",
        cancellation_request=RuntimeCancellationRequest(attempt=attempt),
    )

    assert runtime.manifest_preflight_failed is True
    assert runtime.history.cancellation_accepted is False
    assert runtime.history.events[-1].proposed_outcome is AttemptTerminalOutcome.FAILED


def test_owner_accepted_cancellation_closes_without_worker_dispatch(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="cancelled-execution")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a", "raw:b"),
        owner_completeness_attestation="runtime-owner-test",
        cancellation_request=RuntimeCancellationRequest(attempt=attempt),
    )
    worker_calls: list[str] = []
    persisted: list[RuntimeFinalizationReceipt] = []

    def persist(
        proposed: RuntimeFinalizationReceipt,
    ) -> RuntimeFinalizationReceipt:
        persisted.append(proposed)
        return proposed

    receipt = execute_runtime_attempt(
        runtime,
        (_reservation(attempt),),
        worker_batch_provider=lambda _aggregate: (
            worker_calls.append("called") or RuntimeWorkerBatchReceipt(settlements=())
        ),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=persist,
    )

    state = receipt.coordinator.state
    assert worker_calls == []
    assert persisted == [receipt]
    assert receipt.coordinator.proposed_outcome is AttemptTerminalOutcome.CANCELLED
    assert tuple(
        (settlement.work_unit_key, settlement.outcome)
        for settlement in state.runtime.settlements
    ) == (
        ("raw:a", WorkUnitTerminalOutcome.CANCELLED),
        ("raw:b", WorkUnitTerminalOutcome.CANCELLED),
    )
    assert all(
        settlement.owner_result_ref is None
        and settlement.failure_detail is not None
        and settlement.failure_detail.category is WorkUnitFailureCategory.CANCELLATION
        and settlement.failure_detail.stage == "runtime-owner-pre-dispatch"
        for settlement in state.runtime.settlements
    )
    assert tuple(
        (resource.open_outcome, resource.cleanup_outcome)
        for resource in state.resources
    ) == (
        (
            RuntimeResourceOpenOutcome.NOT_OPENED,
            RuntimeResourceCleanupOutcome.SUCCEEDED,
        ),
    )
    assert state.artifacts == ()
    assert "worker_started" not in tuple(
        event.kind for event in receipt.observability_ledger.events
    )


def test_runtime_phase_error_wraps_worker_exception_without_follow_on_callbacks(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="phase-worker")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test",
    )
    worker_seen: list[RuntimeAttemptAggregate] = []
    telemetry_calls: list[str] = []
    persister_calls: list[RuntimeFinalizationReceipt] = []
    sentinel = RuntimeError("worker callback details stay out of owner facts")

    def worker(aggregate: RuntimeAttemptAggregate) -> RuntimeWorkerBatchReceipt:
        worker_seen.append(aggregate)
        raise sentinel

    def telemetry(_coordinator):
        telemetry_calls.append("called")
        return build_runtime_telemetry_samples_from_coordinator(_coordinator)

    def persist(proposed: RuntimeFinalizationReceipt) -> RuntimeFinalizationReceipt:
        persister_calls.append(proposed)
        return proposed

    with pytest.raises(RuntimeExecutionPhaseError) as raised:
        execute_runtime_attempt(
            runtime,
            (_reservation(attempt),),
            worker_batch_provider=worker,
            telemetry_provider=telemetry,
            persister=persist,
        )

    error = raised.value
    assert error.attempt == attempt
    assert error.phase is RuntimeExecutionPhase.WORKER_BATCH_PROVIDER
    assert error.__cause__ is sentinel
    assert str(error) == (
        f"runtime attempt {attempt.processing_attempt_id} failed during "
        "worker_batch_provider"
    )
    assert worker_seen[0].state.runtime.settlements == ()
    assert worker_seen[0].state.resources[0].open_outcome is None
    assert worker_seen[0].state.resources[0].cleanup_outcome is None
    assert telemetry_calls == []
    assert persister_calls == []
    assert not hasattr(error, "receipt")


def test_runtime_phase_error_wraps_telemetry_exception_after_one_worker_batch(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="phase-telemetry")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test",
    )
    worker_calls: list[str] = []
    persister_calls: list[RuntimeFinalizationReceipt] = []
    sentinel = RuntimeError("telemetry callback details stay out of owner facts")

    def worker(_aggregate: RuntimeAttemptAggregate) -> RuntimeWorkerBatchReceipt:
        worker_calls.append("called")
        return _failed_worker_batch(attempt)

    def telemetry(_coordinator):
        raise sentinel

    def persist_after_telemetry(
        proposed: RuntimeFinalizationReceipt,
    ) -> RuntimeFinalizationReceipt:
        persister_calls.append(proposed)
        return proposed

    with pytest.raises(RuntimeExecutionPhaseError) as raised:
        execute_runtime_attempt(
            runtime,
            (),
            worker_batch_provider=worker,
            telemetry_provider=telemetry,
            persister=persist_after_telemetry,
        )

    error = raised.value
    assert worker_calls == ["called"]
    assert error.attempt == attempt
    assert error.phase is RuntimeExecutionPhase.TELEMETRY_PROVIDER
    assert error.__cause__ is sentinel
    assert persister_calls == []
    assert not hasattr(error, "observability_ledger")


def test_runtime_phase_error_wraps_persister_exception_before_write(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="phase-persister-before-write")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test",
    )
    proposed_receipts: list[RuntimeFinalizationReceipt] = []
    sentinel = RuntimeError("persister callback details stay out of owner facts")

    def persist(proposed: RuntimeFinalizationReceipt) -> RuntimeFinalizationReceipt:
        proposed_receipts.append(proposed)
        raise sentinel

    with pytest.raises(RuntimeExecutionPhaseError) as raised:
        execute_runtime_attempt(
            runtime,
            (),
            worker_batch_provider=lambda _aggregate: _failed_worker_batch(attempt),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            persister=persist,
        )

    error = raised.value
    assert len(proposed_receipts) == 1
    assert error.attempt == attempt
    assert error.phase is RuntimeExecutionPhase.PERSISTER
    assert error.__cause__ is sentinel
    assert not hasattr(error, "receipt")
    assert "persister callback details" not in str(error)


def test_runtime_phase_error_preserves_marker_durability_but_not_acceptance(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="phase-persister-after-write")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test",
    )
    marker = OwnerPathRuntimeMarker(
        tmp_path / "phase-persister.runtime.json",
        "runtime-attempt-artifacts:phase-persister",
    )
    marker.record_launch(runtime)
    sentinel = RuntimeError("persister raised after durable marker write")

    def persist(proposed: RuntimeFinalizationReceipt) -> RuntimeFinalizationReceipt:
        marker(proposed)
        raise sentinel

    with pytest.raises(RuntimeExecutionPhaseError) as raised:
        execute_runtime_attempt(
            runtime,
            (),
            worker_batch_provider=lambda _aggregate: _failed_worker_batch(attempt),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            persister=persist,
        )

    error = raised.value
    assert error.phase is RuntimeExecutionPhase.PERSISTER
    assert error.attempt == attempt
    assert error.__cause__ is sentinel
    assert marker.read().state.value == "finalized"
    assert marker.recover_after_coordinator_loss(attempt).finalization_acknowledged


def test_invalid_callback_returns_remain_contract_errors_not_phase_errors(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="phase-invalid-returns")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test",
    )

    with pytest.raises(RuntimeFinalizationContractError):
        execute_runtime_attempt(
            runtime,
            (),
            worker_batch_provider=lambda _aggregate: cast(
                RuntimeWorkerBatchReceipt,
                object(),
            ),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            persister=lambda proposed: proposed,
        )

    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test",
    )
    with pytest.raises(RuntimeFinalizationContractError):
        execute_runtime_attempt(
            runtime,
            (),
            worker_batch_provider=lambda _aggregate: _failed_worker_batch(attempt),
            telemetry_provider=lambda _coordinator: cast(
                RuntimeTelemetrySamples,
                object(),
            ),
            persister=lambda proposed: proposed,
        )

    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test",
    )
    with pytest.raises(RuntimeFinalizationContractError):
        execute_runtime_attempt(
            runtime,
            (),
            worker_batch_provider=lambda _aggregate: _failed_worker_batch(attempt),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            persister=lambda _proposed: cast(
                RuntimeFinalizationReceipt,
                object(),
            ),
        )


def test_runtime_phase_error_does_not_catch_base_exception(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path, stem="phase-base-exception")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test",
    )

    class SentinelBaseException(BaseException):
        pass

    sentinel = SentinelBaseException("base exception remains unchanged")
    with pytest.raises(SentinelBaseException) as raised:
        execute_runtime_attempt(
            runtime,
            (),
            worker_batch_provider=lambda _aggregate: (_ for _ in ()).throw(sentinel),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            persister=lambda proposed: proposed,
        )

    assert raised.value is sentinel


def test_runtime_phase_error_public_contract_is_closed_and_owner_bound(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="phase-public-contract")
    assert tuple(phase.value for phase in RuntimeExecutionPhase) == (
        "worker_batch_provider",
        "telemetry_provider",
        "persister",
    )
    error = RuntimeExecutionPhaseError(
        attempt,
        RuntimeExecutionPhase.WORKER_BATCH_PROVIDER,
    )
    assert error.attempt == attempt
    assert error.phase is RuntimeExecutionPhase.WORKER_BATCH_PROVIDER
    assert error.args == (
        f"runtime attempt {attempt.processing_attempt_id} failed during "
        "worker_batch_provider",
    )
    assert (
        "RuntimeExecutionPhase"
        in __import__(
            "xic_extractor.execution.attempt",
            fromlist=["__all__"],
        ).__all__
    )
    assert (
        "RuntimeExecutionPhaseError"
        in __import__(
            "xic_extractor.execution.attempt",
            fromlist=["__all__"],
        ).__all__
    )


def test_owner_aggregate_derives_lifecycle_metrics_and_one_receipt(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test",
    )
    result_ref = mint_targeted_process_result_ref(
        processing_attempt=attempt,
        work_unit_key="raw:a",
        owner_result_payload=b"runtime-owner-result",
    )
    aggregate = _prepare_runtime_attempt(runtime, (_reservation(attempt),))
    aggregate = _apply_runtime_worker_batch(
        aggregate,
        RuntimeWorkerBatchReceipt(
            settlements=(
                mint_work_unit_settlement(
                    attempt=attempt,
                    work_unit_key="raw:a",
                    outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                    owner_result_ref=result_ref.artifact,
                ),
            ),
            resource_settlements=(
                RuntimeResourceSettlementReceipt(
                    attempt=attempt,
                    resource_key="session:a",
                    open_outcome=RuntimeResourceOpenOutcome.OPENED,
                    cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
                ),
            ),
            artifact_registrations=(
                RuntimeArtifactRegistrationReceipt(
                    work_unit_key="raw:a",
                    artifact=result_ref.artifact,
                ),
            ),
        ),
    )

    receipt = _finalize_runtime_attempt(
        aggregate,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=lambda proposed: proposed,
    )

    assert isinstance(receipt, RuntimeFinalizationReceipt)
    assert receipt.coordinator.state.runtime.all_manifest_work_settled is True
    assert receipt.supervisor.observability_ledger.metrics[0].name == ("raw_open_count")
    metrics = {
        metric.name: metric.value
        for metric in receipt.observability_ledger.metrics
        if metric.scope_key == attempt.processing_attempt_id
    }
    assert metrics["raw_open_count"] == 1
    assert metrics["logical_xic_request_count"] == 0
    assert metrics["artifact_write_count"] == 0
    event_kinds = tuple(event.kind for event in receipt.observability_ledger.events)
    assert "worker_started" not in event_kinds
    assert event_kinds[:3] == (
        "attempt_queued",
        "manifest_closed",
        "attempt_started",
    )
    assert event_kinds[-3:] == (
        "terminal_acknowledged",
        "artifact_offered_to_owner",
        "terminal_outcome",
    )
    coordinator_event_kinds = tuple(
        event.kind.value for event in receipt.coordinator.state.events
    )
    assert (
        coordinator_event_kinds.index("resource_reserved")
        < coordinator_event_kinds.index("resource_opened")
        < coordinator_event_kinds.index("artifact_registered")
        < coordinator_event_kinds.index("resource_cleanup_succeeded")
        < coordinator_event_kinds.index("terminal_proposal")
    )
    assert (
        event_kinds.index("resource_reserved")
        < event_kinds.index("resource_opened")
        < event_kinds.index("work_unit_submitted")
        < event_kinds.index("work_unit_started")
        < event_kinds.index("work_unit_settled")
        < event_kinds.index("artifact_registered")
        < event_kinds.index("resource_cleanup_succeeded")
        < event_kinds.index("terminal_proposal")
    )

    coordinator_payload = json.loads(
        canonical_coordinator_terminal_record_bytes(receipt.coordinator)
    )
    observability_payload = json.loads(
        canonical_runtime_observability_ledger_bytes(receipt.observability_ledger)
    )
    assert coordinator_payload["schema_version"] == (
        "execution_runtime_coordinator_terminal"
    )
    assert observability_payload["schema_version"] == (
        "execution_runtime_observability_ledger"
    )
    assert {
        metric.measurement_source for metric in receipt.observability_ledger.metrics
    } == {"execution_runtime_owner"}

    with pytest.raises(
        RuntimeFinalizationContractError,
        match="RuntimeAttemptAggregate must be minted by the runtime owner",
    ):
        RuntimeAttemptAggregate(
            state=aggregate.state,
            acquisitions=aggregate.acquisitions,
            _mint_token=object(),
        )
    with pytest.raises(
        RuntimeFinalizationContractError,
        match="RuntimeFinalizationReceipt must be minted by the runtime owner",
    ):
        RuntimeFinalizationReceipt(
            coordinator=receipt.coordinator,
            supervisor=receipt.supervisor,
            _mint_token=object(),
        )
    assert not hasattr(aggregate, "_mint_token")
    assert not hasattr(receipt, "_mint_token")
    with pytest.raises((TypeError, ValueError), match="InitVar '_mint_token'"):
        replace(aggregate)
    with pytest.raises((TypeError, ValueError), match="InitVar '_mint_token'"):
        replace(receipt)
    with pytest.raises(TypeError, match="RuntimeAttemptAggregate is final"):
        type("ForgedAggregate", (RuntimeAttemptAggregate,), {})
    with pytest.raises(TypeError, match="RuntimeFinalizationReceipt is final"):
        type("ForgedReceipt", (RuntimeFinalizationReceipt,), {})

    foreign_attempt = _attempt(tmp_path, stem="foreign-aggregate")
    foreign_aggregate = _prepare_runtime_attempt(
        start_runtime_attempt(
            attempt=foreign_attempt,
            work_unit_keys=("raw:a",),
            owner_completeness_attestation="runtime-owner-test",
        ),
        (),
    )
    with pytest.raises(
        RuntimeFinalizationContractError,
        match="exact runtime aggregate state",
    ):
        _derive_runtime_observability_ledger(
            foreign_aggregate,
            receipt.coordinator,
            telemetry=build_runtime_telemetry_samples_from_coordinator(
                receipt.coordinator
            ),
        )


def test_owner_rejects_telemetry_that_omits_derived_events(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path, stem="missing-offset")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test",
    )
    result_ref = mint_targeted_process_result_ref(
        processing_attempt=attempt,
        work_unit_key="raw:a",
        owner_result_payload=b"runtime-owner-result",
    )
    aggregate = _apply_runtime_worker_batch(
        _prepare_runtime_attempt(runtime, (_reservation(attempt),)),
        RuntimeWorkerBatchReceipt(
            settlements=(
                mint_work_unit_settlement(
                    attempt=attempt,
                    work_unit_key="raw:a",
                    outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                    owner_result_ref=result_ref.artifact,
                ),
            ),
            resource_settlements=(
                RuntimeResourceSettlementReceipt(
                    attempt=attempt,
                    resource_key="session:a",
                    open_outcome=RuntimeResourceOpenOutcome.OPENED,
                    cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
                ),
            ),
            artifact_registrations=(
                RuntimeArtifactRegistrationReceipt("raw:a", result_ref.artifact),
            ),
        ),
    )

    def missing_offset(coordinator):
        samples = build_runtime_telemetry_samples_from_coordinator(coordinator)
        return type(samples)(
            event_monotonic_offsets_ns=(samples.event_monotonic_offsets_ns[:-1]),
            artifact_io=samples.artifact_io,
            process_tree_peak_rss=samples.process_tree_peak_rss,
            worker_peak_rss=samples.worker_peak_rss,
        )

    with pytest.raises(
        RuntimeFinalizationContractError,
        match="owner-derived event count",
    ):
        _finalize_runtime_attempt(
            aggregate,
            telemetry_provider=missing_offset,
            persister=lambda proposed: proposed,
        )


@pytest.mark.parametrize(
    ("work_unit_keys", "owner_completeness_attestation"),
    (
        ((), "runtime-owner-test"),
        (("raw:a",), ""),
        (("raw:a",), None),
    ),
)
def test_owner_preflight_failure_uses_finalization_and_recovery_path(
    tmp_path: Path,
    work_unit_keys: tuple[str, ...],
    owner_completeness_attestation: str | None,
) -> None:
    attempt = _attempt(tmp_path, stem="preflight-failure")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=work_unit_keys,
        owner_completeness_attestation=owner_completeness_attestation,
    )
    marker = OwnerPathRuntimeMarker(
        tmp_path / "preflight.runtime.json",
        "runtime-attempt-artifacts:preflight",
    )
    worker_calls: list[str] = []

    assert runtime.manifest_preflight_failed is True
    assert tuple(event.kind for event in runtime.history.events) == (
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
        AttemptLifecycleEventKind.MANIFEST_PREFLIGHT_FAILED,
        AttemptLifecycleEventKind.TERMINAL_PROPOSAL,
    )
    marker.record_launch(runtime)
    receipt = execute_runtime_attempt(
        runtime,
        (_reservation(attempt),),
        worker_batch_provider=lambda _aggregate: (
            worker_calls.append("called") or RuntimeWorkerBatchReceipt(settlements=())
        ),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=marker,
    )

    assert worker_calls == []
    assert receipt.coordinator.state.resources == ()
    assert receipt.coordinator.proposed_outcome is AttemptTerminalOutcome.FAILED
    assert tuple(event.kind for event in receipt.observability_ledger.events) == (
        "attempt_queued",
        "manifest_preflight_failed",
        "terminal_proposal",
        "terminal_acknowledged",
        "terminal_outcome",
    )
    metrics = {metric.name: metric for metric in receipt.observability_ledger.metrics}
    for name in (
        "raw_open_count",
        "logical_xic_request_count",
        "xic_batch_count",
        "adapter_chromatogram_call_count",
        "trace_point_count",
        "artifact_read_count",
        "artifact_read_bytes",
        "artifact_write_count",
        "artifact_write_bytes",
    ):
        assert metrics[name].value == 0
    for name in (
        "coordinator_work_seconds",
        "stage_or_unit_elapsed_seconds",
        "worker_peak_rss_bytes",
        "process_tree_peak_rss_bytes",
    ):
        assert metrics[name].availability is RuntimeMetricAvailability.UNSUPPORTED
        assert metrics[name].reason == "not_started_preflight_failure"
    recovered = marker.recover_after_coordinator_loss(attempt)
    assert recovered.finalization_acknowledged is True
    assert recovered.terminal_outcome is AttemptTerminalOutcome.FAILED


@pytest.mark.parametrize(
    "open_outcome",
    (
        RuntimeResourceOpenOutcome.NOT_OPENED,
        RuntimeResourceOpenOutcome.OPEN_FAILED,
        RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED,
    ),
)
def test_unopened_process_resource_does_not_invent_worker_topology(
    tmp_path: Path,
    open_outcome: RuntimeResourceOpenOutcome,
) -> None:
    attempt = _attempt(tmp_path, stem=f"unopened-worker-{open_outcome.value}")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test",
    )
    reservation = RuntimeResourceReservation(
        attempt=attempt,
        kind=RuntimeResourceKind.PROCESS_WORKER,
        resource_key="process:a",
        worker_key="worker:a",
        source_reference="no-raw:sample:a",
        compatibility_token="runtime-owner-test",
    )
    aggregate = _apply_runtime_worker_batch(
        _prepare_runtime_attempt(runtime, (reservation,)),
        RuntimeWorkerBatchReceipt(
            settlements=(
                mint_work_unit_settlement(
                    attempt=attempt,
                    work_unit_key="raw:a",
                    outcome=WorkUnitTerminalOutcome.FAILED,
                    failure_detail=WorkUnitFailureDetail(
                        category=WorkUnitFailureCategory.EXECUTION_ERROR,
                        stage="runtime-owner-test",
                        source_reference="raw:a",
                        message="worker did not open",
                    ),
                ),
            ),
            resource_settlements=(
                RuntimeResourceSettlementReceipt(
                    attempt=attempt,
                    resource_key="process:a",
                    open_outcome=open_outcome,
                    cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
                ),
            ),
        ),
    )

    receipt = _finalize_runtime_attempt(
        aggregate,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=lambda proposed: proposed,
    )

    assert "worker_started" not in {
        event.kind for event in receipt.observability_ledger.events
    }
    event_kinds = tuple(event.kind for event in receipt.observability_ledger.events)
    open_event_kind = {
        RuntimeResourceOpenOutcome.NOT_OPENED: "resource_not_opened",
        RuntimeResourceOpenOutcome.OPEN_FAILED: "resource_open_failed",
        RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED: (
            "resource_condition_not_activated"
        ),
    }[open_outcome]
    assert (
        event_kinds.index("resource_reserved")
        < event_kinds.index(open_event_kind)
        < event_kinds.index("work_unit_submitted")
        < event_kinds.index("work_unit_started")
        < event_kinds.index("work_unit_settled")
        < event_kinds.index("resource_cleanup_succeeded")
        < event_kinds.index("terminal_proposal")
    )
    worker_metric = next(
        metric
        for metric in receipt.observability_ledger.metrics
        if metric.name == "worker_peak_rss_bytes"
    )
    assert worker_metric.scope_key == attempt.processing_attempt_id
    assert worker_metric.availability is RuntimeMetricAvailability.UNSUPPORTED
    assert worker_metric.reason == "not_applicable_serial"


def test_unknown_open_state_after_worker_loss_retains_worker_topology(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="unknown-open-worker-loss")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test",
    )
    reservation = RuntimeResourceReservation(
        attempt=attempt,
        kind=RuntimeResourceKind.PROCESS_WORKER,
        resource_key="process:a",
        worker_key="worker:a",
        source_reference="no-raw:sample:a",
        compatibility_token="runtime-owner-test",
    )
    receipt = execute_runtime_attempt(
        runtime,
        (reservation,),
        worker_batch_provider=lambda _aggregate: RuntimeWorkerBatchReceipt(
            settlements=(
                mint_work_unit_settlement(
                    attempt=attempt,
                    work_unit_key="raw:a",
                    outcome=WorkUnitTerminalOutcome.FAILED,
                    failure_detail=WorkUnitFailureDetail(
                        category=WorkUnitFailureCategory.WORKER_LOSS,
                        stage="runtime-owner-test",
                        source_reference="raw:a",
                        message="worker vanished after dispatch",
                    ),
                ),
            ),
            resource_settlements=(
                RuntimeResourceSettlementReceipt(
                    attempt=attempt,
                    resource_key="process:a",
                    open_outcome=(
                        RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS
                    ),
                    cleanup_outcome=(
                        RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS
                    ),
                ),
            ),
        ),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=lambda proposed: proposed,
    )

    assert "worker_started" in {
        event.kind for event in receipt.observability_ledger.events
    }
    event_kinds = tuple(event.kind for event in receipt.observability_ledger.events)
    assert (
        event_kinds.index("resource_reserved")
        < event_kinds.index("resource_open_state_unknown_after_worker_loss")
        < event_kinds.index("worker_started")
        < event_kinds.index("work_unit_submitted")
        < event_kinds.index("work_unit_started")
        < event_kinds.index("work_unit_settled")
        < event_kinds.index("resource_cleanup_unknown_after_worker_loss")
        < event_kinds.index("terminal_proposal")
    )
    worker_metric = next(
        metric
        for metric in receipt.observability_ledger.metrics
        if metric.name == "worker_peak_rss_bytes" and metric.scope_key == "worker:a"
    )
    assert worker_metric.availability is RuntimeMetricAvailability.MEASURED
    assert worker_metric.value == 0


@pytest.mark.parametrize(
    "scenario",
    (
        "success",
        "mixed",
        "replay",
        "conflict",
        "undeclared",
        "seeded_conflict",
        "cancelled",
        "unaccepted_cancellation",
        "cancellation_before_foreign_attempt",
        "foreign_attempt_before_cancellation",
        "foreign_artifact",
        "undeclared_artifact",
    ),
)
def test_owner_worker_batch_matches_sequential_state_and_bytes(
    tmp_path: Path,
    scenario: str,
) -> None:
    attempt = _attempt(tmp_path, stem="batch-parity")
    keys = ("raw:c", "raw:a", "raw:b")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=keys,
        owner_completeness_attestation="runtime-owner-test",
        cancellation_request=(
            RuntimeCancellationRequest(attempt=attempt)
            if scenario == "cancelled"
            else None
        ),
    )
    artifacts = tuple(
        mint_targeted_process_result_ref(
            processing_attempt=attempt,
            work_unit_key=key,
            owner_result_payload=key.encode(),
        ).artifact
        for key in keys
    )
    settlements = [
        mint_work_unit_settlement(
            attempt=attempt,
            work_unit_key=key,
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=artifact,
        )
        for key, artifact in zip(keys, artifacts, strict=True)
    ]
    registrations = [
        RuntimeArtifactRegistrationReceipt(work_unit_key=key, artifact=artifact)
        for key, artifact in zip(keys, artifacts, strict=True)
    ]
    failure_detail = WorkUnitFailureDetail(
        category=WorkUnitFailureCategory.EXECUTION_ERROR,
        stage="runtime-owner-test",
        source_reference="no-raw:sample",
        message="synthetic failure",
    )
    failed = mint_work_unit_settlement(
        attempt=attempt,
        work_unit_key=keys[0],
        outcome=WorkUnitTerminalOutcome.FAILED,
        failure_detail=failure_detail,
    )
    cancelled = replace(
        failed,
        outcome=WorkUnitTerminalOutcome.CANCELLED,
        failure_detail=replace(
            failure_detail,
            category=WorkUnitFailureCategory.CANCELLATION,
        ),
    )
    seeds: list[RuntimeWorkerBatchReceipt] = []
    if scenario == "mixed":
        settlements[1] = replace(failed, work_unit_key=keys[1])
    elif scenario in {"conflict", "seeded_conflict"}:
        if scenario == "seeded_conflict":
            seeds.append(
                RuntimeWorkerBatchReceipt(
                    settlements=(settlements[0], failed),
                    artifact_registrations=(registrations[0],),
                )
            )
        settlements.extend((failed, replace(failed, work_unit_key=keys[1]), failed))
        conflict = replace(registrations[0], work_unit_key=keys[1])
        registrations.extend((conflict, registrations[0], conflict))
    elif scenario == "undeclared":
        settlements.extend(
            (
                replace(failed, work_unit_key="undeclared:first"),
                replace(failed, work_unit_key="undeclared:second"),
            )
        )
    elif scenario in {
        "cancelled",
        "unaccepted_cancellation",
        "cancellation_before_foreign_attempt",
        "foreign_attempt_before_cancellation",
    }:
        settlements[0] = cancelled
        if "foreign_attempt" in scenario:
            foreign = _attempt(tmp_path, stem="foreign-batch")
            foreign_settlement = replace(failed, attempt=foreign, work_unit_key=keys[1])
            if scenario == "foreign_attempt_before_cancellation":
                settlements[0] = replace(foreign_settlement, work_unit_key=keys[0])
                settlements[1] = replace(cancelled, work_unit_key=keys[1])
            else:
                settlements[1] = foreign_settlement
    elif scenario == "foreign_artifact":
        foreign = _attempt(tmp_path, stem="foreign-artifact")
        registrations[0] = RuntimeArtifactRegistrationReceipt(
            work_unit_key=keys[0],
            artifact=mint_targeted_process_result_ref(
                processing_attempt=foreign,
                work_unit_key=keys[0],
                owner_result_payload=b"foreign-result",
            ).artifact,
        )
    elif scenario == "undeclared_artifact":
        registrations[0] = replace(registrations[0], work_unit_key="undeclared")

    # Stable sorting must retain the first submitted fact within each work key.
    batch = RuntimeWorkerBatchReceipt(
        settlements=tuple(sorted(settlements, key=lambda item: item.work_unit_key)),
        artifact_registrations=tuple(reversed(registrations)),
        resource_settlements=(
            RuntimeResourceSettlementReceipt(
                attempt=attempt,
                resource_key="session:a",
                open_outcome=RuntimeResourceOpenOutcome.OPENED,
                cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
            ),
        ),
    )
    aggregate = _prepare_runtime_attempt(runtime, (_reservation(attempt),))
    for seed in seeds:
        expected_state = _sequential_worker_batch_state(aggregate, seed)
        aggregate = _apply_runtime_worker_batch(aggregate, seed)
        assert aggregate.state == expected_state

    if scenario in {
        "unaccepted_cancellation",
        "cancellation_before_foreign_attempt",
        "foreign_attempt_before_cancellation",
        "foreign_artifact",
        "undeclared_artifact",
    }:
        with pytest.raises(
            (AttemptLifecycleContractError, RuntimeFinalizationContractError)
        ) as expected_error:
            _sequential_worker_batch_state(aggregate, batch)
        with pytest.raises(type(expected_error.value)) as actual_error:
            _apply_runtime_worker_batch(aggregate, batch)
        assert str(actual_error.value) == str(expected_error.value)
        return

    for _ in range(2 if scenario == "replay" else 1):
        expected_state = _sequential_worker_batch_state(aggregate, batch)
        aggregate = _apply_runtime_worker_batch(aggregate, batch)
        assert aggregate.state == expected_state
    expected = _finalize_runtime_attempt(
        _mint_runtime_attempt_aggregate(state=expected_state),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=lambda proposed: proposed,
    )
    actual = _finalize_runtime_attempt(
        aggregate,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=lambda proposed: proposed,
    )
    assert canonical_coordinator_terminal_record_bytes(actual.coordinator) == (
        canonical_coordinator_terminal_record_bytes(expected.coordinator)
    )
    assert canonical_runtime_observability_ledger_bytes(
        actual.observability_ledger
    ) == (canonical_runtime_observability_ledger_bytes(expected.observability_ledger))
    assert actual.supervisor == expected.supervisor


def _sequential_worker_batch_state(
    aggregate: RuntimeAttemptAggregate,
    batch: RuntimeWorkerBatchReceipt,
) -> AttemptFinalizationState:
    """Apply the singular reducers as the state/byte oracle for typed batches."""

    assert not batch.acquisitions
    state = aggregate.state
    attempt = state.runtime.manifest.attempt
    manifest_order = {
        key: index for index, key in enumerate(state.runtime.manifest.work_unit_keys)
    }
    resource_order = {
        record.reservation.resource_key: index
        for index, record in enumerate(state.resources)
    }
    resources = sorted(
        batch.resource_settlements,
        key=lambda item: resource_order.get(item.resource_key, len(resource_order)),
    )
    for receipt in resources:
        if receipt.attempt != attempt:
            raise RuntimeFinalizationContractError(
                "resource receipt references a different runtime attempt"
            )
    for receipt in resources:
        state = record_runtime_resource_open(
            state, resource_key=receipt.resource_key, outcome=receipt.open_outcome
        )
    for settlement in sorted(
        batch.settlements,
        key=lambda item: manifest_order.get(item.work_unit_key, len(manifest_order)),
    ):
        if settlement.attempt != attempt:
            raise RuntimeFinalizationContractError(
                "work settlement references a different runtime attempt"
            )
        state = replace(
            state,
            runtime=settle_work_unit(
                state.runtime,
                work_unit_key=settlement.work_unit_key,
                outcome=settlement.outcome,
                owner_result_ref=settlement.owner_result_ref,
                failure_detail=settlement.failure_detail,
            ),
        )
    for registration in sorted(
        batch.artifact_registrations,
        key=lambda item: (
            manifest_order.get(item.work_unit_key, len(manifest_order)),
            item.artifact.evidence_artifact_id,
        ),
    ):
        state = register_runtime_artifact(
            state,
            work_unit_key=registration.work_unit_key,
            artifact=registration.artifact,
        )
    for receipt in resources:
        state = record_runtime_resource_cleanup(
            state, resource_key=receipt.resource_key, outcome=receipt.cleanup_outcome
        )
    return state


def _failed_worker_batch(
    attempt: ProcessingAttemptIdentity,
) -> RuntimeWorkerBatchReceipt:
    return RuntimeWorkerBatchReceipt(
        settlements=(
            mint_work_unit_settlement(
                attempt=attempt,
                work_unit_key="raw:a",
                outcome=WorkUnitTerminalOutcome.FAILED,
                failure_detail=WorkUnitFailureDetail(
                    category=WorkUnitFailureCategory.EXECUTION_ERROR,
                    stage="runtime-owner-test",
                    source_reference="no-raw:sample:a",
                    message="synthetic worker failure",
                ),
            ),
        ),
    )


def _reservation(
    attempt: ProcessingAttemptIdentity,
) -> RuntimeResourceReservation:
    return RuntimeResourceReservation(
        attempt=attempt,
        kind=RuntimeResourceKind.ACQUISITION_SESSION,
        resource_key="session:a",
        worker_key="worker:a",
        source_reference="no-raw:sample:a",
        compatibility_token="runtime-owner-test",
    )


def _attempt(
    tmp_path: Path,
    *,
    stem: str = "sample",
) -> ProcessingAttemptIdentity:
    raw_path = tmp_path / f"{stem}.raw"
    raw_path.write_bytes(f"synthetic-no-raw:{stem}".encode())
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo.raw.ms_controller_1",
    )
    request = build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key=raw_path.name,
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="runtime_owner_test",
        processing_algorithm_version="runtime_owner_test",
        processing_contract_version="processing_identity",
        scientific_inputs={"method": "cid_nl", "neutral_loss_da": 116.0},
    )
    return issue_processing_attempt(request)


def test_runtime_acquisition_compaction_preserves_counts_replay_and_releases_arrays(
    tmp_path: Path,
) -> None:
    from xic_extractor.xic_models import XICRequest, XICTrace

    capability = mint_trace_acquisition_capability(
        _source(tmp_path),
        adapter_namespace="tests.runtime.compaction",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    acquired = acquire_trace_batch(
        capability,
        (XICRequest(mz=500.0, rt_min=1.0, rt_max=2.0, ppm_tol=20.0),),
        _NoRawBatchAdapter(),
    )
    array_ref = weakref.ref(acquired.traces[0].trace.intensity)
    receipt = RuntimeAcquisitionReceipt("raw:a", acquired)
    compact = compact_runtime_acquisition_receipt(receipt)
    assert compact.work_unit_key == receipt.work_unit_key
    assert type(compact.acquisition) is RuntimeAcquisitionMetrics
    assert compact_runtime_acquisition_receipt(compact) is compact
    assert derive_runtime_acquisition_metrics((compact.acquisition,)) == (
        derive_runtime_acquisition_metrics((acquired,))
    )
    assert derive_runtime_acquisition_metrics((compact.acquisition, acquired)) == (
        derive_runtime_acquisition_metrics((acquired, acquired))
    )
    with pytest.raises(
        RuntimeFinalizationContractError, match="minted by the runtime owner"
    ):
        RuntimeAcquisitionReceipt("raw:a", compact.acquisition)

    attempt = _attempt(tmp_path)
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-compaction-test",
    )
    initial = _prepare_runtime_attempt(runtime, ())
    worker = replace(_failed_worker_batch(attempt), acquisitions=(receipt,))
    eager = _apply_runtime_worker_batch(initial, worker)
    recovery_capability = mint_trace_acquisition_capability(
        _source(tmp_path),
        adapter_namespace="tests.runtime.compaction.recovery",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    recovery_acquired = acquire_trace_batch(
        recovery_capability,
        (XICRequest(mz=501.0, rt_min=1.0, rt_max=2.0, ppm_tol=20.0),),
        _NoRawBatchAdapter(),
    )
    recovery_receipt = RuntimeAcquisitionReceipt("raw:a", recovery_acquired)
    multi_phase = _apply_runtime_worker_batch(
        initial,
        replace(worker, acquisitions=(receipt, recovery_receipt)),
    )
    assert multi_phase.acquisitions == (receipt, recovery_receipt)
    compressed = _apply_runtime_worker_batch(
        initial, replace(worker, acquisitions=(compact,))
    )
    replayed = _apply_runtime_worker_batch(
        eager, replace(worker, acquisitions=(compact,))
    )
    assert replayed.acquisitions == (compact,)
    replayed_multi_phase = _apply_runtime_worker_batch(
        multi_phase, replace(worker, acquisitions=(compact,))
    )
    assert replayed_multi_phase.acquisitions == (compact, recovery_receipt)
    _apply_runtime_worker_batch(compressed, worker)
    _apply_runtime_worker_batch(compressed, replace(worker, acquisitions=(compact,)))
    expected = _finalize_runtime_attempt(
        eager,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=lambda receipt: receipt,
    )
    actual = _finalize_runtime_attempt(
        compressed,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=lambda receipt: receipt,
    )
    assert canonical_runtime_observability_ledger_bytes(
        actual.observability_ledger
    ) == (canonical_runtime_observability_ledger_bytes(expected.observability_ledger))
    changed = replace(
        acquired,
        traces=(
            replace(
                acquired.traces[0],
                trace=XICTrace.from_arrays([1.0, 2.0], [0.0, 99.0]),
            ),
        ),
    )
    conflicting = compact_runtime_acquisition_receipt(
        RuntimeAcquisitionReceipt("raw:a", changed)
    )
    assert conflicting.acquisition == compact.acquisition
    with pytest.raises(
        RuntimeFinalizationContractError, match="conflicting acquisition"
    ):
        _apply_runtime_worker_batch(
            compressed, replace(worker, acquisitions=(conflicting,))
        )
    with pytest.raises(RuntimeFinalizationContractError, match="submitted terminal"):
        foreign = compact_runtime_acquisition_receipt(
            RuntimeAcquisitionReceipt("raw:other", acquired)
        )
        _apply_runtime_worker_batch(initial, replace(worker, acquisitions=(foreign,)))
    del (
        receipt,
        acquired,
        worker,
        eager,
        expected,
        multi_phase,
        replayed_multi_phase,
    )
    gc.collect()
    assert array_ref() is None
    assert compressed.acquisitions == (compact,)
