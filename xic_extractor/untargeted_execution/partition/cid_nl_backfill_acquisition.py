"""Acquire and assess group-directed Untargeted CID-NL backfill observations."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from xic_extractor.acquisition_contract import (
    NATIVE_SCAN_COORDINATE_KIND,
    AcquiredTrace,
    TraceAcquisitionBatch,
    acquire_trace_batch,
    mint_trace_acquisition_capability,
)
from xic_extractor.alignment.backfill.cid_nl_planning import CidNlBackfillQuestion
from xic_extractor.alignment.cid_nl_inputs import UntargetedCidNlBackfillInput
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.event_evidence import resolve_discovery_scan_events
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.peak_detection.cid_nl_candidate_set_relation import (
    DdaCidNlCandidateSetRelation,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceObservation,
    cid_nl_evidence_observation,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodAssessment,
    CidNlMethodAssessmentContractError,
    is_cid_nl_missing_ms1_rejection,
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
from xic_extractor.peak_detection.detector_invocations import GROUP_BACKFILL_DETECTOR
from xic_extractor.peak_detection.observations import (
    PeakObservation,
    mint_physical_peak_observations,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    answer_physical_peak_question,
    evaluate_physical_peak,
    mint_physical_peak_question,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PHYSICAL_PEAK_BACKFILL_POLICY,
    PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY,
    PhysicalPeakEvaluation,
    PreparedSignalResult,
)
from xic_extractor.peak_detection.traces import Trace, trace_from_xic_request
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.raw_superwindow_source import RawSuperWindowSource
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.partition.cid_nl_raw_source import (
    _ADAPTER_NAMESPACE,
    CidNlAcquisitionError,
    CidNlRawSource,
    _freeze_acquisition,
    _within_ppm,
    read_direct_cid_nl_seeds,
)
from xic_extractor.xic_models import XICRequest

if TYPE_CHECKING:
    from xic_extractor.untargeted_execution.partition.cid_nl_sample_acquisition import (
        CidNlAcquisitionResult,
    )


@dataclass(frozen=True, slots=True)
class UntargetedCidNlBackfillAttemptResult:
    assessment: CidNlMethodAssessment
    backfill_input: UntargetedCidNlBackfillInput | None
    acquisition: TraceAcquisitionBatch | None
    group_hypothesis_id: str
    query_rt: float | None = None
    query_rt_basis: str = "group_center"


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
        localized = question.localization
        target = cls(
            group_hypothesis_id=group.group_hypothesis_id,
            method_subject=question.method_subject,
            physical_mz=group.group_center_mz,
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
        _validate_backfill_target(target)
        return target

    @property
    def sample_query_rt(self) -> float:
        return self.center_rt if self.query_rt is None else self.query_rt


def _validate_backfill_target(model: UntargetedCidNlBackfillTarget) -> None:
    if (
        not math.isfinite(model.physical_mz)
        or model.physical_mz <= 0.0
        or not math.isfinite(model.center_rt)
        or model.center_rt < 0.0
        or not math.isfinite(model.sample_query_rt)
        or model.sample_query_rt < 0.0
        or (
            model.query_rt_tolerance_sec is not None
            and (
                not math.isfinite(model.query_rt_tolerance_sec)
                or model.query_rt_tolerance_sec <= 0.0
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
    context_half_window = half_window * (2.0 if physical_context else 1.0)
    return XICRequest(
        mz=mz,
        rt_min=max(0.0, float(query_rt) - context_half_window),
        rt_max=float(query_rt) + context_half_window,
        ppm_tol=ppm_tol,
    )


def _backfill_policy(location_reliable: bool) -> str:
    return (
        PHYSICAL_PEAK_BACKFILL_POLICY
        if location_reliable
        else PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY
    )


def _backfill_window(
    query: float, half_window: float, tolerance_sec: float | None = None
) -> tuple[float, float]:
    radius = (
        half_window if tolerance_sec is None else min(half_window, tolerance_sec / 60)
    )
    return max(0.0, query - radius), query + radius


def physical_backfill_observation(
    *,
    sample: SampleMetadata,
    trace: Trace,
    query_rt_min: float,
    profile: SignalProcessingProfile,
    search_rt_window: tuple[float, float] | None = None,
    location_reliable: bool = True,
    evaluation_sink: Callable[[PhysicalPeakEvaluation], None] | None = None,
) -> PeakObservation | None:
    """Measure one backfill trace; an available selected peak is observed.

    Overlap review travels with the observation instead of withholding it.
    """
    evaluation = evaluate_physical_peak(
        prepare_physical_signal(trace.rt, trace.intensity, profile=profile),
        query_rt_min=query_rt_min,
        request_context_rt_start_min=trace.rt_min,
        request_context_rt_end_min=trace.rt_max,
        selection_policy_version=_backfill_policy(location_reliable),
        search_rt_window=search_rt_window,
    )
    if evaluation_sink is not None:
        evaluation_sink(evaluation)
    if evaluation.state != "available":
        return None
    return mint_physical_peak_observations(
        sample=sample,
        trace=trace,
        evaluations=(evaluation,),
        signal_processing_profile=profile,
        capture_stage="method_backfill",
    )[0]


def acquire_untargeted_cid_nl_backfill_trace_batch(
    *,
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    backfill_requests: tuple[UntargetedCidNlBackfillTarget, ...],
    half_window_min: float,
    raw: CidNlRawSource,
) -> UntargetedCidNlBackfillTraceBatch:
    """Acquire search traces and bounded fallback context in one sample phase."""

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
    requests = tuple(
        _backfill_xic_request(
            mz=target.physical_mz,
            query_rt=target.sample_query_rt,
            half_window=half_window_min,
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
            half_window=half_window_min,
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
    )
    acquisition = _freeze_acquisition(
        acquire_trace_batch(capability, tuple(unique), RawSuperWindowSource(raw))
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
    sample: SampleMetadata,
    acquisition_source: AcquisitionSourceIdentity,
    method_subject: CidNlMethodSubject,
    group_center_rt: float,
    group_center_mz: float,
    query_rt: float | None = None,
    query_rt_basis: str = "group_center",
    query_rt_tolerance_sec: float | None = None,
    query_rt_reliable: bool = True,
    group_hypothesis_id: str,
    support_policy: DdaCidNlPeakSupportPolicy,
    half_window_min: float,
    raw: CidNlRawSource | None,
    initial_assessment: CidNlMethodAssessment | None = None,
    settings: DiscoverySettings | None = None,
    signal_processing_profile: SignalProcessingProfile,
    _preacquired_traces: UntargetedCidNlBackfillTraceBatch | None = None,
) -> UntargetedCidNlBackfillAttemptResult:
    """Evaluate one owner-authorized bounded physical candidate universe.

    MS1-only backfill remains HOLD. A missing-MS1 rejection may contribute its
    immutable measured CID-NL evidence to new owner-resolved relations.
    """

    if initial_assessment is not None and not is_cid_nl_missing_ms1_rejection(
        initial_assessment
    ):
        raise CidNlAcquisitionError("backfill requires the exact missing-MS1 rejection")
    if not math.isfinite(group_center_rt) or group_center_rt < 0.0:
        raise CidNlAcquisitionError("group_center_rt must be finite and non-negative")
    query_rt = group_center_rt if query_rt is None else query_rt
    if not math.isfinite(query_rt) or query_rt < 0.0:
        raise CidNlAcquisitionError("query RT requires a finite non-negative value")
    physical_mz = group_center_mz
    if not math.isfinite(physical_mz) or physical_mz <= 0.0:
        raise CidNlAcquisitionError("group_center_mz must be finite and positive")
    source = acquisition_source
    query_rt_min, query_rt_max = _backfill_window(query_rt, half_window_min)
    selection_window = (
        _backfill_window(query_rt, half_window_min, query_rt_tolerance_sec)
        if query_rt_tolerance_sec is not None
        else None
    )
    request = _backfill_xic_request(
        mz=physical_mz,
        query_rt=float(query_rt),
        half_window=half_window_min,
        ppm_tol=method_subject.precursor_tolerance_ppm,
        physical_context=False,
    )
    context_request = _backfill_xic_request(
        mz=physical_mz,
        query_rt=float(query_rt),
        half_window=half_window_min,
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
        )
        acquisition = _freeze_acquisition(
            acquire_trace_batch(
                capability, (request, context_request), RawSuperWindowSource(raw)
            )
        )
        acquired = acquisition.traces[0]
        context_acquired = acquisition.traces[1]
    else:
        acquisition = _preacquired_traces.acquisition
        acquired = _preacquired_traces.acquired_trace(
            group_hypothesis_id=group_hypothesis_id,
            request=request,
        )
        context_acquired = _preacquired_traces.acquired_trace(
            group_hypothesis_id=group_hypothesis_id,
            request=context_request,
        )
    observations: tuple[PeakObservation, ...] = ()
    physical_evaluations: list[PhysicalPeakEvaluation] = []
    for capture_request, capture_acquired in (
        (request, acquired),
        (context_request, context_acquired),
    ):
        trace = trace_from_xic_request(
            sample_name=sample.sample_name,
            request=capture_request,
            xic_trace=capture_acquired.trace,
            source=f"{_ADAPTER_NAMESPACE}.backfill:{NATIVE_SCAN_COORDINATE_KIND}",
            acquisition_source_id=source.acquisition_source_id,
        )
        physical_evaluations.clear()
        observation = physical_backfill_observation(
            sample=sample,
            trace=trace,
            query_rt_min=float(query_rt),
            location_reliable=query_rt_reliable,
            search_rt_window=selection_window
            or (
                (query_rt_min, query_rt_max)
                if capture_request is context_request
                else None
            ),
            profile=signal_processing_profile,
            evaluation_sink=physical_evaluations.append,
        )
        if physical_evaluations[0].reason != "incomplete_peak_context":
            break
    observations = (observation,) if observation is not None else ()
    backfill_evidence: tuple[CidNlEvidenceObservation, ...] = ()
    backfill_support: tuple[DdaCidNlPeakSupport, ...] = ()
    backfill_sets: tuple[DdaCidNlCandidateSetRelation, ...] = ()
    if initial_assessment is not None and observations:
        backfill_evidence = tuple(
            ref
            for ref in initial_assessment.evidence_observations
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
        backfill_evidence, backfill_support, backfill_sets = (
            _composite_backfill_evidence(
                sample=sample,
                acquisition_source=source,
                method_subject=method_subject,
                raw=cast(CidNlRawSource, raw),
                settings=settings,
                query_rt_min=query_rt_min,
                query_rt_max=query_rt_max,
                observations=observations,
                support_policy=support_policy,
            )
        )
    return _close_backfill_attempt(
        sample=sample,
        source=source,
        method_subject=method_subject,
        group_hypothesis_id=group_hypothesis_id,
        query_rt=query_rt,
        query_rt_basis=query_rt_basis,
        physical_mz=request.mz,
        query_rt_min=query_rt_min,
        query_rt_max=query_rt_max,
        support_policy=support_policy,
        initial_receipt=initial_assessment.evaluation_receipt
        if initial_assessment is not None
        else None,
        observations=observations,
        backfill_evidence=backfill_evidence,
        backfill_support=backfill_support,
        backfill_sets=backfill_sets,
        acquisition=acquisition,
    )


def _close_backfill_attempt(
    *,
    sample: SampleMetadata,
    source: AcquisitionSourceIdentity,
    method_subject: CidNlMethodSubject,
    group_hypothesis_id: str,
    query_rt: float,
    query_rt_basis: str,
    physical_mz: float,
    query_rt_min: float,
    query_rt_max: float,
    support_policy: DdaCidNlPeakSupportPolicy,
    initial_receipt: CidNlEvaluationReceipt | None,
    observations: tuple[PeakObservation, ...],
    backfill_evidence: tuple[CidNlEvidenceObservation, ...],
    backfill_support: tuple[DdaCidNlPeakSupport, ...],
    backfill_sets: tuple[DdaCidNlCandidateSetRelation, ...],
    acquisition: TraceAcquisitionBatch | None,
) -> UntargetedCidNlBackfillAttemptResult:
    try:
        assessment = mint_cid_nl_method_assessment(
            sample=sample,
            acquisition_source=source,
            method_subject=method_subject,
            detector_invocation_id=GROUP_BACKFILL_DETECTOR,
            evaluation_receipt=CidNlEvaluationReceipt(
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
                    initial_receipt.evidence_scope
                    if initial_receipt is not None and backfill_evidence
                    else "subject_window"
                ),
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
        if observations and assessment.method_result == "HOLD"
        else None
    )
    return UntargetedCidNlBackfillAttemptResult(
        assessment=assessment,
        backfill_input=backfill_input,
        acquisition=acquisition,
        group_hypothesis_id=group_hypothesis_id,
        query_rt=float(query_rt),
        query_rt_basis=query_rt_basis,
    )


def reuse_cid_nl_backfill_peak(
    *,
    target: UntargetedCidNlBackfillTarget,
    capture: CidNlAcquisitionResult,
    source: AcquisitionSourceIdentity,
    half_window_min: float,
) -> UntargetedCidNlBackfillAttemptResult | None:
    """Ask the group's physical question of measured segments, without new RAW reads.

    The initial method assessment (including all declared NL events) stays intact.
    A set-level or incomplete composite relation is not missing Direct support.
    Never create a new segment by narrowing an ambiguous chemical universe.
    """
    initial = capture.assessment_input.assessment
    if initial.reason_codes != ("no_unique_qualifying_relation",):
        return None
    sample = capture.assessment_input.sample
    query = target.sample_query_rt
    window = _backfill_window(query, half_window_min, target.query_rt_tolerance_sec)
    matches = {}
    for record in capture.capture_evidence:
        peak = record.observation_ref
        if (
            peak is None
            or peak not in initial.candidate_peaks
            or not within_ppm(
                peak.mz,
                target.physical_mz,
                target.method_subject.precursor_tolerance_ppm,
            )
        ):
            continue
        ev = record.physical_evaluation
        if ev is None or ev.retained_prepared_signal is None:
            continue
        question = mint_physical_peak_question(
            ev.prepared,
            ev.segment_universe,
            query_rt_min=query,
            selection_policy_version=_backfill_policy(target.query_rt_reliable),
            search_rt_window=window,
        )
        answer = answer_physical_peak_question(
            PreparedSignalResult(ev.prepared, ev.retained_prepared_signal),
            ev.segment_universe,
            question,
        )
        if (
            answer.state == "available"
            and answer.answer.selected_segment == ev.answer.selected_segment
        ):
            matches[peak.physical_identity] = (record, answer)
    if len(matches) != 1:
        return None
    record, answer = next(iter(matches.values()))
    initial_peak = record.observation_ref
    assert initial_peak is not None
    (observed,) = mint_physical_peak_observations(
        sample=sample,
        trace=record.trace,
        evaluations=(answer,),
        signal_processing_profile=answer.prepared.profile,
        measured_mz=(initial_peak.mz,),
        capture_stage="method_backfill",
    )
    return _close_backfill_attempt(
        sample=sample,
        source=source,
        method_subject=target.method_subject,
        group_hypothesis_id=target.group_hypothesis_id,
        query_rt=query,
        query_rt_basis=target.query_rt_basis,
        physical_mz=target.physical_mz,
        query_rt_min=window[0],
        query_rt_max=window[1],
        support_policy=initial.evaluation_receipt.support_policy,
        initial_receipt=None,
        observations=(observed,),
        backfill_evidence=(),
        backfill_support=(),
        backfill_sets=(),
        acquisition=None,
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
    observations: tuple[PeakObservation, ...],
    support_policy: DdaCidNlPeakSupportPolicy,
) -> tuple[
    tuple[CidNlEvidenceObservation, ...],
    tuple[DdaCidNlPeakSupport, ...],
    tuple[DdaCidNlCandidateSetRelation, ...],
]:
    """Evaluate the configured loss set on the backfilled physical universe."""
    evidence_refs: list[CidNlEvidenceObservation] = []
    individual_refs: list[DdaCidNlPeakSupport] = []
    set_refs: list[DdaCidNlCandidateSetRelation] = []
    subject_seeds = tuple(
        seed
        for seed in read_direct_cid_nl_seeds(
            raw,
            raw_file=Path(sample.raw_stem),
            settings=settings,
            rt_min=query_rt_min,
            rt_max=query_rt_max,
        )
        if _within_ppm(
            seed.precursor_mz,
            method_subject.precursor_mz,
            method_subject.precursor_tolerance_ppm,
        )
    )
    for seed in resolve_discovery_scan_events(
        subject_seeds,
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
    "physical_backfill_observation",
]
