from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

from xic_extractor.alignment.batch_correspondence import (
    BatchCorrespondence,
    BatchFeature,
    batch_feature,
    batch_istds,
    choose_reference_batch,
    compose_calibrations,
    correspond_batches,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.grouping_experiment import combine_grouping_inputs
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration
from xic_extractor.applications.untargeted.istd_reference import (
    extract_batch_known_istd_reference,
    read_known_istd_reference,
)
from xic_extractor.applications.untargeted.publication import (
    publish_batch_untargeted_outputs,
    publish_untargeted_outputs,
    untargeted_child_projection,
)
from xic_extractor.applications.untargeted.request import (
    UntargetedExecutionContext,
    UntargetedRequest,
)
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    serialize_mass_recalibration_result,
)
from xic_extractor.diagnostics.grouping_experiment_io import (
    write_grouping_inputs,
    write_grouping_result,
    write_json,
)
from xic_extractor.discovery.batch_contract import DiscoveryPeakUniverseReceipt
from xic_extractor.discovery.csv_reader import (
    DiscoveryBatchIndex,
    DiscoveryBatchInput,
    read_discovery_batch_index,
)
from xic_extractor.discovery.input_source import DiscoveryInputSource
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.output.alignment.batch_correspondence_table import (
    BATCH_CORRESPONDENCE_ARTIFACT_NAME,
    batch_correspondence_summary,
    write_batch_correspondence_table,
)
from xic_extractor.output.alignment.output_levels import (
    AlignmentOutputLevel,
    artifact_names_for_output_level,
)
from xic_extractor.output.alignment.pipeline_outputs import (
    AlignmentRunOutputs,
    alignment_metadata,
)
from xic_extractor.output.alignment.rt_anchor_table import write_rt_anchor_table
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.raw_mass_recalibration import PreparedRawMassRecalibration
from xic_extractor.raw_mass_recalibration_batch import prepare_batch_mass_recalibration
from xic_extractor.raw_source_binding import bind_expected_path_identity
from xic_extractor.untargeted_execution.alignment import (
    complete_untargeted_alignment,
    execute_prepared_alignment,
    prepare_untargeted_alignment_inputs,
)
from xic_extractor.untargeted_execution.batch_split import (
    latest_acquisition_reader,
    split_discovery_batch,
)
from xic_extractor.untargeted_execution.execution_result import (
    CompletedUntargetBatchWorkflowResult,
)
from xic_extractor.untargeted_execution.grouping_experiment import (
    PreparedGroupingExperiment,
)

GROUPING_SNAPSHOT_NAME = "grouping-inputs.json.gz"


class AlignmentProcessExecutionError(RuntimeError):
    """Raised when retained Untargeted execution fails before publication."""


def _admitted_batch(
    index: Path | DiscoveryBatchIndex, recorder: TimingRecorder
) -> DiscoveryBatchIndex:
    if isinstance(index, DiscoveryBatchIndex):
        return index
    with recorder.stage("alignment.read_batch_index"):
        return read_discovery_batch_index(index)


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
    batch = _admitted_batch(request.discovery_batch_index, recorder)
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
        discovery_batch_index=batch.path,
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
    batches = split_discovery_batch(batch, request.sample_metadata)
    completed_by_batch: dict[str, CompletedUntargetBatchWorkflowResult] = {}
    try:
        for batch_id, batch_input in batches:
            completed_by_batch[batch_id] = complete_untargeted_alignment(
                discovery_batch=batch_input,
                peaks_by_sample=(
                    peaks_by_sample
                    if len(batches) == 1
                    else DiscoveryInputSource(batch_input)
                ),
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
                    _batch_mass_recalibration(mass_recalibration_by_sample, batch_input)
                    if request.discovery_settings.mass_recalibration
                    is not MassRecalibration.NONE
                    else None
                ),
                project_child=untargeted_child_projection(outputs, request.publication),
            )
    except BaseException as exc:
        for completed in completed_by_batch.values():
            completed.close()
        if isinstance(exc, RuntimeError):
            raise AlignmentProcessExecutionError(str(exc)) from exc
        raise
    if context.progress_callback:
        context.progress_callback({"stage": "publish_matrix"})
    if len(completed_by_batch) == 1:
        (completed,) = completed_by_batch.values()
        return publish_untargeted_outputs(
            completed,
            output_dir=request.output_dir,
            outputs=outputs,
            policy=request.publication,
            metadata=metadata,
            timing_recorder=recorder,
            should_cancel=context.should_cancel,
        )
    try:
        correspondence, features = _correspond_batches(
            request,
            {
                batch_id: (
                    completed.initial_groups,
                    completed.sample_rt_frame.calibration_for_snapshot
                    if completed.sample_rt_frame is not None
                    else None,
                )
                for batch_id, completed in completed_by_batch.items()
            },
            dict(batches),
        )
    except BaseException:
        for completed in completed_by_batch.values():
            completed.close()
        raise
    return publish_batch_untargeted_outputs(
        completed_by_batch,
        correspondence,
        features,
        output_dir=request.output_dir,
        outputs=outputs,
        policy=request.publication,
        metadata=metadata,
        timing_recorder=recorder,
        should_cancel=context.should_cancel,
    )


def _batch_mass_recalibration(
    mass_recalibration_by_sample: dict[str, PreparedRawMassRecalibration] | None,
    batch: DiscoveryBatchInput,
) -> dict[str, PreparedRawMassRecalibration] | None:
    """One batch's prepared mass recalibrations; the whole map for one batch."""
    if not mass_recalibration_by_sample:
        return mass_recalibration_by_sample
    return {
        sample: prepared
        for sample, prepared in mass_recalibration_by_sample.items()
        if sample in batch.sample_order
    }


def _prepare_mass_recalibration(
    request: UntargetedRequest,
    batch: DiscoveryBatchInput,
    recorder: TimingRecorder,
    context: UntargetedExecutionContext,
) -> dict[str, PreparedRawMassRecalibration]:
    from xic_extractor.acquisition.raw_paths import existing_raw_paths

    policy = request.discovery_settings.mass_recalibration
    if policy is not MassRecalibration.NONE:
        # Discovery already computed each RAW's result; bind it to the path.
        return {
            stem: _bind_discovery_mass_recalibration(path, batch.peak_receipts[stem])
            for stem, path in existing_raw_paths(
                sample_order=batch.sample_order,
                raw_files=batch.raw_files,
                raw_dir=request.raw_dir,
            ).items()
        }
    if request.known_istd_reference is None and request.targets_csv is not None:
        # Only the ISTD reference needs Targeted calibration; Discovery has none.
        policy = request.peak_config.mass_recalibration
    if policy is MassRecalibration.NONE:
        return {}
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


def _bind_discovery_mass_recalibration(
    path: Path, receipt: DiscoveryPeakUniverseReceipt
) -> PreparedRawMassRecalibration:
    source = receipt.acquisition_source
    assert source.local_file_revision is not None
    binding = bind_expected_path_identity(path, source, source.local_file_revision)
    return PreparedRawMassRecalibration(
        AcquisitionSourceExpectation(source, binding.path_identity),
        receipt.mass_recalibration_result,
    )


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


def run_untargeted_grouping(
    request: UntargetedRequest,
    context: UntargetedExecutionContext | None = None,
    *,
    save_snapshot: bool = False,
) -> Path:
    """Write the formal initial groups; optionally retain compact replay inputs.

    Same inputs, ISTD reference, initial assessment, calibration and grouping
    as a full run; stops before Backfill and publication.
    """
    context = context or UntargetedExecutionContext()
    recorder = context.timing_recorder or TimingRecorder.disabled("alignment")
    batch = _admitted_batch(request.discovery_batch_index, recorder)
    mass_recalibration_by_sample = _prepare_mass_recalibration(
        request, batch, recorder, context
    )
    known_istd_reference = _resolve_known_istd_reference(
        request, batch, recorder, mass_recalibration_by_sample
    )
    snapshot = request.output_dir / GROUPING_SNAPSHOT_NAME
    if (request.output_dir / "groups.tsv").exists() or (
        save_snapshot and snapshot.exists()
    ):
        raise ValueError(f"grouping output already exists: {request.output_dir}")
    batches = split_discovery_batch(batch, request.sample_metadata)
    with recorder.stage("alignment.grouping") as stage:
        groupings = {
            batch_id: _group_batch(
                request,
                context,
                recorder,
                batch_input,
                mass_recalibration_by_sample,
                known_istd_reference,
            )
            for batch_id, batch_input in batches
        }
        stage.metrics["partition_count"] = sum(
            item.total_partition_count for item in groupings.values()
        )
        stage.metrics["group_count"] = sum(
            len(item.groups) for item in groupings.values()
        )
    provenance = {
        "discovery_batch_index": str(batch.path.resolve()),
        "partition_count": sum(
            item.total_partition_count for item in groupings.values()
        ),
        "initial_job_count": sum(item.initial_job_count for item in groupings.values()),
    }
    if len(groupings) == 1:
        (grouping,) = groupings.values()
        inputs, groups, calibration = (
            grouping.inputs,
            grouping.groups,
            grouping.rt_calibration,
        )
        correspondence_summary = None
    else:
        correspondence, features = _correspond_batches(
            request,
            {
                batch_id: (item.groups, item.rt_calibration)
                for batch_id, item in groupings.items()
            },
            dict(batches),
        )
        inputs = combine_grouping_inputs([item.inputs for item in groupings.values()])
        groups = tuple(group for item in groupings.values() for group in item.groups)
        calibration = compose_calibrations(
            {batch_id: item.rt_calibration for batch_id, item in groupings.items()},
            correspondence,
        )
        write_batch_correspondence_table(
            request.output_dir / "batch_correspondence.tsv", correspondence, features
        )
        correspondence_summary = batch_correspondence_summary(correspondence)
    with recorder.stage("alignment.write_grouping_results"):
        summary = write_grouping_result(request.output_dir, inputs, groups)
        if correspondence_summary is not None:
            summary["batch_correspondence"] = correspondence_summary
        write_json(
            request.output_dir / "summary.json", {**summary, "source": provenance}
        )
        write_rt_anchor_table(request.output_dir / "rt_anchors.tsv", calibration)
    if save_snapshot:
        with recorder.stage("alignment.write_grouping_snapshot"):
            write_grouping_inputs(snapshot, inputs, provenance=provenance)
    return request.output_dir


def _group_batch(
    request: UntargetedRequest,
    context: UntargetedExecutionContext,
    recorder: TimingRecorder,
    batch: DiscoveryBatchInput,
    mass_recalibration_by_sample: dict[str, PreparedRawMassRecalibration] | None,
    known_istd_reference: KnownIstdReference | None,
) -> PreparedGroupingExperiment:
    """Group one batch exactly as if it were the run's only input."""
    batch_recalibration = _batch_mass_recalibration(mass_recalibration_by_sample, batch)
    plan = prepare_untargeted_alignment_inputs(
        discovery_batch=batch,
        peaks_by_sample=DiscoveryInputSource(batch),
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
                for sample, prepared in batch_recalibration.items()
            }
            if batch_recalibration
            else None
        ),
    )
    return cast(
        PreparedGroupingExperiment,
        execute_prepared_alignment(
            plan,
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
                batch_recalibration
                if request.discovery_settings.mass_recalibration
                is not MassRecalibration.NONE
                else None
            ),
        ),
    )


def _correspond_batches(
    request: UntargetedRequest,
    grouped: Mapping[
        str,
        tuple[Iterable[CrossSamplePeakGroupHypothesis], BatchRtCalibration | None],
    ],
    batches: Mapping[str, DiscoveryBatchInput],
) -> tuple[BatchCorrespondence, tuple[BatchFeature, ...]]:
    """Pair each batch's Matrix groups with the reference batch's."""
    features = tuple(
        batch_feature(batch_id, group, calibration)
        for batch_id, (groups, calibration) in grouped.items()
        for group in groups
        if group.destination == "matrix"
    )
    sizes = {batch_id: len(batch.sample_order) for batch_id, batch in batches.items()}
    correspondence = correspond_batches(
        features,
        (
            istd
            for batch_id, (_groups, calibration) in grouped.items()
            for istd in batch_istds(batch_id, calibration)
        ),
        batch_sizes=sizes,
        reference_batch=choose_reference_batch(
            sizes,
            latest_acquisition_reader(
                batches, raw_dir=request.raw_dir, dll_dir=request.dll_dir
            ),
        ),
        precursor_tolerance_ppm=request.discovery_settings.precursor_mz_tolerance_ppm,
    )
    return correspondence, features


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
    BATCH_CORRESPONDENCE_ARTIFACT_NAME,
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
