from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any

from xic_extractor.alignment.artifact_write_receipts import (
    AlignmentArtifactFingerprintBatch,
)
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
from xic_extractor.alignment.early_loser_shadow import (
    EarlyLoserShadowProposal,
    EarlyLoserShadowReport,
    build_early_loser_shadow_proposal,
    compare_early_loser_shadow_with_final_authority,
)
from xic_extractor.alignment.edge_scoring import (
    DriftLookupProtocol,
    OwnerEdgeEvidence,
)
from xic_extractor.alignment.identity_coherence.adapter import (
    run_identity_coherence_diagnostic,
)
from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.alignment.ms1_index_source import (
    OwnerBackfillXicBackend,
    OwnerBuildXicBackend,
    source_for_owner_build_backend,
)
from xic_extractor.alignment.output_levels import (
    AlignmentOutputLevel,
    artifact_names_for_output_level,
)
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillCandidateAuditRow,
    OwnerBackfillPeakFactObservation,
    OwnerBackfillRequestReuseObservation,
    OwnerBackfillResult,
    OwnerBackfillWindowStrategy,
    build_owner_backfill_cells,
    build_owner_backfill_result,
)
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    OwnerBackfillCellProjectionV2,
    OwnerBackfillPeakFactStageProjection,
    build_owner_backfill_peak_fact_provenance_batch,
    owner_backfill_authority_cell_projection,
    owner_backfill_cell_lineage_matches,
    owner_backfill_cell_projection,
    owner_backfill_peak_fact_stage_projection_from_observation,
    selected_integration_authority_projection,
)
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    OwnerBackfillPeakFactObservation as ProvenanceObservation,
)
from xic_extractor.alignment.owner_build_route_receipt import (
    _mint_owner_build_route_receipt,
    _validate_owner_build_xic_backend,
)
from xic_extractor.alignment.owner_clustering import (
    cluster_sample_local_owners,
    review_only_features_from_ambiguous_records,
)
from xic_extractor.alignment.owner_matrix import build_owner_alignment_matrix
from xic_extractor.alignment.ownership import (
    build_sample_local_owners,
    build_sample_local_owners_from_discovery_bindings,
)
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
from xic_extractor.alignment.production_decisions import (
    ProductionDecisionSet,
    build_production_decisions,
    production_cell_decision_projection,
)
from xic_extractor.alignment.quantitative_projection_authority import (
    load_quantitative_projection_authority_for_diagnostic,
    require_product_quantitative_projection_authority,
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
from xic_extractor.discovery.batch_contract import (
    DiscoveryCandidateUniverseReceipt,
    validate_discovery_candidate_physical_bindings,
)
from xic_extractor.discovery.models import DiscoveryCandidate
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryCandidatePhysicalBinding,
)
from xic_extractor.injection_rolling import (
    order_sample_columns_by_injection,
    read_injection_order,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    DEFAULT_SIGNAL_PROCESSING_PROFILE_ID,
    SignalProcessingProfile,
    resolve_signal_processing_profile,
)
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.processing_run_spec import ProcessingRunSpec
from xic_extractor.sample_metadata import (
    is_sample_metadata_source,
    load_sample_metadata,
    sample_metadata_to_injection_order,
)

_RawSourceTimingStats = RawSourceTimingStats
_TimedRawSource = TimedRawSource
AlignmentRawHandle = _AlignmentRawHandle
RawOpener = Callable[[Path, Path], AbstractContextManager[AlignmentRawHandle]]


def _ordered_discovery_physical_bindings(
    *,
    sample_order: tuple[str, ...],
    candidates_by_sample: Mapping[str, Sequence[DiscoveryCandidate]],
    candidate_receipts: Mapping[
        str,
        DiscoveryCandidateUniverseReceipt | None,
    ],
    run_spec: ProcessingRunSpec,
    expected_profile_id: str,
    bindings_by_sample: Mapping[
        str,
        Sequence[DiscoveryCandidatePhysicalBinding],
    ],
) -> tuple[DiscoveryCandidatePhysicalBinding, ...]:
    if not isinstance(run_spec, ProcessingRunSpec):
        raise TypeError("discovery_processing_run_spec must be a ProcessingRunSpec")
    if run_spec.profile_id != expected_profile_id:
        raise ValueError("Discovery processing run crosses Alignment signal profile")
    if set(bindings_by_sample) != set(sample_order):
        raise ValueError(
            "Discovery physical binding samples must exactly match the batch"
        )
    if tuple(item.sample_stem for item in run_spec.acquisition_inputs) != sample_order:
        raise ValueError("Discovery processing run sample order crosses the batch")
    ordered: list[DiscoveryCandidatePhysicalBinding] = []
    for sample_stem, acquisition_input in zip(
        sample_order,
        run_spec.acquisition_inputs,
        strict=True,
    ):
        receipt = candidate_receipts.get(sample_stem)
        if receipt is None:
            raise ValueError(f"{sample_stem}: Discovery candidate receipt is required")
        if receipt.acquisition_source != acquisition_input.acquisition_source:
            raise ValueError(f"{sample_stem}: processing run crosses batch acquisition")
        ordered.extend(
            validate_discovery_candidate_physical_bindings(
                run_spec=run_spec,
                sample_stem=sample_stem,
                candidates=candidates_by_sample[sample_stem],
                bindings=bindings_by_sample[sample_stem],
            )
        )
    return tuple(ordered)


def run_alignment(
    *,
    discovery_batch_index: Path,
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    alignment_config: AlignmentConfig,
    peak_config: ExtractionConfig,
    signal_processing_profile: SignalProcessingProfile | None = None,
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
    capture_artifact_fingerprints: bool = False,
    backfill_scope: BackfillScope = "full-audit",
    audit_evidence_mode: str = "auto",
    selected_family_ids: frozenset[str] = frozenset(),
    selected_family_source: str = "",
    require_discovery_owner_closure: bool = False,
    discovery_processing_run_spec: ProcessingRunSpec | None = None,
    discovery_physical_bindings_by_sample: (
        Mapping[str, Sequence[DiscoveryCandidatePhysicalBinding]] | None
    ) = None,
    emit_provisional_matrix: bool = False,
    emit_early_loser_shadow: bool = False,
) -> AlignmentRunOutputs:
    owner_build_xic_backend = _validate_owner_build_xic_backend(
        owner_build_xic_backend,
    )
    resolved_signal_processing_profile = resolve_signal_processing_profile(
        signal_processing_method=getattr(
            peak_config,
            "signal_processing_method",
            DEFAULT_SIGNAL_PROCESSING_PROFILE.method,
        ),
        signal_processing_window_points=getattr(
            peak_config,
            "signal_processing_window_points",
            DEFAULT_SIGNAL_PROCESSING_PROFILE.window_points,
        ),
    )
    if signal_processing_profile is not None:
        if not isinstance(signal_processing_profile, SignalProcessingProfile):
            raise TypeError(
                "signal_processing_profile must be a SignalProcessingProfile"
            )
        if signal_processing_profile != resolved_signal_processing_profile:
            raise ValueError(
                "signal_processing_profile conflicts with peak_config canonical fields"
            )
    signal_processing_profile = resolved_signal_processing_profile
    if (discovery_processing_run_spec is None) != (
        discovery_physical_bindings_by_sample is None
    ):
        raise ValueError(
            "Discovery processing run and physical bindings must be provided together"
        )
    if type(selected_family_ids) is not frozenset or any(
        type(family_id) is not str for family_id in selected_family_ids
    ):
        raise TypeError("selected_family_ids must be an exact frozenset[str]")
    family_selection_requested = (
        bool(selected_family_ids) or selected_family_source.strip() != ""
    )
    default_signal_processing_profile = (
        signal_processing_profile.profile_id == DEFAULT_SIGNAL_PROCESSING_PROFILE_ID
    )
    if (
        output_level in {"production", "machine"}
        and signal_processing_profile.profile_id != DEFAULT_SIGNAL_PROCESSING_PROFILE_ID
    ):
        raise ValueError(
            "production and machine outputs require the approved default "
            "signal-processing profile"
        )
    if raw_workers < 1:
        raise ValueError("raw_workers must be >= 1")
    if raw_xic_batch_size < 1:
        raise ValueError("raw_xic_batch_size must be >= 1")
    if owner_backfill_superwindow_span_factor < 1:
        raise ValueError("owner_backfill_superwindow_span_factor must be >= 1")
    if emit_early_loser_shadow and preconsolidate_owner_families:
        raise ValueError(
            "early-loser shadow cannot run with activated pre-Backfill consolidation"
        )
    if emit_early_loser_shadow and backfill_scope != "full-audit":
        raise ValueError("early-loser shadow requires full-audit Backfill scope")
    if emit_provisional_matrix and not require_discovery_owner_closure:
        raise ValueError("provisional matrix emission requires Discovery owner closure")
    if emit_provisional_matrix and backfill_scope != "full-audit":
        raise ValueError("provisional matrix emission requires full-audit Backfill")
    if emit_provisional_matrix and (
        selected_family_ids or selected_family_source.strip()
    ):
        raise ValueError(
            "provisional matrix emission does not allow a family selection"
        )
    if require_discovery_owner_closure and raw_opener is not None:
        raise ValueError("Discovery owner closure does not allow a custom RAW opener")
    if (
        require_discovery_owner_closure
        and emit_identity_coherence_diagnostic
        and raw_workers > 1
    ):
        raise ValueError(
            "owner-closed identity coherence diagnostics require raw_workers=1"
        )
    if backfill_scope == "production-equivalent":
        if not default_signal_processing_profile:
            raise ValueError(
                "production-equivalent Backfill requires the approved default "
                "signal-processing profile"
            )
        if family_selection_requested:
            raise ValueError(
                "production-equivalent Backfill does not allow a family selection"
            )
        require_product_quantitative_projection_authority(
            consumer_id=(
                "xic_extractor.alignment.pipeline.run_alignment:production-equivalent"
            ),
            signal_processing_profile_id=signal_processing_profile.profile_id,
        )
    elif backfill_scope == "full-audit":
        if family_selection_requested:
            raise ValueError(
                "full-audit Backfill does not allow a family selection; "
                "use selected-families"
            )
        if default_signal_processing_profile:
            load_quantitative_projection_authority_for_diagnostic()
    elif backfill_scope != "selected-families":
        raise ValueError(
            "backfill scope must be full-audit, production-equivalent, "
            "or selected-families"
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
            "emit_early_loser_shadow": emit_early_loser_shadow,
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
            candidates_by_sample = {
                sample_stem: read_owner_bound_discovery_candidates_csv(
                    batch.candidate_csvs[sample_stem],
                    receipt=_required_candidate_receipt(batch, sample_stem),
                    require_successor_columns=True,
                )
                for sample_stem in batch.sample_order
            }
        else:
            candidates_by_sample = {
                sample_stem: read_discovery_candidates_csv(
                    batch.candidate_csvs[sample_stem]
                )
                for sample_stem in batch.sample_order
            }
        candidates = tuple(
            candidate
            for sample_stem in batch.sample_order
            for candidate in candidates_by_sample[sample_stem]
        )
        stage.metrics["candidate_count"] = len(candidates)
    if discovery_physical_bindings_by_sample is None:
        discovery_physical_bindings = None
        owner_build_route_receipt = _mint_owner_build_route_receipt(
            configured_backend=owner_build_xic_backend,
        )
    else:
        assert discovery_processing_run_spec is not None
        discovery_physical_bindings = _ordered_discovery_physical_bindings(
            sample_order=batch.sample_order,
            candidates_by_sample=candidates_by_sample,
            candidate_receipts=batch.candidate_receipts,
            run_spec=discovery_processing_run_spec,
            expected_profile_id=signal_processing_profile.profile_id,
            bindings_by_sample=discovery_physical_bindings_by_sample,
        )
        owner_build_route_receipt = _mint_owner_build_route_receipt(
            configured_backend=owner_build_xic_backend,
            processing_run_spec_id=discovery_processing_run_spec.run_spec_id,
            processing_run_spec_hash=discovery_processing_run_spec.run_spec_hash,
        )
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
        emit_early_loser_shadow=emit_early_loser_shadow,
    )
    _reject_alignment_output_collisions(
        output_dir=output_dir,
        outputs=outputs,
        emit_identity_coherence_diagnostic=emit_identity_coherence_diagnostic,
        identity_coherence_output_dir=identity_coherence_output_dir,
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
    if discovery_physical_bindings is not None and emit_cell_region_audit:
        raise ValueError("Discovery physical bindings require audit_evidence_mode=none")
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
            if discovery_physical_bindings is not None:
                ownership = build_sample_local_owners_from_discovery_bindings(
                    discovery_physical_bindings,
                    alignment_config=alignment_config,
                )
            elif raw_workers > 1:
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
        injection_order: dict[str, int] | None = None
        injection_order_source_schema: str | None = None
        if sample_column_injection_order is not None:
            injection_order, injection_order_source_schema = (
                _read_sample_column_injection_order(sample_column_injection_order)
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
                    sample_universe=frozenset(batch.sample_order),
                    injection_order=injection_order,
                )
            else:
                owner_features = cluster_sample_local_owners(
                    ownership.owners,
                    config=alignment_config,
                    sample_universe=frozenset(batch.sample_order),
                    injection_order=injection_order,
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
        early_loser_shadow_proposal: EarlyLoserShadowProposal | None = None
        if emit_early_loser_shadow:
            with recorder.stage("alignment.early_loser_shadow.proposal") as stage:
                early_loser_shadow_proposal = build_early_loser_shadow_proposal(
                    owner_features,
                    run_id=recorder.run_id,
                    sample_order=batch.sample_order,
                    raw_sample_stems=frozenset(raw_paths),
                    alignment_config=alignment_config,
                )
                stage.metrics.update(
                    {
                        "source_feature_count": len(early_loser_shadow_proposal.rows),
                        "proposed_loser_count": sum(
                            row.proposal_status == "loser"
                            for row in early_loser_shadow_proposal.rows
                        ),
                        "proposed_unresolved_count": sum(
                            row.proposal_status == "unresolved"
                            for row in early_loser_shadow_proposal.rows
                        ),
                    }
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
            owner_backfill_peak_fact_observations: tuple[
                OwnerBackfillPeakFactObservation,
                ...,
            ] = ()
            request_reuse_observations: tuple[
                OwnerBackfillRequestReuseObservation,
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
                    signal_processing_profile=signal_processing_profile,
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
                request_reuse_observations = process_output.request_reuse_observations
                owner_backfill_peak_fact_observations = (
                    process_output.peak_fact_observations
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
                    expected_acquisition_sources=expected_acquisition_sources,
                    alignment_config=alignment_config,
                    peak_config=peak_config,
                    signal_processing_profile=signal_processing_profile,
                    raw_xic_batch_size=raw_xic_batch_size,
                    owner_backfill_xic_backend=owner_backfill_xic_backend,
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
                request_reuse_observations = backfill_result.request_reuse_observations
                owner_backfill_peak_fact_observations = (
                    backfill_result.peak_fact_observations
                )
                owner_backfill_stage.metrics.update(
                    _summarize_xic_timing_stats(timing_stats),
                )
                record_raw_source_timing_stats(timing_stats, recorder=recorder)
            owner_backfill_stage.metrics.update(
                _request_reuse_summary(request_reuse_observations)
            )
        matrix_sample_order = batch.sample_order
        if injection_order is not None:
            matrix_sample_order = order_sample_columns_by_injection(
                batch.sample_order,
                injection_order,
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
        production_decisions = build_production_decisions(matrix, alignment_config)
        peak_fact_provenance = _owner_backfill_peak_fact_provenance(
            owner_backfill_peak_fact_observations,
            matrix=matrix,
            alignment_config=alignment_config,
            production_decisions=production_decisions,
            feature_order=tuple(
                feature_id
                for feature in owner_features
                if (feature_id := getattr(feature, "feature_family_id", None))
            ),
            sample_order=matrix_sample_order,
            signal_processing_profile=signal_processing_profile,
        )
        early_loser_shadow_report: EarlyLoserShadowReport | None = None
        if early_loser_shadow_proposal is not None:
            with recorder.stage("alignment.early_loser_shadow.compare") as stage:
                early_loser_shadow_report = (
                    compare_early_loser_shadow_with_final_authority(
                        early_loser_shadow_proposal,
                        matrix,
                    )
                )
                stage.metrics.update(
                    {
                        "verdict": early_loser_shadow_report.verdict,
                        "proposed_loser_count": (
                            early_loser_shadow_report.proposed_loser_count
                        ),
                        "confirmed_loser_count": (
                            early_loser_shadow_report.confirmed_loser_count
                        ),
                        "false_loser_count": (
                            early_loser_shadow_report.false_loser_count
                        ),
                        "winner_mismatch_count": (
                            early_loser_shadow_report.winner_mismatch_count
                        ),
                        "late_loser_count": (
                            early_loser_shadow_report.late_loser_count
                        ),
                        "activation_allowed": False,
                    }
                )
        with recorder.stage("alignment.write_outputs"):
            artifact_fingerprint_batch = _write_outputs_atomic(
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
                        if (
                            backfill_scope == "selected-families"
                            or signal_processing_profile.profile_id
                            != DEFAULT_SIGNAL_PROCESSING_PROFILE_ID
                        )
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
                        else (
                            "diagnostic_only_unapproved_signal_processing_profile"
                            if signal_processing_profile.profile_id
                            != DEFAULT_SIGNAL_PROCESSING_PROFILE_ID
                            else ""
                        )
                    ),
                    skipped_evidence_predicate_version=PREDICATE_VERSION,
                    require_discovery_owner_closure=(require_discovery_owner_closure),
                    signal_processing_profile=signal_processing_profile,
                    owner_backfill_peak_fact_provenance_row_count=len(
                        peak_fact_provenance.rows
                    ),
                ),
                ownership=ownership,
                alignment_config=alignment_config,
                edge_evidence=edge_evidence or (),
                skipped_evidence=scope_selection.skipped,
                owner_backfill_candidate_audit_rows=(
                    owner_backfill_candidate_audit_rows
                ),
                owner_backfill_peak_fact_provenance=peak_fact_provenance,
                production_decisions=production_decisions,
                baseline_integration_method=getattr(
                    peak_config,
                    "baseline_integration_method",
                    "asls",
                ),
                baseline_audit_method=getattr(peak_config, "baseline_audit_method", ""),
                timing_recorder=recorder,
                early_loser_shadow=early_loser_shadow_report,
                capture_artifact_fingerprints=capture_artifact_fingerprints,
            )
        if capture_artifact_fingerprints:
            if not isinstance(
                artifact_fingerprint_batch,
                AlignmentArtifactFingerprintBatch,
            ):
                raise TypeError(
                    "Alignment writer must return an owner-minted fingerprint batch"
                )
            outputs = replace(
                outputs,
                artifact_fingerprint_batch=artifact_fingerprint_batch,
            )
        return replace(
            outputs,
            owner_build_route_receipt=owner_build_route_receipt,
        )


_existing_raw_paths = existing_raw_paths
_output_paths = output_paths
_write_outputs_atomic = write_outputs_atomic
_DEFAULT_BUILD_OWNER_BACKFILL_CELLS = build_owner_backfill_cells

_IDENTITY_COHERENCE_ARTIFACT_NAMES = (
    "untargeted_identity_coherence_requests.tsv",
    "untargeted_identity_coherence_decisions.tsv",
    "untargeted_identity_coherence_cell_evidence.tsv",
    "untargeted_identity_coherence_controls.tsv",
    "untargeted_identity_coherence_summary.md",
)
_CONDITIONAL_ALIGNMENT_ARTIFACT_NAMES = (
    "alignment_cell_integration_audit.tsv",
    "alignment_owner_backfill_seed_audit.tsv",
    "alignment_owner_backfill_candidate_audit.tsv",
    "skipped_evidence_ledger.tsv",
    "alignment_run_metadata.json",
    "provisional_alignment_matrix.tsv",
    "provisional_row_identity.tsv",
    "provisional_cell_provenance.tsv",
    "provisional_matrix_summary.json",
    "alignment_early_loser_shadow.json",
)


def _reject_alignment_output_collisions(
    *,
    output_dir: Path,
    outputs: AlignmentRunOutputs,
    emit_identity_coherence_diagnostic: bool,
    identity_coherence_output_dir: Path | None,
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
        path.name
        for path in vars(outputs).values()
        if isinstance(path, Path) and path.parent == output_dir
    )
    collisions = _owned_path_collisions(
        output_dir / name for name in sorted(owned_names)
    )
    if emit_identity_coherence_diagnostic:
        identity_dir = (
            identity_coherence_output_dir or output_dir / "identity_coherence"
        )
        collisions.extend(
            _owned_path_collisions(
                identity_dir / name for name in _IDENTITY_COHERENCE_ARTIFACT_NAMES
            )
        )
    if collisions:
        names = ", ".join(path.name for path in sorted(collisions))
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


def _expected_acquisition_sources(
    batch: DiscoveryBatchInput,
) -> dict[str, AcquisitionSourceIdentity]:
    return {
        sample_stem: _required_candidate_receipt(batch, sample_stem).acquisition_source
        for sample_stem in batch.sample_order
    }


def _build_owner_backfill_result(*args: Any, **kwargs: Any) -> OwnerBackfillResult:
    if build_owner_backfill_cells is not _DEFAULT_BUILD_OWNER_BACKFILL_CELLS:
        legacy_kwargs = dict(kwargs)
        legacy_kwargs.pop("expected_acquisition_sources", None)
        legacy_kwargs.pop("owner_backfill_xic_backend", None)
        legacy_kwargs.pop("signal_processing_profile", None)
        return OwnerBackfillResult(
            cells=tuple(build_owner_backfill_cells(*args, **legacy_kwargs)),
            candidate_audit_rows=(),
        )
    return build_owner_backfill_result(*args, **kwargs)


def _owner_backfill_peak_fact_provenance(
    observations: Iterable[OwnerBackfillPeakFactObservation],
    *,
    matrix,
    alignment_config: AlignmentConfig,
    production_decisions: ProductionDecisionSet,
    feature_order: tuple[str, ...],
    sample_order: tuple[str, ...],
    signal_processing_profile: SignalProcessingProfile,
):
    feature_rank = {feature_id: index for index, feature_id in enumerate(feature_order)}
    sample_rank = {sample_stem: index for index, sample_stem in enumerate(sample_order)}
    final_cells: dict[tuple[str, str, str], AlignedCell] = {}
    rescued_cells_by_integration: dict[tuple[str, str], list[AlignedCell]] = {}
    rescued_cells_by_source_group: dict[tuple[str, str], list[AlignedCell]] = {}
    for cell in matrix.cells:
        key = (cell.group_hypothesis_id, cell.public_family_id, cell.sample_stem)
        if key in final_cells:
            raise ValueError("final Matrix contains duplicate owner cell identity")
        final_cells[key] = cell
        if (
            cell.status != "rescued"
            or not production_decisions.cell(
                cell.cluster_id,
                cell.sample_stem,
            ).write_matrix_value
        ):
            continue
        integration_id = selected_integration_authority_projection(
            cell.selected_integration
        ).selected_integration_authority_content_id
        rescued_cells_by_integration.setdefault(
            (cell.sample_stem, integration_id),
            [],
        ).append(cell)
        for source_group_id in {
            cell.group_hypothesis_id,
            cell.consolidation_source_group_hypothesis_id,
        }:
            if source_group_id:
                rescued_cells_by_source_group.setdefault(
                    (cell.sample_stem, source_group_id),
                    [],
                ).append(cell)
    projected: list[ProvenanceObservation] = []
    selected: list[
        tuple[
            OwnerBackfillPeakFactObservation,
            OwnerBackfillPeakFactStageProjection,
        ]
    ] = []
    for observation in observations:
        evaluation = observation.physical_evaluation
        if evaluation is None:
            raise ValueError(
                "covered Untargeted provenance requires canonical physical evaluation"
            )
        stage = owner_backfill_peak_fact_stage_projection_from_observation(
            observation,
            feature_order_index=feature_rank.get(
                observation.cell.cluster_id,
                len(feature_rank),
            ),
            sample_order_index=sample_rank.get(
                observation.cell.sample_stem,
                len(sample_rank),
            ),
        )
        projected.append(ProvenanceObservation(stage, evaluation))
        if observation.selected_for_output:
            selected.append((observation, stage))

    cell_projections: list[OwnerBackfillCellProjectionV2] = []
    selected_cell_keys: set[tuple[str, str, str]] = set()
    for observation, stage in selected:
        evaluation = observation.physical_evaluation
        if evaluation is None:
            raise ValueError(
                "selected Untargeted provenance requires canonical physical evaluation"
            )
        facts = evaluation.facts
        if facts is None or evaluation.answer.physical_segment_id is None:
            continue
        if observation.facts is None:
            raise ValueError("selected physical fact lacks bounded owner receipt")
        selected_integration_id = selected_integration_authority_projection(
            observation.facts.integration
        ).selected_integration_authority_content_id
        integration_candidates = rescued_cells_by_integration.get(
            (stage.sample_stem, selected_integration_id),
            (),
        )
        lineage_candidates = tuple(
            cell
            for cell in rescued_cells_by_source_group.get(
                (stage.sample_stem, stage.group_hypothesis_id),
                (),
            )
            if owner_backfill_cell_lineage_matches(stage, cell)
        )
        candidates = tuple(
            cell
            for cell in integration_candidates
            if owner_backfill_cell_lineage_matches(stage, cell)
        )
        if not candidates:
            if lineage_candidates:
                raise ValueError("selected work unit has no exact final Matrix cell")
            continue
        if len(candidates) != 1:
            raise ValueError("selected work unit maps to multiple final Matrix cells")
        final_cell = candidates[0]
        key = (
            final_cell.group_hypothesis_id,
            final_cell.public_family_id,
            final_cell.sample_stem,
        )
        decision = production_decisions.cell(
            final_cell.cluster_id,
            final_cell.sample_stem,
        )
        if not decision.write_matrix_value:
            continue
        if key in selected_cell_keys:
            raise ValueError("multiple selected work units claim one final Matrix cell")
        authority_cell = owner_backfill_authority_cell_projection(
            stage=stage,
            cell=final_cell,
            profile_id=signal_processing_profile.profile_id,
            selected_fact_content_id=facts.fact_content_id,
            physical_answer_id=evaluation.answer.physical_answer_id,
            physical_segment_id=evaluation.answer.physical_segment_id,
        )
        decision_projection = production_cell_decision_projection(
            decision,
            cell=final_cell,
            config=alignment_config,
            owner_backfill_cell_content_id=(
                authority_cell.owner_backfill_cell_content_id
            ),
        )
        cell_projections.append(
            owner_backfill_cell_projection(
                authority_cell=authority_cell,
                production_decision=decision_projection,
            )
        )
        selected_cell_keys.add(key)
    expected_written_cell_keys = {
        (cell.group_hypothesis_id, cell.public_family_id, cell.sample_stem)
        for cell in matrix.cells
        if cell.status == "rescued"
        and production_decisions.cell(
            cell.cluster_id,
            cell.sample_stem,
        ).write_matrix_value
    }
    if selected_cell_keys != expected_written_cell_keys:
        missing = tuple(sorted(expected_written_cell_keys - selected_cell_keys))
        unexpected = tuple(sorted(selected_cell_keys - expected_written_cell_keys))
        raise ValueError(
            "final written rescued cells require exact provenance coverage; "
            f"missing={missing!r}; unexpected={unexpected!r}"
        )
    return build_owner_backfill_peak_fact_provenance_batch(
        projected,
        profile=signal_processing_profile,
        cell_projections=cell_projections,
    )


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
    metrics.update(_request_reuse_summary(result.request_reuse_observations))
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
        int(getattr(stats, "raw_chromatogram_call_count", 0)) for stats in stats_items
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


def _request_reuse_summary(
    observations: Iterable[OwnerBackfillRequestReuseObservation],
) -> dict[str, int | bool]:
    items = tuple(observations)
    if not items:
        return {}
    logical_request_count = sum(item.logical_request_count for item in items)
    canonical_request_class_count = sum(
        item.canonical_request_class_count for item in items
    )
    return {
        "exact_request_equivalence_available": True,
        "logical_request_count": logical_request_count,
        "canonical_request_class_count": canonical_request_class_count,
        "equivalent_logical_request_count": (
            logical_request_count - canonical_request_class_count
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
