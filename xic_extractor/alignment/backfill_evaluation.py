"""Backfill candidate evaluation and cell assembly helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from time import perf_counter
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from xic_extractor.alignment.backfill_acquisition import (
    _peak_facts_match_baseline,
    _positive_elapsed,
)
from xic_extractor.alignment.backfill_request_plan import (
    BackfillRequestItem,
)
from xic_extractor.alignment.backfill_result import (
    BackfillPeakFactReceipt,
    _BackfillTraceEvaluation,
    validate_backfill_peak_fact_observation,  # noqa: F401
)
from xic_extractor.alignment.backfill_runtime_projection import (
    BackfillRuntimeFeature,
)
from xic_extractor.alignment.backfill_scope import (
    any_detected_owner_can_be_superseded,
    backfill_seed_centers,
)
from xic_extractor.alignment.cell_region_audit import with_region_audit
from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.alignment.matrix_handoff import integration_from_peak
from xic_extractor.alignment.owner_area import median_owner_area, positive_finite
from xic_extractor.alignment.owner_group_delivery import (
    delivery_cell_projection,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.evidence_facts import (
    TraceWindowEvidenceFacts,
    peak_trace_evidence,
)
from xic_extractor.peak_detection.facade import find_peak_and_area
from xic_extractor.peak_detection.ms1_trace_detection import detect_ms1_trace_peak
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
    PreparedSignalResult,
)
from xic_extractor.peak_detection.region_audit import (
    PeakRegionAuditSummary,
)
from xic_extractor.xic_models import XICRequest, XICTrace

_RequestItem = BackfillRequestItem
# One extraction chunk's prepared signals, keyed by the identity of the trace
# object request equivalence fanned out.  The cache holds the trace itself so a
# recycled id() can never alias a different trace.
_ChunkPreparedSignalCache: TypeAlias = dict[int, tuple[XICTrace, PreparedSignalResult]]
_RequestGroupKey = tuple[str, int | float, int | float]


def _chunk_prepared_signal_cache() -> _ChunkPreparedSignalCache:
    """Start one extraction chunk with no carried-over prepared signals."""

    return {}


def _backfill_feature_sample_trace(
    feature: BackfillRuntimeFeature,
    sample_stem: str,
    trace: XICTrace,
    *,
    request: XICRequest,
    preferred_rt: float | None = None,
    peak_config: ExtractionConfig,
    maximum_query_delta_min: float,
    signal_processing_profile: SignalProcessingProfile | None,
    selection_subject_id: str,
    prepared_signal_cache: _ChunkPreparedSignalCache,
    require_sealed_facts: bool = False,
    emit_region_audit: bool = False,
) -> _BackfillTraceEvaluation:
    if signal_processing_profile is None:
        return _backfill_feature_sample_trace_legacy_audit(
            feature,
            sample_stem,
            trace,
            request=request,
            preferred_rt=preferred_rt,
            peak_config=peak_config,
            require_sealed_facts=require_sealed_facts,
        )
    return _backfill_feature_sample_trace_canonical(
        feature,
        sample_stem,
        trace,
        request=request,
        preferred_rt=preferred_rt,
        peak_config=peak_config,
        maximum_query_delta_min=maximum_query_delta_min,
        emit_region_audit=emit_region_audit,
        signal_processing_profile=signal_processing_profile,
        selection_subject_id=selection_subject_id,
        prepared_signal_cache=prepared_signal_cache,
    )


def _backfill_feature_sample_trace_legacy_audit(
    feature: BackfillRuntimeFeature,
    sample_stem: str,
    trace: XICTrace,
    *,
    request: XICRequest,
    preferred_rt: float | None,
    peak_config: ExtractionConfig,
    require_sealed_facts: bool,
) -> _BackfillTraceEvaluation:
    peak_preferred_rt = (
        feature.family_center_rt if preferred_rt is None else preferred_rt
    )
    detection = detect_ms1_trace_peak(
        trace.rt,
        trace.intensity,
        peak_config=peak_config,
        preferred_rt=peak_preferred_rt,
        strict_preferred_rt=False,
        peak_finder=find_peak_and_area,
    )
    if detection.status == "unassessable_trace":
        return _BackfillTraceEvaluation(
            cell=_backfill_unchecked_cell(
                feature,
                sample_stem,
                request=request,
                preferred_rt=preferred_rt,
                trace_quality="owner_backfill_unassessable",
                reason="owner-centered MS1 backfill query was not assessable",
            ),
            peak_facts=None,
            peak_fact_elapsed_seconds=None,
        )
    if (
        detection.status != "detected"
        or detection.result is None
        or detection.peak is None
    ):
        return _BackfillTraceEvaluation(
            cell=_backfill_unchecked_cell(
                feature,
                sample_stem,
                request=request,
                preferred_rt=preferred_rt,
                trace_quality="owner_backfill_not_detected",
                reason="owner-centered MS1 backfill query found no accepted peak",
            ),
            peak_facts=None,
            peak_fact_elapsed_seconds=None,
        )
    rt_array = detection.rt
    intensity_array = detection.intensity
    peak = detection.peak
    base_integration = integration_from_peak(
        peak,
        boundary_sources=("owner_backfill_batch",),
    )
    fact_start = perf_counter()
    try:
        peak_facts = (
            peak_trace_evidence(
                base_integration,
                rt_array,
                intensity_array,
                baseline_integration_method=getattr(
                    peak_config,
                    "baseline_integration_method",
                    "asls",
                ),
            )
            if base_integration is not None
            else None
        )
    except (IndexError, TypeError, ValueError, FloatingPointError):
        peak_facts = None
    fact_elapsed_seconds = _positive_elapsed(perf_counter() - fact_start)
    if peak_facts is None:
        fact_elapsed_seconds = None
    if require_sealed_facts and not _peak_facts_match_baseline(
        base_integration,
        peak_facts,
    ):
        return _BackfillTraceEvaluation(
            cell=_backfill_unchecked_cell(
                feature,
                sample_stem,
                request=request,
                preferred_rt=preferred_rt,
                trace_quality="owner_backfill_unassessable",
                reason=(
                    "shared peak facts were unavailable or disagreed with the "
                    "sealed Owner Backfill hypothesis"
                ),
            ),
            peak_facts=peak_facts,
            peak_fact_elapsed_seconds=fact_elapsed_seconds,
        )
    cell = AlignedCell(
        sample_stem=sample_stem,
        cluster_id=feature.feature_family_id,
        status="backfilled",
        area=peak.area,
        apex_rt=peak.rt,
        height=peak.intensity,
        peak_start_rt=peak.peak_start,
        peak_end_rt=peak.peak_end,
        rt_delta_sec=(peak.rt - feature.family_center_rt) * 60.0,
        trace_quality="owner_backfill",
        scan_support_score=_scan_support_score(
            rt_array,
            peak_start=peak.peak_start,
            peak_end=peak.peak_end,
            scans_target=peak_config.resolver_min_scans,
        ),
        source_candidate_id=None,
        reason="owner-centered MS1 backfill",
        selected_integration=(
            peak_facts.integration if peak_facts is not None else base_integration
        ),
        backfill_seed_mz=request.mz,
        backfill_seed_rt=preferred_rt,
        backfill_request_rt_min=request.rt_min,
        backfill_request_rt_max=request.rt_max,
        backfill_request_ppm=request.ppm_tol,
        **delivery_cell_projection(
            feature,
            gap_fill_state="gap_fill_rescued",
            gap_fill_reason="group_centered_query_detected",
            missing_observation_state="queried_and_detected",
        ),
    )
    return _BackfillTraceEvaluation(
        cell=cell,
        peak_facts=peak_facts,
        peak_fact_elapsed_seconds=fact_elapsed_seconds,
    )


def _backfill_feature_sample_trace_canonical(
    feature: BackfillRuntimeFeature,
    sample_stem: str,
    trace: XICTrace,
    *,
    request: XICRequest,
    preferred_rt: float | None,
    peak_config: ExtractionConfig,
    maximum_query_delta_min: float,
    emit_region_audit: bool,
    signal_processing_profile: SignalProcessingProfile,
    selection_subject_id: str,
    prepared_signal_cache: _ChunkPreparedSignalCache,
) -> _BackfillTraceEvaluation:
    query_rt = feature.family_center_rt if preferred_rt is None else preferred_rt
    started = perf_counter()
    # Request equivalence fans one extracted trace object out to every logical
    # request in its class, so keying on that object reuses the prepared signal
    # exactly where reuse is structural.  Keying on a pre-minted trace content id
    # instead cost one full canonical hash per request to re-derive an identity
    # the physical owner mints anyway, and retained one prepared signal per
    # distinct trace for the whole call.
    cache_key = id(trace)
    cached = prepared_signal_cache.get(cache_key)
    prepared = cached[1] if cached is not None and cached[0] is trace else None
    if prepared is None:
        prepared = prepare_physical_signal(
            trace.rt,
            trace.intensity,
            profile=signal_processing_profile,
        )
        prepared_signal_cache[cache_key] = (trace, prepared)
    physical_evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id=selection_subject_id,
        query_rt_min=query_rt,
        request_context_rt_start_min=request.rt_min,
        request_context_rt_end_min=request.rt_max,
    )
    if (
        physical_evaluation.state == "unavailable"
        and physical_evaluation.reason == "no_selection"
        and len(physical_evaluation.segment_universe.segments) == 1
    ):
        unique_segment = physical_evaluation.segment_universe.segments[0]
        if abs(unique_segment.apex_rt_min - query_rt) <= maximum_query_delta_min:
            physical_evaluation = evaluate_physical_peak(
                prepared,
                selection_subject_id=selection_subject_id,
                query_rt_min=unique_segment.apex_rt_min,
                request_context_rt_start_min=request.rt_min,
                request_context_rt_end_min=request.rt_max,
            )
    elapsed = _positive_elapsed(perf_counter() - started)
    facts = physical_evaluation.facts
    if physical_evaluation.state != "available" or facts is None:
        trace_quality = (
            "owner_backfill_not_detected"
            if physical_evaluation.reason
            in {"no_selection", "peak_not_found", "no_signal_above_baseline"}
            else "owner_backfill_unassessable"
        )
        cell = _backfill_unchecked_cell(
            feature,
            sample_stem,
            request=request,
            preferred_rt=preferred_rt,
            trace_quality=trace_quality,
            reason=(
                "canonical physical owner: "
                f"{physical_evaluation.state}/{physical_evaluation.reason}"
            ),
        )
        return _BackfillTraceEvaluation(
            cell=cell,
            peak_facts=None,
            peak_fact_elapsed_seconds=elapsed,
            physical_evaluation=physical_evaluation,
            trace_rt_start_min=(float(trace.rt[0]) if len(trace.rt) else None),
            trace_rt_end_min=(float(trace.rt[-1]) if len(trace.rt) else None),
        )

    integration = physical_evaluation.integration
    if integration is None:
        raise ValueError("available physical evaluation lacks raw descriptor")
    if emit_region_audit:
        physical_evaluation = replace(
            physical_evaluation,
            operations=(*physical_evaluation.operations, "region_audit_projection"),
        )
    peak_fact_receipt = BackfillPeakFactReceipt(
        integration=integration,
        physical_evaluation=physical_evaluation,
        normalized_shape_status="ok",
        normalized_shape_reason="canonical_physical_owner",
        normalized_shape_point_count=facts.relative_shape_point_count,
        normalized_shape_max_intensity=1.0,
        window=TraceWindowEvidenceFacts(
            status="ok",
            reason="canonical_closed_segment_subject",
            query_apex_rt_min=facts.apex_rt_min,
            query_apex_in_trace_window=True,
            trace_apex_rt_min=facts.apex_rt_min,
            global_trace_apex_delta_min=0.0,
            local_apex_delta_min=0.0,
            local_max_intensity=facts.height,
            local_to_global_max_ratio=1.0,
        ),
        smoothing_count=1,
        integration_count=1,
    )
    scans_target = int(getattr(peak_config, "resolver_min_scans", 1))
    scan_support_score = (
        min(1.0, facts.scan_support_count / scans_target) if scans_target > 0 else 0.0
    )
    cell = AlignedCell(
        sample_stem=sample_stem,
        cluster_id=feature.feature_family_id,
        status="backfilled",
        area=facts.area_counts_seconds,
        apex_rt=facts.apex_rt_min,
        height=facts.height,
        peak_start_rt=facts.left_rt_min,
        peak_end_rt=facts.right_rt_min,
        rt_delta_sec=(facts.apex_rt_min - feature.family_center_rt) * 60.0,
        trace_quality="owner_backfill",
        scan_support_score=scan_support_score,
        source_candidate_id=None,
        reason="owner-centered MS1 backfill",
        selected_integration=integration,
        backfill_seed_mz=request.mz,
        backfill_seed_rt=preferred_rt,
        backfill_request_rt_min=request.rt_min,
        backfill_request_rt_max=request.rt_max,
        backfill_request_ppm=request.ppm_tol,
        **delivery_cell_projection(
            feature,
            gap_fill_state="gap_fill_rescued",
            gap_fill_reason="group_centered_query_detected",
            missing_observation_state="queried_and_detected",
        ),
    )
    if emit_region_audit:
        cell = with_region_audit(
            cell,
            _canonical_region_audit(physical_evaluation, signal_processing_profile),
        )
    return _BackfillTraceEvaluation(
        cell=cell,
        peak_facts=peak_fact_receipt,
        peak_fact_elapsed_seconds=elapsed,
        physical_evaluation=physical_evaluation,
        trace_rt_start_min=(float(trace.rt[0]) if len(trace.rt) else None),
        trace_rt_end_min=(float(trace.rt[-1]) if len(trace.rt) else None),
    )


def _canonical_region_audit(
    evaluation: PhysicalPeakEvaluation,
    profile: SignalProcessingProfile,
) -> PeakRegionAuditSummary:
    facts = evaluation.facts
    if facts is None:
        return PeakRegionAuditSummary(
            candidate_count=len(evaluation.segment_universe.segments),
            shadow_status="evaluated",
            shadow_verdict=evaluation.reason,
            review_reason="canonical physical owner returned no selected segment",
            baseline_method=profile.baseline_method,
        )
    return PeakRegionAuditSummary(
        candidate_count=len(evaluation.segment_universe.segments),
        selected_proposal_sources=("canonical_physical_segment",),
        selected_merge_note="canonical owner; no audit merge",
        shadow_status="evaluated",
        shadow_verdict="canonical_selected_segment",
        merge_suggestion_source="canonical_physical_owner",
        selected_interval_count=1,
        local_mixture_diagnostic="canonical_closed_segment",
        local_mixture_reason="audit projects the selected canonical segment",
        review_reason="diagnostic projection; no selection authority",
        decision_status="review_only",
        decision_class="canonical_owner_projection",
        product_action="no_change",
        promotion_reason="selected value remains owned by canonical physical facts",
        baseline_method=profile.baseline_method,
    )


def _backfill_unchecked_cell(
    feature: BackfillRuntimeFeature,
    sample_stem: str,
    *,
    request: XICRequest,
    preferred_rt: float | None,
    trace_quality: str,
    reason: str,
) -> AlignedCell:
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=feature.feature_family_id,
        status="unchecked",
        area=None,
        apex_rt=None,
        height=None,
        peak_start_rt=None,
        peak_end_rt=None,
        rt_delta_sec=None,
        trace_quality=trace_quality,
        scan_support_score=None,
        source_candidate_id=None,
        reason=reason,
        backfill_seed_mz=request.mz,
        backfill_seed_rt=preferred_rt,
        backfill_request_rt_min=request.rt_min,
        backfill_request_rt_max=request.rt_max,
        backfill_request_ppm=request.ppm_tol,
        **delivery_cell_projection(
            feature,
            gap_fill_state="not_filled",
            gap_fill_reason="query_attempt_not_detected",
            missing_observation_state="missing_unchecked",
        ),
    )


def _backfill_seed_centers(
    feature: BackfillRuntimeFeature,
) -> tuple[tuple[float, float], ...]:
    return backfill_seed_centers(feature)


def _any_detected_owner_can_be_superseded(
    feature: BackfillRuntimeFeature,
    owners: Sequence[SampleLocalMS1Owner] | None,
) -> bool:
    return any_detected_owner_can_be_superseded(feature, owners)


def _detected_owner_can_be_superseded(
    feature: BackfillRuntimeFeature,
    owner: SampleLocalMS1Owner,
) -> bool:
    detected_area = positive_finite(owner.owner_area)
    if detected_area is None:
        return False
    family_area = median_owner_area(feature)
    if family_area is None:
        return False
    return detected_area <= family_area * 0.25


def _emit_region_audit_for_family(
    emit_region_audit: bool,
    family_id: str,
    region_audit_family_ids: frozenset[str] | None,
) -> bool:
    if not emit_region_audit:
        return False
    if region_audit_family_ids is None:
        return True
    return family_id in region_audit_family_ids


def _keep_best_backfill_outcome_cell(
    cells: dict[tuple[str, str], AlignedCell],
    candidate: AlignedCell,
) -> None:
    key = (candidate.cluster_id, candidate.sample_stem)
    current = cells.get(key)
    if current is None:
        cells[key] = candidate
        return
    if current.status == "backfilled" and candidate.status != "backfilled":
        return
    if current.status != "backfilled" and candidate.status == "backfilled":
        cells[key] = candidate
        return
    if candidate.status != "backfilled":
        return
    if _backfilled_cell_sort_key(candidate) < _backfilled_cell_sort_key(current):
        cells[key] = candidate


def _backfilled_cell_sort_key(cell: AlignedCell) -> tuple[float, float, float]:
    area = float(cell.area or 0.0)
    rt_delta = abs(cell.rt_delta_sec) if cell.rt_delta_sec is not None else np.inf
    apex_rt = float(cell.apex_rt or 0.0)
    return (-area, rt_delta, apex_rt)

def _scan_support_score(
    rt: NDArray[np.float64],
    *,
    peak_start: float,
    peak_end: float,
    scans_target: int,
) -> float:
    if scans_target <= 0:
        return 0.0
    scan_count = int(np.count_nonzero((rt >= peak_start) & (rt <= peak_end)))
    return min(1.0, scan_count / scans_target)
