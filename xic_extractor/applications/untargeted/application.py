from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from xic_extractor.alignment.backfill.peak_fact_provenance import (
    EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH,
)
from xic_extractor.alignment.owner_build_route_receipt import (
    _mint_retained_untargeted_cid_nl_route_receipt,
)
from xic_extractor.applications.untargeted.istd_reference import (
    extract_batch_known_istd_reference,
    read_known_istd_reference,
)
from xic_extractor.applications.untargeted.publication import publish_untargeted_outputs
from xic_extractor.applications.untargeted.request import (
    UntargetedExecutionContext,
    UntargetedRequest,
)
from xic_extractor.discovery.csv_reader import read_discovery_batch_index
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
from xic_extractor.untargeted_execution.alignment import complete_untargeted_alignment


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
            "owner_build_xic_backend": request.legacy.owner_build_xic_backend,
            "owner_backfill_xic_backend": "raw",
            "owner_backfill_window_strategy": "exact",
            "owner_backfill_superwindow_span_factor": 2,
            "output_level": request.publication.output_level,
            "backfill_scope": "full-audit",
            "requested_audit_evidence_mode": request.legacy.audit_evidence_mode,
            "require_discovery_owner_closure": True,
        },
    )
    with recorder.stage("alignment.read_batch_index"):
        batch = read_discovery_batch_index(request.discovery_batch_index)
    with recorder.stage("alignment.read_candidates") as stage:
        candidates_by_sample = DiscoveryInputSource(batch)
        stage.metrics["candidate_count"] = len(candidates_by_sample.locations)
        stage.metrics["feature_candidate_count"] = len(
            candidates_by_sample.feature_candidate_ids()
        )
    owner_build_route_receipt = _mint_retained_untargeted_cid_nl_route_receipt(
        configured_backend=request.legacy.owner_build_xic_backend,
    )
    outputs = request.publication.output_paths(request.output_dir)
    if not request.alignment_config.backfill_enabled:
        outputs = replace(
            outputs,
            run_metadata_json=request.output_dir / "alignment_run_metadata.json",
        )
    _reject_alignment_output_collisions(output_dir=request.output_dir, outputs=outputs)
    if request.known_istd_reference is not None:
        resolved_known_istd_reference = request.known_istd_reference
    elif request.targets_csv is not None:
        from xic_extractor.acquisition.raw_paths import existing_raw_paths
        from xic_extractor.configuration.targets import read_istd_targets

        selection = read_istd_targets(request.targets_csv).for_method(
            request.discovery_settings.neutral_loss_profiles
        )
        resolved_known_istd_reference = None
        if selection.targets:
            raw_paths_by_sample = existing_raw_paths(
                sample_order=batch.sample_order,
                raw_files=batch.raw_files,
                raw_dir=request.raw_dir,
            )
            resolved_known_istd_reference = extract_batch_known_istd_reference(
                targets=selection.targets,
                raw_paths_by_sample=raw_paths_by_sample,
                dll_dir=request.dll_dir,
                peak_config=request.peak_config,
                source_identifier=str(request.targets_csv.resolve()),
                source_sha256=hashlib.sha256(
                    request.targets_csv.read_bytes()
                ).hexdigest(),
            )
    elif request.known_istd_reference_workbook is not None:
        resolved_known_istd_reference = read_known_istd_reference(
            request.known_istd_reference_workbook
        )
    else:
        resolved_known_istd_reference = None

    metadata = alignment_metadata(
        discovery_batch_index=request.discovery_batch_index,
        raw_dir=request.raw_dir,
        dll_dir=request.dll_dir,
        owner_build_xic_backend=request.legacy.owner_build_xic_backend,
        owner_backfill_xic_backend="raw",
        owner_backfill_window_strategy="exact",
        owner_backfill_superwindow_span_factor=2,
        output_level=request.publication.output_level,
        peak_config=request.peak_config,
        backfill_scope="full-audit",
        output_scope="full-audit",
        audit_evidence_mode="none",
        requested_audit_evidence_mode=request.legacy.audit_evidence_mode,
        heavy_audit_enabled=False,
        audit_evidence_mode_reason="retained_untargeted_cid_nl_owner",
        signal_processing_profile=signal_processing_profile,
        owner_backfill_peak_fact_provenance_row_count=len(
            EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH.rows
        ),
    )
    if not request.alignment_config.backfill_enabled:
        metadata["backfill_execution"] = "not_run"
    metadata.update(
        {
            "alignment_route": "untargeted_cid_nl_finalized",
            "effective_owner_build_route": (
                owner_build_route_receipt.effective_owner_build_route
            ),
            "effective_owner_build_xic_backend": "none",
            "owner_build_xic_backend_role": "configured_legacy_option",
            "matrix_value_policy": "owner_finalized_cell_decision",
            "matrix_coordinate_policy": "cross_sample_owner_arithmetic_mean",
        }
    )
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
            candidates_by_sample=candidates_by_sample,
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
            retain_partition_memory=False,
            retain_acquisition_cache=request.execution.retain_acquisition_cache,
            acquisition_cache_dir=request.execution.acquisition_cache_dir,
            max_partition_jobs=request.execution.max_partition_jobs,
        )
    except RuntimeError as exc:
        raise AlignmentProcessExecutionError(str(exc)) from exc
    return publish_untargeted_outputs(
        completed,
        output_dir=request.output_dir,
        outputs=outputs,
        policy=request.publication,
        metadata=metadata,
        timing_recorder=recorder,
        should_cancel=context.should_cancel,
        owner_build_route_receipt=owner_build_route_receipt,
    )


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
    "cid_nl_explanation.sqlite",
    "alignment_matrix_status.tsv",
    "alignment_cell_integration_audit.tsv",
    "alignment_owner_backfill_seed_audit.tsv",
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
        "validation",
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
