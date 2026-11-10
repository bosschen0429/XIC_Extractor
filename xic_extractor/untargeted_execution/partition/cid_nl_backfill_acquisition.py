"""Acquire and assess group-directed Untargeted CID-NL backfill observations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import cast

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    TraceAcquisitionBatch,
    TraceCoordinateKind,
    mint_trace_acquisition_capability,
)
from xic_extractor.alignment.backfill.cid_nl_planning import CidNlBackfillQuestion
from xic_extractor.alignment.cid_nl_inputs import UntargetedCidNlBackfillInput
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.models import DiscoveryEvent
from xic_extractor.discovery.ms2_events import discovery_events_from_scan
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.discovery.strict_event_receipts import resolve_discovery_scan_events
from xic_extractor.peak_detection import capture as capture_owner
from xic_extractor.peak_detection.cid_nl_candidate_set_relation import (
    DdaCidNlCandidateSetRelationRef,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceObservation,
    cid_nl_evidence_observation,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodAssessmentContractError,
    CidNlMethodAssessmentRef,
    is_cid_nl_missing_ms1_rejection,
    mint_cid_nl_method_assessment_ref,
    resolve_cid_nl_method_relations,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlComposition,
    CidNlMethodSubject,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
    DdaCidNlPeakSupportRef,
)
from xic_extractor.peak_detection.observations import (
    PeakObservationRef,
    RetainedCaptureEvidence,
)
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation
from xic_extractor.peak_detection.traces import trace_from_xic_request
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.sample_metadata import SampleMetadata
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


@dataclass(frozen=True, slots=True)
class UntargetedCidNlBackfillAttemptResult:
    assessment: CidNlMethodAssessmentRef
    backfill_input: UntargetedCidNlBackfillInput | None
    acquisition: TraceAcquisitionBatch
    group_hypothesis_id: str | None = None
    query_rt: float | None = None
    query_rt_basis: str = "group_center"
    capture_evidence: tuple[RetainedCaptureEvidence, ...] = dataclass_field(
        default=(), compare=False
    )


@dataclass(frozen=True, slots=True)
class UntargetedCidNlBackfillTarget:
    """One frozen group question using its physical ion and subject lineage."""

    group_hypothesis_id: str
    method_subject: CidNlMethodSubject
    physical_mz: float
    center_rt: float
    query_rt: float | None = None
    query_rt_basis: str = "group_center"
    query_rt_tolerance_sec: float | None = None
    query_rt_reliable: bool = True

    @classmethod
    def from_question(
        cls, question: CidNlBackfillQuestion
    ) -> UntargetedCidNlBackfillTarget:
        """Adapt the complete scientific question to the acquisition contract."""
        group = question.group
        assert group.group_center_rt is not None
        localized = question.localization
        return cls(
            group_hypothesis_id=group.group_hypothesis_id,
            method_subject=question.method_subject,
            physical_mz=group.family_center_mz,
            center_rt=group.group_center_rt,
            query_rt=None if localized is None else localized.location.query_rt,
            query_rt_basis=(
                "group_center" if localized is None else localized.location.basis
            ),
            query_rt_tolerance_sec=(
                None if localized is None else localized.apex_tolerance_sec
            ),
            query_rt_reliable=True if localized is None else localized.reliable,
        )

    @property
    def sample_query_rt(self) -> float:
        return self.center_rt if self.query_rt is None else self.query_rt

    def __post_init__(self) -> None:
        if (
            not isinstance(self.group_hypothesis_id, str)
            or not self.group_hypothesis_id.strip()
            or not isinstance(self.method_subject, CidNlMethodSubject)
            or isinstance(self.physical_mz, bool)
            or not isinstance(self.physical_mz, (int, float))
            or not math.isfinite(float(self.physical_mz))
            or self.physical_mz <= 0.0
            or isinstance(self.center_rt, bool)
            or not isinstance(self.center_rt, (int, float))
            or not math.isfinite(float(self.center_rt))
            or self.center_rt < 0.0
            or isinstance(self.sample_query_rt, bool)
            or not isinstance(self.sample_query_rt, (int, float))
            or not math.isfinite(float(self.sample_query_rt))
            or self.sample_query_rt < 0.0
            or not isinstance(self.query_rt_basis, str)
            or not self.query_rt_basis.strip()
            or type(self.query_rt_reliable) is not bool
            or (
                self.query_rt_tolerance_sec is not None
                and (
                    isinstance(self.query_rt_tolerance_sec, bool)
                    or not isinstance(self.query_rt_tolerance_sec, (int, float))
                    or not math.isfinite(self.query_rt_tolerance_sec)
                    or self.query_rt_tolerance_sec <= 0.0
                )
            )
        ):
            raise CidNlAcquisitionError(
                "backfill target requires a group, physical mass, and RT"
            )


@dataclass(frozen=True, slots=True)
class UntargetedCidNlBackfillTraceBatch:
    """One sample-local backfill phase with exact group-to-trace attribution."""

    sample_key: str
    acquisition_source_id: str
    targets: tuple[UntargetedCidNlBackfillTarget, ...]
    requests: tuple[XICRequest, ...]
    trace_indices: tuple[int, ...]
    acquisition: TraceAcquisitionBatch
    context_requests: tuple[XICRequest, ...] = ()
    context_trace_indices: tuple[int, ...] = ()

    @property
    def group_hypothesis_ids(self) -> tuple[str, ...]:
        return tuple(target.group_hypothesis_id for target in self.targets)

    def __post_init__(self) -> None:
        if not self.sample_key.strip() or not self.acquisition_source_id.strip():
            raise CidNlAcquisitionError(
                "backfill trace batch requires owner identities"
            )
        if (
            type(self.targets) is not tuple
            or not self.targets
            or any(
                not isinstance(target, UntargetedCidNlBackfillTarget)
                for target in self.targets
            )
            or len(self.group_hypothesis_ids) != len(set(self.group_hypothesis_ids))
            or self.targets
            != tuple(
                sorted(
                    self.targets,
                    key=lambda target: (
                        target.method_subject.method_subject_id,
                        target.center_rt,
                        target.group_hypothesis_id,
                    ),
                )
            )
            or type(self.requests) is not tuple
            or type(self.trace_indices) is not tuple
            or type(self.context_requests) is not tuple
            or type(self.context_trace_indices) is not tuple
            or not (len(self.targets) == len(self.requests) == len(self.trace_indices))
        ):
            raise CidNlAcquisitionError(
                "backfill trace batch entries must be canonical and complete"
            )
        if (
            not isinstance(self.acquisition, TraceAcquisitionBatch)
            or set((*self.trace_indices, *self.context_trace_indices))
            != set(range(len(self.acquisition.traces)))
            or any(
                self.acquisition.traces[index].coordinate.request != request
                for request, index in zip(
                    self.requests, self.trace_indices, strict=True
                )
            )
        ):
            raise CidNlAcquisitionError(
                "backfill trace batch acquisition does not close its requests"
            )
        if (
            len(self.context_requests) != len(self.context_trace_indices)
            or len(self.context_requests) not in (0, len(self.targets))
            or any(
                self.acquisition.traces[index].coordinate.request != request
                for request, index in zip(
                    self.context_requests, self.context_trace_indices, strict=True
                )
            )
        ):
            raise CidNlAcquisitionError("backfill context requests are not bound")

    def acquired_trace(
        self,
        *,
        group_hypothesis_id: str,
        request: XICRequest,
    ) -> AcquiredTrace:
        try:
            index = self.group_hypothesis_ids.index(group_hypothesis_id)
        except ValueError as exc:
            raise CidNlAcquisitionError(
                "backfill trace batch has no matching group"
            ) from exc
        if self.requests[index] == request:
            return self.acquisition.traces[self.trace_indices[index]]
        if self.context_requests and self.context_requests[index] == request:
            return self.acquisition.traces[self.context_trace_indices[index]]
        raise CidNlAcquisitionError(
            "backfill trace batch request crossed its owner decision"
        )


def _backfill_xic_request(
    *,
    mz: float,
    query_rt: float,
    half_window: float,
    ppm_tol: float,
    physical_context: bool,
) -> XICRequest:
    # One additional search half-window per flank supplies signal context.
    # This is a bounded acquisition budget, never an expanded search permission.
    context_half_window = half_window * (2.0 if physical_context else 1.0)
    return XICRequest(
        mz=mz,
        rt_min=max(0.0, float(query_rt) - context_half_window),
        rt_max=float(query_rt) + context_half_window,
        ppm_tol=ppm_tol,
    )


def acquire_untargeted_cid_nl_backfill_trace_batch(
    *,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    backfill_requests: tuple[UntargetedCidNlBackfillTarget, ...],
    peak_config: ExtractionConfig,
    raw: CidNlRawSource,
) -> UntargetedCidNlBackfillTraceBatch:
    """Acquire search traces and bounded fallback context in one sample phase."""

    if not isinstance(peak_config, ExtractionConfig):
        raise CidNlAcquisitionError("peak_config has the wrong owner type")
    if (
        type(backfill_requests) is not tuple
        or not backfill_requests
        or any(
            not isinstance(item, UntargetedCidNlBackfillTarget)
            for item in backfill_requests
        )
        or len({item.group_hypothesis_id for item in backfill_requests})
        != len(backfill_requests)
    ):
        raise CidNlAcquisitionError("backfill requests must be a non-empty typed tuple")
    targets = tuple(
        sorted(
            backfill_requests,
            key=lambda target: (
                target.method_subject.method_subject_id,
                target.center_rt,
                target.group_hypothesis_id,
            ),
        )
    )
    source = acquisition_source
    half_window = peak_config.nl_rt_anchor_half_window_min
    requests = tuple(
        _backfill_xic_request(
            mz=target.physical_mz,
            query_rt=target.sample_query_rt,
            half_window=half_window,
            ppm_tol=target.method_subject.precursor_tolerance_ppm,
            physical_context=False,
        )
        for target in targets
    )
    unique: dict[XICRequest, int] = {}
    trace_indices = tuple(
        unique.setdefault(request, len(unique)) for request in requests
    )
    context_requests = tuple(
        _backfill_xic_request(
            mz=target.physical_mz,
            query_rt=target.sample_query_rt,
            half_window=half_window,
            ppm_tol=target.method_subject.precursor_tolerance_ppm,
            physical_context=True,
        )
        for target in targets
    )
    context_trace_indices = tuple(
        unique.setdefault(request, len(unique)) for request in context_requests
    )
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace=f"{_ADAPTER_NAMESPACE}.backfill_batch",
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )
    acquisition = _freeze_acquisition(
        acquire_untargeted_trace_batch(capability, tuple(unique), raw)
    )
    return UntargetedCidNlBackfillTraceBatch(
        sample_key=sample.sample_name,
        acquisition_source_id=source.acquisition_source_id,
        targets=targets,
        requests=requests,
        trace_indices=trace_indices,
        acquisition=acquisition,
        context_requests=context_requests,
        context_trace_indices=context_trace_indices,
    )


def backfill_untargeted_cid_nl_sample_subject(
    *,
    processing_attempt_id: str,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    method_subject: CidNlMethodSubject,
    group_center_rt: float,
    group_center_mz: float,
    query_rt: float | None = None,
    query_rt_basis: str = "group_center",
    query_rt_tolerance_sec: float | None = None,
    query_rt_reliable: bool = True,
    group_hypothesis_id: str | None = None,
    support_policy: DdaCidNlPeakSupportPolicy,
    peak_config: ExtractionConfig,
    raw: CidNlRawSource | None,
    initial_assessment: CidNlMethodAssessmentRef | None = None,
    settings: DiscoverySettings | None = None,
    signal_processing_profile: SignalProcessingProfile,
    _preacquired_traces: UntargetedCidNlBackfillTraceBatch | None = None,
) -> UntargetedCidNlBackfillAttemptResult:
    """Evaluate one owner-authorized bounded physical candidate universe.

    MS1-only backfill remains HOLD. A missing-MS1 rejection may contribute its
    immutable measured CID-NL evidence to new owner-resolved relations.
    """

    if not isinstance(method_subject, CidNlMethodSubject):
        raise CidNlAcquisitionError("method_subject has the wrong owner type")
    if not isinstance(peak_config, ExtractionConfig):
        raise CidNlAcquisitionError("peak_config has the wrong owner type")
    if not isinstance(support_policy, DdaCidNlPeakSupportPolicy):
        raise CidNlAcquisitionError("support_policy has the wrong owner type")
    if initial_assessment is not None:
        if (
            not is_cid_nl_missing_ms1_rejection(initial_assessment)
            or initial_assessment.content.evaluation_receipt.support_policy
            != support_policy
        ):
            raise CidNlAcquisitionError(
                "backfill requires the exact missing-MS1 rejection and support policy"
            )
    if (
        isinstance(group_center_rt, bool)
        or not isinstance(group_center_rt, (int, float))
        or not math.isfinite(float(group_center_rt))
        or float(group_center_rt) < 0.0
    ):
        raise CidNlAcquisitionError("group_center_rt must be finite and non-negative")
    query_rt = group_center_rt if query_rt is None else query_rt
    if (
        isinstance(query_rt, bool)
        or not isinstance(query_rt, (int, float))
        or not math.isfinite(float(query_rt))
        or query_rt < 0.0
        or not isinstance(query_rt_basis, str)
        or not query_rt_basis.strip()
    ):
        raise CidNlAcquisitionError(
            "query RT requires a finite non-negative value and basis"
        )
    physical_mz = group_center_mz
    if (
        isinstance(physical_mz, bool)
        or not isinstance(physical_mz, (int, float))
        or not math.isfinite(float(physical_mz))
        or physical_mz <= 0.0
    ):
        raise CidNlAcquisitionError("group_center_mz must be finite and positive")
    source = acquisition_source
    half_window = peak_config.nl_rt_anchor_half_window_min
    query_rt_min = max(0.0, float(query_rt) - half_window)
    query_rt_max = float(query_rt) + half_window
    selection_window = (
        (
            max(query_rt_min, float(query_rt) - query_rt_tolerance_sec / 60.0),
            min(query_rt_max, float(query_rt) + query_rt_tolerance_sec / 60.0),
        )
        if query_rt_tolerance_sec is not None
        else None
    )
    request = _backfill_xic_request(
        mz=physical_mz,
        query_rt=float(query_rt),
        half_window=half_window,
        ppm_tol=method_subject.precursor_tolerance_ppm,
        physical_context=False,
    )
    context_request = _backfill_xic_request(
        mz=physical_mz,
        query_rt=float(query_rt),
        half_window=half_window,
        ppm_tol=method_subject.precursor_tolerance_ppm,
        physical_context=True,
    )
    if _preacquired_traces is None:
        if raw is None:
            raise CidNlAcquisitionError(
                "backfill requires owner-opened RAW or preacquired traces"
            )
        capability = mint_trace_acquisition_capability(
            source,
            adapter_namespace=f"{_ADAPTER_NAMESPACE}.backfill",
            coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
        )
        acquisition = _freeze_acquisition(
            acquire_untargeted_trace_batch(
                capability,
                (request, context_request),
                raw,
            )
        )
        acquired = acquisition.traces[0]
        context_acquired = acquisition.traces[1]
    else:
        if group_hypothesis_id is None:
            raise CidNlAcquisitionError(
                "preacquired backfill requires exact group identity"
            )
        if (
            _preacquired_traces.sample_key != sample.sample_name
            or _preacquired_traces.acquisition_source_id != source.acquisition_source_id
        ):
            raise CidNlAcquisitionError(
                "backfill trace batch crossed its sample or acquisition source"
            )
        acquisition = _preacquired_traces.acquisition
        acquired = _preacquired_traces.acquired_trace(
            group_hypothesis_id=group_hypothesis_id,
            request=request,
        )
        context_acquired = _preacquired_traces.acquired_trace(
            group_hypothesis_id=group_hypothesis_id,
            request=context_request,
        )
    observations: tuple[PeakObservationRef, ...] = ()
    physical_evaluations: list[PhysicalPeakEvaluation] = []
    # Complete observations keep their original signal and integral. Only
    # clipped context permits one bounded reconsideration of the same search.
    for capture_request, capture_acquired in (
        (request, acquired),
        (context_request, context_acquired),
    ):
        trace = trace_from_xic_request(
            sample_name=sample.sample_name,
            request=capture_request,
            xic_trace=capture_acquired.trace,
            source=(
                f"{_ADAPTER_NAMESPACE}.backfill:{TraceCoordinateKind.NATIVE_SCAN.value}"
            ),
            acquisition_source_id=source.acquisition_source_id,
        )
        physical_evaluations.clear()
        observation = capture_owner.capture_physical_backfill_observation(
            sample=sample,
            method_subject=method_subject,
            trace=trace,
            query_rt_min=float(query_rt),
            location_reliable=query_rt_reliable,
            search_rt_window=selection_window
            or (
                (query_rt_min, query_rt_max)
                if capture_request is context_request
                else None
            ),
            physical_mz=float(physical_mz),
            profile=signal_processing_profile,
            resolver_mode=peak_config.resolver_mode,
            evaluation_sink=physical_evaluations.append,
        )
        if physical_evaluations[0].reason != "incomplete_peak_context":
            break
    observations = (observation,) if observation is not None else ()
    backfill_evidence: tuple[CidNlEvidenceObservation, ...] = ()
    backfill_support: tuple[DdaCidNlPeakSupportRef, ...] = ()
    backfill_sets: tuple[DdaCidNlCandidateSetRelationRef, ...] = ()
    if initial_assessment is not None and observations:
        backfill_evidence = tuple(
            ref
            for ref in initial_assessment.evidence_refs
            if query_rt_min <= ref.scan_rt_min <= query_rt_max
        )
        backfill_support, backfill_sets = resolve_cid_nl_method_relations(
            sample=sample,
            evidence_refs=backfill_evidence,
            candidate_peaks=observations,
            policy=support_policy,
        )
    elif method_subject.composition is not None:
        if raw is None or settings is None:
            raise CidNlAcquisitionError(
                "composite backfill requires full settings and owner-opened MS2 source"
            )
        if settings.tag_combine_mode == "single":
            raise CidNlAcquisitionError(
                "Composite backfill requires union or intersection"
            )
        expected = CidNlComposition(
            tags=tuple(
                (profile.tag, profile.neutral_loss_da)
                for profile in settings.neutral_loss_profiles
            ),
            combine_mode=settings.tag_combine_mode,
        )
        if expected != method_subject.composition:
            raise CidNlAcquisitionError(
                "backfill composition mismatches the method subject"
            )
        backfill_evidence, backfill_support, backfill_sets = (
            _composite_backfill_evidence(
                sample=sample,
                acquisition_source=source,
                method_subject=method_subject,
                # Composite workers retain RAW even with preacquired traces.
                raw=cast(CidNlRawSource, raw),
                settings=settings,
                query_rt_min=query_rt_min,
                query_rt_max=query_rt_max,
                observations=observations,
                support_policy=support_policy,
            )
        )
    try:
        assessment = mint_cid_nl_method_assessment_ref(
            sample=sample,
            acquisition_source=source,
            method_subject=method_subject,
            detector_invocation_id="untargeted-cid-nl-backfill",
            evaluation_receipt=CidNlEvaluationReceipt(
                # The physical owner may recover a complete query-containing
                # peak from bounded context. Cover that evaluated acquisition,
                # without expanding the search question or MS2 evidence query.
                query_rt_min=min(
                    (
                        query_rt_min,
                        *(p.generation_provenance.query_rt_min for p in observations),
                    )
                ),
                query_rt_max=max(
                    (
                        query_rt_max,
                        *(p.generation_provenance.query_rt_max for p in observations),
                    )
                ),
                support_policy=support_policy,
                acquisition_supported=True,
                search_completed=True,
                reason_code=None,
                evidence_scope=(
                    initial_assessment.content.evaluation_receipt.evidence_scope
                    if initial_assessment is not None and backfill_evidence
                    else "subject_window"
                ),
                physical_query_mz=request.mz,
            ),
            candidate_peaks=observations,
            evidence_refs=backfill_evidence,
            individual_support_refs=backfill_support,
            set_relation_refs=backfill_sets,
        )
    except CidNlMethodAssessmentContractError as exc:
        raise CidNlAcquisitionError(str(exc)) from exc
    backfill_input = (
        UntargetedCidNlBackfillInput(
            sample=sample,
            candidate_assessment=assessment,
        )
        if observations and assessment.content.method_result == "HOLD"
        else None
    )
    return UntargetedCidNlBackfillAttemptResult(
        assessment=assessment,
        backfill_input=backfill_input,
        acquisition=acquisition,
        group_hypothesis_id=group_hypothesis_id,
        query_rt=float(query_rt),
        query_rt_basis=query_rt_basis,
        capture_evidence=tuple(
            RetainedCaptureEvidence(
                sample_key=sample.sample_name,
                processing_attempt_id=processing_attempt_id,
                method_subject_id=method_subject.method_subject_id,
                candidate_id=None,
                capture_stage="method_backfill",
                trace=trace,
                physical_evaluation=evaluation,
                observation_ref=observation,
            )
            for evaluation in physical_evaluations
        ),
    )


def _composite_backfill_evidence(
    *,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    method_subject: CidNlMethodSubject,
    raw: CidNlRawSource,
    settings: DiscoverySettings,
    query_rt_min: float,
    query_rt_max: float,
    observations: tuple[PeakObservationRef, ...],
    support_policy: DdaCidNlPeakSupportPolicy,
) -> tuple[
    tuple[CidNlEvidenceObservation, ...],
    tuple[DdaCidNlPeakSupportRef, ...],
    tuple[DdaCidNlCandidateSetRelationRef, ...],
]:
    """Evaluate the configured loss set on the backfilled physical universe."""
    evidence_refs: list[CidNlEvidenceObservation] = []
    individual_refs: list[DdaCidNlPeakSupportRef] = []
    set_refs: list[DdaCidNlCandidateSetRelationRef] = []
    scan_numbers: set[int] = set()
    subject_seeds: list[DiscoveryEvent] = []
    for event in raw.iter_ms2_scans(query_rt_min, query_rt_max):
        if event.parse_error is not None or event.scan is None:
            raise CidNlAcquisitionError(
                "composite backfill MS2 acquisition did not complete"
            )
        scan = event.scan
        if scan.scan_number in scan_numbers:
            raise CidNlAcquisitionError(
                "duplicate acquisition scan during composite backfill"
            )
        scan_numbers.add(scan.scan_number)
        subject_seeds.extend(
            seed
            for seed in discovery_events_from_scan(
                scan, raw_file=Path(sample.raw_stem), settings=settings
            )
            if seed.supports_direct_nl
            and _within_ppm(
                seed.precursor_mz,
                method_subject.precursor_mz,
                method_subject.precursor_tolerance_ppm,
            )
        )
    for seed in resolve_discovery_scan_events(
        tuple(subject_seeds),
        settings=settings,
        preserve_configured_losses=method_subject.composition is not None,
    ):
        evidence = cid_nl_evidence_observation(
            sample=sample,
            acquisition_source=acquisition_source,
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
        )
        evidence_refs.append(evidence)
        resolved_support, resolved_sets = resolve_cid_nl_method_relations(
            sample=sample,
            evidence_refs=(evidence,),
            candidate_peaks=observations,
            policy=support_policy,
        )
        individual_refs.extend(resolved_support)
        set_refs.extend(resolved_sets)
    return (
        tuple(evidence_refs),
        tuple(individual_refs),
        tuple(set_refs),
    )


__all__ = [
    "UntargetedCidNlBackfillAttemptResult",
    "UntargetedCidNlBackfillTarget",
    "UntargetedCidNlBackfillTraceBatch",
    "acquire_untargeted_cid_nl_backfill_trace_batch",
    "backfill_untargeted_cid_nl_sample_subject",
]
