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
from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.peak_detection.background_shape import is_provisional_peak_shape
from xic_extractor.peak_detection.physical_peak_evaluation import (
    discovery_raw_peak_components,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
    PhysicalSegment,
    RawPeakComponent,
)
from xic_extractor.peak_detection.physical_peak_population import (
    PhysicalPeakPopulation,
    measure_physical_peak_population,
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
        self._populations: dict[
            XICRequest,
            tuple[
                AcquiredTrace,
                PhysicalPeakPopulation,
                dict[PhysicalSegment, tuple[int, PhysicalPeakEvaluation]],
            ],
        ] = {}
        self._batches: list[TraceAcquisitionBatch] = []
        self._trace_qualities: dict[int, str] = {}
        self._next_work_ordinal = 0
        self.initial_request_count = 0
        self.reacquisition_request_count = 0
        self.remeasurement_request_count = 0

    @property
    def acquisition_batches(self) -> tuple[TraceAcquisitionBatch, ...]:
        return tuple(self._batches)

    def capture(
        self, searches: Sequence[PhysicalPeakSearch]
    ) -> tuple[CapturedPhysicalPeak, ...]:
        """Acquire complete shared traces, measure once, then attach point queries.

        Raw-only context probes precede canonical preparation. They never run
        AsLS/Gaussian or quantify NL requests. Extension is bounded to one read
        per merged channel; the final trace is prepared and measured once.
        """
        unique = tuple(dict.fromkeys(searches))
        contexts = _shared_contexts(unique)
        traces = dict(zip(contexts, self._acquire(tuple(contexts)), strict=True))
        self.initial_request_count = len(contexts)
        extended: dict[PhysicalPeakSearch, PhysicalPeakSearch] = {}
        probed_components: dict[XICRequest, tuple[RawPeakComponent, ...]] = {}
        for request, members in contexts.items():
            trace = traces[request].trace
            matching_probe = next(
                (
                    previous
                    for previous in probed_components
                    if previous.ppm_tol == request.ppm_tol
                    and within_ppm(previous.mz, request.mz, request.ppm_tol)
                    and _same_acquired_signal(traces[previous], traces[request])
                ),
                None,
            )
            components = (
                probed_components[matching_probe]
                if matching_probe is not None
                else discovery_raw_peak_components(
                    trace.rt, trace.intensity, self._profile
                )
            )
            probed_components[request] = components
            expanded = _expanded_trace_request(
                request, members, trace.rt, trace.intensity, components, self._profile
            )
            extended.update(
                (member, replace(member, request=expanded or request))
                for member in members
            )
        final_contexts = _shared_contexts(tuple(extended.values()))
        missing = tuple(request for request in final_contexts if request not in traces)
        traces.update(zip(missing, self._acquire(missing), strict=True))
        self.reacquisition_request_count = len(missing)
        final_requests = {
            member: request
            for request, members in final_contexts.items()
            for member in members
        }
        # Different mass windows are never substituted during acquisition.
        # Only equality over the complete final acquired range can establish
        # that two compatible windows observed the very same signal.
        signal_owners: dict[XICRequest, XICRequest] = {}
        owner_members: dict[XICRequest, list[XICRequest]] = {}
        for request in sorted(
            final_contexts, key=lambda r: (r.rt_min, r.rt_max, r.mz, r.ppm_tol)
        ):
            for owner, mass_windows in owner_members.items():
                if (
                    request.ppm_tol == owner.ppm_tol
                    and all(
                        within_ppm(request.mz, member.mz, request.ppm_tol)
                        for member in mass_windows
                    )
                    and _same_acquired_signal(traces[request], traces[owner])
                ):
                    signal_owners[request] = owner
                    mass_windows.append(request)
                    break
            else:
                signal_owners[request] = request
                owner_members[request] = [request]
        final_requests = {
            search: signal_owners[request] for search, request in final_requests.items()
        }
        # A population, not a question, owns the canonical preparation and all
        # segment measurements. NL count cannot change this work.
        for request in owner_members:
            self._measure_population(
                request, traces[request], probed_components.get(request)
            )
        return tuple(
            self._lookup(replace(search, request=final_requests[extended[search]]))
            for search in searches
        )

    def remeasure(
        self, changes: Sequence[tuple[CapturedPhysicalPeak, float]]
    ) -> tuple[CapturedPhysicalPeak, ...]:
        """Measure captured peaks again in mass windows centred on new m/z values.

        Each new window keeps its peak's RT context and tolerance, so only the
        mass window changes. The peak is selected at its raw apex; a window
        that selects nothing there returns an answer without facts.
        """
        searches = tuple(
            replace(
                captured.search,
                query_rt_min=captured.evaluation.question.query_rt_min,
                request=replace(captured.search.request, mz=mz),
            )
            for captured, mz in changes
        )
        missing = tuple(
            dict.fromkeys(
                search.request
                for search in searches
                if search.request not in self._populations
            )
        )
        self.remeasurement_request_count += len(missing)
        for request, acquired in zip(missing, self._acquire(missing), strict=True):
            self._measure_population(request, acquired, None)
        return tuple(self._lookup(search) for search in searches)

    def _measure_population(
        self,
        request: XICRequest,
        acquired: AcquiredTrace,
        components: tuple[RawPeakComponent, ...] | None,
    ) -> None:
        population = measure_physical_peak_population(
            acquired.trace.rt,
            acquired.trace.intensity,
            context_rt_start_min=request.rt_min,
            context_rt_end_min=request.rt_max,
            profile=self._profile,
            components=components,
        )
        measurements = {}
        for segment in population.universe.segments:
            measurements[segment] = (
                self._next_work_ordinal,
                population.measure_segment(segment),
            )
            self._next_work_ordinal += 1
        self._populations[request] = acquired, population, measurements

    def reselect(
        self,
        captured: CapturedPhysicalPeak,
        *,
        selection_subject_id: str,
        query_rt_min: float,
    ) -> CapturedPhysicalPeak:
        """Attach another NL event to an existing measurement; no science rerun."""
        return self._lookup(
            replace(
                captured.search,
                selection_subject_id=selection_subject_id,
                query_rt_min=query_rt_min,
            )
        )

    def _lookup(self, search: PhysicalPeakSearch) -> CapturedPhysicalPeak:
        acquired, population, measurements = self._populations[search.request]
        segment = population.segment_at(search.query_rt_min)
        if segment is not None:
            ordinal, evaluation = measurements[segment]
        else:
            # Unassigned queries retain an explicit no-selection answer. There
            # is no measurement to repeat and no prepared-signal evaluation.
            evaluation = population.unselected(search.query_rt_min)
            ordinal = self._next_work_ordinal
            self._next_work_ordinal += 1
        if ordinal not in self._trace_qualities:
            self._trace_qualities[ordinal] = _trace_quality(
                evaluation, acquired.trace.rt, acquired.trace.intensity
            )
        return CapturedPhysicalPeak(
            ordinal, search, acquired, evaluation, self._trace_qualities[ordinal]
        )

    def _acquire(self, requests: Sequence[XICRequest]) -> tuple[AcquiredTrace, ...]:
        traces: list[AcquiredTrace] = []
        for start in range(0, len(requests), PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS):
            chunk = requests[start : start + PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS]
            batch = acquire_trace_batch(self._capability, tuple(chunk), self._raw)
            self._batches.append(batch)
            traces.extend(batch.traces)
        return tuple(traces)


def _shared_contexts(
    searches: Sequence[PhysicalPeakSearch],
) -> dict[XICRequest, tuple[PhysicalPeakSearch, ...]]:
    """Share RT planning, retaining every distinct mass window's read semantics."""
    channels: list[list[PhysicalPeakSearch]] = []
    # Discovery's existing mass grouping requires every member within ppm of
    # every other member. No transitive mass chaining or isotope merging.
    # Searches arrive in ascending (ppm, m/z), so each channel's first member is
    # its lowest and farthest m/z: the search is within ppm of every member iff
    # it is within ppm of the first. A channel that fails stays failed for every
    # later search, so only the oldest live channel can accept the next one.
    live = 0
    for search in sorted(
        searches,
        key=lambda s: (
            s.request.ppm_tol,
            s.request.mz,
            s.request.rt_min,
            s.query_rt_min,
            s.selection_subject_id,
        ),
    ):
        request = search.request
        while live < len(channels):
            first = channels[live][0].request
            if first.ppm_tol == request.ppm_tol and within_ppm(
                request.mz, first.mz, request.ppm_tol
            ):
                break
            live += 1
        if live < len(channels):
            channels[live].append(search)
        else:
            channels.append([search])
    contexts: dict[XICRequest, tuple[PhysicalPeakSearch, ...]] = {}
    for channel_members in channels:
        clusters: list[list[PhysicalPeakSearch]] = []
        upper = -math.inf
        for search in sorted(
            channel_members, key=lambda s: (s.request.rt_min, s.request.rt_max)
        ):
            if not clusters or search.request.rt_min > upper:
                clusters.append([])
                upper = search.request.rt_max
            clusters[-1].append(search)
            upper = max(upper, search.request.rt_max)
        for cluster in clusters:
            lower = min(s.request.rt_min for s in cluster)
            upper = max(s.request.rt_max for s in cluster)
            by_mass: dict[tuple[float, float], list[PhysicalPeakSearch]] = {}
            for search in cluster:
                by_mass.setdefault(
                    (search.request.mz, search.request.ppm_tol), []
                ).append(search)
            for members in by_mass.values():
                request = replace(members[0].request, rt_min=lower, rt_max=upper)
                contexts[request] = tuple(members)
    return contexts


def _same_acquired_signal(left: AcquiredTrace, right: AcquiredTrace) -> bool:
    """Exact observed signal equality, not chemical identity or area similarity."""
    return (
        bool(np.any(left.trace.intensity > 0.0))
        and np.array_equal(left.trace.rt, right.trace.rt)
        and np.array_equal(left.trace.intensity, right.trace.intensity)
    )


def _expanded_trace_request(
    request: XICRequest,
    searches: Sequence[PhysicalPeakSearch],
    rt: np.ndarray,
    intensity: np.ndarray,
    components: tuple[RawPeakComponent, ...],
    profile: SignalProcessingProfile,
) -> XICRequest | None:
    """Plan one bounded read from acquired raw ownership, without preparation."""
    point_count = len(rt)
    if (
        point_count < 2
        or rt.ndim != 1
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
    if point_count >= profile.window_points:
        relevant = tuple(
            c
            for c in components
            if any(
                (request.rt_min if c.start_index == 0 else rt[c.start_index])
                <= s.query_rt_min
                <= (
                    request.rt_max
                    if c.end_index == point_count
                    else rt[c.end_index - 1]
                )
                for s in searches
            )
        )
        left = any(c.clips_left_edge for c in relevant)
        right = any(c.clips_right_edge for c in relevant)
        extent_left = any(c.left_return_index == 0 for c in relevant)
        extent_right = any(c.right_return_index == point_count for c in relevant)
        if not left and not right and not extent_left and not extent_right:
            return None
        span = max(
            PHYSICAL_CONTEXT_HALF_WINDOW_MIN,
            cadence_min * (profile.window_points // 2),
            3.0 * (request.rt_max - request.rt_min) if left or right else 0.0,
        )
        return replace(
            request,
            rt_min=max(0.0, request.rt_min - span)
            if left or extent_left
            else request.rt_min,
            rt_max=request.rt_max + span if right or extent_right else request.rt_max,
        )
    if not any(s.allow_sparse_reacquisition for s in searches):
        return None
    extra_point_count = profile.window_points - point_count + 2
    extra_span_min = cadence_min * extra_point_count
    expanded_rt_min = max(0.0, request.rt_min - cadence_min * (extra_point_count // 2))
    return replace(
        request,
        rt_min=expanded_rt_min,
        rt_max=request.rt_max + extra_span_min - (request.rt_min - expanded_rt_min),
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
