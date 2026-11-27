"""Acquire the batch-seeded MS1 population before cross-sample correspondence."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from statistics import median
from typing import Protocol

import numpy as np

from xic_extractor.acquisition.peak_apex_ion import (
    APEX_ION_SPREAD_PPM,
    MS1Centroid,
    read_peak_apex_ion,
)
from xic_extractor.acquisition.physical_capture import (
    PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS,
    same_acquired_signal,
)
from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    TraceAcquisitionCapability,
    acquire_trace_batch,
    mint_trace_acquisition_capability,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.ion_grouping import event_ion_mz
from xic_extractor.discovery.models import DiscoveryEvent
from xic_extractor.discovery.same_ion_peaks import group_same_ion_peaks
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.discovery.strict_event_receipts import resolve_discovery_scan_events
from xic_extractor.mass_accuracy import mz_tolerance_da, mz_window, signed_ppm_error
from xic_extractor.peak_detection.background_contrast import (
    CONTRAST_HALF_WINDOW_MIN,
    background_contrast,
)
from xic_extractor.peak_detection.cid_nl_candidate_set_relation import (
    DdaCidNlCandidateSetRelation,
)
from xic_extractor.peak_detection.cid_nl_evidence import CidNlEvidenceObservation
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    resolve_cid_nl_method_relations,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupport,
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
    mint_physical_peak_observations,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
)
from xic_extractor.peak_detection.physical_peak_population import (
    PhysicalPeakPopulation,
    measure_physical_peak_population,
)
from xic_extractor.peak_detection.traces import Trace, trace_from_xic_request
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.raw_superwindow_source import (
    RawSuperWindowSource,
    ScanResolvedXICSource,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.partition.cid_nl_discovery_evidence import (
    discovery_scan_evidence,
)
from xic_extractor.xic_models import XICRequest, XICTrace

EVENT_NEIGHBORHOOD_MIN = 3.0
"""A peak apex this far from every batch event of its region is not minted."""

CONTRAST_READ_REACH_MIN = CONTRAST_HALF_WINDOW_MIN + 0.5
"""Each ion is also read this far past its window, so a peak's background
neighbourhood never ends on a boundary scan."""


class RegionRawSource(ScanResolvedXICSource, Protocol):
    def acquisition_rt_bounds(self) -> tuple[float, float]: ...

    def ms1_centroid(
        self, rt: float, mz_min: float, mz_max: float
    ) -> tuple[float, float] | None: ...


@dataclass(frozen=True, slots=True)
class DiscoveryMassRegion:
    """A connected union of seed mass windows and the batch event RTs inside it.

    Not a chemical ion identity. Each sample reads one XIC per ion in ``ions``,
    and only peaks near some sample's event become observations. When
    ``only_samples`` is set, too few samples carry an event for the region to
    yield a published row, and only those samples read it.
    """

    centers: tuple[float, ...]
    ppm: float
    event_rts: tuple[float, ...]
    ions: tuple[XICRequest, ...]
    only_samples: frozenset[str] | None = None

    def near_event(self, rt: float) -> bool:
        index = bisect_left(self.event_rts, rt)
        return any(
            abs(rt - event_rt) <= EVENT_NEIGHBORHOOD_MIN
            for event_rt in self.event_rts[max(index - 1, 0) : index + 1]
        )

    @property
    def mz_bounds(self) -> tuple[float, float]:
        return mz_window(self.centers[0], self.ppm)[0], mz_window(
            self.centers[-1], self.ppm
        )[1]


@dataclass(frozen=True, slots=True)
class RegionTraceCoverage:
    """Native scans read for one query; a failed read did not search this request."""

    request: XICRequest
    scan_bounds: tuple[int, int] | None
    reason: str


@dataclass(frozen=True, slots=True)
class RegionSegment:
    acquired_trace: AcquiredTrace
    evaluation: PhysicalPeakEvaluation


@dataclass(frozen=True, slots=True)
class CidNlSampleRegion:
    """Canonical physical observations plus separate, local method relations."""

    sample: SampleMetadata
    region: DiscoveryMassRegion
    coverage: tuple[RegionTraceCoverage, ...]
    peaks: tuple[PeakObservation, ...]
    evidence: tuple[CidNlEvidenceObservation, ...]
    individual_relations: tuple[DdaCidNlPeakSupport, ...]
    ambiguous_relations: tuple[DdaCidNlCandidateSetRelation, ...]


def plan_cid_nl_mass_regions(
    events: Iterable[DiscoveryEvent],
    *,
    settings: DiscoverySettings,
    minimum_event_samples: int = 1,
    peak_events: Iterable[Sequence[DiscoveryEvent]] = (),
) -> tuple[DiscoveryMassRegion, ...]:
    """Discover the search universe from valid local method events across the batch.

    Overlap defines work co-location, not one wider XIC. Event masses within
    ``APEX_ION_SPREAD_PPM`` of the lowest one are readings of one ion; that ion
    gets one XIC at the median reading, over its event RTs widened by
    ``EVENT_NEIGHBORHOOD_MIN``. Two neighboring ions keep separate XICs.

    A region with events in fewer than ``minimum_event_samples`` samples is
    read only in those samples: the others' peaks there could not complete a
    row that direct evidence is too sparse to establish. A region stays read
    everywhere when one peak, given by its events in ``peak_events``, shows the
    loss in two or more scans: a repeat under one peak is a reproducible signal
    that sparse sampling alone would hide.
    """
    ppm = settings.precursor_mz_tolerance_ppm
    rts_by_mz: dict[float, set[float]] = {}
    samples_by_mz: dict[float, set[str]] = {}

    def qualifies(event: DiscoveryEvent) -> bool:
        return (
            event.neutral_loss_tag in settings.selected_tag_names
            and event.assess_precursor_loss(settings.nl_tolerance_ppm).supports_nl
        )

    repeated = {
        event_ion_mz(event)
        for located in peak_events
        if len(supporting := [event for event in located if qualifies(event)]) > 1
        for event in supporting
    }
    for event in events:
        if qualifies(event):
            mz = event_ion_mz(event)
            rts_by_mz.setdefault(mz, set()).add(float(event.rt))
            samples_by_mz.setdefault(mz, set()).add(event.sample_stem)
    regions: list[tuple[list[float], set[float], set[str]]] = []
    right = 0.0
    for mz in sorted(rts_by_mz):
        low, high = mz_window(mz, ppm)
        if not regions or low > right:
            regions.append(([], set(), set()))
        regions[-1][0].append(mz)
        regions[-1][1].update(rts_by_mz[mz])
        regions[-1][2].update(samples_by_mz[mz])
        right = high
    return tuple(
        DiscoveryMassRegion(
            tuple(centers),
            ppm,
            tuple(sorted(rts)),
            _ion_requests(centers, rts_by_mz, ppm),
            frozenset(samples)
            if len(samples) < minimum_event_samples and repeated.isdisjoint(centers)
            else None,
        )
        for centers, rts, samples in regions
    )


def _ion_requests(
    centers: list[float], rts_by_mz: dict[float, set[float]], ppm: float
) -> tuple[XICRequest, ...]:
    groups: list[list[float]] = []
    for mz in centers:
        if not groups or signed_ppm_error(mz, groups[-1][0]) > APEX_ION_SPREAD_PPM:
            groups.append([])
        groups[-1].append(mz)
    requests = []
    for group in groups:
        rts = [rt for mz in group for rt in rts_by_mz[mz]]
        requests.append(
            XICRequest(
                mz=float(median(mz for mz in group for _ in rts_by_mz[mz])),
                rt_min=min(rts) - EVENT_NEIGHBORHOOD_MIN,
                rt_max=max(rts) + EVENT_NEIGHBORHOOD_MIN,
                ppm_tol=ppm,
            )
        )
    return tuple(requests)


def acquire_cid_nl_sample_regions(
    raw: RegionRawSource,
    *,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    regions: tuple[DiscoveryMassRegion, ...],
    events: Iterable[DiscoveryEvent],
    settings: DiscoverySettings,
    profile: SignalProcessingProfile,
) -> tuple[CidNlSampleRegion, ...]:
    """Read every planned ion within this RAW's LC run, even without local CID.

    An ion whose event window lies outside this run's LC range is not read, nor
    is a region restricted to other samples. Each ion's request is followed by
    its background neighbourhood, so both come from one vendor read.
    """
    if not regions:
        return ()
    rt_start, rt_end = raw.acquisition_rt_bounds()
    per_region = tuple(
        tuple(
            replace(
                ion,
                rt_min=max(ion.rt_min, rt_start),
                rt_max=min(ion.rt_max, rt_end),
            )
            for ion in region.ions
            if ion.rt_max > rt_start
            and ion.rt_min < rt_end
            and (region.only_samples is None or sample.raw_stem in region.only_samples)
        )
        for region in regions
    )
    requests = tuple(request for chunk in per_region for request in chunk)
    region_events = _region_event_selector(events, sample=sample, settings=settings)
    capability = mint_trace_acquisition_capability(
        acquisition_source, adapter_namespace=__name__
    )
    paired = tuple(
        _acquire_regions(
            raw,
            capability,
            tuple(
                item
                for request in requests
                for item in (
                    request,
                    replace(
                        request,
                        rt_min=max(rt_start, request.rt_min - CONTRAST_READ_REACH_MIN),
                        rt_max=min(rt_end, request.rt_max + CONTRAST_READ_REACH_MIN),
                    ),
                )
            ),
        )
    )
    acquired = paired[0::2]
    contexts = {
        request: context.trace
        for (request, _, _), (_, context, _) in zip(acquired, paired[1::2], strict=True)
        if context is not None
    }
    captures = iter(acquired)
    results = []
    for region, region_requests in zip(regions, per_region, strict=True):
        results.append(
            _measure_region(
                sample=sample,
                region=region,
                acquired=tuple(next(captures) for _ in region_requests),
                ms1_centroid=raw.ms1_centroid,
                acquisition_source=acquisition_source,
                events=region_events(region),
                settings=settings,
                profile=profile,
                contexts=contexts,
            )
        )
    return tuple(results)


def relate_region_events(
    regions: tuple[CidNlSampleRegion, ...],
    *,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    events: Iterable[DiscoveryEvent],
    settings: DiscoverySettings,
) -> tuple[CidNlSampleRegion, ...]:
    """The same measured peaks related only to the given CID-NL events."""
    region_events = _region_event_selector(events, sample=sample, settings=settings)
    related = []
    for region in regions:
        evidence, individual, ambiguous = attach_cid_nl_events(
            sample=sample,
            acquisition_source=acquisition_source,
            peaks=region.peaks,
            events=region_events(region.region),
            settings=settings,
        )
        related.append(
            replace(
                region,
                evidence=evidence,
                individual_relations=individual,
                ambiguous_relations=ambiguous,
            )
        )
    return tuple(related)


def _region_event_selector(
    events: Iterable[DiscoveryEvent],
    *,
    sample: SampleMetadata,
    settings: DiscoverySettings,
) -> Callable[[DiscoveryMassRegion], tuple[DiscoveryEvent, ...]]:
    local = sorted(
        (
            (event_ion_mz(event), event)
            for event in events
            if event.sample_stem == sample.raw_stem
            and event.neutral_loss_tag in settings.selected_tag_names
        ),
        key=lambda item: item[0],
    )
    masses = tuple(mz for mz, _ in local)

    def select(region: DiscoveryMassRegion) -> tuple[DiscoveryEvent, ...]:
        low, high = region.mz_bounds
        return tuple(
            event
            for _, event in local[bisect_left(masses, low) : bisect_right(masses, high)]
        )

    return select


_Capture = tuple[XICRequest, AcquiredTrace | None, str]


def _acquire_regions(
    raw: RegionRawSource,
    capability: TraceAcquisitionCapability,
    requests: tuple[XICRequest, ...],
) -> Iterator[_Capture]:
    """Record a failed vendor chunk without zero traces or per-query retries."""
    for start in range(0, len(requests), PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS):
        chunk = requests[start : start + PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS]
        try:
            batch = acquire_trace_batch(capability, chunk, RawSuperWindowSource(raw))
        except InterruptedError:
            raise
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            yield from ((request, None, reason) for request in chunk)
        else:
            yield from ((item.coordinate.request, item, "") for item in batch.traces)


def _measure_region(
    *,
    sample: SampleMetadata,
    region: DiscoveryMassRegion,
    acquired: tuple[_Capture, ...],
    ms1_centroid: MS1Centroid,
    acquisition_source: AcquisitionSourceIdentity,
    events: tuple[DiscoveryEvent, ...],
    settings: DiscoverySettings,
    profile: SignalProcessingProfile,
    contexts: Mapping[XICRequest, XICTrace],
) -> CidNlSampleRegion:
    """Resolve signal aliases before attaching any CID-NL events.

    A compact peak may have fewer hits than the partial-overlap test needs.
    Identical selected scans of the same measured ion still observe one peak.
    Overlapping ion windows can also mint one physical peak twice, e.g. as one
    trace's segment and another trace's localized lobe that differ only where
    a centroid falls outside one window; the measurement that observes more
    scans is kept under the peak identity downstream assessment requires.
    """
    coverage = []
    measured: list[RegionSegment] = []
    distinct: list[tuple[AcquiredTrace, PhysicalPeakPopulation]] = []
    for request, trace, error in acquired:
        if trace is None:
            coverage.append(RegionTraceCoverage(request, None, error))
            continue
        rt, y = trace.trace.rt, trace.trace.intensity
        matching = next(
            (pop for owner, pop in distinct if same_acquired_signal(owner, trace)),
            None,
        )
        population = matching or measure_physical_peak_population(
            rt,
            y,
            context_rt_start_min=request.rt_min,
            context_rt_end_min=request.rt_max,
            profile=profile,
        )
        coordinate = trace.coordinate
        coverage.append(
            RegionTraceCoverage(
                request,
                (coordinate.first_scan, coordinate.last_scan),
                population.universe.reason,
            )
        )
        if matching is not None:
            continue
        distinct.append((trace, population))
        for segment in population.universe.segments:
            evaluation = population.measure_segment(segment)
            if evaluation.state == "available":
                measured.append(RegionSegment(trace, evaluation))

    traces: dict[XICRequest, Trace] = {}
    peaks: dict[str, PeakObservation] = {}
    for same_ion in group_same_ion_peaks(measured):
        kept = same_ion[0]
        trace = kept.acquired_trace
        request = trace.coordinate.request
        apex = kept.evaluation.apex_rt_min
        if apex is None or not region.near_event(apex):
            continue
        mz, _ = read_peak_apex_ion(trace, apex, ms1_centroid=ms1_centroid)
        if mz is None:
            continue
        if request not in traces:
            traces[request] = trace_from_xic_request(
                sample_name=sample.sample_name,
                request=request,
                xic_trace=trace.trace,
                source=__name__,
                acquisition_source_id=acquisition_source.acquisition_source_id,
            )
        (peak,) = mint_physical_peak_observations(
            sample=sample,
            trace=traces[request],
            evaluations=(kept.evaluation,),
            measured_mz=(mz,),
            signal_processing_profile=profile,
        )
        channel = contexts[request]
        peak = replace(
            peak,
            background_contrast=background_contrast(
                np.asarray(channel.rt, dtype=float),
                np.asarray(channel.intensity, dtype=float),
                left_rt_min=peak.rt_left_min,
                right_rt_min=peak.rt_right_min,
                profile=profile,
            ),
        )
        held = peaks.get(peak.physical_peak_key)
        if held is None or np.count_nonzero(peak.intensity) > np.count_nonzero(
            held.intensity
        ):
            peaks[peak.physical_peak_key] = peak
    observations = tuple(
        sorted(peaks.values(), key=lambda peak: peak.measured_order_key)
    )
    evidence, individual, ambiguous = attach_cid_nl_events(
        sample=sample,
        acquisition_source=acquisition_source,
        peaks=observations,
        events=events,
        settings=settings,
    )
    return CidNlSampleRegion(
        sample,
        region,
        tuple(coverage),
        observations,
        evidence,
        individual,
        ambiguous,
    )


def attach_cid_nl_events(
    *,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    peaks: tuple[PeakObservation, ...],
    events: tuple[DiscoveryEvent, ...],
    settings: DiscoverySettings,
) -> tuple[
    tuple[CidNlEvidenceObservation, ...],
    tuple[DdaCidNlPeakSupport, ...],
    tuple[DdaCidNlCandidateSetRelation, ...],
]:
    evidence = []
    individual: list[DdaCidNlPeakSupport] = []
    ambiguous: list[DdaCidNlCandidateSetRelation] = []
    for event in resolve_discovery_scan_events(
        events,
        settings=settings,
        preserve_configured_losses=len(settings.selected_tag_names) > 1,
    ):
        ref = discovery_scan_evidence(
            event,
            sample=sample,
            acquisition_source=acquisition_source,
            settings=settings,
        )
        evidence.append(ref)
        direct, competing = resolve_cid_nl_method_relations(
            sample=sample,
            evidence_refs=(ref,),
            candidate_peaks=peaks,
            policy=DdaCidNlPeakSupportPolicy(
                precursor_tolerance_da=mz_tolerance_da(
                    event_ion_mz(event), settings.precursor_mz_tolerance_ppm
                ),
                neutral_loss_tolerance_ppm=settings.nl_tolerance_ppm,
            ),
        )
        individual.extend(direct)
        ambiguous.extend(competing)
    return tuple(evidence), tuple(individual), tuple(ambiguous)
