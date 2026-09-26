from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction.anchors import (
    apply_anchor_mismatch_penalty,
    paired_anchor_mismatch_diagnostic,
)
from xic_extractor.extraction.diagnostics import (
    append_anchor_window_diagnostics,
    candidate_ms2_evidence_builder,
    check_target_nl,
    istd_anchor_missing_diagnostic,
)
from xic_extractor.extraction.drift import estimate_sample_drift
from xic_extractor.extraction.handoff_spine_runtime import (
    selected_handoff_peak,
)
from xic_extractor.extraction.istd_anchor_retry import retry_istd_anchor_peak_if_needed
from xic_extractor.extraction.peak_candidate_audit import append_peak_audit_rows
from xic_extractor.extraction.result_assembly import build_extraction_result
from xic_extractor.extraction.rt_windows import get_rt_window
from xic_extractor.extraction.scoring_factory import (
    allow_prepass_anchor,
    paired_istd_fwhm,
    selected_candidate,
    selected_shape_metrics,
)
from xic_extractor.neutral_loss import CandidateMS2Evidence
from xic_extractor.output.messages import (
    DiagnosticRecord,
    build_diagnostic_records,
    istd_confidence_note,
)
from xic_extractor.peak_detection.engine import (
    find_peak_and_area as find_peak_and_area_from_candidates,
)
from xic_extractor.peak_detection.model_selection import ExpectedDiffApprovalRecords
from xic_extractor.peak_detection.models import (
    PeakCandidatesResult,
    PeakDetectionResult,
)
from xic_extractor.peak_detection.observations import (
    physical_peak_observation_integration,
)
from xic_extractor.peak_detection.physical_peak_candidate_adapter import (
    physical_candidates_result,
    selected_physical_segment,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    answer_physical_peak_question,
    enumerate_physical_segment_universe,
    mint_physical_peak_question,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalSegmentUniverse,
    PreparedSignalResult,
)
from xic_extractor.peak_detection.scoring_models import ScoringContext
from xic_extractor.peak_detection.selection_decision import (
    selection_decision_from_hypothesis,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.rt_prior_library import LibraryEntry
from xic_extractor.signal_processing import PeakCandidate

if TYPE_CHECKING:
    from xic_extractor.extractor import (
        ExtractionResult,
        FileResult,
        RawFileExtractionResult,
    )


def extract_raw_file_result(
    raw_index: int,
    config: ExtractionConfig,
    targets: list[Target],
    raw_path: Path,
    *,
    scoring_context_factory: Callable[..., Any] | None = None,
    rt_prior_library: dict[tuple[str, str], LibraryEntry] | None = None,
    model_selection_expected_diff_approvals: ExpectedDiffApprovalRecords | None = None,
    expected_acquisition_source: AcquisitionSourceExpectation | None = None,
) -> RawFileExtractionResult:
    from xic_extractor import extractor

    file_result, diagnostics = process_file(
        config,
        targets,
        raw_path,
        scoring_context_factory=scoring_context_factory,
        rt_prior_library=rt_prior_library,
        model_selection_expected_diff_approvals=model_selection_expected_diff_approvals,
        expected_acquisition_source=expected_acquisition_source,
    )
    return extractor.RawFileExtractionResult(
        raw_index=raw_index,
        sample_name=file_result.sample_name,
        file_result=file_result,
        diagnostics=diagnostics,
    )


def process_file(
    config: ExtractionConfig,
    targets: list[Target],
    raw_path: Path,
    *,
    scoring_context_factory: Callable[..., Any] | None = None,
    precomputed_istd_results: dict[str, ExtractionResult] | None = None,
    precomputed_istd_diagnostics: list[DiagnosticRecord] | None = None,
    precomputed_istd_anchor_rts: dict[str, float] | None = None,
    precomputed_istd_shape_metrics: dict[str, tuple[float, float | None]] | None = None,
    rt_prior_library: dict[tuple[str, str], LibraryEntry] | None = None,
    model_selection_expected_diff_approvals: ExpectedDiffApprovalRecords | None = None,
    expected_acquisition_source: AcquisitionSourceExpectation | None = None,
) -> tuple[FileResult, list[DiagnosticRecord]]:
    from xic_extractor import extractor

    sample_name = raw_path.stem
    approval_records = model_selection_expected_diff_approvals
    try:
        raw_context = (
            extractor.open_raw(raw_path, config.dll_dir)
            if expected_acquisition_source is None
            else extractor.open_raw(
                raw_path,
                config.dll_dir,
                expected_acquisition_source=expected_acquisition_source.source,
                _expected_path_identity=expected_acquisition_source.path_identity,
            )
        )
        with raw_context as raw:
            results = dict(precomputed_istd_results or {})
            peak_candidate_rows: list[dict[str, str]] = []
            peak_candidate_boundary_rows: list[dict[str, str]] = []
            selected_envelope_diagnostic_rows: list[dict[str, str]] = []
            diagnostics: list[DiagnosticRecord] = list(
                precomputed_istd_diagnostics or []
            )
            istd_shape_metrics_by_label: dict[str, tuple[float, float | None]] = dict(
                precomputed_istd_shape_metrics or {}
            )
            istd_confidence_by_label = {
                label: result.peak_result.confidence
                for label, result in results.items()
                if result.peak_result.confidence is not None
            }
            active_rt_prior_library = dict(rt_prior_library or {})
            if precomputed_istd_anchor_rts is None:
                istd_anchor_rts: dict[str, float] = {}
                for target in targets:
                    if not target.is_istd:
                        continue
                    extract_one_target(
                        raw,
                        config,
                        sample_name,
                        target,
                        reference_rt=None,
                        strict_preferred_rt=False,
                        results=results,
                        diagnostics=diagnostics,
                        peak_candidate_rows=peak_candidate_rows,
                        peak_candidate_boundary_rows=peak_candidate_boundary_rows,
                        selected_envelope_diagnostic_rows=(
                            selected_envelope_diagnostic_rows
                        ),
                        scoring_context_factory=scoring_context_factory,
                        model_selection_expected_diff_approvals=approval_records,
                        shape_metrics_by_label=istd_shape_metrics_by_label,
                    )
                    confidence = results[target.label].peak_result.confidence
                    if confidence is not None:
                        istd_confidence_by_label[target.label] = confidence
                    anchor_rt = credible_istd_anchor_rt(results.get(target.label))
                    if anchor_rt is not None:
                        istd_anchor_rts[target.label] = anchor_rt
            else:
                istd_anchor_rts = dict(precomputed_istd_anchor_rts)
            sample_drift = estimate_sample_drift(targets, istd_anchor_rts)

            for target in targets:
                if target.is_istd:
                    continue
                if target.istd_pair and target.istd_pair in istd_anchor_rts:
                    reference_rt: float | None = istd_anchor_rts[target.istd_pair]
                else:
                    if target.istd_pair:
                        diagnostics.append(
                            istd_anchor_missing_diagnostic(sample_name, target)
                        )
                    reference_rt = None
                extract_one_target(
                    raw,
                    config,
                    sample_name,
                    target,
                    reference_rt=reference_rt,
                    rt_prior_library=active_rt_prior_library,
                    sample_drift=sample_drift,
                    strict_preferred_rt=reference_rt is not None,
                    results=results,
                    diagnostics=diagnostics,
                    peak_candidate_rows=peak_candidate_rows,
                    peak_candidate_boundary_rows=peak_candidate_boundary_rows,
                    selected_envelope_diagnostic_rows=(
                        selected_envelope_diagnostic_rows
                    ),
                    scoring_context_factory=scoring_context_factory,
                    model_selection_expected_diff_approvals=approval_records,
                    istd_confidence_note=istd_confidence_note(
                        istd_confidence_by_label.get(target.istd_pair)
                    ),
                    istd_rt_in_this_sample=istd_anchor_rts.get(target.istd_pair),
                    paired_istd_fwhm=paired_istd_fwhm(
                        target,
                        istd_shape_metrics_by_label,
                    ),
                )
            return extractor.FileResult(
                sample_name=sample_name,
                results=results,
                peak_candidate_rows=peak_candidate_rows,
                peak_candidate_boundary_rows=peak_candidate_boundary_rows,
                selected_envelope_diagnostic_rows=selected_envelope_diagnostic_rows,
            ), diagnostics
    except Exception as exc:
        reason = f"Failed to open .raw: {type(exc).__name__}: {exc}"
        return (
            extractor.FileResult(sample_name=sample_name, results={}, error=reason),
            [DiagnosticRecord(sample_name, "", "FILE_ERROR", reason)],
        )


def extract_one_target(
    raw: Any,
    config: ExtractionConfig,
    sample_name: str,
    target: Target,
    *,
    reference_rt: float | None,
    rt_prior_library: dict[tuple[str, str], LibraryEntry] | None = None,
    sample_drift: float = 0.0,
    strict_preferred_rt: bool = False,
    results: dict[str, ExtractionResult],
    diagnostics: list[DiagnosticRecord],
    peak_candidate_rows: list[dict[str, str]] | None = None,
    peak_candidate_boundary_rows: list[dict[str, str]] | None = None,
    selected_envelope_diagnostic_rows: list[dict[str, str]] | None = None,
    scoring_context_factory: Callable[..., Any] | None = None,
    istd_confidence_note: str | None = None,
    istd_rt_in_this_sample: float | None = None,
    paired_istd_fwhm: float | None = None,
    shape_metrics_by_label: dict[str, tuple[float, float | None]] | None = None,
    model_selection_expected_diff_approvals: ExpectedDiffApprovalRecords | None = None,
) -> float | None:
    selection_config = replace(
        config,
        resolver_mode="local_minimum",
        baseline_integration_method="asls",
    )
    target_reference_rt = paired_target_reference_rt(
        target,
        reference_rt=reference_rt,
        rt_prior_library=rt_prior_library,
    )
    rt_min, rt_max, anchor_used, anchor_rt = get_rt_window(
        raw,
        target,
        config,
        reference_rt=reference_rt,
        target_reference_rt=target_reference_rt,
        sample_drift=sample_drift,
    )
    rt, intensity = raw.extract_xic(target.mz, rt_min, rt_max, target.ppm_tol)
    nl_result = check_target_nl(raw, target, config)
    profile = resolve_signal_processing_profile(
        signal_processing_method=config.signal_processing_method,
        signal_processing_window_points=config.signal_processing_window_points,
    )
    physical_trace_cache: dict[
        tuple[bytes, bytes],
        tuple[PreparedSignalResult, PhysicalSegmentUniverse, PeakCandidatesResult],
    ] = {}

    def _physical_trace_owners(
        rt_values: object,
        intensity_values: object,
    ) -> tuple[PreparedSignalResult, PhysicalSegmentUniverse, PeakCandidatesResult]:
        rt_array = np.asarray(rt_values, dtype=float)
        intensity_array = np.asarray(intensity_values, dtype=float)
        cache_key = (rt_array.tobytes(), intensity_array.tobytes())
        cached = physical_trace_cache.get(cache_key)
        if cached is not None:
            return cached
        prepared_result = prepare_physical_signal(
            rt_array,
            intensity_array,
            profile=profile,
        )
        segment_universe = enumerate_physical_segment_universe(
            prepared_result,
            request_context_rt_start_min=rt_min,
            request_context_rt_end_min=rt_max,
        )
        cached = (
            prepared_result,
            segment_universe,
            physical_candidates_result(prepared_result, segment_universe),
        )
        physical_trace_cache[cache_key] = cached
        return cached

    trace_scoring_context_factory: Callable[..., Any] | None = None
    if scoring_context_factory is not None:
        source_factory = scoring_context_factory

        def _trace_scoring_context_factory(
            *, rt: object, intensity: object, **kwargs: Any
        ) -> Any:
            prepared_result, _, _ = _physical_trace_owners(rt, intensity)
            return source_factory(
                rt=rt, intensity=intensity, prepared_result=prepared_result, **kwargs
            )

        trace_scoring_context_factory = _trace_scoring_context_factory

    def _find_physical_peak(
        rt_values: object,
        intensity_values: object,
        _config: ExtractionConfig,
        *,
        preferred_rt: float | None = None,
        strict_preferred_rt: bool = False,
        scoring_context_builder: (
            Callable[[PeakCandidate], ScoringContext] | None
        ) = None,
        istd_confidence_note: str | None = None,
        evidence_role: str = "",
        istd_pair: str = "",
        paired_istd_anchor_rt: float | None = None,
    ) -> PeakDetectionResult:
        _, _, candidates_result = _physical_trace_owners(
            rt_values, intensity_values
        )

        def _find_candidates(
            *_args: object, **_kwargs: object
        ) -> PeakCandidatesResult:
            return candidates_result

        return find_peak_and_area_from_candidates(
            np.asarray(rt_values, dtype=float),
            np.asarray(intensity_values, dtype=float),
            selection_config,
            candidate_finder=_find_candidates,
            preferred_rt=preferred_rt,
            strict_preferred_rt=strict_preferred_rt,
            scoring_context_builder=scoring_context_builder,
            istd_confidence_note=istd_confidence_note,
            evidence_role=evidence_role,
            istd_pair=istd_pair,
            paired_istd_anchor_rt=paired_istd_anchor_rt,
        )

    scoring_context_builder = None
    candidate_ms2_builder = candidate_ms2_evidence_builder(raw, target, config)
    candidate_ms2_cache: dict[PeakCandidate, CandidateMS2Evidence] = {}

    def _cached_candidate_ms2_builder(
        candidate: PeakCandidate,
    ) -> CandidateMS2Evidence | None:
        if candidate_ms2_builder is None:
            return None
        evidence = candidate_ms2_cache.get(candidate)
        if evidence is None:
            evidence = candidate_ms2_builder(candidate)
            candidate_ms2_cache[candidate] = evidence
        return evidence

    for allow_context_extension in (True, False):
        scoring_context_builder = None
        if trace_scoring_context_factory is not None:
            scoring_context_builder = trace_scoring_context_factory(
                target=target,
                sample_name=sample_name,
                rt=rt,
                intensity=intensity,
                istd_rt_in_this_sample=istd_rt_in_this_sample,
                paired_istd_fwhm=paired_istd_fwhm,
                nl_result=nl_result,
                candidate_ms2_evidence_builder=_cached_candidate_ms2_builder,
            )
        if scoring_context_builder is not None:
            peak_result = _find_physical_peak(
                rt,
                intensity,
                selection_config,
                preferred_rt=anchor_rt,
                strict_preferred_rt=strict_preferred_rt,
                scoring_context_builder=scoring_context_builder,
                istd_confidence_note=istd_confidence_note,
                evidence_role="ISTD" if target.is_istd else "Analyte",
                istd_pair=target.istd_pair,
                paired_istd_anchor_rt=istd_rt_in_this_sample,
            )
        else:
            peak_result = _find_physical_peak(
                rt,
                intensity,
                selection_config,
                preferred_rt=anchor_rt,
                strict_preferred_rt=strict_preferred_rt,
                evidence_role="ISTD" if target.is_istd else "Analyte",
                istd_pair=target.istd_pair,
                paired_istd_anchor_rt=istd_rt_in_this_sample,
            )
        peak = peak_result.peak
        if (
            not allow_context_extension
            or not anchor_used
            or peak is None
            or len(rt) == 0
        ):
            break
        if not target.rt_min <= peak.rt <= target.rt_max:
            break
        clipped_left = peak.peak_start <= float(rt[0]) and target.rt_min < rt_min
        clipped_right = peak.peak_end >= float(rt[-1]) and target.rt_max > rt_max
        if not clipped_left and not clipped_right:
            break
        # An NL-focused query must not turn an unobserved peak return into an apex.
        # Restore the configured context once and let the same owners select again.
        rt_min = min(rt_min, target.rt_min)
        rt_max = max(rt_max, target.rt_max)
        rt, intensity = raw.extract_xic(target.mz, rt_min, rt_max, target.ppm_tol)
    retry_decision = retry_istd_anchor_peak_if_needed(
        peak_result,
        raw=raw,
        config=selection_config,
        target=target,
        anchor_used=anchor_used,
        anchor_rt=anchor_rt,
        scoring_context_factory=trace_scoring_context_factory,
        candidate_ms2_evidence_builder=_cached_candidate_ms2_builder,
        sample_name=sample_name,
        nl_result=nl_result,
        istd_confidence_note=istd_confidence_note,
        istd_rt_in_this_sample=istd_rt_in_this_sample,
        paired_istd_fwhm=paired_istd_fwhm,
        peak_finder=_find_physical_peak,
    )
    peak_result = retry_decision.peak_result
    audit_rt = retry_decision.rt if retry_decision.rt is not None else rt
    audit_intensity = (
        retry_decision.intensity
        if retry_decision.intensity is not None
        else intensity
    )
    if retry_decision.rt is not None and trace_scoring_context_factory is not None:
        scoring_context_builder = trace_scoring_context_factory(
            target=target,
            sample_name=sample_name,
            rt=audit_rt,
            intensity=audit_intensity,
            istd_rt_in_this_sample=istd_rt_in_this_sample,
            paired_istd_fwhm=paired_istd_fwhm,
            nl_result=nl_result,
            candidate_ms2_evidence_builder=_cached_candidate_ms2_builder,
        )
    prepared, universe, _ = _physical_trace_owners(audit_rt, audit_intensity)
    peak_result = _find_physical_peak(
        audit_rt,
        audit_intensity,
        selection_config,
        preferred_rt=anchor_rt,
        strict_preferred_rt=strict_preferred_rt,
        scoring_context_builder=scoring_context_builder,
        istd_confidence_note=istd_confidence_note,
        evidence_role="ISTD" if target.is_istd else "Analyte",
        istd_pair=target.istd_pair,
        paired_istd_anchor_rt=istd_rt_in_this_sample,
    )
    paired_rejection = paired_anchor_mismatch_diagnostic(
        sample_name,
        target,
        peak_result,
        reference_rt=reference_rt,
        anchor_rt=anchor_rt,
        anchor_used=anchor_used,
        strict_preferred_rt=strict_preferred_rt,
    )
    if paired_rejection is not None:
        peak_result = apply_anchor_mismatch_penalty(
            peak_result,
            paired_rejection.reason,
        )
    if istd_rt_in_this_sample is not None:
        peak_result = replace(
            peak_result,
            paired_istd_anchor_rt=istd_rt_in_this_sample,
        )
    shape_metrics = selected_shape_metrics(prepared, peak_result)
    candidate = selected_candidate(peak_result)
    if shape_metrics_by_label is not None and shape_metrics is not None:
        shape_metrics_by_label[target.label] = shape_metrics
    handoff_peak = selected_handoff_peak(
        config=selection_config,
        sample_name=sample_name,
        target=target,
        peak_result=peak_result,
        candidate=candidate,
        candidate_ms2_cache=candidate_ms2_cache,
        candidate_ms2_builder=_cached_candidate_ms2_builder,
        rt=audit_rt,
        intensity=audit_intensity,
        rt_min=rt_min,
        rt_max=rt_max,
        expected_rt_min=anchor_rt,
        paired_istd_anchor_rt=istd_rt_in_this_sample,
        model_selection_expected_diff_approvals=model_selection_expected_diff_approvals,
    )
    physical_evaluation = None
    if handoff_peak.trace_group is not None:
        selected_segment = selected_physical_segment(
            selected_candidate(peak_result), universe
        )
        query_rt_min = (
            selected_segment.apex_rt_min if selected_segment is not None else None
        )
        if query_rt_min is None and universe.state != "available":
            acquisition_trace = handoff_peak.trace_group.primary_trace
            query_rt_min = (acquisition_trace.rt_min + acquisition_trace.rt_max) / 2
        if query_rt_min is not None:
            question = mint_physical_peak_question(
                prepared.projection,
                universe,
                selection_subject_id=target.label,
                query_rt_min=query_rt_min,
            )
            physical_evaluation = answer_physical_peak_question(
                prepared,
                universe,
                question,
            )
        physical_hypothesis = None
        if (
            handoff_peak.selected_hypothesis is not None
            and physical_evaluation is not None
            and physical_evaluation.integration is not None
        ):
            physical_hypothesis = replace(
                handoff_peak.selected_hypothesis,
                integration=physical_peak_observation_integration(
                    physical_evaluation
                ),
            )
        if handoff_peak.selected_hypothesis is not None:
            handoff_peak = replace(
                handoff_peak,
                selected_hypothesis=physical_hypothesis,
                selection_decision=(
                    handoff_peak.selection_decision
                    if physical_hypothesis is None
                    else selection_decision_from_hypothesis(
                        physical_hypothesis,
                        peak_result=peak_result,
                    )
                ),
            )

    result = build_extraction_result(
        peak_result=peak_result,
        nl_result=nl_result,
        candidate_ms2_evidence=handoff_peak.candidate_ms2_evidence,
        target=target,
        candidate=candidate,
        scoring_context_builder=scoring_context_builder,
        selected_hypothesis=handoff_peak.selected_hypothesis,
        selection_decision=handoff_peak.selection_decision,
        model_selection_result=handoff_peak.model_selection_result,
        acquisition_trace=(
            None
            if handoff_peak.trace_group is None
            else handoff_peak.trace_group.primary_trace
        ),
        physical_evaluation=physical_evaluation,
        sample_name=sample_name,
    )
    results[target.label] = result
    diagnostics.extend(build_diagnostic_records(sample_name, target, result, config))
    if paired_rejection is not None:
        diagnostics.append(paired_rejection)
    append_anchor_window_diagnostics(
        diagnostics,
        sample_name,
        target,
        config,
        peak_result,
        anchor_used=anchor_used,
        anchor_rt=anchor_rt if anchor_used else None,
        rt_min=rt_min,
        rt_max=rt_max,
        nl_result=result.nl,
        paired_rejection=paired_rejection,
    )
    append_peak_audit_rows(
        peak_candidate_rows=peak_candidate_rows,
        peak_candidate_boundary_rows=peak_candidate_boundary_rows,
        selected_envelope_diagnostic_rows=selected_envelope_diagnostic_rows,
        config=config,
        sample_name=sample_name,
        target=target,
        peak_result=peak_result,
        candidate_ms2_builder=_cached_candidate_ms2_builder,
        rt=audit_rt,
        intensity=audit_intensity,
        trace_group=handoff_peak.trace_group,
        product_selected_candidate_id=getattr(
            handoff_peak.selected_hypothesis, "hypothesis_id", None
        ),
        product_selected_hypothesis=handoff_peak.selected_hypothesis,
        scoring_context_builder=scoring_context_builder,
        istd_confidence_note=istd_confidence_note,
    )
    return anchor_rt


def paired_target_reference_rt(
    target: Target,
    *,
    reference_rt: float | None,
    rt_prior_library: dict[tuple[str, str], LibraryEntry] | None,
) -> float | None:
    if target.is_istd or not target.istd_pair or reference_rt is None:
        return None
    library = rt_prior_library or {}
    entry = library.get((target.label, "analyte"))
    if (
        entry is None
        or entry.istd_pair != target.istd_pair
        or entry.median_delta_rt is None
    ):
        return None
    return reference_rt + entry.median_delta_rt


def credible_istd_anchor_rt(result: ExtractionResult | None) -> float | None:
    """Return the selected ISTD MS1 RT only when it is credible as a pair anchor."""
    if result is None:
        return None
    projection = result.targeted_product_projection
    if projection is not None and not projection.counted_detection:
        return None
    if not allow_prepass_anchor(result.peak_result):
        return None
    rt = result.reported_rt
    area = result.reported_peak_area
    if not _finite_positive(rt) or not _finite_positive(area):
        return None
    assert rt is not None
    return float(rt)


def _finite_positive(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0
