from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from xic_extractor.acquisition.ms2_windows import acquire_ms2_window
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.evidence.neutral_loss import (
    CandidateMS2Evidence,
    candidate_ms2_geometry,
)
from xic_extractor.extraction.anchors import (
    paired_anchor_mismatch_diagnostic,
)
from xic_extractor.extraction.diagnostics import (
    append_anchor_window_diagnostics,
    candidate_ms2_evidence_builder,
    check_target_nl,
    istd_anchor_missing_diagnostic,
)
from xic_extractor.extraction.drift import estimate_sample_drift
from xic_extractor.extraction.ms2_selection import selected_candidate_ms2_evidence
from xic_extractor.extraction.rt_windows import get_rt_window
from xic_extractor.extraction.scoring_factory import (
    allow_prepass_anchor,
    paired_istd_fwhm,
    selected_candidate,
    selected_shape_metrics,
)
from xic_extractor.extraction.targeted_peak_selection import (
    assess_targeted_candidate_population,
)
from xic_extractor.extraction.trace_context import targeted_extraction_trace_group
from xic_extractor.output.messages import (
    DiagnosticRecord,
    build_diagnostic_records,
    istd_confidence_note,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidatesResult,
)
from xic_extractor.peak_detection.physical_peak_candidate_adapter import (
    physical_candidates_result,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    enumerate_physical_segment_universe,
    physical_segment_has_incomplete_context,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalSegmentUniverse,
    PreparedSignalResult,
)
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.raw_reader import open_raw

if TYPE_CHECKING:
    from xic_extractor.extraction.result_models import (
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
    expected_acquisition_source: AcquisitionSourceExpectation | None = None,
) -> RawFileExtractionResult:
    from xic_extractor.extraction import result_models

    file_result, diagnostics = process_file(
        config,
        targets,
        raw_path,
        scoring_context_factory=scoring_context_factory,
        expected_acquisition_source=expected_acquisition_source,
    )
    return result_models.RawFileExtractionResult(
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
    expected_acquisition_source: AcquisitionSourceExpectation | None = None,
) -> tuple[FileResult, list[DiagnosticRecord]]:
    from xic_extractor.extraction import result_models

    sample_name = raw_path.stem
    try:
        raw_context = (
            open_raw(raw_path, config.dll_dir)
            if expected_acquisition_source is None
            else open_raw(
                raw_path,
                config.dll_dir,
                expected_acquisition_source=expected_acquisition_source.source,
                _expected_path_identity=expected_acquisition_source.path_identity,
            )
        )
        with raw_context as raw:
            results = dict(precomputed_istd_results or {})
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
                        scoring_context_factory=scoring_context_factory,
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
            istd_targets = {
                target.label: target for target in targets if target.is_istd
            }

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
                    paired_target=istd_targets.get(target.istd_pair),
                    sample_drift=sample_drift,
                    strict_preferred_rt=reference_rt is not None,
                    results=results,
                    diagnostics=diagnostics,
                    scoring_context_factory=scoring_context_factory,
                    istd_confidence_note=istd_confidence_note(
                        istd_confidence_by_label.get(target.istd_pair)
                    ),
                    istd_rt_in_this_sample=istd_anchor_rts.get(target.istd_pair),
                    paired_istd_fwhm=paired_istd_fwhm(
                        target,
                        istd_shape_metrics_by_label,
                    ),
                )
            return result_models.FileResult(
                sample_name=sample_name,
                results=results,
            ), diagnostics
    except Exception as exc:
        reason = f"Failed to open .raw: {type(exc).__name__}: {exc}"
        return (
            result_models.FileResult(sample_name=sample_name, results={}, error=reason),
            [DiagnosticRecord(sample_name, "", "FILE_ERROR", reason)],
        )


def extract_one_target(
    raw: Any,
    config: ExtractionConfig,
    sample_name: str,
    target: Target,
    *,
    reference_rt: float | None,
    paired_target: Target | None = None,
    sample_drift: float = 0.0,
    strict_preferred_rt: bool = False,
    results: dict[str, ExtractionResult],
    diagnostics: list[DiagnosticRecord],
    scoring_context_factory: Callable[..., Any] | None = None,
    istd_confidence_note: str | None = None,
    istd_rt_in_this_sample: float | None = None,
    paired_istd_fwhm: float | None = None,
    shape_metrics_by_label: dict[str, tuple[float, float | None]] | None = None,
) -> float | None:
    selection_config = replace(
        config,
        resolver_mode="local_minimum",
        baseline_integration_method="asls",
    )
    target_reference_rt = current_target_reference_rt(
        target,
        reference_rt=reference_rt,
        paired_target=paired_target,
        sample_drift=sample_drift,
    )
    selection_rt_min, selection_rt_max, anchor_used, anchor_rt = get_rt_window(
        raw,
        target,
        config,
        sample_key=sample_name,
        target_reference_rt=target_reference_rt,
        paired_istd_credible=(reference_rt is not None and paired_target is not None),
    )
    configured_width = target.rt_max - target.rt_min
    context_rt_min = max(
        0.0,
        min(target.rt_min, selection_rt_min) - configured_width,
    )
    context_rt_max = max(target.rt_max, selection_rt_max) + configured_width
    rt, intensity = raw.extract_xic(
        target.mz,
        context_rt_min,
        context_rt_max,
        target.ppm_tol,
    )
    nl_result = check_target_nl(raw, target, config, sample_key=sample_name)
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
        acquired_context_rt_start = (
            float(rt_array[0])
            if rt_array.ndim == 1 and rt_array.size
            else context_rt_min
        )
        acquired_context_rt_end = (
            float(rt_array[-1])
            if rt_array.ndim == 1 and rt_array.size
            else context_rt_max
        )
        segment_universe = enumerate_physical_segment_universe(
            prepared_result,
            request_context_rt_start_min=acquired_context_rt_start,
            request_context_rt_end_min=acquired_context_rt_end,
        )
        candidates_result = physical_candidates_result(
            prepared_result, segment_universe
        )
        cached = (prepared_result, segment_universe, candidates_result)
        physical_trace_cache[cache_key] = cached
        return cached

    initial_prepared, initial_universe, initial_candidates = _physical_trace_owners(
        rt, intensity
    )
    if not initial_candidates.candidates and any(
        selection_rt_min <= segment.apex_rt_min <= selection_rt_max
        and physical_segment_has_incomplete_context(
            initial_prepared.projection, segment
        )
        for segment in initial_universe.segments
    ):
        context_rt_min = max(0.0, context_rt_min - configured_width)
        context_rt_max += configured_width
        rt, intensity = raw.extract_xic(
            target.mz,
            context_rt_min,
            context_rt_max,
            target.ppm_tol,
        )

    prepared, universe, physical_candidates = _physical_trace_owners(rt, intensity)
    method_window = None
    if target.neutral_loss_da is not None:
        geometries = tuple(
            candidate_ms2_geometry(candidate)
            for candidate in physical_candidates.candidates
        )
        method_window = acquire_ms2_window(
            raw,
            sample_key=sample_name,
            rt_min=min((target.rt_min, *(item.rt_min for item in geometries))),
            rt_max=max((target.rt_max, *(item.rt_max for item in geometries))),
        )
    trace_group = targeted_extraction_trace_group(
        sample_name=sample_name,
        target=target,
        config=selection_config,
        rt=rt,
        intensity=intensity,
        rt_min=context_rt_min,
        rt_max=context_rt_max,
        expected_rt_min=anchor_rt,
    )
    candidate_assessment = assess_targeted_candidate_population(
        target=target,
        config=selection_config,
        trace_group=trace_group,
        prepared_result=prepared,
        universe=universe,
        candidates=physical_candidates,
        selection_window=(selection_rt_min, selection_rt_max),
        preferred_rt=anchor_rt,
        strict_preferred_rt=strict_preferred_rt,
        method_window=method_window,
    )

    scoring_context_builder = None
    candidate_ms2_builder = candidate_ms2_evidence_builder(
        raw, target, config, sample_key=sample_name, window=method_window
    )
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

    if scoring_context_factory is not None:
        scoring_context_builder = scoring_context_factory(
            target=target,
            sample_name=sample_name,
            rt=rt,
            intensity=intensity,
            istd_rt_in_this_sample=istd_rt_in_this_sample,
            target_reference_rt=target_reference_rt,
            paired_istd_fwhm=paired_istd_fwhm,
            nl_result=nl_result,
            candidate_ms2_evidence_builder=_cached_candidate_ms2_builder,
            candidate_nl_support=candidate_assessment.candidate_nl_support,
            prepared_result=prepared,
        )
    peak_result = candidate_assessment.rank_candidates(
        scoring_context_builder=scoring_context_builder,
        istd_confidence_note=istd_confidence_note,
        paired_istd_anchor_rt=istd_rt_in_this_sample,
    )
    paired_rejection = paired_anchor_mismatch_diagnostic(
        sample_name,
        target,
        peak_result,
        reference_rt=reference_rt,
        anchor_rt=anchor_rt,
        anchor_used=anchor_used,
        strict_preferred_rt=candidate_assessment.strict_preferred_rt,
    )
    if istd_rt_in_this_sample is not None:
        peak_result = replace(
            peak_result,
            paired_istd_anchor_rt=istd_rt_in_this_sample,
        )
    candidate = selected_candidate(peak_result)
    selected_candidate_ms2_evidence(
        candidate,
        candidate_ms2_cache,
        _cached_candidate_ms2_builder,
    )
    result = candidate_assessment.complete_result(
        peak_result,
        candidate_ms2_evidence=candidate_ms2_cache,
        nl_result=nl_result,
        scoring_context_builder=scoring_context_builder,
    )
    peak_result = result.peak_result
    shape_metrics = selected_shape_metrics(prepared, peak_result)
    if shape_metrics_by_label is not None and shape_metrics is not None:
        shape_metrics_by_label[target.label] = shape_metrics

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
        rt_min=selection_rt_min,
        rt_max=selection_rt_max,
        nl_result=result.nl,
        paired_rejection=paired_rejection,
    )
    return anchor_rt


def current_target_reference_rt(
    target: Target,
    *,
    reference_rt: float | None,
    paired_target: Target | None = None,
    sample_drift: float = 0.0,
) -> float:
    """Transfer credible current-sample ISTD drift, retaining the method offset.

    Targets and isotope relationships remain method knowledge. This reference
    neither learns chemistry from another batch nor authorizes a peak identity.
    Missing pairs use the existing sample drift, not another sample's prediction.
    """
    center = (target.rt_min + target.rt_max) / 2.0
    if (
        not target.is_istd
        and target.istd_pair
        and paired_target is not None
        and reference_rt is not None
    ):
        paired_center = (paired_target.rt_min + paired_target.rt_max) / 2.0
        return center + reference_rt - paired_center
    return center + sample_drift


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
