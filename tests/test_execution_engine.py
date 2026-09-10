"""Unit tests for the generic execution engine core."""

from __future__ import annotations

import io
import json
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
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from xic_extractor.evidence_artifacts import (
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
)
from xic_extractor.execution_engine import (
    PartitionAcquisitionOutput,
    PartitionCompositionOutput,
    PartitionPipelineScheduler,
    PartitionResultStore,
    ProcessingOwners,
    StreamedResultStore,
    WorkerAdmissionPool,
    WorkPartition,
)
from xic_extractor.execution_engine.streamed_result_store import _OwnerUnpickler
from xic_extractor.execution_runtime import (
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitSettlement,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeArtifactRegistrationReceipt,
    RuntimeFinalizationReceipt,
    RuntimeResourceSettlementReceipt,
    mint_work_unit_settlement,
)

_TEST_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.test_execution_engine",
    semantic_role="test_partition_artifact",
    schema_version="test_v1",
    validation_status="diagnostic_only",
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf-v1",
)


@dataclass(frozen=True)
class MockPayload:
    children: tuple
    method_assessments: tuple = ()
    excluded_candidates: tuple = ()
    label: str = "partition"


@dataclass(frozen=True)
class CustomPayload:
    custom_items: tuple
    label: str = "custom"


@dataclass(frozen=True)
class RefPayload:
    children: tuple
    ref: object = None


# ---------------------------------------------------------------------------
# StreamedResultStore Tests
# ---------------------------------------------------------------------------


def test_streamed_result_store_basic_roundtrip(tmp_path: Path) -> None:
    store = StreamedResultStore(tmp_path / "spool", ())
    payload = MockPayload(
        children=(1, 2, 3),
        method_assessments=("assess_1",),
        excluded_candidates=("ex_1", "ex_2"),
    )
    entry = store.append("p-0", payload)
    assert entry.index == 0
    assert entry.key == "p-0"
    assert entry.size_bytes > 0

    restored = store.read(0)
    assert restored.children == (1, 2, 3)
    assert restored.method_assessments == ("assess_1",)
    assert restored.excluded_candidates == ("ex_1", "ex_2")

    store.seal()
    assert tuple(store.sequence("children")) == (1, 2, 3)
    assert tuple(store.sequence("method_assessments")) == ("assess_1",)
    assert store.read_selected(0, "excluded_candidates", (1, 0)) == ("ex_2", "ex_1")


def test_streamed_result_store_retained_memory_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StreamedResultStore(tmp_path / "spool", ())
    payload = MockPayload(
        children=(10, 20, 30),
        method_assessments=(100, 200),
        excluded_candidates=(1000, 2000),
    )
    entry = store.append_children(
        "p-0",
        MockPayload(
            (),
            payload.method_assessments,
            payload.excluded_candidates,
            payload.label,
        ),
        children=(10, 20, 30),
        retain_memory=True,
    )
    assert entry.size_bytes > 0
    store.seal()

    opened: list[Any] = []
    orig_open = Path.open

    def tracking_open(path, *args, **kwargs):
        stream = orig_open(path, *args, **kwargs)
        opened.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", tracking_open)

    seq = store.sequence("children")
    assert tuple(seq) == (10, 20, 30)
    assert len(opened) == 0

    selected = store.read_selected(0, "excluded_candidates", (1, 0))
    assert selected == (2000, 1000)
    assert len(opened) == 0


def test_streamed_result_store_shared_objects_identity(tmp_path: Path) -> None:
    shared_config = {"anchor": 42}
    shared_ref = ["shared_authority"]
    store = StreamedResultStore(tmp_path / "spool", (shared_config, shared_ref))

    payload = RefPayload(
        children=((shared_config, shared_ref),), ref=shared_config
    )
    store.append("p-0", payload)
    restored = store.read(0)

    assert restored.ref is shared_config
    assert restored.children[0][0] is shared_config
    assert restored.children[0][1] is shared_ref


def test_streamed_result_store_unknown_shared_identity_rejected() -> None:
    shared_objects = (object(),)
    bio = io.BytesIO()
    # Write a pickle opcode using persistent load with an invalid identity
    # Protocol 2+ persistent load opcode: BINPERSID
    # Write int 99 which is out of range for shared_objects (length 1)
    # Manually pickling with persistent id out of range
    bio.write(pickle.PROTO + b"\x04")
    bio.write(pickle.BININT1 + b"\x63")  # 99
    bio.write(pickle.BINPERSID)
    bio.write(pickle.STOP)
    bio.seek(0)

    unpickler = _OwnerUnpickler(bio, shared_objects)
    with pytest.raises(
        pickle.UnpicklingError, match="unknown same-process owner reference"
    ):
        unpickler.load()


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
    store.append("p-0", MockPayload(children=(1, 2)))
    assert spool_dir.exists()

    store.cleanup()
    assert not spool_dir.exists()
    assert len(store._field_caches) == 0

    # Idempotent cleanup must not raise
    store.cleanup()
    assert not spool_dir.exists()


def test_streamed_result_store_alias() -> None:
    assert PartitionResultStore is StreamedResultStore


# ---------------------------------------------------------------------------
# PartitionPipelineScheduler Tests
# ---------------------------------------------------------------------------


class MockAdapter:
    def __init__(
        self,
        owners: ProcessingOwners,
        *,
        num_partitions: int = 2,
        fail_acquisition_index: int | None = None,
        fail_composition_index: int | None = None,
        fail_finalize: bool = False,
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
        self._work_unit_keys = tuple(
            f"wu:{p.partition_key}" for p in self._partitions
        )
        self._resource_reservations = tuple(
            RuntimeResourceReservation(
                attempt=owners.attempt,
                kind=RuntimeResourceKind.ACQUISITION_SESSION,
                resource_key=f"acquisition-session:{p.partition_key}:phase",
                worker_key=f"worker:{p.partition_key}",
                source_reference="test-src",
                compatibility_token="test:v1",
                owner_conditional=True,
            )
            for p in self._partitions
        )
        self.fail_acquisition_index = fail_acquisition_index
        self.fail_composition_index = fail_composition_index
        self.fail_finalize = fail_finalize
        self.retained_memory_seen: list[bool] = []

    @property
    def partitions(self) -> tuple[WorkPartition[dict[str, int]], ...]:
        return self._partitions

    @property
    def processing_owners(self) -> ProcessingOwners:
        return self._owners

    @property
    def work_unit_keys(self) -> tuple[str, ...]:
        return self._work_unit_keys

    @property
    def resource_reservations(self) -> tuple[RuntimeResourceReservation, ...]:
        return self._resource_reservations

    @property
    def owner_completeness_attestation(self) -> str:
        return f"{self._owners.request.processing_request_id}:mock-attestation"

    def get_shared_objects(self) -> tuple[object, ...]:
        return (self._owners,)

    def execute_acquisition(
        self,
        partition: WorkPartition[dict[str, int]],
        *,
        should_cancel=None,
        progress_callback=None,
    ) -> PartitionAcquisitionOutput[dict[str, Any]]:
        if self.fail_acquisition_index == partition.index:
            return PartitionAcquisitionOutput(
                result={},
                succeeded=False,
                failure_detail=WorkUnitFailureDetail(
                    category=WorkUnitFailureCategory.EXECUTION_ERROR,
                    stage="mock-acq-failure",
                    source_reference=partition.partition_key,
                    message="simulated acquisition failure",
                ),
            )
        res_key = f"acquisition-session:{partition.partition_key}:phase"
        resource_receipt = RuntimeResourceSettlementReceipt(
            attempt=self._owners.attempt,
            resource_key=res_key,
            open_outcome=RuntimeResourceOpenOutcome.OPENED,
            cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
        )
        return PartitionAcquisitionOutput(
            result={"acq_done": partition.index},
            resources=(resource_receipt,),
            acquisitions=(),
            succeeded=True,
        )

    def execute_composition(
        self,
        partition: WorkPartition[dict[str, int]],
        acquisition_output: PartitionAcquisitionOutput[dict[str, Any]],
        store: StreamedResultStore,
        *,
        retain_memory: bool = False,
        should_cancel=None,
    ) -> PartitionCompositionOutput[dict[str, Any]]:
        self.retained_memory_seen.append(retain_memory)
        if self.fail_composition_index == partition.index:
            raise RuntimeError("simulated composition exception")
        payload = MockPayload(
            children=(f"child-{partition.index}",),
            method_assessments=(f"assess-{partition.index}",),
            excluded_candidates=(f"ex-{partition.index}",),
        )
        sealed = store.append(
            partition.partition_key, payload, retain_memory=retain_memory
        )
        return PartitionCompositionOutput(
            result={"comp_done": partition.index},
            sealed=sealed,
        )

    def create_partition_artifacts(
        self,
        partition: WorkPartition[dict[str, int]],
        composition_output: PartitionCompositionOutput[dict[str, Any]],
    ) -> tuple[
        tuple[RuntimeArtifactRegistrationReceipt, ...],
        tuple[WorkUnitSettlement, ...],
    ]:
        key = f"wu:{partition.partition_key}"
        payload_bytes = json.dumps(
            {
                "work_unit_key": key,
                "sha256": composition_output.sealed.sha256,
            }
        ).encode("utf-8")
        artifact = _mint_evidence_artifact_ref(
            producer_processing_request_id=self._owners.request.processing_request_id,
            producer_processing_attempt_id=self._owners.attempt.processing_attempt_id,
            semantic_contract=_TEST_CONTRACT,
            payload_bytes=payload_bytes,
            upstream_artifacts=(),
        )
        art_receipt = RuntimeArtifactRegistrationReceipt(key, artifact)
        wu_settlement = mint_work_unit_settlement(
            attempt=self._owners.attempt,
            work_unit_key=key,
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=artifact,
        )
        return (art_receipt,), (wu_settlement,)

    def finalize_application(
        self,
        store: StreamedResultStore,
        composition_outputs: tuple[PartitionCompositionOutput[dict[str, Any]], ...],
    ) -> dict[str, Any]:
        if self.fail_finalize:
            raise RuntimeError("simulated finalize application exception")
        return {"total_children": len(store.sequence("children"))}

    def build_workflow_result(
        self,
        runtime_receipt: RuntimeFinalizationReceipt,
        application_result: dict[str, Any],
        store: StreamedResultStore,
    ) -> dict[str, Any]:
        return {
            "runtime_receipt": runtime_receipt,
            "application_result": application_result,
            "children": tuple(store.sequence("children")),
        }


def test_scheduler_execute_success(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(fixture.owners, num_partitions=2)
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-scheduler")

    progress_events: list[dict[str, Any]] = []

    result = PartitionPipelineScheduler.execute(
        adapter,
        output_dir=output_dir,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=marker,
        progress_callback=progress_events.append,
        retain_partition_memory=False,
    )

    assert result["application_result"]["total_children"] == 2
    assert result["children"] == ("child-0", "child-1")

    # Verify progress event sequence
    stages = [ev["stage"] for ev in progress_events]
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


def test_scheduler_execute_single_partition_retains_memory(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1",)
    )
    adapter = MockAdapter(fixture.owners, num_partitions=1)
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-retained")

    result = PartitionPipelineScheduler.execute(
        adapter,
        output_dir=output_dir,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=marker,
        retain_partition_memory=True,
    )

    assert result["application_result"]["total_children"] == 1
    assert adapter.retained_memory_seen == [True]


def test_scheduler_execute_cancellation_cleans_up(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(fixture.owners, num_partitions=2)
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-cancel")

    call_count = 0

    def should_cancel() -> bool:
        nonlocal call_count
        call_count += 1
        # Cancel during the second partition processing
        return call_count >= 5

    with pytest.raises(
        RuntimeError,
        match="Partition pipeline execution failed before batch publication",
    ):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            should_cancel=should_cancel,
        )

    # Verify spool directory was cleaned up
    spool_dirs = list(output_dir.glob(".partition_spool-*"))
    assert len(spool_dirs) == 0


def test_scheduler_execute_acquisition_failure_cleans_up(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(
        fixture.owners, num_partitions=2, fail_acquisition_index=0
    )
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-acq-fail")

    with pytest.raises(
        RuntimeError,
        match="Partition pipeline execution failed before batch publication",
    ):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
        )

    # Verify spool directory was cleaned up
    spool_dirs = list(output_dir.glob(".partition_spool-*"))
    assert len(spool_dirs) == 0


def test_scheduler_execute_composition_failure_cleans_up(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(
        fixture.owners, num_partitions=2, fail_composition_index=1
    )
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-comp-fail")

    with pytest.raises(
        RuntimeError,
        match="Partition pipeline execution failed before batch publication",
    ):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
        )

    # Verify spool directory was cleaned up
    spool_dirs = list(output_dir.glob(".partition_spool-*"))
    assert len(spool_dirs) == 0


def test_scheduler_execute_finalize_failure_cleans_up(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(
        fixture.owners, num_partitions=2, fail_finalize=True
    )
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-final-fail")

    with pytest.raises(
        RuntimeError,
        match="Partition pipeline execution failed before batch publication",
    ):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
        )

    # Verify spool directory was cleaned up
    spool_dirs = list(output_dir.glob(".partition_spool-*"))
    assert len(spool_dirs) == 0


def test_worker_admission_pool_validation() -> None:
    with pytest.raises(ValueError, match="max_workers"):
        WorkerAdmissionPool(max_workers=0)
    with pytest.raises(ValueError, match="backend"):
        WorkerAdmissionPool(backend="invalid")


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

