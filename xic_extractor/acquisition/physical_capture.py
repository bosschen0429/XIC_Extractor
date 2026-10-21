"""Acquire and evaluate bounded physical peak searches without method evidence.

The method supplies an opaque selection subject, coordinates and permission to
extend sparse context. This adapter owns batching and signal reuse; the pure
physical peak owner still owns preparation, selection and integration.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    BatchXICAdapter,
    TraceAcquisitionBatch,
    TraceCoordinateKind,
    acquire_trace_batch,
    mint_trace_acquisition_capability,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.background_shape import is_provisional_peak_shape
from xic_extractor.peak_detection.ms1_morphology import DEFAULT_GAUSSIAN15_WINDOW_POINTS
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_identity import (
    mint_physical_trace_content_id,
)
from xic_extractor.peak_detection.physical_peak_models import (
    DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    PHYSICAL_PEAK_POINT_POLICY,
    PhysicalPeakEvaluation,
    PreparedSignalResult,
)
from xic_extractor.peak_detection.scoring_metrics import short_scale_noise_mad
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.raw_superwindow_source import RawSuperWindowSource
from xic_extractor.xic_models import XICRequest

PHYSICAL_CONTEXT_HALF_WINDOW_MIN = 0.60
PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS = 256


@dataclass(frozen=True, slots=True)
class PhysicalPeakSearch:
    """A method-selected physical question, not an MS2 or chemistry record."""

    selection_subject_id: str
    query_rt_min: float
    request: XICRequest
    allow_sparse_reacquisition: bool


@dataclass(frozen=True, slots=True, eq=False)
class CapturedPhysicalPeak:
    work_ordinal: int
    search: PhysicalPeakSearch
    acquired_trace: AcquiredTrace
    evaluation: PhysicalPeakEvaluation
    trace_quality: str


class PhysicalPeakCapture:
    """Sample-local acquisition and physical results shared by method hits."""

    def __init__(
        self,
        raw: BatchXICAdapter,
        *,
        acquisition_source: AcquisitionSourceIdentity,
        adapter_namespace: str,
        profile: SignalProcessingProfile,
    ) -> None:
        self._raw = RawSuperWindowSource(raw)
        self._capability = mint_trace_acquisition_capability(
            acquisition_source,
            adapter_namespace=adapter_namespace,
            coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
        )
        self._profile = profile
        self._prepared: dict[tuple[str, str], PreparedSignalResult] = {}
        self._results: dict[tuple[str, float, XICRequest], CapturedPhysicalPeak] = {}
        self._batches: list[TraceAcquisitionBatch] = []
        self._next_work_ordinal = 0
        self.initial_request_count = 0
        self.reacquisition_request_count = 0

    @property
    def acquisition_batches(self) -> tuple[TraceAcquisitionBatch, ...]:
        return tuple(self._batches)

    def capture(
        self, searches: Sequence[PhysicalPeakSearch]
    ) -> tuple[CapturedPhysicalPeak, ...]:
        """Batch distinct questions, extend context once, return input order."""
        unique: dict[tuple[str, float, XICRequest], PhysicalPeakSearch] = {}
        for search in searches:
            unique.setdefault(_question_key(search), search)
        physical_searches = tuple(unique.values())
        acquired = self._acquire(physical_searches)
        self.initial_request_count = len(physical_searches)
        results = [
            self._evaluate(index, search, trace)
            for index, (search, trace) in enumerate(
                zip(physical_searches, acquired, strict=True)
            )
        ]
        expanded = [
            (index, replace(result.search, request=request))
            for index, result in enumerate(results)
            if (request := _expanded_request(result, self._profile)) is not None
        ]
        extended_traces = self._acquire(tuple(search for _, search in expanded))
        self.reacquisition_request_count = len(expanded)
        for (index, search), trace in zip(expanded, extended_traces, strict=True):
            results[index] = self._evaluate(index, search, trace)
        self._results = {_question_key(result.search): result for result in results}
        self._next_work_ordinal = len(results)
        by_original_question = dict(zip(unique, results, strict=True))
        return tuple(by_original_question[_question_key(search)] for search in searches)

    def reselect(
        self,
        captured: CapturedPhysicalPeak,
        *,
        selection_subject_id: str,
        query_rt_min: float,
    ) -> CapturedPhysicalPeak:
        """Answer another question on retained signal, without another RAW read."""
        search = replace(
            captured.search,
            selection_subject_id=selection_subject_id,
            query_rt_min=query_rt_min,
        )
        key = _question_key(search)
        result = self._results.get(key)
        if result is None:
            result = self._evaluate(
                self._next_work_ordinal, search, captured.acquired_trace
            )
            self._next_work_ordinal += 1
            self._results[key] = result
        return result

    def _acquire(
        self, searches: Sequence[PhysicalPeakSearch]
    ) -> tuple[AcquiredTrace, ...]:
        traces: list[AcquiredTrace] = []
        for start in range(0, len(searches), PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS):
            chunk = searches[start : start + PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS]
            batch = acquire_trace_batch(
                self._capability, tuple(search.request for search in chunk), self._raw
            )
            self._batches.append(batch)
            traces.extend(batch.traces)
        return tuple(traces)

    def _evaluate(
        self, ordinal: int, search: PhysicalPeakSearch, acquired: AcquiredTrace
    ) -> CapturedPhysicalPeak:
        rt, intensity = acquired.trace.rt, acquired.trace.intensity
        trace_content_id = mint_physical_trace_content_id(rt, intensity)
        cache_key = (trace_content_id, self._profile.profile_id)
        prepared = self._prepared.get(cache_key)
        if prepared is None:
            prepared = prepare_physical_signal(
                rt, intensity, trace_content_id=trace_content_id, profile=self._profile
            )
            self._prepared[cache_key] = prepared
        evaluation = evaluate_physical_peak(
            prepared,
            selection_subject_id=search.selection_subject_id,
            query_rt_min=search.query_rt_min,
            request_context_rt_start_min=search.request.rt_min,
            request_context_rt_end_min=search.request.rt_max,
            selection_policy_version=(
                DISCOVERY_PHYSICAL_PEAK_POINT_POLICY
                if self._profile.window_points == DEFAULT_GAUSSIAN15_WINDOW_POINTS
                else PHYSICAL_PEAK_POINT_POLICY
            ),
        )
        return CapturedPhysicalPeak(
            work_ordinal=ordinal,
            search=search,
            acquired_trace=acquired,
            evaluation=evaluation,
            trace_quality=_trace_quality(evaluation, rt, intensity),
        )


def _question_key(search: PhysicalPeakSearch) -> tuple[str, float, XICRequest]:
    return search.selection_subject_id, search.query_rt_min, search.request


def _expanded_request(
    captured: CapturedPhysicalPeak, profile: SignalProcessingProfile
) -> XICRequest | None:
    rt, intensity = (
        captured.acquired_trace.trace.rt,
        captured.acquired_trace.trace.intensity,
    )
    point_count = len(rt)
    if point_count < 2:
        return None
    if (
        rt.ndim != 1
        or intensity.ndim != 1
        or rt.shape != intensity.shape
        or not np.all(np.isfinite(rt))
        or not np.all(np.isfinite(intensity))
    ):
        return None
    deltas = np.diff(rt)
    if not np.all(deltas > 0.0):
        return None
    cadence_min = float(np.median(deltas))
    if not math.isfinite(cadence_min) or cadence_min <= 0.0:
        return None
    request = captured.search.request
    if point_count >= profile.window_points:
        if captured.evaluation.reason != "incomplete_peak_context":
            return None
        extra_context = max(
            PHYSICAL_CONTEXT_HALF_WINDOW_MIN, cadence_min * (profile.window_points // 2)
        )
        return replace(
            request,
            rt_min=max(0.0, request.rt_min - extra_context),
            rt_max=request.rt_max + extra_context,
        )
    if not captured.search.allow_sparse_reacquisition:
        return None
    extra_point_count = profile.window_points - point_count + 2
    extra_span_min = cadence_min * extra_point_count
    left_span_min = cadence_min * (extra_point_count // 2)
    expanded_rt_min = max(0.0, request.rt_min - left_span_min)
    actual_left_span_min = request.rt_min - expanded_rt_min
    return replace(
        request,
        rt_min=expanded_rt_min,
        rt_max=request.rt_max + (extra_span_min - actual_left_span_min),
    )


def _trace_quality(
    evaluation: PhysicalPeakEvaluation, rt: np.ndarray, intensity: np.ndarray
) -> str:
    if evaluation.state in {"unavailable", "failed"}:
        return f"physical_{evaluation.state}"
    if evaluation.facts is None:
        return "missing"
    if evaluation.requires_overlap_review:
        return "physical_shoulder_overlap"
    if evaluation.state == "available":
        for segment in evaluation.segment_universe.segments:
            if segment.physical_segment_id == evaluation.answer.physical_segment_id:
                raw_seg = np.asarray(
                    intensity[segment.start_index : segment.end_index], dtype=float
                )
                rt_seg = np.asarray(
                    rt[segment.start_index : segment.end_index], dtype=float
                )
                if is_provisional_peak_shape(
                    rt_seg,
                    raw_seg,
                    apex_index=segment.apex_index - segment.start_index,
                    raw_noise_mad=short_scale_noise_mad(rt_seg, raw_seg),
                ):
                    return "provisional"
                break
    return "clean"
