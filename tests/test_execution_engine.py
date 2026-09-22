"""Unit tests for the generic execution engine core."""

from __future__ import annotations

import hashlib
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
    AttemptTerminalOutcome,
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
from xic_extractor.execution_runtime_marker import (
    OwnerPathRuntimeMarker,
    RuntimeMarkerState,
)
from xic_extractor.execution_runtime_owner import (
    RuntimeArtifactRegistrationReceipt,
    RuntimeCancellationIngress,
    RuntimeCancellationRequest,
    RuntimeFinalizationReceipt,
    RuntimeResourceSettlementReceipt,
    RuntimeWorkerBatchReceipt,
    execute_runtime_attempt,
    mint_work_unit_settlement,
    start_runtime_attempt,
)

_TEST_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.test_execution_engine",
    semantic_role="test_partition_artifact",
    schema_version="test",
    validation_status="diagnostic_only",
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf",
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
_MOCK_PROCESS_ACTIVE = 0
_MOCK_PROCESS_MAX_ACTIVE = 0
_MOCK_PROCESS_LOCK = threading.Lock()


def _initialize_shared_process_test() -> None:
    global _SHARED_PROCESS_INITIALIZED
    _SHARED_PROCESS_INITIALIZED = True


def _execute_shared_process_task(task: SharedProcessTask) -> SharedProcessResult:
    time.sleep(0.2)
    return SharedProcessResult(
        task.owner,
        task.value * 10,
        os.getpid(),
        _SHARED_PROCESS_INITIALIZED,
    )


def _execute_shared_process_budget_task(task: SharedProcessTask) -> SharedProcessResult:
    global _MOCK_PROCESS_ACTIVE, _MOCK_PROCESS_MAX_ACTIVE
    with _MOCK_PROCESS_LOCK:
        _MOCK_PROCESS_ACTIVE += 1
        _MOCK_PROCESS_MAX_ACTIVE = max(
            _MOCK_PROCESS_MAX_ACTIVE, _MOCK_PROCESS_ACTIVE
        )
    try:
        time.sleep(0.02)
        return SharedProcessResult(
            task.owner,
            task.value,
            os.getpid(),
            _SHARED_PROCESS_INITIALIZED,
        )
    finally:
        with _MOCK_PROCESS_LOCK:
            _MOCK_PROCESS_ACTIVE -= 1


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
        children=((shared_config, shared_ref),), ref=shared_config
    )
    store.append("p-0", payload)
    restored = store.read(0)

    assert restored.ref is shared_config
    assert restored.children[0][0] is shared_config
    assert restored.children[0][1] is shared_ref
    checkpoint = store.preserve_failed_results()
    assert checkpoint is not None
    with checkpoint.open("rb") as stream:
        retained = _OwnerUnpickler(stream, ()).load()
    replay = retained.read(0)
    assert replay.ref == shared_config
    assert replay.children[0][1] == shared_ref
    assert replay.ref is retained._shared_objects[0]
    assert replay.children[0][0] is replay.ref
    assert replay.children[0][1] is retained._shared_objects[1]


def test_streamed_result_store_unknown_shared_identity_rejected() -> None:
    shared_objects = (object(),)
    bio = io.BytesIO()
    # A memo reference cannot bind an authority absent from this record.
    bio.write(pickle.PROTO + b"\x04")
    bio.write(pickle.BINGET + b"\x63")  # 99
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
        acquisition_delay: float = 0.0,
        composition_delay: float = 0.0,
        declare_global_closure: bool = False,
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
        self._global_closure_work_unit_key = (
            f"global-closure:{owners.request.processing_request_id}"
            if declare_global_closure
            else None
        )
        self._work_unit_keys = (
            *(f"wu:{p.partition_key}" for p in self._partitions),
            *(
                (self._global_closure_work_unit_key,)
                if self._global_closure_work_unit_key
                else ()
            ),
        )
        self._resource_reservations = tuple(
            RuntimeResourceReservation(
                attempt=owners.attempt,
                kind=RuntimeResourceKind.ACQUISITION_SESSION,
                resource_key=f"acquisition-session:{p.partition_key}:phase",
                worker_key=f"worker:{p.partition_key}",
                source_reference="test-src",
                compatibility_token="test",
                owner_conditional=True,
            )
            for p in self._partitions
        )
        self.fail_acquisition_index = fail_acquisition_index
        self.fail_composition_index = fail_composition_index
        self.fail_finalize = fail_finalize
        self.acquisition_delay = acquisition_delay
        self.composition_delay = composition_delay
        self.on_acquisition = on_acquisition
        self.on_composition = on_composition
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
    def global_closure_work_unit_key(self) -> str | None:
        return self._global_closure_work_unit_key

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
        if self.acquisition_delay > 0:
            time.sleep(self.acquisition_delay)
        if self.on_acquisition is not None:
            self.on_acquisition(partition.index)
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
        if self.composition_delay > 0:
            time.sleep(self.composition_delay)
        if self.on_composition is not None:
            self.on_composition(partition.index)
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

    def create_global_closure_artifacts(
        self,
        application_result: dict[str, Any],
    ) -> tuple[
        tuple[RuntimeArtifactRegistrationReceipt, ...],
        tuple[WorkUnitSettlement, ...],
    ]:
        if self._global_closure_work_unit_key is None:
            return (), ()
        key = self._global_closure_work_unit_key
        payload_bytes = json.dumps(
            {
                "work_unit_key": key,
                "total_children": application_result.get("total_children", 0),
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
        pipeline_overlap=False,
    )

    assert result["application_result"]["total_children"] == 2
    assert result["children"] == ("child-0", "child-1")

    # Verify progress event sequence
    stages = [
        ev["stage"]
        for ev in progress_events
        if ev.get("event") == "partition_progress"
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
    adapter = MockAdapter(fixture.owners, num_partitions=2)
    explicit_collects: list[int] = []
    monkeypatch.setattr(
        gc,
        "collect",
        lambda: explicit_collects.append(1),
    )

    result = PartitionPipelineScheduler.execute(
        adapter,
        output_dir=tmp_path / "gc-output",
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "gc-marker.json", "gc"),
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
    adapter = MockAdapter(fixture.owners, num_partitions=2)
    marker = OwnerPathRuntimeMarker(tmp_path / "cleanup-marker.json", "cleanup")
    cleanup_states: list[RuntimeMarkerState] = []

    def cleanup() -> None:
        cleanup_states.append(marker.read().state)

    result = PartitionPipelineScheduler.execute(
        adapter,
        output_dir=tmp_path / "cleanup-output",
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=marker,
        acquisition_cleanup=cleanup,
    )

    assert result["application_result"]["total_children"] == 2
    assert cleanup_states == [RuntimeMarkerState.LAUNCHED]
    assert marker.read().state is RuntimeMarkerState.FINALIZED


def test_scheduler_cleanup_failure_prevents_successful_terminal_acknowledgement(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1",)
    )
    adapter = MockAdapter(fixture.owners, num_partitions=1)
    marker = OwnerPathRuntimeMarker(
        tmp_path / "cleanup-failure-marker.json", "cleanup-failure"
    )
    cleanup_calls = 0

    def cleanup() -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1
        raise OSError("process pool shutdown failed")

    with pytest.raises(RuntimeError, match="failed before batch publication"):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=tmp_path / "cleanup-failure-output",
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            acquisition_cleanup=cleanup,
        )

    assert cleanup_calls == 1
    assert marker.read().terminal_outcome is AttemptTerminalOutcome.FAILED


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
    cleanup_calls = 0

    def should_cancel() -> bool:
        nonlocal call_count
        call_count += 1
        # Cancel during the second partition processing
        return call_count >= 5

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
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            should_cancel=should_cancel,
            acquisition_cleanup=cleanup,
        )

    # Verify spool directory was cleaned up
    spool_dirs = list(output_dir.glob(".partition_spool-*"))
    assert len(spool_dirs) == 0
    assert cleanup_calls == 1


def test_scheduler_execute_acquisition_failure_cleans_up(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(
        fixture.owners, num_partitions=2, fail_acquisition_index=0
    )
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-acq-fail")
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
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            acquisition_cleanup=cleanup,
        )

    # Verify spool directory was cleaned up
    spool_dirs = list(output_dir.glob(".partition_spool-*"))
    assert len(spool_dirs) == 0
    assert cleanup_calls == 1


def test_scheduler_composition_failure_retains_completed_results(
    tmp_path: Path,
) -> None:
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

    spool_dirs = list(output_dir.glob(".partition_spool-*"))
    assert len(spool_dirs) == 1
    with (spool_dirs[0] / "failed-results.pickle").open("rb") as stream:
        retained = _OwnerUnpickler(stream, ()).load()
    assert tuple(retained.sequence("children")) == ("child-0",)
    assert tuple(retained.sequence("method_assessments")) == ("assess-0",)
    assert not list(spool_dirs[0].glob("*.tmp"))
    with pytest.raises(ValueError, match="sealed"):
        retained.append("new-partition", MockPayload(children=("invented",)))


def test_scheduler_finalize_failure_retains_completed_results(tmp_path: Path) -> None:
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

    spool_dirs = list(output_dir.glob(".partition_spool-*"))
    assert len(spool_dirs) == 1
    with (spool_dirs[0] / "failed-results.pickle").open("rb") as stream:
        retained = _OwnerUnpickler(stream, ()).load()
    assert tuple(retained.sequence("children")) == ("child-0", "child-1")
    assert tuple(retained.sequence("method_assessments")) == ("assess-0", "assess-1")
    assert marker.read().state == RuntimeMarkerState.FINALIZED


def test_pipeline_overlap_temporal_intersection(tmp_path: Path) -> None:
    """Prove acquire(P1) and compose(P0) overlap temporally (> 0)."""
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(
        fixture.owners,
        num_partitions=2,
        acquisition_delay=0.04,
        composition_delay=0.04,
    )
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-overlap")
    events: list[dict[str, Any]] = []

    result = PartitionPipelineScheduler.execute(
        adapter,
        output_dir=output_dir,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=marker,
        progress_callback=events.append,
        pipeline_overlap=True,
    )
    assert result["application_result"]["total_children"] == 2

    timelines = [
        ev["timeline"]
        for ev in events
        if ev.get("event") == "partition_timeline"
    ]
    assert len(timelines) == 1
    timeline = timelines[0]
    assert len(timeline) == 2

    p0 = timeline[0]
    p1 = timeline[1]
    overlap = max(
        0.0,
        min(p1["acquire_end"], p0["compose_end"])
        - max(p1["acquire_start"], p0["compose_start"]),
    )
    assert overlap > 0.0
    assert p1.get("overlap_with_previous", 0.0) > 0.0


def test_pipeline_overlap_single_partition_serial(tmp_path: Path) -> None:
    """Verify single partition executes serially with no overlap overhead."""
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1",)
    )
    adapter = MockAdapter(fixture.owners, num_partitions=1)
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-single")
    events: list[dict[str, Any]] = []

    result = PartitionPipelineScheduler.execute(
        adapter,
        output_dir=output_dir,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=marker,
        progress_callback=events.append,
        pipeline_overlap=True,
    )
    assert result["application_result"]["total_children"] == 1
    timelines = [
        ev["timeline"]
        for ev in events
        if ev.get("event") == "partition_timeline"
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
        fixture.owners,
        num_partitions=4,
        on_acquisition=on_acq,
        on_composition=on_comp,
    )
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-backpressure")

    if acquisition_workers == 1:
        result = PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
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
                        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
                        recovery_marker=marker,
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
        fixture.owners,
        num_partitions=2,
        fail_composition_index=0,
        acquisition_delay=0.03,
    )
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-comp-abort")

    with pytest.raises(
        RuntimeError,
        match="Partition pipeline execution failed before batch publication",
    ):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
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

    adapter = MockAdapter(fixture.owners, on_acquisition=cancel_acquisitions)
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "parallel-cancel")
    with pytest.raises(RuntimeError, match="before batch publication"):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            should_cancel=cancelled.is_set,
            pipeline_overlap=True,
            acquisition_workers=2,
        )
    assert marker.read().terminal_outcome is AttemptTerminalOutcome.CANCELLED
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
        fixture.owners,
        num_partitions=2,
        fail_acquisition_index=1,
    )
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-prod-abort")

    with pytest.raises(
        RuntimeError,
        match="Partition pipeline execution failed before batch publication",
    ):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            pipeline_overlap=True,
            acquisition_workers=acquisition_workers,
        )

    spool_dirs = list(output_dir.glob(".partition_spool-*"))
    assert len(spool_dirs) == 0


def test_r386_01_in_flight_cancellation_ingress(tmp_path: Path) -> None:
    """In-flight cancellation ingress acceptance transitions attempt to CANCELLED."""
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1",)
    )
    attempt = fixture.owners.attempt
    ingress = RuntimeCancellationIngress(attempt=attempt)
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("wu:1", "wu:2"),
        owner_completeness_attestation="test-attestation",
    )
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-ingress")
    marker.record_launch(runtime)

    def provide_worker_batch(aggregate: object) -> RuntimeWorkerBatchReceipt:
        accepted = ingress.request_cancellation(
            RuntimeCancellationRequest(attempt=attempt)
        )
        assert accepted is True
        return RuntimeWorkerBatchReceipt(
            settlements=(
                mint_work_unit_settlement(
                    attempt=attempt,
                    work_unit_key="wu:1",
                    outcome=WorkUnitTerminalOutcome.CANCELLED,
                    failure_detail=WorkUnitFailureDetail(
                        category=WorkUnitFailureCategory.CANCELLATION,
                        stage="test-cancel",
                        source_reference="wu:1",
                        message="cancelled in flight",
                    ),
                ),
                mint_work_unit_settlement(
                    attempt=attempt,
                    work_unit_key="wu:2",
                    outcome=WorkUnitTerminalOutcome.CANCELLED,
                    failure_detail=WorkUnitFailureDetail(
                        category=WorkUnitFailureCategory.CANCELLATION,
                        stage="test-cancel",
                        source_reference="wu:2",
                        message="cancelled in flight",
                    ),
                ),
            ),
            resource_settlements=(),
            artifact_registrations=(),
            acquisitions=(),
        )

    receipt = execute_runtime_attempt(
        runtime,
        reservations=(),
        worker_batch_provider=provide_worker_batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=marker,
        cancellation_ingress=ingress,
    )
    assert receipt.supervisor.terminal_outcome is AttemptTerminalOutcome.CANCELLED
    snapshot = marker.read()
    assert snapshot.terminal_outcome is AttemptTerminalOutcome.CANCELLED


def test_r386_01_global_closure_failure_retains_prior_success(
    tmp_path: Path,
) -> None:
    """Closure failure marks only closure unit FAILED; partition units stay
    SUCCEEDED.
    """
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(
        fixture.owners,
        num_partitions=2,
        declare_global_closure=True,
        fail_finalize=True,
    )
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-closure-fail")

    with pytest.raises(
        RuntimeError,
        match="Partition pipeline execution failed before batch publication",
    ):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            pipeline_overlap=True,
        )

    snapshot = marker.read()
    assert snapshot.terminal_outcome is AttemptTerminalOutcome.FAILED
    assert snapshot.coordinator_record_bytes is not None
    record = json.loads(snapshot.coordinator_record_bytes)
    settlements = record["work_settlements"]
    assert len(settlements) == 3

    p0_settlement = next(
        s for s in settlements if s["work_unit_key"] == "wu:partition-00000"
    )
    p1_settlement = next(
        s for s in settlements if s["work_unit_key"] == "wu:partition-00001"
    )
    closure_settlement = next(
        s
        for s in settlements
        if s["work_unit_key"].startswith("global-closure:")
    )

    assert p0_settlement["outcome"] == "succeeded"
    assert p1_settlement["outcome"] == "succeeded"
    assert closure_settlement["outcome"] == "failed"
    assert closure_settlement["failure_detail"] is not None
    assert (
        closure_settlement["failure_detail"]["stage"]
        == "partition-pipeline-completion"
    )


def test_r386_01_cancellation_cutoff_atomic(tmp_path: Path) -> None:
    """Cancellation submitted after ingress cutoff returns False."""
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1",)
    )
    attempt = fixture.owners.attempt
    ingress = RuntimeCancellationIngress(attempt=attempt)
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("wu:1",),
        owner_completeness_attestation="test-attestation",
    )
    marker = OwnerPathRuntimeMarker(tmp_path / "marker.json", "test-cutoff")
    marker.record_launch(runtime)

    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=fixture.owners.request.processing_request_id,
        producer_processing_attempt_id=attempt.processing_attempt_id,
        semantic_contract=_TEST_CONTRACT,
        payload_bytes=b"dummy",
        upstream_artifacts=(),
    )

    def provide_worker_batch(aggregate: object) -> RuntimeWorkerBatchReceipt:
        return RuntimeWorkerBatchReceipt(
            settlements=(
                mint_work_unit_settlement(
                    attempt=attempt,
                    work_unit_key="wu:1",
                    outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                    owner_result_ref=artifact,
                ),
            ),
            resource_settlements=(),
            artifact_registrations=(
                RuntimeArtifactRegistrationReceipt("wu:1", artifact),
            ),
            acquisitions=(),
        )

    receipt = execute_runtime_attempt(
        runtime,
        reservations=(),
        worker_batch_provider=provide_worker_batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=marker,
        cancellation_ingress=ingress,
    )
    late_accepted = ingress.request_cancellation(
        RuntimeCancellationRequest(attempt=attempt)
    )
    assert late_accepted is False
    assert receipt.supervisor.terminal_outcome is AttemptTerminalOutcome.SUCCEEDED


def test_r386_01_consumer_composition_failure_settled_failed(
    tmp_path: Path,
) -> None:
    """Consumer composition error marks failing partition FAILED, not CANCELLED."""
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
        fixture.owners,
        num_partitions=2,
        declare_global_closure=True,
        on_acquisition=acq,
        on_composition=compose,
    )
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(
        tmp_path / "marker.json", "test-comp-fail-settled"
    )
    events: list[dict[str, Any]] = []

    with pytest.raises(
        RuntimeError,
        match="Partition pipeline execution failed before batch publication",
    ):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            progress_callback=events.append,
            pipeline_overlap=True,
        )

    snapshot = marker.read()
    assert snapshot.terminal_outcome is AttemptTerminalOutcome.FAILED
    assert snapshot.coordinator_record_bytes is not None
    record = json.loads(snapshot.coordinator_record_bytes)
    settlements = record["work_settlements"]

    p0 = next(s for s in settlements if s["work_unit_key"] == "wu:partition-00000")
    p1 = next(s for s in settlements if s["work_unit_key"] == "wu:partition-00001")
    closure = next(
        s for s in settlements if s["work_unit_key"].startswith("global-closure:")
    )

    assert p0["outcome"] == "failed"
    assert p0["failure_detail"]["stage"] == "partition-composition"
    assert "simulated consumer composition failure" in p0["failure_detail"]["message"]
    assert p1["outcome"] == "cancelled"
    assert closure["outcome"] == "cancelled"

    timeline_events = [
        e for e in events if e.get("event") == "partition_timeline"
    ]
    assert len(timeline_events) == 1


def test_r386_01_closure_boundary_cancellation_settled_cancelled(
    tmp_path: Path,
) -> None:
    """Cancellation at closure boundary marks closure and attempt CANCELLED."""
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample-1", "sample-2")
    )
    adapter = MockAdapter(
        fixture.owners,
        num_partitions=2,
        declare_global_closure=True,
    )
    output_dir = tmp_path / "output"
    marker = OwnerPathRuntimeMarker(
        tmp_path / "marker.json", "test-closure-cancel"
    )
    events: list[dict[str, Any]] = []
    cancel = False

    def progress(event: dict[str, Any]) -> None:
        nonlocal cancel
        events.append(event)
        if event.get("stage") == "global_closure":
            cancel = True

    with pytest.raises(
        RuntimeError,
        match="Partition pipeline execution failed before batch publication",
    ):
        PartitionPipelineScheduler.execute(
            adapter,
            output_dir=output_dir,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            progress_callback=progress,
            should_cancel=lambda: cancel,
            pipeline_overlap=True,
        )

    snapshot = marker.read()
    assert snapshot.terminal_outcome is AttemptTerminalOutcome.CANCELLED
    assert snapshot.coordinator_record_bytes is not None
    record = json.loads(snapshot.coordinator_record_bytes)
    settlements = record["work_settlements"]

    p0 = next(s for s in settlements if s["work_unit_key"] == "wu:partition-00000")
    p1 = next(s for s in settlements if s["work_unit_key"] == "wu:partition-00001")
    closure = next(
        s for s in settlements if s["work_unit_key"].startswith("global-closure:")
    )

    assert p0["outcome"] == "succeeded"
    assert p1["outcome"] == "succeeded"
    assert closure["outcome"] == "cancelled"
    assert (
        closure["failure_detail"]["stage"]
        == "partition-pipeline-cancellation"
    )

    timeline_events = [
        e for e in events if e.get("event") == "partition_timeline"
    ]
    assert len(timeline_events) == 1


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
    assert pool.transport_metrics is None


def test_worker_admission_pool_process_shares_owner_once_and_restores_identity(
) -> None:
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
            tuple(range(1, 21)),
            _execute_shared_process_task,
            task_arg_fn=lambda value: SharedProcessTask(owner, value),
            on_completed=lambda _c, _t, _item, result: first_results.append(result),
        )
        first_metrics = pool.transport_metrics
        assert first_metrics is not None
        pool.run_windowed(
            tuple(range(21, 41)),
            _execute_shared_process_task,
            task_arg_fn=lambda value: SharedProcessTask(owner, value),
            on_completed=lambda _c, _t, _item, result: second_results.append(result),
        )

    results = (*first_results, *second_results)
    assert sorted(result.value for result in results) == [
        value * 10 for value in range(1, 41)
    ]
    assert all(result.owner is owner for result in results)
    assert all(result.initialized for result in results)
    assert len({result.worker_pid for result in results}) == 2
    assert all(result.worker_pid != os.getpid() for result in results)
    metrics = pool.transport_metrics
    assert metrics is not None
    assert metrics.shared_bootstrap_bytes == first_metrics.shared_bootstrap_bytes
    assert {result.worker_pid for result in second_results} == {
        result.worker_pid for result in first_results
    }
    assert metrics.shared_bootstrap_bytes >= 2_000_000
    assert metrics.task_request_bytes < 10_000
    assert metrics.task_response_bytes < 10_000
    assert metrics.worker_pids == tuple(
        sorted({result.worker_pid for result in results})
    )


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
    import xic_extractor.execution_engine.worker_pool as owner

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
    global _MOCK_PROCESS_ACTIVE, _MOCK_PROCESS_MAX_ACTIVE
    with _MOCK_PROCESS_LOCK:
        _MOCK_PROCESS_ACTIVE = 0
        _MOCK_PROCESS_MAX_ACTIVE = 0
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
                _execute_shared_process_budget_task,
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
    with _MOCK_PROCESS_LOCK:
        _MOCK_PROCESS_ACTIVE = 0
        _MOCK_PROCESS_MAX_ACTIVE = 0
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
                _execute_shared_process_budget_task,
                task_arg_fn=lambda value: SharedProcessTask(shared_owner, value),
                on_completed=lambda _c, _t, _item, result: results.append(result),
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    pool.run_windowed(
        (0,),
        _execute_shared_process_budget_task,
        task_arg_fn=lambda value: SharedProcessTask(shared_owner, value),
        on_completed=lambda _c, _t, _item, result: results.append(result),
    )
    threads = [threading.Thread(target=run_batch, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    with pool:
        pass

    assert not errors
    assert len(created) == 1
    assert created[0].max_workers == 4
    assert _MOCK_PROCESS_MAX_ACTIVE == 4
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


def test_reserved_partition_written_elsewhere_reads_back_identically(
    tmp_path: Path,
) -> None:
    """A writer outside this process may produce the bytes; the store still
    owns the index, the path and the sealed identity, and the same content
    written elsewhere must land byte-identical."""

    import json
    import subprocess
    import sys

    store = StreamedResultStore(tmp_path / "spool", ())
    reservation = store.reserve_partition("partition-remote")
    assert reservation.path == tmp_path.resolve() / "spool" / "00000000.pickle"
    module = "xic_extractor.execution_engine.streamed_result_store"
    script = "; ".join(
        (
            "import json",
            "from pathlib import Path",
            "from tests.test_execution_engine import MockPayload",
            "from %s import write_partition_payload" % module,
            "payload = MockPayload(children=(), method_assessments=('a', 'b'),"
            " label='remote')",
            "kids = tuple({'child': index} for index in range(4))",
            "digest, size, lengths, records, _ = write_partition_payload("
            "Path(%r), payload, shared_objects=(), children=kids)"
            % str(reservation.path),
            "print(json.dumps({'lengths': lengths, 'digest': digest,"
            " 'size': size, 'records': {name: [list(item) for item in rows]"
            " for name, rows in records.items()}}))",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    written = json.loads(completed.stdout.strip())
    sealed = store.adopt_partition(
        reservation,
        lengths=written["lengths"],
        records={
            name: tuple((offset, length) for offset, length in rows)
            for name, rows in written["records"].items()
        },
    )
    assert sealed.sha256 == written["digest"]
    assert sealed.size_bytes == written["size"]
    # Identity is derived here from the published bytes, not reported by the
    # writer that produced them.
    assert sealed.sha256 == hashlib.sha256(reservation.path.read_bytes()).hexdigest()
    assert sealed.size_bytes == reservation.path.stat().st_size

    restored = store.read(0)
    assert restored.label == "remote"
    assert restored.method_assessments == ("a", "b")
    assert restored.children == tuple({"child": index} for index in range(4))

    local = StreamedResultStore(tmp_path / "local", ())
    local_sealed = local.append_children(
        "partition-local",
        MockPayload(children=(), method_assessments=("a", "b"), label="remote"),
        tuple({"child": index} for index in range(4)),
    )
    assert local_sealed.sha256 == sealed.sha256
