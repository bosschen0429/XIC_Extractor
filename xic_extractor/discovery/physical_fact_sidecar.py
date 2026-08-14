"""Staging adapter for the Discovery physical-fact publication transaction."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from xic_extractor.discovery.models import DiscoveryPhysicalWorkItem
from xic_extractor.discovery.physical_fact_persistence import (
    DiscoveryPhysicalFactRecord,
    write_discovery_physical_fact_sidecar,
)
from xic_extractor.processing_run_spec import (
    ProcessingAcquisitionInput,
    ProcessingRunSpec,
)


def stage_discovery_physical_fact_sidecar(
    path: Path,
    *,
    run_spec: ProcessingRunSpec,
    acquisition_input: ProcessingAcquisitionInput,
    work_manifest: Sequence[DiscoveryPhysicalWorkItem],
    records: Sequence[DiscoveryPhysicalFactRecord],
) -> Path:
    """Encode and verify a sidecar at a caller-owned transaction stage path."""

    staged = write_discovery_physical_fact_sidecar(
        path,
        run_spec=run_spec,
        acquisition_input=acquisition_input,
        work_manifest=work_manifest,
        records=records,
    )
    if staged != path:
        raise ValueError("Discovery physical sidecar staged at an unexpected path")
    return staged
