"""Reacquire Discovery sample observations for Untargeted CID-NL assessment."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    TraceAcquisitionBatch,
    TraceCoordinateKind,
    mint_trace_acquisition_capability,
)
from xic_extractor.alignment.cid_nl_inputs import (
    CID_NL_ALIGNMENT_CONFIG,
    UntargetedCidNlAssessmentInput,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.models import DiscoveryEvent, DiscoveryPeak
from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.discovery.strict_event_receipts import (
    resolve_discovery_scan_events,
)
from xic_extractor.peak_detection import capture as capture_owner
from xic_extractor.peak_detection.cid_nl_candidate_set_relation import (
    DdaCidNlCandidateSetRelationRef,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceRef,
    mint_cid_nl_evidence_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodAssessmentContractError,
    CidNlTechnicalState,
    mint_cid_nl_method_assessment_ref,
    resolve_cid_nl_method_relations,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    CidNlComposition,
    CidNlMethodSubject,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
    DdaCidNlPeakSupportRef,
)
from xic_extractor.peak_detection.elution_context import (
    SampleElutionContext,
    project_elution_context,
)
from xic_extractor.peak_detection.observations import (
    PeakObservationRef,
    RetainedCaptureEvidence,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureCandidateBinding,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    SearchScopeContractError,
    UntargetedSearchScope,
)
from xic_extractor.peak_detection.traces import trace_from_xic_request
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.untargeted_execution.acquisition_cache import (
    acquire_untargeted_trace_batch,
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
    f"{_ADAPTER_NAMESPACE}:{TraceCoordinateKind.NATIVE_SCAN.value}"
)
_WRITER_ROUNDING_REL_TOL = 5e-6
_WRITER_ROUNDING_ABS_TOL = 5e-6


@dataclass(frozen=True, slots=True)
class CidNlAcquisitionResult:
    assessment_input: UntargetedCidNlAssessmentInput
    acquisition: TraceAcquisitionBatch | None
    captures: tuple[capture_owner.SampleLocalCapture, ...]
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

    def __post_init__(self) -> None:
        if not self.sample_key.strip() or not self.acquisition_source_id.strip():
            raise CidNlAcquisitionError("sample trace batch requires owner identities")
        if type(self.candidate_ids) is not tuple or not self.candidate_ids:
            raise CidNlAcquisitionError(
                "sample trace batch requires exact peak identities"
            )
        if self.candidate_ids != tuple(sorted(set(self.candidate_ids))):
            raise CidNlAcquisitionError(
                "sample trace batch peak identities must be canonical and unique"
            )
        if not isinstance(self.acquisition, TraceAcquisitionBatch):
            raise CidNlAcquisitionError("invalid sample acquisition")
        indices = self.candidate_trace_indices
        if indices is None:
            indices = tuple(range(len(self.candidate_ids)))
            object.__setattr__(self, "candidate_trace_indices", indices)
        all_indices = (*indices, *self.context_trace_indices)
        if (
            type(indices) is not tuple
            or len(indices) != len(self.candidate_ids)
            or any(type(index) is not int for index in indices)
            or type(self.context_trace_indices) is not tuple
            or any(type(index) is not int for index in self.context_trace_indices)
            or set(all_indices) != set(range(len(self.acquisition.traces)))
            or tuple(dict.fromkeys(all_indices))
            != tuple(range(len(self.acquisition.traces)))
            or self.acquisition.capability.acquisition_source.acquisition_source_id
            != self.acquisition_source_id
        ):
            raise CidNlAcquisitionError(
                "sample trace batch acquisition does not close its peaks"
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


def _sample_capture_requests(
    *,
    peaks: tuple[DiscoveryPeak, ...],
    settings: DiscoverySettings,
) -> tuple[tuple[XICRequest, ...], tuple[int, ...]]:
    """Each peak's XIC exactly as Discovery measured it, read once per window."""
    unique: dict[XICRequest, int] = {}
    indices = []
    for item in peaks:
        request = XICRequest(
            mz=item.xic_mz,
            rt_min=item.ms1_search_rt_min,
            rt_max=item.ms1_search_rt_max,
            ppm_tol=settings.precursor_mz_tolerance_ppm,
        )
        indices.append(unique.setdefault(request, len(unique)))
    return tuple(unique), tuple(indices)


def acquire_untargeted_cid_nl_sample_trace_batch(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    peaks: tuple[DiscoveryPeak, ...],
    settings: DiscoverySettings,
    raw: CidNlRawSource,
    signal_processing_profile: SignalProcessingProfile,
) -> UntargetedCidNlSampleTraceBatch | None:
    """Acquire all eligible peak XICs for one sample in one adapter call."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise CidNlAcquisitionError("processing_owners has the wrong owner type")
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise CidNlAcquisitionError("sample has the wrong owner type")
    if not isinstance(settings, DiscoverySettings):
        raise CidNlAcquisitionError("settings has the wrong owner type")
    if type(peaks) is not tuple or any(
        not isinstance(peak, DiscoveryPeak) for peak in peaks
    ):
        raise CidNlAcquisitionError("peaks must be current DiscoveryPeak receipts")
    candidate_ids = tuple(peak.peak_id for peak in peaks)
    if candidate_ids != tuple(sorted(set(candidate_ids))):
        raise CidNlAcquisitionError(
            "sample trace batch peaks must be canonical and unique"
        )
    raw_stem = sample.metadata.raw_stem
    if any(
        peak.sample_stem != raw_stem or peak.raw_file.stem != raw_stem for peak in peaks
    ):
        raise CidNlAcquisitionError(
            "sample trace batch peaks crossed the resolved sample"
        )
    capture_candidates = tuple(peak for peak in peaks if peak.is_clean_peak)
    if not capture_candidates:
        return None
    source = resolve_request_acquisition_source(
        processing_owners=processing_owners,
        sample=sample,
    )
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace=f"{_ADAPTER_NAMESPACE}.sample_batch",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    requests, indices = _sample_capture_requests(
        peaks=capture_candidates,
        settings=settings,
    )
    context_requests = _elution_context_requests(
        requests, padding_min=CID_NL_ALIGNMENT_CONFIG.preferred_rt_sec / 60.0
    )
    # Original requests and indices remain exact. Wider context is acquired
    # in this batch but is never supplied to the integration owner.
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
            sample_stem=sample.metadata.raw_stem,
            mz=acquired.coordinate.request.mz,
        )
        if context is not None:
            contexts.append(context)
    return UntargetedCidNlSampleTraceBatch(
        sample_key=sample.identity.sample_key,
        acquisition_source_id=source.acquisition_source_id,
        candidate_ids=tuple(peak.peak_id for peak in capture_candidates),
        acquisition=acquisition,
        candidate_trace_indices=indices,
        context_trace_indices=context_indices,
        elution_contexts=tuple(contexts),
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
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    method_subject: CidNlMethodSubject,
    peaks: tuple[DiscoveryPeak, ...],
    support_candidates: tuple[DiscoveryPeak, ...] = (),
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw: CidNlRawSource | None,
    _preacquired_traces: UntargetedCidNlSampleTraceBatch | None = None,
    signal_processing_profile: SignalProcessingProfile,
    physical_records: tuple[DiscoveryPhysicalFactRecord, ...] = (),
) -> CidNlAcquisitionResult:
    """Reacquire a subject and mint its complete CellObservationRef input."""

    _validate_inputs(
        processing_owners=processing_owners,
        sample=sample,
        method_subject=method_subject,
        peaks=peaks,
        support_candidates=support_candidates,
        settings=settings,
        peak_config=peak_config,
    )
    bounded = _bounded_subject_candidates(
        peaks,
        sample=sample,
        method_subject=method_subject,
        settings=settings,
    )
    support_bounded = _bounded_subject_candidates(
        support_candidates,
        sample=sample,
        method_subject=method_subject,
        settings=settings,
    )
    if {peak.peak_id for peak in bounded} & {peak.peak_id for peak in support_bounded}:
        raise CidNlAcquisitionError("support peaks must be distinct from feature peaks")
    support_policy = DdaCidNlPeakSupportPolicy(
        precursor_tolerance_da=max(
            method_subject.precursor_mz
            * method_subject.precursor_tolerance_ppm
            / 1_000_000.0,
            _WRITER_ROUNDING_ABS_TOL,
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
            processing_owners=processing_owners,
            sample=sample,
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
            captures=(),
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
    source = resolve_request_acquisition_source(
        processing_owners=processing_owners,
        sample=sample,
    )
    scan_seeds = _located_discovery_events(seed_candidates + support_seed_candidates)

    search_scope = _untargeted_search_scope(
        processing_owners=processing_owners,
        sample=sample,
        acquisition_source_id=source.acquisition_source_id,
        method_subject=method_subject,
        peaks=peaks,
        capture_candidates=peak_candidates,
        peak_config=peak_config,
        signal_processing_profile=signal_processing_profile,
        physical_records=physical_records,
    )
    capture_evidence: list[RetainedCaptureEvidence] = []
    acquisition, observations, captures = _adopt_peak_observations(
        processing_owners=processing_owners,
        sample=sample,
        peaks=peak_candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
        source=source,
        search_scope=search_scope,
        evidence_sink=capture_evidence,
        _preacquired_traces=_preacquired_traces,
    )
    evidence_refs: list[CidNlEvidenceRef] = []
    individual_refs: list[DdaCidNlPeakSupportRef] = []
    set_refs: list[DdaCidNlCandidateSetRelationRef] = []
    candidate_universe = tuple(
        sorted(
            observations,
            key=lambda item: item.content.measured_order_key,
        )
    )
    for raw_seed in resolve_discovery_scan_events(
        scan_seeds,
        settings=settings,
        preserve_configured_losses=method_subject.composition is not None,
    ):
        seed = replace(raw_seed, acquisition_source_id=source.acquisition_source_id)
        evidence = mint_cid_nl_evidence_ref(
            processing_owners=processing_owners,
            sample=sample,
            scan_number=seed.scan_number,
            precursor_mz=seed.precursor_mz,
            product_mz=seed.product_mz,
            configured_neutral_loss_da=seed.configured_neutral_loss_da,
            observed_neutral_loss_da=seed.observed_neutral_loss_da,
            observed_loss_error_ppm=seed.observed_loss_error_ppm,
            scan_rt_min=seed.rt,
            product_intensity=seed.product_intensity,
            precursor_mz_basis=seed.precursor_mz_basis,
            scan_precursor_mz=seed.scan_precursor_mz,
            precursor_metadata=seed.precursor_metadata,
            ms1_precursor_observation=seed.ms1_precursor_observation,
            precursor_inference_tolerance_da=(
                settings.ms2_precursor_tol_da
                if seed.precursor_mz_basis == "product_plus_neutral_loss"
                else None
            ),
        )
        evidence_refs.append(evidence)
        resolved_support, resolved_sets = resolve_cid_nl_method_relations(
            processing_owners=processing_owners,
            sample=sample,
            evidence_refs=(evidence,),
            candidate_peaks=candidate_universe,
            policy=support_policy,
        )
        individual_refs.extend(resolved_support)
        set_refs.extend(resolved_sets)

    assessment = _mint_assessment(
        processing_owners=processing_owners,
        sample=sample,
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
        captures=captures,
        elution_contexts=(
            _preacquired_traces.elution_contexts
            if _preacquired_traces is not None
            else ()
        ),
        source_candidate_ids=tuple(item.peak_id for item in bounded),
        strict_scan_count=len(
            {
                ref.observation.scan_number
                for ref in evidence_refs
                if ref.observation.assess_precursor_loss(
                    settings.nl_tolerance_ppm
                ).supports_nl
            }
        ),
        capture_evidence=tuple(capture_evidence),
    )


def _located_discovery_events(
    peaks: tuple[DiscoveryPeak, ...],
) -> tuple[DiscoveryEvent, ...]:
    """Every event the Discovery event table locates on these peaks.

    The table, not RAW, carries each event's precursor and parent-MS1 facts.
    A peak whose located scans differ from its declared scans belongs to
    another Discovery run, so its evidence cannot be assessed.
    """
    events: dict[tuple[object, ...], DiscoveryEvent] = {}
    for peak in peaks:
        if {event.scan_number for event in peak.located_events} != set(
            peak.event_scan_ids
        ) or any(
            event.sample_stem != peak.sample_stem for event in peak.located_events
        ):
            raise CidNlAcquisitionError(
                f"{peak.peak_id}: Discovery event table does not close the peak's "
                "declared MS2 scans; regenerate Discovery with the current version"
            )
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
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    peaks: tuple[DiscoveryPeak, ...],
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw: CidNlRawSource,
    source,
    search_scope: UntargetedSearchScope,
    evidence_sink: list[RetainedCaptureEvidence] | None = None,
    _preacquired_traces: UntargetedCidNlSampleTraceBatch | None = None,
) -> tuple[
    TraceAcquisitionBatch | None,
    tuple[PeakObservationRef, ...],
    tuple[capture_owner.SampleLocalCapture, ...],
]:
    if not peaks:
        return None, (), ()
    requests, indices = _sample_capture_requests(
        peaks=peaks,
        settings=settings,
    )
    if _preacquired_traces is None:
        capability = mint_trace_acquisition_capability(
            source,
            adapter_namespace=_ADAPTER_NAMESPACE,
            coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
        )
        acquisition = _freeze_acquisition(
            acquire_untargeted_trace_batch(capability, requests, raw)
        )
        acquired_traces = tuple(acquisition.traces[index] for index in indices)
    else:
        if (
            _preacquired_traces.sample_key != sample.identity.sample_key
            or _preacquired_traces.acquisition_source_id != source.acquisition_source_id
        ):
            raise CidNlAcquisitionError(
                "preacquired traces crossed their sample or acquisition source"
            )
        acquired_traces = tuple(
            _preacquired_traces.acquired_trace(peak.peak_id) for peak in peaks
        )
        acquisition = _preacquired_traces.acquisition
    for peak, acquired, index in zip(peaks, acquired_traces, indices, strict=True):
        if acquired.coordinate.request != requests[index]:
            raise CidNlAcquisitionError(
                "preacquired trace does not match the peak capture plan: "
                f"{peak.peak_id}: acquired={acquired.coordinate.request!r}, "
                f"planned={requests[index]!r}"
            )
    observations: list[PeakObservationRef] = []
    captures: list[capture_owner.SampleLocalCapture] = []
    for peak, acquired in zip(peaks, acquired_traces, strict=True):
        trace = trace_from_xic_request(
            sample_name=sample.metadata.sample_name,
            request=acquired.coordinate.request,
            xic_trace=acquired.trace,
            source=UNTARGETED_CID_NL_CAPTURE_TRACE_SOURCE,
            acquisition_source_id=source.acquisition_source_id,
        )
        binding = next(
            item
            for item in search_scope.capture_bindings
            if item.candidate_id == peak.peak_id
        )
        # Discovery measured the peak once; its retained evaluation is adopted
        # on the identical trace and never measured again here.
        capture = capture_owner.capture_peak_hypotheses(
            scope=search_scope,
            candidate_id=peak.peak_id,
            trace=trace,
        )
        captures.append(capture)
        if (
            capture.detection.status != "detected"
            or capture.selected_hypothesis is None
        ):
            raise CidNlAcquisitionError(
                f"{peak.peak_id}: current MS1 receipt was not reacquired"
            )
        if capture.physical_evaluation is not None:
            _validate_physical_candidate_receipt(peak, capture.physical_evaluation)
        observation = capture_owner.mint_captured_peak_observation(
            capture,
            processing_owners=processing_owners,
            sample=sample,
        )
        observations.append(observation)
        if evidence_sink is not None:
            evidence_sink.append(
                RetainedCaptureEvidence(
                    sample_key=sample.identity.sample_key,
                    processing_attempt_id=(
                        processing_owners.attempt.processing_attempt_id
                    ),
                    method_subject_id=binding.method_subject_id,
                    candidate_id=peak.peak_id,
                    capture_stage="initial_capture",
                    trace=trace,
                    physical_evaluation=capture.physical_evaluation,
                    observation_ref=observation,
                )
            )
    if len(set(observations)) != len(observations):
        raise CidNlAcquisitionError("Discovery delivered one physical peak twice")
    return acquisition, tuple(observations), tuple(captures)


def method_label(peak: DiscoveryPeak) -> str:
    """Return a diagnostic-only label; it is never configured Target identity."""

    return (
        f"untargeted:{peak.best_event_precursor_mz:.8g}:"
        f"NL{peak.configured_neutral_loss_da:.8g}"
    )


def _mint_assessment(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    method_subject: CidNlMethodSubject,
    settings: DiscoverySettings,
    raw: CidNlRawSource | None,
    support_policy: DdaCidNlPeakSupportPolicy,
    candidate_peaks: tuple[PeakObservationRef, ...] = (),
    evidence_refs: tuple[CidNlEvidenceRef, ...] = (),
    individual_support_refs: tuple[DdaCidNlPeakSupportRef, ...] = (),
    set_relation_refs: tuple[DdaCidNlCandidateSetRelationRef, ...] = (),
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
        return mint_cid_nl_method_assessment_ref(
            processing_owners=processing_owners,
            sample=sample,
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
    sample: ResolvedCanonicalSampleIdentity,
    method_subject: CidNlMethodSubject,
    settings: DiscoverySettings,
) -> tuple[DiscoveryPeak, ...]:
    ids = tuple(peak.peak_id for peak in peaks)
    if len(ids) != len(set(ids)):
        raise CidNlAcquisitionError("Discovery peak IDs must be unique")
    for peak in peaks:
        if peak.sample_stem != sample.metadata.raw_stem:
            raise CidNlAcquisitionError(
                f"{peak.peak_id}: peak references a different sample"
            )
        if peak.raw_file.stem != sample.metadata.raw_stem:
            raise CidNlAcquisitionError(
                f"{peak.peak_id}: raw receipt mismatches its sample"
            )
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
                and any(
                    math.isclose(
                        peak.configured_neutral_loss_da,
                        loss,
                        rel_tol=_WRITER_ROUNDING_REL_TOL,
                        abs_tol=_WRITER_ROUNDING_ABS_TOL,
                    )
                    for loss in method_subject.configured_losses
                )
                and peak.neutral_loss_tag in settings.selected_tag_names
            ),
            key=lambda item: item.peak_id,
        )
    )


def _untargeted_search_scope(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    acquisition_source_id: str,
    method_subject: CidNlMethodSubject,
    peaks: tuple[DiscoveryPeak, ...],
    capture_candidates: tuple[DiscoveryPeak, ...],
    peak_config: ExtractionConfig,
    signal_processing_profile: SignalProcessingProfile,
    physical_records: tuple[DiscoveryPhysicalFactRecord, ...] = (),
) -> UntargetedSearchScope:
    records = {record.peak_id: record for record in physical_records}
    try:
        return UntargetedSearchScope(
            schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
            request=processing_owners.request,
            samples=(sample,),
            method_constraints=CaptureMethodConstraints(
                boundary_policy="resolver_interval",
                peak_config=peak_config,
                signal_processing_profile=signal_processing_profile,
            ),
            evidence_providers=(
                EvidenceProviderBinding(
                    provider_id=CID_NL_PROVIDER_ID,
                    method_subject_id=method_subject.method_subject_id,
                ),
            ),
            declared_candidate_ids=tuple(item.peak_id for item in peaks),
            capture_bindings=tuple(
                CaptureCandidateBinding(
                    candidate_id=item.peak_id,
                    method_subject_id=method_subject.method_subject_id,
                    sample_key=sample.identity.sample_key,
                    acquisition_source_id=acquisition_source_id,
                    query_mz=item.peak_mz,
                    query_rt_min=item.ms1_search_rt_min,
                    query_rt_max=item.ms1_search_rt_max,
                    query_ppm_tol=method_subject.precursor_tolerance_ppm,
                    preferred_rt=item.best_event_rt,
                    physical_evaluation=(records[item.peak_id].evaluation),
                    # The query names the peak's ion; the retained evaluation
                    # belongs to the XIC Discovery measured, which is read here.
                    physical_evaluation_mz=(records[item.peak_id].work_item.xic_mz),
                )
                for item in capture_candidates
            ),
        )
    except SearchScopeContractError as exc:
        raise CidNlAcquisitionError(str(exc)) from exc


def _validate_inputs(**values: object) -> None:
    expected = {
        "processing_owners": ProcessingOwnerReferences,
        "sample": ResolvedCanonicalSampleIdentity,
        "method_subject": CidNlMethodSubject,
        "peaks": tuple,
        "support_candidates": tuple,
        "settings": DiscoverySettings,
        "peak_config": ExtractionConfig,
    }
    for name, kind in expected.items():
        if not isinstance(values[name], kind):
            raise CidNlAcquisitionError(f"{name} has the wrong owner type")
    peaks = values["peaks"]
    assert isinstance(peaks, tuple)
    if any(not isinstance(item, DiscoveryPeak) for item in peaks):
        raise CidNlAcquisitionError("peaks must contain current DiscoveryPeak values")
    support_candidates = values["support_candidates"]
    assert isinstance(support_candidates, tuple)
    if any(not isinstance(item, DiscoveryPeak) for item in support_candidates):
        raise CidNlAcquisitionError(
            "support_candidates must contain current DiscoveryPeak values"
        )
    method_subject = values["method_subject"]
    settings = values["settings"]
    peak_config = values["peak_config"]
    processing_owners = values["processing_owners"]
    assert isinstance(method_subject, CidNlMethodSubject)
    assert isinstance(settings, DiscoverySettings)
    assert isinstance(peak_config, ExtractionConfig)
    assert isinstance(processing_owners, ProcessingOwnerReferences)
    if method_subject.subject_kind != "untargeted_precursor":
        raise CidNlAcquisitionError(
            "adapter requires an Untargeted CID-NL method subject"
        )
    if method_subject.composition is not None:
        if settings.tag_combine_mode == "single":
            raise CidNlAcquisitionError(
                "Composite assessment requires union or intersection"
            )
        expected_composition = CidNlComposition(
            tags=tuple(
                (profile.tag, profile.neutral_loss_da)
                for profile in settings.neutral_loss_profiles
            ),
            combine_mode=settings.tag_combine_mode,
        )
        if method_subject.composition != expected_composition:
            raise CidNlAcquisitionError(
                "Discovery composition mismatches the method subject"
            )
    elif (
        settings.tag_combine_mode not in {"single", "union", "intersection"}
        or len(settings.selected_tag_names) != 1
        or len(settings.neutral_loss_profiles) != 1
    ):
        raise CidNlAcquisitionError(
            "The real-data runtime accepts exactly one CID-NL Discovery tag"
        )
    if method_subject.composition is None and not math.isclose(
        settings.neutral_loss_profiles[0].neutral_loss_da,
        method_subject.configured_losses[0],
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise CidNlAcquisitionError(
            "Discovery neutral loss mismatches the method subject"
        )
    if not math.isclose(
        settings.precursor_mz_tolerance_ppm,
        method_subject.precursor_tolerance_ppm,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise CidNlAcquisitionError(
            "Discovery precursor tolerance mismatches the method subject"
        )
    if peak_config.baseline_integration_method != "asls":
        raise CidNlAcquisitionError(
            "peak_config requires canonical asls baseline integration"
        )


def _validate_physical_candidate_receipt(
    peak: DiscoveryPeak,
    evaluation: PhysicalPeakEvaluation,
) -> None:
    facts = evaluation.facts
    if evaluation.state != "available" or facts is None:
        raise CidNlAcquisitionError("physical receipt was not reacquired")
    for field, expected in (
        ("apex_rt_min", peak.ms1_apex_rt),
        ("left_rt_min", peak.ms1_peak_rt_start),
        ("right_rt_min", peak.ms1_peak_rt_end),
        ("area_counts_seconds", peak.ms1_area),
        ("height", peak.ms1_height),
    ):
        if expected is None or not math.isclose(
            getattr(facts, field),
            expected,
            rel_tol=_WRITER_ROUNDING_REL_TOL,
            abs_tol=_WRITER_ROUNDING_ABS_TOL,
        ):
            raise CidNlAcquisitionError(
                f"{peak.peak_id}: physical {field} mismatches current receipt"
            )


__all__ = [
    "CidNlAcquisitionResult",
    "UntargetedCidNlSampleTraceBatch",
    "acquire_untargeted_cid_nl_sample_trace_batch",
    "adapt_current_discovery_sample_subject",
    "method_label",
    "UNTARGETED_CID_NL_CAPTURE_TRACE_SOURCE",
]
