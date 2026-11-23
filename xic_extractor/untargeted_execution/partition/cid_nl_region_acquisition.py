"""Acquire the batch-seeded MS1 population before cross-sample correspondence."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from typing import Protocol

from xic_extractor.acquisition.peak_apex_ion import MS1Centroid, read_peak_apex_ion
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
from xic_extractor.mass_accuracy import mz_tolerance_da, mz_window
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
from xic_extractor.peak_detection.elution_context import (
    SampleElutionContext,
    project_elution_context,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
    mint_localized_peak_observation,
    mint_physical_peak_observations,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    localize_overlap_peak_lobes,
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
from xic_extractor.xic_models import XICRequest


class RegionRawSource(ScanResolvedXICSource, Protocol):
    def acquisition_rt_bounds(self) -> tuple[float, float]: ...

    def ms1_centroid(
        self, rt: float, mz_min: float, mz_max: float
    ) -> tuple[float, float] | None: ...


@dataclass(frozen=True, slots=True)
class DiscoveryMassRegion:
    """A connected union of seed mass windows, not a chemical ion identity."""

    centers: tuple[float, ...]
    ppm: float

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
    contexts: tuple[SampleElutionContext, ...]
    evidence: tuple[CidNlEvidenceObservation, ...]
    individual_relations: tuple[DdaCidNlPeakSupport, ...]
    ambiguous_relations: tuple[DdaCidNlCandidateSetRelation, ...]


def plan_cid_nl_mass_regions(
    events: Iterable[DiscoveryEvent], *, settings: DiscoverySettings
) -> tuple[DiscoveryMassRegion, ...]:
    """Discover the search universe from valid local method events across the batch.

    Overlap defines work co-location, not one wider XIC. Retain each distinct
    request so two neighboring ions cannot be fused by merely sharing a region.
    """
    ppm = settings.precursor_mz_tolerance_ppm
    centers = sorted(
        {
            event_ion_mz(event)
            for event in events
            if event.neutral_loss_tag in settings.selected_tag_names
            and event.assess_precursor_loss(settings.nl_tolerance_ppm).supports_nl
        }
    )
    regions: list[list[float]] = []
    right = 0.0
    for mz in centers:
        low, high = mz_window(mz, ppm)
        if not regions or low > right:
            regions.append([])
        regions[-1].append(mz)
        right = high
    return tuple(DiscoveryMassRegion(tuple(region), ppm) for region in regions)


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
    """Search every planned region over this RAW's actual LC run, even without CID."""
    if not regions:
        return ()
    rt_start, rt_end = raw.acquisition_rt_bounds()
    requests = tuple(
        XICRequest(mz=mz, rt_min=rt_start, rt_max=rt_end, ppm_tol=region.ppm)
        for region in regions
        for mz in region.centers
    )
    region_events = _region_event_selector(events, sample=sample, settings=settings)
    capability = mint_trace_acquisition_capability(
        acquisition_source, adapter_namespace=__name__
    )
    captures = _acquire_regions(raw, capability, requests)
    results = []
    for region in regions:
        results.append(
            _measure_region(
                sample=sample,
                region=region,
                acquired=tuple(next(captures) for _ in region.centers),
                ms1_centroid=raw.ms1_centroid,
                acquisition_source=acquisition_source,
                events=region_events(region),
                settings=settings,
                profile=profile,
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
) -> CidNlSampleRegion:
    """Resolve signal aliases before attaching any CID-NL events.

    A compact peak may have fewer hits than the partial-overlap test needs.
    Identical selected scans of the same measured ion still observe one peak.
    """
    coverage = []
    measured: list[RegionSegment] = []
    distinct: list[tuple[AcquiredTrace, PhysicalPeakPopulation]] = []
    contexts = []
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
        if context := project_elution_context(
            population.prepared,
            sample_stem=sample.raw_stem,
            mz=request.mz,
            universe=population.universe,
        ):
            contexts.append(context)
        for segment in population.universe.segments:
            evaluation = population.measure_segment(segment)
            if evaluation.state == "available":
                measured.append(RegionSegment(trace, evaluation))

    traces: dict[XICRequest, Trace] = {}
    peaks: dict[tuple[str, tuple[float, ...], tuple[float, ...]], PeakObservation] = {}
    for same_ion in group_same_ion_peaks(measured):
        kept = same_ion[0]
        trace = kept.acquired_trace
        request = trace.coordinate.request
        lobes = localize_overlap_peak_lobes(kept.evaluation)
        sites = (
            tuple((float(trace.trace.rt[lobe.apex_index]), lobe) for lobe in lobes)
            if lobes
            else ((kept.evaluation.apex_rt_min, None),)
        )
        for apex, lobe in sites:
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
            if lobe is not None:
                peak = mint_localized_peak_observation(
                    sample=sample,
                    trace=traces[request],
                    component=lobe,
                    measured_mz=mz,
                    signal_processing_profile=profile,
                    resolver_mode=settings.resolver_mode,
                )
            else:
                (peak,) = mint_physical_peak_observations(
                    sample=sample,
                    trace=traces[request],
                    evaluations=(kept.evaluation,),
                    measured_mz=(mz,),
                    signal_processing_profile=profile,
                    resolver_mode=settings.resolver_mode,
                )
            peaks.setdefault((peak.physical_peak_key, peak.rt, peak.intensity), peak)
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
        tuple(contexts),
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
