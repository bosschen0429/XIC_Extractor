from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import cast

from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.grouping_experiment import replay_grouping
from xic_extractor.applications.untargeted.istd_reference import (
    extract_batch_known_istd_reference,
    read_known_istd_reference,
)
from xic_extractor.applications.untargeted.publication import publish_untargeted_outputs
from xic_extractor.applications.untargeted.request import (
    UntargetedExecutionContext,
    UntargetedRequest,
)
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    serialize_mass_recalibration_result,
)
from xic_extractor.diagnostics.grouping_experiment_io import (
    group_signature,
    read_grouping_inputs,
    write_grouping_inputs,
)
from xic_extractor.discovery.csv_reader import (
    DiscoveryBatchInput,
    read_discovery_batch_index,
)
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.output.alignment.output_levels import (
    AlignmentOutputLevel,
    artifact_names_for_output_level,
)
from xic_extractor.output.alignment.pipeline_outputs import (
    AlignmentRunOutputs,
    alignment_metadata,
)
from xic_extractor.raw_mass_recalibration import PreparedRawMassRecalibration
from xic_extractor.raw_mass_recalibration_batch import prepare_batch_mass_recalibration
from xic_extractor.untargeted_execution.alignment import (
    complete_untargeted_alignment,
    execute_prepared_alignment,
    prepare_untargeted_alignment_inputs,
)
from xic_extractor.untargeted_execution.grouping_experiment import (
    PreparedGroupingExperiment,
)

GROUPING_SNAPSHOT_NAME = "grouping-inputs.json.gz"


class AlignmentProcessExecutionError(RuntimeError):
    """Raised when retained Untargeted execution fails before publication."""


def run_untargeted_application(
    request: UntargetedRequest,
    context: UntargetedExecutionContext | None = None,
) -> AlignmentRunOutputs:
    """Execute and publish a normalized retained CID-NL Alignment request."""
    signal_processing_profile = request.resolve_processing_profile()
    context = context or UntargetedExecutionContext()
    recorder = context.timing_recorder or TimingRecorder.disabled("alignment")
    recorder.record(
        "alignment.run_config",
        elapsed_sec=0.0,
        metrics={
            "raw_workers": request.execution.raw_workers,
            "raw_worker_backend": request.execution.raw_worker_backend,
            "raw_xic_batch_size": 1,
            "owner_backfill_xic_backend": "raw",
            "owner_backfill_window_strategy": "exact",
            "owner_backfill_superwindow_span_factor": 2,
            "output_level": request.publication.output_level,
            "backfill_scope": "full-audit",
            "require_discovery_owner_closure": True,
        },
    )
    with recorder.stage("alignment.read_batch_index"):
        batch = read_discovery_batch_index(request.discovery_batch_index)
    with recorder.stage("alignment.read_candidates") as stage:
        peaks_by_sample = DiscoveryInputSource(batch)
        stage.metrics["candidate_count"] = len(peaks_by_sample.locations)
        stage.metrics["feature_candidate_count"] = len(
            peaks_by_sample.feature_candidate_ids()
        )
    outputs = replace(
        request.publication.output_paths(request.output_dir),
        run_metadata_json=request.output_dir / "alignment_run_metadata.json",
    )
    _reject_alignment_output_collisions(output_dir=request.output_dir, outputs=outputs)
    if context.progress_callback:
        context.progress_callback({"stage": "mass_recalibration"})
    mass_recalibration_by_sample = _prepare_mass_recalibration(
        request, batch, recorder, context
    )
    if context.progress_callback:
        context.progress_callback({"stage": "known_istd_reference"})
    resolved_known_istd_reference = _resolve_known_istd_reference(
        request, batch, recorder, mass_recalibration_by_sample
    )

    metadata = alignment_metadata(
        discovery_batch_index=request.discovery_batch_index,
        raw_dir=request.raw_dir,
        dll_dir=request.dll_dir,
        output_level=request.publication.output_level,
        peak_config=request.peak_config,
        signal_processing_profile=signal_processing_profile,
    )
    # Source-bound Discovery receipts carry each RAW's mass result.
    # Publication renders these facts; it never recomputes a mass policy.
    metadata["mass_recalibration_by_sample"] = json.dumps(
        {
            sample: json.loads(
                serialize_mass_recalibration_result(receipt.mass_recalibration_result)
            )
            for sample, receipt in batch.peak_receipts.items()
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if not request.alignment_config.backfill_enabled:
        metadata["backfill_execution"] = "not_run"
    if resolved_known_istd_reference is not None:
        metadata.update(
            {
                f"known_istd_reference_{key}": value
                for key, value in resolved_known_istd_reference.provenance.items()
            }
        )
    if context.should_cancel is not None and context.should_cancel():
        raise InterruptedError(
            "Alignment cancelled before retained untargeted execution"
        )
    try:
        completed = complete_untargeted_alignment(
            discovery_batch=batch,
            peaks_by_sample=peaks_by_sample,
            raw_dir=request.raw_dir,
            dll_dir=request.dll_dir,
            output_dir=request.output_dir,
            settings=request.discovery_settings,
            peak_config=request.peak_config,
            alignment_config=request.alignment_config,
            sample_metadata=request.sample_metadata,
            known_istd_reference=resolved_known_istd_reference,
            timing_recorder=recorder,
            signal_processing_profile=signal_processing_profile,
            raw_workers=request.execution.raw_workers,
            raw_worker_backend=request.execution.raw_worker_backend,
            should_cancel=context.should_cancel,
            progress_callback=context.progress_callback,
            retain_partition_memory=False,
            retain_acquisition_cache=request.execution.retain_acquisition_cache,
            acquisition_cache_dir=request.execution.acquisition_cache_dir,
            max_partition_jobs=request.execution.max_partition_jobs,
            mass_recalibration_by_sample=(
                mass_recalibration_by_sample
                if request.discovery_settings.mass_recalibration
                is not MassRecalibration.NONE
                else None
            ),
        )
    except RuntimeError as exc:
        raise AlignmentProcessExecutionError(str(exc)) from exc
    if context.progress_callback:
        context.progress_callback({"stage": "publish_matrix"})
    return publish_untargeted_outputs(
        completed,
        output_dir=request.output_dir,
        outputs=outputs,
        policy=request.publication,
        metadata=metadata,
        timing_recorder=recorder,
        should_cancel=context.should_cancel,
    )


def _prepare_mass_recalibration(
    request: UntargetedRequest,
    batch: DiscoveryBatchInput,
    recorder: TimingRecorder,
    context: UntargetedExecutionContext,
) -> dict[str, PreparedRawMassRecalibration]:
    policy = request.discovery_settings.mass_recalibration
    if (
        policy is MassRecalibration.NONE
        and request.known_istd_reference is None
        and request.targets_csv is not None
    ):
        policy = request.peak_config.mass_recalibration
    if policy is MassRecalibration.NONE:
        return {}
    from xic_extractor.acquisition.raw_paths import existing_raw_paths

    paths = existing_raw_paths(
        sample_order=batch.sample_order,
        raw_files=batch.raw_files,
        raw_dir=request.raw_dir,
    )
    with recorder.stage("alignment.mass_recalibration") as stage:
        results = prepare_batch_mass_recalibration(
            tuple(
                (path, batch.peak_receipts[stem].acquisition_source)
                for stem, path in paths.items()
            ),
            request.dll_dir,
            policy,
            workers=request.execution.raw_workers,
            backend=request.execution.raw_worker_backend,
            should_cancel=context.should_cancel,
        )
        stage.metrics["raw_file_count"] = len(results)
        return results


def _resolve_known_istd_reference(
    request: UntargetedRequest,
    batch: DiscoveryBatchInput,
    recorder: TimingRecorder,
    mass_recalibration_by_sample: dict[str, PreparedRawMassRecalibration] | None = None,
) -> KnownIstdReference | None:
    if request.known_istd_reference is not None:
        return request.known_istd_reference
    if request.targets_csv is not None:
        from xic_extractor.acquisition.raw_paths import existing_raw_paths
        from xic_extractor.configuration.targets import read_istd_targets

        selection = read_istd_targets(request.targets_csv).for_method(
            request.discovery_settings.neutral_loss_profiles
        )
        if not selection.targets:
            return None
        raw_paths_by_sample = existing_raw_paths(
            sample_order=batch.sample_order,
            raw_files=batch.raw_files,
            raw_dir=request.raw_dir,
        )
        with recorder.stage("alignment.known_istd_reference") as stage:
            stage.metrics["raw_file_count"] = len(raw_paths_by_sample)
            stage.metrics["istd_target_count"] = len(selection.targets)
            return extract_batch_known_istd_reference(
                targets=selection.targets,
                raw_paths_by_sample=raw_paths_by_sample,
                dll_dir=request.dll_dir,
                peak_config=request.peak_config,
                mass_recalibration_by_sample=(
                    mass_recalibration_by_sample
                    if request.peak_config.mass_recalibration
                    is not MassRecalibration.NONE
                    else None
                ),
                sample_metadata=request.sample_metadata,
                source_identifier=str(request.targets_csv.resolve()),
                source_sha256=hashlib.sha256(
                    request.targets_csv.read_bytes()
                ).hexdigest(),
            )
    if request.known_istd_reference_workbook is not None:
        return read_known_istd_reference(request.known_istd_reference_workbook)
    return None


def snapshot_untargeted_grouping(
    request: UntargetedRequest,
    context: UntargetedExecutionContext | None = None,
) -> Path:
    """Run the formal Alignment up to grouping and write its replay snapshot.

    Same inputs, ISTD reference, initial assessment, calibration and grouping
    as a full run; stops before Backfill and publication.
    """
    context = context or UntargetedExecutionContext()
    recorder = context.timing_recorder or TimingRecorder.disabled("alignment")
    with recorder.stage("alignment.read_batch_index"):
        batch = read_discovery_batch_index(request.discovery_batch_index)
    peaks_by_sample = DiscoveryInputSource(batch)
    mass_recalibration_by_sample = _prepare_mass_recalibration(
        request, batch, recorder, context
    )
    known_istd_reference = _resolve_known_istd_reference(
        request, batch, recorder, mass_recalibration_by_sample
    )
    snapshot = request.output_dir / GROUPING_SNAPSHOT_NAME
    if snapshot.exists():
        raise ValueError(f"grouping snapshot already exists: {snapshot}")
    prepared = prepare_untargeted_alignment_inputs(
        discovery_batch=batch,
        peaks_by_sample=peaks_by_sample,
        raw_dir=request.raw_dir,
        dll_dir=request.dll_dir,
        output_dir=request.output_dir,
        settings=request.discovery_settings,
        peak_config=request.peak_config,
        sample_metadata=request.sample_metadata,
        timing_recorder=recorder,
        signal_processing_profile=request.resolve_processing_profile(),
        raw_workers=request.execution.raw_workers,
        raw_worker_backend=request.execution.raw_worker_backend,
        should_cancel=context.should_cancel,
        verified_acquisition_sources=(
            {
                sample: prepared.acquisition
                for sample, prepared in mass_recalibration_by_sample.items()
            }
            if mass_recalibration_by_sample
            else None
        ),
    )
    try:
        with recorder.stage("alignment.grouping_snapshot") as stage:
            grouping = cast(
                PreparedGroupingExperiment,
                execute_prepared_alignment(
                    prepared.plan,
                    output_dir=request.output_dir,
                    alignment_config=request.alignment_config,
                    known_istd_reference=known_istd_reference,
                    timing_recorder=recorder,
                    acquisition_cache_dir=request.execution.acquisition_cache_dir,
                    retain_acquisition_cache=request.execution.retain_acquisition_cache,
                    raw_workers=request.execution.raw_workers,
                    raw_worker_backend=request.execution.raw_worker_backend,
                    max_partition_jobs=request.execution.max_partition_jobs,
                    should_cancel=context.should_cancel,
                    grouping_only=True,
                    mass_recalibration_by_sample=(
                        mass_recalibration_by_sample
                        if request.discovery_settings.mass_recalibration
                        is not MassRecalibration.NONE
                        else None
                    ),
                ),
            )
            write_grouping_inputs(
                snapshot,
                grouping.inputs,
                provenance={
                    "discovery_batch_index": str(
                        request.discovery_batch_index.resolve()
                    ),
                    "partition_count": grouping.total_partition_count,
                    "initial_job_count": grouping.initial_job_count,
                },
            )
            stage.metrics["partition_count"] = grouping.total_partition_count
            stage.metrics["group_count"] = len(grouping.groups)
        with recorder.stage("alignment.grouping_snapshot.verify"):
            restored, _ = read_grouping_inputs(snapshot)
            if group_signature(replay_grouping(restored)) != group_signature(
                grouping.groups
            ):
                raise ValueError("Snapshot replay differs from formal initial grouping")
    finally:
        prepared.close()
    return snapshot


# No level writes these any more. They stay owned so that a copy left by an
# earlier run is still reported as a stale artifact instead of being mistaken
# for this run's output.
_RETIRED_ALIGNMENT_ARTIFACT_NAMES = (
    "alignment_results.xlsx",
    "review_report.html",
    "event_to_ms1_owner.tsv",
    "ambiguous_ms1_owners.tsv",
)

_CONDITIONAL_ALIGNMENT_ARTIFACT_NAMES = (
    "alignment_explanation.sqlite",
    "alignment_matrix_status.tsv",
    "alignment_cell_integration_audit.tsv",
    "alignment_owner_backfill_candidate_audit.tsv",
    "skipped_evidence_ledger.tsv",
    "alignment_run_metadata.json",
)


def _reject_alignment_output_collisions(
    *, output_dir: Path, outputs: AlignmentRunOutputs
) -> None:
    output_levels: tuple[AlignmentOutputLevel, ...] = (
        "production",
        "machine",
        "debug",
        "validation-minimal",
    )
    owned_names = {
        name
        for level in output_levels
        for name in artifact_names_for_output_level(level)
    }
    owned_names.update(_CONDITIONAL_ALIGNMENT_ARTIFACT_NAMES)
    owned_names.update(_RETIRED_ALIGNMENT_ARTIFACT_NAMES)
    owned_names.update(
        (
            path.name
            for path in vars(outputs).values()
            if isinstance(path, Path) and path.parent == output_dir
        )
    )
    collisions = _owned_path_collisions(
        (output_dir / name for name in sorted(owned_names))
    )
    if collisions:
        names = ", ".join((path.name for path in sorted(collisions)))
        raise FileExistsError(
            "Alignment output directory contains owned artifact collisions: " + names
        )


def _owned_path_collisions(paths: Iterable[Path]) -> list[Path]:
    collisions: list[Path] = []
    for path in paths:
        for candidate in (
            path,
            path.with_name(f"{path.name}.tmp"),
            path.with_name(f"{path.name}.bak"),
        ):
            if candidate.exists():
                collisions.append(candidate)
    return collisions
