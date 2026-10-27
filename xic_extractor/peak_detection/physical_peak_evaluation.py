"""Physical peak signal preparation, segment evaluation, and answer assembly."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np
from scipy.stats import fisher_exact

from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.baseline import (
    asls_baseline_on_rt,
)
from xic_extractor.peak_detection.chrom_peak_segments import (
    ChromPeakSegmentPolicy,
    enumerate_chrom_peak_segments,
    local_apex_prominence,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.ms1_morphology import (
    canonical_gaussian_signal_traces,
)
from xic_extractor.peak_detection.physical_peak_models import (
    _PHYSICAL_PEAK_OPERATION_ORDER,
    _PHYSICAL_PEAK_SELECTION_POLICIES,
    DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    PHYSICAL_PEAK_BACKFILL_POLICY,
    PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
    PHYSICAL_PEAK_NO_SELECTION_POLICY,
    PHYSICAL_PEAK_POINT_POLICY,
    PHYSICAL_PEAK_REFERENCE_POLICY,
    PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY,
    PHYSICAL_PEAK_WINDOW_POLICY,
    PHYSICAL_SEGMENT_UNIVERSE_SCHEMA,
    PhysicalPeakAnswer,
    PhysicalPeakEvaluation,
    PhysicalPeakFacts,
    PhysicalPeakOperation,
    PhysicalPeakQuestion,
    PhysicalPeakState,
    PhysicalSegment,
    PhysicalSegmentUniverse,
    PreparedSignal,
    PreparedSignalProjection,
    PreparedSignalResult,
)
from xic_extractor.peak_detection.signal_quality import (
    BACKFILL_MIN_CONTRAST_SIGMA,
    DISCOVERY_MIN_CONTRAST_SIGMA,
    MAD_TO_SIGMA,
    measure_signal_quality,
    short_scale_noise_sigma,
)


def _physical_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    return value


def prepare_physical_signal(
    rt_values: object,
    intensity_values: object,
    *,
    profile: SignalProcessingProfile = DEFAULT_SIGNAL_PROCESSING_PROFILE,
) -> PreparedSignalResult:
    if not isinstance(profile, SignalProcessingProfile):
        raise TypeError("profile must be a SignalProcessingProfile")
    rt = _physical_vector(rt_values, field="rt_values")
    raw = _physical_vector(intensity_values, field="intensity_values")

    def projection_for(
        *, state: PhysicalPeakState, reason: str
    ) -> PreparedSignalProjection:
        return PreparedSignalProjection(
            schema_version="prepared_signal_projection",
            profile=profile,
            rt=rt,
            raw=raw,
            state=state,
            reason=reason,
        )

    trace_context = _derivable_physical_trace_context(rt)
    trace_operation: tuple[PhysicalPeakOperation, ...] = ("trace_evaluation",)
    malformed_reason = _physical_trace_failure_reason(rt, raw)
    if malformed_reason:
        projection = projection_for(
            state="failed",
            reason=malformed_reason,
        )
        return PreparedSignalResult(
            projection,
            None,
            trace_operation,
            *(trace_context or (None, None)),
        )
    if not rt:
        projection = projection_for(
            state="unavailable",
            reason="no_signal_above_baseline",
        )
        return PreparedSignalResult(projection, None, trace_operation)
    if len(rt) < profile.window_points:
        projection = projection_for(
            state="unavailable",
            reason="insufficient_points_for_profile",
        )
        return PreparedSignalResult(
            projection,
            None,
            trace_operation,
            *(trace_context or (None, None)),
        )
    if max(raw, default=0.0) <= 0.0:
        projection = projection_for(
            state="unavailable",
            reason="no_signal_above_baseline",
        )
        return PreparedSignalResult(
            projection,
            None,
            trace_operation,
            *(trace_context or (None, None)),
        )
    try:
        baseline_unclipped = asls_baseline_on_rt(
            np.asarray(rt, dtype=float),
            np.asarray(raw, dtype=float),
            lam=profile.baseline_lam,
            p=profile.baseline_p,
            n_iter=profile.baseline_n_iter,
        )
        baseline = np.maximum(baseline_unclipped, 0.0)
    except (ValueError, FloatingPointError, ArithmeticError) as exc:
        projection = projection_for(
            state="failed",
            reason=f"numerical_owner_failure:{type(exc).__name__}",
        )
        return PreparedSignalResult(
            projection,
            None,
            (*trace_operation, "asls_baseline"),
            *(trace_context or (None, None)),
        )
    try:
        smoothed, positive = canonical_gaussian_signal_traces(
            np.asarray(raw, dtype=float),
            baseline,
            profile=profile,
        )
    except (ValueError, FloatingPointError, ArithmeticError) as exc:
        projection = projection_for(
            state="failed",
            reason=f"numerical_owner_failure:{type(exc).__name__}",
        )
        return PreparedSignalResult(
            projection,
            None,
            (*trace_operation, "asls_baseline", "gaussian_smoothing"),
            *(trace_context or (None, None)),
        )
    operations: tuple[PhysicalPeakOperation, ...] = (
        "trace_evaluation",
        "asls_baseline",
        "gaussian_smoothing",
    )
    state: PhysicalPeakState = (
        "available" if float(np.max(positive)) > 0.0 else "unavailable"
    )
    reason = "" if state == "available" else "no_signal_above_baseline"
    projection = projection_for(state=state, reason=reason)
    prepared = PreparedSignal(
        projection=projection,
        profile=profile,
        rt=rt,
        raw=raw,
        baseline=tuple(float(value) for value in baseline),
        smoothed=tuple(float(value) for value in smoothed),
        positive_signal=tuple(float(value) for value in positive),
    )
    context_start, context_end = trace_context or (None, None)
    return PreparedSignalResult(
        projection,
        prepared,
        operations,
        context_start,
        context_end,
        baseline_unclipped=tuple(float(value) for value in baseline_unclipped),
    )


def enumerate_physical_segment_universe(
    prepared_result: PreparedSignalResult,
    *,
    request_context_rt_start_min: float | None = None,
    request_context_rt_end_min: float | None = None,
    selection_policy_version: str = PHYSICAL_PEAK_POINT_POLICY,
) -> PhysicalSegmentUniverse:
    if not isinstance(prepared_result, PreparedSignalResult):
        raise TypeError("prepared_result must be a PreparedSignalResult")
    if selection_policy_version not in _PHYSICAL_PEAK_SELECTION_POLICIES:
        raise ValueError("unsupported physical peak selection policy")
    universe_schema = PHYSICAL_SEGMENT_UNIVERSE_SCHEMA
    context_start, context_end = _physical_owner_context(
        prepared_result,
        request_context_rt_start_min=request_context_rt_start_min,
        request_context_rt_end_min=request_context_rt_end_min,
    )
    state = prepared_result.projection.state
    reason = prepared_result.projection.reason
    physical_segments: tuple[PhysicalSegment, ...] = ()
    prepared = prepared_result.prepared
    if state == "available" and prepared is not None:
        rt = np.asarray(prepared.rt, dtype=float)
        signal = np.asarray(prepared.positive_signal, dtype=float)
        acquired = np.asarray(prepared.raw, dtype=float)
        zero_baseline = np.zeros_like(signal)
        segment_policy = ChromPeakSegmentPolicy(
            morphology_trace_method="rt_envelope",
            min_apex_fraction_of_context=0.0,
            morphology_trace_window_points=prepared.profile.window_points,
            morphology_min_sigma_rt=prepared.profile.boundary_min_sigma_rt,
            baseline_return_fraction=0.10,
            baseline_return_min_residual=0.0,
            include_baseline_return_samples=True,
            clear_valley_max_fraction=0.50,
            min_prominence_noise_sigma=DISCOVERY_MIN_CONTRAST_SIGMA,
            min_prominence_fraction_of_envelope=(
                prepared.profile.segment_min_prominence_fraction
            ),
        )
        enumeration = enumerate_chrom_peak_segments(
            rt,
            signal,
            zero_baseline,
            quantitation_context_rt_start=context_start,
            quantitation_context_rt_end=context_end,
            policy=segment_policy,
            acquired_intensity_values=acquired,
        )
        if enumeration.status == "OK":
            segments = enumeration.segments
            if any(
                s.interval.start_index == 0 or s.interval.end_index == len(rt)
                for s in segments
            ):
                # Context-dependent baseline estimates can bridge a nearly
                # returned peak to an acquired edge. Only recover a fully
                # observed return; complete segments keep their exact values.
                near_baseline = enumerate_chrom_peak_segments(
                    rt,
                    signal,
                    zero_baseline,
                    quantitation_context_rt_start=context_start,
                    quantitation_context_rt_end=context_end,
                    policy=replace(
                        segment_policy,
                        baseline_return_min_residual=(
                            short_scale_noise_sigma(rt, acquired) or 0.0
                        )
                        / MAD_TO_SIGMA,
                    ),
                    acquired_intensity_values=acquired,
                )
                alternatives = {s.apex_index: s for s in near_baseline.segments}
                recovered = []
                for segment in segments:
                    alternative = alternatives.get(segment.apex_index)
                    interval = segment.interval
                    if (
                        (interval.start_index == 0 or interval.end_index == len(rt))
                        and alternative is not None
                        and 0 < alternative.interval.start_index
                        and alternative.interval.end_index < len(rt)
                        and interval.start_index <= alternative.interval.start_index
                        and alternative.interval.end_index <= interval.end_index
                    ):
                        segment = alternative
                    recovered.append(segment)
                segments = tuple(recovered)
            physical_segments = tuple(
                _physical_segment_from_chrom(segment) for segment in segments
            )
            physical_segments = _censoring_aware_segments(prepared, physical_segments)
            if len(physical_segments) > prepared.profile.segment_universe_limit:
                state = "unavailable"
                reason = "segment_universe_too_large"
                physical_segments = ()
        elif enumeration.status == "NO_SIGNAL":
            state, reason = "unavailable", "no_signal_above_baseline"
        elif enumeration.status == "PEAK_NOT_FOUND":
            state, reason = "unavailable", "peak_not_found"
        elif enumeration.status == "WINDOW_TOO_SHORT":
            state, reason = "unavailable", "peak_not_found"
        else:
            state, reason = "failed", "malformed_trace"
    return PhysicalSegmentUniverse(
        schema_version=universe_schema,
        prepared=prepared_result.projection,
        context_rt_start_min=context_start,
        context_rt_end_min=context_end,
        state=state,
        reason=reason,
        segments=physical_segments,
    )


def _hit_enrichment(raw: np.ndarray, left: int, right: int) -> bool:
    """Compare detections with both local flanks; zeros are left-censored.

    A fourfold enrichment and a one-sided exact test are required. Correct
    for every possible scan-centred window in this context (not just retained
    peaks). This is a screening threshold, not an empirical blank FPR claim.
    """
    width = right - left
    flank_width = max(30, width)
    before = raw[max(0, left - flank_width) : left] > 0.0
    after = raw[right : min(len(raw), right + flank_width)] > 0.0
    if min(len(before), len(after)) < 15:
        return False
    hits = int(np.count_nonzero(raw[left:right] > 0.0))
    if hits < 6:
        return False
    density = hits / width
    # The denser flank prevents a background step from masquerading as a peak.
    for flank in (before, after):
        background_hits = int(np.count_nonzero(flank))
        if density < 4.0 * max(background_hits / len(flank), 1.0 / len(flank)):
            return False
        probability = fisher_exact(
            [[hits, width - hits], [background_hits, len(flank) - background_hits]],
            alternative="greater",
        ).pvalue
        if probability * len(raw) >= 0.01:
            return False
    return True


def _positive_runs(raw: np.ndarray) -> int:
    hit = raw > 0.0
    return int(np.count_nonzero(hit & ~np.r_[False, hit[:-1]]))


def _has_continuous_core_support(
    observed_residual: np.ndarray, window_points: int
) -> bool:
    """Distinguish compact acquired cores from repeated censoring gaps.

    A compact continuous peak need not contain a whole smoothing footprint.
    Repeatedly interrupted cores require sustained acquired support instead:
    one short continuous patch cannot make an otherwise censored core ordinary.
    """
    hit = observed_residual > 0.0
    edges = np.diff(np.r_[False, hit, False].astype(int))
    starts = np.flatnonzero(edges == 1)
    stops = np.flatnonzero(edges == -1)
    if len(starts) < 3:
        # This protects an already resolved morphology peak; it does not mint
        # one from isolated hits or impose the smoothing width as a scan minimum.
        return bool(np.any(stops - starts >= 2))
    if not np.any(stops - starts >= window_points):
        return False
    acquired_per_window = np.convolve(
        hit.astype(int), np.ones(window_points, dtype=int), mode="valid"
    )
    return bool(np.all(2 * acquired_per_window > window_points))


def _censoring_aware_segments(
    prepared: PreparedSignal,
    segments: tuple[PhysicalSegment, ...],
) -> tuple[PhysicalSegment, ...]:
    """Replace intensity-valley fragments only inside enriched hit clusters.

    Use a 31-scan detection window and >=3 hits to locate dense runs.
    Qualification uses unsmoothed binary observations on independent exterior
    flanks. Intensities never locate boundaries and censored values are not
    imputed. Ordinary continuous peaks keep their existing segmentation.
    """
    raw = np.asarray(prepared.raw)
    if _positive_runs(raw) < 3:
        return segments
    hits = raw > 0.0
    counts = np.convolve(hits.astype(int), np.ones(31, dtype=int), mode="same")
    dense = counts >= 3
    edges = np.diff(np.r_[False, dense, False].astype(int))
    rt = np.asarray(prepared.rt)
    signal = np.asarray(prepared.positive_signal)
    baseline = np.asarray(prepared.baseline)
    replacements = []
    for start, stop in zip(
        np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True
    ):
        detected = np.flatnonzero(hits[start:stop]) + start
        if len(detected) < 6:
            continue
        first, last = int(detected[0]), int(detected[-1])
        if _positive_runs(raw[first : last + 1]) < 3:
            continue
        # One observed exterior zero at each boundary retains the measured
        # trapezoid shoulders, including the zeros, without inventing tails.
        left, right = max(0, first - 1), min(len(raw), last + 2)
        if not _hit_enrichment(raw, left, right):
            continue
        # A supported continuous core already has morphology-owned bounds and
        # area. Sparse tail hits do not make it censored; binary density must
        # neither widen that peak nor merge it with neighbouring detections.
        continuous_peaks = 0
        for segment in segments:
            if not left <= segment.apex_index < right:
                continue
            values = signal[segment.start_index : segment.end_index]
            core = np.flatnonzero(values >= 0.2 * segment.height)
            core_slice = slice(
                segment.start_index + core[0], segment.start_index + core[-1] + 1
            )
            observed = raw[core_slice] - baseline[core_slice]
            if _has_continuous_core_support(observed, prepared.profile.window_points):
                continuous_peaks += 1
        if continuous_peaks:
            continue
        apex = left + int(np.argmax(signal[left:right]))
        provisional = PhysicalSegment(
            start_index=left,
            end_index=right,
            apex_index=apex,
            left_rt_min=float(rt[left]),
            right_rt_min=float(rt[right - 1]),
            apex_rt_min=float(rt[apex]),
            height=float(signal[apex]),
            scan_support_count=right - left,
            area_counts_seconds=float(np.trapezoid(raw[left:right], rt[left:right]))
            * 60.0,
            segment_class="isolated_peak",
            boundary_stop_reason="detection_density_return",
            evidence_sources=("local_hit_density_enrichment", "near_detection_limit"),
        )
        replacements.append(provisional)
    retained = [
        s
        for s in segments
        if not any(r.start_index <= s.apex_index < r.end_index for r in replacements)
    ]
    return tuple(sorted((*retained, *replacements), key=lambda s: s.apex_index))


def mint_physical_peak_question(
    prepared: PreparedSignalProjection,
    universe: PhysicalSegmentUniverse,
    *,
    query_rt_min: float,
    selection_policy_version: str = PHYSICAL_PEAK_POINT_POLICY,
    search_rt_window: tuple[float, float] | None = None,
) -> PhysicalPeakQuestion:
    if not isinstance(prepared, PreparedSignalProjection):
        raise TypeError("prepared must be a PreparedSignalProjection")
    if not isinstance(universe, PhysicalSegmentUniverse):
        raise TypeError("universe must be a PhysicalSegmentUniverse")
    query_rt = _finite_physical_float(query_rt_min, field="query_rt_min")
    search_rt_window = _validated_search_window(
        search_rt_window, query_rt, selection_policy_version
    )
    admissible = _admissible_physical_segments(
        universe, query_rt, selection_policy_version, search_rt_window
    )
    schema_version = (
        "bounded_physical_peak_question"
        if search_rt_window is not None
        else "physical_peak_question"
    )
    return PhysicalPeakQuestion(
        schema_version=schema_version,
        universe=universe,
        query_rt_min=query_rt,
        admissible_segments=admissible,
        selection_policy_version=selection_policy_version,
        search_rt_window=search_rt_window,
    )


def _validated_search_window(
    window: tuple[float, float] | None,
    query_rt: float,
    selection_policy_version: str,
) -> tuple[float, float] | None:
    if window is None:
        return None
    if selection_policy_version not in {
        PHYSICAL_PEAK_WINDOW_POLICY,
        PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
        PHYSICAL_PEAK_BACKFILL_POLICY,
        PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY,
    }:
        raise ValueError("search window requires a window selection policy")
    if not isinstance(window, tuple) or len(window) != 2:
        raise ValueError("search window must contain two RT bounds")
    start, end = (
        _finite_physical_float(value, field="search_rt_window") for value in window
    )
    if not 0.0 <= start <= query_rt <= end:
        raise ValueError(
            "search window must contain the query RT in non-negative bounds"
        )
    return start, end


def _admissible_physical_segments(
    universe: PhysicalSegmentUniverse,
    query_rt: float,
    selection_policy_version: str,
    search_rt_window: tuple[float, float] | None = None,
) -> tuple[PhysicalSegment, ...]:
    if selection_policy_version not in _PHYSICAL_PEAK_SELECTION_POLICIES:
        raise ValueError("unsupported physical peak selection policy")
    if universe.schema_version != PHYSICAL_SEGMENT_UNIVERSE_SCHEMA:
        raise ValueError("physical question crosses boundary policy")
    if selection_policy_version == PHYSICAL_PEAK_NO_SELECTION_POLICY:
        return ()
    if selection_policy_version in {
        PHYSICAL_PEAK_WINDOW_POLICY,
        PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
        PHYSICAL_PEAK_BACKFILL_POLICY,
        PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY,
    }:
        if not universe.context_rt_start_min <= query_rt <= universe.context_rt_end_min:
            return ()
        candidates = tuple(
            segment
            for segment in universe.segments
            if search_rt_window is None
            or search_rt_window[0] <= segment.apex_rt_min <= search_rt_window[1]
        )
        if not candidates and selection_policy_version == PHYSICAL_PEAK_BACKFILL_POLICY:
            # A complete peak can contain the forecast while its apex misses the
            # window. Recover only containing segments, never nearby context peaks.
            # The answer still enforces uniqueness, complete support and contrast.
            candidates = tuple(
                segment
                for segment in universe.segments
                if segment.left_rt_min <= query_rt <= segment.right_rt_min
            )
        # The prior resolves ambiguity only among eligible candidates.
        if selection_policy_version in {
            PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
            PHYSICAL_PEAK_BACKFILL_POLICY,
        }:
            containing = tuple(
                segment
                for segment in candidates
                if segment.left_rt_min <= query_rt <= segment.right_rt_min
            )
            if len(containing) == 1:
                return containing
        return candidates
    return tuple(
        segment
        for segment in universe.segments
        if segment.left_rt_min <= query_rt <= segment.right_rt_min
    )


def unique_physical_segment_at_rt(
    universe: PhysicalSegmentUniverse, query_rt_min: float
) -> PhysicalSegment | None:
    """Project point ownership from an already evaluated segment universe.

    In-memory consumers need the segment answer, not a new persisted question
    or another validation of the authoritative universe.
    """
    admissible = _admissible_physical_segments(
        universe, query_rt_min, PHYSICAL_PEAK_POINT_POLICY, None
    )
    return admissible[0] if len(admissible) == 1 else None


def physical_segment_has_incomplete_context(
    prepared: PreparedSignalProjection,
    segment: PhysicalSegment,
) -> bool:
    """Read clipped boundaries from trusted owner geometry."""
    return segment.start_index == 0 or segment.end_index == prepared.trace_point_count


def qualified_physical_segments(
    prepared: PreparedSignal,
    segments: tuple[PhysicalSegment, ...],
    *,
    selection_policy_version: str = PHYSICAL_PEAK_POINT_POLICY,
) -> tuple[PhysicalSegment, ...]:
    """Return segments with shared original-XIC support before candidate ranking.

    This reuses the prepared signal and measured segments.
    """
    return tuple(
        segment
        for segment in segments
        if not _physical_segment_support_failure(
            prepared, segment, selection_policy_version=selection_policy_version
        )
    )


def physical_peak_support_failures_at_reference(
    prepared: PreparedSignal,
    universe: PhysicalSegmentUniverse,
    reference_rt: float,
) -> tuple[str, ...]:
    """Explain rejected segments containing a reference without selecting a peak.

    These are diagnostic support facts, not identity or counting decisions.
    No containing segment, or any supported segment, means no shared failure
    can be attributed to the reference. Reuse the candidate gate verbatim.
    """
    failures = tuple(
        _physical_segment_support_failure(prepared, segment)
        for segment in universe.segments
        if segment.left_rt_min <= reference_rt <= segment.right_rt_min
    )
    return tuple(dict.fromkeys(failures)) if failures and all(failures) else ()


def _physical_segment_support_failure(
    prepared: PreparedSignal,
    selected: PhysicalSegment,
    *,
    selection_policy_version: str = PHYSICAL_PEAK_POINT_POLICY,
) -> str:
    if physical_segment_has_incomplete_context(prepared.projection, selected):
        return "incomplete_peak_context"
    if "local_hit_density_enrichment" in selected.evidence_sources:
        return ""
    # Smoothing may spread acquired impulses into a peak. Acquired background
    # is not support; reuse the prepared baseline for the original XIC.
    acquired = np.asarray(
        prepared.raw[selected.start_index : selected.end_index], dtype=float
    )
    original = np.maximum(
        acquired
        - np.asarray(
            prepared.baseline[selected.start_index : selected.end_index],
            dtype=float,
        ),
        0.0,
    )
    left, right, apex = (selected.start_index, selected.end_index, selected.apex_index)
    signal = np.asarray(prepared.positive_signal[left:right], dtype=float)
    apex_offset = apex - left
    prominence = float(signal[apex_offset]) - max(
        float(np.min(signal[: apex_offset + 1])),
        float(np.min(signal[apex_offset:])),
    )
    # Only acquired observations in this apex's half-prominence core support
    # its top. Low neighbors elsewhere in a long envelope cannot certify an
    # impulse. Keep this core separate from the local-valley contrast below.
    top_level = float(signal[apex_offset]) - 0.5 * prominence
    top_left = top_right = apex_offset
    while top_left > 0 and signal[top_left - 1] >= top_level:
        top_left -= 1
    while top_right + 1 < len(signal) and signal[top_right + 1] >= top_level:
        top_right += 1
    original = original[top_left : top_right + 1]
    original = np.where(original >= top_level, original, 0.0)
    if sum(value > 0.0 for value in original) < 2:
        return "insufficient_original_xic_support"
    if not any(
        left > 0.0 and right > 0.0
        for left, right in zip(original, original[1:], strict=False)
    ):
        return "insufficient_original_xic_continuity"
    # Hit density can identify additional censored clusters; it does not
    # establish the absence of an already observed morphology peak.
    # A known-reference search localizes an existing contour, rather than
    # discovering each local maximum. Surface ripples cannot erase that
    # contour's acquired support and full rise/fall. Discovery and Backfill
    # retain their nearest-valley contrast requirement.
    if selection_policy_version != PHYSICAL_PEAK_REFERENCE_POLICY:
        prominence = local_apex_prominence(signal, apex_offset)
    rt = np.asarray(prepared.rt[left:right], dtype=float)
    noise_sigma = short_scale_noise_sigma(rt, acquired) or 0.0
    minimum_contrast = (
        BACKFILL_MIN_CONTRAST_SIGMA
        if selection_policy_version
        in {PHYSICAL_PEAK_BACKFILL_POLICY, PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY}
        else DISCOVERY_MIN_CONTRAST_SIGMA
    )
    if prominence < minimum_contrast * noise_sigma:
        if selection_policy_version == PHYSICAL_PEAK_REFERENCE_POLICY:
            return "insufficient_reference_peak_contrast"
        return (
            "insufficient_backfill_peak_contrast"
            if selection_policy_version
            in {PHYSICAL_PEAK_BACKFILL_POLICY, PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY}
            else "insufficient_local_peak_contrast"
        )
    return ""


def answer_physical_peak_question(
    prepared_result: PreparedSignalResult,
    universe: PhysicalSegmentUniverse,
    question: PhysicalPeakQuestion,
) -> PhysicalPeakEvaluation:
    if not isinstance(prepared_result, PreparedSignalResult):
        raise TypeError("prepared_result must be a PreparedSignalResult")
    state = universe.state
    reason = universe.reason
    selected: PhysicalSegment | None = None
    background_qualified = False
    if state == "available":
        if not question.admissible_segments:
            state = "unavailable"
            reason = (
                "no_peak_in_sample_local_window"
                if question.selection_policy_version == PHYSICAL_PEAK_BACKFILL_POLICY
                else "no_selection"
            )
        elif len(question.admissible_segments) > 1:
            state, reason = "unavailable", "ambiguous_segment_candidates"
        else:
            selected = question.admissible_segments[0]
            prepared = prepared_result.prepared
            if prepared is None:
                raise ValueError("available physical answer lacks worker-local signal")
            support_failure = _physical_segment_support_failure(
                prepared,
                selected,
                selection_policy_version=question.selection_policy_version,
            )
            if support_failure:
                state, reason = "unavailable", support_failure
                selected = None
            elif (
                question.selection_policy_version
                == DISCOVERY_PHYSICAL_PEAK_POINT_POLICY
            ):
                # Shared acquired support and local contrast qualify the peak.
                # Overlap remains review evidence on the measured interval.
                background_qualified = True
    answer = PhysicalPeakAnswer(
        schema_version="physical_peak_answer",
        state=state,
        reason=reason,
        question=question,
        selected_segment=selected,
    )
    facts: PhysicalPeakFacts | None = None
    integration: IntegrationResult | None = None
    operations = prepared_result.operations
    if prepared_result.projection.state == "available":
        operations = (*operations, "segment_enumeration")
    if background_qualified:
        operations = (*operations, "background_qualification")
    if selected is not None:
        prepared = prepared_result.prepared
        if prepared is None:
            raise ValueError("available physical answer lacks worker-local signal")
        facts = PhysicalPeakFacts(
            schema_version="physical_peak_facts",
            apex_rt_min=selected.apex_rt_min,
            left_rt_min=selected.left_rt_min,
            right_rt_min=selected.right_rt_min,
            height=selected.height,
            scan_support_count=selected.scan_support_count,
            area_counts_seconds=selected.area_counts_seconds,
            area_source=(
                "trapezoid_counts_seconds_on_observed_censored_signal"
                if "near_detection_limit" in selected.evidence_sources
                else "trapezoid_counts_seconds_on_canonical_signal"
            ),
        )
        integration = _physical_raw_descriptor_projection(prepared, selected, facts)
        operations = (
            *operations,
            "integration",
            "raw_descriptor_projection",
        )
    return PhysicalPeakEvaluation(
        state=state,
        reason=reason,
        prepared=prepared_result.projection,
        segment_universe=universe,
        question=question,
        answer=answer,
        facts=facts,
        integration=integration,
        operations=operations,
        retained_prepared_signal=prepared_result.prepared,
    )


def evaluate_physical_peak(
    prepared_result: PreparedSignalResult,
    *,
    query_rt_min: float,
    request_context_rt_start_min: float | None = None,
    request_context_rt_end_min: float | None = None,
    selection_policy_version: str = PHYSICAL_PEAK_POINT_POLICY,
    search_rt_window: tuple[float, float] | None = None,
) -> PhysicalPeakEvaluation:
    """Answer one workflow-neutral physical question from owner-derived context."""

    if not isinstance(prepared_result, PreparedSignalResult):
        raise TypeError("prepared_result must be a PreparedSignalResult")
    universe = enumerate_physical_segment_universe(
        prepared_result,
        request_context_rt_start_min=request_context_rt_start_min,
        request_context_rt_end_min=request_context_rt_end_min,
        selection_policy_version=selection_policy_version,
    )
    question = mint_physical_peak_question(
        prepared_result.projection,
        universe,
        query_rt_min=query_rt_min,
        selection_policy_version=selection_policy_version,
        search_rt_window=search_rt_window,
    )
    return answer_physical_peak_question(prepared_result, universe, question)


def _physical_raw_descriptor_projection(
    prepared: PreparedSignal,
    selected: PhysicalSegment,
    facts: PhysicalPeakFacts,
) -> IntegrationResult:
    rt = np.asarray(
        prepared.rt[selected.start_index : selected.end_index],
        dtype=float,
    )
    raw = np.asarray(
        prepared.raw[selected.start_index : selected.end_index],
        dtype=float,
    )
    if len(rt) < 2 or len(raw) != len(rt):
        raise ValueError("selected physical segment cannot mint a raw descriptor")
    raw_apex_offset = int(np.argmax(raw))
    raw_area_counts_seconds = float(np.trapezoid(raw, rt)) * 60.0
    return IntegrationResult(
        rt_left_min=facts.left_rt_min,
        rt_apex_min=facts.apex_rt_min,
        rt_right_min=facts.right_rt_min,
        raw_apex_rt_min=float(rt[raw_apex_offset]),
        rt_width_min=facts.right_rt_min - facts.left_rt_min,
        height_raw=float(raw[raw_apex_offset]),
        height_smoothed=float(prepared.smoothed[selected.apex_index]),
        area_raw_counts_seconds=raw_area_counts_seconds,
        integration_method="canonical_physical_signal_trapezoid",
        boundary_sources=(
            ("canonical_physical_segment", "near_detection_limit")
            if "near_detection_limit" in selected.evidence_sources
            else ("canonical_physical_segment",)
        ),
        area_baseline_corrected=facts.area_counts_seconds,
        baseline_type=prepared.profile.baseline_method,
        raw_scan_indices=(),
        area_ms1_morphology=facts.area_counts_seconds,
        ms1_morphology_area_source=facts.area_source,
        ms1_morphology_trace_method=prepared.profile.method,
        ms1_morphology_trace_window_points=prepared.profile.window_points,
        ms1_morphology_trace_effective_points=prepared.profile.window_points,
        signal_quality=measure_signal_quality(
            rt,
            raw,
            baseline=np.asarray(
                prepared.baseline[selected.start_index : selected.end_index],
                dtype=float,
            ),
            apex_index=selected.apex_index - selected.start_index,
        ),
    )


def _physical_vector(values: object, *, field: str) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field} must be a one-dimensional numeric sequence")
    try:
        array = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field} must be a one-dimensional numeric sequence") from exc
    if array.ndim != 1:
        raise ValueError(f"{field} must be one-dimensional")
    return tuple(float(value) for value in array)


def _finite_physical_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def validate_prepared_signal(projection: PreparedSignalProjection) -> None:
    if projection.schema_version != "prepared_signal_projection":
        raise ValueError("unsupported prepared signal projection schema")
    if projection.state not in {"available", "unavailable", "failed"}:
        raise ValueError("unsupported prepared signal state")
    if (projection.state == "available") != (projection.reason == ""):
        raise ValueError("prepared signal state/reason is not canonical")


def validate_physical_segment_universe(universe: PhysicalSegmentUniverse) -> None:
    if universe.schema_version != PHYSICAL_SEGMENT_UNIVERSE_SCHEMA:
        raise ValueError("unsupported physical segment universe schema")
    if universe.state not in {"available", "unavailable", "failed"}:
        raise ValueError("unsupported physical segment universe state")
    if (universe.state == "available") != (universe.reason == ""):
        raise ValueError("physical segment universe state/reason is not canonical")
    if universe.state == "available" and not universe.segments:
        raise ValueError("available physical segment universe must not be empty")
    start = _finite_physical_float(
        universe.context_rt_start_min, field="context_rt_start_min"
    )
    end = _finite_physical_float(
        universe.context_rt_end_min, field="context_rt_end_min"
    )
    if start > end:
        raise ValueError("physical segment universe context is reversed")


def validate_physical_peak_question(question: PhysicalPeakQuestion) -> None:
    expected_schema = (
        "bounded_physical_peak_question"
        if question.search_rt_window is not None
        else "physical_peak_question"
    )
    if question.schema_version != expected_schema:
        raise ValueError("unsupported physical peak question schema")
    _validated_search_window(
        question.search_rt_window,
        question.query_rt_min,
        question.selection_policy_version,
    )
    query_rt = _finite_physical_float(question.query_rt_min, field="query_rt_min")
    if question.admissible_segments != _admissible_physical_segments(
        question.universe,
        query_rt,
        question.selection_policy_version,
        question.search_rt_window,
    ):
        raise ValueError("physical question omits owner-admissible segments")


def validate_physical_peak_answer(answer: PhysicalPeakAnswer) -> None:
    if answer.schema_version != "physical_peak_answer":
        raise ValueError("unsupported physical peak answer schema")
    if answer.state not in {"available", "unavailable", "failed"}:
        raise ValueError("unsupported physical peak answer state")
    if (answer.state == "available") != (answer.reason == ""):
        raise ValueError("physical peak answer state/reason is not canonical")
    if (answer.state == "available") != (answer.selected_segment is not None):
        raise ValueError("physical peak answer selection is not canonical")
    if (
        answer.selected_segment is not None
        and answer.selected_segment not in answer.question.admissible_segments
    ):
        raise ValueError("physical peak answer selects outside admissible segments")


def validate_bounded_physical_peak_evaluation(
    evaluation: PhysicalPeakEvaluation,
) -> None:
    if not isinstance(evaluation, PhysicalPeakEvaluation):
        raise TypeError("evaluation must be a PhysicalPeakEvaluation")
    qualified_policy = (
        evaluation.question.selection_policy_version
        == DISCOVERY_PHYSICAL_PEAK_POINT_POLICY
    )
    operation_order = tuple(
        operation
        for operation in _PHYSICAL_PEAK_OPERATION_ORDER
        if qualified_policy or operation != "background_qualification"
    )
    terminal_prefix_length = 5 if qualified_policy else 4
    allowed_operation_sequences = tuple(
        operation_order[:count]
        for count in (
            1,
            2,
            3,
            4,
            terminal_prefix_length,
            len(operation_order) - 1,
            len(operation_order),
        )
    )
    if evaluation.operations not in allowed_operation_sequences:
        raise ValueError("physical evaluation operations are not canonical")
    validate_prepared_signal(evaluation.prepared)
    validate_physical_segment_universe(evaluation.segment_universe)
    validate_physical_peak_question(evaluation.question)
    validate_physical_peak_answer(evaluation.answer)
    if evaluation.prepared != evaluation.segment_universe.prepared:
        raise ValueError("physical evaluation crosses prepared signal")
    if evaluation.question.universe != evaluation.segment_universe:
        raise ValueError("physical evaluation crosses segment universe")
    if evaluation.answer.question != evaluation.question:
        raise ValueError("physical evaluation crosses question")
    if (
        evaluation.state != evaluation.answer.state
        or evaluation.reason != evaluation.answer.reason
    ):
        raise ValueError("bounded physical evaluation crosses terminal state")
    if evaluation.state == "available":
        if (
            evaluation.facts is None
            or evaluation.integration is None
            or evaluation.answer.selected_segment is None
        ):
            raise ValueError("available physical evaluation requires complete facts")
        facts = evaluation.facts
        integration = evaluation.integration
        if not isinstance(integration, IntegrationResult):
            raise ValueError("available physical evaluation has invalid integration")
        if evaluation.operations not in {
            operation_order[:-1],
            operation_order,
        }:
            raise ValueError("available physical evaluation lacks completed operations")
        if facts.schema_version != "physical_peak_facts":
            raise ValueError("unsupported physical peak facts schema")
        segment = evaluation.answer.selected_segment
        if (
            facts.apex_rt_min != segment.apex_rt_min
            or facts.left_rt_min != segment.left_rt_min
            or facts.right_rt_min != segment.right_rt_min
            or facts.height != segment.height
            or facts.scan_support_count != segment.scan_support_count
            or facts.area_counts_seconds != segment.area_counts_seconds
        ):
            raise ValueError("physical facts cross selected segment")
        if (
            integration.rt_left_min != facts.left_rt_min
            or integration.rt_apex_min != facts.apex_rt_min
            or integration.rt_right_min != facts.right_rt_min
            or integration.area_baseline_corrected != facts.area_counts_seconds
            or integration.area_ms1_morphology != facts.area_counts_seconds
            or integration.raw_scan_indices
        ):
            raise ValueError("bounded raw descriptor crosses physical facts")
    elif (
        evaluation.facts is not None
        or evaluation.integration is not None
        or evaluation.answer.selected_segment is not None
    ):
        raise ValueError("non-available physical evaluation cannot carry facts")
    elif len(evaluation.operations) > terminal_prefix_length:
        raise ValueError("non-available physical evaluation claims fact operations")


def _physical_segment_from_chrom(segment: Any) -> PhysicalSegment:
    return PhysicalSegment(
        start_index=segment.interval.start_index,
        end_index=segment.interval.end_index,
        apex_index=segment.apex_index,
        left_rt_min=segment.interval.rt_start_min,
        right_rt_min=segment.interval.rt_end_min,
        apex_rt_min=segment.apex_rt_min,
        height=segment.morphology_apex_residual,
        scan_support_count=segment.interval.scan_count,
        area_counts_seconds=segment.area_baseline_corrected,
        segment_class=segment.segment_class,
        boundary_stop_reason=segment.boundary_stop_reason,
        evidence_sources=segment.evidence_sources,
    )


def _physical_trace_failure_reason(
    rt: tuple[float, ...], raw: tuple[float, ...]
) -> str:
    if len(rt) != len(raw):
        return "length_mismatch"
    if not all(math.isfinite(value) for value in (*rt, *raw)):
        return "nonfinite_trace"
    if any(right <= left for left, right in zip(rt, rt[1:], strict=False)):
        return "non_monotonic_rt"
    return ""


def _derivable_physical_trace_context(
    rt: tuple[float, ...],
) -> tuple[float, float] | None:
    if not rt:
        return None
    start = float(rt[0])
    end = float(rt[-1])
    if not math.isfinite(start) or not math.isfinite(end) or start > end:
        return None
    return start, end


def _validated_request_context(
    start_value: float | None,
    end_value: float | None,
) -> tuple[float, float] | None:
    if start_value is None and end_value is None:
        return None
    if start_value is None or end_value is None:
        raise ValueError("request context bounds must be supplied together")
    start = _finite_physical_float(
        start_value,
        field="request_context_rt_start_min",
    )
    end = _finite_physical_float(
        end_value,
        field="request_context_rt_end_min",
    )
    if start > end:
        raise ValueError("request context start must not exceed end")
    return start, end


def _physical_owner_context(
    prepared_result: PreparedSignalResult,
    *,
    request_context_rt_start_min: float | None,
    request_context_rt_end_min: float | None,
) -> tuple[float, float]:
    request_context = _validated_request_context(
        request_context_rt_start_min,
        request_context_rt_end_min,
    )
    if (
        prepared_result.trace_context_rt_start_min is not None
        and prepared_result.trace_context_rt_end_min is not None
    ):
        return (
            prepared_result.trace_context_rt_start_min,
            prepared_result.trace_context_rt_end_min,
        )
    if request_context is None:
        raise ValueError(
            "request context is required when trace context cannot be derived"
        )
    return request_context


def _physical_state(value: object, *, field: str) -> PhysicalPeakState:
    text = _physical_text(value, field=field)
    if text not in {"available", "unavailable", "failed"}:
        raise ValueError(f"{field} is not a physical terminal state")
    return text  # type: ignore[return-value]


def _physical_operation(value: object) -> PhysicalPeakOperation:
    text = _physical_text(value, field="physical operation")
    if text not in _PHYSICAL_PEAK_OPERATION_ORDER:
        raise ValueError("unsupported physical operation")
    return text
