"""Generic execution engine core infrastructure for partitioned workloads."""

from __future__ import annotations

from xic_extractor.execution_engine.contracts import (
    PartitionAcquisitionOutput,
    PartitionCompositionOutput,
    PartitionPipelineAdapter,
    ProcessingOwners,
    WorkPartition,
)
from xic_extractor.execution_engine.partition_pipeline_scheduler import (
    PartitionPipelineScheduler,
)
from xic_extractor.execution_engine.streamed_result_store import (
    PartitionResultStore,
    SealedPartition,
    StreamedResultStore,
)
from xic_extractor.execution_engine.worker_pool import (
    WorkerAdmissionPool,
)

__all__ = [
    "PartitionAcquisitionOutput",
    "PartitionCompositionOutput",
    "PartitionPipelineAdapter",
    "PartitionPipelineScheduler",
    "PartitionResultStore",
    "ProcessingOwners",
    "SealedPartition",
    "StreamedResultStore",
    "WorkPartition",
    "WorkerAdmissionPool",
]
