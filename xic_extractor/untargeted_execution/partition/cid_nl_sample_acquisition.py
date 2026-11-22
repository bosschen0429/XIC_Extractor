"""Reacquire Discovery sample observations for Untargeted CID-NL assessment."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field

from xic_extractor.acquisition_contract import (
    NATIVE_SCAN_COORDINATE_KIND,
    AcquiredTrace,
    TraceAcquisitionBatch,
    mint_trace_acquisition_capability,
)
from xic_extractor.alignment.cid_nl_inputs import (
    CID_NL_ALIGNMENT_CONFIG,
    UntargetedCidNlAssessmentInput,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.models import (
    DiscoveryEvent,
    DiscoveryPeak,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.discovery.strict_event_receipts import (
    resolve_discovery_scan_events,
)
from xic_extractor.mass_accuracy import mz_tolerance_da
from xic_extractor.peak_detection.cid_nl_candidate_set_relation import (
    DdaCidNlCandidateSetRelation,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceObservation,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodAssessmentContractError,
    CidNlTechnicalState,
    mint_cid_nl_method_assessment,
    resolve_cid_nl_method_relations,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
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
    RetainedCaptureEvidence,
    mint_physical_peak_observations,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation
from xic_extractor.peak_detection.physical_peak_population import (
    PhysicalPeakPopulation,
    measure_physical_peak_population,
)
from xic_extractor.peak_detection.traces import trace_from_xic_request
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.acquisition_cache import (
    acquire_untargeted_trace_batch,
)
from xic_extractor.untargeted_execution.partition.cid_nl_discovery_evidence import (
    discovery_scan_evidence,
)
from xic_extractor.untargeted_execution.partition.cid_nl_raw_source import (
    _ADAPTER_NAMESPACE,
    CidNlAcquisitionError,
    CidNlRawSource,
    _freeze_acquisition,
    _within_ppm,
)
from xic_extractor.xic_models import XICRequest

UNTARGETED_CID_NL_CAPTURE_TRACE_SOURCE = (
    f"{_ADAPTER_NAMESPACE}:{NATIVE_SCAN_COORDINATE_KIND}"
)
_WRITER_ROUNDING_REL_TOL = 5e-6
_WRITER_ROUNDING_ABS_TOL = 5e-6


@dataclass(frozen=True, slots=True)
class CidNlAcquisitionResult:
    assessment_input: UntargetedCidNlAssessmentInput
    acquisition: TraceAcquisitionBatch | None
    candidate_observations: tuple[tuple[str, PeakObservation], ...]
    source_candidate_ids: tuple[str, ...]
    strict_scan_count: int
    capture_evidence: tuple[RetainedCaptureEvidence, ...] = dataclass_field(
        default=(), compare=False
    )
    elution_contexts: tuple[SampleElutionContext, ...] = ()


@dataclass(frozen=True, slots=True)
class UntargetedCidNlSampleTraceBatch:
    """One sample-local XIC batch keyed by exact Discovery peak identity."""

    sample_key: str
    acquisition_source_id: str
    candidate_ids: tuple[str, ...]
    acquisition: TraceAcquisitionBatch
    candidate_trace_indices: tuple[int, ...] | None = None
    context_trace_indices: tuple[int, ...] = ()
    elution_contexts: tuple[SampleElutionContext, ...] = ()
    candidate_evaluations: tuple[PhysicalPeakEvaluation, ...] = ()

    def __post_init__(self) -> None:
        if self.candidate_trace_indices is None:
            object.__setattr__(
                self,
                "candidate_trace_indices",
                tuple(range(len(self.candidate_ids))),
            )

    def acquired_trace(self, candidate_id: str) -> AcquiredTrace:
        try:
            index = self.candidate_ids.index(candidate_id)
        except ValueError as exc:
            raise CidNlAcquisitionError(
                f"sample trace batch has no peak {candidate_id!r}"
            ) from exc
        assert self.candidate_trace_indices is not None
        return self.acquisition.traces[self.candidate_trace_indices[index]]

    def evaluation(self, candidate_id: str) -> PhysicalPeakEvaluation:
        return self.candidate_evaluations[self.candidate_ids.index(candidate_id)]


def _sample_capture_requests(
    *,
    peaks: tuple[DiscoveryPeak, ...],
    settings: DiscoverySettings,
) -> tuple[tuple[XICRequest, ...], tuple[int, ...]]:
    """Each peak's XIC exactly as Discovery measured it, read once per window.

    Discovery measures every peak in the window centred on its ``peak_mz``;
    the peak table keeps that window.
    """
    unique: dict[XICRequest, int] = {}
    indices = []
    for item in peaks:
        request = XICRequest(
            mz=item.peak_mz,
            rt_min=item.ms1_search_rt_min,
            rt_max=item.ms1_search_rt_max,
            ppm_tol=settings.precursor_mz_tolerance_ppm,
        )
        indices.append(unique.setdefault(request, len(unique)))
    return tuple(unique), tuple(indices)


def _measure_peaks(
    peaks: tuple[DiscoveryPeak, ...],
    acquired_traces: tuple[AcquiredTrace, ...],
    profile: SignalProcessingProfile,
) -> tuple[PhysicalPeakEvaluation, ...]:
    """Measure each peak the way Discovery did, from the reread XIC.

    A window is prepared once for all its peaks; each peak is the segment its
    query RT selects. Equal RAW, window, query RT and settings give Discovery's
    measurement; this one is the published value.
    """
    populations: dict[XICRequest, PhysicalPeakPopulation] = {}
    evaluations = []
    for peak, acquired in zip(peaks, acquired_traces, strict=True):
        request = acquired.coordinate.request
        population = populations.get(request)
        if population is None:
            population = populations[request] = measure_physical_peak_population(
                acquired.trace.rt,
                acquired.trace.intensity,
                context_rt_start_min=request.rt_min,
                context_rt_end_min=request.rt_max,
                profile=profile,
            )
        evaluations.append(population.evaluate(peak.query_rt_min))
    return tuple(evaluations)


def acquire_untargeted_cid_nl_sample_trace_batch(
    *,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    peaks: tuple[DiscoveryPeak, ...],
    settings: DiscoverySettings,
    raw: CidNlRawSource,
    signal_processing_profile: SignalProcessingProfile,
) -> UntargetedCidNlSampleTraceBatch | None:
    """Acquire all eligible peak XICs for one sample in one adapter call."""

    capture_candidates = tuple(peak for peak in peaks if peak.is_clean_peak)
    if not capture_candidates:
        return None
    source = acquisition_source
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace=f"{_ADAPTER_NAMESPACE}.sample_batch",
    )
    requests, indices = _sample_capture_requests(
        peaks=capture_candidates, settings=settings
    )
    context_requests = _elution_context_requests(
        requests, padding_min=CID_NL_ALIGNMENT_CONFIG.preferred_rt_sec / 60.0
    )
    context_indices = tuple(range(len(requests), len(requests) + len(context_requests)))
    acquisition = _freeze_acquisition(
        acquire_untargeted_trace_batch(capability, (*requests, *context_requests), raw)
    )
    contexts = []
    for index in context_indices:
        acquired = acquisition.traces[index]
        context = project_elution_context(
            prepare_physical_signal(
                acquired.trace.rt,
                acquired.trace.intensity,
                profile=signal_processing_profile,
            ),
            sample_stem=sample.raw_stem,
            mz=acquired.coordinate.request.mz,
        )
        if context is not None:
            contexts.append(context)
    return UntargetedCidNlSampleTraceBatch(
        sample_key=sample.sample_name,
        acquisition_source_id=source.acquisition_source_id,
        candidate_ids=tuple(peak.peak_id for peak in capture_candidates),
        acquisition=acquisition,
        candidate_trace_indices=indices,
        context_trace_indices=context_indices,
        elution_contexts=tuple(contexts),
        candidate_evaluations=_measure_peaks(
            capture_candidates,
            tuple(acquisition.traces[index] for index in indices),
            signal_processing_profile,
        ),
    )


def _elution_context_requests(
    requests: tuple[XICRequest, ...], *, padding_min: float
) -> tuple[XICRequest, ...]:
    """Share neighboring context reads without widening integration requests."""
    merged: list[XICRequest] = []
    for request in sorted(
        requests, key=lambda item: (item.mz, item.ppm_tol, item.rt_min, item.rt_max)
    ):
        expanded = replace(
            request,
            rt_min=max(0.0, request.rt_min - padding_min),
            rt_max=request.rt_max + padding_min,
        )
        if (
            merged
            and (merged[-1].mz, merged[-1].ppm_tol) == (expanded.mz, expanded.ppm_tol)
            and expanded.rt_min <= merged[-1].rt_max
        ):
            merged[-1] = replace(
                merged[-1], rt_max=max(merged[-1].rt_max, expanded.rt_max)
            )
        else:
            merged.append(expanded)
    return tuple(merged)


def adapt_current_discovery_sample_subject(
    *,
    processing_attempt_id: str,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    method_subject: CidNlMethodSubject,
    peaks: tuple[DiscoveryPeak, ...],
    support_candidates: tuple[DiscoveryPeak, ...] = (),
    rt_reference_only: bool = False,
    settings: DiscoverySettings,
    raw: CidNlRawSource | None,
    _preacquired_traces: UntargetedCidNlSampleTraceBatch | None = None,
    signal_processing_profile: SignalProcessingProfile,
) -> CidNlAcquisitionResult:
    """Reacquire a subject and mint its complete CellObservation input."""

    bounded = _bounded_subject_candidates(
        peaks,
        method_subject=method_subject,
        settings=settings,
    )
    support_bounded = _bounded_subject_candidates(
        support_candidates,
        method_subject=method_subject,
        settings=settings,
    )
    if {peak.peak_id for peak in bounded} & {peak.peak_id for peak in support_bounded}:
        raise CidNlAcquisitionError("support peaks must be distinct from feature peaks")
    support_policy = DdaCidNlPeakSupportPolicy(
        precursor_tolerance_da=mz_tolerance_da(
            method_subject.precursor_mz,
            method_subject.precursor_tolerance_ppm,
            minimum_da=_WRITER_ROUNDING_ABS_TOL,
        ),
        neutral_loss_tolerance_ppm=settings.nl_tolerance_ppm,
    )
    seed_candidates = tuple(
        peak
        for peak in bounded
        if peak.precursor_mz_basis in {"scan_precursor", "scan_monoisotopic", "mixed"}
        or peak.is_clean_peak
    )
    peak_candidates = tuple(peak for peak in bounded if peak.is_clean_peak)
    support_seed_candidates = tuple(
        peak
        for peak in support_bounded
        if peak.precursor_mz_basis in {"scan_precursor", "scan_monoisotopic", "mixed"}
    )
    if not seed_candidates and not peak_candidates:
        assessment = _mint_assessment(
            sample=sample,
            acquisition_source=acquisition_source,
            method_subject=method_subject,
            settings=settings,
            raw=None,
            support_policy=support_policy,
            technical_state="not_evaluated",
            technical_reason_code="no_search_eligible_discovery_input",
        )
        return CidNlAcquisitionResult(
            assessment_input=UntargetedCidNlAssessmentInput(
                sample=sample,
                assessment=assessment,
            ),
            acquisition=None,
            candidate_observations=(),
            elution_contexts=(
                _preacquired_traces.elution_contexts
                if _preacquired_traces is not None
                else ()
            ),
            source_candidate_ids=tuple(item.peak_id for item in bounded),
            strict_scan_count=0,
        )
    if raw is None:
        raise CidNlAcquisitionError(
            "bounded subject universe requires an owner-opened RAW adapter"
        )
    source = acquisition_source
    scan_seeds = _located_discovery_events(seed_candidates + support_seed_candidates)
    capture_evidence: list[RetainedCaptureEvidence] = []
    acquisition, candidate_observations = _adopt_peak_observations(
        processing_attempt_id=processing_attempt_id,
        sample=sample,
        method_subject=method_subject,
        peaks=peak_candidates,
        settings=settings,
        raw=raw,
        source=source,
        signal_processing_profile=signal_processing_profile,
        evidence_sink=capture_evidence,
        rt_reference_only=rt_reference_only,
        _preacquired_traces=_preacquired_traces,
    )
    evidence_refs: list[CidNlEvidenceObservation] = []
    individual_refs: list[DdaCidNlPeakSupport] = []
    set_refs: list[DdaCidNlCandidateSetRelation] = []
    candidate_universe = tuple(
        sorted(
            (observation for _, observation in candidate_observations),
            key=lambda item: item.measured_order_key,
        )
    )
    for raw_seed in resolve_discovery_scan_events(
        scan_seeds,
        settings=settings,
        preserve_configured_losses=method_subject.composition is not None,
    ):
        seed = replace(raw_seed, acquisition_source_id=source.acquisition_source_id)
        evidence = discovery_scan_evidence(
            seed, sample=sample, acquisition_source=source, settings=settings
        )
        evidence_refs.append(evidence)
        resolved_support, resolved_sets = resolve_cid_nl_method_relations(
            sample=sample,
            evidence_refs=(evidence,),
            candidate_peaks=candidate_universe,
            policy=support_policy,
        )
        individual_refs.extend(resolved_support)
        set_refs.extend(resolved_sets)

    assessment = _mint_assessment(
        sample=sample,
        acquisition_source=source,
        method_subject=method_subject,
        settings=settings,
        raw=raw,
        support_policy=support_policy,
        candidate_peaks=candidate_universe,
        evidence_refs=tuple(evidence_refs),
        individual_support_refs=tuple(individual_refs),
        set_relation_refs=tuple(set_refs),
    )
    return CidNlAcquisitionResult(
        assessment_input=UntargetedCidNlAssessmentInput(
            sample=sample,
            assessment=assessment,
        ),
        acquisition=acquisition,
        candidate_observations=candidate_observations,
        elution_contexts=(
            _preacquired_traces.elution_contexts
            if _preacquired_traces is not None
            else ()
        ),
        source_candidate_ids=tuple(item.peak_id for item in bounded),
        strict_scan_count=len(
            {
                ref.scan_number
                for ref in evidence_refs
                if ref.assess_precursor_loss(settings.nl_tolerance_ppm).supports_nl
            }
        ),
        capture_evidence=tuple(capture_evidence),
    )


def _located_discovery_events(
    peaks: tuple[DiscoveryPeak, ...],
) -> tuple[DiscoveryEvent, ...]:
    """Every event the Discovery event table locates on these peaks.

    The table, not RAW, carries each event's precursor and parent-MS1 facts.
    """
    events: dict[tuple[object, ...], DiscoveryEvent] = {}
    for peak in peaks:
        for event in peak.located_events:
            events.setdefault(
                (
                    event.scan_number,
                    event.neutral_loss_tag,
                    event.precursor_mz,
                    event.product_mz,
                    event.precursor_mz_basis,
                ),
                event,
            )
    return tuple(events[key] for key in sorted(events))


def _adopt_peak_observations(
    *,
    processing_attempt_id: str,
    sample: SampleMetadata,
    method_subject: CidNlMethodSubject,
    peaks: tuple[DiscoveryPeak, ...],
    settings: DiscoverySettings,
    raw: CidNlRawSource,
    source,
    signal_processing_profile: SignalProcessingProfile,
    evidence_sink: list[RetainedCaptureEvidence] | None = None,
    rt_reference_only: bool = False,
    _preacquired_traces: UntargetedCidNlSampleTraceBatch | None = None,
) -> tuple[TraceAcquisitionBatch | None, tuple[tuple[str, PeakObservation], ...]]:
    if not peaks:
        return None, ()
    if _preacquired_traces is None:
        requests, indices = _sample_capture_requests(peaks=peaks, settings=settings)
        capability = mint_trace_acquisition_capability(
            source,
            adapter_namespace=_ADAPTER_NAMESPACE,
        )
        acquisition = _freeze_acquisition(
            acquire_untargeted_trace_batch(capability, requests, raw)
        )
        acquired_traces = tuple(acquisition.traces[index] for index in indices)
        evaluations = _measure_peaks(peaks, acquired_traces, signal_processing_profile)
    else:
        acquired_traces = tuple(
            _preacquired_traces.acquired_trace(peak.peak_id) for peak in peaks
        )
        evaluations = tuple(
            _preacquired_traces.evaluation(peak.peak_id) for peak in peaks
        )
        acquisition = _preacquired_traces.acquisition
    admitted = tuple(
        (peak, acquired, evaluation)
        for peak, acquired, evaluation in zip(
            peaks, acquired_traces, evaluations, strict=True
        )
        if evaluation.state == "available"
        and not (rt_reference_only and evaluation.requires_overlap_review)
    )
    observations: list[tuple[str, PeakObservation]] = []
    for peak, acquired, evaluation in admitted:
        trace = trace_from_xic_request(
            sample_name=sample.sample_name,
            request=acquired.coordinate.request,
            xic_trace=acquired.trace,
            source=UNTARGETED_CID_NL_CAPTURE_TRACE_SOURCE,
            acquisition_source_id=source.acquisition_source_id,
        )
        observation = mint_physical_peak_observations(
            sample=sample,
            trace=trace,
            evaluations=(evaluation,),
            signal_processing_profile=signal_processing_profile,
            resolver_mode=settings.resolver_mode,
        )[0]
        observations.append((peak.peak_id, observation))
        if evidence_sink is not None:
            evidence_sink.append(
                RetainedCaptureEvidence(
                    sample_key=sample.sample_name,
                    processing_attempt_id=processing_attempt_id,
                    method_subject_id=method_subject.method_subject_id,
                    candidate_id=peak.peak_id,
                    capture_stage="initial_capture",
                    trace=trace,
                    physical_evaluation=evaluation,
                    observation_ref=observation,
                )
            )
    if len({observation for _, observation in observations}) != len(observations):
        raise CidNlAcquisitionError("Discovery delivered one physical peak twice")
    return acquisition, tuple(observations)


def _mint_assessment(
    *,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    method_subject: CidNlMethodSubject,
    settings: DiscoverySettings,
    raw: CidNlRawSource | None,
    support_policy: DdaCidNlPeakSupportPolicy,
    candidate_peaks: tuple[PeakObservation, ...] = (),
    evidence_refs: tuple[CidNlEvidenceObservation, ...] = (),
    individual_support_refs: tuple[DdaCidNlPeakSupport, ...] = (),
    set_relation_refs: tuple[DdaCidNlCandidateSetRelation, ...] = (),
    technical_state: CidNlTechnicalState = "evaluated",
    technical_reason_code: str | None = None,
):
    search_completed = technical_state == "evaluated"
    candidate_query_rt_mins = tuple(
        peak.generation_provenance.query_rt_min for peak in candidate_peaks
    )
    candidate_query_rt_maxes = tuple(
        peak.generation_provenance.query_rt_max for peak in candidate_peaks
    )
    query_rt_min, query_rt_max = settings.rt_min, settings.rt_max
    if search_completed and (query_rt_min is None or query_rt_max is None):
        if raw is None:
            raise CidNlAcquisitionError(
                "automatic acquisition bounds require an opened RAW"
            )
        acquisition_start, acquisition_end = raw.acquisition_rt_bounds()
        query_rt_min = acquisition_start if query_rt_min is None else query_rt_min
        query_rt_max = acquisition_end if query_rt_max is None else query_rt_max
    elif not search_completed and (query_rt_min is None or query_rt_max is None):
        query_rt_min = query_rt_max = None
    try:
        return mint_cid_nl_method_assessment(
            sample=sample,
            acquisition_source=acquisition_source,
            method_subject=method_subject,
            detector_invocation_id=(
                f"untargeted-cid-nl-real-data:{settings.resolver_mode}"
            ),
            evaluation_receipt=CidNlEvaluationReceipt(
                query_rt_min=(
                    min(query_rt_min, *candidate_query_rt_mins)
                    if query_rt_min is not None and candidate_query_rt_mins
                    else query_rt_min
                ),
                query_rt_max=(
                    max(query_rt_max, *candidate_query_rt_maxes)
                    if query_rt_max is not None and candidate_query_rt_maxes
                    else query_rt_max
                ),
                support_policy=support_policy,
                acquisition_supported=technical_state != "unsupported",
                search_completed=search_completed,
                reason_code=technical_reason_code,
                evidence_scope="declared_scans",
            ),
            candidate_peaks=candidate_peaks,
            evidence_refs=evidence_refs,
            individual_support_refs=individual_support_refs,
            set_relation_refs=set_relation_refs,
            technical_state=technical_state,
        )
    except CidNlMethodAssessmentContractError as exc:
        raise CidNlAcquisitionError(str(exc)) from exc


def _bounded_subject_candidates(
    peaks: tuple[DiscoveryPeak, ...],
    *,
    method_subject: CidNlMethodSubject,
    settings: DiscoverySettings,
) -> tuple[DiscoveryPeak, ...]:
    return tuple(
        sorted(
            (
                peak
                for peak in peaks
                if _within_ppm(
                    peak.peak_mz,
                    method_subject.precursor_mz,
                    method_subject.precursor_tolerance_ppm,
                )
                and peak.neutral_loss_tag in settings.selected_tag_names
                and any(
                    math.isclose(
                        settings.neutral_loss_by_tag[peak.neutral_loss_tag],
                        loss,
                        rel_tol=_WRITER_ROUNDING_REL_TOL,
                        abs_tol=_WRITER_ROUNDING_ABS_TOL,
                    )
                    for loss in method_subject.configured_losses
                )
            ),
            key=lambda item: item.peak_id,
        )
    )


__all__ = [
    "CidNlAcquisitionResult",
    "UntargetedCidNlSampleTraceBatch",
    "acquire_untargeted_cid_nl_sample_trace_batch",
    "adapt_current_discovery_sample_subject",
    "UNTARGETED_CID_NL_CAPTURE_TRACE_SOURCE",
]
