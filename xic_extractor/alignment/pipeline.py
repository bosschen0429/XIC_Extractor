from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any

from xic_extractor.alignment.backfill_scope import (
    PREDICATE_VERSION,
    REQUEST_PLAN_VERSION,
    BackfillScope,
    backfill_request_sample_stems,
    backfill_seed_centers,
    select_backfill_features,
    skipped_evidence_summary,
)
from xic_extractor.alignment.claim_registry import apply_ms1_peak_claim_registry
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.csv_io import (
    DiscoveryBatchInput,
    read_discovery_batch_index,
    read_discovery_candidates_csv,
    read_owner_bound_discovery_candidates_csv,
)
from xic_extractor.alignment.edge_scoring import (
    DriftLookupProtocol,
    OwnerEdgeEvidence,
)
from xic_extractor.alignment.identity_coherence.adapter import (
    run_identity_coherence_diagnostic,
)
from xic_extractor.alignment.ms1_index_source import (
    OwnerBackfillXicBackend,
    OwnerBuildXicBackend,
    source_for_owner_build_backend,
)
from xic_extractor.alignment.output_levels import AlignmentOutputLevel
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillCandidateAuditRow,
    OwnerBackfillResult,
    OwnerBackfillWindowStrategy,
    build_owner_backfill_cells,
    build_owner_backfill_result,
)
from xic_extractor.alignment.owner_clustering import (
    cluster_sample_local_owners,
    review_only_features_from_ambiguous_records,
)
from xic_extractor.alignment.owner_matrix import build_owner_alignment_matrix
from xic_extractor.alignment.ownership import build_sample_local_owners
from xic_extractor.alignment.pipeline_outputs import (
    AlignmentRunOutputs,
    alignment_metadata,
    output_paths,
    write_outputs_atomic,
)
from xic_extractor.alignment.pre_backfill_consolidation import (
    consolidate_pre_backfill_identity_families,
    recenter_pre_backfill_identity_families,
)
from xic_extractor.alignment.primary_consolidation import (
    consolidate_primary_family_rows,
)
from xic_extractor.alignment.process_backend import (
    OwnerBackfillSampleResult,
    OwnerBackfillWorkerError,
    OwnerBuildSampleResult,
    OwnerBuildWorkerError,
    run_owner_backfill_process,
    run_owner_build_process,
)
from xic_extractor.alignment.raw_sources import (
    AlignmentRawHandle as _AlignmentRawHandle,
)
from xic_extractor.alignment.raw_sources import (
    RawSourceTimingStats,
    TimedRawSource,
    existing_raw_paths,
    record_raw_source_timing_stats,
    record_timed_raw_sources,
    timed_owner_backfill_sources,
    timed_raw_sources,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.discovery.batch_contract import DiscoveryCandidateUniverseReceipt
from xic_extractor.injection_rolling import (
    order_sample_columns_by_injection,
    read_injection_order,
)
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.sample_metadata import (
    is_sample_metadata_source,
    load_sample_metadata,
    sample_metadata_to_injection_order,
)

_RawSourceTimingStats = RawSourceTimingStats
_TimedRawSource = TimedRawSource
AlignmentRawHandle = _AlignmentRawHandle
RawOpener = Callable[[Path, Path], AbstractContextManager[AlignmentRawHandle]]


def run_alignment(
    *,
    discovery_batch_index: Path,
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    alignment_config: AlignmentConfig,
    peak_config: ExtractionConfig,
    output_level: AlignmentOutputLevel = "machine",
    emit_alignment_cells: bool = False,
    emit_alignment_status_matrix: bool = False,
    emit_alignment_integration_audit: bool = False,
    emit_alignment_backfill_seed_audit: bool = False,
    emit_alignment_backfill_candidate_audit: bool = False,
    raw_opener: RawOpener | None = None,
    raw_workers: int = 1,
    raw_xic_batch_size: int = 1,
    owner_build_xic_backend: OwnerBuildXicBackend = "raw",
    owner_backfill_xic_backend: OwnerBackfillXicBackend = "raw",
    owner_backfill_window_strategy: OwnerBackfillWindowStrategy = "exact",
    owner_backfill_superwindow_span_factor: int = 2,
    preconsolidate_owner_families: bool = False,
    emit_identity_coherence_diagnostic: bool = False,
    identity_coherence_output_dir: Path | None = None,
    identity_coherence_controls_manifest: Path | None = None,
    drift_lookup: DriftLookupProtocol | None = None,
    sample_column_injection_order: Path | None = None,
    timing_recorder: TimingRecorder | None = None,
    backfill_scope: BackfillScope = "full-audit",
    audit_evidence_mode: str = "auto",
    selected_family_ids: frozenset[str] = frozenset(),
    selected_family_source: str = "",
    require_discovery_owner_closure: bool = False,
    emit_provisional_matrix: bool = False,
) -> AlignmentRunOutputs:
    if raw_workers < 1:
        raise ValueError("raw_workers must be >= 1")
    if raw_xic_batch_size < 1:
        raise ValueError("raw_xic_batch_size must be >= 1")
    if owner_backfill_superwindow_span_factor < 1:
        raise ValueError("owner_backfill_superwindow_span_factor must be >= 1")
    if emit_provisional_matrix and not require_discovery_owner_closure:
        raise ValueError(
            "provisional matrix emission requires Discovery owner closure"
        )
    if emit_provisional_matrix and backfill_scope != "full-audit":
        raise ValueError(
            "provisional matrix emission requires full-audit Backfill"
        )
    if emit_provisional_matrix and (
        selected_family_ids or selected_family_source.strip()
    ):
        raise ValueError(
            "provisional matrix emission does not allow a family selection"
        )
    if require_discovery_owner_closure and raw_opener is not None:
        raise ValueError(
            "Discovery owner closure does not allow a custom RAW opener"
        )
    if (
        require_discovery_owner_closure
        and emit_identity_coherence_diagnostic
        and raw_workers > 1
    ):
        raise ValueError(
            "owner-closed identity coherence diagnostics require raw_workers=1"
        )
    recorder = timing_recorder or TimingRecorder.disabled("alignment")
    recorder.record(
        "alignment.run_config",
        elapsed_sec=0.0,
        metrics={
            "raw_workers": raw_workers,
            "raw_xic_batch_size": raw_xic_batch_size,
            "owner_build_xic_backend": owner_build_xic_backend,
            "owner_backfill_xic_backend": owner_backfill_xic_backend,
            "owner_backfill_window_strategy": owner_backfill_window_strategy,
            "owner_backfill_superwindow_span_factor": (
                owner_backfill_superwindow_span_factor
            ),
            "preconsolidate_owner_families": preconsolidate_owner_families,
            "output_level": output_level,
            "backfill_scope": backfill_scope,
            "requested_audit_evidence_mode": audit_evidence_mode,
            "selected_family_count": len(selected_family_ids),
            "require_discovery_owner_closure": require_discovery_owner_closure,
            "emit_provisional_matrix": emit_provisional_matrix,
            "drift_prior_source": (
                drift_lookup.source if drift_lookup is not None else "none"
            ),
        },
    )
    with recorder.stage("alignment.read_batch_index"):
        batch = read_discovery_batch_index(
            discovery_batch_index,
            require_owner_closure=require_discovery_owner_closure,
        )
    expected_acquisition_sources = (
        _expected_acquisition_sources(batch)
        if require_discovery_owner_closure
        else None
    )
    with recorder.stage("alignment.read_candidates") as stage:
        if require_discovery_owner_closure:
            candidates = tuple(
                candidate
                for sample_stem in batch.sample_order
                for candidate in read_owner_bound_discovery_candidates_csv(
                    batch.candidate_csvs[sample_stem],
                    receipt=_required_candidate_receipt(batch, sample_stem),
                    require_successor_columns=True,
                )
            )
        else:
            candidates = tuple(
                candidate
                for sample_stem in batch.sample_order
                for candidate in read_discovery_candidates_csv(
                    batch.candidate_csvs[sample_stem]
                )
            )
        stage.metrics["candidate_count"] = len(candidates)
    opener = raw_opener or _default_raw_opener
    outputs = _output_paths(
        output_dir,
        output_level=output_level,
        emit_alignment_cells=emit_alignment_cells,
        emit_alignment_status_matrix=emit_alignment_status_matrix,
        emit_alignment_integration_audit=emit_alignment_integration_audit,
        emit_alignment_backfill_seed_audit=emit_alignment_backfill_seed_audit,
        emit_alignment_backfill_candidate_audit=(
            emit_alignment_backfill_candidate_audit
        ),
        emit_skipped_evidence_ledger=backfill_scope != "full-audit",
        emit_provisional_matrix=emit_provisional_matrix,
    )
    resolved_audit_evidence_mode, audit_evidence_mode_reason = (
        _resolve_audit_evidence_mode(
            backfill_scope=backfill_scope,
            requested_mode=audit_evidence_mode,
            output_level=output_level,
            outputs=outputs,
        )
    )
    recorder.record(
        "alignment.audit_evidence_mode",
        elapsed_sec=0.0,
        metrics={
            "requested_audit_evidence_mode": audit_evidence_mode,
            "audit_evidence_mode": resolved_audit_evidence_mode,
            "audit_evidence_mode_reason": audit_evidence_mode_reason,
            "heavy_audit_enabled": resolved_audit_evidence_mode != "none",
        },
    )
    emit_cell_region_audit = resolved_audit_evidence_mode in {"full", "selected"}
    region_audit_family_ids = (
        selected_family_ids if backfill_scope == "selected-families" else None
    )

    with ExitStack() as stack:
        raw_paths = _existing_raw_paths(
            sample_order=batch.sample_order,
            raw_files=batch.raw_files,
            raw_dir=raw_dir,
        )
        if require_discovery_owner_closure and set(raw_paths) != set(
            batch.sample_order
        ):
            missing = sorted(set(batch.sample_order) - set(raw_paths))
            raise ValueError(
                "owner-closed Alignment requires RAW for every sample: "
                + ", ".join(missing)
            )
        with recorder.stage("alignment.open_raw_sources") as stage:
            if raw_workers > 1:
                raw_sources = {}
                stage.metrics["raw_count"] = len(raw_paths)
                stage.metrics["mode"] = "process_workers"
            else:
                if expected_acquisition_sources is None:
                    raw_sources = {
                        sample_stem: stack.enter_context(opener(raw_path, dll_dir))
                        for sample_stem, raw_path in raw_paths.items()
                    }
                else:
                    raw_sources = {
                        sample_stem: stack.enter_context(
                            _default_raw_opener(
                                raw_path,
                                dll_dir,
                                expected_acquisition_source=(
                                    expected_acquisition_sources[sample_stem]
                                ),
                            )
                        )
                        for sample_stem, raw_path in raw_paths.items()
                    }
                stage.metrics["raw_count"] = len(raw_sources)
        with recorder.stage("alignment.build_owners"):
            if raw_workers > 1:
                owner_output = run_owner_build_process(
                    candidates,
                    sample_order=batch.sample_order,
                    raw_paths=raw_paths,
                    dll_dir=dll_dir,
                    alignment_config=alignment_config,
                    peak_config=peak_config,
                    max_workers=raw_workers,
                    raw_xic_batch_size=raw_xic_batch_size,
                    owner_build_xic_backend=owner_build_xic_backend,
                    expected_acquisition_sources=expected_acquisition_sources,
                    emit_region_audit=emit_cell_region_audit,
                    region_audit_family_ids=region_audit_family_ids,
                    audit_evidence_mode=resolved_audit_evidence_mode,
                    progress_callback=lambda result: _record_owner_build_progress(
                        recorder,
                        result,
                    ),
                )
                ownership = owner_output.ownership
                for stats in owner_output.timing_stats:
                    recorder.record(
                        "alignment.build_owners.extract_xic",
                        elapsed_sec=stats.elapsed_sec,
                        sample_stem=stats.sample_stem,
                        metrics={
                            "extract_xic_count": stats.extract_xic_count,
                            "extract_xic_batch_count": stats.extract_xic_batch_count,
                            "raw_chromatogram_call_count": (
                                stats.raw_chromatogram_call_count
                            ),
                            "point_count": stats.point_count,
                        },
                    )
            else:
                owner_build_raw_sources = {
                    sample_stem: source_for_owner_build_backend(
                        source,
                        owner_build_xic_backend,
                    )
                    for sample_stem, source in raw_sources.items()
                }
                timed_raw_sources_ = timed_raw_sources(
                    owner_build_raw_sources,
                    stage="alignment.build_owners.extract_xic",
                )
                ownership = build_sample_local_owners(
                    candidates,
                    raw_sources=timed_raw_sources_,
                    alignment_config=alignment_config,
                    peak_config=peak_config,
                    raw_xic_batch_size=raw_xic_batch_size,
                    emit_region_audit=emit_cell_region_audit,
                    region_audit_family_ids=region_audit_family_ids,
                    audit_evidence_mode=resolved_audit_evidence_mode,
                )
                record_timed_raw_sources(timed_raw_sources_, recorder=recorder)
        edge_evidence: list[OwnerEdgeEvidence] | None = (
            []
            if outputs.edge_evidence_tsv is not None or drift_lookup is not None
            else None
        )
        with recorder.stage("alignment.cluster_owners"):
            if edge_evidence is None:
                owner_features = cluster_sample_local_owners(
                    ownership.owners,
                    config=alignment_config,
                )
            else:
                owner_features = cluster_sample_local_owners(
                    ownership.owners,
                    config=alignment_config,
                    drift_lookup=drift_lookup,
                    edge_evidence_sink=edge_evidence,
                )
            owner_features = (
                *owner_features,
                *review_only_features_from_ambiguous_records(
                    ownership.ambiguous_records,
                    start_index=len(owner_features) + 1,
                ),
            )
        if emit_identity_coherence_diagnostic:
            identity_output_dir = (
                identity_coherence_output_dir
                if identity_coherence_output_dir is not None
                else output_dir / "identity_coherence"
            )
            with recorder.stage("alignment.identity_coherence_diagnostic") as stage:
                diagnostic_run = run_identity_coherence_diagnostic(
                    candidates=candidates,
                    ownership=ownership,
                    sample_order=batch.sample_order,
                    raw_sources=raw_sources,
                    raw_paths=raw_paths,
                    dll_dir=dll_dir,
                    raw_workers=raw_workers,
                    raw_xic_batch_size=raw_xic_batch_size,
                    output_dir=identity_output_dir,
                    alignment_config=alignment_config,
                    fragment_profile_id="alignment-cid-neutral-loss-v0.4",
                    fragment_profile_hash="unavailable",
                    controls_manifest_path=identity_coherence_controls_manifest,
                )
                stage.metrics["record_count"] = len(diagnostic_run.records)
                stage.metrics["raw_xic_request_count"] = (
                    diagnostic_run.context.raw_xic_request_count or 0
                )
                stage.metrics["xic_point_count"] = (
                    diagnostic_run.context.xic_point_count or 0
                )
            outputs = replace(
                outputs,
                identity_coherence_output_dir=identity_output_dir,
            )
        if preconsolidate_owner_families:
            with recorder.stage("alignment.pre_backfill_consolidation"):
                owner_features = consolidate_pre_backfill_identity_families(
                    owner_features,
                    config=alignment_config,
                )
        with recorder.stage("alignment.backfill_scope") as stage:
            scope_selection = select_backfill_features(
                owner_features,
                sample_order=batch.sample_order,
                raw_sample_stems=frozenset(raw_paths),
                alignment_config=alignment_config,
                scope=backfill_scope,
                selected_family_ids=selected_family_ids,
            )
            backfill_features = scope_selection.features
            request_summary = _backfill_request_summary(
                backfill_features,
                sample_order=batch.sample_order,
                raw_sample_stems=frozenset(raw_paths),
                alignment_config=alignment_config,
            )
            stage.metrics.update(
                {
                    "backfill_scope": backfill_scope,
                    "input_feature_count": len(owner_features),
                    "backfill_feature_count": len(backfill_features),
                    "skipped_feature_count": (
                        len(owner_features) - len(backfill_features)
                    ),
                    "selected_family_count": len(selected_family_ids),
                    **skipped_evidence_summary(scope_selection.skipped),
                    **request_summary,
                }
        )
        with recorder.stage("alignment.owner_backfill") as owner_backfill_stage:
            owner_backfill_candidate_audit_rows: tuple[
                OwnerBackfillCandidateAuditRow,
                ...,
            ] = ()
            if raw_workers > 1:
                process_output = run_owner_backfill_process(
                    backfill_features,
                    sample_order=batch.sample_order,
                    raw_paths=raw_paths,
                    dll_dir=dll_dir,
                    alignment_config=alignment_config,
                    peak_config=peak_config,
                    max_workers=raw_workers,
                    raw_xic_batch_size=raw_xic_batch_size,
                    owner_backfill_xic_backend=owner_backfill_xic_backend,
                    expected_acquisition_sources=expected_acquisition_sources,
                    owner_backfill_window_strategy=owner_backfill_window_strategy,
                    owner_backfill_superwindow_span_factor=(
                        owner_backfill_superwindow_span_factor
                    ),
                    backfill_scope=backfill_scope,
                    emit_region_audit=emit_cell_region_audit,
                    region_audit_family_ids=region_audit_family_ids,
                    audit_evidence_mode=resolved_audit_evidence_mode,
                    progress_callback=lambda result: _record_owner_backfill_progress(
                        recorder,
                        result,
                    ),
                )
                rescued_cells = process_output.cells
                owner_backfill_candidate_audit_rows = (
                    process_output.candidate_audit_rows
                )
                owner_backfill_stage.metrics.update(
                    _summarize_xic_timing_stats(process_output.timing_stats),
                )
                for backfill_stats in process_output.timing_stats:
                    recorder.record(
                        "alignment.owner_backfill.extract_xic",
                        elapsed_sec=backfill_stats.elapsed_sec,
                        sample_stem=backfill_stats.sample_stem,
                        metrics={
                            "extract_xic_count": backfill_stats.extract_xic_count,
                            "extract_xic_batch_count": (
                                backfill_stats.extract_xic_batch_count
                            ),
                            "raw_chromatogram_call_count": (
                                backfill_stats.raw_chromatogram_call_count
                            ),
                            "point_count": backfill_stats.point_count,
                        },
                    )
            else:
                (
                    backfill_raw_sources,
                    validation_raw_sources,
                    timing_stats,
                ) = timed_owner_backfill_sources(
                    raw_sources,
                    backend=owner_backfill_xic_backend,
                    stage="alignment.owner_backfill.extract_xic",
                )
                validation_kwargs = (
                    {"validation_raw_sources": validation_raw_sources}
                    if validation_raw_sources is not None
                    else {}
                )
                backfill_result = _build_owner_backfill_result(
                    backfill_features,
                    sample_order=batch.sample_order,
                    raw_sources=backfill_raw_sources,
                    **validation_kwargs,
                    alignment_config=alignment_config,
                    peak_config=peak_config,
                    raw_xic_batch_size=raw_xic_batch_size,
                    owner_backfill_window_strategy=owner_backfill_window_strategy,
                    owner_backfill_superwindow_span_factor=(
                        owner_backfill_superwindow_span_factor
                    ),
                    emit_region_audit=emit_cell_region_audit,
                    region_audit_family_ids=region_audit_family_ids,
                    audit_evidence_mode=resolved_audit_evidence_mode,
                )
                rescued_cells = backfill_result.cells
                owner_backfill_candidate_audit_rows = (
                    backfill_result.candidate_audit_rows
                )
                owner_backfill_stage.metrics.update(
                    _summarize_xic_timing_stats(timing_stats),
                )
                record_raw_source_timing_stats(timing_stats, recorder=recorder)
        matrix_sample_order = batch.sample_order
        if sample_column_injection_order is not None:
            injection_order, injection_order_source_schema = (
                _read_sample_column_injection_order(sample_column_injection_order)
            )
            matrix_sample_order = order_sample_columns_by_injection(
                batch.sample_order,
                injection_order,
            )
            recorder.record(
                "alignment.sample_column_injection_order",
                elapsed_sec=0.0,
                metrics={
                    "ordered_samples": sum(
                        1 for sample in batch.sample_order if sample in injection_order
                    ),
                    "unordered_samples": sum(
                        1
                        for sample in batch.sample_order
                        if sample not in injection_order
                    ),
                    "source": str(sample_column_injection_order),
                    "source_schema": injection_order_source_schema,
                },
            )
        with recorder.stage("alignment.build_matrix"):
            matrix = build_owner_alignment_matrix(
                owner_features,
                sample_order=matrix_sample_order,
                ambiguous_by_sample={},
                rescued_cells=rescued_cells,
            )
        with recorder.stage("alignment.claim_registry"):
            matrix = apply_ms1_peak_claim_registry(matrix, alignment_config)
        with recorder.stage("alignment.primary_consolidation"):
            matrix = consolidate_primary_family_rows(matrix, alignment_config)
        with recorder.stage("alignment.pre_backfill_recenter"):
            matrix = recenter_pre_backfill_identity_families(matrix)
        with recorder.stage("alignment.write_outputs"):
            _write_outputs_atomic(
                outputs,
                matrix,
                metadata=_metadata(
                    discovery_batch_index=discovery_batch_index,
                    raw_dir=raw_dir,
                    dll_dir=dll_dir,
                    owner_build_xic_backend=owner_build_xic_backend,
                    owner_backfill_xic_backend=owner_backfill_xic_backend,
                    owner_backfill_window_strategy=owner_backfill_window_strategy,
                    owner_backfill_superwindow_span_factor=(
                        owner_backfill_superwindow_span_factor
                    ),
                    output_level=output_level,
                    peak_config=peak_config,
                    backfill_scope=backfill_scope,
                    output_scope=(
                        "diagnostic_only"
                        if backfill_scope == "selected-families"
                        else backfill_scope
                    ),
                    selected_family_count=len(selected_family_ids),
                    selected_family_source=_selected_family_source(
                        backfill_scope,
                        selected_family_ids,
                        selected_family_source,
                    ),
                    request_plan_version=REQUEST_PLAN_VERSION,
                    audit_evidence_mode=resolved_audit_evidence_mode,
                    requested_audit_evidence_mode=audit_evidence_mode,
                    heavy_audit_enabled=resolved_audit_evidence_mode != "none",
                    audit_evidence_mode_reason=audit_evidence_mode_reason,
                    scope_warning=(
                        "diagnostic_only_incomplete_scope"
                        if backfill_scope == "selected-families"
                        else ""
                    ),
                    skipped_evidence_predicate_version=PREDICATE_VERSION,
                    require_discovery_owner_closure=(
                        require_discovery_owner_closure
                    ),
                ),
                ownership=ownership,
                alignment_config=alignment_config,
                edge_evidence=edge_evidence or (),
                skipped_evidence=scope_selection.skipped,
                owner_backfill_candidate_audit_rows=(
                    owner_backfill_candidate_audit_rows
                ),
                baseline_integration_method=getattr(
                    peak_config,
                    "baseline_integration_method",
                    "asls",
                ),
                baseline_audit_method=getattr(peak_config, "baseline_audit_method", ""),
                timing_recorder=recorder,
            )
        return outputs


_existing_raw_paths = existing_raw_paths
_output_paths = output_paths
_write_outputs_atomic = write_outputs_atomic
_DEFAULT_BUILD_OWNER_BACKFILL_CELLS = build_owner_backfill_cells


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


def _expected_acquisition_sources(
    batch: DiscoveryBatchInput,
) -> dict[str, AcquisitionSourceIdentity]:
    return {
        sample_stem: _required_candidate_receipt(batch, sample_stem).acquisition_source
        for sample_stem in batch.sample_order
    }


def _build_owner_backfill_result(*args: Any, **kwargs: Any) -> OwnerBackfillResult:
    if build_owner_backfill_cells is not _DEFAULT_BUILD_OWNER_BACKFILL_CELLS:
        return OwnerBackfillResult(
            cells=tuple(build_owner_backfill_cells(*args, **kwargs)),
            candidate_audit_rows=(),
        )
    return build_owner_backfill_result(*args, **kwargs)
_metadata = alignment_metadata


def _resolve_audit_evidence_mode(
    *,
    backfill_scope: BackfillScope,
    requested_mode: str,
    output_level: str,
    outputs: AlignmentRunOutputs,
) -> tuple[str, str]:
    if requested_mode not in {"auto", "none", "full", "selected"}:
        raise ValueError("audit_evidence_mode must be auto, none, full, or selected")
    if requested_mode == "selected" and backfill_scope != "selected-families":
        raise ValueError(
            "audit_evidence_mode selected requires backfill_scope selected-families"
        )
    if requested_mode != "auto":
        return requested_mode, f"explicit_{requested_mode}"

    has_integration_destination = outputs.integration_audit_tsv is not None
    if output_level == "validation-minimal" and not has_integration_destination:
        return "none", "validation_minimal_default_no_audit"
    if backfill_scope == "full-audit":
        if outputs.cells_tsv is not None or has_integration_destination:
            return "full", "full_audit_legacy_audit_output"
        return "none", "no_audit_destination"
    if backfill_scope == "selected-families":
        if has_integration_destination:
            return "selected", "selected_family_audit_destination"
        return "none", "selected_family_no_audit_destination"
    if has_integration_destination:
        return "full", "production_equivalent_explicit_audit_destination"
    return "none", "production_equivalent_default_no_audit"


def _selected_family_source(
    backfill_scope: BackfillScope,
    selected_family_ids: frozenset[str],
    selected_family_source: str,
) -> str:
    if backfill_scope != "selected-families":
        return ""
    if selected_family_source:
        return selected_family_source
    return "inline:" + ",".join(sorted(selected_family_ids))


def _backfill_request_summary(
    features,
    *,
    sample_order: tuple[str, ...],
    raw_sample_stems: frozenset[str],
    alignment_config: AlignmentConfig,
) -> dict[str, int | float | bool]:
    sample_extract_counts = {sample_stem: 0 for sample_stem in sample_order}
    request_target_count = 0
    extract_request_count = 0
    for feature in features:
        try:
            request_samples = backfill_request_sample_stems(
                feature,
                sample_order=sample_order,
                raw_sample_stems=raw_sample_stems,
                alignment_config=alignment_config,
            )
            seed_count = len(backfill_seed_centers(feature))
        except AttributeError:
            return {"request_summary_unavailable": True}
        request_target_count += len(request_samples)
        extract_request_count += len(request_samples) * seed_count
        for sample_stem in request_samples:
            sample_extract_counts[sample_stem] = (
                sample_extract_counts.get(sample_stem, 0) + seed_count
            )
    counts = sorted(sample_extract_counts.values())
    median_count = 0.0
    if counts:
        midpoint = len(counts) // 2
        if len(counts) % 2:
            median_count = float(counts[midpoint])
        else:
            median_count = (counts[midpoint - 1] + counts[midpoint]) / 2.0
    return {
        "request_target_count": request_target_count,
        "extract_request_count": extract_request_count,
        "max_sample_extract_request_count": max(counts) if counts else 0,
        "median_sample_extract_request_count": median_count,
    }


def _record_owner_build_progress(
    recorder: TimingRecorder,
    result: OwnerBuildSampleResult | OwnerBuildWorkerError,
) -> None:
    if isinstance(result, OwnerBuildWorkerError):
        recorder.record(
            "alignment.build_owners.sample_error",
            elapsed_sec=0.0,
            sample_stem=result.sample_stem,
            metrics={
                "sample_index": result.sample_index,
                "raw_name": result.raw_name,
                "message": result.message,
            },
        )
        return
    metrics: dict[str, object] = {
        "sample_index": result.sample_index,
        "owner_count": len(result.owners),
        "assignment_count": len(result.assignments),
        "ambiguous_record_count": len(result.ambiguous_records),
    }
    if result.timing_stats:
        stats = result.timing_stats[0]
        metrics.update(
            {
                "extract_xic_count": stats.extract_xic_count,
                "extract_xic_batch_count": stats.extract_xic_batch_count,
                "raw_chromatogram_call_count": stats.raw_chromatogram_call_count,
                "point_count": stats.point_count,
                "worker_elapsed_sec": stats.elapsed_sec,
            }
        )
    recorder.record(
        "alignment.build_owners.sample_complete",
        elapsed_sec=0.0,
        sample_stem=result.sample_stem,
        metrics=metrics,
    )


def _record_owner_backfill_progress(
    recorder: TimingRecorder,
    result: OwnerBackfillSampleResult | OwnerBackfillWorkerError,
) -> None:
    if isinstance(result, OwnerBackfillWorkerError):
        recorder.record(
            "alignment.owner_backfill.sample_error",
            elapsed_sec=0.0,
            sample_stem=result.sample_stem,
            metrics={
                "sample_index": result.sample_index,
                "raw_name": result.raw_name,
                "message": result.message,
            },
        )
        return
    metrics: dict[str, object] = {
        "sample_index": result.sample_index,
        "rescued_cell_count": len(result.cells),
    }
    if result.timing_stats:
        stats = result.timing_stats[0]
        metrics.update(
            {
                "extract_xic_count": stats.extract_xic_count,
                "extract_xic_batch_count": stats.extract_xic_batch_count,
                "raw_chromatogram_call_count": stats.raw_chromatogram_call_count,
                "point_count": stats.point_count,
                "worker_elapsed_sec": stats.elapsed_sec,
            }
        )
    recorder.record(
        "alignment.owner_backfill.sample_complete",
        elapsed_sec=0.0,
        sample_stem=result.sample_stem,
        metrics=metrics,
    )


def _summarize_xic_timing_stats(
    timing_stats: Iterable[object],
) -> dict[str, int | float | None]:
    stats_items = tuple(timing_stats)
    extract_xic_count = sum(
        int(getattr(stats, "extract_xic_count", 0)) for stats in stats_items
    )
    extract_xic_batch_count = sum(
        int(getattr(stats, "extract_xic_batch_count", 0)) for stats in stats_items
    )
    raw_chromatogram_call_count = sum(
        int(getattr(stats, "raw_chromatogram_call_count", 0))
        for stats in stats_items
    )
    point_count = sum(int(getattr(stats, "point_count", 0)) for stats in stats_items)
    return {
        "extract_xic_count": extract_xic_count,
        "extract_xic_batch_count": extract_xic_batch_count,
        "raw_chromatogram_call_count": raw_chromatogram_call_count,
        "point_count": point_count,
        "mean_xic_per_raw_chromatogram_call": (
            None
            if raw_chromatogram_call_count == 0
            else extract_xic_count / raw_chromatogram_call_count
        ),
        "mean_xic_per_extract_batch": (
            None
            if extract_xic_batch_count == 0
            else extract_xic_count / extract_xic_batch_count
        ),
    }


def _read_sample_column_injection_order(path: Path) -> tuple[dict[str, int], str]:
    if is_sample_metadata_source(path):
        return (
            sample_metadata_to_injection_order(load_sample_metadata(path)),
            "sample_metadata_v1",
        )
    return read_injection_order(path), "legacy_injection_order"


def _default_raw_opener(
    raw_path: Path,
    dll_dir: Path,
    *,
    expected_acquisition_source: AcquisitionSourceIdentity | None = None,
) -> AbstractContextManager[AlignmentRawHandle]:
    from xic_extractor.raw_reader import open_raw

    return open_raw(
        raw_path,
        dll_dir,
        expected_acquisition_source=expected_acquisition_source,
    )
