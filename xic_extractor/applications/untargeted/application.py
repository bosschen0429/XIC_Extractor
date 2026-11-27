from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import cast

from xic_extractor.alignment.batch_correspondence import (
    BatchFeature,
    GroupedBatch,
    compose_calibrations,
    pair_grouped_batches,
)
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.grouping_experiment import (
    GroupingInputs,
    combine_grouping_inputs,
)
from xic_extractor.applications.untargeted.istd_reference import (
    known_istd_reference_from_discovery,
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
from xic_extractor.diagnostics.backfill_replay import BackfillCheckpoint
from xic_extractor.diagnostics.batch_pairing import save_pairing_input
from xic_extractor.diagnostics.grouping_experiment_io import (
    write_grouping_result,
    write_json,
)
from xic_extractor.diagnostics.stage_snapshot import write_grouping_inputs
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
    alignment_artifact_collisions,
    alignment_metadata,
)
from xic_extractor.output.alignment.rt_anchor_table import write_rt_anchor_table
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.raw_mass_recalibration import PreparedRawMassRecalibration
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
            "raw_workers": request.execution.profile.alignment_workers,
            "raw_worker_backend": request.execution.profile.backend,
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
    batches = split_discovery_batch(batch, request.sample_metadata)
    if request.replay.pairing and any(not batch_id for batch_id, _ in batches):
        raise ValueError("Saving pairing inputs requires a batch_id in sample metadata")
    if request.replay.backfill and not request.alignment_config.backfill_enabled:
        raise ValueError("Saving Backfill inputs requires Backfill to be enabled")
    if context.progress_callback:
        context.progress_callback({"stage": "mass_recalibration"})
    mass_recalibration_by_sample = _prepare_mass_recalibration(request, batch)
    if context.progress_callback:
        context.progress_callback({"stage": "known_istd_reference"})
    resolved_known_istd_reference = _resolve_known_istd_reference(
        request, lambda: peaks_by_sample, recorder
    )

    metadata = alignment_metadata(
        discovery_batch_index=batch.path,
        raw_dir=request.raw_dir,
        dll_dir=request.dll_dir,
        output_level=request.publication.output_level,
        signal_processing_profile=signal_processing_profile,
    )
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
    completed_by_batch: dict[str, CompletedUntargetBatchWorkflowResult] = {}
    groupings: list[PreparedGroupingExperiment] = []
    project_child = untargeted_child_projection(outputs, request.publication)
    try:
        with _backfill_capture(request.output_dir, request.replay.backfill) as capture:
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
                    alignment_config=request.alignment_config,
                    sample_metadata=request.sample_metadata,
                    known_istd_reference=resolved_known_istd_reference,
                    timing_recorder=recorder,
                    signal_processing_profile=signal_processing_profile,
                    execution=request.execution.profile,
                    should_cancel=context.should_cancel,
                    progress_callback=context.progress_callback,
                    max_partition_jobs=request.execution.max_partition_jobs,
                    mass_recalibration_by_sample=(
                        _batch_mass_recalibration(
                            mass_recalibration_by_sample, batch_input
                        )
                        if request.discovery_settings.mass_recalibration
                        is not MassRecalibration.NONE
                        else None
                    ),
                    project_child=project_child,
                    capture_grouping=groupings.append
                    if request.replay.grouping
                    else None,
                    capture_backfill=capture.append if capture is not None else None,
                )
                if capture is not None:
                    capture.record_samples(
                        completed_by_batch[
                            batch_id
                        ].application_result.canonical_samples
                    )
    except BaseException as exc:
        for completed in completed_by_batch.values():
            completed.close()
        if isinstance(exc, RuntimeError):
            raise AlignmentProcessExecutionError(str(exc)) from exc
        raise
    correspondence = None
    features: tuple[BatchFeature, ...] = ()
    try:
        if groupings:
            _write_grouping_snapshot(
                request.output_dir / GROUPING_SNAPSHOT_NAME,
                combine_grouping_inputs([item.inputs for item in groupings])
                if len(groupings) > 1
                else groupings[0].inputs,
                provenance=_grouping_provenance(batch.path, groupings),
                recorder=recorder,
            )
        if len(batches) > 1 or request.replay.pairing:
            grouped_batches = []
            for batch_id, batch_input in batches:
                completed = completed_by_batch[batch_id]
                frame = completed.sample_rt_frame
                grouped_batches.append(
                    GroupedBatch.from_groups(
                        batch_id,
                        len(batch_input.sample_order),
                        completed.initial_groups,
                        frame.calibration_for_snapshot if frame is not None else None,
                    )
                )
            latest = cache(
                latest_acquisition_reader(
                    dict(batches), raw_dir=request.raw_dir, dll_dir=request.dll_dir
                )
            )
            ppm = request.discovery_settings.precursor_mz_tolerance_ppm
            if len(batches) > 1:
                correspondence, features = pair_grouped_batches(
                    grouped_batches,
                    latest_acquisition=latest,
                    precursor_tolerance_ppm=ppm,
                )
            if request.replay.pairing:
                _save_pairing_inputs(
                    request.output_dir / "pairing-inputs", grouped_batches, latest, ppm
                )
        if context.progress_callback:
            context.progress_callback({"stage": "publish_matrix"})
    except BaseException:
        for completed in completed_by_batch.values():
            completed.close()
        raise
    if correspondence is None:
        (completed,) = completed_by_batch.values()
        return publish_untargeted_outputs(
            completed,
            output_dir=request.output_dir,
            outputs=outputs,
            policy=request.publication,
            metadata=metadata,
            timing_recorder=recorder,
            should_cancel=context.should_cancel,
            save_matrix_inputs=request.replay.matrix,
        )
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
        save_matrix_inputs=request.replay.matrix,
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
    request: UntargetedRequest, batch: DiscoveryBatchInput
) -> dict[str, PreparedRawMassRecalibration]:
    from xic_extractor.acquisition.raw_paths import existing_raw_paths

    if request.discovery_settings.mass_recalibration is MassRecalibration.NONE:
        return {}
    return {
        stem: _bind_discovery_mass_recalibration(path, batch.peak_receipts[stem])
        for stem, path in existing_raw_paths(
            sample_order=batch.sample_order,
            raw_files=batch.raw_files,
            raw_dir=request.raw_dir,
        ).items()
    }


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
    source: Callable[[], DiscoveryInputSource],
    recorder: TimingRecorder,
) -> KnownIstdReference | None:
    """``source`` indexes the whole batch's Discovery peaks only when an ISTD
    list needs them."""
    if request.known_istd_reference is not None:
        return request.known_istd_reference
    if request.targets_csv is not None:
        from xic_extractor.configuration.targets import read_istd_targets

        selection = read_istd_targets(request.targets_csv).for_method(
            request.discovery_settings.neutral_loss_profiles
        )
        if not selection.targets:
            return None
        with recorder.stage("alignment.known_istd_reference") as stage:
            stage.metrics["istd_target_count"] = len(selection.targets)
            return known_istd_reference_from_discovery(
                selection.targets,
                source(),
                neutral_loss_by_tag=request.discovery_settings.neutral_loss_by_tag,
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
    save_backfill_inputs: bool = False,
    save_pairing_inputs: bool = False,
) -> Path:
    """Write the formal initial groups; optionally retain compact replay inputs.

    Same inputs, ISTD reference, initial assessment, calibration and grouping
    as a full run; stops before Backfill and publication.
    """
    context = context or UntargetedExecutionContext()
    recorder = context.timing_recorder or TimingRecorder.disabled("alignment")
    batch = _admitted_batch(request.discovery_batch_index, recorder)
    directory = request.output_dir / "pairing-inputs"
    snapshot = request.output_dir / GROUPING_SNAPSHOT_NAME
    if (
        (request.output_dir / "groups.tsv").exists()
        or (save_snapshot and snapshot.exists())
        or (save_pairing_inputs and directory.exists())
    ):
        raise ValueError(f"grouping output already exists: {request.output_dir}")
    batches = split_discovery_batch(batch, request.sample_metadata)
    if save_pairing_inputs and any(not batch_id for batch_id, _ in batches):
        raise ValueError("Saving pairing inputs requires a batch_id in sample metadata")
    mass_recalibration_by_sample = _prepare_mass_recalibration(request, batch)
    source = DiscoveryInputSource(batch) if len(batches) == 1 else None
    known_istd_reference = _resolve_known_istd_reference(
        request,
        lambda: DiscoveryInputSource(batch) if source is None else source,
        recorder,
    )
    with (
        _backfill_capture(request.output_dir, save_backfill_inputs) as capture,
        recorder.stage("alignment.grouping") as stage,
    ):
        groupings = {
            batch_id: _group_batch(
                request,
                context,
                recorder,
                batch_input,
                DiscoveryInputSource(batch_input) if source is None else source,
                mass_recalibration_by_sample,
                known_istd_reference,
                capture,
            )
            for batch_id, batch_input in batches
        }
        stage.metrics["partition_count"] = sum(
            item.total_partition_count for item in groupings.values()
        )
        stage.metrics["group_count"] = sum(
            len(item.groups) for item in groupings.values()
        )
    provenance = _grouping_provenance(batch.path, tuple(groupings.values()))
    pairing_batches = (
        tuple(
            GroupedBatch.from_groups(
                batch_id,
                len(batch_input.sample_order),
                groupings[batch_id].groups,
                groupings[batch_id].rt_calibration,
            )
            for batch_id, batch_input in batches
        )
        if save_pairing_inputs or len(groupings) > 1
        else ()
    )
    latest = cache(
        latest_acquisition_reader(
            dict(batches), raw_dir=request.raw_dir, dll_dir=request.dll_dir
        )
    )
    ppm = request.discovery_settings.precursor_mz_tolerance_ppm
    if save_pairing_inputs:
        _save_pairing_inputs(directory, pairing_batches, latest, ppm)
    if len(groupings) == 1:
        (grouping,) = groupings.values()
        inputs, groups, calibration = (
            grouping.inputs,
            grouping.groups,
            grouping.rt_calibration,
        )
        correspondence_summary = None
    else:
        correspondence, features = pair_grouped_batches(
            pairing_batches,
            latest_acquisition=latest,
            precursor_tolerance_ppm=ppm,
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
        _write_grouping_snapshot(
            snapshot, inputs, provenance=provenance, recorder=recorder
        )
    return request.output_dir


def _backfill_capture(
    output_dir: Path, enabled: bool
) -> BackfillCheckpoint | nullcontext[None]:
    return (
        BackfillCheckpoint(output_dir / "backfill-inputs") if enabled else nullcontext()
    )


def _grouping_provenance(
    index: Path, groupings: Sequence[PreparedGroupingExperiment]
) -> dict[str, object]:
    return {
        "discovery_batch_index": str(index.resolve()),
        "partition_count": sum(item.total_partition_count for item in groupings),
        "initial_job_count": sum(item.initial_job_count for item in groupings),
    }


def _write_grouping_snapshot(
    path: Path,
    inputs: GroupingInputs,
    *,
    provenance: dict[str, object],
    recorder: TimingRecorder,
) -> None:
    with recorder.stage("alignment.write_grouping_snapshot"):
        write_grouping_inputs(path, inputs, provenance=provenance)


def _save_pairing_inputs(
    directory: Path,
    batches: Sequence[GroupedBatch],
    latest: Callable[[str], datetime],
    ppm: float,
) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    for index, batch in enumerate(batches, start=1):
        save_pairing_input(
            directory / f"batch-{index}.pickle.gz",
            batch,
            latest_acquisition=latest(batch.batch_id),
            precursor_tolerance_ppm=ppm,
        )


def _group_batch(
    request: UntargetedRequest,
    context: UntargetedExecutionContext,
    recorder: TimingRecorder,
    batch: DiscoveryBatchInput,
    peaks_by_sample: DiscoveryInputSource,
    mass_recalibration_by_sample: dict[str, PreparedRawMassRecalibration] | None,
    known_istd_reference: KnownIstdReference | None,
    capture: BackfillCheckpoint | None,
) -> PreparedGroupingExperiment:
    """Group one batch exactly as if it were the run's only input."""
    batch_recalibration = _batch_mass_recalibration(mass_recalibration_by_sample, batch)
    plan = prepare_untargeted_alignment_inputs(
        discovery_batch=batch,
        peaks_by_sample=peaks_by_sample,
        raw_dir=request.raw_dir,
        dll_dir=request.dll_dir,
        output_dir=request.output_dir,
        settings=request.discovery_settings,
        sample_metadata=request.sample_metadata,
        timing_recorder=recorder,
        signal_processing_profile=request.resolve_processing_profile(),
        execution=request.execution.profile,
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
    if capture is not None:
        capture.record_samples(plan.context.samples)
    return cast(
        PreparedGroupingExperiment,
        execute_prepared_alignment(
            plan,
            output_dir=request.output_dir,
            alignment_config=request.alignment_config,
            known_istd_reference=known_istd_reference,
            timing_recorder=recorder,
            execution=request.execution.profile,
            max_partition_jobs=request.execution.max_partition_jobs,
            should_cancel=context.should_cancel,
            grouping_only=True,
            capture_backfill=capture.append if capture is not None else None,
            mass_recalibration_by_sample=(
                batch_recalibration
                if request.discovery_settings.mass_recalibration
                is not MassRecalibration.NONE
                else None
            ),
        ),
    )


_RETIRED_ALIGNMENT_ARTIFACT_NAMES = (
    "alignment_review.tsv",
    "alignment_results.xlsx",
    "review_report.html",
    "event_to_ms1_owner.tsv",
    "ambiguous_ms1_owners.tsv",
)

_CONDITIONAL_ALIGNMENT_ARTIFACT_NAMES = (
    "alignment_explanation.sqlite",
    "alignment_cell_integration_audit.tsv",
    "alignment_owner_backfill_candidate_audit.tsv",
    "skipped_evidence_ledger.tsv",
    "alignment_run_metadata.json",
    BATCH_CORRESPONDENCE_ARTIFACT_NAME,
    GROUPING_SNAPSHOT_NAME,
    "backfill-inputs",
    "pairing-inputs",
    "matrix-cells.pickle.gz",
    "matrix-join.pickle.gz",
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
    collisions = alignment_artifact_collisions(
        (output_dir / name for name in sorted(owned_names))
    )
    if collisions:
        names = ", ".join((path.name for path in sorted(collisions)))
        raise FileExistsError(
            "Alignment output directory contains owned artifact collisions: " + names
        )
