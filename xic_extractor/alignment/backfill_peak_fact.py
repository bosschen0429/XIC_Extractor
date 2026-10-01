"""Backfill peak fact search and result composition."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TypeAlias

from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillCandidatePhase,
    BackfillEvaluationCoverageContext,
    BackfillPlanningPosition,
    BackfillTerminalReason,
    build_backfill_evaluation_coverage,
    build_backfill_planning_epoch,
    mint_backfill_attempt_terminal,
    mint_backfill_selection_subject_id,
)
from xic_extractor.alignment.backfill_acquisition import (
    _available_acquisition_sources,
    _iter_extracted_request_traces,
    _logical_binding_for_result,
    _request_equivalence_context,
    _trace_for_logical_request_result,
    _validate_owner_bound_sources,
)
from xic_extractor.alignment.backfill_evaluation import (
    _backfill_feature_sample_trace,
    _backfill_unchecked_cell,
    _chunk_prepared_signal_cache,
    _keep_best_backfill_outcome_cell,
)
from xic_extractor.alignment.backfill_request_plan import (
    BACKFILL_TRACE_RESULT_SEMANTICS,
    BACKFILL_WINDOW_STRATEGIES,
    BACKFILL_XIC_BACKENDS,
    BackfillLogicalRequestMember,
    BackfillRequestIdentity,
    BackfillRequestItem,
    BackfillValidationMode,
    BackfillWindowStrategy,
    build_backfill_request_equivalence_classes,
    build_backfill_request_plan,
)
from xic_extractor.alignment.backfill_result import (
    _BACKFILL_PEAK_FACT_OBSERVATION_MINT_TOKEN,
    BackfillCandidateAuditRow,
    BackfillExtractionResult,  # noqa: F401
    BackfillLogicalExtractionResult,  # noqa: F401
    BackfillPeakFactObservation,
    BackfillPeakFactReceipt,
    BackfillRequestReuseObservation,
    BackfillResult,
    BackfillSource,
    _BackfillCandidateRecord,
    _BackfillCoverageAttempt,
    validate_backfill_peak_fact_observation,  # noqa: F401
)
from xic_extractor.alignment.backfill_runtime_projection import (
    BackfillRuntimeFeature,
)
from xic_extractor.alignment.config import AlignmentConfig, BackfillXicBackend
from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.evidence_facts import (
    PeakTraceEvidenceFacts,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
    PreparedSignalResult,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    resolve_request_acquisition_source,
)
from xic_extractor.xic_models import XICTrace

_RequestItem = BackfillRequestItem
# One extraction chunk's prepared signals, keyed by the identity of the trace
# object request equivalence fanned out.  The cache holds the trace itself so a
# recycled id() can never alias a different trace.
_ChunkPreparedSignalCache: TypeAlias = dict[int, tuple[XICTrace, PreparedSignalResult]]
_RequestGroupKey = tuple[str, int | float, int | float]


def build_backfill_result(
    features: tuple[BackfillRuntimeFeature, ...],
    *,
    sample_order: tuple[str, ...],
    raw_sources: Mapping[str, BackfillSource],
    validation_raw_sources: Mapping[str, BackfillSource] | None = None,
    expected_acquisition_sources: Mapping[str, AcquisitionSourceIdentity] | None = None,
    alignment_config: AlignmentConfig,
    peak_config: ExtractionConfig,
    signal_processing_profile: SignalProcessingProfile | None = None,
    raw_xic_batch_size: int = 1,
    owner_backfill_xic_backend: BackfillXicBackend = "raw",
    owner_backfill_window_strategy: BackfillWindowStrategy = "exact",
    owner_backfill_superwindow_span_factor: int = 2,
    candidate_index_offset: int = 0,
    validation_candidate_index_offset: int | None = None,
    audit_evidence_mode: str = "full",
    emit_candidate_audit_rows: bool = True,
    evaluation_coverage_context: BackfillEvaluationCoverageContext | None = None,
    evaluation_coverage_positions: tuple[BackfillPlanningPosition, ...] = (),
) -> BackfillResult:
    if evaluation_coverage_context is not None and evaluation_coverage_positions:
        raise ValueError("coverage context and worker positions are mutually exclusive")
    if evaluation_coverage_context is not None:
        if (
            evaluation_coverage_context.sample_order != sample_order
            or evaluation_coverage_context.raw_sample_stems != frozenset(raw_sources)
            or evaluation_coverage_context.alignment_config != alignment_config
        ):
            raise ValueError("serial Backfill crossed its evaluation coverage epoch")
        if expected_acquisition_sources is None:
            raise ValueError("evaluation coverage requires acquisition identities")
        for sample in evaluation_coverage_context.samples:
            if sample.lookup_token not in evaluation_coverage_context.raw_sample_stems:
                continue
            if expected_acquisition_sources.get(sample.lookup_token) != (
                resolve_request_acquisition_source(
                    processing_owners=evaluation_coverage_context.processing_owners,
                    sample=sample,
                )
            ):
                raise ValueError(
                    "evaluation coverage crossed its request acquisition source"
                )
    if signal_processing_profile is not None and not isinstance(
        signal_processing_profile,
        SignalProcessingProfile,
    ):
        raise TypeError("signal_processing_profile must be a SignalProcessingProfile")
    if raw_xic_batch_size < 1:
        raise ValueError("raw_xic_batch_size must be >= 1")
    if owner_backfill_superwindow_span_factor < 1:
        raise ValueError("owner_backfill_superwindow_span_factor must be >= 1")
    if (
        isinstance(candidate_index_offset, bool)
        or not isinstance(candidate_index_offset, int)
        or candidate_index_offset < 0
    ):
        raise ValueError("candidate_index_offset must be a non-negative integer")
    if owner_backfill_window_strategy not in BACKFILL_WINDOW_STRATEGIES:
        raise ValueError(
            "owner_backfill_window_strategy must be exact or super-window",
        )
    if owner_backfill_xic_backend not in BACKFILL_XIC_BACKENDS:
        raise ValueError("unsupported owner Backfill XIC backend")
    _validate_owner_bound_sources(
        raw_sources,
        expected_acquisition_sources=expected_acquisition_sources,
        source_label="primary",
    )
    if validation_raw_sources is not None:
        _validate_owner_bound_sources(
            validation_raw_sources,
            expected_acquisition_sources=expected_acquisition_sources,
            source_label="validation",
        )
    cells: list[AlignedCell] = []
    candidate_records: list[_BackfillCandidateRecord] = []
    request_reuse_observations: list[BackfillRequestReuseObservation] = []
    primary_validation_mode: BackfillValidationMode = (
        "prefilter" if validation_raw_sources is not None else "primary"
    )
    if evaluation_coverage_context is not None and (
        evaluation_coverage_context.backend != owner_backfill_xic_backend
        or evaluation_coverage_context.window_strategy != owner_backfill_window_strategy
        or evaluation_coverage_context.superwindow_span_factor
        != owner_backfill_superwindow_span_factor
        or evaluation_coverage_context.validation_mode != primary_validation_mode
        or evaluation_coverage_context.result_semantics
        != BACKFILL_TRACE_RESULT_SEMANTICS
    ):
        raise ValueError("serial Backfill crossed its evaluation request semantics")
    effective_acquisition_sources = expected_acquisition_sources or (
        _available_acquisition_sources(raw_sources)
    )
    primary_equivalence_context = _request_equivalence_context(
        expected_acquisition_sources=effective_acquisition_sources,
        backend=owner_backfill_xic_backend,
        window_strategy=owner_backfill_window_strategy,
        superwindow_span_factor=owner_backfill_superwindow_span_factor,
        validation_mode=primary_validation_mode,
    )
    request_plan = build_backfill_request_plan(
        features,
        sample_order=sample_order,
        raw_sample_stems=frozenset(raw_sources),
        alignment_config=alignment_config,
        equivalence_context=primary_equivalence_context,
    )
    evaluation_epoch = (
        build_backfill_planning_epoch(
            evaluation_coverage_context,
            selected_features=features,
            request_plan=request_plan,
        )
        if evaluation_coverage_context is not None
        else None
    )
    active_coverage_positions = (
        evaluation_epoch.positions
        if evaluation_epoch is not None
        else evaluation_coverage_positions
    )
    coverage_position_by_key = {
        (
            position.group_identity.group_hypothesis_id,
            position.sample.lookup_token,
        ): position
        for position in active_coverage_positions
        if position.planning_state == "planned"
    }
    coverage_attempts: list[_BackfillCoverageAttempt] = []
    candidate_index_offset_by_sample: dict[str, int] = {}
    validation_candidate_index_offset_by_sample: dict[str, int] = {}
    # Direct owner calls keep the historical append-order identity.  Process
    # jobs pass explicit offsets so each sample can mint collision-free local
    # identities without seeing another sample's candidate records.
    partition_candidate_identity = (
        candidate_index_offset != 0 or validation_candidate_index_offset is not None
    )
    total_logical_request_count = sum(
        request_plan.request_count_for_sample(sample_stem)
        for sample_stem in sample_order
    )
    if validation_candidate_index_offset is None:
        validation_candidate_index_offset = (
            candidate_index_offset + total_logical_request_count
        )
    elif (
        isinstance(validation_candidate_index_offset, bool)
        or not isinstance(validation_candidate_index_offset, int)
        or validation_candidate_index_offset
        < candidate_index_offset + total_logical_request_count
    ):
        raise ValueError(
            "validation candidate index offset must follow the primary request range"
        )
    logical_request_offset = candidate_index_offset
    for sample_stem in sample_order:
        sample_requests = request_plan.requests_for_sample(sample_stem)
        candidate_index_offset_by_sample[sample_stem] = logical_request_offset
        validation_candidate_index_offset_by_sample[sample_stem] = (
            validation_candidate_index_offset
            + logical_request_offset
            - candidate_index_offset
        )
        logical_request_offset += len(sample_requests)
    backfill_by_feature_sample: dict[tuple[str, str], AlignedCell] = {}
    validation_pending: dict[str, list[_RequestItem]] = defaultdict(list)
    validation_pending_keys: set[tuple[str, str]] = set()
    for sample_stem in sample_order:
        sample_requests = request_plan.requests_for_sample(sample_stem)
        if not sample_requests:
            continue
        sample_candidate_index_offset = candidate_index_offset_by_sample[sample_stem]
        equivalence_classes = (
            request_plan.equivalence_classes_for_sample(sample_stem)
            if request_plan.exact_equivalence_available
            else None
        )
        if equivalence_classes is not None:
            request_reuse_observations.append(
                BackfillRequestReuseObservation(
                    sample_stem=sample_stem,
                    validation_mode=primary_validation_mode,
                    logical_request_count=len(sample_requests),
                    canonical_request_class_count=len(equivalence_classes),
                )
            )
        source = raw_sources[sample_stem]
        for chunk, traces in _iter_extracted_request_traces(
            source,
            tuple(sample_requests),
            raw_xic_batch_size,
            window_strategy=owner_backfill_window_strategy,
            superwindow_span_factor=owner_backfill_superwindow_span_factor,
            equivalence_classes=equivalence_classes,
        ):
            prepared_signal_cache = _chunk_prepared_signal_cache()
            for request_item, result in zip(chunk, traces, strict=True):
                feature, requested_sample, request, preferred_rt = request_item
                logical_request, request_identity = _logical_binding_for_result(result)
                trace = _trace_for_logical_request_result(
                    BackfillRequestItem(
                        feature,
                        requested_sample,
                        request,
                        preferred_rt,
                    ),
                    result,
                )
                candidate_phase: BackfillCandidatePhase = (
                    "prefilter_query"
                    if validation_raw_sources is not None
                    else "primary_query"
                )
                candidate_index = (
                    sample_candidate_index_offset + len(candidate_records) + 1
                    if partition_candidate_identity
                    else len(candidate_records) + 1
                )
                evaluation = _backfill_feature_sample_trace(
                    feature,
                    requested_sample,
                    trace,
                    request=request,
                    preferred_rt=preferred_rt,
                    peak_config=peak_config,
                    maximum_query_delta_min=(alignment_config.preferred_rt_sec / 60.0),
                    signal_processing_profile=signal_processing_profile,
                    prepared_signal_cache=prepared_signal_cache,
                    selection_subject_id=mint_backfill_selection_subject_id(
                        group_hypothesis_id=feature.group_hypothesis_id,
                        sample_stem=requested_sample,
                        request=request,
                        candidate_index=candidate_index,
                        candidate_phase=candidate_phase,
                        request_identity=request_identity,
                    ),
                    require_sealed_facts=logical_request is not None,
                )
                cell = evaluation.cell
                _append_candidate_record(
                    candidate_records,
                    cell,
                    phase=candidate_phase,
                    candidate_index=candidate_index,
                    trace_evaluated=True,
                    peak_facts=evaluation.peak_facts,
                    peak_fact_elapsed_seconds=evaluation.peak_fact_elapsed_seconds,
                    physical_evaluation=evaluation.physical_evaluation,
                    trace_rt_start_min=evaluation.trace_rt_start_min,
                    trace_rt_end_min=evaluation.trace_rt_end_min,
                    logical_request=logical_request,
                    request_identity=request_identity,
                )
                _append_coverage_attempt(
                    coverage_attempts,
                    coverage_position_by_key=coverage_position_by_key,
                    cell=cell,
                    logical_request=logical_request,
                    request_identity=request_identity,
                    candidate_phase=candidate_phase,
                    candidate_index=candidate_index,
                    trace_evaluated=True,
                    terminal_reason=None,
                    physical_evaluation=evaluation.physical_evaluation,
                )
                if validation_raw_sources is None or cell.status != "backfilled":
                    _keep_best_backfill_outcome_cell(
                        backfill_by_feature_sample,
                        cell,
                    )
                else:
                    validation_pending[requested_sample].append(request_item)
                    validation_pending_keys.add(
                        (feature.feature_family_id, requested_sample)
                    )
        for request_item in sample_requests:
            feature, requested_sample, request, preferred_rt = request_item
            key = (feature.feature_family_id, requested_sample)
            if (
                key not in backfill_by_feature_sample
                and key not in validation_pending_keys
            ):
                cell = _backfill_unchecked_cell(
                    feature,
                    requested_sample,
                    request=request,
                    preferred_rt=preferred_rt,
                    trace_quality="owner_backfill_unassessable",
                    reason="owner-centered MS1 backfill query was not assessable",
                )
                _append_candidate_record(
                    candidate_records,
                    cell,
                    phase="primary_fallback",
                    candidate_index=(
                        sample_candidate_index_offset + len(candidate_records) + 1
                        if partition_candidate_identity
                        else len(candidate_records) + 1
                    ),
                )
                _keep_best_backfill_outcome_cell(
                    backfill_by_feature_sample,
                    cell,
                )
        if coverage_position_by_key:
            observed_primary_ids = {
                attempt.logical_request.logical_request_id
                for attempt in coverage_attempts
                if attempt.candidate_phase
                in {"primary_query", "prefilter_query", "primary_fallback"}
            }
            for (
                logical_request,
                request_identity,
            ) in request_plan.logical_bindings_for_sample(sample_stem):
                if logical_request.logical_request_id in observed_primary_ids:
                    continue
                feature, requested_sample, request, preferred_rt = logical_request.item
                fallback_cell = _backfill_unchecked_cell(
                    feature,
                    requested_sample,
                    request=request,
                    preferred_rt=preferred_rt,
                    trace_quality="owner_backfill_unassessable",
                    reason="owner-centered MS1 backfill query was not assessable",
                )
                _append_coverage_attempt(
                    coverage_attempts,
                    coverage_position_by_key=coverage_position_by_key,
                    cell=fallback_cell,
                    logical_request=logical_request,
                    request_identity=request_identity,
                    candidate_phase="primary_fallback",
                    candidate_index=(
                        candidate_index_offset_by_sample[sample_stem]
                        + logical_request.logical_index
                        + 1
                    ),
                    trace_evaluated=False,
                    terminal_reason="primary_unassessable",
                    physical_evaluation=None,
                )
    if validation_raw_sources is not None:
        for sample_stem in sample_order:
            validation_requests = validation_pending.get(sample_stem, [])
            if not validation_requests:
                continue
            coverage_validation_bindings: tuple[
                tuple[
                    BackfillLogicalRequestMember,
                    BackfillRequestIdentity,
                ],
                ...,
            ] = ()
            if coverage_position_by_key:
                coverage_validation_context = _request_equivalence_context(
                    expected_acquisition_sources=expected_acquisition_sources,
                    backend="raw",
                    window_strategy="exact",
                    superwindow_span_factor=(owner_backfill_superwindow_span_factor),
                    validation_mode="validation",
                )
                if coverage_validation_context is None:
                    raise ValueError(
                        "evaluation coverage requires validation acquisition identity"
                    )
                coverage_validation_classes = (
                    build_backfill_request_equivalence_classes(
                        tuple(validation_requests),
                        context=coverage_validation_context,
                    )
                )
                coverage_validation_bindings = tuple(
                    sorted(
                        (
                            (member, equivalence_class.identity)
                            for equivalence_class in coverage_validation_classes
                            for member in equivalence_class.members
                        ),
                        key=lambda item: item[0].logical_index,
                    )
                )
            if sample_stem not in validation_raw_sources:
                sample_candidate_index_offset = (
                    validation_candidate_index_offset_by_sample[sample_stem]
                )
                validation_candidate_count = 0
                for request_index, request_item in enumerate(validation_requests):
                    feature, requested_sample, request, preferred_rt = request_item
                    cell = _backfill_unchecked_cell(
                        feature,
                        requested_sample,
                        request=request,
                        preferred_rt=preferred_rt,
                        trace_quality="owner_backfill_unassessable",
                        reason=(
                            "owner-centered MS1 backfill validation "
                            "source was not available"
                        ),
                    )
                    validation_candidate_count += 1
                    _append_candidate_record(
                        candidate_records,
                        cell,
                        phase="validation_unavailable",
                        candidate_index=(
                            sample_candidate_index_offset + validation_candidate_count
                            if partition_candidate_identity
                            else len(candidate_records) + 1
                        ),
                    )
                    if coverage_validation_bindings:
                        logical_request, request_identity = (
                            coverage_validation_bindings[request_index]
                        )
                        _append_coverage_attempt(
                            coverage_attempts,
                            coverage_position_by_key=coverage_position_by_key,
                            cell=cell,
                            logical_request=logical_request,
                            request_identity=request_identity,
                            candidate_phase="validation_unavailable",
                            candidate_index=(
                                sample_candidate_index_offset
                                + logical_request.logical_index
                                + 1
                            ),
                            trace_evaluated=False,
                            terminal_reason="validation_source_unavailable",
                            physical_evaluation=None,
                        )
                    _keep_best_backfill_outcome_cell(
                        backfill_by_feature_sample,
                        cell,
                    )
                continue
            source = validation_raw_sources[sample_stem]
            sample_candidate_index_offset = validation_candidate_index_offset_by_sample[
                sample_stem
            ]
            validation_candidate_count = 0
            validation_items = tuple(validation_requests)
            validation_context = _request_equivalence_context(
                expected_acquisition_sources=(
                    expected_acquisition_sources
                    or _available_acquisition_sources(validation_raw_sources)
                ),
                backend="raw",
                window_strategy="exact",
                superwindow_span_factor=owner_backfill_superwindow_span_factor,
                validation_mode="validation",
            )
            validation_classes = (
                build_backfill_request_equivalence_classes(
                    validation_items,
                    context=validation_context,
                )
                if validation_context is not None
                else None
            )
            if validation_classes is not None:
                request_reuse_observations.append(
                    BackfillRequestReuseObservation(
                        sample_stem=sample_stem,
                        validation_mode="validation",
                        logical_request_count=len(validation_items),
                        canonical_request_class_count=len(validation_classes),
                    )
                )
            for chunk, traces in _iter_extracted_request_traces(
                source,
                validation_items,
                raw_xic_batch_size,
                window_strategy="exact",
                superwindow_span_factor=owner_backfill_superwindow_span_factor,
                equivalence_classes=validation_classes,
            ):
                prepared_signal_cache = _chunk_prepared_signal_cache()
                for request_item, result in zip(chunk, traces, strict=True):
                    feature, requested_sample, _request, preferred_rt = request_item
                    logical_request, request_identity = _logical_binding_for_result(
                        result
                    )
                    trace = _trace_for_logical_request_result(
                        BackfillRequestItem(
                            feature,
                            requested_sample,
                            _request,
                            preferred_rt,
                        ),
                        result,
                    )
                    validation_candidate_count += 1
                    candidate_index = (
                        sample_candidate_index_offset + validation_candidate_count
                        if partition_candidate_identity
                        else len(candidate_records) + 1
                    )
                    evaluation = _backfill_feature_sample_trace(
                        feature,
                        requested_sample,
                        trace,
                        request=_request,
                        preferred_rt=preferred_rt,
                        peak_config=peak_config,
                        maximum_query_delta_min=(
                            alignment_config.preferred_rt_sec / 60.0
                        ),
                        signal_processing_profile=signal_processing_profile,
                        prepared_signal_cache=prepared_signal_cache,
                        selection_subject_id=mint_backfill_selection_subject_id(
                            group_hypothesis_id=feature.group_hypothesis_id,
                            sample_stem=requested_sample,
                            request=_request,
                            candidate_index=candidate_index,
                            candidate_phase="validation_query",
                            request_identity=request_identity,
                        ),
                        require_sealed_facts=logical_request is not None,
                    )
                    cell = evaluation.cell
                    _append_candidate_record(
                        candidate_records,
                        cell,
                        phase="validation_query",
                        candidate_index=candidate_index,
                        trace_evaluated=True,
                        peak_facts=evaluation.peak_facts,
                        peak_fact_elapsed_seconds=evaluation.peak_fact_elapsed_seconds,
                        physical_evaluation=evaluation.physical_evaluation,
                        trace_rt_start_min=evaluation.trace_rt_start_min,
                        trace_rt_end_min=evaluation.trace_rt_end_min,
                        logical_request=logical_request,
                        request_identity=request_identity,
                    )
                    _append_coverage_attempt(
                        coverage_attempts,
                        coverage_position_by_key=coverage_position_by_key,
                        cell=cell,
                        logical_request=logical_request,
                        request_identity=request_identity,
                        candidate_phase="validation_query",
                        candidate_index=candidate_index,
                        trace_evaluated=True,
                        terminal_reason=None,
                        physical_evaluation=evaluation.physical_evaluation,
                    )
                    _keep_best_backfill_outcome_cell(
                        backfill_by_feature_sample,
                        cell,
                    )
            if coverage_validation_bindings:
                observed_validation_ids = {
                    attempt.logical_request.logical_request_id
                    for attempt in coverage_attempts
                    if attempt.candidate_phase
                    in {
                        "validation_query",
                        "validation_unavailable",
                        "validation_fallback",
                    }
                }
                for logical_request, request_identity in coverage_validation_bindings:
                    if logical_request.logical_request_id in observed_validation_ids:
                        continue
                    feature, requested_sample, request, preferred_rt = (
                        logical_request.item
                    )
                    fallback_cell = _backfill_unchecked_cell(
                        feature,
                        requested_sample,
                        request=request,
                        preferred_rt=preferred_rt,
                        trace_quality="owner_backfill_unassessable",
                        reason=(
                            "owner-centered MS1 backfill validation "
                            "query was not assessable"
                        ),
                    )
                    _append_coverage_attempt(
                        coverage_attempts,
                        coverage_position_by_key=coverage_position_by_key,
                        cell=fallback_cell,
                        logical_request=logical_request,
                        request_identity=request_identity,
                        candidate_phase="validation_fallback",
                        candidate_index=(
                            validation_candidate_index_offset_by_sample[sample_stem]
                            + logical_request.logical_index
                            + 1
                        ),
                        trace_evaluated=False,
                        terminal_reason="validation_unassessable",
                        physical_evaluation=None,
                    )
            for request_item in validation_requests:
                feature, requested_sample, request, preferred_rt = request_item
                key = (feature.feature_family_id, requested_sample)
                if key not in backfill_by_feature_sample:
                    cell = _backfill_unchecked_cell(
                        feature,
                        requested_sample,
                        request=request,
                        preferred_rt=preferred_rt,
                        trace_quality="owner_backfill_unassessable",
                        reason=(
                            "owner-centered MS1 backfill validation "
                            "query was not assessable"
                        ),
                    )
                    validation_candidate_count += 1
                    _append_candidate_record(
                        candidate_records,
                        cell,
                        phase="validation_fallback",
                        candidate_index=(
                            sample_candidate_index_offset + validation_candidate_count
                            if partition_candidate_identity
                            else len(candidate_records) + 1
                        ),
                    )
                    _keep_best_backfill_outcome_cell(
                        backfill_by_feature_sample,
                        cell,
                    )
    for feature in features:
        if feature.review_only:
            continue
        for sample_stem in sample_order:
            selected_cell = backfill_by_feature_sample.get(
                (feature.feature_family_id, sample_stem)
            )
            if selected_cell is not None:
                cells.append(selected_cell)
    selected_cells = tuple(backfill_by_feature_sample.values())
    selected_cell_ids = {id(cell) for cell in selected_cells}
    selected_coverage_attempt_ids: set[int] = set()
    for selected_cell in selected_cells:
        exact_matches = tuple(
            index
            for index, attempt in enumerate(coverage_attempts)
            if attempt.cell is selected_cell
        )
        if exact_matches:
            selected_coverage_attempt_ids.add(exact_matches[0])
            continue
        equivalent_matches = tuple(
            index
            for index, attempt in enumerate(coverage_attempts)
            if attempt.cell == selected_cell
        )
        if equivalent_matches:
            selected_coverage_attempt_ids.add(equivalent_matches[0])
    evaluation_coverage_terminals = tuple(
        mint_backfill_attempt_terminal(
            position_id=attempt.position_id,
            logical_request=attempt.logical_request,
            request_identity=attempt.request_identity,
            candidate_phase=attempt.candidate_phase,
            candidate_index=attempt.candidate_index,
            trace_evaluated=attempt.trace_evaluated,
            selected_for_output=index in selected_coverage_attempt_ids,
            terminal_reason=attempt.terminal_reason,
            physical_evaluation=attempt.physical_evaluation,
        )
        for index, attempt in enumerate(coverage_attempts)
    )
    evaluation_coverage = (
        build_backfill_evaluation_coverage(
            evaluation_epoch,
            evaluation_coverage_terminals,
        )
        if evaluation_epoch is not None
        else None
    )
    peak_fact_observations = _peak_fact_observations(
        candidate_records,
        selected_cell_ids=selected_cell_ids,
    )
    return BackfillResult(
        cells=tuple(cells),
        # These rows only ever reach alignment_owner_backfill_candidate_audit.tsv,
        # which no output level emits by default.  Building and shipping one row
        # per Backfill request when nothing will write them costs the Alignment
        # parent 657 B/request of retention for an artifact it then discards.
        candidate_audit_rows=(
            _candidate_audit_rows(
                candidate_records,
                selected_cell_ids=selected_cell_ids,
                feature_order=tuple(feature.feature_family_id for feature in features),
                sample_order=sample_order,
            )
            if emit_candidate_audit_rows
            else ()
        ),
        request_reuse_observations=tuple(request_reuse_observations),
        peak_fact_observations=peak_fact_observations,
        peak_fact_unsealed_evaluation_keys=(
            _peak_fact_unsealed_evaluation_keys(candidate_records)
        ),
        evaluation_coverage=evaluation_coverage,
        evaluation_coverage_terminals=(
            evaluation_coverage_terminals if evaluation_epoch is None else ()
        ),
    )




def _append_candidate_record(
    records: list[_BackfillCandidateRecord],
    cell: AlignedCell,
    *,
    phase: str,
    candidate_index: int | None = None,
    candidate_index_offset: int = 0,
    trace_evaluated: bool = False,
    peak_facts: PeakTraceEvidenceFacts | BackfillPeakFactReceipt | None = None,
    peak_fact_elapsed_seconds: float | None = None,
    physical_evaluation: PhysicalPeakEvaluation | None = None,
    trace_rt_start_min: float | None = None,
    trace_rt_end_min: float | None = None,
    logical_request: BackfillLogicalRequestMember | None = None,
    request_identity: BackfillRequestIdentity | None = None,
) -> None:
    if (logical_request is None) != (request_identity is None):
        raise ValueError("logical request and request identity must stay paired")
    if candidate_index is None:
        candidate_index = candidate_index_offset + len(records) + 1
    elif (
        isinstance(candidate_index, bool)
        or not isinstance(candidate_index, int)
        or candidate_index < 1
    ):
        raise ValueError("candidate_index must be a positive integer")
    peak_fact_receipt = _bounded_peak_fact_receipt(
        peak_facts,
        physical_evaluation,
    )
    records.append(
        _BackfillCandidateRecord(
            cell=cell,
            candidate_index=candidate_index,
            candidate_phase=phase,
            trace_evaluated=trace_evaluated,
            peak_facts=peak_fact_receipt,
            peak_fact_elapsed_seconds=peak_fact_elapsed_seconds,
            physical_evaluation=physical_evaluation,
            trace_rt_start_min=trace_rt_start_min,
            trace_rt_end_min=trace_rt_end_min,
            logical_request=logical_request,
            request_identity=request_identity,
        )
    )


def _append_coverage_attempt(
    attempts: list[_BackfillCoverageAttempt],
    *,
    coverage_position_by_key: Mapping[
        tuple[str, str],
        BackfillPlanningPosition,
    ],
    cell: AlignedCell,
    logical_request: BackfillLogicalRequestMember | None,
    request_identity: BackfillRequestIdentity | None,
    candidate_phase: BackfillCandidatePhase,
    candidate_index: int,
    trace_evaluated: bool,
    terminal_reason: BackfillTerminalReason | None,
    physical_evaluation: PhysicalPeakEvaluation | None,
) -> None:
    if not coverage_position_by_key:
        return
    if logical_request is None or request_identity is None:
        raise ValueError("evaluation coverage requires sealed logical identity")
    position = coverage_position_by_key.get(
        (
            logical_request.group_hypothesis_id,
            logical_request.item.sample_stem,
        )
    )
    if position is None:
        raise ValueError("evaluation attempt is outside the planning epoch")
    attempts.append(
        _BackfillCoverageAttempt(
            position_id=position.position_id,
            logical_request=logical_request,
            request_identity=request_identity,
            candidate_phase=candidate_phase,
            candidate_index=candidate_index,
            trace_evaluated=trace_evaluated,
            terminal_reason=terminal_reason,
            physical_evaluation=physical_evaluation,
            cell=cell,
        )
    )


def _bounded_peak_fact_receipt(
    facts: PeakTraceEvidenceFacts | BackfillPeakFactReceipt | None,
    physical_evaluation: PhysicalPeakEvaluation | None,
) -> BackfillPeakFactReceipt | None:
    if isinstance(facts, BackfillPeakFactReceipt):
        if facts.physical_evaluation != physical_evaluation:
            raise ValueError("peak fact receipt crosses physical evaluation")
        return facts
    if facts is None:
        return None
    shape = facts.normalized_shape_trace
    return BackfillPeakFactReceipt(
        integration=replace(facts.integration, raw_scan_indices=()),
        physical_evaluation=physical_evaluation,
        normalized_shape_status=shape.status,
        normalized_shape_reason=shape.reason,
        normalized_shape_point_count=len(shape.rt),
        normalized_shape_max_intensity=shape.max_intensity,
        window=facts.window,
        smoothing_count=facts.smoothing_count,
        integration_count=facts.integration_count,
    )


def _peak_fact_observations(
    records: Sequence[_BackfillCandidateRecord],
    *,
    selected_cell_ids: set[int],
) -> tuple[BackfillPeakFactObservation, ...]:
    return tuple(
        BackfillPeakFactObservation(
            logical_request=record.logical_request,
            request_identity=record.request_identity,
            candidate_index=record.candidate_index,
            candidate_phase=record.candidate_phase,
            selected_for_output=id(record.cell) in selected_cell_ids,
            cell=record.cell,
            facts=record.peak_facts,
            physical_evaluation=record.physical_evaluation,
            trace_rt_start_min=record.trace_rt_start_min,
            trace_rt_end_min=record.trace_rt_end_min,
            fact_elapsed_seconds=record.peak_fact_elapsed_seconds,
            trace_evaluated=record.trace_evaluated,
            _mint_token=_BACKFILL_PEAK_FACT_OBSERVATION_MINT_TOKEN,
        )
        for record in records
        if record.logical_request is not None and record.request_identity is not None
    )


def _peak_fact_unsealed_evaluation_keys(
    records: Sequence[_BackfillCandidateRecord],
) -> tuple[str, ...]:
    return tuple(
        (
            f"unsealed:{record.cell.sample_stem}:"
            f"{record.cell.group_hypothesis_id}:"
            f"{record.candidate_index}:{record.candidate_phase}"
        )
        for record in records
        if record.trace_evaluated
        and (record.logical_request is None or record.request_identity is None)
    )


def _candidate_audit_rows(
    records: Sequence[_BackfillCandidateRecord],
    *,
    selected_cell_ids: set[int],
    feature_order: tuple[str, ...],
    sample_order: tuple[str, ...],
) -> tuple[BackfillCandidateAuditRow, ...]:
    feature_rank = {feature_id: index for index, feature_id in enumerate(feature_order)}
    sample_rank = {sample: index for index, sample in enumerate(sample_order)}
    rows = [
        _candidate_audit_row(record, id(record.cell) in selected_cell_ids)
        for record in records
    ]
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                feature_rank.get(row.feature_family_id, len(feature_rank)),
                sample_rank.get(row.sample_stem, len(sample_rank)),
                row.candidate_index,
            ),
        )
    )


def _candidate_audit_row(
    record: _BackfillCandidateRecord,
    selected_for_output: bool,
) -> BackfillCandidateAuditRow:
    cell = record.cell
    return BackfillCandidateAuditRow(
        feature_family_id=cell.cluster_id,
        group_hypothesis_id=cell.group_hypothesis_id,
        public_family_id=cell.public_family_id,
        sample_stem=cell.sample_stem,
        candidate_index=record.candidate_index,
        candidate_phase=record.candidate_phase,
        selected_for_output=selected_for_output,
        candidate_status=cell.status,
        candidate_outcome=_candidate_outcome(cell),
        trace_quality=cell.trace_quality,
        area=cell.area,
        apex_rt=cell.apex_rt,
        peak_start_rt=cell.peak_start_rt,
        peak_end_rt=cell.peak_end_rt,
        rt_delta_sec=cell.rt_delta_sec,
        backfill_seed_mz=cell.backfill_seed_mz,
        backfill_seed_rt=cell.backfill_seed_rt,
        backfill_request_rt_min=cell.backfill_request_rt_min,
        backfill_request_rt_max=cell.backfill_request_rt_max,
        backfill_request_ppm=cell.backfill_request_ppm,
        reason=cell.reason,
        selection_note="selected" if selected_for_output else "not_selected",
    )


def _candidate_outcome(cell: AlignedCell) -> str:
    if cell.status == "backfilled":
        return "detected"
    if cell.trace_quality == "owner_backfill_not_detected":
        return "not_detected"
    if cell.trace_quality == "owner_backfill_unassessable":
        return "unassessable"
    return cell.trace_quality or cell.status
