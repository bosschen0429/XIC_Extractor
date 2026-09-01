"""Opt-in RAW-to-ModeTrack workflow for debug and batch review only."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.conservative_backfill import (
    assess_mode_track_conservative_backfill_groups,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleSuccessorApplication,
    validate_cross_sample_group_population,
)
from xic_extractor.alignment.csv_io import (
    read_discovery_batch_index,
    read_owner_bound_discovery_candidates_csv,
)
from xic_extractor.alignment.mode_track import (
    AcceptedModeTrackRefinement,
    ModeTrackRefinementTransaction,
    ModeTrackTrajectoryPolicy,
    apply_accepted_mode_track_refinement,
    bootstrap_mode_track_refinement_from_cid_nl_support,
)
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    UntargetedCidNlModeTrackProjection,
    build_untargeted_cid_nl_mode_track_projection,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.models import DiscoverySettings
from xic_extractor.discovery.pipeline import run_discovery_batch
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_observability import RuntimeMetricAvailability
from xic_extractor.execution_runtime_owner import (
    RuntimeArtifactIoSample,
    RuntimeProcessTreePeakRssSample,
    RuntimeTelemetrySamples,
    RuntimeWorkerPeakRssSample,
    runtime_observation_event_count,
    runtime_started_worker_keys,
    start_runtime_attempt,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    UntargetedCidNlRealDataContext,
    build_untargeted_cid_nl_real_data_context,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
    UntargetedCidNlRealDataRuntimeJob,
    execute_untargeted_cid_nl_real_data_runtime,
    untargeted_cid_nl_runtime_call_cost,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    mint_untargeted_cid_nl_method_subject,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS


@dataclass(frozen=True, slots=True)
class ModeTrackWorkflowRun:
    projection: UntargetedCidNlModeTrackProjection
    discovery_index: Path
    call_cost: Mapping[str, int]
    refinement_transactions: tuple[ModeTrackRefinementTransaction, ...] = ()
    cross_sample_applications: tuple[CrossSampleSuccessorApplication, ...] = ()


def run_untargeted_cid_nl_mode_track_workflow(
    *,
    raw_files: tuple[Path, ...],
    sample_metadata: tuple[SampleMetadata, ...],
    dll_dir: Path,
    output_dir: Path,
    settings: DiscoverySettings,
    precursor_mz: float,
    source_artifact_relpath: str,
    sample_identity_namespace: str,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> ModeTrackWorkflowRun:
    """Build a non-authoritative diagnostic projection for explicit review."""

    if not raw_files or len(raw_files) != len(sample_metadata):
        raise ValueError(
            "RAW and sample metadata populations must be non-empty and equal"
        )
    if not dll_dir.is_dir() or any(not path.is_file() for path in raw_files):
        raise ValueError("RAW or Thermo DLL input does not exist")
    if output_dir.exists():
        raise ValueError("workflow output already exists")
    if len(settings.neutral_loss_profiles) != 1:
        raise ValueError("ModeTrack CID-NL workflow requires one neutral-loss tag")
    output_dir.mkdir(parents=True)

    discovery_dir = output_dir / "discovery"
    peak_config = _peak_config(
        raw_files[0].parent,
        dll_dir,
        discovery_dir,
        settings,
    )
    discovery = run_discovery_batch(
        raw_files,
        output_dir=discovery_dir,
        settings=settings,
        peak_config=peak_config,
    )
    _progress(progress_callback, "discovery_complete")
    batch = read_discovery_batch_index(
        discovery.batch_index_csv,
        require_owner_closure=True,
    )
    candidates_by_sample = {}
    receipts_by_sample = {}
    for raw_file in raw_files:
        receipt = batch.candidate_receipts[raw_file.stem]
        if receipt is None:
            raise ValueError("fresh Discovery did not mint every owner receipt")
        receipts_by_sample[raw_file.stem] = receipt
        candidates_by_sample[raw_file.stem] = read_owner_bound_discovery_candidates_csv(
            batch.candidate_csvs[raw_file.stem],
            receipt=receipt,
            require_successor_columns=True,
        )

    neutral_loss = settings.neutral_loss_profiles[0]
    method_subject = mint_untargeted_cid_nl_method_subject(
        precursor_mz=precursor_mz,
        precursor_tolerance_ppm=settings.precursor_mz_tolerance_ppm,
        neutral_loss_da=neutral_loss.neutral_loss_da,
    )
    context = build_untargeted_cid_nl_real_data_context(
        raw_paths=raw_files,
        sample_metadata=sample_metadata,
        method_subject=method_subject,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=receipts_by_sample,
        sample_identity_namespace=sample_identity_namespace,
    )
    jobs = _mode_track_runtime_jobs(
        context=context,
        dll_dir=dll_dir,
    )
    runtime = start_runtime_attempt(
        attempt=context.processing_owners.attempt,
        work_unit_keys=tuple(job.work_unit_key for job in jobs),
        owner_completeness_attestation=f"{sample_identity_namespace}:v1",
    )
    runtime_result = execute_untargeted_cid_nl_real_data_runtime(
        runtime,
        jobs,
        telemetry_provider=_coarse_runtime_telemetry,
        recovery_marker=OwnerPathRuntimeMarker(
            output_dir / "runtime.json",
            sample_identity_namespace,
        ),
        progress_callback=progress_callback,
    )
    if not runtime_result.succeeded or len(runtime_result.sample_results) != len(jobs):
        raise RuntimeError("Untargeted CID-NL runtime did not complete atomically")
    m7_call_cost = untargeted_cid_nl_runtime_call_cost(runtime_result.finalization)
    if m7_call_cost["raw_open_count"] > len(raw_files):
        raise RuntimeError("Untargeted CID-NL runtime exceeded one RAW open per sample")
    _progress(progress_callback, "m7_complete")

    sample_assessments = tuple(
        (sample, result.adapter_result.assessment_input.assessment)
        for sample, result in zip(
            context.samples,
            runtime_result.sample_results,
            strict=True,
        )
    )
    refinement = bootstrap_mode_track_refinement_from_cid_nl_support(
        processing_owners=context.processing_owners,
        sample_assessments=sample_assessments,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=_mode_track_trajectory_policy(),
    )
    cross_sample_applications = _apply_mode_track_refinement_transactions(
        refinement.transactions
    )
    model_set = refinement.model_set
    batches = refinement.assignment_batches
    facts = assess_mode_track_conservative_backfill_groups(
        processing_owners=context.processing_owners,
        model_set=model_set,
        assignment_batches=batches,
        sample_assessments=sample_assessments,
    )
    projection = build_untargeted_cid_nl_mode_track_projection(
        processing_owners=context.processing_owners,
        mode_track_facts=facts,
        source_artifact_relpath=source_artifact_relpath,
    )
    _progress(progress_callback, "mode_track_complete")
    return ModeTrackWorkflowRun(
        projection=projection,
        discovery_index=discovery.batch_index_csv,
        call_cost=_workflow_call_cost(
            discovery_raw_open_count=len(raw_files),
            m7_call_cost=m7_call_cost,
        ),
        refinement_transactions=refinement.transactions,
        cross_sample_applications=cross_sample_applications,
    )


def _apply_mode_track_refinement_transactions(
    transactions: tuple[ModeTrackRefinementTransaction, ...],
) -> tuple[CrossSampleSuccessorApplication, ...]:
    if not transactions:
        return ()
    population = validate_cross_sample_group_population(
        tuple(
            predecessor
            for transaction in transactions
            for predecessor in transaction.application_context.predecessors
        )
    )
    return tuple(
        apply_accepted_mode_track_refinement(
            trajectory_input=transaction.trajectory_input,
            proposal=transaction.proposal,
            application_context=transaction.application_context,
            population=population,
        )
        for transaction in transactions
        if type(transaction.proposal) is AcceptedModeTrackRefinement
    )


def _mode_track_runtime_jobs(
    *,
    context: UntargetedCidNlRealDataContext,
    dll_dir: Path,
) -> tuple[UntargetedCidNlRealDataRuntimeJob, ...]:
    method_subject = context.method_subject
    if type(method_subject) is not CidNlMethodSubject:
        raise ValueError("ModeTrack CID-NL workflow requires one method subject")
    jobs = []
    for sample in context.samples:
        sample_stem = sample.metadata.raw_stem
        jobs.append(
            UntargetedCidNlRealDataRuntimeJob(
                processing_owners=context.processing_owners,
                sample=sample,
                method_subject=method_subject,
                candidates=context.candidates_by_sample[sample_stem],
                discovery_receipt=context.discovery_receipts_by_sample[sample_stem],
                settings=context.settings,
                peak_config=context.peak_config,
                raw_path=context.raw_paths_by_sample[sample_stem],
                dll_dir=dll_dir,
                work_unit_key=f"untargeted-cid-nl:{sample_stem}",
            )
        )
    return tuple(jobs)


def _workflow_call_cost(
    *,
    discovery_raw_open_count: int,
    m7_call_cost: Mapping[str, int],
) -> dict[str, int]:
    m7_raw_open_count = m7_call_cost["raw_open_count"]
    return {
        "discovery_raw_open_count": discovery_raw_open_count,
        "m7_raw_open_count": m7_raw_open_count,
        "total_raw_open_count": discovery_raw_open_count + m7_raw_open_count,
        "m7_xic_batch_count": m7_call_cost["xic_batch_count"],
        "m7_logical_xic_request_count": m7_call_cost[
            "logical_xic_request_count"
        ],
        "m7_adapter_chromatogram_call_count": m7_call_cost[
            "adapter_chromatogram_call_count"
        ],
        "m7_trace_point_count": m7_call_cost["trace_point_count"],
    }


def _mode_track_trajectory_policy() -> ModeTrackTrajectoryPolicy:
    return ModeTrackTrajectoryPolicy(
        trajectory_policy_version="anchor-trajectory-v1",
        band_policy_version="fixed-band-v1",
        prediction_half_width_min=2 / 60,
        max_interpolation_gap=10,
        max_extrapolation_gap=10,
    )


def _peak_config(
    data_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    settings: DiscoverySettings,
) -> ExtractionConfig:
    defaults = CANONICAL_SETTINGS_DEFAULTS
    return ExtractionConfig(
        data_dir=data_dir,
        dll_dir=dll_dir,
        output_csv=output_dir / "unused_xic_results.csv",
        diagnostics_csv=output_dir / "unused_xic_diagnostics.csv",
        smooth_window=int(defaults["smooth_window"]),
        smooth_polyorder=int(defaults["smooth_polyorder"]),
        peak_rel_height=float(defaults["peak_rel_height"]),
        peak_min_prominence_ratio=float(defaults["peak_min_prominence_ratio"]),
        ms2_precursor_tol_da=settings.ms2_precursor_tol_da,
        nl_min_intensity_ratio=settings.nl_min_intensity_ratio,
        resolver_mode=settings.resolver_mode,
    )


def _coarse_runtime_telemetry(
    coordinator: Any,
) -> RuntimeTelemetrySamples:
    event_count = runtime_observation_event_count(coordinator)
    worker_keys = runtime_started_worker_keys(coordinator)
    reason = "not_collected_by_mode_track_foreground_runner"
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


def _progress(
    callback: Callable[[Mapping[str, object]], None] | None,
    event: str,
) -> None:
    if callback is not None:
        callback({"event": event})
