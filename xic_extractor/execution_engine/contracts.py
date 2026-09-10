"""Execution engine contracts and pipeline adapter protocol."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Generic, Protocol, TypeAlias, TypeVar

from xic_extractor.execution_engine.streamed_result_store import (
    SealedPartition,
    StreamedResultStore,
)
from xic_extractor.execution_runtime import (
    WorkUnitFailureDetail,
    WorkUnitSettlement,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_owner import (
    RuntimeAcquisitionReceipt,
    RuntimeArtifactRegistrationReceipt,
    RuntimeFinalizationReceipt,
    RuntimeResourceSettlementReceipt,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences

ProcessingOwners: TypeAlias = ProcessingOwnerReferences

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
class PartitionAcquisitionOutput(Generic[TAcquisitionResult]):
    result: TAcquisitionResult
    resources: Sequence[RuntimeResourceSettlementReceipt] = ()
    acquisitions: Sequence[RuntimeAcquisitionReceipt] = ()
    succeeded: bool = True
    failure_detail: WorkUnitFailureDetail | None = None


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

    @property
    def processing_owners(self) -> ProcessingOwners: ...

    @property
    def work_unit_keys(self) -> Sequence[str]: ...

    @property
    def resource_reservations(self) -> Sequence[RuntimeResourceReservation]: ...

    @property
    def owner_completeness_attestation(self) -> str: ...

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

    def create_partition_artifacts(
        self,
        partition: WorkPartition[TPartitionPayload],
        composition_output: PartitionCompositionOutput[TCompositionResult],
    ) -> tuple[
        Sequence[RuntimeArtifactRegistrationReceipt],
        Sequence[WorkUnitSettlement],
    ]: ...

    def finalize_application(
        self,
        store: StreamedResultStore,
        composition_outputs: Sequence[PartitionCompositionOutput[TCompositionResult]],
    ) -> TApplicationResult: ...

    def build_workflow_result(
        self,
        runtime_receipt: RuntimeFinalizationReceipt,
        application_result: TApplicationResult,
        store: StreamedResultStore,
    ) -> TFinalResult: ...
