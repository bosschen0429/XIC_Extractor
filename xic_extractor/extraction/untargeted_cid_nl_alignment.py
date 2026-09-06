"""Alignment execution adapter for pure single-provider Untargeted CID-NL."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
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
from xic_extractor.discovery.csv_writer import format_discovery_csv_value
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings
from xic_extractor.discovery.physical_fact_persistence import (
    read_discovery_physical_fact_records,
)
from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
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
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
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
    sample_metadata: tuple[SampleMetadata, ...] | None = None,
    timing_recorder: TimingRecorder | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
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

    retained_sample_metadata = (
        sample_metadata
        if sample_metadata is not None
        else tuple(
            SampleMetadata(
                sample_name=sample_stem,
                raw_stem=sample_stem,
                injection_order=index,
                sample_role="study_sample",
            )
            for index, sample_stem in enumerate(
                discovery_batch.sample_order,
                start=1,
            )
        )
    )
    ordered_raw_paths = tuple(
        raw_paths[sample_stem] for sample_stem in discovery_batch.sample_order
    )
    physical_records: dict[str, DiscoveryPhysicalFactRecord] = {}
    if signal_processing_profile is not None:
        candidates_by_sample, physical_records = _load_physical_replay_inputs(
            discovery_batch=discovery_batch,
            candidates_by_sample=candidates_by_sample,
            raw_paths=ordered_raw_paths,
            settings=settings,
            profile=signal_processing_profile,
        )
    batch = build_untargeted_cid_nl_production_batch(
        raw_paths=ordered_raw_paths,
        sample_metadata=retained_sample_metadata,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=receipts,
        dll_dir=dll_dir,
        sample_identity_namespace=_SAMPLE_IDENTITY_NAMESPACE,
        signal_processing_profile=signal_processing_profile,
        physical_records_by_candidate=physical_records,
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


def _load_physical_replay_inputs(
    *,
    discovery_batch: DiscoveryBatchInput,
    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]],
    raw_paths: tuple[Path, ...],
    settings: DiscoverySettings,
    profile: SignalProcessingProfile,
) -> tuple[
    dict[str, tuple[DiscoveryCandidate, ...]],
    dict[str, DiscoveryPhysicalFactRecord],
]:
    spec = compile_discovery_processing_run_spec(
        raw_paths,
        discovery_settings=settings,
        signal_processing_profile=profile,
    )
    restored: dict[str, tuple[DiscoveryCandidate, ...]] = {}
    selected_records: dict[str, DiscoveryPhysicalFactRecord] = {}
    for acquisition_input in spec.acquisition_inputs:
        stem = acquisition_input.sample_stem
        records = read_discovery_physical_fact_records(
            discovery_batch.candidate_csvs[stem].parent
            / "discovery_physical_facts.v1.json",
            expected_run_spec=spec,
            expected_acquisition_input=acquisition_input,
        )
        by_id = {record.candidate_id: record for record in records}
        if len(by_id) != len(records):
            raise ValueError(
                "Discovery physical sidecar has ambiguous candidate identity"
            )
        candidates: list[DiscoveryCandidate] = []
        for candidate in candidates_by_sample[stem]:
            record = by_id.get(candidate.candidate_id)
            if record is None:
                raise ValueError("Discovery candidate lacks its exact physical record")
            candidates.append(_restore_physical_candidate(candidate, record))
            if candidate.candidate_id in selected_records:
                raise ValueError("Discovery physical candidate identity is duplicated")
            selected_records[candidate.candidate_id] = record
        restored[stem] = tuple(candidates)
    return restored, selected_records


def _restore_physical_candidate(
    candidate: DiscoveryCandidate,
    record: DiscoveryPhysicalFactRecord,
) -> DiscoveryCandidate:
    work = record.work_item
    if (
        record.candidate_id != candidate.candidate_id
        or work.sample_stem != candidate.sample_stem
        or work.best_ms2_scan_id != candidate.best_ms2_scan_id
    ):
        raise ValueError("physical replay crossed exact Discovery candidate identity")
    values: dict[str, Any] = {
        "precursor_mz": work.precursor_mz,
        "product_mz": work.product_mz,
        "best_seed_rt": work.query_rt_min,
        "ms1_search_rt_min": work.xic_rt_min,
        "ms1_search_rt_max": work.xic_rt_max,
    }
    facts = record.evaluation.facts
    if facts is not None:
        values.update(
            ms1_apex_rt=facts.apex_rt_min,
            ms1_peak_rt_start=facts.left_rt_min,
            ms1_peak_rt_end=facts.right_rt_min,
            ms1_area=facts.area_counts_seconds,
            ms1_height=facts.height,
            ms1_seed_delta_min=facts.apex_rt_min - work.query_rt_min,
        )
    for name, value in values.items():
        if format_discovery_csv_value(name, getattr(candidate, name)) != (
            format_discovery_csv_value(name, value)
        ):
            raise ValueError(
                f"{candidate.candidate_id}: physical {name} crossed its CSV receipt"
            )
    return replace(candidate, **values)


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
