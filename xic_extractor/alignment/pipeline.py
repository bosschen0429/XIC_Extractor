from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from xic_extractor.alignment.artifact_write_receipts import (
    AlignmentArtifactFingerprintBatch,
)
from xic_extractor.alignment.backfill_scope import BackfillScope
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.csv_io import (
    DiscoveryBatchInput,
    read_discovery_batch_index,
    read_owner_bound_discovery_candidates_csv,
)
from xic_extractor.alignment.drift_evidence import (
    KnownIstdReference,
    mint_known_istd_reference_from_measurements,
    read_known_istd_reference,
)
from xic_extractor.alignment.ms1_index_source import (
    OwnerBackfillXicBackend,
    OwnerBuildXicBackend,
)
from xic_extractor.alignment.output_levels import (
    AlignmentOutputLevel,
    artifact_names_for_output_level,
)
from xic_extractor.alignment.owner_backfill import OwnerBackfillWindowStrategy
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    EMPTY_OWNER_BACKFILL_PEAK_FACT_PROVENANCE_BATCH,
)
from xic_extractor.alignment.owner_build_route_receipt import (
    OwnerBuildRouteReceipt,
    _mint_retained_untargeted_cid_nl_route_receipt,
    _validate_owner_build_xic_backend,
)
from xic_extractor.alignment.ownership import OwnershipBuildResult
from xic_extractor.alignment.pipeline_outputs import (
    AlignmentRunOutputs,
    alignment_metadata,
    output_paths,
    write_outputs_atomic,
)
from xic_extractor.alignment.process_execution import AlignmentProcessExecutionError
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.configuration.models import Target
from xic_extractor.diagnostics.cid_nl_explanation_projection import (
    write_cid_nl_explanation,
)
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.discovery.batch_contract import DiscoveryCandidateUniverseReceipt
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings
from xic_extractor.extraction.untargeted_cid_nl_alignment import (
    complete_untargeted_cid_nl_alignment,
)
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    is_pure_single_provider_cid_nl,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    DEFAULT_SIGNAL_PROCESSING_PROFILE_ID,
    SignalProcessingProfile,
    resolve_signal_processing_profile,
)
from xic_extractor.sample_metadata import SampleMetadata


def extract_batch_known_istd_reference(
    targets: Sequence[Target],
    raw_paths_by_sample: Mapping[str, Path],
    dll_dir: Path,
    peak_config: ExtractionConfig,
    *,
    source_identifier: str = "<in-memory>",
    source_sha256: str = "",
) -> KnownIstdReference:
    """Extract ISTD peaks across raw files and construct in-memory reference."""
    from xic_extractor.raw_reader import open_raw
    from xic_extractor.signal_processing import find_peak_and_area
    from xic_extractor.xic_models import XICRequest

    istd_targets = tuple(t for t in targets if t.is_istd)
    if not istd_targets:
        raise ValueError("No ISTD targets (is_istd=True) found in targets")

    requests = tuple(
        XICRequest(
            mz=target.mz,
            rt_min=target.rt_min,
            rt_max=target.rt_max,
            ppm_tol=target.ppm_tol,
        )
        for target in istd_targets
    )

    coordinates: dict[tuple[str, str], float] = {}
    for sample_stem, raw_path in sorted(raw_paths_by_sample.items()):
        with open_raw(raw_path, dll_dir=dll_dir) as raw:
            traces = raw.extract_xic_many(requests)
            for target, trace in zip(istd_targets, traces, strict=True):
                peak_result = find_peak_and_area(
                    trace.rt,
                    trace.intensity,
                    peak_config,
                    evidence_role="ISTD",
                )
                if peak_result.peak is not None and peak_result.peak.rt > 0:
                    peak_rt = float(peak_result.peak.rt)
                    coordinates[(target.label, sample_stem)] = peak_rt

    return mint_known_istd_reference_from_measurements(
        istd_targets,
        coordinates,
        source_identifier=source_identifier,
        source_sha256=source_sha256,
    )


def run_alignment(
    *,
    discovery_batch_index: Path,
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    alignment_config: AlignmentConfig,
    peak_config: ExtractionConfig,
    discovery_settings: DiscoverySettings | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
    output_level: AlignmentOutputLevel = "production",
    emit_alignment_cells: bool = False,
    emit_alignment_status_matrix: bool = False,
    emit_alignment_integration_audit: bool = False,
    emit_alignment_backfill_seed_audit: bool = False,
    emit_alignment_backfill_candidate_audit: bool = False,
    raw_workers: int = 1,
    raw_worker_backend: str = "thread",
    raw_xic_batch_size: int = 1,
    owner_build_xic_backend: OwnerBuildXicBackend = "raw",
    owner_backfill_xic_backend: OwnerBackfillXicBackend = "raw",
    owner_backfill_window_strategy: OwnerBackfillWindowStrategy = "exact",
    owner_backfill_superwindow_span_factor: int = 2,
    sample_column_injection_order: Path | None = None,
    sample_metadata: tuple[SampleMetadata, ...] | None = None,
    known_istd_reference_workbook: Path | None = None,
    targets_csv: Path | None = None,
    known_istd_reference: KnownIstdReference | None = None,
    timing_recorder: TimingRecorder | None = None,
    capture_artifact_fingerprints: bool = False,
    backfill_scope: BackfillScope = "full-audit",
    audit_evidence_mode: str = "auto",
    selected_family_ids: frozenset[str] = frozenset(),
    selected_family_source: str = "",
    require_discovery_owner_closure: bool = False,
    emit_provisional_matrix: bool = False,
    should_cancel: Callable[[], bool] | None = None,
    max_partition_jobs: int = 100_000,
) -> AlignmentRunOutputs:
    """Publish finalized single-provider CID-NL Alignment owner results."""
    if (
        discovery_settings is not None
        and type(discovery_settings) is not DiscoverySettings
    ):
        raise TypeError("discovery_settings must be an exact DiscoverySettings")
    if discovery_settings is None or not is_pure_single_provider_cid_nl(
        discovery_settings
    ):
        raise ValueError(
            "run_alignment supports only pure single-provider CID-NL "
            "discovery_settings; "
            "use scripts.run_alignment --preset dna_dr with an "
            "owner-closed discovery batch index"
        )
    owner_build_xic_backend = _validate_owner_build_xic_backend(owner_build_xic_backend)
    resolved_profile = resolve_signal_processing_profile(
        signal_processing_method=peak_config.signal_processing_method,
        signal_processing_window_points=peak_config.signal_processing_window_points,
    )
    if signal_processing_profile is not None:
        if not isinstance(signal_processing_profile, SignalProcessingProfile):
            raise TypeError(
                "signal_processing_profile must be a SignalProcessingProfile"
            )
        if signal_processing_profile != resolved_profile:
            raise ValueError(
                "signal_processing_profile conflicts with peak_config canonical fields"
            )
    signal_processing_profile = resolved_profile
    if emit_provisional_matrix and not require_discovery_owner_closure:
        raise ValueError("provisional matrix emission requires Discovery owner closure")
    recorder = timing_recorder or TimingRecorder.disabled("alignment")
    recorder.record(
        "alignment.run_config",
        elapsed_sec=0.0,
        metrics={
            "raw_workers": raw_workers,
            "raw_worker_backend": raw_worker_backend,
            "raw_xic_batch_size": raw_xic_batch_size,
            "owner_build_xic_backend": owner_build_xic_backend,
            "owner_backfill_xic_backend": owner_backfill_xic_backend,
            "owner_backfill_window_strategy": owner_backfill_window_strategy,
            "owner_backfill_superwindow_span_factor": (
                owner_backfill_superwindow_span_factor
            ),
            "output_level": output_level,
            "backfill_scope": backfill_scope,
            "requested_audit_evidence_mode": audit_evidence_mode,
            "require_discovery_owner_closure": True,
            "emit_provisional_matrix": emit_provisional_matrix,
        },
    )
    with recorder.stage("alignment.read_batch_index"):
        batch = read_discovery_batch_index(
            discovery_batch_index, require_owner_closure=True
        )
    with recorder.stage("alignment.read_candidates") as stage:
        candidates_by_sample = {
            sample_stem: read_owner_bound_discovery_candidates_csv(
                batch.candidate_csvs[sample_stem],
                receipt=_required_candidate_receipt(batch, sample_stem),
                require_successor_columns=True,
            )
            for sample_stem in batch.sample_order
        }
        stage.metrics["candidate_count"] = sum(map(len, candidates_by_sample.values()))
    owner_build_route_receipt = _mint_retained_untargeted_cid_nl_route_receipt(
        configured_backend=owner_build_xic_backend,
    )
    outputs = _output_paths(
        output_dir,
        output_level=output_level,
        emit_alignment_cells=emit_alignment_cells,
        emit_alignment_status_matrix=emit_alignment_status_matrix,
        emit_alignment_integration_audit=emit_alignment_integration_audit,
        emit_alignment_backfill_seed_audit=emit_alignment_backfill_seed_audit,
        emit_alignment_backfill_candidate_audit=emit_alignment_backfill_candidate_audit,
        emit_provisional_matrix=emit_provisional_matrix,
    )
    _reject_alignment_output_collisions(output_dir=output_dir, outputs=outputs)
    if known_istd_reference is not None:
        resolved_known_istd_reference = known_istd_reference
    elif targets_csv is not None:
        from xic_extractor.alignment.raw_sources import existing_raw_paths
        from xic_extractor.configuration.targets import read_istd_targets

        targets = read_istd_targets(targets_csv).targets
        raw_paths_by_sample = existing_raw_paths(
            sample_order=batch.sample_order,
            raw_files=batch.raw_files,
            raw_dir=raw_dir,
        )
        resolved_known_istd_reference = extract_batch_known_istd_reference(
            targets=targets,
            raw_paths_by_sample=raw_paths_by_sample,
            dll_dir=dll_dir,
            peak_config=peak_config,
            source_identifier=str(targets_csv.resolve()),
            source_sha256=hashlib.sha256(targets_csv.read_bytes()).hexdigest(),
        )
    elif known_istd_reference_workbook is not None:
        resolved_known_istd_reference = read_known_istd_reference(
            known_istd_reference_workbook
        )
    else:
        resolved_known_istd_reference = None

    return _run_untargeted_cid_nl_alignment(
        discovery_batch_index=discovery_batch_index,
        discovery_batch=batch,
        candidates_by_sample=candidates_by_sample,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        output_dir=output_dir,
        settings=discovery_settings,
        peak_config=peak_config,
        alignment_config=alignment_config,
        outputs=outputs,
        output_level=output_level,
        owner_build_route_receipt=owner_build_route_receipt,
        timing_recorder=recorder,
        capture_artifact_fingerprints=capture_artifact_fingerprints,
        raw_workers=raw_workers,
        raw_worker_backend=raw_worker_backend,
        raw_xic_batch_size=raw_xic_batch_size,
        owner_build_xic_backend=owner_build_xic_backend,
        owner_backfill_xic_backend=owner_backfill_xic_backend,
        owner_backfill_window_strategy=owner_backfill_window_strategy,
        owner_backfill_superwindow_span_factor=owner_backfill_superwindow_span_factor,
        sample_column_injection_order=sample_column_injection_order,
        sample_metadata=sample_metadata,
        known_istd_reference=resolved_known_istd_reference,
        backfill_scope=backfill_scope,
        audit_evidence_mode=audit_evidence_mode,
        selected_family_ids=selected_family_ids,
        selected_family_source=selected_family_source,
        signal_processing_profile=signal_processing_profile,
        should_cancel=should_cancel,
        max_partition_jobs=max_partition_jobs,
    )


def _validate_untargeted_cid_nl_alignment_options(
    *,
    raw_workers: int = 1,
    raw_worker_backend: str = "thread",
    raw_xic_batch_size: int = 1,
    owner_backfill_xic_backend: OwnerBackfillXicBackend = "raw",
    owner_backfill_window_strategy: OwnerBackfillWindowStrategy = "exact",
    owner_backfill_superwindow_span_factor: int = 2,
    sample_column_injection_order: Path | None = None,
    backfill_scope: BackfillScope = "full-audit",
    audit_evidence_mode: str = "auto",
    selected_family_ids: frozenset[str] = frozenset(),
    selected_family_source: str = "",
    signal_processing_profile: SignalProcessingProfile = (
        DEFAULT_SIGNAL_PROCESSING_PROFILE
    ),
) -> None:
    """Reject legacy Alignment options unsupported by the retained CID-NL route."""
    unsupported: list[str] = []
    if type(raw_workers) is not int or raw_workers < 1:
        raise ValueError("raw_workers must be a positive integer")
    if raw_worker_backend not in {"process", "thread"}:
        raise ValueError("raw_worker_backend must be 'process' or 'thread'")
    if raw_xic_batch_size != 1:
        unsupported.append("raw_xic_batch_size")
    if owner_backfill_xic_backend != "raw":
        unsupported.append("owner_backfill_xic_backend")
    if owner_backfill_window_strategy != "exact":
        unsupported.append("owner_backfill_window_strategy")
    if owner_backfill_superwindow_span_factor != 2:
        unsupported.append("owner_backfill_superwindow_span_factor")
    if sample_column_injection_order is not None:
        unsupported.append("sample_column_injection_order")
    if backfill_scope != "full-audit":
        unsupported.append("backfill_scope")
    if audit_evidence_mode not in {"auto", "none"}:
        unsupported.append("audit_evidence_mode")
    if selected_family_ids:
        unsupported.append("selected_family_ids")
    if selected_family_source.strip():
        unsupported.append("selected_family_source")
    if signal_processing_profile.profile_id != DEFAULT_SIGNAL_PROCESSING_PROFILE_ID:
        unsupported.append("signal_processing_profile")
    if unsupported:
        raise ValueError(
            "pure CID-NL retained route does not support legacy Alignment options: "
            + ", ".join(unsupported)
        )


def _run_untargeted_cid_nl_alignment(
    *,
    discovery_batch_index: Path,
    discovery_batch: DiscoveryBatchInput,
    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]],
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    alignment_config: AlignmentConfig,
    outputs: AlignmentRunOutputs,
    output_level: AlignmentOutputLevel,
    owner_build_route_receipt: OwnerBuildRouteReceipt,
    timing_recorder: TimingRecorder,
    capture_artifact_fingerprints: bool,
    raw_workers: int,
    raw_worker_backend: str = "thread",
    raw_xic_batch_size: int,
    owner_build_xic_backend: OwnerBuildXicBackend,
    owner_backfill_xic_backend: OwnerBackfillXicBackend,
    owner_backfill_window_strategy: OwnerBackfillWindowStrategy,
    owner_backfill_superwindow_span_factor: int,
    sample_column_injection_order: Path | None,
    sample_metadata: tuple[SampleMetadata, ...] | None,
    known_istd_reference: KnownIstdReference | None,
    backfill_scope: BackfillScope,
    audit_evidence_mode: str,
    selected_family_ids: frozenset[str],
    selected_family_source: str,
    signal_processing_profile: SignalProcessingProfile,
    should_cancel: Callable[[], bool] | None,
    max_partition_jobs: int = 100_000,
) -> AlignmentRunOutputs:
    """Run the retained CID-NL owner and project its completed result once."""
    _validate_untargeted_cid_nl_alignment_options(
        raw_workers=raw_workers,
        raw_worker_backend=raw_worker_backend,
        raw_xic_batch_size=raw_xic_batch_size,
        owner_backfill_xic_backend=owner_backfill_xic_backend,
        owner_backfill_window_strategy=owner_backfill_window_strategy,
        owner_backfill_superwindow_span_factor=owner_backfill_superwindow_span_factor,
        sample_column_injection_order=sample_column_injection_order,
        backfill_scope=backfill_scope,
        audit_evidence_mode=audit_evidence_mode,
        selected_family_ids=selected_family_ids,
        selected_family_source=selected_family_source,
        signal_processing_profile=signal_processing_profile,
    )
    if should_cancel is not None and should_cancel():
        raise InterruptedError("Alignment cancelled before retained CID-NL execution")
    try:
        completed = complete_untargeted_cid_nl_alignment(
            discovery_batch=discovery_batch,
            candidates_by_sample=candidates_by_sample,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            output_dir=output_dir,
            settings=settings,
            peak_config=peak_config,
            alignment_config=alignment_config,
            sample_metadata=sample_metadata,
            known_istd_reference=known_istd_reference,
            timing_recorder=timing_recorder,
            signal_processing_profile=signal_processing_profile,
            raw_workers=raw_workers,
            raw_worker_backend=raw_worker_backend,
            should_cancel=should_cancel,
            retain_partition_memory=True,
            max_partition_jobs=max_partition_jobs,
        )
    except RuntimeError as exc:
        raise AlignmentProcessExecutionError(str(exc)) from exc
    try:
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Alignment cancelled before output publication")
        writer_inputs = project_completed_untarget_batch_writer_inputs(completed)
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Alignment cancelled before output publication")
        metadata = _metadata(
            discovery_batch_index=discovery_batch_index,
            raw_dir=raw_dir,
            dll_dir=dll_dir,
            owner_build_xic_backend=owner_build_xic_backend,
            owner_backfill_xic_backend=owner_backfill_xic_backend,
            owner_backfill_window_strategy=owner_backfill_window_strategy,
            owner_backfill_superwindow_span_factor=owner_backfill_superwindow_span_factor,
            output_level=output_level,
            peak_config=peak_config,
            backfill_scope="full-audit",
            output_scope="full-audit",
            audit_evidence_mode="none",
            requested_audit_evidence_mode=audit_evidence_mode,
            heavy_audit_enabled=False,
            audit_evidence_mode_reason="retained_untargeted_cid_nl_owner",
            require_discovery_owner_closure=True,
            signal_processing_profile=signal_processing_profile,
            owner_backfill_peak_fact_provenance_row_count=len(
                EMPTY_OWNER_BACKFILL_PEAK_FACT_PROVENANCE_BATCH.rows
            ),
        )
        metadata.update(
            {
                "alignment_route": "untargeted_cid_nl_finalized_v1",
                "effective_owner_build_route": (
                    owner_build_route_receipt.effective_owner_build_route
                ),
                "effective_owner_build_xic_backend": "none",
                "owner_build_xic_backend_role": "configured_legacy_option",
                "matrix_value_policy": "owner_finalized_cell_decision_v1",
                "matrix_coordinate_policy": "cross_sample_owner_arithmetic_mean_v1",
            }
        )
        if known_istd_reference is not None:
            metadata.update({f"known_istd_reference_{key}": value
                             for key, value in known_istd_reference.provenance.items()})
        with timing_recorder.stage("alignment.write_outputs"):
            artifact_fingerprint_batch = _write_outputs_atomic(
                outputs,
                writer_inputs.matrix,
                metadata=metadata,
                ownership=OwnershipBuildResult(
                    owners=(), assignments=(), ambiguous_records=()
                ),
                alignment_config=alignment_config,
                owner_backfill_peak_fact_provenance=EMPTY_OWNER_BACKFILL_PEAK_FACT_PROVENANCE_BATCH,
                production_decisions=writer_inputs.production_decisions,
                baseline_integration_method=getattr(
                    peak_config, "baseline_integration_method", "asls"
                ),
                baseline_audit_method=getattr(peak_config, "baseline_audit_method", ""),
                timing_recorder=timing_recorder,
                capture_artifact_fingerprints=capture_artifact_fingerprints,
            )
        if capture_artifact_fingerprints:
            if not isinstance(
                artifact_fingerprint_batch, AlignmentArtifactFingerprintBatch
            ):
                raise TypeError(
                    "Alignment writer must return an owner-minted fingerprint batch"
                )
            outputs = replace(
                outputs, artifact_fingerprint_batch=artifact_fingerprint_batch
            )
        if outputs.matrix_tsv is None:
            raise RuntimeError("CID-NL explanation requires the published Matrix")
        with timing_recorder.stage("alignment.write_cid_nl_explanation"):
            write_cid_nl_explanation(
                output_dir / "cid_nl_explanation.sqlite",
                completed=completed,
                writer_inputs=writer_inputs,
                candidates_by_sample=candidates_by_sample,
                matrix_path=outputs.matrix_tsv,
                alignment_config=alignment_config,
                known_istd_reference_provenance=(
                    None
                    if known_istd_reference is None
                    else known_istd_reference.provenance
                ),
                progress_callback=timing_recorder.heartbeat_event,
            )
        return replace(
            outputs,
            owner_build_route_receipt=owner_build_route_receipt,
            untargeted_cid_nl_result=completed,
        )
    finally:
        if hasattr(completed, "close"):
            completed.close()


_output_paths = output_paths


_write_outputs_atomic = write_outputs_atomic


_CONDITIONAL_ALIGNMENT_ARTIFACT_NAMES = (
    "cid_nl_explanation.sqlite",
    "alignment_cell_integration_audit.tsv",
    "alignment_owner_backfill_seed_audit.tsv",
    "alignment_owner_backfill_candidate_audit.tsv",
    "skipped_evidence_ledger.tsv",
    "alignment_run_metadata.json",
    "provisional_alignment_matrix.tsv",
    "provisional_row_identity.tsv",
    "provisional_cell_provenance.tsv",
    "provisional_matrix_summary.json",
)


_metadata = alignment_metadata


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


def _required_candidate_receipt(
    batch: DiscoveryBatchInput,
    sample_stem: str,
) -> DiscoveryCandidateUniverseReceipt:
    receipt = batch.candidate_receipts[sample_stem]
    if receipt is None:
        raise ValueError(
            f"{sample_stem}: owner-closed Alignment requires a candidate receipt"
        )
    return receipt
