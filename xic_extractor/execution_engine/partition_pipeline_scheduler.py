"""Generic partition pipeline execution engine scheduler."""

from __future__ import annotations

import gc
import queue
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, TypeVar

from xic_extractor.execution_engine.contracts import (
    PartitionAcquisitionOutput,
    PartitionCompositionOutput,
    PartitionPipelineAdapter,
    WorkPartition,
)
from xic_extractor.execution_engine.streamed_result_store import (
    StreamedResultStore,
)
from xic_extractor.execution_runtime import (
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitSettlement,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeAcquisitionReceipt,
    RuntimeArtifactRegistrationReceipt,
    RuntimeCancellationIngress,
    RuntimeCancellationRequest,
    RuntimeFinalizationReceipt,
    RuntimeResourceSettlementReceipt,
    RuntimeTelemetryProvider,
    RuntimeWorkerBatchReceipt,
    compact_runtime_acquisition_receipt,
    execute_runtime_attempt,
    mint_work_unit_settlement,
    start_runtime_attempt,
)

TPartitionPayload = TypeVar("TPartitionPayload")
TAcquisitionResult = TypeVar("TAcquisitionResult")
TCompositionResult = TypeVar("TCompositionResult")
TApplicationResult = TypeVar("TApplicationResult")
TFinalResult = TypeVar("TFinalResult")
_DONE_SENTINEL = object()


class PartitionPipelineScheduler:
    """Generic orchestrator executing partitioned workloads under runtime contracts."""

    @staticmethod
    def execute(
        adapter: PartitionPipelineAdapter[
            TPartitionPayload,
            TAcquisitionResult,
            TCompositionResult,
            TApplicationResult,
            TFinalResult,
        ],
        *,
        output_dir: Path,
        telemetry_provider: RuntimeTelemetryProvider,
        recovery_marker: OwnerPathRuntimeMarker,
        progress_callback: Callable[[Mapping[str, object]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        pipeline_overlap: bool = True,
        acquisition_workers: int = 1,
        retain_partition_memory: bool = False,
        execute_runtime_attempt: Callable[..., RuntimeFinalizationReceipt] = (
            execute_runtime_attempt
        ),
        store_factory: Callable[[Path, tuple[object, ...]], StreamedResultStore] = (
            StreamedResultStore
        ),
        acquisition_cleanup: Callable[[], None] | None = None,
    ) -> TFinalResult:
        """Execute finite partitions under one runtime attempt."""
        if type(acquisition_workers) is not int or acquisition_workers not in (1, 2):
            raise ValueError("acquisition_workers must be 1 or 2")
        partitions = adapter.partitions
        owners = adapter.processing_owners
        work_unit_keys = adapter.work_unit_keys
        resource_reservations = adapter.resource_reservations

        runtime = start_runtime_attempt(
            attempt=owners.attempt,
            work_unit_keys=tuple(work_unit_keys),
            owner_completeness_attestation=adapter.owner_completeness_attestation,
        )
        cancellation_ingress = RuntimeCancellationIngress(attempt=owners.attempt)

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        store = store_factory(
            Path(mkdtemp(prefix=".partition_spool-", dir=output_dir)),
            adapter.get_shared_objects(),
        )

        failure: Exception | None = None
        application_result: TApplicationResult | None = None

        def progress(stage: str, completed: int) -> None:
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "partition_progress",
                        "stage": stage,
                        "completed": completed,
                        "total": len(partitions),
                    }
                )

        def provide_worker_batch(_aggregate: object) -> RuntimeWorkerBatchReceipt:
            nonlocal failure, application_result
            settlements: list[WorkUnitSettlement] = []
            resources: dict[str, RuntimeResourceSettlementReceipt] = {}
            artifacts: list[RuntimeArtifactRegistrationReceipt] = []
            acquisitions: list[RuntimeAcquisitionReceipt] = []
            composition_outputs: list[
                PartitionCompositionOutput[TCompositionResult]
            ] = []
            runtime_inflight: set[str] = set()
            failed_work_unit_keys: set[str] = set()
            failed_stage: str = "partition-pipeline-completion"
            failed_exc: Exception | None = None
            timeline_emitted = False
            thread_lock = threading.Lock()

            def record_partition_failure(
                partition: WorkPartition[TPartitionPayload],
                stage: str,
                exc: Exception,
            ) -> None:
                nonlocal failed_stage, failed_exc
                with thread_lock:
                    if not failed_work_unit_keys:
                        pkey = partition.partition_key
                        matching = {
                            k
                            for k in work_unit_keys
                            if k == pkey
                            or k.startswith(f"wu:{pkey}")
                            or k.startswith(f"{pkey}:")
                            or pkey in k
                        }
                        if not matching and partition.index < len(work_unit_keys):
                            matching = {work_unit_keys[partition.index]}
                        failed_work_unit_keys.update(matching)
                        failed_stage = stage
                        failed_exc = exc

            timeline_records: list[dict[str, Any]] = [
                {
                    "partition_key": p.partition_key,
                    "index": p.index,
                    "acquire_start": None,
                    "acquire_end": None,
                    "compose_start": None,
                    "compose_end": None,
                    "status": "pending",
                }
                for p in partitions
            ]

            def emit_timeline() -> None:
                nonlocal timeline_emitted
                with thread_lock:
                    if (
                        timeline_emitted
                        or progress_callback is None
                        or not timeline_records
                    ):
                        return
                    timeline_emitted = True
                    for i in range(1, len(timeline_records)):
                        curr_acq_s = timeline_records[i].get("acquire_start")
                        curr_acq_e = timeline_records[i].get("acquire_end")
                        prev_comp_s = timeline_records[i - 1].get("compose_start")
                        prev_comp_e = timeline_records[i - 1].get("compose_end")
                        if (
                            isinstance(curr_acq_s, (int, float))
                            and isinstance(curr_acq_e, (int, float))
                            and isinstance(prev_comp_s, (int, float))
                            and isinstance(prev_comp_e, (int, float))
                        ):
                            overlap = max(
                                0.0,
                                min(curr_acq_e, prev_comp_e)
                                - max(curr_acq_s, prev_comp_s),
                            )
                            timeline_records[i]["overlap_with_previous"] = overlap
                    if cancellation_ingress.is_cancelled or bool(failed_work_unit_keys):
                        for record in timeline_records:
                            if record.get("status") == "pending":
                                record["status"] = "cancelled"
                    payload = {
                        "event": "partition_timeline",
                        "stage": "partition_timeline",
                        "timeline": [dict(r) for r in timeline_records],
                    }
                progress_callback(payload)

            def check_cancellation() -> None:
                if cancellation_ingress.is_cancelled or (
                    should_cancel is not None and should_cancel()
                ):
                    cancellation_ingress.request_cancellation(
                        RuntimeCancellationRequest(
                            attempt=owners.attempt,
                        )
                    )
                    raise InterruptedError("Partition pipeline execution cancelled")

            use_overlap = pipeline_overlap and len(partitions) > 1

            try:
                check_cancellation()
                if not use_overlap:
                    for index, partition in enumerate(partitions):
                        check_cancellation()
                        active_partition = partition.partition_key
                        progress("partition_start", index)
                        runtime_inflight.add(active_partition)
                        acq_start = time.perf_counter()
                        acq_out = adapter.execute_acquisition(
                            partition,
                            should_cancel=should_cancel,
                            progress_callback=progress_callback,
                        )
                        acq_end = time.perf_counter()
                        timeline_records[index]["acquire_start"] = acq_start
                        timeline_records[index]["acquire_end"] = acq_end
                        resources.update(
                            (receipt.resource_key, receipt)
                            for receipt in acq_out.resources
                        )
                        acquisitions.extend(
                            compact_runtime_acquisition_receipt(receipt)
                            for receipt in acq_out.acquisitions
                        )
                        runtime_inflight.discard(active_partition)
                        if not acq_out.succeeded:
                            timeline_records[index]["status"] = "failed"
                            failure_msg = (
                                f": {acq_out.failure_detail}"
                                if acq_out.failure_detail is not None
                                else ""
                            )
                            exc = RuntimeError(
                                f"Partition acquisition failed{failure_msg}"
                            )
                            record_partition_failure(
                                partition, "partition-acquisition", exc
                            )
                            raise exc
                        check_cancellation()
                        progress("partition_compose", index)
                        comp_start = time.perf_counter()
                        try:
                            comp_out = adapter.execute_composition(
                                partition,
                                acq_out,
                                store,
                                retain_memory=retain_partition_memory
                                and len(partitions) == 1,
                                should_cancel=check_cancellation,
                            )
                        except Exception as exc:
                            timeline_records[index]["compose_end"] = time.perf_counter()
                            timeline_records[index]["status"] = "failed"
                            record_partition_failure(
                                partition, "partition-composition", exc
                            )
                            raise
                        comp_end = time.perf_counter()
                        timeline_records[index]["compose_start"] = comp_start
                        timeline_records[index]["compose_end"] = comp_end
                        timeline_records[index]["status"] = "completed"
                        composition_outputs.append(comp_out)
                        check_cancellation()
                        part_artifacts, part_settlements = (
                            adapter.create_partition_artifacts(partition, comp_out)
                        )
                        artifacts.extend(part_artifacts)
                        settlements.extend(part_settlements)
                        del acq_out, comp_out
                        gc.collect()
                        progress("partition_released", index + 1)
                else:
                    admission_credits = threading.Semaphore(2)
                    handoff_queue: queue.Queue[
                        tuple[
                            WorkPartition[TPartitionPayload],
                            PartitionAcquisitionOutput[TAcquisitionResult],
                        ]
                        | object
                    ] = queue.Queue(maxsize=1)
                    stop_event = threading.Event()
                    producer_exc: Exception | None = None
                    consumer_exc: Exception | None = None
                    pending_partitions: queue.Queue[
                        tuple[int, WorkPartition[TPartitionPayload]]
                    ] = queue.Queue()
                    for entry in enumerate(partitions):
                        pending_partitions.put(entry)

                    def is_stopped() -> bool:
                        return (
                            stop_event.is_set()
                            or cancellation_ingress.is_cancelled
                            or (should_cancel() if should_cancel is not None else False)
                        )

                    def producer_worker() -> None:
                        nonlocal producer_exc
                        try:
                            while True:
                                if is_stopped():
                                    break
                                acquired = False
                                while not is_stopped():
                                    if admission_credits.acquire(timeout=0.05):
                                        acquired = True
                                        break
                                if not acquired or is_stopped():
                                    if acquired:
                                        admission_credits.release()
                                    break
                                try:
                                    index, partition = pending_partitions.get_nowait()
                                except queue.Empty:
                                    admission_credits.release()
                                    break

                                progress("partition_start", index)
                                with thread_lock:
                                    runtime_inflight.add(partition.partition_key)
                                acq_start = time.perf_counter()
                                acq_out = adapter.execute_acquisition(
                                    partition,
                                    should_cancel=is_stopped,
                                    progress_callback=progress_callback,
                                )
                                acq_end = time.perf_counter()
                                with thread_lock:
                                    runtime_inflight.discard(partition.partition_key)
                                    timeline_records[index]["acquire_start"] = acq_start
                                    timeline_records[index]["acquire_end"] = acq_end
                                    resources.update(
                                        (receipt.resource_key, receipt)
                                        for receipt in acq_out.resources
                                    )
                                    acquisitions.extend(
                                        compact_runtime_acquisition_receipt(receipt)
                                        for receipt in acq_out.acquisitions
                                    )
                                if not acq_out.succeeded:
                                    with thread_lock:
                                        timeline_records[index]["status"] = "failed"
                                    admission_credits.release()
                                    failure_msg = (
                                        f": {acq_out.failure_detail}"
                                        if acq_out.failure_detail is not None
                                        else ""
                                    )
                                    exc = RuntimeError(
                                        f"Partition acquisition failed{failure_msg}"
                                    )
                                    record_partition_failure(
                                        partition, "partition-acquisition", exc
                                    )
                                    raise exc

                                put_done = False
                                while not is_stopped():
                                    try:
                                        handoff_queue.put(
                                            (partition, acq_out), timeout=0.05
                                        )
                                        put_done = True
                                        break
                                    except queue.Full:
                                        continue
                                if not put_done:
                                    admission_credits.release()
                                    break
                                del acq_out
                        except Exception as exc:
                            with thread_lock:
                                if producer_exc is None:
                                    producer_exc = exc
                                timeline_records[index]["acquire_end"] = (
                                    time.perf_counter()
                                )
                                timeline_records[index]["status"] = "failed"
                            record_partition_failure(
                                partition, "partition-acquisition", exc
                            )
                            stop_event.set()
                            cancellation_ingress.request_cancellation(
                                RuntimeCancellationRequest(attempt=owners.attempt)
                            )
                        finally:
                            while not is_stopped():
                                try:
                                    handoff_queue.put(_DONE_SENTINEL, timeout=0.05)
                                    break
                                except queue.Full:
                                    continue

                    def consumer_worker() -> None:
                        nonlocal consumer_exc
                        next_index = 0
                        finished_producers = 0
                        waiting: dict[
                            int,
                            tuple[
                                WorkPartition[TPartitionPayload],
                                PartitionAcquisitionOutput[TAcquisitionResult],
                            ],
                        ] = {}
                        item: object
                        try:
                            while True:
                                if is_stopped():
                                    break
                                if next_index in waiting:
                                    item = waiting.pop(next_index)
                                else:
                                    try:
                                        item = handoff_queue.get(timeout=0.05)
                                    except queue.Empty:
                                        continue
                                if item is _DONE_SENTINEL:
                                    finished_producers += 1
                                    if finished_producers == acquisition_workers:
                                        break
                                    continue
                                if not isinstance(item, tuple):
                                    continue
                                if item[0].index != next_index:
                                    waiting[item[0].index] = item
                                    continue
                                partition: WorkPartition[TPartitionPayload] = item[0]
                                acq_out: PartitionAcquisitionOutput[
                                    TAcquisitionResult
                                ] = item[1]
                                item = None
                                credit_released = False
                                try:
                                    if is_stopped():
                                        break
                                    progress("partition_compose", partition.index)
                                    comp_start = time.perf_counter()
                                    comp_out = adapter.execute_composition(
                                        partition,
                                        acq_out,
                                        store,
                                        retain_memory=retain_partition_memory
                                        and len(partitions) == 1,
                                        should_cancel=is_stopped,
                                    )
                                    comp_end = time.perf_counter()
                                    with thread_lock:
                                        composition_outputs.append(comp_out)
                                        timeline_records[partition.index][
                                            "compose_start"
                                        ] = comp_start
                                        timeline_records[partition.index][
                                            "compose_end"
                                        ] = comp_end
                                        timeline_records[partition.index]["status"] = (
                                            "completed"
                                        )
                                    part_artifacts, part_settlements = (
                                        adapter.create_partition_artifacts(
                                            partition, comp_out
                                        )
                                    )
                                    with thread_lock:
                                        artifacts.extend(part_artifacts)
                                        settlements.extend(part_settlements)
                                    del acq_out, comp_out
                                    gc.collect()
                                    progress("partition_released", partition.index + 1)
                                    next_index += 1
                                finally:
                                    if not credit_released:
                                        admission_credits.release()
                                        credit_released = True
                        except Exception as exc:
                            consumer_exc = exc
                            if "partition" in locals():
                                with thread_lock:
                                    timeline_records[partition.index]["compose_end"] = (
                                        time.perf_counter()
                                    )
                                    timeline_records[partition.index]["status"] = (
                                        "failed"
                                    )
                                record_partition_failure(
                                    partition, "partition-composition", exc
                                )
                            stop_event.set()
                            cancellation_ingress.request_cancellation(
                                RuntimeCancellationRequest(
                                    attempt=owners.attempt,
                                )
                            )
                            admission_credits.release()
                            admission_credits.release()

                    producer_threads = [
                        threading.Thread(
                            target=producer_worker,
                            name=f"pipeline-producer-{index}",
                        )
                        for index in range(acquisition_workers)
                    ]
                    consumer_thread = threading.Thread(
                        target=consumer_worker,
                        name="pipeline-consumer",
                    )
                    for producer_thread in producer_threads:
                        producer_thread.start()
                    consumer_thread.start()
                    for producer_thread in producer_threads:
                        producer_thread.join()
                    consumer_thread.join()

                    if consumer_exc is not None and not isinstance(
                        consumer_exc, InterruptedError
                    ):
                        raise consumer_exc
                    if producer_exc is not None:
                        raise producer_exc
                    if consumer_exc is not None:
                        raise consumer_exc
                    if cancellation_ingress.is_cancelled or (
                        should_cancel is not None and should_cancel()
                    ):
                        cancellation_ingress.request_cancellation(
                            RuntimeCancellationRequest(
                                attempt=owners.attempt,
                            )
                        )
                        raise InterruptedError("Partition pipeline execution cancelled")

                check_cancellation()
                store.seal()
                progress("global_closure", len(partitions))
                global_closure_key = getattr(
                    adapter, "global_closure_work_unit_key", None
                )
                try:
                    application_result = adapter.finalize_application(
                        store, composition_outputs
                    )
                    check_cancellation()
                    if global_closure_key is not None:
                        closure_artifacts, closure_settlements = (
                            adapter.create_global_closure_artifacts(application_result)
                        )
                        artifacts.extend(closure_artifacts)
                        settlements.extend(closure_settlements)
                except Exception as exc:
                    if not (
                        isinstance(exc, InterruptedError)
                        or cancellation_ingress.is_cancelled
                    ):
                        if global_closure_key is not None:
                            with thread_lock:
                                failed_work_unit_keys.add(global_closure_key)
                                failed_stage = "partition-pipeline-completion"
                                failed_exc = exc
                    raise

                emit_timeline()
            except Exception as exc:
                failure = exc
                completed_keys = {receipt.work_unit_key for receipt in settlements}
                global_closure_key = getattr(
                    adapter, "global_closure_work_unit_key", None
                )
                if (
                    len(completed_keys) == len(work_unit_keys)
                    and global_closure_key is None
                ):
                    completed_keys.remove(settlements.pop().work_unit_key)

                is_cancellation_accepted = (
                    cancellation_ingress.accepted_request is not None
                )
                if not is_cancellation_accepted and (
                    isinstance(exc, InterruptedError)
                    or cancellation_ingress.is_cancelled
                    or (should_cancel is not None and should_cancel())
                ):
                    cancellation_ingress.request_cancellation(
                        RuntimeCancellationRequest(attempt=owners.attempt)
                    )
                    is_cancellation_accepted = (
                        cancellation_ingress.accepted_request is not None
                    )

                unsettled_keys = [k for k in work_unit_keys if k not in completed_keys]
                if (
                    not is_cancellation_accepted
                    and not failed_work_unit_keys
                    and unsettled_keys
                ):
                    failed_work_unit_keys.add(unsettled_keys[0])
                    failed_stage = "partition-pipeline-completion"
                    failed_exc = exc

                for key in work_unit_keys:
                    if key not in completed_keys:
                        if key in failed_work_unit_keys:
                            settlements.append(
                                mint_work_unit_settlement(
                                    attempt=owners.attempt,
                                    work_unit_key=key,
                                    outcome=WorkUnitTerminalOutcome.FAILED,
                                    failure_detail=WorkUnitFailureDetail(
                                        category=WorkUnitFailureCategory.EXECUTION_ERROR,
                                        stage=failed_stage,
                                        source_reference=key,
                                        message=(
                                            str(failed_exc or exc).strip()
                                            or type(failed_exc or exc).__name__
                                        )[:2000],
                                    ),
                                )
                            )
                        elif is_cancellation_accepted or bool(failed_work_unit_keys):
                            if not is_cancellation_accepted:
                                cancellation_ingress.request_cancellation(
                                    RuntimeCancellationRequest(attempt=owners.attempt)
                                )
                                is_cancellation_accepted = True
                            settlements.append(
                                mint_work_unit_settlement(
                                    attempt=owners.attempt,
                                    work_unit_key=key,
                                    outcome=WorkUnitTerminalOutcome.CANCELLED,
                                    failure_detail=WorkUnitFailureDetail(
                                        category=WorkUnitFailureCategory.CANCELLATION,
                                        stage="partition-pipeline-cancellation",
                                        source_reference=key,
                                        message=(
                                            "work unit cancelled due to attempt "
                                            "cancellation"
                                        ),
                                    ),
                                )
                            )
                        else:
                            settlements.append(
                                mint_work_unit_settlement(
                                    attempt=owners.attempt,
                                    work_unit_key=key,
                                    outcome=WorkUnitTerminalOutcome.FAILED,
                                    failure_detail=WorkUnitFailureDetail(
                                        category=WorkUnitFailureCategory.EXECUTION_ERROR,
                                        stage=failed_stage,
                                        source_reference=key,
                                        message=(
                                            str(exc).strip() or type(exc).__name__
                                        )[:2000],
                                    ),
                                )
                            )
                emit_timeline()

            for reservation in resource_reservations:
                if reservation.resource_key not in resources:
                    unknown = any(
                        reservation.resource_key.startswith(
                            f"acquisition-session:{inflight}:"
                        )
                        or f":{inflight}:" in reservation.resource_key
                        for inflight in runtime_inflight
                    )
                    resources[reservation.resource_key] = (
                        RuntimeResourceSettlementReceipt(
                            attempt=owners.attempt,
                            resource_key=reservation.resource_key,
                            open_outcome=(
                                RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS
                                if unknown
                                else RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED
                            ),
                            cleanup_outcome=(
                                RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS
                                if unknown
                                else RuntimeResourceCleanupOutcome.SUCCEEDED
                            ),
                        )
                    )
            return RuntimeWorkerBatchReceipt(
                settlements=tuple(settlements),
                resource_settlements=tuple(resources.values()),
                artifact_registrations=tuple(artifacts),
                acquisitions=tuple(acquisitions),
            )

        try:
            recovery_marker.record_launch(runtime)
            def provide_worker_batch_and_cleanup(
                aggregate: object,
            ) -> RuntimeWorkerBatchReceipt:
                try:
                    batch = provide_worker_batch(aggregate)
                except Exception:
                    if acquisition_cleanup is not None:
                        try:
                            acquisition_cleanup()
                        except Exception:
                            pass
                    raise
                if acquisition_cleanup is None:
                    return batch
                try:
                    acquisition_cleanup()
                except Exception as exc:
                    nonlocal failure
                    failure = exc
                    cleanup_failure = WorkUnitFailureDetail(
                        category=WorkUnitFailureCategory.EXECUTION_ERROR,
                        stage="partition-acquisition-pool-shutdown",
                        source_reference=owners.attempt.processing_attempt_id,
                        message=(str(exc).strip() or type(exc).__name__)[:2000],
                    )
                    return RuntimeWorkerBatchReceipt(
                        settlements=tuple(
                            mint_work_unit_settlement(
                                attempt=owners.attempt,
                                work_unit_key=key,
                                outcome=WorkUnitTerminalOutcome.FAILED,
                                failure_detail=cleanup_failure,
                            )
                            for key in work_unit_keys
                        ),
                        resource_settlements=batch.resource_settlements,
                        artifact_registrations=batch.artifact_registrations,
                        acquisitions=batch.acquisitions,
                    )
                return batch

            finalization = execute_runtime_attempt(
                runtime,
                resource_reservations,
                worker_batch_provider=provide_worker_batch_and_cleanup,
                telemetry_provider=telemetry_provider,
                persister=recovery_marker,
                cancellation_ingress=cancellation_ingress,
            )
            if failure is not None:
                raise RuntimeError(
                    f"Partition pipeline execution failed before batch publication: "
                    f"{failure}"
                ) from failure
            outcome = finalization.supervisor.terminal_outcome
            if outcome is not AttemptTerminalOutcome.SUCCEEDED or (
                application_result is None
            ):
                raise RuntimeError(
                    "Partition pipeline runtime did not complete atomically"
                )
            progress("batch_completed", len(partitions))
            return adapter.build_workflow_result(
                finalization, application_result, store
            )
        except Exception as exc:
            try:
                checkpoint = store.preserve_failed_results()
            except Exception as preservation_error:
                # Keep the original failure and every sealed file even if the
                # diagnostic metadata cannot be written (for example, disk full).
                exc.add_note(f"Could not retain replay metadata: {preservation_error}")
                raise exc
            if checkpoint is not None:
                exc.add_note(f"Completed partition results retained at {checkpoint}")
            raise
