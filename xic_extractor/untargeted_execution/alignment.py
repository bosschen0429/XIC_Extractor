"""Alignment execution adapter for pure single-provider Untargeted CID-NL."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.raw_reader import PreparedRawMassRecalibration

import os
import shutil
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import mkdtemp
from threading import Lock
from typing import Any, cast

from xic_extractor.acquisition.raw_paths import existing_raw_paths
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.configuration.execution_profile import DEFAULT_ALIGNMENT_WORKERS
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.batch_contract import (
    DiscoveryCandidateUniverseReceipt,
    validate_discovery_physical_facts_artifact,
)
from xic_extractor.discovery.csv_reader import DiscoveryBatchInput
from xic_extractor.discovery.csv_writer import format_discovery_csv_value
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings
from xic_extractor.discovery.physical_fact_persistence import (
    DetachedIndexedDiscoveryPhysicalFacts,
    IndexedDiscoveryPhysicalFacts,
    read_discovery_physical_fact_records,
)
from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec_with_expectations,
)
from xic_extractor.execution._observability import RuntimeMetricAvailability
from xic_extractor.execution.attempt import (
    RuntimeProcessTreePeakRssSample,
    RuntimeTelemetrySamples,
    RuntimeWorkerPeakRssSample,
    runtime_started_worker_keys,
)
from xic_extractor.execution.process_memory import ProcessMemoryPeak
from xic_extractor.execution.recovery import OwnerPathRuntimeMarker
from xic_extractor.execution.result_store import StreamedResultStore
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.execution.workers import WorkerAdmissionPool
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.processing_run_spec import (
    ProcessingAcquisitionInput,
    ProcessingRunSpec,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.execution import (
    execute_untargeted_plan,
)
from xic_extractor.untargeted_execution.execution_result import (
    CompletedUntargetBatchWorkflowResult,
)
from xic_extractor.untargeted_execution.grouping_experiment import (
    PreparedGroupingExperiment,
)
from xic_extractor.untargeted_execution.input_planning import (
    UntargetedInputPlan,
    prepare_eager_untargeted_plan,
    prepare_indexed_untargeted_plan,
)

_SAMPLE_IDENTITY_NAMESPACE = "alignment-untargeted-cid-nl"
_RUNTIME_ARTIFACT_NAMESPACE = "untargeted-cid-nl-alignment"


@dataclass(frozen=True, slots=True)
class _DiscoveryEvidencePartition:
    discovery_physical_records: tuple[DiscoveryPhysicalFactRecord, ...]


@dataclass(frozen=True, slots=True)
class _PhysicalIndexJob:
    path: Path
    receipt: DiscoveryCandidateUniverseReceipt
    expected_run_spec: ProcessingRunSpec
    expected_acquisition_input: ProcessingAcquisitionInput
    candidate_ids: frozenset[str]
    spool_dir: Path


@dataclass(frozen=True, slots=True)
class _PhysicalIndexWorkerResult:
    sample_stem: str
    transfer: DetachedIndexedDiscoveryPhysicalFacts
    worker_pid: int


@dataclass(frozen=True, slots=True)
class _PhysicalIndexBatchResult:
    sources: dict[str, IndexedDiscoveryPhysicalFacts]
    worker_pids: tuple[int, ...]


def _index_discovery_physical_source(
    job: _PhysicalIndexJob,
) -> _PhysicalIndexWorkerResult:
    validate_discovery_physical_facts_artifact(job.receipt, job.path)
    indexed = IndexedDiscoveryPhysicalFacts(
        job.path,
        expected_run_spec=job.expected_run_spec,
        expected_acquisition_input=job.expected_acquisition_input,
        candidate_ids=job.candidate_ids,
        spool_dir=job.spool_dir,
    )
    try:
        return _PhysicalIndexWorkerResult(
            sample_stem=job.expected_acquisition_input.sample_stem,
            transfer=indexed.detach(),
            worker_pid=os.getpid(),
        )
    finally:
        indexed.close()


def _index_discovery_physical_sources(
    jobs: tuple[_PhysicalIndexJob, ...],
    *,
    raw_workers: int,
    raw_worker_backend: str,
    should_cancel: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> _PhysicalIndexBatchResult:
    """Build bounded sidecar indexes and adopt their private spools in the caller."""

    sources: dict[str, IndexedDiscoveryPhysicalFacts] = {}
    worker_pids: list[int] = []

    def adopt(
        completed: int,
        total: int,
        job: _PhysicalIndexJob,
        result: _PhysicalIndexWorkerResult,
    ) -> None:
        if result.sample_stem != job.expected_acquisition_input.sample_stem:
            raise ValueError("physical index worker crossed sample identity")
        if result.sample_stem in sources:
            raise ValueError("physical index worker duplicated sample identity")
        sources[result.sample_stem] = IndexedDiscoveryPhysicalFacts.from_detached(
            result.transfer,
            expected_run_spec=job.expected_run_spec,
            expected_acquisition_input=job.expected_acquisition_input,
        )
        worker_pids.append(result.worker_pid)
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "alignment_input_index",
                    "sample_stem": result.sample_stem,
                    "completed": completed,
                    "total": total,
                }
            )

    try:
        with WorkerAdmissionPool(
            max_workers=raw_workers,
            backend=raw_worker_backend,
            thread_name_prefix="physical-fact-index",
        ) as pool:
            pool.run_windowed(
                jobs,
                _index_discovery_physical_source,
                on_completed=adopt,
                should_cancel=should_cancel,
            )
        if pool.first_error is not None:
            raise pool.first_error
        ordered_sources = {
            job.expected_acquisition_input.sample_stem: sources[
                job.expected_acquisition_input.sample_stem
            ]
            for job in jobs
        }
        return _PhysicalIndexBatchResult(
            sources=ordered_sources,
            worker_pids=tuple(worker_pids),
        )
    except BaseException:
        for source in sources.values():
            source.close()
        raise


@dataclass(slots=True)
class PreparedUntargetedAlignmentInputs:
    """Prepared inputs own their private indexes until transferred to a result."""

    plan: UntargetedInputPlan
    physical_sources: dict[str, IndexedDiscoveryPhysicalFacts]
    physical_spool_dir: Path | None
    evidence_store: StreamedResultStore | None
    _resources: ExitStack = field(repr=False)

    def retained_physical_records(
        self,
    ) -> (
        tuple[DiscoveryPhysicalFactRecord, ...]
        | ImmutableSequence[DiscoveryPhysicalFactRecord]
    ):
        """Expose retained records without materializing the indexed sidecars."""
        if self.physical_sources:
            sources = self.physical_sources
            chunks = tuple(
                (stem, indices[start : start + 64])
                for stem, source in sources.items()
                for indices in (tuple(sorted(source.candidate_ordinals.values())),)
                for start in range(0, len(indices), 64)
            )

            def load_records(index: int) -> tuple[DiscoveryPhysicalFactRecord, ...]:
                stem, indices = chunks[index]
                return tuple(sources[stem].read_record(item) for item in indices)

            return ImmutableSequence(tuple(len(ids) for _, ids in chunks), load_records)
        if self.physical_spool_dir is not None:
            shutil.rmtree(self.physical_spool_dir, ignore_errors=True)
            self.physical_spool_dir = None
        if self.evidence_store is None:
            return ()
        self.evidence_store.seal()
        return self.evidence_store.sequence("discovery_physical_records")

    def take_result_resources(self) -> ExitStack:
        """Transfer only resource handles, not the finished planning state."""
        return self._resources.pop_all()

    def close(self) -> None:
        """Release all private inputs once, including when one close fails."""
        self._resources.close()


def prepare_untargeted_alignment_inputs(
    *,
    discovery_batch: DiscoveryBatchInput,
    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]],
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    sample_metadata: tuple[SampleMetadata, ...] | None = None,
    timing_recorder: TimingRecorder | None = None,
    signal_processing_profile: SignalProcessingProfile,
    raw_workers: int = DEFAULT_ALIGNMENT_WORKERS,
    raw_worker_backend: str = "thread",
    should_cancel: Callable[[], bool] | None = None,
    verified_acquisition_sources: Mapping[str, AcquisitionSourceExpectation]
    | None = None,
) -> PreparedUntargetedAlignmentInputs:
    """Prepare Discovery-backed inputs without scheduling Backfill or outputs."""
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

    ordered_raw_paths = tuple(
        raw_paths[sample_stem] for sample_stem in discovery_batch.sample_order
    )
    if sample_metadata is not None:
        retained_sample_metadata = sample_metadata
    else:
        from xic_extractor.raw_reader import read_analysis_set

        by_path = {
            path.resolve(): entry.metadata
            for entry in read_analysis_set(raw_dir, dll_dir).entries
            for path in entry.copy_paths
        }
        missing_paths = [
            path for path in ordered_raw_paths if path.resolve() not in by_path
        ]
        if missing_paths:
            raise ValueError(
                "RAW analysis set has no injection for: "
                + ", ".join(map(str, missing_paths))
            )
        retained_sample_metadata = tuple(
            by_path[path.resolve()] for path in ordered_raw_paths
        )
    recorder = timing_recorder or TimingRecorder.disabled("alignment")
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_store = (
        StreamedResultStore(
            Path(mkdtemp(prefix=".cid_nl_discovery_evidence_", dir=output_dir)),
            shared_objects=(),
            individually_addressable_fields=frozenset({"discovery_physical_records"}),
        )
        if not isinstance(candidates_by_sample, DiscoveryInputSource)
        else None
    )
    resources = ExitStack()
    if evidence_store is not None:
        resources.callback(evidence_store.cleanup)

    def retain_records(
        stem: str, records: tuple[DiscoveryPhysicalFactRecord, ...]
    ) -> None:
        assert evidence_store is not None
        evidence_store.append(stem, _DiscoveryEvidencePartition(records))

    physical_sources: dict[str, IndexedDiscoveryPhysicalFacts] = {}
    physical_spool_dir: Path | None = None
    try:
        physical_records: dict[str, DiscoveryPhysicalFactRecord] = {}
        acquisition_source_expectations: (
            tuple[AcquisitionSourceExpectation, ...] | None
        ) = None
        if isinstance(candidates_by_sample, DiscoveryInputSource):
            with recorder.stage("alignment.verify_acquisition_sources"):
                spec, expectations = (
                    compile_discovery_processing_run_spec_with_expectations(
                        ordered_raw_paths,
                        discovery_settings=settings,
                        signal_processing_profile=signal_processing_profile,
                        fingerprint_workers=raw_workers,
                        verified_acquisition_sources=verified_acquisition_sources,
                    )
                )
            physical_spool_dir = Path(
                mkdtemp(prefix=".physical_fact_indexes_", dir=output_dir)
            )
            resources.callback(shutil.rmtree, physical_spool_dir, ignore_errors=True)
            jobs = tuple(
                _PhysicalIndexJob(
                    path=(
                        discovery_batch.candidate_csvs[
                            acquisition_input.sample_stem
                        ].parent
                        / "discovery_physical_facts.json"
                    ),
                    receipt=discovery_batch.candidate_receipts[
                        acquisition_input.sample_stem
                    ],
                    expected_run_spec=spec,
                    expected_acquisition_input=acquisition_input,
                    candidate_ids=frozenset(feature_ids),
                    spool_dir=physical_spool_dir,
                )
                for acquisition_input in spec.acquisition_inputs
                for feature_ids in (
                    candidates_by_sample.feature_candidate_ids(
                        acquisition_input.sample_stem
                    ),
                )
                if feature_ids
            )
            with recorder.stage("alignment.index_physical_facts") as stage:
                indexed = _index_discovery_physical_sources(
                    jobs,
                    raw_workers=raw_workers,
                    raw_worker_backend=raw_worker_backend,
                    should_cancel=should_cancel,
                    progress_callback=recorder.heartbeat_event,
                )
                for source in indexed.sources.values():
                    resources.callback(source.close)
                physical_sources.update(indexed.sources)
                stage.metrics["feature_candidate_count"] = len(
                    candidates_by_sample.feature_candidate_ids()
                )
                stage.metrics["discovery_record_count"] = len(
                    candidates_by_sample.locations
                )
            with recorder.stage("alignment.prepare_feature_inputs"):
                plan = prepare_indexed_untargeted_plan(
                    source=candidates_by_sample,
                    physical_sources=physical_sources,
                    raw_paths=ordered_raw_paths,
                    sample_metadata=retained_sample_metadata,
                    settings=settings,
                    peak_config=peak_config,
                    dll_dir=dll_dir,
                    profile=signal_processing_profile,
                    acquisition_source_expectations=expectations,
                    sample_identity_namespace=_SAMPLE_IDENTITY_NAMESPACE,
                    restore_candidate=_restore_physical_candidate,
                )
        else:
            with recorder.stage("alignment.load_physical_facts"):
                (
                    candidates_by_sample,
                    physical_records,
                    acquisition_source_expectations,
                ) = _load_physical_replay_inputs(
                    discovery_batch=discovery_batch,
                    candidates_by_sample=candidates_by_sample,
                    raw_paths=ordered_raw_paths,
                    settings=settings,
                    profile=signal_processing_profile,
                    raw_workers=raw_workers,
                    retained_records_sink=retain_records,
                    verified_acquisition_sources=verified_acquisition_sources,
                )
        if not isinstance(candidates_by_sample, DiscoveryInputSource):
            plan = prepare_eager_untargeted_plan(
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
                acquisition_source_expectations=acquisition_source_expectations,
            )

        return PreparedUntargetedAlignmentInputs(
            plan,
            physical_sources,
            physical_spool_dir,
            evidence_store,
            _resources=resources,
        )
    except BaseException:
        resources.close()
        raise


def complete_untargeted_alignment(
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
    known_istd_reference: KnownIstdReference | None = None,
    timing_recorder: TimingRecorder | None = None,
    signal_processing_profile: SignalProcessingProfile,
    raw_workers: int = DEFAULT_ALIGNMENT_WORKERS,
    raw_worker_backend: str = "thread",
    should_cancel: Callable[[], bool] | None = None,
    retain_partition_memory: bool = False,
    retain_acquisition_cache: bool = False,
    acquisition_cache_dir: Path | None = None,
    max_partition_jobs: int = 100_000,
    mass_recalibration_by_sample: Mapping[str, PreparedRawMassRecalibration]
    | None = None,
) -> CompletedUntargetBatchWorkflowResult:
    """Execute and compose one owner-closed CID-NL batch exactly once."""

    prepared = prepare_untargeted_alignment_inputs(
        discovery_batch=discovery_batch,
        candidates_by_sample=candidates_by_sample,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=output_dir,
        settings=settings,
        peak_config=peak_config,
        sample_metadata=sample_metadata,
        timing_recorder=timing_recorder,
        signal_processing_profile=signal_processing_profile,
        raw_workers=raw_workers,
        raw_worker_backend=raw_worker_backend,
        should_cancel=should_cancel,
        verified_acquisition_sources=(
            {
                sample: prepared.acquisition
                for sample, prepared in mass_recalibration_by_sample.items()
            }
            if mass_recalibration_by_sample
            else None
        ),
    )
    plan = prepared.plan
    with ExitStack() as pending:
        pending.callback(prepared.close)
        completed = cast(
            CompletedUntargetBatchWorkflowResult,
            execute_prepared_alignment(
                plan,
                output_dir=output_dir,
                alignment_config=alignment_config,
                known_istd_reference=known_istd_reference,
                timing_recorder=timing_recorder,
                raw_workers=raw_workers,
                raw_worker_backend=raw_worker_backend,
                should_cancel=should_cancel,
                retain_partition_memory=retain_partition_memory,
                retain_acquisition_cache=retain_acquisition_cache,
                acquisition_cache_dir=acquisition_cache_dir,
                max_partition_jobs=max_partition_jobs,
                mass_recalibration_by_sample=mass_recalibration_by_sample,
            ),
        )
        pending.callback(completed.close)

        completed = completed.with_discovery_inputs(prepared)
        completed = completed.bind_publication_inputs(
            candidates_by_sample=candidates_by_sample,
            alignment_config=alignment_config,
            known_istd_reference=known_istd_reference,
        )
        pending.pop_all()
        return completed


def execute_prepared_alignment(
    plan: UntargetedInputPlan,
    *,
    output_dir: Path,
    alignment_config: AlignmentConfig,
    known_istd_reference: KnownIstdReference | None = None,
    timing_recorder: TimingRecorder | None = None,
    raw_workers: int = DEFAULT_ALIGNMENT_WORKERS,
    raw_worker_backend: str = "thread",
    should_cancel: Callable[[], bool] | None = None,
    retain_partition_memory: bool = False,
    retain_acquisition_cache: bool = False,
    acquisition_cache_dir: Path | None = None,
    max_partition_jobs: int = 100_000,
    grouping_only: bool = False,
    mass_recalibration_by_sample: Mapping[str, PreparedRawMassRecalibration]
    | None = None,
) -> CompletedUntargetBatchWorkflowResult | PreparedGroupingExperiment:
    """One instrumented scheduler for complete Alignment and frozen grouping."""
    recorder = timing_recorder or TimingRecorder.disabled("alignment")
    output_dir.mkdir(parents=True, exist_ok=True)
    inference_counts: Counter[str] = Counter()
    inference_counts_lock = Lock()

    def runtime_progress(event: Mapping[str, object]) -> None:
        counts = event.get("operation_counts")
        if event.get("event") == "feature_membership_inference" and isinstance(
            counts, dict
        ):
            with inference_counts_lock:
                for key, value in counts.items():
                    if key.startswith("largest_"):
                        inference_counts[key] = max(inference_counts[key], value)
                    else:
                        inference_counts[key] += value
                runtime_stage.metrics.update(
                    {
                        f"feature_inference_{key}": value
                        for key, value in inference_counts.items()
                    }
                )
        recorder.heartbeat_event(event)

    with (
        recorder.stage("alignment.untargeted_cid_nl_runtime") as runtime_stage,
        recorder.process_memory_scope("retained_runtime") as runtime_peak,
    ):
        completed = execute_untargeted_plan(
            plan,
            output_dir=output_dir,
            telemetry_provider=lambda coordinator: _coarse_runtime_telemetry(
                coordinator, runtime_peak
            ),
            recovery_marker=OwnerPathRuntimeMarker(
                output_dir / ".cid_nl_runtime.json",
                _RUNTIME_ARTIFACT_NAMESPACE,
                allow_failed_attempt_retry=True,
            ),
            progress_callback=runtime_progress,
            alignment_config=alignment_config,
            known_istd_reference=known_istd_reference,
            raw_workers=raw_workers,
            raw_worker_backend=raw_worker_backend,
            should_cancel=should_cancel,
            retain_partition_memory=retain_partition_memory,
            retain_acquisition_cache=retain_acquisition_cache,
            acquisition_cache_dir=acquisition_cache_dir,
            pipeline_overlap=True,
            max_partition_jobs=max_partition_jobs,
            grouping_only=grouping_only,
            mass_recalibration_by_sample=mass_recalibration_by_sample,
        )
    return completed


def _load_physical_replay_inputs(
    *,
    discovery_batch: DiscoveryBatchInput,
    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]],
    raw_paths: tuple[Path, ...],
    settings: DiscoverySettings,
    profile: SignalProcessingProfile,
    raw_workers: int = DEFAULT_ALIGNMENT_WORKERS,
    verified_acquisition_sources: Mapping[str, AcquisitionSourceExpectation]
    | None = None,
    retained_records_sink: Callable[
        [str, tuple[DiscoveryPhysicalFactRecord, ...]], None
    ]
    | None = None,
) -> tuple[
    dict[str, tuple[DiscoveryCandidate, ...]],
    dict[str, DiscoveryPhysicalFactRecord],
    tuple[AcquisitionSourceExpectation, ...],
]:
    spec, acquisition_source_expectations = (
        compile_discovery_processing_run_spec_with_expectations(
            raw_paths,
            discovery_settings=settings,
            signal_processing_profile=profile,
            verified_acquisition_sources=verified_acquisition_sources,
        )
    )

    def _load_sample_input(
        acquisition_input: ProcessingAcquisitionInput,
    ) -> tuple[
        str,
        tuple[DiscoveryCandidate, ...],
        dict[str, DiscoveryPhysicalFactRecord],
        tuple[DiscoveryPhysicalFactRecord, ...],
    ]:
        stem = acquisition_input.sample_stem
        physical_facts_json = (
            discovery_batch.candidate_csvs[stem].parent
            / "discovery_physical_facts.json"
        )
        validate_discovery_physical_facts_artifact(
            discovery_batch.candidate_receipts[stem], physical_facts_json
        )
        records = read_discovery_physical_fact_records(
            physical_facts_json,
            expected_run_spec=spec,
            expected_acquisition_input=acquisition_input,
        )
        by_id = {record.candidate_id: record for record in records}
        if len(by_id) != len(records):
            raise ValueError(
                "Discovery physical sidecar has ambiguous candidate identity"
            )
        sample_candidates: list[DiscoveryCandidate] = []
        sample_records: dict[str, DiscoveryPhysicalFactRecord] = {}
        for candidate in candidates_by_sample[stem]:
            record = by_id.get(candidate.candidate_id)
            if record is None:
                if candidate.ms1_peak_found:
                    raise ValueError(
                        "Discovery candidate lacks its exact physical record"
                    )
                # Diagnostic orphan rows have no persisted physical observation.
                sample_candidates.append(candidate)
                continue
            sample_candidates.append(_restore_physical_candidate(candidate, record))
            if candidate.candidate_id in sample_records:
                raise ValueError("Discovery physical candidate identity is duplicated")
            sample_records[candidate.candidate_id] = record
        return stem, tuple(sample_candidates), sample_records, records

    restored: dict[str, tuple[DiscoveryCandidate, ...]] = {}
    selected_records: dict[str, DiscoveryPhysicalFactRecord] = {}

    workers = min(raw_workers, len(spec.acquisition_inputs))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for stem, sample_candidates, sample_records, all_records in executor.map(
                _load_sample_input, spec.acquisition_inputs
            ):
                if retained_records_sink is not None:
                    retained_records_sink(stem, all_records)
                restored[stem] = sample_candidates
                for cid, rec in sample_records.items():
                    if cid in selected_records:
                        raise ValueError(
                            "Discovery physical candidate identity is duplicated"
                        )
                    selected_records[cid] = rec
    else:
        for acquisition_input in spec.acquisition_inputs:
            stem, sample_candidates, sample_records, all_records = _load_sample_input(
                acquisition_input
            )
            if retained_records_sink is not None:
                retained_records_sink(stem, all_records)
            restored[stem] = sample_candidates
            for cid, rec in sample_records.items():
                if cid in selected_records:
                    raise ValueError(
                        "Discovery physical candidate identity is duplicated"
                    )
                selected_records[cid] = rec

    return restored, selected_records, acquisition_source_expectations


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
        candidate_val = getattr(candidate, name)
        if candidate_val != value and (
            format_discovery_csv_value(name, candidate_val)
            != format_discovery_csv_value(name, value)
        ):
            raise ValueError(
                f"{candidate.candidate_id}: physical {name} crossed its CSV receipt"
            )
    return replace(candidate, **values)


def _coarse_runtime_telemetry(
    coordinator: Any, peak: ProcessMemoryPeak
) -> RuntimeTelemetrySamples:
    worker_keys = runtime_started_worker_keys(coordinator)
    measured = peak.availability == "measured" and peak.rss_bytes is not None
    unavailable_reason = "not_collected_by_alignment_foreground_runner"
    return RuntimeTelemetrySamples(
        event_monotonic_offsets_ns=None,
        artifact_io=None,
        process_tree_peak_rss=RuntimeProcessTreePeakRssSample(
            availability=(
                RuntimeMetricAvailability.MEASURED
                if measured
                else RuntimeMetricAvailability.UNSUPPORTED
            ),
            value=peak.rss_bytes if measured else None,
            reason=(
                None if measured else (peak.reason or "process_tree_sample_unavailable")
            ),
        ),
        worker_peak_rss=tuple(
            RuntimeWorkerPeakRssSample(
                worker_key=worker_key,
                availability=RuntimeMetricAvailability.UNSUPPORTED,
                reason="not_applicable_shared_process_partition_workers",
            )
            for worker_key in worker_keys
        ),
        unavailable_reason=unavailable_reason,
    )


__all__ = [
    "complete_untargeted_alignment",
]
