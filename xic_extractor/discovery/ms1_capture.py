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
    events_neutral_loss_error_basis,
    events_precursor_mz_basis,
    max_scan_precursor_abs_delta_da,
    merge_event_evidence,
    ordered_tag_names,
    scan_precursor_delta_da,
    select_representative_event,
    tag_intersection_status,
)
from xic_extractor.discovery.models import (
    DiscoveryEvent,
    DiscoveryIon,
    DiscoveryPeak,
    DiscoveryPhysicalWorkItem,
    EventAttachment,
    PeakStatus,
    build_discovery_physical_selection_subject_id,
)
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryPeakPhysicalBinding,
    DiscoveryPhysicalFactRecord,
)
from xic_extractor.discovery.priority import (
    assign_review_priority,
    build_peak_reason,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.mass_accuracy import weighted_ion_mz, within_ppm
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
    PhysicalSegment,
)
from xic_extractor.precursor_evidence import MS1MassWindowSource
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.xic_models import XICRequest

MS1CentroidMz = Callable[[float, float, float], float | None]
"""(apex RT, m/z min, m/z max) -> most intense MS1 centroid m/z at that scan."""

_DISCOVERY_MS1_PREDICTION_MAX_REQUESTS = 256
# The apex scan and one MS1 scan on each side read the ion's m/z; readings
# spread wider than a few ppm come from more than one ion.
_APEX_ION_SCAN_RADIUS = 1
_APEX_ION_SPREAD_PPM = 5.0
# 13C spacing of a doubly charged ion. Product + loss assumes a singly charged
# precursor, so an inferred ion read beside this isotope is not one.
_DOUBLY_CHARGED_ISOTOPE_SPACING_MZ = 1.0033548 / 2
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

    def __post_init__(self) -> None:
        count_values = (
            self.input_ion_count,
            self.input_event_count,
            self.exact_scan_window_count,
            self.predicted_chunked_call_count,
            self.initial_request_count,
            self.reacquisition_request_count,
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
        if self.reacquisition_request_count > self.initial_request_count:
            raise ValueError(
                "Discovery MS1 reacquisition exceeds its initial population"
            )
        if logical_request_count != (
            self.initial_request_count + self.reacquisition_request_count
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
    physical_work_manifest: tuple[DiscoveryPhysicalWorkItem, ...]
    physical_evaluations: tuple[DiscoveryPhysicalFactRecord, ...]
    physical_bindings: tuple[DiscoveryPeakPhysicalBinding, ...]


def capture_ms1_peaks(
    raw: DiscoveryMs1Source,
    ions: Iterable[DiscoveryIon],
    *,
    acquisition_source: AcquisitionSourceIdentity,
    settings: DiscoverySettings,
    measurement_observer: Callable[[_DiscoveryMs1Measurements], None] | None = None,
    signal_processing_profile: SignalProcessingProfile,
    ms1_centroid_mz: MS1CentroidMz,
) -> _DiscoveryMs1AcquisitionResult:
    """Measure each ion's physical peaks once, then attach every NL event."""

    ion_tuple = tuple(ions)
    padding_min = max(settings.ms1_search_padding_min, PHYSICAL_CONTEXT_HALF_WINDOW_MIN)
    questions: list[tuple[DiscoveryIon, DiscoveryEvent, PhysicalPeakSearch]] = []
    for ion in ion_tuple:
        sparse = _sparse_reacquisition_by_event(
            ion, padding_min=padding_min, settings=settings
        )
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
                        allow_sparse_reacquisition=sparse[event],
                    ),
                )
            )
    physical_capture = PhysicalPeakCapture(
        raw.batch_xic_source(),
        acquisition_source=acquisition_source,
        # Keep persisted acquisition coordinates compatible with current sidecars.
        adapter_namespace="xic_extractor.discovery.ms1_capture",
        profile=signal_processing_profile,
    )
    # Every event may expose an incomplete edge, so context is resolved for the
    # whole population before any event is attached.
    captures = physical_capture.capture(tuple(search for _, _, search in questions))
    measurements = _measurements(
        physical_capture,
        input_ion_count=len(ion_tuple),
        input_event_count=len({search for _, _, search in questions}),
    )
    if measurement_observer is not None:
        measurement_observer(measurements)

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

    measured = _one_measurement_per_peak(
        capture_by_work,
        located,
        tolerance_ppm=settings.precursor_mz_tolerance_ppm,
    )
    retained_ordinals = {
        ordinal: index for index, ordinal in enumerate(sorted(measured))
    }
    peaks: list[DiscoveryPeak] = []
    records: list[DiscoveryPhysicalFactRecord] = []
    for ordinal in sorted(measured):
        peak, record, peak_attachments = _measured_peak(
            capture_by_work[ordinal],
            measured[ordinal],
            work_ordinal=retained_ordinals[ordinal],
            settings=settings,
            ms1_centroid_mz=ms1_centroid_mz,
        )
        peaks.append(peak)
        records.append(record)
        attachments.extend(peak_attachments)
    peak_ids = [peak.peak_id for peak in peaks]
    if len(peak_ids) != len(set(peak_ids)):
        raise ValueError("Discovery measured one physical peak twice")
    bindings = tuple(
        DiscoveryPeakPhysicalBinding(
            acquisition_source=acquisition_source,
            final_ordinal=index,
            peak=peak,
            physical_fact_record=record,
        )
        for index, (peak, record) in enumerate(zip(peaks, records, strict=True))
    )
    return _DiscoveryMs1AcquisitionResult(
        peaks=tuple(peaks),
        attachments=tuple(attachments),
        measurements=measurements,
        physical_work_manifest=tuple(record.work_item for record in records),
        physical_evaluations=tuple(records),
        physical_bindings=bindings,
    )


def _one_measurement_per_peak(
    capture_by_work: dict[int, CapturedPhysicalPeak],
    located: dict[int, list[tuple[DiscoveryIon, DiscoveryEvent]]],
    *,
    tolerance_ppm: float,
) -> dict[int, list[tuple[DiscoveryIon, DiscoveryEvent]]]:
    """Keep one measurement of a peak that adjacent ions both acquired.

    Neighbouring ion XICs overlap, so one ion can be measured in both.
    Measurements are grouped as ions are: from the lowest XIC mass, a group
    takes each later measurement within the precursor tolerance of that anchor
    whose peak shares the anchor's raw apex scan, so no measurement bridges two
    groups. The kept measurement is the one locating more attachments, then the
    larger area, then the lower mass; every event of the group is attached
    against it.
    """
    ordered = sorted(
        capture_by_work,
        key=lambda ordinal: (
            capture_by_work[ordinal].search.request.mz,
            capture_by_work[ordinal].evaluation.question.query_rt_min,
            ordinal,
        ),
    )
    members: dict[int, list[int]] = {}
    assigned: set[int] = set()
    for index, anchor in enumerate(ordered):
        if anchor in assigned:
            continue
        anchor_capture = capture_by_work[anchor]
        anchor_mz = anchor_capture.search.request.mz
        anchor_rt = anchor_capture.evaluation.question.query_rt_min
        group = [anchor]
        for other in ordered[index + 1 :]:
            other_capture = capture_by_work[other]
            if not within_ppm(
                other_capture.search.request.mz, anchor_mz, tolerance_ppm
            ):
                break
            if (
                other not in assigned
                and other_capture.evaluation.question.query_rt_min == anchor_rt
            ):
                group.append(other)
        assigned.update(group)
        members[anchor] = group

    def rank(ordinal: int) -> tuple[int, float, float]:
        facts = capture_by_work[ordinal].evaluation.facts
        area = 0.0 if facts is None else facts.area_counts_seconds
        return (
            -len(located[ordinal]),
            -area,
            capture_by_work[ordinal].search.request.mz,
        )

    return {
        min(group, key=rank): [item for ordinal in group for item in located[ordinal]]
        for group in members.values()
    }


def _measured_peak(
    captured: CapturedPhysicalPeak,
    located: Sequence[tuple[DiscoveryIon, DiscoveryEvent]],
    *,
    work_ordinal: int,
    settings: DiscoverySettings,
    ms1_centroid_mz: MS1CentroidMz,
) -> tuple[DiscoveryPeak, DiscoveryPhysicalFactRecord, tuple[EventAttachment, ...]]:
    """One measured peak, its attached attachments, and why the rest do not attach."""
    evaluation = captured.evaluation
    facts = evaluation.facts
    selected = evaluation.answer.selected_segment
    if facts is None or selected is None:
        raise ValueError("a located Discovery peak requires measured facts")
    request = captured.search.request
    located_events = [event for _, event in located]
    inferred = events_precursor_mz_basis(located_events) == "product_plus_neutral_loss"
    apex_mz, doubly_charged = (
        _apex_ion_reading(
            request,
            captured.acquired_trace,
            evaluation,
            ms1_centroid_mz=ms1_centroid_mz,
        )
        if inferred
        else (None, False)
    )
    physical_mz = request.mz if apex_mz is None else apex_mz
    neighbors = tuple(
        segment
        for segment in evaluation.segment_universe.segments
        if segment != selected
    )
    reasons = {
        event: _unattached_reason(
            event,
            physical_mz=physical_mz,
            peak=selected,
            neighbors=neighbors,
            settings=settings,
        )
        for event in located_events
    }
    attached = [event for event in located_events if not reasons[event]]
    # Every event locating this peak is its MS2 evidence, which Alignment
    # rereads and judges; only attached attachments admit it.
    evidence = located_events
    representative = select_representative_event(
        attached or located_events, settings=settings
    )
    record = _cid_nl_physical_record(
        representative, captured, work_ordinal=work_ordinal
    )
    peak_id = record.work_item.peak_id
    trace_quality = captured.trace_quality
    event_delta_min = facts.apex_rt_min - representative.rt
    priority = assign_review_priority(
        attached_event_count=len(attached),
        ms1_event_delta_min=event_delta_min,
        settings=settings,
    )
    reason = build_peak_reason(
        attached_event_count=len(attached),
        ms1_event_delta_min=event_delta_min,
        settings=settings,
    )
    physical_overlap = trace_quality == "physical_shoulder_overlap"
    if physical_overlap:
        priority = "MEDIUM"
        reason += "; physical_shoulder_overlap"
    if doubly_charged:
        reason = (
            "inferred precursor reads a doubly charged isotope at the peak apex; "
            "retained for review"
        )
    matched_tags = ordered_tag_names(
        (event.neutral_loss_tag for event in evidence), settings=settings
    )
    peak = DiscoveryPeak(
        peak_id=peak_id,
        raw_file=representative.raw_file,
        sample_stem=representative.sample_stem,
        xic_mz=request.mz,
        best_event_precursor_mz=representative.precursor_mz,
        best_event_product_mz=representative.product_mz,
        best_event_neutral_loss_da=representative.observed_neutral_loss_da,
        neutral_loss_tag=representative.neutral_loss_tag,
        configured_neutral_loss_da=representative.configured_neutral_loss_da,
        neutral_loss_mass_error_ppm=representative.observed_loss_error_ppm,
        best_ms2_scan_id=representative.scan_number,
        best_event_rt=representative.rt,
        event_scan_ids=tuple(sorted({event.scan_number for event in evidence})),
        attached_event_count=len(attached),
        event_rt_min=min(event.rt for event in evidence),
        event_rt_max=max(event.rt for event in evidence),
        ms2_product_max_intensity=max(event.product_intensity for event in evidence),
        ms1_search_rt_min=request.rt_min,
        ms1_search_rt_max=request.rt_max,
        ms1_apex_rt=facts.apex_rt_min,
        ms1_area=facts.area_counts_seconds,
        ms1_height=facts.height,
        ms1_peak_rt_start=facts.left_rt_min,
        ms1_peak_rt_end=facts.right_rt_min,
        ms1_event_delta_min=event_delta_min,
        ms1_trace_quality=trace_quality,
        review_priority=priority,
        reason=reason,
        status=_peak_status(
            doubly_charged=doubly_charged,
            overlap=evaluation.requires_overlap_review,
            attached_event_count=len(attached),
        ),
        ms1_scan_support_score=min(
            1.0,
            float(facts.scan_support_count)
            / float(settings.evidence_profile.thresholds.scan_support_target),
        ),
        ms1_apex_mz=apex_mz,
        neutral_loss_error_basis=events_neutral_loss_error_basis(evidence),
        precursor_mz_basis=events_precursor_mz_basis(evidence),
        scan_precursor_mz=representative.scan_precursor_mz,
        scan_precursor_delta_da=scan_precursor_delta_da(representative),
        max_scan_precursor_abs_delta_da=max_scan_precursor_abs_delta_da(evidence),
        selected_tag_count=len(settings.selected_tag_names),
        matched_tag_count=len(matched_tags),
        matched_tag_names=matched_tags,
        primary_tag_name=representative.neutral_loss_tag,
        tag_combine_mode=settings.tag_combine_mode,
        tag_intersection_status=tag_intersection_status(
            matched_tags, settings=settings
        ),
        tag_evidence_json=merge_event_evidence(evidence, settings=settings),
        # Provisional trace shape is judged with every event that locates it.
        is_provisional=(
            physical_overlap
            or (len(located_events) < 2 and trace_quality == "provisional")
        ),
    )
    peak_attachments = tuple(
        EventAttachment(
            event=event,
            xic_mz=ion.mz,
            precursor_verified=_precursor_verified(event, settings),
            peak_id="" if reasons[event] else peak_id,
            unattached_reason=reasons[event],
        )
        for ion, event in located
    )
    return peak, record, peak_attachments


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
    physical_mz: float,
    peak: PhysicalSegment,
    neighbors: Sequence[PhysicalSegment],
    settings: DiscoverySettings,
) -> str:
    assessment = event.assess_precursor_loss(settings.nl_tolerance_ppm)
    if not assessment.supports_nl:
        return assessment.status
    if assessment.reference_mz is None or not within_ppm(
        assessment.reference_mz, physical_mz, settings.precursor_mz_tolerance_ppm
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
    return build_discovery_physical_selection_subject_id(
        sample_stem=event.sample_stem,
        best_ms2_scan_id=event.scan_number,
        precursor_mz=event.precursor_mz,
        product_mz=event.product_mz,
    )


def _sparse_reacquisition_by_event(
    ion: DiscoveryIon,
    *,
    padding_min: float,
    settings: DiscoverySettings,
) -> dict[DiscoveryEvent, bool]:
    """Whether sparse reacquisition may widen each event's trace context.

    The judgement covers the ion's events whose search windows chain into
    one RT region. Events with a recorded precursor allow it; an all-inferred
    region needs two attachments and a high-intensity product.
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
            if events_precursor_mz_basis(region) == "product_plus_neutral_loss":
                value = (
                    len(region) >= 2
                    and max(item.product_intensity for item in region)
                    >= settings.evidence_profile.thresholds.product_intensity_high_min
                )
            else:
                value = True
            allowed.update((item, value) for item in region)
        if event is not None:
            region = [event]
            upper = event.rt + padding_min
    return allowed


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
    )


def _apex_ion_reading(
    request: XICRequest,
    acquired: AcquiredTrace,
    evaluation: PhysicalPeakEvaluation,
    *,
    ms1_centroid_mz: MS1CentroidMz,
) -> tuple[float | None, bool]:
    """The MS1 ion read at the resolved peak of an inferred precursor.

    MS1 is the method's accurate-mass scan, while ddMS2 runs at a quarter of
    its resolution, so product + loss only points at the ion. The ion is read
    inside the request at the apex scans; readings spread wider than a few ppm
    come from two ions and report none. A selected precursor is not read,
    since a reading near it can belong to a neighbouring ion or isotope. The
    m/z is reported only: the precursor, its trace and its MS2 evidence keep
    their own values. The flag is true when the ion's doubly charged isotope is
    read in most of the scans where the ion is read.
    """
    facts = evaluation.facts
    if evaluation.state != "available" or facts is None:
        return None, False
    rt = acquired.trace.rt
    intensity = acquired.trace.intensity
    if len(rt) == 0:
        return None, False
    apex = int(np.argmin(np.abs(rt - facts.apex_rt_min)))
    half_width = request.mz * request.ppm_tol * 1e-6
    readings = []
    read_rts = []
    for index in range(
        max(0, apex - _APEX_ION_SCAN_RADIUS),
        min(len(rt), apex + _APEX_ION_SCAN_RADIUS + 1),
    ):
        mz = ms1_centroid_mz(
            float(rt[index]), request.mz - half_width, request.mz + half_width
        )
        if mz is not None:
            readings.append((mz, float(intensity[index])))
            read_rts.append(float(rt[index]))
    measured = weighted_ion_mz(readings, max_spread_ppm=_APEX_ION_SPREAD_PPM)
    if measured is None:
        return None, False
    return measured, _reads_doubly_charged_isotope(
        measured, read_rts, ms1_centroid_mz=ms1_centroid_mz
    )


def _reads_doubly_charged_isotope(
    apex_mz: float,
    read_rts: Sequence[float],
    *,
    ms1_centroid_mz: MS1CentroidMz,
) -> bool:
    isotope_mz = apex_mz + _DOUBLY_CHARGED_ISOTOPE_SPACING_MZ
    half_width = isotope_mz * _APEX_ION_SPREAD_PPM * 1e-6
    isotope_reads = sum(
        ms1_centroid_mz(rt, isotope_mz - half_width, isotope_mz + half_width)
        is not None
        for rt in read_rts
    )
    return 2 * isotope_reads > len(read_rts)


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


def _cid_nl_physical_record(
    representative: DiscoveryEvent,
    captured: CapturedPhysicalPeak,
    *,
    work_ordinal: int,
) -> DiscoveryPhysicalFactRecord:
    """Project completed physical work into the retained CID-NL sidecar schema."""
    acquired = captured.acquired_trace
    coordinate = acquired.coordinate
    request = coordinate.request
    question = captured.evaluation.question
    return DiscoveryPhysicalFactRecord(
        work_item=DiscoveryPhysicalWorkItem(
            work_ordinal=work_ordinal,
            acquisition_source_id=coordinate.capability.acquisition_source.acquisition_source_id,
            sample_stem=representative.sample_stem,
            best_ms2_scan_id=representative.scan_number,
            precursor_mz=representative.precursor_mz,
            product_mz=representative.product_mz,
            query_rt_min=question.query_rt_min,
            context_rt_start_min=question.context_rt_start_min,
            context_rt_end_min=question.context_rt_end_min,
            xic_mz=request.mz,
            xic_rt_min=request.rt_min,
            xic_rt_max=request.rt_max,
            xic_ppm_tol=request.ppm_tol,
        ),
        acquired_trace=acquired,
        evaluation=captured.evaluation,
    )
