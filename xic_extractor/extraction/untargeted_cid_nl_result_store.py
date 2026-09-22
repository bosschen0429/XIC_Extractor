"""Compatibility facade re-exporting generic StreamedResultStore as
PartitionResultStore.
"""

from __future__ import annotations

from xic_extractor.execution_engine.streamed_result_store import (
    PartitionChildSink,
    PartitionResultStore,
    SealedPartition,
    StreamedResultStore,
)

__all__ = [
    "PartitionChildSink",
    "PartitionResultStore",
    "SealedPartition",
    "StreamedResultStore",
]
