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
)
from xic_extractor.extraction.anchors import (
    paired_anchor_mismatch_diagnostic,
)
from xic_extractor.extraction.diagnostics import (
    candidate_ms2_evidence_builder,
    check_target_nl,
    istd_anchor_missing_diagnostic,
)
from xic_extractor.extraction.drift import estimate_sample_drift
from xic_extractor.extraction.ms2_selection import selected_candidate_ms2_evidence
from xic_extractor.extraction.scoring_factory import (
    is_credible_istd_peak,
    paired_istd_fwhm,
    selected_candidate,
    selected_shape_metrics,
)
from xic_extractor.extraction.targeted_peak_selection import (
    assess_targeted_candidate_population,
    discovery_settings_for_tag,
    prepare_targeted_method_seeds,
)
from xic_extractor.extraction.trace_context import targeted_extraction_trace_group
from xic_extractor.ms2_scan_models import Ms2Window
from xic_extractor.output.messages import (
    DiagnosticRecord,
    build_diagnostic_records,
    istd_confidence_note,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
)
from xic_extractor.peak_detection.physical_peak_candidate_adapter import (
    physical_candidates_result,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    enumerate_physical_segment_universe,
    prepare_physical_signal,
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
            if not targets:
                return result_models.FileResult(sample_name=sample_name, results={}), []
            start, end = raw.acquisition_rt_bounds()
            sample_window = (
                acquire_ms2_window(
                    raw, sample_key=sample_name, rt_min=start, rt_max=end
                )
                if any(target.neutral_loss_da is not None for target in targets)
                else Ms2Window(
                    sample_key=sample_name, rt_min=start, rt_max=end, events=()
                )
            )
            seeds_by_tag = prepare_sample_method_seeds(raw, sample_window, targets)
            results: dict[str, ExtractionResult] = {}
            diagnostics: list[DiagnosticRecord] = []
            istd_shape_metrics_by_label: dict[str, tuple[float, float | None]] = {}
            istd_confidence_by_label = {}
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
                    sample_window=sample_window,
                    method_seeds=seeds_by_tag.get(target.cid_nl_tag),
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
                    sample_window=sample_window,
                    method_seeds=seeds_by_tag.get(target.cid_nl_tag),
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
    sample_window: Ms2Window | None = None,
    method_seeds: tuple | None = None,
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
    # Coverage comes from the acquired sample, never a method RT window or NL anchor.
    # Missing MS2 still receives the same exact-mass MS1 candidate enumeration.
    selection_rt_min, selection_rt_max = raw.acquisition_rt_bounds()
    context_rt_min, context_rt_max = selection_rt_min, selection_rt_max
    anchor_rt = target_reference_rt
    rt, intensity = raw.extract_xic(
        target.mz,
        context_rt_min,
        context_rt_max,
        target.ppm_tol,
    )
    if sample_window is None:
        sample_window = (
            acquire_ms2_window(
                raw,
                sample_key=sample_name,
                rt_min=context_rt_min,
                rt_max=context_rt_max,
            )
            if target.neutral_loss_da is not None
            else Ms2Window(
                sample_key=sample_name,
                rt_min=context_rt_min,
                rt_max=context_rt_max,
                events=(),
            )
        )
    if method_seeds is None and target.neutral_loss_da is not None:
        method_seeds = prepare_sample_method_seeds(raw, sample_window, [target])[
            target.cid_nl_tag
        ]
    nl_result = check_target_nl(
        raw, target, config, sample_key=sample_name, window=sample_window
    )
    profile = resolve_signal_processing_profile(
        signal_processing_method=config.signal_processing_method,
        signal_processing_window_points=config.signal_processing_window_points,
    )
    rt_array = np.asarray(rt, dtype=float)
    prepared = prepare_physical_signal(rt_array, intensity, profile=profile)
    universe = enumerate_physical_segment_universe(
        prepared,
        request_context_rt_start_min=(
            float(rt_array[0])
            if rt_array.ndim == 1 and rt_array.size
            else context_rt_min
        ),
        request_context_rt_end_min=(
            float(rt_array[-1])
            if rt_array.ndim == 1 and rt_array.size
            else context_rt_max
        ),
    )
    physical_candidates = physical_candidates_result(prepared, universe)
    method_window = sample_window
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
        method_seeds=method_seeds,
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
    center = (
        target.expected_rt_min
        if target.expected_rt_min is not None
        else (target.rt_min + target.rt_max) / 2.0
    )
    if (
        not target.is_istd
        and target.istd_pair
        and paired_target is not None
        and reference_rt is not None
    ):
        paired_center = (
            paired_target.expected_rt_min
            if paired_target.expected_rt_min is not None
            else (paired_target.rt_min + paired_target.rt_max) / 2.0
        )
        return center + reference_rt - paired_center
    return center + sample_drift


def credible_istd_anchor_rt(result: ExtractionResult | None) -> float | None:
    """Return the selected ISTD MS1 RT only when it is credible as a pair anchor."""
    if result is None:
        return None
    projection = result.targeted_product_projection
    if projection is not None and not projection.counted_detection:
        return None
    if not is_credible_istd_peak(result.peak_result):
        return None
    rt = result.reported_rt
    area = result.reported_peak_area
    if not _finite_positive(rt) or not _finite_positive(area):
        return None
    assert rt is not None
    return float(rt)


def _finite_positive(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def prepare_sample_method_seeds(
    raw: Any, window: Ms2Window, targets: list[Target]
) -> dict[str, tuple]:
    """Acquire shared parent-MS1 verification once, then resolve shared scan events."""
    from xic_extractor.discovery.strict_seed_receipts import (
        read_parent_ms1_observations,
        resolve_discovery_scan_events,
    )

    prepared = prepare_targeted_method_seeds(window, targets)
    return {
        tag: resolve_discovery_scan_events(
            read_parent_ms1_observations(
                raw,
                seeds,
                tolerance_ppm=discovery_settings_for_tag(
                    tag
                ).precursor_mz_tolerance_ppm,
            ),
            settings=discovery_settings_for_tag(tag),
        )
        for tag, seeds in prepared.items()
    }
