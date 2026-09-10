"""Generic partition pipeline execution engine scheduler."""

from __future__ import annotations

import gc
from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import mkdtemp
from typing import TypeVar

from xic_extractor.execution_engine.contracts import (
    PartitionCompositionOutput,
    PartitionPipelineAdapter,
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
        retain_partition_memory: bool = False,
        execute_runtime_attempt: Callable[..., RuntimeFinalizationReceipt] = (
            execute_runtime_attempt
        ),
        store_factory: Callable[[Path, tuple[object, ...]], StreamedResultStore] = (
            StreamedResultStore
        ),
    ) -> TFinalResult:
        """Execute finite partitions under one runtime attempt."""
        partitions = adapter.partitions
        owners = adapter.processing_owners
        work_unit_keys = adapter.work_unit_keys
        resource_reservations = adapter.resource_reservations

        runtime = start_runtime_attempt(
            attempt=owners.attempt,
            work_unit_keys=tuple(work_unit_keys),
            owner_completeness_attestation=adapter.owner_completeness_attestation,
        )
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
            runtime_inflight: str | None = None

            def check_cancellation() -> None:
                if should_cancel is not None and should_cancel():
                    raise InterruptedError("Partition pipeline execution cancelled")

            try:
                check_cancellation()
                for index, partition in enumerate(partitions):
                    check_cancellation()
                    active_partition = partition.partition_key
                    progress("partition_start", index)
                    runtime_inflight = active_partition
                    acq_out = adapter.execute_acquisition(
                        partition,
                        should_cancel=should_cancel,
                        progress_callback=progress_callback,
                    )
                    resources.update(
                        (receipt.resource_key, receipt)
                        for receipt in acq_out.resources
                    )
                    acquisitions.extend(
                        compact_runtime_acquisition_receipt(receipt)
                        for receipt in acq_out.acquisitions
                    )
                    runtime_inflight = None
                    if not acq_out.succeeded:
                        failure_msg = (
                            f": {acq_out.failure_detail}"
                            if acq_out.failure_detail is not None
                            else ""
                        )
                        raise RuntimeError(f"Partition acquisition failed{failure_msg}")
                    check_cancellation()
                    progress("partition_compose", index)
                    comp_out = adapter.execute_composition(
                        partition,
                        acq_out,
                        store,
                        retain_memory=retain_partition_memory
                        and len(partitions) == 1,
                        should_cancel=check_cancellation,
                    )
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
                check_cancellation()
                store.seal()
                progress("global_closure", len(partitions))
                application_result = adapter.finalize_application(
                    store, composition_outputs
                )
                check_cancellation()
            except Exception as exc:
                failure = exc
                completed_keys = {receipt.work_unit_key for receipt in settlements}
                # A closure failure invalidates the batch even if all parts finished.
                if len(completed_keys) == len(work_unit_keys):
                    completed_keys.remove(settlements.pop().work_unit_key)
                for key in work_unit_keys:
                    if key not in completed_keys:
                        settlements.append(
                            mint_work_unit_settlement(
                                attempt=owners.attempt,
                                work_unit_key=key,
                                outcome=WorkUnitTerminalOutcome.FAILED,
                                failure_detail=WorkUnitFailureDetail(
                                    category=WorkUnitFailureCategory.EXECUTION_ERROR,
                                    stage="partition-pipeline-completion",
                                    source_reference=key,
                                    message=(
                                        str(exc).strip() or type(exc).__name__
                                    )[:2000],
                                ),
                            )
                        )
            for reservation in resource_reservations:
                if reservation.resource_key not in resources:
                    unknown = (
                        runtime_inflight is not None
                        and (
                            reservation.resource_key.startswith(
                                f"acquisition-session:{runtime_inflight}:"
                            )
                            or f":{runtime_inflight}:" in reservation.resource_key
                        )
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
            finalization = execute_runtime_attempt(
                runtime,
                resource_reservations,
                worker_batch_provider=provide_worker_batch,
                telemetry_provider=telemetry_provider,
                persister=recovery_marker,
            )
            if failure is not None:
                store.cleanup()
                raise RuntimeError(
                    f"Partition pipeline execution failed before batch publication: "
                    f"{failure}"
                ) from failure
            outcome = finalization.supervisor.terminal_outcome
            if outcome is not AttemptTerminalOutcome.SUCCEEDED or (
                application_result is None
            ):
                store.cleanup()
                raise RuntimeError(
                    "Partition pipeline runtime did not complete atomically"
                )
            progress("batch_completed", len(partitions))
            return adapter.build_workflow_result(
                finalization, application_result, store
            )
        except Exception:
            store.cleanup()
            raise
