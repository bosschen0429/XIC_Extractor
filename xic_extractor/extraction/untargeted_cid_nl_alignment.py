"""Alignment execution adapter for pure single-provider Untargeted CID-NL."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.csv_io import DiscoveryBatchInput
from xic_extractor.alignment.raw_sources import existing_raw_paths
from xic_extractor.alignment.workflow import CompletedUntargetBatchWorkflowResult
from xic_extractor.config import ExtractionConfig
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.discovery.batch_contract import (
    DiscoveryCandidateUniverseReceipt,
)
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_observability import RuntimeMetricAvailability
from xic_extractor.execution_runtime_owner import (
    RuntimeArtifactIoSample,
    RuntimeProcessTreePeakRssSample,
    RuntimeTelemetrySamples,
    RuntimeWorkerPeakRssSample,
    runtime_observation_event_count,
    runtime_started_worker_keys,
)
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    build_untargeted_cid_nl_production_batch,
    compose_untargeted_cid_nl_production_result,
    execute_untargeted_cid_nl_production_batch,
)
from xic_extractor.sample_metadata import SampleMetadata

_SAMPLE_IDENTITY_NAMESPACE = "alignment-untargeted-cid-nl-v1"
_RUNTIME_ARTIFACT_NAMESPACE = "untargeted-cid-nl-alignment-v1"


def complete_untargeted_cid_nl_alignment(
    *,
    discovery_batch: DiscoveryBatchInput,
    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]],
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    alignment_config: AlignmentConfig,
    timing_recorder: TimingRecorder | None = None,
) -> CompletedUntargetBatchWorkflowResult:
    """Execute and compose one owner-closed CID-NL batch exactly once."""

    raw_paths = existing_raw_paths(
        sample_order=discovery_batch.sample_order,
        raw_files=discovery_batch.raw_files,
        raw_dir=raw_dir,
    )
    if set(raw_paths) != set(discovery_batch.sample_order):
        missing = sorted(set(discovery_batch.sample_order) - set(raw_paths))
        raise ValueError(
            "CID-NL production requires RAW for every sample: " + ", ".join(missing)
        )

    receipts: dict[str, DiscoveryCandidateUniverseReceipt] = {}
    for sample_stem in discovery_batch.sample_order:
        receipt = discovery_batch.candidate_receipts[sample_stem]
        if not isinstance(receipt, DiscoveryCandidateUniverseReceipt):
            raise ValueError(
                "CID-NL production requires owner-closed Discovery receipts"
            )
        receipts[sample_stem] = receipt

    sample_metadata = tuple(
        SampleMetadata(
            sample_name=sample_stem,
            raw_stem=sample_stem,
            injection_order=index,
            sample_role="study_sample",
        )
        for index, sample_stem in enumerate(discovery_batch.sample_order, start=1)
    )
    batch = build_untargeted_cid_nl_production_batch(
        raw_paths=tuple(
            raw_paths[sample_stem] for sample_stem in discovery_batch.sample_order
        ),
        sample_metadata=sample_metadata,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=receipts,
        dll_dir=dll_dir,
        sample_identity_namespace=_SAMPLE_IDENTITY_NAMESPACE,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    recorder = timing_recorder or TimingRecorder.disabled("alignment")
    with recorder.stage("alignment.untargeted_cid_nl_runtime"):
        runtime_result = execute_untargeted_cid_nl_production_batch(
            batch,
            telemetry_provider=_coarse_runtime_telemetry,
            recovery_marker=OwnerPathRuntimeMarker(
                output_dir / ".cid_nl_runtime.json",
                _RUNTIME_ARTIFACT_NAMESPACE,
            ),
            progress_callback=recorder.heartbeat_event,
        )
    with recorder.stage("alignment.untargeted_cid_nl_compose"):
        return compose_untargeted_cid_nl_production_result(
            batch,
            runtime_result,
            alignment_config=alignment_config,
        )


def _coarse_runtime_telemetry(coordinator: Any) -> RuntimeTelemetrySamples:
    event_count = runtime_observation_event_count(coordinator)
    worker_keys = runtime_started_worker_keys(coordinator)
    reason = "not_collected_by_alignment_foreground_runner"
    return RuntimeTelemetrySamples(
        event_monotonic_offsets_ns=(0,) * event_count,
        artifact_io=RuntimeArtifactIoSample(
            read_count=0,
            read_bytes=0,
            write_count=0,
            write_bytes=0,
        ),
        process_tree_peak_rss=RuntimeProcessTreePeakRssSample(
            availability=RuntimeMetricAvailability.UNSUPPORTED,
            reason=reason,
        ),
        worker_peak_rss=tuple(
            RuntimeWorkerPeakRssSample(
                worker_key=worker_key,
                availability=RuntimeMetricAvailability.UNSUPPORTED,
                reason=reason,
            )
            for worker_key in worker_keys
        ),
    )


__all__ = ["complete_untargeted_cid_nl_alignment"]
