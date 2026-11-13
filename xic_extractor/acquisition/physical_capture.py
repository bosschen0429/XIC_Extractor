"""Locate physical peaks in search windows, then measure each centred on itself.

The method supplies an opaque selection subject, search coordinates and a query
RT. Search windows only locate the peak a query falls in. Each located peak is
read again in the window centred on its raw apex and measured there, so other
queries on the same mass channel cannot change its measurement. This adapter
owns batching and signal reuse; the pure physical peak owner still owns
preparation, selection and integration.
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
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
    PhysicalSegment,
)
from xic_extractor.peak_detection.physical_peak_population import (
    PhysicalPeakPopulation,
    measure_physical_peak_population,
)
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.xic_models import XICRequest

# Smallest half-width of a search window around a query.
PHYSICAL_CONTEXT_HALF_WINDOW_MIN = 0.60
PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS = 256


@dataclass(frozen=True, slots=True)
class PhysicalPeakSearch:
    """A method-selected physical question, not an MS2 or chemistry record."""

    selection_subject_id: str
    query_rt_min: float
    request: XICRequest


@dataclass(frozen=True, slots=True, eq=False)
class CapturedPhysicalPeak:
    work_ordinal: int
    search: PhysicalPeakSearch
    acquired_trace: AcquiredTrace
    evaluation: PhysicalPeakEvaluation
    trace_quality: str


class PhysicalPeakCapture:
    """Sample-local acquisition and physical results shared by method hits.

    One instance answers one ``capture`` population; ``remeasure`` extends it.
    """

    def __init__(
        self,
        raw: BatchXICAdapter,
        *,
        acquisition_source: AcquisitionSourceIdentity,
        adapter_namespace: str,
        profile: SignalProcessingProfile,
        context_half_width_peak_widths: float,
    ) -> None:
        if not (
            math.isfinite(context_half_width_peak_widths)
            and context_half_width_peak_widths > 0.0
        ):
            raise ValueError("context half-width must be positive peak widths")
        self._raw = raw
        self._capability = mint_trace_acquisition_capability(
            acquisition_source,
            adapter_namespace=adapter_namespace,
            coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
        )
        self._profile = profile
        self._half_width_peak_widths = context_half_width_peak_widths
        self._populations: dict[
            XICRequest, tuple[AcquiredTrace, PhysicalPeakPopulation]
        ] = {}
        self._measurements: dict[
            tuple[XICRequest, PhysicalSegment], tuple[int, PhysicalPeakEvaluation]
        ] = {}
        self._batches: list[TraceAcquisitionBatch] = []
        self._trace_qualities: dict[int, str] = {}
        self._next_work_ordinal = 0
        # Search windows read to locate peaks, then peak-centred windows.
        self.initial_request_count = 0
        self.reacquisition_request_count = 0
        self.remeasurement_request_count = 0

    @property
    def acquisition_batches(self) -> tuple[TraceAcquisitionBatch, ...]:
        return tuple(self._batches)

    def capture(
        self, searches: Sequence[PhysicalPeakSearch]
    ) -> tuple[CapturedPhysicalPeak, ...]:
        """Locate each query's peak, then measure every peak centred on itself.

        Overlapping search windows of one mass channel are read and prepared
        together, only to find the segment holding each query, its raw apex and
        boundary width. Each located peak is read once more, from its raw apex
        out to the configured number of peak widths on each side, and measured
        there once; queries locating the same apex share that measurement. The
        peak measured is the segment of that window holding the located apex,
        chosen once even when its own apex lies elsewhere. A query no segment
        holds keeps its search window's no-selection answer.
        """
        unique = tuple(dict.fromkeys(searches))
        contexts = _shared_contexts(unique)
        self.initial_request_count = len(contexts)
        searched: dict[PhysicalPeakSearch, XICRequest] = {}
        located: dict[PhysicalPeakSearch, tuple[float, float, float]] = {}
        centred: dict[tuple[float, float, float], tuple[float, float]] = {}
        for (request, members), acquired in zip(
            contexts.items(), self._acquire(tuple(contexts)), strict=True
        ):
            population = self._prepare(request, acquired)
            self._populations.setdefault(request, (acquired, population))
            for member in members:
                searched[member] = request
                segment = population.segment_at(member.query_rt_min)
                if segment is None:
                    continue
                apex_rt, rt_min, rt_max = population.peak_centred_context(
                    segment, half_width_peak_widths=self._half_width_peak_widths
                )
                peak = (member.request.mz, member.request.ppm_tol, apex_rt)
                located[member] = peak
                # Search windows that locate one apex apart keep its widest
                # context, independent of the order they are read in.
                known = centred.get(peak, (rt_min, rt_max))
                centred[peak] = (min(known[0], rt_min), max(known[1], rt_max))
        requests = {
            peak: XICRequest(mz=peak[0], rt_min=rt_min, rt_max=rt_max, ppm_tol=peak[1])
            for peak, (rt_min, rt_max) in centred.items()
        }
        windows = tuple(dict.fromkeys(requests.values()))
        traces = dict(zip(windows, self._acquire(windows), strict=True))
        self.reacquisition_request_count = len(windows)
        # Different mass windows are never substituted during acquisition.
        # Only equality over the complete acquired range can establish that two
        # compatible windows observed the very same signal.
        signal_owners: dict[XICRequest, XICRequest] = {}
        owner_members: dict[XICRequest, list[XICRequest]] = {}
        for request in sorted(
            windows, key=lambda r: (r.rt_min, r.rt_max, r.mz, r.ppm_tol)
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
        # A population, not a question, owns the canonical preparation and each
        # segment's single measurement. NL count cannot change this work.
        for request in owner_members:
            self._populations[request] = (
                traces[request],
                self._prepare(request, traces[request]),
            )
        return tuple(
            self._lookup(
                replace(search, request=signal_owners[requests[located[search]]]),
                select_rt_min=located[search][2],
            )
            if search in located
            else self._lookup(
                replace(search, request=searched[search]),
                select_rt_min=search.query_rt_min,
            )
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
            self._populations[request] = acquired, self._prepare(request, acquired)
        return tuple(
            self._lookup(search, select_rt_min=search.query_rt_min)
            for search in searches
        )

    def _prepare(
        self, request: XICRequest, acquired: AcquiredTrace
    ) -> PhysicalPeakPopulation:
        return measure_physical_peak_population(
            acquired.trace.rt,
            acquired.trace.intensity,
            context_rt_start_min=request.rt_min,
            context_rt_end_min=request.rt_max,
            profile=self._profile,
        )

    def _lookup(
        self, search: PhysicalPeakSearch, *, select_rt_min: float
    ) -> CapturedPhysicalPeak:
        acquired, population = self._populations[search.request]
        segment = population.segment_at(select_rt_min)
        if segment is not None:
            key = (search.request, segment)
            if key not in self._measurements:
                self._measurements[key] = (
                    self._next_work_ordinal,
                    population.measure_segment(segment),
                )
                self._next_work_ordinal += 1
            ordinal, evaluation = self._measurements[key]
        else:
            # Unassigned queries retain an explicit no-selection answer. There
            # is no measurement to repeat and no prepared-signal evaluation.
            evaluation = population.unselected(select_rt_min)
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
