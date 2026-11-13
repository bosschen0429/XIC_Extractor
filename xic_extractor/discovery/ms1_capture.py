import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from xic_extractor.acquisition.physical_capture import (
    PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS,
    PHYSICAL_CONTEXT_HALF_WINDOW_MIN,
    CapturedPhysicalPeak,
    PhysicalPeakCapture,
    PhysicalPeakSearch,
)
from xic_extractor.acquisition_contract import (
    AcquiredTrace,
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
from xic_extractor.discovery.evidence_score import score_discovery_evidence
from xic_extractor.discovery.models import (
    DiscoveryEvent,
    DiscoveryIon,
    DiscoveryPeak,
    EventAttachment,
    PeakStatus,
    build_discovery_peak_id,
    located_event_order,
)
from xic_extractor.discovery.peak_binding import DiscoveryPeakPhysicalBinding
from xic_extractor.discovery.same_ion_peaks import group_same_ion_peaks
from xic_extractor.discovery.settings import (
    MEASUREMENT_CONTEXT_HALF_WIDTH_PEAK_WIDTHS,
    DiscoverySettings,
)
from xic_extractor.mass_accuracy import mz_tolerance_da, weighted_ion_mz, within_ppm
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
    PhysicalSegment,
)
from xic_extractor.precursor_evidence import MS1MassWindowSource
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.xic_models import XICRequest

MS1Centroid = Callable[[float, float, float], tuple[float, float] | None]
"""(apex RT, m/z min, m/z max) -> (m/z, intensity) of the most intense MS1
centroid at that scan."""

DISCOVERY_TRACE_ADAPTER_NAMESPACE = "xic_extractor.discovery.ms1_capture"
_DISCOVERY_MS1_PREDICTION_MAX_REQUESTS = 256
# The apex scan and one MS1 scan on each side read the ion's m/z; readings
# spread wider than a few ppm come from more than one ion.
_APEX_ION_SCAN_RADIUS = 1
_APEX_ION_SPREAD_PPM = 5.0
# 13C spacing of a doubly charged ion. Product + loss assumes a singly charged
# precursor, so an inferred ion read beside this isotope is not one.
_DOUBLY_CHARGED_ISOTOPE_SPACING_MZ = 1.0033548 / 2
# The isotope counts only at this fraction of the ion's centroid in the same
# scan. Tissue peaks read as doubly charged carry it at 38-88% of the ion; a
# singly charged ion with a noise centroid at +0.5 carried it at 2%.
_DOUBLY_CHARGED_ISOTOPE_MIN_RATIO = 0.10
# An NL event supports an MS1 peak only when its MS2 scan, and the parent MS1
# scan of an inferred precursor, fall within the peak bounds plus this margin.
_NL_PEAK_ATTACHMENT_TOLERANCE_MIN = 0.1


class DiscoveryMs1Source(BatchXICAdapter, MS1MassWindowSource, Protocol):
    """RAW reader surface used by Discovery MS1 capture."""

    def batch_xic_source(self) -> BatchXICAdapter:
        """XIC adapter for physical capture; the reader chooses its read merging."""


@dataclass(frozen=True, slots=True)
class _DiscoveryMs1Measurements:
    input_ion_count: int
    input_event_count: int
    exact_scan_window_count: int
    predicted_chunked_call_count: int
    batch_max_requests: int
    acquisition_batches: tuple[TraceAcquisitionBatch, ...]
    initial_request_count: int = 0
    reacquisition_request_count: int = 0
    remeasurement_request_count: int = 0

    def __post_init__(self) -> None:
        count_values = (
            self.input_ion_count,
            self.input_event_count,
            self.exact_scan_window_count,
            self.predicted_chunked_call_count,
            self.initial_request_count,
            self.reacquisition_request_count,
            self.remeasurement_request_count,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (*count_values, self.batch_max_requests)
        ):
            raise ValueError("Discovery MS1 measurements must be integral")
        if any(value < 0 for value in count_values) or self.batch_max_requests < 1:
            raise ValueError("Discovery MS1 measurements must be non-negative")
        if any(
            not isinstance(batch, TraceAcquisitionBatch)
            for batch in self.acquisition_batches
        ):
            raise ValueError("Discovery MS1 acquisition batches must be owner-minted")
        logical_request_count = sum(
            batch.logical_xic_request_count for batch in self.acquisition_batches
        )
        if self.initial_request_count > self.input_event_count:
            raise ValueError("Discovery MS1 acquisition request population is invalid")
        if logical_request_count != (
            self.initial_request_count
            + self.reacquisition_request_count
            + self.remeasurement_request_count
        ):
            raise ValueError("Discovery MS1 acquisition receipts are incomplete")
        if self.exact_scan_window_count > logical_request_count:
            raise ValueError("exact scan-window count exceeds logical requests")
        if self.predicted_chunked_call_count > logical_request_count:
            raise ValueError("predicted call count exceeds logical requests")


@dataclass(frozen=True, slots=True)
class _DiscoveryMs1AcquisitionResult:
    peaks: tuple[DiscoveryPeak, ...]
    attachments: tuple[EventAttachment, ...]
    measurements: _DiscoveryMs1Measurements
    physical_bindings: tuple[DiscoveryPeakPhysicalBinding, ...]


def capture_ms1_peaks(
    raw: DiscoveryMs1Source,
    ions: Iterable[DiscoveryIon],
    *,
    acquisition_source: AcquisitionSourceIdentity,
    settings: DiscoverySettings,
    measurement_observer: Callable[[_DiscoveryMs1Measurements], None] | None = None,
    signal_processing_profile: SignalProcessingProfile,
    ms1_centroid: MS1Centroid,
) -> _DiscoveryMs1AcquisitionResult:
    """Measure each ion's physical peaks once, then attach every NL event."""

    ion_tuple = tuple(ions)
    padding_min = max(settings.ms1_search_padding_min, PHYSICAL_CONTEXT_HALF_WINDOW_MIN)
    questions: list[tuple[DiscoveryIon, DiscoveryEvent, PhysicalPeakSearch]] = []
    for ion in ion_tuple:
        for event in ion.events:
            questions.append(
                (
                    ion,
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
                    ),
                )
            )
    physical_capture = PhysicalPeakCapture(
        raw.batch_xic_source(),
        acquisition_source=acquisition_source,
        adapter_namespace=DISCOVERY_TRACE_ADAPTER_NAMESPACE,
        profile=signal_processing_profile,
        context_half_width_peak_widths=MEASUREMENT_CONTEXT_HALF_WIDTH_PEAK_WIDTHS,
    )
    # Events only locate peaks; each peak is measured centred on its own apex
    # before any event is attached against it.
    captures = physical_capture.capture(tuple(search for _, _, search in questions))

    attachments: list[EventAttachment] = []
    located: dict[int, list[tuple[DiscoveryIon, DiscoveryEvent]]] = {}
    capture_by_work: dict[int, CapturedPhysicalPeak] = {}
    for (ion, event, _search), captured in zip(questions, captures, strict=True):
        if captured.evaluation.facts is None:
            attachments.append(
                EventAttachment(
                    event=event,
                    xic_mz=ion.mz,
                    precursor_verified=_precursor_verified(event, settings),
                    unattached_reason=_no_peak_reason(captured.evaluation),
                )
            )
            continue
        located.setdefault(captured.work_ordinal, []).append((ion, event))
        capture_by_work.setdefault(captured.work_ordinal, captured)

    measured = _one_measurement_per_ion_peak(
        physical_capture,
        capture_by_work,
        located,
        ms1_centroid=ms1_centroid,
    )
    measurements = _measurements(
        physical_capture,
        input_ion_count=len(ion_tuple),
        input_event_count=len({search for _, _, search in questions}),
    )
    if measurement_observer is not None:
        measurement_observer(measurements)
    bindings: list[DiscoveryPeakPhysicalBinding] = []
    for index, (captured, ion_mz, peak_located, ion_reading) in enumerate(measured):
        peak, representative, peak_attachments = _measured_peak(
            captured,
            peak_located,
            settings=settings,
            ion_mz=ion_mz,
            ion_reading=ion_reading,
        )
        bindings.append(
            DiscoveryPeakPhysicalBinding(
                final_ordinal=index,
                peak=peak,
                physical_evaluation=captured.evaluation,
                representative_event=representative,
            )
        )
        attachments.extend(peak_attachments)
    peak_ids = [binding.peak.peak_id for binding in bindings]
    if len(peak_ids) != len(set(peak_ids)):
        raise ValueError("Discovery measured one physical peak twice")
    return _DiscoveryMs1AcquisitionResult(
        peaks=tuple(binding.peak for binding in bindings),
        attachments=tuple(attachments),
        measurements=measurements,
        physical_bindings=tuple(bindings),
    )


def _one_measurement_per_ion_peak(
    physical_capture: PhysicalPeakCapture,
    capture_by_work: dict[int, CapturedPhysicalPeak],
    located: dict[int, list[tuple[DiscoveryIon, DiscoveryEvent]]],
    *,
    ms1_centroid: MS1Centroid,
) -> list[
    tuple[
        CapturedPhysicalPeak,
        float,
        list[tuple[DiscoveryIon, DiscoveryEvent]],
        tuple[float | None, bool],
    ]
]:
    """One measurement, one interpretation and one ion reading per ion peak.

    A measurement located only by inferred precursors is first read at its
    apex and measured once more in the window centred on that ion, with the
    same RT context; the reading is the ion's m/z only when that window
    measures the peak, selecting a peak with the same raw apex. Measurements of
    one ion peak are then grouped (see ``same_ion_peaks``); a recorded
    precursor already fixes the ion, so a group holding one keeps a
    measurement it located. The kept measurement is the
    peak, every event of the group is attached against it, and a group whose
    events are all inferred reports its kept window's reading and judges the
    doubly charged isotope once at that peak's apex. A peak keeps the XIC
    center of the ion that first measured it, which names the peak.
    """
    centred = dict(capture_by_work)
    readings = {
        ordinal: _apex_ion_mz(
            captured.search.request,
            captured.acquired_trace,
            captured.evaluation,
            ms1_centroid=ms1_centroid,
        )[0]
        for ordinal, captured in capture_by_work.items()
        if _inferred_only(event for _, event in located[ordinal])
    }
    moved = {
        ordinal: mz
        for ordinal, mz in sorted(readings.items())
        if mz is not None and mz != capture_by_work[ordinal].search.request.mz
    }
    remeasured = physical_capture.remeasure(
        tuple((capture_by_work[ordinal], mz) for ordinal, mz in moved.items())
    )
    ion_mz: dict[int, float] = {}
    centred_mz = {
        capture_by_work[ordinal].work_ordinal: mz
        for ordinal, mz in readings.items()
        if mz is not None and mz == capture_by_work[ordinal].search.request.mz
    }
    for ordinal, captured in zip(moved, remeasured, strict=True):
        # The new window measures the peak only when it selects a peak with the
        # same raw apex. Extra signal in that window can merge or split peaks so
        # the segment at the apex is another peak; that window, like one that
        # selects nothing there, keeps the first measurement and its reading.
        if (
            captured.evaluation.facts is not None
            and captured.evaluation.question.query_rt_min
            == capture_by_work[ordinal].evaluation.question.query_rt_min
        ):
            centred[ordinal] = captured
            centred_mz[captured.work_ordinal] = moved[ordinal]
            ion_mz[captured.work_ordinal] = min(
                ion_mz.get(captured.work_ordinal, math.inf),
                capture_by_work[ordinal].search.request.mz,
            )
    by_measurement: dict[int, CapturedPhysicalPeak] = {}
    located_by_measurement: dict[int, list[tuple[DiscoveryIon, DiscoveryEvent]]] = {}
    for ordinal, captured in centred.items():
        by_measurement.setdefault(captured.work_ordinal, captured)
        located_by_measurement.setdefault(captured.work_ordinal, []).extend(
            located[ordinal]
        )
    recorded = frozenset(
        work_ordinal
        for work_ordinal, items in located_by_measurement.items()
        if not _inferred_only(event for _, event in items)
    )

    measured = []
    for group in group_same_ion_peaks(
        tuple(by_measurement.values()), recorded_ordinals=recorded
    ):
        kept = group[0]
        group_located = sorted(
            (
                item
                for member in group
                for item in located_by_measurement[member.work_ordinal]
            ),
            key=lambda item: _event_order_key(item[1]),
        )
        ion_reading: tuple[float | None, bool] = (None, False)
        if _inferred_only(event for _, event in group_located):
            _, doubly_charged = _apex_ion_reading(
                kept.search.request,
                kept.acquired_trace,
                kept.evaluation,
                ms1_centroid=ms1_centroid,
            )
            ion_reading = (centred_mz.get(kept.work_ordinal), doubly_charged)
        measured.append(
            (
                kept,
                ion_mz.get(kept.work_ordinal, kept.search.request.mz),
                group_located,
                ion_reading,
            )
        )
    return measured


def _inferred_only(events: Iterable[DiscoveryEvent]) -> bool:
    return events_precursor_mz_basis(list(events)) == "product_plus_neutral_loss"


def _event_order_key(event: DiscoveryEvent) -> tuple[int, float, float, str]:
    return (
        event.scan_number,
        event.precursor_mz,
        event.product_mz,
        event.neutral_loss_tag,
    )


def _measured_peak(
    captured: CapturedPhysicalPeak,
    located: Sequence[tuple[DiscoveryIon, DiscoveryEvent]],
    *,
    settings: DiscoverySettings,
    ion_mz: float,
    ion_reading: tuple[float | None, bool],
) -> tuple[DiscoveryPeak, DiscoveryEvent, tuple[EventAttachment, ...]]:
    """One measured peak, its attached attachments, and why the rest do not attach."""
    evaluation = captured.evaluation
    facts = evaluation.facts
    selected = evaluation.answer.selected_segment
    if facts is None or selected is None:
        raise ValueError("a located Discovery peak requires measured facts")
    request = captured.search.request
    located_events = [event for _, event in located]
    apex_mz, doubly_charged = ion_reading
    peak_mz = request.mz if apex_mz is None else apex_mz
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
        for event in located_events
    }
    attached = [event for event in located_events if not reasons[event]]
    # Every event locating this peak is its MS2 evidence, handed to Alignment
    # through the event table; only attached events admit it.
    evidence = located_events
    representative = select_representative_event(
        attached or located_events, settings=settings
    )
    question = evaluation.question
    window = captured.acquired_trace.coordinate.request
    # A peak keeps the XIC center of the ion that first measured it as its name.
    peak_id = build_discovery_peak_id(
        sample_stem=representative.sample_stem,
        xic_mz=ion_mz,
        peak_rt_min=question.query_rt_min,
    )
    matched_tags = ordered_tag_names(
        (event.neutral_loss_tag for event in evidence), settings=settings
    )
    peak = DiscoveryPeak(
        peak_id=peak_id,
        sample_stem=representative.sample_stem,
        peak_mz=peak_mz,
        neutral_loss_tag=representative.neutral_loss_tag,
        best_ms2_scan_id=representative.scan_number,
        attached_event_count=len(attached),
        ms1_apex_rt=facts.apex_rt_min,
        ms1_search_rt_min=window.rt_min,
        ms1_search_rt_max=window.rt_max,
        query_rt_min=question.query_rt_min,
        status=_peak_status(
            doubly_charged=doubly_charged,
            overlap=evaluation.requires_overlap_review,
            attached_event_count=len(attached),
        ),
        ms1_apex_mz=apex_mz,
        evidence_score=score_discovery_evidence(
            attached_event_count=len(attached),
            ms1_event_delta_min=facts.apex_rt_min - representative.rt,
            ms2_product_max_intensity=max(
                event.product_intensity for event in evidence
            ),
            ms1_area=facts.area_counts_seconds,
            ms1_scan_support_score=min(
                1.0,
                float(facts.scan_support_count)
                / float(settings.evidence_profile.thresholds.scan_support_target),
            ),
            settings=settings,
        ),
        precursor_mz_basis=events_precursor_mz_basis(evidence),
        matched_tag_names=matched_tags,
        located_events=located_event_order(evidence),
    )
    peak_attachments = tuple(
        EventAttachment(
            event=event,
            xic_mz=ion.mz,
            precursor_verified=_precursor_verified(event, settings),
            peak_id=peak_id,
            unattached_reason=reasons[event],
        )
        for ion, event in located
    )
    return peak, representative, peak_attachments


def _peak_status(
    *, doubly_charged: bool, overlap: bool, attached_event_count: int
) -> PeakStatus:
    """The first problem that holds the peak, then NL attachment."""
    if doubly_charged:
        return "doubly_charged"
    if overlap:
        return "overlap"
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


def _precursor_verified(event: DiscoveryEvent, settings: DiscoverySettings) -> bool:
    return event.assess_precursor_loss(settings.nl_tolerance_ppm).supports_nl


def _no_peak_reason(evaluation: PhysicalPeakEvaluation) -> str:
    reason = evaluation.reason.strip()
    return "no_peak_at_rt" if reason in {"", "no_selection"} else reason


def _event_rt_attaches_to_peak(
    rt: float, peak: PhysicalSegment, neighbors: Sequence[PhysicalSegment]
) -> bool:
    if peak.left_rt_min <= rt <= peak.right_rt_min:
        return True
    # DDA often samples a peak's foot or tail just past its 10%-height return.
    # That allowance never lends another measured peak's event to this one.
    return peak.left_rt_min - _NL_PEAK_ATTACHMENT_TOLERANCE_MIN <= rt <= (
        peak.right_rt_min + _NL_PEAK_ATTACHMENT_TOLERANCE_MIN
    ) and not any(
        segment.left_rt_min <= rt <= segment.right_rt_min for segment in neighbors
    )


def _seed_subject_id(event: DiscoveryEvent) -> str:
    """One physical search per NL event."""
    return (
        "discovery-physical-work:"
        f"{event.sample_stem}:{event.scan_number}:"
        f"{float(event.precursor_mz).hex()}:{float(event.product_mz).hex()}"
    )


def _measurements(
    physical_capture: PhysicalPeakCapture,
    *,
    input_ion_count: int,
    input_event_count: int,
) -> _DiscoveryMs1Measurements:
    batches = physical_capture.acquisition_batches
    scan_windows_by_batch = tuple(
        tuple(
            _native_scan_window(
                acquired.coordinate.first_scan,
                acquired.coordinate.last_scan,
            )
            for acquired in batch.traces
        )
        for batch in batches
    )
    return _DiscoveryMs1Measurements(
        input_ion_count=input_ion_count,
        input_event_count=input_event_count,
        exact_scan_window_count=len(
            {window for windows in scan_windows_by_batch for window in windows}
        ),
        predicted_chunked_call_count=sum(
            _predicted_chunked_call_count(windows) for windows in scan_windows_by_batch
        ),
        batch_max_requests=PHYSICAL_CAPTURE_BATCH_MAX_REQUESTS,
        acquisition_batches=tuple(batches),
        initial_request_count=physical_capture.initial_request_count,
        reacquisition_request_count=physical_capture.reacquisition_request_count,
        remeasurement_request_count=physical_capture.remeasurement_request_count,
    )


def _apex_ion_reading(
    request: XICRequest,
    acquired: AcquiredTrace,
    evaluation: PhysicalPeakEvaluation,
    *,
    ms1_centroid: MS1Centroid,
) -> tuple[float | None, bool]:
    """The MS1 ion read at the resolved peak of an inferred precursor.

    MS1 is the method's accurate-mass scan, while ddMS2 runs at a quarter of
    its resolution, so product + loss only points at the ion. A selected
    precursor is not read, since a reading near it can belong to a
    neighbouring ion or isotope. The precursor and its MS2 evidence keep their
    own values. The flag is true when the ion's doubly charged isotope is read
    strongly enough in most of the scans where the ion is read.
    """
    measured, ion_reads = _apex_ion_mz(
        request, acquired, evaluation, ms1_centroid=ms1_centroid
    )
    if measured is None:
        return None, False
    return measured, _reads_doubly_charged_isotope(
        measured, ion_reads, ms1_centroid=ms1_centroid
    )


def _apex_ion_mz(
    request: XICRequest,
    acquired: AcquiredTrace,
    evaluation: PhysicalPeakEvaluation,
    *,
    ms1_centroid: MS1Centroid,
) -> tuple[float | None, list[tuple[float, float]]]:
    """The ion read inside the request at the apex scans, and its readings.

    Each reading is the scan RT and the ion centroid's intensity there.

    Readings spread wider than a few ppm come from two ions and report none.
    """
    facts = evaluation.facts
    if evaluation.state != "available" or facts is None:
        return None, []
    rt = acquired.trace.rt
    intensity = acquired.trace.intensity
    if len(rt) == 0:
        return None, []
    apex = int(np.argmin(np.abs(rt - facts.apex_rt_min)))
    half_width = mz_tolerance_da(request.mz, request.ppm_tol)
    readings = []
    ion_reads = []
    for index in range(
        max(0, apex - _APEX_ION_SCAN_RADIUS),
        min(len(rt), apex + _APEX_ION_SCAN_RADIUS + 1),
    ):
        centroid = ms1_centroid(
            float(rt[index]), request.mz - half_width, request.mz + half_width
        )
        if centroid is not None:
            mz, centroid_intensity = centroid
            readings.append((mz, float(intensity[index])))
            ion_reads.append((float(rt[index]), centroid_intensity))
    return weighted_ion_mz(readings, max_spread_ppm=_APEX_ION_SPREAD_PPM), ion_reads


def _reads_doubly_charged_isotope(
    apex_mz: float,
    ion_reads: Sequence[tuple[float, float]],
    *,
    ms1_centroid: MS1Centroid,
) -> bool:
    isotope_mz = apex_mz + _DOUBLY_CHARGED_ISOTOPE_SPACING_MZ
    half_width = mz_tolerance_da(isotope_mz, _APEX_ION_SPREAD_PPM)
    isotope_reads = 0
    for rt, ion_intensity in ion_reads:
        isotope = ms1_centroid(rt, isotope_mz - half_width, isotope_mz + half_width)
        if (
            isotope is not None
            and isotope[1] >= _DOUBLY_CHARGED_ISOTOPE_MIN_RATIO * ion_intensity
        ):
            isotope_reads += 1
    return 2 * isotope_reads > len(ion_reads)


def _native_scan_window(
    first_scan: int | None,
    last_scan: int | None,
) -> tuple[int, int]:
    if first_scan is None or last_scan is None:
        raise ValueError("Discovery MS1 request lacks a native scan window")
    if first_scan < 0 or last_scan < first_scan:
        raise ValueError("Discovery MS1 request has an invalid native scan window")
    return first_scan, last_scan


def _predicted_chunked_call_count(
    scan_windows: Sequence[tuple[int, int]],
) -> int:
    return sum(
        len(set(chunk))
        for chunk in _chunks(scan_windows, _DISCOVERY_MS1_PREDICTION_MAX_REQUESTS)
    )


def _chunks(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
