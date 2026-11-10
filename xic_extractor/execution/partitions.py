"""Partition pipeline inputs, adapter protocol and scheduler."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import (
    Callable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import (
    Any,
    Generic,
    Protocol,
    TypeVar,
)

from xic_extractor.execution.result_store import (
    SealedPartition,
    StreamedResultStore,
)

TPayload = TypeVar("TPayload")
TAcquisitionResult = TypeVar("TAcquisitionResult")
TCompositionResult = TypeVar("TCompositionResult")
TPartitionPayload = TypeVar("TPartitionPayload")
TApplicationResult = TypeVar("TApplicationResult")
TFinalResult = TypeVar("TFinalResult", covariant=True)


@dataclass(frozen=True, slots=True)
class WorkPartition(Generic[TPayload]):
    index: int
    partition_key: str
    payload: TPayload


@dataclass(frozen=True, slots=True)
class PartitionPipelinePhase(Generic[TPartitionPayload]):
    """An ordered partition batch with a barrier before admission.

    Phases divide the adapter's declared partitions into consecutive slices;
    partition indices and work/resource identities remain global to the attempt.
    """

    name: str
    partitions: Sequence[WorkPartition[TPartitionPayload]]
    prepare_before: Callable[[StreamedResultStore, Callable[[], None]], None] | None = (
        None
    )


@dataclass(frozen=True, slots=True)
class PartitionAcquisitionOutput(Generic[TAcquisitionResult]):
    """One partition's acquisition; ``failure`` is set when any sample failed.

    An ``InterruptedError`` failure means the partition observed cancellation.
    """

    result: TAcquisitionResult
    failure: Exception | None = None

    @property
    def succeeded(self) -> bool:
        return self.failure is None


@dataclass(frozen=True, slots=True)
class PartitionCompositionOutput(Generic[TCompositionResult]):
    result: TCompositionResult
    sealed: SealedPartition


class PartitionPipelineAdapter(
    Protocol[
        TPartitionPayload,
        TAcquisitionResult,
        TCompositionResult,
        TApplicationResult,
        TFinalResult,
    ]
):
    @property
    def partitions(self) -> Sequence[WorkPartition[TPartitionPayload]]: ...

    def get_shared_objects(self) -> tuple[object, ...]: ...

    def execute_acquisition(
        self,
        partition: WorkPartition[TPartitionPayload],
        *,
        should_cancel: Callable[[], bool] | None = None,
        progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    ) -> PartitionAcquisitionOutput[TAcquisitionResult]: ...

    def execute_composition(
        self,
        partition: WorkPartition[TPartitionPayload],
        acquisition_output: PartitionAcquisitionOutput[TAcquisitionResult],
        store: StreamedResultStore,
        *,
        retain_memory: bool = False,
        should_cancel: Callable[[], None] | Callable[[], bool] | None = None,
    ) -> PartitionCompositionOutput[TCompositionResult]: ...

    def finalize_application(
        self,
        store: StreamedResultStore,
        composition_outputs: Sequence[PartitionCompositionOutput[TCompositionResult]],
    ) -> TApplicationResult: ...

    def build_workflow_result(
        self,
        application_result: TApplicationResult,
        store: StreamedResultStore,
    ) -> TFinalResult: ...


_DONE_SENTINEL = object()


class PartitionPipelineScheduler:
    """Generic orchestrator executing partitioned workloads."""

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
        progress_callback: Callable[[Mapping[str, object]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        pipeline_overlap: bool = True,
        phases: Sequence[PartitionPipelinePhase[TPartitionPayload]] | None = None,
        acquisition_workers: int = 1,
        retain_partition_memory: bool = False,
        store_factory: Callable[[Path, tuple[object, ...]], StreamedResultStore] = (
            StreamedResultStore
        ),
        acquisition_cleanup: Callable[[], None] | None = None,
    ) -> TFinalResult:
        """Execute finite partitions; any failure or cancellation fails the batch."""
        if type(acquisition_workers) is not int or acquisition_workers not in (1, 2):
            raise ValueError("acquisition_workers must be 1 or 2")
        pipeline_phases = (
            tuple(phases)
            if phases is not None
            else (PartitionPipelinePhase("partition-pipeline", adapter.partitions),)
        )
        partitions = tuple(p for phase in pipeline_phases for p in phase.partitions)
        cancelled = threading.Event()

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        store = store_factory(
            Path(mkdtemp(prefix=".partition_spool-", dir=output_dir)),
            adapter.get_shared_objects(),
        )

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

        def run_partitions() -> TApplicationResult:
            composition_outputs: list[
                PartitionCompositionOutput[TCompositionResult]
            ] = []
            partition_failed = False
            timeline_emitted = False
            thread_lock = threading.Lock()

            def record_partition_failure(
                partition: WorkPartition[TPartitionPayload],
                exc: Exception,
            ) -> None:
                nonlocal partition_failed
                partition_cancelled = (
                    isinstance(exc, InterruptedError) and cancelled.is_set()
                )
                with thread_lock:
                    timeline_by_index[partition.index]["status"] = (
                        "cancelled" if partition_cancelled else "failed"
                    )
                    if not partition_cancelled:
                        partition_failed = True

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

            timeline_by_index = {
                p.index: r for p, r in zip(partitions, timeline_records, strict=True)
            }

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
                    if cancelled.is_set() or partition_failed:
                        for record in timeline_records:
                            if record.get("status") == "pending":
                                record["status"] = "cancelled"
                    payload = {
                        "event": "partition_timeline",
                        "stage": "partition_timeline",
                        "timeline": [dict(r) for r in timeline_records],
                    }
                progress_callback(payload)

            def raise_acquisition_failure(
                output: PartitionAcquisitionOutput[Any],
                partition: WorkPartition[TPartitionPayload],
            ) -> None:
                failure = output.failure
                if isinstance(failure, InterruptedError):
                    cancelled.set()
                    exc: Exception = InterruptedError("Partition acquisition cancelled")
                else:
                    exc = RuntimeError(
                        "Partition acquisition failed: "
                        f"{type(failure).__name__}: {failure}"
                    )
                record_partition_failure(partition, exc)
                raise exc from failure

            def check_cancellation() -> None:
                if cancelled.is_set() or (
                    should_cancel is not None and should_cancel()
                ):
                    cancelled.set()
                    raise InterruptedError("Partition pipeline execution cancelled")

            try:
                check_cancellation()
                phase_offset = 0
                for phase in pipeline_phases:
                    check_cancellation()
                    if phase.prepare_before is not None:
                        phase.prepare_before(store, check_cancellation)
                        check_cancellation()
                    use_overlap = pipeline_overlap and len(phase.partitions) > 1
                    if not use_overlap:
                        for index, partition in enumerate(
                            phase.partitions, start=phase_offset
                        ):
                            check_cancellation()
                            progress("partition_start", index)
                            acq_start = time.perf_counter()
                            acq_out = adapter.execute_acquisition(
                                partition,
                                should_cancel=should_cancel,
                                progress_callback=progress_callback,
                            )
                            acq_end = time.perf_counter()
                            timeline_records[index]["acquire_start"] = acq_start
                            timeline_records[index]["acquire_end"] = acq_end
                            if not acq_out.succeeded:
                                raise_acquisition_failure(acq_out, partition)
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
                                timeline_records[index]["compose_end"] = (
                                    time.perf_counter()
                                )
                                record_partition_failure(partition, exc)
                                raise
                            comp_end = time.perf_counter()
                            timeline_records[index]["compose_start"] = comp_start
                            timeline_records[index]["compose_end"] = comp_end
                            timeline_records[index]["status"] = "completed"
                            composition_outputs.append(comp_out)
                            check_cancellation()
                            del acq_out, comp_out
                            progress("partition_released", index + 1)
                    else:
                        # Composition keeps one partition resident while each
                        # acquisition lane can prepare its next partition.
                        admission_credits = threading.Semaphore(acquisition_workers + 1)
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
                        for entry in enumerate(phase.partitions, start=phase_offset):
                            pending_partitions.put(entry)

                        def is_stopped(stop_event=stop_event) -> bool:
                            return (
                                stop_event.is_set()
                                or cancelled.is_set()
                                or (
                                    should_cancel()
                                    if should_cancel is not None
                                    else False
                                )
                            )

                        def producer_worker(
                            admission_credits=admission_credits,
                            pending_partitions=pending_partitions,
                            handoff_queue=handoff_queue,
                            stop_event=stop_event,
                        ) -> None:
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
                                        index, partition = (
                                            pending_partitions.get_nowait()
                                        )
                                    except queue.Empty:
                                        admission_credits.release()
                                        break

                                    progress("partition_start", index)
                                    acq_start = time.perf_counter()
                                    acq_out = adapter.execute_acquisition(
                                        partition,
                                        should_cancel=is_stopped,
                                        progress_callback=progress_callback,
                                    )
                                    acq_end = time.perf_counter()
                                    with thread_lock:
                                        timeline_records[index]["acquire_start"] = (
                                            acq_start
                                        )
                                        timeline_records[index]["acquire_end"] = acq_end
                                    if not acq_out.succeeded:
                                        admission_credits.release()
                                        raise_acquisition_failure(acq_out, partition)

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
                                record_partition_failure(partition, exc)
                                stop_event.set()
                                cancelled.set()
                            finally:
                                while not is_stopped():
                                    try:
                                        handoff_queue.put(_DONE_SENTINEL, timeout=0.05)
                                        break
                                    except queue.Full:
                                        continue

                        def consumer_worker(
                            phase=phase,
                            handoff_queue=handoff_queue,
                            admission_credits=admission_credits,
                            stop_event=stop_event,
                        ) -> None:
                            nonlocal consumer_exc
                            next_index = phase.partitions[0].index
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
                                    partition: WorkPartition[TPartitionPayload] = item[
                                        0
                                    ]
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
                                            timeline_by_index[partition.index][
                                                "compose_start"
                                            ] = comp_start
                                            timeline_by_index[partition.index][
                                                "compose_end"
                                            ] = comp_end
                                            timeline_by_index[partition.index][
                                                "status"
                                            ] = "completed"
                                        del acq_out, comp_out
                                        progress(
                                            "partition_released", partition.index + 1
                                        )
                                        next_index += 1
                                    finally:
                                        if not credit_released:
                                            admission_credits.release()
                                            credit_released = True
                            except Exception as composition_error:
                                consumer_exc = composition_error
                                if "partition" in locals():
                                    with thread_lock:
                                        timeline_by_index[partition.index][
                                            "compose_end"
                                        ] = time.perf_counter()
                                    record_partition_failure(
                                        partition, composition_error
                                    )
                                stop_event.set()
                                cancelled.set()
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
                        check_cancellation()
                    phase_offset += len(phase.partitions)

                check_cancellation()
                store.seal()
                progress("global_closure", len(partitions))
                application_result = adapter.finalize_application(
                    store, composition_outputs
                )
                check_cancellation()
                return application_result
            except Exception:
                # Any failure leaves the remaining partitions unadmitted.
                partition_failed = True
                raise
            finally:
                emit_timeline()

        def close_acquisition() -> None:
            if acquisition_cleanup is not None:
                acquisition_cleanup()

        try:
            try:
                application_result = run_partitions()
            except Exception as failure:
                try:
                    close_acquisition()
                except Exception as cleanup_error:
                    failure.add_note(
                        f"Acquisition cleanup also failed: {cleanup_error}"
                    )
                raise RuntimeError(
                    "Partition pipeline execution failed before batch publication: "
                    f"{failure}"
                ) from failure
            try:
                close_acquisition()
            except Exception as failure:
                raise RuntimeError(
                    "Partition pipeline execution failed before batch publication: "
                    f"{failure}"
                ) from failure
            progress("batch_completed", len(partitions))
            return adapter.build_workflow_result(application_result, store)
        except Exception as exc:
            try:
                checkpoint = store.preserve_failed_results()
            except Exception as preservation_error:
                # Keep the original failure and every sealed file even if the
                # diagnostic metadata cannot be written (for example, disk full).
                exc.add_note(f"Could not retain replay metadata: {preservation_error}")
                raise exc from preservation_error
            if checkpoint is not None:
                exc.add_note(f"Completed partition results retained at {checkpoint}")
            raise
