"""CID-NL MS1 capture: measure each ion's peaks once and attach its NL events."""

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from xic_extractor.acquisition.peak_apex_ion import (
    APEX_ION_SPREAD_PPM,
    MS1Centroid,
    read_peak_apex_ion,
)
from xic_extractor.acquisition.physical_capture import (
    PHYSICAL_CONTEXT_HALF_WINDOW_MIN,
    CapturedPhysicalPeak,
    PhysicalPeakCapture,
    PhysicalPeakSearch,
)
from xic_extractor.acquisition_contract import (
    BatchXICAdapter,
    TraceAcquisitionBatch,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.event_evidence import (
    events_precursor_mz_basis,
    ordered_tag_names,
    select_representative_event,
)
from xic_extractor.discovery.models import (
    DiscoveryEvent,
    DiscoveryIon,
    DiscoveryPeak,
    EventAttachment,
    PeakStatus,
    build_discovery_peak_id,
    located_event_order,
)
from xic_extractor.discovery.same_ion_peaks import group_same_ion_peaks
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.mass_accuracy import (
    CARBON_ISOTOPE_SPACING_DA,
    mz_tolerance_da,
    within_ppm,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    nl_event_rt_attaches,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
    PhysicalSegment,
)
from xic_extractor.precursor_evidence import MS1MassWindowSource
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.xic_models import XICRequest

DISCOVERY_TRACE_ADAPTER_NAMESPACE = "xic_extractor.discovery.ms1_capture"
SPARSE_REACQUISITION_PRODUCT_INTENSITY_MIN = 100_000.0
_DOUBLY_CHARGED_ISOTOPE_SPACING_MZ = CARBON_ISOTOPE_SPACING_DA / 2
_DOUBLY_CHARGED_ISOTOPE_MIN_RATIO = 0.10

BlankBackgroundTest = Callable[[float, float], bool]
"""Whether a peak of this precursor m/z and MS1 height is blank background."""


class DiscoveryMs1Source(BatchXICAdapter, MS1MassWindowSource, Protocol):
    """RAW reader surface used by Discovery MS1 capture."""

    def batch_xic_source(self) -> BatchXICAdapter:
        """XIC adapter for physical capture; the reader chooses its read merging."""


@dataclass(frozen=True, slots=True)
class Ms1Capture:
    """One sample's peaks, every event's attachment and the XIC reads behind them.

    ``evaluations`` holds each peak's own measurement, in peak order. It stays
    in the Discovery process; Alignment measures the peak again from RAW.
    """

    peaks: tuple[DiscoveryPeak, ...]
    attachments: tuple[EventAttachment, ...]
    evaluations: tuple[PhysicalPeakEvaluation, ...]
    acquisition_batches: tuple[TraceAcquisitionBatch, ...]
    initial_request_count: int
    reacquisition_request_count: int
    remeasurement_request_count: int


@dataclass(frozen=True, slots=True)
class _IonPeak:
    """One ion peak: its kept measurement and every event locating it.

    ``xic_mz`` is the XIC center of the ion that first measured the peak and
    names it. ``apex_mz`` is the MS1 reading the kept window is centred on,
    reported only when every event's precursor is inferred.
    """

    measurement: CapturedPhysicalPeak
    xic_mz: float
    events: tuple[DiscoveryEvent, ...]
    apex_mz: float | None
    doubly_charged: bool


def capture_ms1_peaks(
    raw: DiscoveryMs1Source,
    ions: Iterable[DiscoveryIon],
    *,
    acquisition_source: AcquisitionSourceIdentity,
    settings: DiscoverySettings,
    signal_processing_profile: SignalProcessingProfile,
    ms1_centroid: MS1Centroid,
    is_blank: BlankBackgroundTest | None = None,
) -> Ms1Capture:
    """Measure each ion's physical peaks once, then attach every NL event."""

    searches = _event_searches(ions, settings=settings)
    physical_capture = PhysicalPeakCapture(
        raw.batch_xic_source(),
        acquisition_source=acquisition_source,
        adapter_namespace=DISCOVERY_TRACE_ADAPTER_NAMESPACE,
        profile=signal_processing_profile,
    )
    captures = physical_capture.capture(tuple(search for _, search in searches))

    attachments: list[EventAttachment] = []
    located: dict[int, list[DiscoveryEvent]] = {}
    first_measurement: dict[int, CapturedPhysicalPeak] = {}
    for (event, _search), captured in zip(searches, captures, strict=True):
        if captured.evaluation.answer.selected_segment is None:
            attachments.append(
                EventAttachment(
                    event=event,
                    unattached_reason=_no_peak_reason(captured.evaluation),
                )
            )
            continue
        located.setdefault(captured.work_ordinal, []).append(event)
        first_measurement.setdefault(captured.work_ordinal, captured)

    ion_peaks = _ion_peaks(
        physical_capture, first_measurement, located, ms1_centroid=ms1_centroid
    )
    peaks: list[DiscoveryPeak] = []
    for ion_peak in ion_peaks:
        peak, peak_attachments = _discovery_peak(
            ion_peak, settings=settings, is_blank=is_blank
        )
        peaks.append(peak)
        attachments.extend(peak_attachments)
    return Ms1Capture(
        peaks=tuple(peaks),
        attachments=tuple(attachments),
        evaluations=tuple(item.measurement.evaluation for item in ion_peaks),
        acquisition_batches=tuple(physical_capture.acquisition_batches),
        initial_request_count=physical_capture.initial_request_count,
        reacquisition_request_count=physical_capture.reacquisition_request_count,
        remeasurement_request_count=physical_capture.remeasurement_request_count,
    )


def _event_searches(
    ions: Iterable[DiscoveryIon], *, settings: DiscoverySettings
) -> list[tuple[DiscoveryEvent, PhysicalPeakSearch]]:
    """One physical search per NL event, on its ion's XIC around the event RT."""
    padding_min = max(settings.ms1_search_padding_min, PHYSICAL_CONTEXT_HALF_WINDOW_MIN)
    searches: list[tuple[DiscoveryEvent, PhysicalPeakSearch]] = []
    for ion in ions:
        sparse = _sparse_reacquisition_by_event(ion, padding_min=padding_min)
        for event in ion.events:
            searches.append(
                (
                    event,
                    PhysicalPeakSearch(
                        selection_subject_id=_seed_subject_id(event),
                        query_rt_min=event.rt,
                        request=XICRequest(
                            mz=ion.mz,
                            rt_min=max(0.0, event.rt - padding_min),
                            rt_max=event.rt + padding_min,
                            ppm_tol=settings.precursor_mz_tolerance_ppm,
                        ),
                        allow_sparse_reacquisition=sparse[event],
                    ),
                )
            )
    return searches


def _ion_peaks(
    physical_capture: PhysicalPeakCapture,
    first_measurement: dict[int, CapturedPhysicalPeak],
    located: dict[int, list[DiscoveryEvent]],
    *,
    ms1_centroid: MS1Centroid,
) -> list[_IonPeak]:
    """One measurement, one interpretation and one ion reading per ion peak.

    A measurement located only by inferred precursors is first read at its
    apex and measured once more in the window centred on that ion, with the
    same RT context; the reading is the ion's m/z only when that window
    measures the peak, selecting a peak with the same raw apex. Measurements of
    one ion peak are then grouped (see ``same_ion_peaks``); a recorded
    precursor already fixes the ion, so a group holding one keeps a
    measurement it located. The kept measurement is the peak, every event of
    the group is attached against it, and a group whose events are all inferred
    reports its kept window's reading and judges the doubly charged isotope
    once at that peak's apex.
    """
    readings = {
        ordinal: read_peak_apex_ion(
            captured.acquired_trace,
            captured.evaluation.apex_rt_min,
            ms1_centroid=ms1_centroid,
        )[0]
        for ordinal, captured in first_measurement.items()
        if _inferred_only(located[ordinal])
    }
    moved = {
        ordinal: mz
        for ordinal, mz in sorted(readings.items())
        if mz is not None and mz != first_measurement[ordinal].search.request.mz
    }
    remeasured = physical_capture.remeasure(
        tuple((first_measurement[ordinal], mz) for ordinal, mz in moved.items())
    )
    measurement = dict(first_measurement)
    window_reading = {
        ordinal: mz
        for ordinal, mz in readings.items()
        if mz is not None and mz == first_measurement[ordinal].search.request.mz
    }
    xic_mz: dict[int, float] = {}
    for ordinal, centred in zip(moved, remeasured, strict=True):
        first = first_measurement[ordinal]
        if (
            centred.evaluation.answer.selected_segment is not None
            and centred.evaluation.question.query_rt_min
            == first.evaluation.question.query_rt_min
        ):
            measurement[ordinal] = centred
            window_reading[centred.work_ordinal] = moved[ordinal]
            xic_mz[centred.work_ordinal] = min(
                xic_mz.get(centred.work_ordinal, math.inf), first.search.request.mz
            )

    distinct: dict[int, CapturedPhysicalPeak] = {}
    events_of: dict[int, list[DiscoveryEvent]] = {}
    for ordinal, captured in measurement.items():
        distinct.setdefault(captured.work_ordinal, captured)
        events_of.setdefault(captured.work_ordinal, []).extend(located[ordinal])
    recorded = frozenset(
        work_ordinal
        for work_ordinal, events in events_of.items()
        if not _inferred_only(events)
    )

    ion_peaks = []
    for group in group_same_ion_peaks(
        tuple(distinct.values()),
        recorded=lambda item: item.work_ordinal in recorded,
    ):
        kept = group[0]
        events = tuple(
            sorted(
                (event for member in group for event in events_of[member.work_ordinal]),
                key=_event_order_key,
            )
        )
        inferred = _inferred_only(events)
        ion_peaks.append(
            _IonPeak(
                measurement=kept,
                xic_mz=xic_mz.get(kept.work_ordinal, kept.search.request.mz),
                events=events,
                apex_mz=window_reading.get(kept.work_ordinal) if inferred else None,
                doubly_charged=inferred
                and _reads_doubly_charged(kept, ms1_centroid=ms1_centroid),
            )
        )
    return ion_peaks


def _inferred_only(events: Iterable[DiscoveryEvent]) -> bool:
    return events_precursor_mz_basis(list(events)) == "product_plus_neutral_loss"


def _event_order_key(event: DiscoveryEvent) -> tuple[int, float, float, str]:
    return (
        event.scan_number,
        event.precursor_mz,
        event.product_mz,
        event.neutral_loss_tag,
    )


def _discovery_peak(
    ion_peak: _IonPeak,
    *,
    settings: DiscoverySettings,
    is_blank: BlankBackgroundTest | None,
) -> tuple[DiscoveryPeak, tuple[EventAttachment, ...]]:
    """One peak with every located event's attachment, attached or not."""
    evaluation = ion_peak.measurement.evaluation
    selected = evaluation.answer.selected_segment
    if selected is None:
        raise ValueError("a located Discovery peak requires a selected segment")
    apex_mz = ion_peak.apex_mz
    peak_mz = ion_peak.measurement.search.request.mz if apex_mz is None else apex_mz
    neighbors = tuple(
        segment
        for segment in evaluation.segment_universe.segments
        if segment != selected
    )
    reasons = {
        event: _unattached_reason(
            event,
            peak_mz=peak_mz,
            peak=selected,
            neighbors=neighbors,
            settings=settings,
        )
        for event in ion_peak.events
    }
    attached = [event for event in ion_peak.events if not reasons[event]]
    representative = select_representative_event(
        attached or ion_peak.events, settings=settings
    )
    peak_id = build_discovery_peak_id(
        sample_stem=representative.sample_stem,
        xic_mz=ion_peak.xic_mz,
        peak_rt_min=evaluation.question.query_rt_min,
    )
    peak = DiscoveryPeak(
        peak_id=peak_id,
        sample_stem=representative.sample_stem,
        peak_mz=peak_mz,
        neutral_loss_tag=representative.neutral_loss_tag,
        best_ms2_scan_id=representative.scan_number,
        attached_event_count=len(attached),
        ms1_apex_rt=selected.apex_rt_min,
        status=_peak_status(
            doubly_charged=ion_peak.doubly_charged,
            blank=is_blank is not None
            and is_blank(representative.precursor_mz, float(selected.height)),
            attached_event_count=len(attached),
        ),
        ms1_apex_mz=apex_mz,
        matched_tag_names=ordered_tag_names(
            (event.neutral_loss_tag for event in ion_peak.events), settings=settings
        ),
        located_events=located_event_order(ion_peak.events),
    )
    return peak, tuple(
        EventAttachment(event=event, peak_id=peak_id, unattached_reason=reasons[event])
        for event in ion_peak.events
    )


def _peak_status(
    *, doubly_charged: bool, blank: bool, attached_event_count: int
) -> PeakStatus:
    """The first problem that holds the peak, then NL attachment.

    Overlap review does not hold a peak: its value is kept and the cell carries
    the review.
    """
    if doubly_charged:
        return "doubly_charged"
    if blank:
        return "blank"
    if attached_event_count < 1:
        return "no_nl_event"
    return "admitted"


def _unattached_reason(
    event: DiscoveryEvent,
    *,
    peak_mz: float,
    peak: PhysicalSegment,
    neighbors: Sequence[PhysicalSegment],
    settings: DiscoverySettings,
) -> str:
    assessment = event.assess_precursor_loss(settings.nl_tolerance_ppm)
    if not assessment.supports_nl:
        return assessment.status
    if assessment.reference_mz is None or not within_ppm(
        assessment.reference_mz, peak_mz, settings.precursor_mz_tolerance_ppm
    ):
        return "mass_outside_peak"
    parent = event.ms1_precursor_observation
    support_rt = event.rt if parent is None else parent.rt_min
    if not all(
        _event_rt_attaches_to_peak(rt, peak, neighbors) for rt in (support_rt, event.rt)
    ):
        return "rt_outside_peak"
    return ""


def _no_peak_reason(evaluation: PhysicalPeakEvaluation) -> str:
    reason = evaluation.reason.strip()
    return "no_peak_at_rt" if reason in {"", "no_selection"} else reason


def _event_rt_attaches_to_peak(
    rt: float, peak: PhysicalSegment, neighbors: Sequence[PhysicalSegment]
) -> bool:
    return nl_event_rt_attaches(
        rt,
        peak.left_rt_min,
        peak.right_rt_min,
        ((segment.left_rt_min, segment.right_rt_min) for segment in neighbors),
    )


def _seed_subject_id(event: DiscoveryEvent) -> str:
    """One physical search per NL event."""
    return (
        "discovery-physical-work:"
        f"{event.sample_stem}:{event.scan_number}:"
        f"{float(event.precursor_mz).hex()}:{float(event.product_mz).hex()}"
    )


def _sparse_reacquisition_by_event(
    ion: DiscoveryIon,
    *,
    padding_min: float,
) -> dict[DiscoveryEvent, bool]:
    """Whether sparse reacquisition may widen each event's trace context.

    The judgement covers the ion's events whose search windows chain into
    one RT region. Events with a recorded precursor allow it; an all-inferred
    region needs two events and a high-intensity product.
    """
    allowed: dict[DiscoveryEvent, bool] = {}
    region: list[DiscoveryEvent] = []
    upper = -float("inf")
    for event in (*sorted(ion.events, key=lambda event: event.rt), None):
        if event is not None and (not region or event.rt - padding_min <= upper):
            region.append(event)
            upper = max(upper, event.rt + padding_min)
            continue
        if region:
            if _inferred_only(region):
                value = (
                    len(region) >= 2
                    and max(item.product_intensity for item in region)
                    >= SPARSE_REACQUISITION_PRODUCT_INTENSITY_MIN
                )
            else:
                value = True
            allowed.update((item, value) for item in region)
        if event is not None:
            region = [event]
            upper = event.rt + padding_min
    return allowed


def _reads_doubly_charged(
    captured: CapturedPhysicalPeak, *, ms1_centroid: MS1Centroid
) -> bool:
    """Whether the ion read at the peak's apex carries a doubly charged isotope.

    MS1 is the method's accurate-mass scan, while ddMS2 runs at a quarter of
    its resolution, so product + loss only points at the ion. True when the
    isotope one doubly charged 13C spacing above the ion is read strongly
    enough in most of the scans where the ion is read.
    """
    apex_mz, ion_reads = read_peak_apex_ion(
        captured.acquired_trace,
        captured.evaluation.apex_rt_min,
        ms1_centroid=ms1_centroid,
    )
    if apex_mz is None:
        return False
    isotope_mz = apex_mz + _DOUBLY_CHARGED_ISOTOPE_SPACING_MZ
    half_width = mz_tolerance_da(isotope_mz, APEX_ION_SPREAD_PPM)
    isotope_reads = 0
    for rt, ion_intensity in ion_reads:
        isotope = ms1_centroid(rt, isotope_mz - half_width, isotope_mz + half_width)
        if (
            isotope is not None
            and isotope[1] >= _DOUBLY_CHARGED_ISOTOPE_MIN_RATIO * ion_intensity
        ):
            isotope_reads += 1
    return 2 * isotope_reads > len(ion_reads)
