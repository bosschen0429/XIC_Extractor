"""Alignment execution adapter for pure single-provider Untargeted CID-NL."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.raw_mass_recalibration import PreparedRawMassRecalibration

from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from pathlib import Path
from threading import Lock
from typing import cast

from xic_extractor.acquisition.raw_paths import existing_raw_paths
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.configuration.execution_profile import DEFAULT_ALIGNMENT_WORKERS
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.batch_contract import DiscoveryPeakUniverseReceipt
from xic_extractor.discovery.csv_reader import (
    DiscoveryBatchInput,
)
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec_with_expectations,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.processing_identity import AcquisitionSourceExpectation
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
    prepare_indexed_untargeted_plan,
)

_SAMPLE_IDENTITY_NAMESPACE = "alignment-untargeted-cid-nl"
_RUNTIME_ARTIFACT_NAMESPACE = "untargeted-cid-nl-alignment"


def prepare_untargeted_alignment_inputs(
    *,
    discovery_batch: DiscoveryBatchInput,
    peaks_by_sample: DiscoveryInputSource,
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
) -> UntargetedInputPlan:
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

    for sample_stem in discovery_batch.sample_order:
        receipt = discovery_batch.peak_receipts[sample_stem]
        if not isinstance(receipt, DiscoveryPeakUniverseReceipt):
            raise ValueError(
                "CID-NL production requires owner-closed Discovery receipts"
            )

    ordered_raw_paths = tuple(
        raw_paths[sample_stem] for sample_stem in discovery_batch.sample_order
    )
    if sample_metadata is not None:
        # Supplied metadata also lists excluded and context injections, which
        # Discovery did not analyze; align the samples in the batch.
        by_stem = {row.raw_stem: row for row in sample_metadata}
        missing_stems = [
            stem for stem in discovery_batch.sample_order if stem not in by_stem
        ]
        if missing_stems:
            raise ValueError(
                "sample metadata has no row for: " + ", ".join(missing_stems)
            )
        retained_sample_metadata = tuple(
            by_stem[stem] for stem in discovery_batch.sample_order
        )
    else:
        from xic_extractor.raw_sequence import read_analysis_set

        by_path = {
            path.resolve(): entry.metadata
            for entry in read_analysis_set(
                raw_dir, dll_dir, analysis="untargeted"
            ).entries
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
    with recorder.stage("alignment.verify_acquisition_sources"):
        _spec, expectations = compile_discovery_processing_run_spec_with_expectations(
            ordered_raw_paths,
            discovery_settings=settings,
            signal_processing_profile=signal_processing_profile,
            fingerprint_workers=raw_workers,
            verified_acquisition_sources=verified_acquisition_sources,
        )
        # Discovery's event table and the RAW Alignment reads must be the same
        # acquisition, with or without mass recalibration.
        changed = [
            stem
            for stem, expectation in zip(
                discovery_batch.sample_order, expectations, strict=True
            )
            if expectation.source
            != discovery_batch.peak_receipts[stem].acquisition_source
        ]
        if changed:
            raise ValueError(
                "RAW changed after Discovery; regenerate Discovery for: "
                + ", ".join(changed)
            )
    with recorder.stage("alignment.prepare_feature_inputs") as stage:
        stage.metrics["feature_candidate_count"] = len(
            peaks_by_sample.feature_candidate_ids()
        )
        stage.metrics["discovery_record_count"] = len(peaks_by_sample.locations)
        return prepare_indexed_untargeted_plan(
            source=peaks_by_sample,
            raw_paths=ordered_raw_paths,
            sample_metadata=retained_sample_metadata,
            settings=settings,
            peak_config=peak_config,
            dll_dir=dll_dir,
            profile=signal_processing_profile,
            acquisition_source_expectations=expectations,
        )


def complete_untargeted_alignment(
    *,
    discovery_batch: DiscoveryBatchInput,
    peaks_by_sample: DiscoveryInputSource,
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
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    retain_partition_memory: bool = False,
    retain_acquisition_cache: bool = False,
    acquisition_cache_dir: Path | None = None,
    max_partition_jobs: int = 100_000,
    mass_recalibration_by_sample: Mapping[str, PreparedRawMassRecalibration]
    | None = None,
) -> CompletedUntargetBatchWorkflowResult:
    """Execute and compose one owner-closed CID-NL batch exactly once."""

    if progress_callback:
        progress_callback({"stage": "alignment_inputs"})
    plan = prepare_untargeted_alignment_inputs(
        discovery_batch=discovery_batch,
        peaks_by_sample=peaks_by_sample,
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
    with ExitStack() as pending:
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
                progress_callback=progress_callback,
                retain_partition_memory=retain_partition_memory,
                retain_acquisition_cache=retain_acquisition_cache,
                acquisition_cache_dir=acquisition_cache_dir,
                max_partition_jobs=max_partition_jobs,
                mass_recalibration_by_sample=mass_recalibration_by_sample,
            ),
        )
        pending.callback(completed.close)

        completed = completed.bind_publication_inputs(
            peaks_by_sample=peaks_by_sample,
            precursor_mz_by_candidate=peaks_by_sample.precursor_mz_by_candidate,
            neutral_loss_by_tag=settings.neutral_loss_by_tag,
            alignment_config=alignment_config,
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
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
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
        if progress_callback:
            progress_callback(event)

    with (
        recorder.stage("alignment.untargeted_cid_nl_runtime") as runtime_stage,
        recorder.process_memory_scope("retained_runtime"),
    ):
        completed = execute_untargeted_plan(
            plan,
            output_dir=output_dir,
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


__all__ = [
    "complete_untargeted_alignment",
]
