"""Acquire and evaluate bounded physical peak searches without method evidence.

The method supplies an opaque selection subject, coordinates and permission to
extend sparse context. This adapter owns batching and signal reuse; the pure
physical peak owner still owns preparation, selection and integration.
"""

from __future__ import annotations

import math
from collections import defaultdict
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
from xic_extractor.peak_detection.physical_peak_evaluation import (
    answer_physical_peak_question,
    enumerate_physical_segment_universe,
    mint_physical_peak_question,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import (
    DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
    PhysicalPeakEvaluation,
    PhysicalSegmentUniverse,
    PreparedSignalResult,
)
from xic_extractor.processing_identity import AcquisitionSourceIdentity
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
    """Sample-local acquisition and physical results shared by method hits.

    One instance answers one ``capture`` population; ``reselect`` extends it.
    """

    def __init__(
        self,
        raw: BatchXICAdapter,
        *,
        acquisition_source: AcquisitionSourceIdentity,
        adapter_namespace: str,
        profile: SignalProcessingProfile,
    ) -> None:
        self._raw = raw
        self._capability = mint_trace_acquisition_capability(
            acquisition_source,
            adapter_namespace=adapter_namespace,
            coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
        )
        self._profile = profile
        self._prepared: dict[
            tuple[tuple[float, ...], tuple[float, ...], SignalProcessingProfile],
            PreparedSignalResult,
        ] = {}
        self._populations: dict[
            XICRequest, tuple[PreparedSignalResult, PhysicalSegmentUniverse]
        ] = {}
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
        """Build shared peak populations before answering individual questions.

        Only identical mass queries share signal. Overlapping local windows
        form a common context; neither a question nor a later reselect crops it.
        One bounded extension round applies to the entire affected population.
        """
        unique: dict[tuple[str, float, XICRequest], PhysicalPeakSearch] = {}
        for search in searches:
            unique.setdefault(_question_key(search), search)
        contexts = _shared_contexts(tuple(unique.values()))
        traces = dict(zip(contexts, self._acquire(tuple(contexts)), strict=True))
        self.initial_request_count = len(contexts)
        ordinals = {key: index for index, key in enumerate(unique)}
        results = {}
        extended_searches: dict[tuple[str, float, XICRequest], PhysicalPeakSearch] = {}
        for request, members in contexts.items():
            lower, upper = request.rt_min, request.rt_max
            for original in members:
                key = _question_key(original)
                result = self._evaluate(
                    ordinals[key], replace(original, request=request), traces[request]
                )
                results[key] = result
                expanded = _expanded_request(result, self._profile)
                if expanded is not None:
                    lower = min(lower, expanded.rt_min)
                    upper = max(upper, expanded.rt_max)
            shared = replace(request, rt_min=lower, rt_max=upper)
            extended_searches.update(
                (_question_key(s), replace(s, request=shared)) for s in members
            )

        # An extension can connect two previously separate contexts. Re-union
        # before reacquiring so no question retains a smaller competing view.
        final_contexts = _shared_contexts(tuple(extended_searches.values()))
        missing = tuple(request for request in final_contexts if request not in traces)
        traces.update(zip(missing, self._acquire(missing), strict=True))
        self.reacquisition_request_count = len(missing)
        final_requests = {
            s: request for request, members in final_contexts.items() for s in members
        }
        for key, original in unique.items():
            request = final_requests[extended_searches[key]]
            if results[key].search.request != request:
                results[key] = self._evaluate(
                    ordinals[key], replace(original, request=request), traces[request]
                )
        # Different input windows can now ask exactly the same final question.
        # Keep a single completed work item for that question, not two aliases.
        for key, result in results.items():
            final_key = _question_key(result.search)
            if final_key not in self._results:
                self._results[final_key] = replace(
                    result, work_ordinal=len(self._results)
                )
            results[key] = self._results[final_key]
        self._next_work_ordinal = len(self._results)
        return tuple(results[_question_key(search)] for search in searches)

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

    def _acquire(self, requests: Sequence[XICRequest]) -> tuple[AcquiredTrace, ...]:
        traces: list[AcquiredTrace] = []
        for start in range(0, len(requests), PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS):
            chunk = requests[start : start + PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS]
            batch = acquire_trace_batch(self._capability, tuple(chunk), self._raw)
            self._batches.append(batch)
            traces.extend(batch.traces)
        return tuple(traces)

    def _evaluate(
        self, ordinal: int, search: PhysicalPeakSearch, acquired: AcquiredTrace
    ) -> CapturedPhysicalPeak:
        rt, intensity = acquired.trace.rt, acquired.trace.intensity
        population = self._populations.get(search.request)
        if population is None:
            cache_key = (
                tuple(map(float, rt)),
                tuple(map(float, intensity)),
                self._profile,
            )
            prepared = self._prepared.get(cache_key)
            if prepared is None:
                prepared = prepare_physical_signal(
                    rt,
                    intensity,
                    profile=self._profile,
                )
                self._prepared[cache_key] = prepared
            universe = enumerate_physical_segment_universe(
                prepared,
                request_context_rt_start_min=search.request.rt_min,
                request_context_rt_end_min=search.request.rt_max,
                selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
            )
            population = prepared, universe
            self._populations[search.request] = population
        prepared, universe = population
        question = mint_physical_peak_question(
            prepared.projection,
            universe,
            query_rt_min=search.query_rt_min,
            selection_policy_version=DISCOVERY_PHYSICAL_PEAK_POINT_POLICY,
        )
        evaluation = answer_physical_peak_question(prepared, universe, question)
        return CapturedPhysicalPeak(
            work_ordinal=ordinal,
            search=search,
            acquired_trace=acquired,
            evaluation=evaluation,
            trace_quality=_trace_quality(evaluation, rt, intensity),
        )


def _question_key(search: PhysicalPeakSearch) -> tuple[str, float, XICRequest]:
    return search.selection_subject_id, search.query_rt_min, search.request


def _shared_contexts(
    searches: Sequence[PhysicalPeakSearch],
) -> dict[XICRequest, tuple[PhysicalPeakSearch, ...]]:
    """Union overlapping local windows, never nearby but different mass queries."""
    channels: dict[tuple[float, float], list[PhysicalPeakSearch]] = defaultdict(list)
    for search in searches:
        channels[search.request.mz, search.request.ppm_tol].append(search)
    contexts: dict[XICRequest, tuple[PhysicalPeakSearch, ...]] = {}
    for channel in sorted(channels):
        request: XICRequest | None = None
        members: list[PhysicalPeakSearch] = []
        for search in sorted(
            channels[channel], key=lambda s: (s.request.rt_min, s.request.rt_max)
        ):
            if request is None or search.request.rt_min > request.rt_max:
                if request is not None:
                    contexts[request] = tuple(members)
                request, members = search.request, [search]
            else:
                request = replace(
                    request, rt_max=max(request.rt_max, search.request.rt_max)
                )
                members.append(search)
        if request is not None:
            contexts[request] = tuple(members)
    return contexts


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
            if segment == evaluation.answer.selected_segment:
                raw_seg = np.asarray(
                    intensity[segment.start_index : segment.end_index], dtype=float
                )
                if is_provisional_peak_shape(
                    raw_seg,
                    apex_index=segment.apex_index - segment.start_index,
                    signal_quality=(
                        evaluation.integration.signal_quality
                        if evaluation.integration is not None
                        else None
                    ),
                ):
                    return "provisional"
                break
    return "clean"
