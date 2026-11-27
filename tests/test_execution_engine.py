"""Unit tests for the generic execution engine core."""

from __future__ import annotations

import io
import json
import os
import pickle
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)
from xic_extractor.execution.partitions import (
    PartitionAcquisitionOutput,
    PartitionCompositionOutput,
    PartitionPipelinePhase,
    PartitionPipelineScheduler,
    WorkPartition,
)
from xic_extractor.execution.result_store import (
    StreamedResultStore,
    WrittenPartition,
    _OwnerUnpickler,
)
from xic_extractor.execution.workers import WorkerAdmissionPool


@dataclass(frozen=True)
class MockPayload:
    child_outputs: tuple
    method_assessments: tuple = ()
    excluded_candidates: tuple = ()
    label: str = "partition"


@dataclass(frozen=True)
class CustomPayload:
    custom_items: tuple
    label: str = "custom"


@dataclass(frozen=True)
class RefPayload:
    child_outputs: tuple
    ref: object = None


@dataclass(frozen=True)
class SharedProcessTask:
    owner: object
    value: int


@dataclass(frozen=True)
class SharedProcessResult:
    owner: object
    value: int
    worker_pid: int
    initialized: bool


_SHARED_PROCESS_INITIALIZED = False


def _initialize_shared_process_test() -> None:
    global _SHARED_PROCESS_INITIALIZED
    _SHARED_PROCESS_INITIALIZED = True


def _execute_shared_process_task(task: SharedProcessTask) -> SharedProcessResult:
    return SharedProcessResult(
        task.owner,
        task.value * 10,
        os.getpid(),
        _SHARED_PROCESS_INITIALIZED,
    )


_LANE_ITEMS: list[int] = []


def _remember_lane_item(task: SharedProcessTask) -> tuple[int, int, tuple[int, ...]]:
    _LANE_ITEMS.append(task.value)
    return os.getpid(), task.value, tuple(_LANE_ITEMS)


def _forget_lane_items(_request: None) -> None:
    _LANE_ITEMS.clear()


def test_streamed_result_store_basic_roundtrip(tmp_path: Path) -> None:
    store = StreamedResultStore(tmp_path / "spool", ())
    payload = MockPayload(
        child_outputs=(1, 2, 3),
        method_assessments=("assess_1",),
        excluded_candidates=("ex_1", "ex_2"),
    )
    entry = store.append("p-0", payload)
    assert entry.index == 0
    assert entry.key == "p-0"

    restored = store.read(0)
    assert restored.child_outputs == (1, 2, 3)
    assert restored.method_assessments == ("assess_1",)
    assert restored.excluded_candidates == ("ex_1", "ex_2")

    store.seal()
    assert tuple(store.sequence("child_outputs")) == (1, 2, 3)
    assert tuple(store.sequence("method_assessments")) == ("assess_1",)
    assert store.read_selected(0, "excluded_candidates", (1, 0)) == ("ex_2", "ex_1")


@pytest.mark.parametrize("duplicate_shared_root", [False, True])
def test_streamed_result_store_shared_objects_identity(
    tmp_path: Path, duplicate_shared_root: bool
) -> None:
    shared_config = {"anchor": 42}
    shared_ref = ["shared_authority"]
    shared = (shared_config, shared_ref)
    if duplicate_shared_root:
        shared += (shared_config,)
    store = StreamedResultStore(tmp_path / "spool", shared)

    payload = RefPayload(
        child_outputs=((shared_config, shared_ref),), ref=shared_config
    )
    store.append("p-0", payload)
    restored = store.read(0)

    assert restored.ref is shared_config
    assert restored.child_outputs[0][0] is shared_config
    assert restored.child_outputs[0][1] is shared_ref


def test_streamed_result_store_unknown_shared_identity_rejected() -> None:
    shared_objects = (object(),)
    bio = io.BytesIO()
    bio.write(pickle.PROTO + b"\x04")
    bio.write(pickle.BINGET + b"\x63")
    bio.write(pickle.STOP)
    bio.seek(0)

    with pytest.raises(pickle.UnpicklingError):
        _OwnerUnpickler(bio, shared_objects).load()


def test_streamed_result_store_individually_addressable_fields(
    tmp_path: Path,
) -> None:
    store = StreamedResultStore(
        tmp_path / "spool",
        (),
        individually_addressable_fields=frozenset({"custom_items"}),
    )
    payload = CustomPayload(custom_items=("item_0", "item_1", "item_2"))
    store.append("p-0", payload)
    store.seal()

    selected = store.read_selected(0, "custom_items", (2, 0))
    assert selected == ("item_2", "item_0")

    with pytest.raises(
        ValueError, match="field does not have individually addressable results"
    ):
        store.read_selected(0, "label", (0,))


def test_streamed_result_store_cleanup_idempotent(tmp_path: Path) -> None:
    spool_dir = tmp_path / "spool"
    store = StreamedResultStore(spool_dir, ())
    store.append("p-0", MockPayload(child_outputs=(1, 2)))
    assert spool_dir.exists()

    store.cleanup()
    assert not spool_dir.exists()

    store.cleanup()
    assert not spool_dir.exists()


class MockAdapter:
    def __init__(
        self,
        owners: object,
        *,
        num_partitions: int = 2,
        fail_acquisition_index: int | None = None,
        fail_composition_index: int | None = None,
        acquisition_delay: float = 0.0,
        composition_delay: float = 0.0,
        on_acquisition: Any = None,
        on_composition: Any = None,
    ):
        self._owners = owners
        self._partitions = tuple(
            WorkPartition(
                index=i,
                partition_key=f"partition-{i:05d}",
                payload={"partition_idx": i},
            )
            for i in range(num_partitions)
        )
        self.fail_acquisition_index = fail_acquisition_index
        self.fail_composition_index = fail_composition_index
        self.acquisition_delay = acquisition_delay
        self.composition_delay = composition_delay
        self.on_acquisition = on_acquisition
        self.on_composition = on_composition

    @property
    def partitions(self) -> tuple[WorkPartition[dict[str, int]], ...]:
        return self._partitions

    def get_shared_objects(self) -> tuple[object, ...]:
        return (self._owners,)

    def execute_acquisition(
        self,
        partition: WorkPartition[dict[str, int]],
        *,
        should_cancel=None,
        progress_callback=None,
    ) -> PartitionAcquisitionOutput[dict[str, Any]]:
        if self.acquisition_delay > 0:
            time.sleep(self.acquisition_delay)
        if self.on_acquisition is not None:
            self.on_acquisition(partition.index)
        if self.fail_acquisition_index == partition.index:
            return PartitionAcquisitionOutput(
                result={},
                failure=RuntimeError("simulated acquisition failure"),
            )
        return PartitionAcquisitionOutput(result={"acq_done": partition.index})

    def execute_composition(
        self,
        partition: WorkPartition[dict[str, int]],
        acquisition_output: PartitionAcquisitionOutput[dict[str, Any]],
        store: StreamedResultStore,
        *,
        should_cancel=None,
    ) -> PartitionCompositionOutput[dict[str, Any]]:
        if self.composition_delay > 0:
            time.sleep(self.composition_delay)
        if self.on_composition is not None:
            self.on_composition(partition.index)
        if self.fail_composition_index == partition.index:
            raise RuntimeError("simulated composition exception")
        payload = MockPayload(
            child_outputs=(f"child-{partition.index}",),
            method_assessments=(f"assess-{partition.index}",),
            excluded_candidates=(f"ex-{partition.index}",),
        )
        sealed = store.append(partition.partition_key, payload)
        return PartitionCompositionOutput(
            result={"comp_done": partition.index},
            sealed=sealed,
        )

    def finalize_application(
        self,
        store: StreamedResultStore,
        composition_outputs: tuple[PartitionCompositionOutput[dict[str, Any]], ...],
    ) -> dict[str, Any]:
        return {"total_children": len(store.sequence("child_outputs"))}

    def build_workflow_result(
        self,
        application_result: dict[str, Any],
        store: StreamedResultStore,
    ) -> dict[str, Any]:
        return {
            "application_result": application_result,
            "child_outputs": tuple(store.sequence("child_outputs")),
        }


def test_scheduler_execute_success(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(fixture.samples, num_partitions=2)
    output_dir = tmp_path / "output"

    progress_events: list[dict[str, Any]] = []

    result = PartitionPipelineScheduler.execute(
        adapter,
        output_dir=output_dir,
        progress_callback=progress_events.append,
        pipeline_overlap=False,
    )

    assert result["application_result"]["total_children"] == 2
    assert result["child_outputs"] == ("child-0", "child-1")

    stages = [
        ev["stage"] for ev in progress_events if ev.get("event") == "partition_progress"
    ]
    assert stages == [
        "partition_start",
        "partition_compose",
        "partition_released",
        "partition_start",
        "partition_compose",
        "partition_released",
        "global_closure",
        "batch_completed",
    ]


@pytest.mark.parametrize("pipeline_overlap", [False, True])
def test_scheduler_does_not_force_full_gc_after_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pipeline_overlap: bool,
) -> None:
    import gc

    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(fixture.samples, num_partitions=2)
    explicit_collects: list[int] = []
    monkeypatch.setattr(
        gc,
        "collect",
        lambda: explicit_collects.append(1),
    )

    result = PartitionPipelineScheduler.execute(
        adapter,
        output_dir=tmp_path / "gc-output",
        pipeline_overlap=pipeline_overlap,
    )

    assert result["application_result"]["total_children"] == 2
    assert explicit_collects == []


def test_scheduler_closes_acquisition_resources_before_terminal_acknowledgement(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1",)
    )
    adapter = MockAdapter(fixture.samples, num_partitions=2)
    cleanup_calls: list[bool] = []

    def cleanup() -> None:
        cleanup_calls.append(True)

    def build_workflow_result(application_result, store):
        assert cleanup_calls == [True]
        return original_build(application_result, store)

    original_build = adapter.build_workflow_result
    adapter.build_workflow_result = build_workflow_result

    result = PartitionPipelineScheduler.execute(
        adapter,
        output_dir=tmp_path / "cleanup-output",
        acquisition_cleanup=cleanup,
    )

    assert result["application_result"]["total_children"] == 2
    assert cleanup_calls == [True]


def test_scheduler_cleanup_failure_prevents_successful_terminal_acknowledgement(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1",)
    )
    adapter = MockAdapter(fixture.samples, num_partitions=1)
    cleanup_calls = 0

    def cleanup() -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1
        raise OSError("process pool shutdown failed")

    with pytest.raises(RuntimeError, match="failed before batch publication"):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=tmp_path / "cleanup-failure-output",
            acquisition_cleanup=cleanup,
        )

    assert cleanup_calls == 1


def test_scheduler_execute_cancellation_cleans_up(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(fixture.samples, num_partitions=2)
    output_dir = tmp_path / "output"

    call_count = 0
    cleanup_calls = 0

    def should_cancel() -> bool:
        nonlocal call_count
        call_count += 1
        return call_count >= 5

    def cleanup() -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1

    with pytest.raises(InterruptedError, match="cancelled"):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            should_cancel=should_cancel,
            acquisition_cleanup=cleanup,
        )

    spool_dirs = list(output_dir.glob(".partition_spool-*"))
    assert len(spool_dirs) == 0
    assert cleanup_calls == 1


def test_scheduler_execute_acquisition_failure_cleans_up(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(fixture.samples, num_partitions=2, fail_acquisition_index=0)
    output_dir = tmp_path / "output"
    cleanup_calls = 0

    def cleanup() -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1

    with pytest.raises(
        RuntimeError,
        match="Partition pipeline execution failed before batch publication",
    ):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            acquisition_cleanup=cleanup,
        )

    spool_dirs = list(output_dir.glob(".partition_spool-*"))
    assert len(spool_dirs) == 0
    assert cleanup_calls == 1


def test_pipeline_overlap_temporal_intersection(tmp_path: Path) -> None:
    """Prove acquire(P1) and compose(P0) overlap temporally (> 0)."""
    acquire1_started = threading.Event()
    compose0_started = threading.Event()
    handshake: dict[str, bool] = {}

    def on_acquisition(index: int) -> None:
        if index == 1:
            acquire1_started.set()
            handshake["acquire1_saw_compose0"] = compose0_started.wait(5)

    def on_composition(index: int) -> None:
        if index == 0:
            compose0_started.set()
            handshake["compose0_saw_acquire1"] = acquire1_started.wait(5)

    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(
        fixture.samples,
        num_partitions=2,
        on_acquisition=on_acquisition,
        on_composition=on_composition,
    )
    output_dir = tmp_path / "output"
    events: list[dict[str, Any]] = []

    result = PartitionPipelineScheduler.execute(
        adapter,
        output_dir=output_dir,
        progress_callback=events.append,
        pipeline_overlap=True,
    )
    timelines = [
        ev["timeline"] for ev in events if ev.get("event") == "partition_timeline"
    ]
    diagnostics = f"handshake={handshake!r}; timelines={timelines!r}"
    assert handshake == {
        "acquire1_saw_compose0": True,
        "compose0_saw_acquire1": True,
    }, f"Handshake missing or timed out after 5 seconds: {diagnostics}"
    assert len(timelines) == 1, diagnostics
    timeline = timelines[0]
    assert len(timeline) == 2, diagnostics

    p0 = timeline[0]
    p1 = timeline[1]
    overlap = max(
        0.0,
        min(p1["acquire_end"], p0["compose_end"])
        - max(p1["acquire_start"], p0["compose_start"]),
    )
    assert overlap > 0.0, diagnostics
    assert p1.get("overlap_with_previous", 0.0) > 0.0, diagnostics
    assert result["application_result"]["total_children"] == 2, diagnostics


def test_pipeline_overlap_single_partition_serial(tmp_path: Path) -> None:
    """Verify single partition executes serially with no overlap overhead."""
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1",)
    )
    adapter = MockAdapter(fixture.samples, num_partitions=1)
    output_dir = tmp_path / "output"
    events: list[dict[str, Any]] = []

    result = PartitionPipelineScheduler.execute(
        adapter,
        output_dir=output_dir,
        progress_callback=events.append,
        pipeline_overlap=True,
    )
    assert result["application_result"]["total_children"] == 1
    timelines = [
        ev["timeline"] for ev in events if ev.get("event") == "partition_timeline"
    ]
    assert len(timelines) == 1
    assert len(timelines[0]) == 1
    assert "overlap_with_previous" not in timelines[0][0]


@pytest.mark.parametrize("acquisition_workers", [1, 2])
def test_pipeline_overlap_backpressure(
    tmp_path: Path, acquisition_workers: int
) -> None:
    """A composition slot leaves one extra acquisition credit available."""
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("sample-1", "sample-2", "sample-3", "sample-4"),
    )
    p0_compose_started = threading.Event()
    release_p0_compose = threading.Event()
    p0_compose_ended: list[float] = []
    acquire_started = {index: threading.Event() for index in range(1, 4)}
    acquire_times: dict[int, float] = {}

    def on_comp(idx: int) -> None:
        if idx == 0:
            p0_compose_started.set()
            if acquisition_workers == 2:
                assert release_p0_compose.wait(timeout=5)
            else:
                time.sleep(0.08)
            p0_compose_ended.append(time.perf_counter())

    def on_acq(idx: int) -> None:
        if idx in acquire_started:
            acquire_times[idx] = time.perf_counter()
            acquire_started[idx].set()

    adapter = MockAdapter(
        fixture.samples,
        num_partitions=4,
        on_acquisition=on_acq,
        on_composition=on_comp,
    )
    output_dir = tmp_path / "output"

    if acquisition_workers == 1:
        result = PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            pipeline_overlap=True,
            acquisition_workers=acquisition_workers,
        )
    else:
        result_holder: list[dict[str, Any]] = []
        error_holder: list[BaseException] = []

        def run_scheduler() -> None:
            try:
                result_holder.append(
                    PartitionPipelineScheduler.execute(
                        adapter,
                        output_dir=output_dir,
                        pipeline_overlap=True,
                        acquisition_workers=acquisition_workers,
                    )
                )
            except BaseException as exc:  # pragma: no cover - surfaced below
                error_holder.append(exc)

        scheduler_thread = threading.Thread(target=run_scheduler)
        scheduler_thread.start()
        try:
            assert p0_compose_started.wait(timeout=5)
            assert acquire_started[1].wait(timeout=5)
            p2_started_while_compose_open = acquire_started[2].wait(timeout=1)
            p3_started_while_compose_open = acquire_started[3].is_set()
        finally:
            release_p0_compose.set()
        scheduler_thread.join(timeout=5)
        assert not scheduler_thread.is_alive()
        if error_holder:
            raise error_holder[0]
        assert result_holder
        result = result_holder[0]

    assert result["application_result"]["total_children"] == 4
    assert len(p0_compose_ended) == 1
    assert set(acquire_times) == {1, 2, 3}
    if acquisition_workers == 1:
        assert acquire_times[2] >= p0_compose_ended[0] - 0.02
    else:
        assert p2_started_while_compose_open
        assert not p3_started_while_compose_open


@pytest.mark.parametrize("acquisition_workers", [1, 2])
def test_pipeline_bilateral_abort_consumer_failure(
    tmp_path: Path, acquisition_workers: int
) -> None:
    """Consumer composition error triggers cancellation, stops producer,
    cleans spool.
    """
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(
        fixture.samples,
        num_partitions=2,
        fail_composition_index=0,
        acquisition_delay=0.03,
    )
    output_dir = tmp_path / "output"

    with pytest.raises(
        RuntimeError,
        match="Partition pipeline execution failed before batch publication",
    ):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            pipeline_overlap=True,
            acquisition_workers=acquisition_workers,
        )

    spool_dirs = list(output_dir.glob(".partition_spool-*"))
    assert len(spool_dirs) == 0


def test_parallel_partition_acquisition_cancellation_closes_all_producers(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    entered = threading.Barrier(2, timeout=5)
    cancelled = threading.Event()

    def cancel_acquisitions(index: int) -> None:
        entered.wait()
        cancelled.set()

    adapter = MockAdapter(fixture.samples, on_acquisition=cancel_acquisitions)
    output_dir = tmp_path / "output"
    with pytest.raises(InterruptedError, match="cancelled"):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            should_cancel=cancelled.is_set,
            pipeline_overlap=True,
            acquisition_workers=2,
        )
    assert not list(output_dir.glob(".partition_spool-*"))


@pytest.mark.parametrize("acquisition_workers", [1, 2])
def test_pipeline_bilateral_abort_producer_failure(
    tmp_path: Path, acquisition_workers: int
) -> None:
    """Producer acquisition error stops consumer, cleans spool."""
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(
        fixture.samples,
        num_partitions=2,
        fail_acquisition_index=1,
    )
    output_dir = tmp_path / "output"

    with pytest.raises(
        RuntimeError,
        match="Partition pipeline execution failed before batch publication",
    ):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            pipeline_overlap=True,
            acquisition_workers=acquisition_workers,
        )

    spool_dirs = list(output_dir.glob(".partition_spool-*"))
    assert len(spool_dirs) == 0


def test_consumer_composition_failure_marks_partition_failed(
    tmp_path: Path,
) -> None:
    """Consumer composition error marks failing partition failed, not cancelled."""
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    reached = threading.Event()

    def acq(index: int) -> None:
        if index == 1:
            reached.set()

    def compose(index: int) -> None:
        if index == 0:
            assert reached.wait(5)
            raise RuntimeError("simulated consumer composition failure")

    adapter = MockAdapter(
        fixture.samples,
        num_partitions=2,
        on_acquisition=acq,
        on_composition=compose,
    )
    output_dir = tmp_path / "output"
    events: list[dict[str, Any]] = []

    with pytest.raises(
        RuntimeError,
        match="Partition pipeline execution failed before batch publication",
    ) as raised:
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            progress_callback=events.append,
            pipeline_overlap=True,
        )

    assert "simulated consumer composition failure" in str(raised.value.__cause__)
    timeline_events = [e for e in events if e.get("event") == "partition_timeline"]
    assert len(timeline_events) == 1
    statuses = [record["status"] for record in timeline_events[0]["timeline"]]
    assert statuses == ["failed", "cancelled"]


def test_closure_boundary_cancellation_withholds_completed_batch(
    tmp_path: Path,
) -> None:
    """Cancellation at the closure boundary withholds an otherwise complete batch."""
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(
        fixture.samples,
        num_partitions=2,
    )
    output_dir = tmp_path / "output"
    events: list[dict[str, Any]] = []
    cancel = False

    def progress(event: dict[str, Any]) -> None:
        nonlocal cancel
        events.append(event)
        if event.get("stage") == "global_closure":
            cancel = True

    with pytest.raises(InterruptedError, match="cancelled"):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            progress_callback=progress,
            should_cancel=lambda: cancel,
            pipeline_overlap=True,
        )

    assert not any(e.get("stage") == "batch_completed" for e in events)

    timeline_events = [e for e in events if e.get("event") == "partition_timeline"]
    assert len(timeline_events) == 1


def test_worker_admission_pool_validation() -> None:
    with pytest.raises(ValueError, match="max_workers"):
        WorkerAdmissionPool(max_workers=0, backend="thread")
    with pytest.raises(ValueError, match="backend"):
        WorkerAdmissionPool(max_workers=1, backend="invalid")


def test_worker_admission_pool_thread_single_worker_inline() -> None:
    pool = WorkerAdmissionPool(max_workers=1, backend="thread")
    items = [1, 2, 3, 4]
    results: list[tuple[int, int, int, int]] = []

    coordinator_thread = threading.get_ident()
    execution_threads: list[int] = []

    def work(val: int) -> int:
        execution_threads.append(threading.get_ident())
        return val * 10

    def on_comp(comp: int, tot: int, item: int, res: int) -> None:
        results.append((comp, tot, item, res))

    with pool:
        pool.run_windowed(items, work, on_completed=on_comp)

    assert results == [(1, 4, 1, 10), (2, 4, 2, 20), (3, 4, 3, 30), (4, 4, 4, 40)]
    assert all(t == coordinator_thread for t in execution_threads)
    assert pool.transport_metrics is None


def test_worker_admission_pool_process_shares_owner_once_and_restores_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.execution import workers

    created = []
    process_executor = workers.ProcessPoolExecutor

    initargs: list[tuple[object, ...]] = []

    def create_executor(**kwargs):
        executor = process_executor(**kwargs)
        created.append(executor)
        initargs.append(kwargs["initargs"])
        return executor

    monkeypatch.setattr(workers, "ProcessPoolExecutor", create_executor)
    owner = {"global_request": "x" * 1_000_000}
    first_results: list[SharedProcessResult] = []
    second_results: list[SharedProcessResult] = []
    pool = WorkerAdmissionPool(
        max_workers=2,
        backend="process",
        shared_objects=(owner,),
        initializer=_initialize_shared_process_test,
    )

    with pool:
        pool.run_windowed(
            (1, 2),
            _execute_shared_process_task,
            task_arg_fn=lambda value: SharedProcessTask(owner, value),
            on_completed=lambda _c, _t, _item, result: first_results.append(result),
        )
        first_metrics = pool.transport_metrics
        assert first_metrics is not None
        pool.run_windowed(
            (3, 4),
            _execute_shared_process_task,
            task_arg_fn=lambda value: SharedProcessTask(owner, value),
            on_completed=lambda _c, _t, _item, result: second_results.append(result),
        )

    results = (*first_results, *second_results)
    assert sorted(result.value for result in first_results) == [10, 20]
    assert sorted(result.value for result in second_results) == [30, 40]
    assert all(result.owner is owner for result in results)
    assert all(result.initialized for result in results)
    assert len(created) == 1
    assert 1 <= len({result.worker_pid for result in results}) <= 2
    assert all(result.worker_pid != os.getpid() for result in results)
    metrics = pool.transport_metrics
    assert metrics is not None
    assert metrics.shared_bootstrap_bytes == first_metrics.shared_bootstrap_bytes
    assert metrics.shared_bootstrap_bytes >= 2_000_000
    assert len(pickle.dumps(initargs[0])) < 4096
    assert metrics.task_request_bytes < 10_000
    assert metrics.task_response_bytes < 10_000
    assert metrics.worker_pids == tuple(
        sorted({result.worker_pid for result in results})
    )


def test_worker_admission_pool_lanes_keep_each_item_on_its_worker() -> None:
    """A laned pool sends each item to the worker ``lane_fn`` names, across
    calls, so state a worker keeps belongs to its own items; a task sent to
    every lane reaches each of them; without ``lane_fn`` any free lane runs
    the item."""
    owner = {"global_request": "x"}
    results: list[tuple[int, int, tuple[int, ...]]] = []

    def run(pool: WorkerAdmissionPool, values: tuple[int, ...]) -> None:
        pool.run_windowed(
            values,
            _remember_lane_item,
            task_arg_fn=lambda value: SharedProcessTask(owner, value),
            lane_fn=lambda value: value,
            on_completed=lambda _c, _t, _item, result: results.append(result),
        )

    with WorkerAdmissionPool(
        max_workers=2,
        backend="process",
        shared_objects=(owner,),
        initializer=_initialize_shared_process_test,
        lanes=True,
    ) as pool:
        run(pool, (0, 1, 2, 3))
        run(pool, (4, 5, 6, 7))
        pool.run_on_every_lane(_forget_lane_items)
        run(pool, (8, 9))
        unrouted: list[int] = []
        pool.run_windowed(
            (10, 11, 12),
            _remember_lane_item,
            task_arg_fn=lambda value: SharedProcessTask(owner, value),
            on_completed=lambda _c, _t, item, _result: unrouted.append(item),
        )
    with WorkerAdmissionPool(max_workers=2, backend="thread") as unlaned:
        with pytest.raises(ValueError, match="lane_fn"):
            unlaned.run_windowed((1,), str, lane_fn=lambda value: value)

    pids = {
        lane: {pid for pid, value, _seen in results if value % 2 == lane}
        for lane in (0, 1)
    }
    assert all(len(lane_pids) == 1 for lane_pids in pids.values())
    assert pids[0] != pids[1]
    for _pid, value, seen in results:
        assert all(item % 2 == value % 2 for item in seen)
    assert {value: seen for _pid, value, seen in results if value >= 8} == {
        8: (8,),
        9: (9,),
    }
    assert sorted(unrouted) == [10, 11, 12]


def test_worker_admission_pool_thread_multi_worker_bounded() -> None:
    warmed: list[int] = []
    pool = WorkerAdmissionPool(
        max_workers=3,
        backend="thread",
        thread_name_prefix="test-worker",
        initializer=lambda: warmed.append(threading.get_ident()),
    )
    items = list(range(10))
    results: list[int] = []
    lock = threading.Lock()
    active_count = 0
    max_concurrent = 0

    def work(val: int) -> int:
        nonlocal active_count, max_concurrent
        with lock:
            active_count += 1
            if active_count > max_concurrent:
                max_concurrent = active_count
        time.sleep(0.01)
        with lock:
            active_count -= 1
        return val * 2

    def on_comp(comp: int, tot: int, item: int, res: int) -> None:
        with lock:
            results.append(res)

    with pool:
        pool.run_windowed(items, work, on_completed=on_comp)

    assert len(warmed) == 1
    assert len(results) == 10
    assert sorted(results) == [x * 2 for x in range(10)]
    assert max_concurrent <= 3


def test_worker_admission_pool_concurrent_batches_share_one_executor_and_budget(
    monkeypatch,
) -> None:
    import xic_extractor.execution.workers as owner

    active_count = 0
    max_active = 0
    active_lock = threading.Lock()
    overlap_requested = False
    four_started = threading.Event()
    release_work = threading.Event()

    def budget_task(task: SharedProcessTask) -> SharedProcessResult:
        nonlocal active_count, max_active
        with active_lock:
            active_count += 1
            max_active = max(max_active, active_count)
            if overlap_requested and active_count == 4:
                four_started.set()
        try:
            if overlap_requested and not release_work.wait(5):
                raise AssertionError("timed out releasing overlapping work")
            return SharedProcessResult(
                task.owner, task.value, os.getpid(), _SHARED_PROCESS_INITIALIZED
            )
        finally:
            with active_lock:
                active_count -= 1

    created = []
    original_executor = owner.ThreadPoolExecutor

    class RecordingExecutor:
        def __init__(
            self,
            *,
            max_workers: int,
            mp_context: object,
            initializer: object,
            initargs: tuple[object, ...],
        ) -> None:
            self.max_workers = max_workers
            self.mp_context = mp_context
            with creation_gate_lock:
                first_constructor = not creation_gate_used[0]
                if first_constructor:
                    creation_gate_used[0] = True
            self._executor = original_executor(max_workers=max_workers)
            created.append(self)
            if first_constructor:
                creation_started.set()
                if not release_creation.wait(5):
                    raise AssertionError("timed out releasing executor construction")
            if initializer is not None:
                initializer(*initargs)

        def submit(self, *args: object, **kwargs: object):
            return self._executor.submit(*args, **kwargs)

        def shutdown(self, **kwargs: object) -> None:
            self._executor.shutdown(**kwargs)

    monkeypatch.setattr(owner, "ProcessPoolExecutor", RecordingExecutor)
    creation_gate_lock = threading.Lock()
    creation_gate_used = [False]
    creation_started = threading.Event()
    release_creation = threading.Event()
    shared_owner = object()
    cold_pool = WorkerAdmissionPool(
        max_workers=4,
        backend="process",
        shared_objects=(shared_owner,),
    )
    cold_barrier = threading.Barrier(2)
    cold_errors: list[BaseException] = []
    cold_results: list[SharedProcessResult] = []

    def run_cold_batch() -> None:
        try:
            cold_barrier.wait(timeout=5)
            cold_pool.run_windowed(
                (0, 1),
                budget_task,
                task_arg_fn=lambda value: SharedProcessTask(shared_owner, value),
                on_completed=lambda _c, _t, _item, result: cold_results.append(result),
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            cold_errors.append(exc)

    cold_threads = [threading.Thread(target=run_cold_batch) for _ in range(2)]
    for thread in cold_threads:
        thread.start()
    assert creation_started.wait(5)
    release_creation.set()
    for thread in cold_threads:
        thread.join(timeout=10)
    cold_pool.shutdown()
    assert not cold_errors
    assert all(not thread.is_alive() for thread in cold_threads)
    assert len(created) == 1
    assert len(cold_results) == 4

    created.clear()
    active_count = max_active = 0
    pool = WorkerAdmissionPool(
        max_workers=4,
        backend="process",
        shared_objects=(shared_owner,),
    )
    barrier = threading.Barrier(2)
    results: list[SharedProcessResult] = []
    errors: list[BaseException] = []

    def run_batch(_name: str) -> None:
        try:
            barrier.wait(timeout=5)
            pool.run_windowed(
                tuple(range(4)),
                budget_task,
                task_arg_fn=lambda value: SharedProcessTask(shared_owner, value),
                on_completed=lambda _c, _t, _item, result: results.append(result),
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    pool.run_windowed(
        (0,),
        budget_task,
        task_arg_fn=lambda value: SharedProcessTask(shared_owner, value),
        on_completed=lambda _c, _t, _item, result: results.append(result),
    )
    overlap_requested = True
    threads = [threading.Thread(target=run_batch, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    try:
        assert four_started.wait(5), "four workers did not enter the shared pool"
    finally:
        release_work.set()
        for thread in threads:
            thread.join(timeout=10)
        pool.shutdown()

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert len(created) == 1
    assert created[0].max_workers == 4
    assert max_active == 4
    assert len(results) == 9
    assert all(result.owner is shared_owner for result in results)


def test_worker_admission_pool_skip_and_task_arg_fn() -> None:
    pool = WorkerAdmissionPool(max_workers=2, backend="thread")
    items = [1, 2, 3, 4, 5]
    completed_events: list[tuple[int, int, Any]] = []

    def work(val: str) -> str:
        return f"done-{val}"

    with pool:
        pool.run_windowed(
            items,
            work,
            task_arg_fn=lambda x: str(x * 10),
            skip_fn=lambda x: x % 2 == 0,
            on_completed=(
                lambda c, t, item, res: completed_events.append((c, item, res))
            ),
        )

    assert len(completed_events) == 5
    skipped = [item for _, item, res in completed_events if res is None]
    processed = [(item, res) for _, item, res in completed_events if res is not None]
    assert sorted(skipped) == [2, 4]
    assert sorted(processed) == [(1, "done-10"), (3, "done-30"), (5, "done-50")]


def test_worker_admission_pool_cancellation() -> None:
    pool = WorkerAdmissionPool(max_workers=2, backend="thread")
    items = list(range(20))
    processed: list[int] = []
    lock = threading.Lock()
    cancelled = False

    def check_cancel() -> bool:
        return cancelled

    def work(val: int) -> int:
        nonlocal cancelled
        time.sleep(0.02)
        with lock:
            processed.append(val)
            if len(processed) >= 2:
                cancelled = True
        return val

    with pool:
        pool.run_windowed(items, work, should_cancel=check_cancel)

    assert pool.stopped
    assert isinstance(pool.first_error, InterruptedError)
    assert len(processed) < 20


def test_worker_admission_pool_stop_on_error() -> None:
    pool = WorkerAdmissionPool(max_workers=2, backend="thread")
    items = list(range(10))
    errors: list[Exception] = []

    def work(val: int) -> int:
        if val == 3:
            raise ValueError(f"failure at {val}")
        time.sleep(0.01)
        return val

    with pool:
        pool.run_windowed(
            items,
            work,
            on_error=lambda item, exc: errors.append(exc),
            stop_on_error=True,
        )

    assert pool.stopped
    assert isinstance(pool.first_error, ValueError)
    assert len(errors) >= 1


def test_partition_written_by_another_process_is_adopted_identically(
    tmp_path: Path,
) -> None:
    """A worker process writes a partition beside the spool with the published
    shared owners; the store adopts it into its next slot, restores the shared
    owner by reference, and holds the bytes its own write would have."""

    import subprocess
    import sys

    owner = {"plan": "shared"}
    store = StreamedResultStore(tmp_path / "spool", (owner,))
    store.append("partition-local-first", MockPayload(child_outputs=(), label="first"))
    script = "; ".join(
        (
            "import json",
            "from pathlib import Path",
            "from tests.test_execution_engine import MockPayload",
            "from xic_extractor.execution.result_store import"
            " load_shared_owners, write_spooled_partition",
            "owners = Path(%r)" % str(store.shared_owners_path()),
            "(owner,) = load_shared_owners(owners)",
            "payload = MockPayload(child_outputs=(), method_assessments=(owner, 'b'),"
            " label='remote')",
            "written = write_spooled_partition(owners.parent, payload, owners)",
            "print(json.dumps({'path': str(written.path),"
            " 'records': {name: [list(item) for item in rows]"
            " for name, rows in written.records.items()}}))",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    reported = json.loads(completed.stdout.strip())
    sealed = store.adopt_written(
        "partition-remote",
        WrittenPartition(
            Path(reported["path"]),
            {
                name: tuple((offset, length) for offset, length in rows)
                for name, rows in reported["records"].items()
            },
        ),
    )

    assert sealed.index == 1
    assert not Path(reported["path"]).exists()
    restored = store.read(1)
    assert restored.label == "remote"
    assert restored.method_assessments == (owner, "b")
    assert restored.method_assessments[0] is owner

    local = StreamedResultStore(tmp_path / "local", (owner,))
    local.append("partition-local-first", MockPayload(child_outputs=(), label="first"))
    local.append(
        "partition-local",
        MockPayload(child_outputs=(), method_assessments=(owner, "b"), label="remote"),
    )
    assert (tmp_path / "local" / "00000001.pickle").read_bytes() == (
        store.partition_path(1).read_bytes()
    )


@pytest.mark.parametrize("pipeline_overlap", [False, True])
def test_scheduler_phase_barrier_preserves_global_order(
    tmp_path: Path, pipeline_overlap: bool
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1",)
    )
    events: list[object] = []
    adapter = MockAdapter(
        fixture.samples,
        num_partitions=4,
        on_acquisition=lambda i: events.append(("acquire", i)),
    )
    original_composition = adapter.execute_composition
    original_finalize = adapter.finalize_application

    def composition(partition, output, store, **kwargs):
        result = original_composition(partition, output, store, **kwargs)
        events.append(("artifacts", partition.index))
        return result

    def finalize(store, outputs):
        events.append("finalize")
        return original_finalize(store, outputs)

    adapter.execute_composition = composition
    adapter.finalize_application = finalize

    def prepare(store, check_cancel):
        check_cancel()
        assert store.read(0).child_outputs == ("child-0",)
        assert store.read(1).child_outputs == ("child-1",)
        assert ("artifacts", 0) in events and ("artifacts", 1) in events
        assert ("acquire", 2) not in events
        events.append("prepare")

    progress_events: list[dict[str, Any]] = []
    result = PartitionPipelineScheduler.execute(
        adapter,
        phases=(
            PartitionPipelinePhase("initial", adapter.partitions[:2]),
            PartitionPipelinePhase("backfill", adapter.partitions[2:], prepare),
        ),
        output_dir=tmp_path / "output",
        progress_callback=progress_events.append,
        pipeline_overlap=pipeline_overlap,
        acquisition_workers=2,
    )
    assert result["child_outputs"] == tuple(f"child-{i}" for i in range(4))
    assert events.count("finalize") == 1
    assert events.index("prepare") < events.index(("acquire", 2))
    assert [
        event["completed"]
        for event in progress_events
        if event.get("stage") == "partition_released"
    ] == [1, 2, 3, 4]
    timelines = [e for e in progress_events if e.get("event") == "partition_timeline"]
    assert len(timelines) == 1
    assert [r["index"] for r in timelines[0]["timeline"]] == [0, 1, 2, 3]


@pytest.mark.parametrize("pipeline_overlap", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
def test_scheduler_phase_preparation_abort_prevents_admission(
    tmp_path: Path, pipeline_overlap: bool, cancel: bool
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1",)
    )
    acquired: list[int] = []
    adapter = MockAdapter(
        fixture.samples, num_partitions=4, on_acquisition=acquired.append
    )
    cancelled = threading.Event()

    def prepare(store, check_cancel):
        assert store.read(1).child_outputs == ("child-1",)
        if cancel:
            cancelled.set()
        else:
            raise ValueError("invalid handoff")

    output_dir = tmp_path / "output"
    with pytest.raises(
        InterruptedError if cancel else RuntimeError,
        match="cancelled" if cancel else "before batch publication",
    ) as raised:
        PartitionPipelineScheduler.execute(
            adapter,
            phases=(
                PartitionPipelinePhase("initial", adapter.partitions[:2]),
                PartitionPipelinePhase("freeze-hints", adapter.partitions[2:], prepare),
            ),
            output_dir=output_dir,
            should_cancel=cancelled.is_set,
            pipeline_overlap=pipeline_overlap,
        )
    assert sorted(acquired) == [0, 1]
    if not cancel:
        assert isinstance(raised.value.__cause__, ValueError)
        assert str(raised.value.__cause__) == "invalid handoff"
    assert not list(output_dir.glob(".partition_spool-*"))
