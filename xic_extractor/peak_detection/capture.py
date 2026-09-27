from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import cast

import numpy as np

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.baseline import BaselineMethod, asls_baseline
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    cid_nl_method_subject_from_request,
    validate_cid_nl_method_subject_binding,
)
from xic_extractor.peak_detection.facade import find_peak_and_area
from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    PeakHypothesis,
    build_peak_hypotheses,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.ms1_morphology import (
    gaussian15_positive_asls_residual_trace,
)
from xic_extractor.peak_detection.ms1_trace_detection import (
    Ms1TracePeakDetection,
    detect_ms1_trace_peak,
)
from xic_extractor.peak_detection.observations import (
    PeakObservationContent,
    PeakObservationContractError,
    PeakObservationGenerationProvenance,
    PeakObservationRef,
    _mint_peak_observation_artifact,
    _project_peak_observation_values,
    _validate_peak_observation_inputs,
    mint_peak_observation_ref,
    physical_peak_observation_integration,
    validate_peak_observation_trace_binding,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_identity import (
    mint_physical_trace_content_id,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
    PhysicalPeakEvaluation,
    PreparedSignalResult,
)
from xic_extractor.peak_detection.search_scope import (
    CaptureCandidateBinding,
    SearchScope,
    SearchScopeContractError,
    TargetSearchScope,
    UntargetedSearchScope,
    request_binds_physical_capture_profile,
    require_capture_binding,
    validate_scope_owner_binding,
    validate_scope_trace_coordinates,
)
from xic_extractor.peak_detection.traces import Trace, TraceGroup
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class SampleLocalCaptureError(ValueError):
    """Raised when shared sample-local capture cannot close one candidate."""


@dataclass(frozen=True, slots=True)
class _CaptureOwnerSeal:
    scope: SearchScope
    candidate_id: str
    trace: Trace
    trace_group: TraceGroup
    detection: Ms1TracePeakDetection
    hypotheses: tuple[PeakHypothesis, ...]
    receipt_hypothesis: PeakHypothesis | None
    selected_hypothesis: PeakHypothesis | None
    physical_evaluation: PhysicalPeakEvaluation | None = None
    physical_selection_intensity: tuple[float, ...] | None = None
    physical_observation_content: PeakObservationContent | None = None
    physical_observation_provenance: PeakObservationGenerationProvenance | None = None


@dataclass(frozen=True, slots=True)
class SampleLocalCapture:
    _owner_seal: _CaptureOwnerSeal = field(repr=False, compare=False)
    scope: SearchScope
    candidate_id: str
    trace: Trace
    trace_group: TraceGroup
    detection: Ms1TracePeakDetection
    hypotheses: tuple[PeakHypothesis, ...]
    receipt_hypothesis: PeakHypothesis | None
    selected_hypothesis: PeakHypothesis | None
    physical_evaluation: PhysicalPeakEvaluation | None = None

    def __post_init__(self) -> None:
        seal = self._owner_seal
        if (
            not isinstance(seal, _CaptureOwnerSeal)
            or seal.scope is not self.scope
            or seal.candidate_id != self.candidate_id
            or seal.trace is not self.trace
            or seal.trace_group is not self.trace_group
            or seal.detection is not self.detection
            or seal.hypotheses is not self.hypotheses
            or seal.receipt_hypothesis is not self.receipt_hypothesis
            or seal.selected_hypothesis is not self.selected_hypothesis
            or seal.physical_evaluation is not self.physical_evaluation
        ):
            raise SampleLocalCaptureError(
                "SampleLocalCapture must be issued by the shared capture owner"
            )


def capture_peak_hypotheses(
    *,
    scope: SearchScope,
    candidate_id: str,
    trace: Trace,
) -> SampleLocalCapture:
    binding = require_capture_binding(scope, candidate_id)
    trace_group = _capture_trace_group(scope=scope, binding=binding, trace=trace)
    if scope.method_constraints.signal_processing_profile is not None:
        return _capture_physical_peak(
            scope=scope,
            binding=binding,
            trace=trace,
            trace_group=trace_group,
        )
    candidate = binding.candidate_id
    peak_config = scope.method_constraints.peak_config
    label, role, istd_pair, common_evidence_source_id = _hypothesis_context(
        scope, query_mz=binding.query_mz, method_subject_id=binding.method_subject_id
    )
    if (
        binding.selection_policy == "retained_only"
        and binding.retained_selected_integration is None
    ):
        detection = Ms1TracePeakDetection(
            status="not_detected", rt=trace.rt, intensity=trace.intensity
        )
    else:
        detection = detect_ms1_trace_peak(
            trace.rt,
            trace.intensity,
            peak_config=peak_config,
            preferred_rt=binding.preferred_rt,
            peak_finder=find_peak_and_area,
        )
    hypotheses: tuple[PeakHypothesis, ...] = ()
    receipt_hypothesis: PeakHypothesis | None = None
    selected: PeakHypothesis | None = None
    if detection.status == "detected" and detection.result is not None:
        hypotheses = build_peak_hypotheses(
            analysis_mode=scope.variant,
            common_evidence_source_id=common_evidence_source_id,
            sample_name=trace.sample_name,
            target_label=label,
            role=role,
            istd_pair=istd_pair,
            resolver_mode=peak_config.resolver_mode,
            peak_result=detection.result,
            trace_group=trace_group,
            baseline_integration_method=cast(
                BaselineMethod,
                peak_config.baseline_integration_method,
            ),
            ms1_morphology_smoothing_window_points=(
                peak_config.ms1_morphology_smoothing_window_points
            ),
        )
        selected_values = tuple(item for item in hypotheses if item.audit.selected)
        if len(selected_values) > 1:
            raise SampleLocalCaptureError(
                "shared hypothesis owner selected more than one candidate"
            )
        receipt_hypothesis = selected_values[0] if selected_values else None
        if binding.retained_selected_integration is None:
            selected = receipt_hypothesis
        else:
            hypotheses, selected = _bind_retained_selection(
                hypotheses,
                binding.retained_selected_integration,
            )
    owner_seal = _CaptureOwnerSeal(
        scope=scope,
        candidate_id=candidate,
        trace=trace,
        trace_group=trace_group,
        detection=detection,
        hypotheses=hypotheses,
        receipt_hypothesis=receipt_hypothesis,
        selected_hypothesis=selected,
    )
    return SampleLocalCapture(
        _owner_seal=owner_seal,
        scope=scope,
        candidate_id=candidate,
        trace=trace,
        trace_group=trace_group,
        detection=detection,
        hypotheses=hypotheses,
        receipt_hypothesis=receipt_hypothesis,
        selected_hypothesis=selected,
    )


def rebind_untargeted_capture_scope(
    capture: SampleLocalCapture, *, scope: UntargetedSearchScope
) -> SampleLocalCapture:
    """Reuse a completed capture in an equivalent sample or workset view."""
    if (
        not isinstance(capture, SampleLocalCapture)
        or not isinstance(capture.scope, UntargetedSearchScope)
        or not isinstance(scope, UntargetedSearchScope)
    ):
        raise SampleLocalCaptureError("scope rebinding requires Untargeted capture")
    if (
        scope.request != capture.scope.request
        or scope.method_constraints != capture.scope.method_constraints
        or require_capture_binding(scope, capture.candidate_id)
        != require_capture_binding(capture.scope, capture.candidate_id)
    ):
        raise SampleLocalCaptureError("scope rebinding changed the capture question")
    return replace(
        capture,
        scope=scope,
        _owner_seal=replace(capture._owner_seal, scope=scope),
    )


def _capture_trace_group(
    *, scope: SearchScope, binding: CaptureCandidateBinding, trace: Trace
) -> TraceGroup:
    if not isinstance(trace, Trace):
        raise SampleLocalCaptureError("capture trace must be an owner trace")
    try:
        validate_scope_trace_coordinates(
            scope,
            binding=binding,
            sample_name=trace.sample_name,
            acquisition_source_id=trace.acquisition_source_id,
            mz=trace.mz,
            rt_min=trace.rt_min,
            rt_max=trace.rt_max,
            ppm_tol=trace.ppm_tol,
        )
    except SearchScopeContractError as exc:
        raise SampleLocalCaptureError(str(exc)) from exc
    label, role, istd_pair, _ = _hypothesis_context(
        scope,
        query_mz=binding.query_mz,
        method_subject_id=binding.method_subject_id,
    )
    return TraceGroup(
        trace_group_id="|".join(
            (
                scope.request.processing_request_id,
                trace.sample_name,
                binding.candidate_id,
            )
        ),
        sample_name=trace.sample_name,
        analysis_mode=scope.variant,
        context_id=label,
        traces=(trace,),
        expected_rt_min=binding.preferred_rt,
        precursor_mz=trace.mz,
        role=role,
        istd_pair=istd_pair,
    )


def _capture_physical_peak(
    *,
    scope: SearchScope,
    binding: CaptureCandidateBinding,
    trace: Trace,
    trace_group: TraceGroup,
) -> SampleLocalCapture:
    profile = scope.method_constraints.signal_processing_profile
    expected = binding.physical_evaluation
    if profile is None or expected is None:
        raise SampleLocalCaptureError(
            "physical capture requires its closed owner question"
        )
    if mint_physical_trace_content_id(trace.rt, trace.intensity) != (
        expected.prepared.trace_content_id
    ):
        raise SampleLocalCaptureError(
            "physical capture trace_content_id differs from the retained owner trace"
        )
    retained = expected.retained_prepared_signal
    if expected.state == "available" and (
        retained is None or retained.projection != expected.prepared
    ):
        raise SampleLocalCaptureError(
            "physical capture lost the retained prepared signal"
        )
    prepared = PreparedSignalResult(
        projection=expected.prepared,
        prepared=retained,
        operations=expected.operations,
        trace_context_rt_start_min=expected.question.context_rt_start_min,
        trace_context_rt_end_min=expected.question.context_rt_end_min,
    )
    return _project_physical_capture(
        scope=scope,
        binding=binding,
        trace=trace,
        trace_group=trace_group,
        prepared=prepared,
        evaluation=expected,
    )


def capture_reacquired_physical_peak(
    *, scope: SearchScope, candidate_id: str, trace: Trace
) -> SampleLocalCapture:
    """Evaluate a changed acquisition question at the capture owner once."""
    binding = require_capture_binding(scope, candidate_id)
    profile = scope.method_constraints.signal_processing_profile
    source_evaluation = binding.physical_evaluation
    if profile is None or source_evaluation is None:
        raise SampleLocalCaptureError(
            "physical capture requires its closed owner question"
        )
    if not isinstance(trace, Trace):
        raise SampleLocalCaptureError("capture trace must be an owner trace")
    if (
        trace.mz == binding.query_mz
        and trace.rt_min == binding.query_rt_min
        and trace.rt_max == binding.query_rt_max
        and trace.ppm_tol == binding.query_ppm_tol
    ):
        return capture_peak_hypotheses(
            scope=scope, candidate_id=candidate_id, trace=trace
        )
    binding = replace(
        binding,
        query_mz=trace.mz,
        query_rt_min=trace.rt_min,
        query_rt_max=trace.rt_max,
        query_ppm_tol=trace.ppm_tol,
    )
    scope = replace(
        scope,
        capture_bindings=tuple(
            binding if item.candidate_id == candidate_id else item
            for item in scope.capture_bindings
        ),
    )
    trace_group = _capture_trace_group(scope=scope, binding=binding, trace=trace)
    prepared = prepare_physical_signal(trace.rt, trace.intensity, profile=profile)
    evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id=source_evaluation.question.selection_subject_id,
        query_rt_min=binding.preferred_rt,
        request_context_rt_start_min=binding.query_rt_min,
        request_context_rt_end_min=binding.query_rt_max,
    )
    binding = replace(
        binding,
        source_physical_evaluation=source_evaluation,
        physical_evaluation=evaluation,
    )
    scope = replace(
        scope,
        capture_bindings=tuple(
            binding if item.candidate_id == candidate_id else item
            for item in scope.capture_bindings
        ),
    )
    return _project_physical_capture(
        scope=scope,
        binding=binding,
        trace=trace,
        trace_group=trace_group,
        prepared=prepared,
        evaluation=evaluation,
    )


def _project_physical_capture(
    *,
    scope: SearchScope,
    binding: CaptureCandidateBinding,
    trace: Trace,
    trace_group: TraceGroup,
    prepared: PreparedSignalResult,
    evaluation: PhysicalPeakEvaluation,
) -> SampleLocalCapture:
    selected: PeakHypothesis | None = None
    selection_intensity: tuple[float, ...] | None = None
    observation_content: PeakObservationContent | None = None
    observation_provenance: PeakObservationGenerationProvenance | None = None
    if evaluation.state == "available":
        facts = evaluation.facts
        integration = evaluation.integration
        if facts is None or integration is None or prepared.prepared is None:
            raise SampleLocalCaptureError("available physical capture lost owner facts")
        selection_intensity = prepared.prepared.positive_signal
        selected = PeakHypothesis(
            hypothesis_id=f"physical-capture:{facts.fact_content_id}",
            trace_group_id=trace_group.trace_group_id,
            target_label=trace_group.context_id,
            role=trace_group.role,
            istd_pair=trace_group.istd_pair,
            analysis_mode=scope.variant,
            resolver_mode=scope.method_constraints.peak_config.resolver_mode,
            integration=physical_peak_observation_integration(evaluation),
            evidence=EvidenceVector(),
            audit=AuditTrail(
                proposal_sources=("canonical_physical_segment",),
                selected=True,
                selection_reference_rt_min=binding.preferred_rt,
            ),
        )
        observation_content, observation_provenance = _project_peak_observation_values(
            canonical_sample_identity_id=binding.sample_identity_id,
            trace=trace,
            integration=selected.integration,
            selection_values=np.asarray(selection_intensity, dtype=float),
            selected_indices=selected.integration.raw_scan_indices,
            resolver_mode=scope.method_constraints.peak_config.resolver_mode,
            proposal_sources=selected.audit.proposal_sources,
        )
    detection = Ms1TracePeakDetection(
        status="detected" if selected is not None else "not_detected",
        rt=trace.rt,
        intensity=trace.intensity,
    )
    hypotheses = (selected,) if selected is not None else ()
    seal = _CaptureOwnerSeal(
        scope=scope,
        candidate_id=binding.candidate_id,
        trace=trace,
        trace_group=trace_group,
        detection=detection,
        hypotheses=hypotheses,
        receipt_hypothesis=selected,
        selected_hypothesis=selected,
        physical_evaluation=evaluation,
        physical_selection_intensity=selection_intensity,
        physical_observation_content=observation_content,
        physical_observation_provenance=observation_provenance,
    )
    return SampleLocalCapture(
        _owner_seal=seal,
        scope=scope,
        candidate_id=binding.candidate_id,
        trace=trace,
        trace_group=trace_group,
        detection=detection,
        hypotheses=hypotheses,
        receipt_hypothesis=selected,
        selected_hypothesis=selected,
        physical_evaluation=evaluation,
    )


def _bind_retained_selection(
    hypotheses: tuple[PeakHypothesis, ...],
    retained: IntegrationResult,
) -> tuple[tuple[PeakHypothesis, ...], PeakHypothesis | None]:
    if type(retained) is not IntegrationResult:
        raise SampleLocalCaptureError(
            "retained selection must be one exact IntegrationResult"
        )
    matches = tuple(item for item in hypotheses if item.integration == retained)
    if not matches:
        matches = tuple(
            item
            for item in hypotheses
            if _same_physical_peak(item.integration, retained)
        )
    if len(matches) != 1:
        raise SampleLocalCaptureError(
            "retained selected hypothesis is absent from the captured trace"
        )
    matched = matches[0]
    selected_hypothesis: PeakHypothesis | None = None
    rebound = []
    for item in hypotheses:
        selected_item = item is matched
        updated = replace(item, audit=replace(item.audit, selected=selected_item))
        rebound.append(updated)
        if selected_item:
            selected_hypothesis = updated
    return tuple(rebound), selected_hypothesis


def capture_physical_backfill_observation(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    method_subject: CidNlMethodSubject,
    trace: Trace,
    group_center_rt: float,
    profile: SignalProcessingProfile,
    resolver_mode: str,
    evaluation_sink: Callable[[PhysicalPeakEvaluation], None] | None = None,
) -> PeakObservationRef | None:
    """Capture a backfill question using its request-bound method and profile."""
    validate_cid_nl_method_subject_binding(
        method_subject, processing_owners=processing_owners
    )
    if (
        not request_binds_physical_capture_profile(processing_owners.request, profile)
        or trace.mz != method_subject.precursor_mz
        or trace.ppm_tol != method_subject.precursor_tolerance_ppm
        or not trace.rt_min <= group_center_rt <= trace.rt_max
    ):
        raise SampleLocalCaptureError("physical backfill question is not request-bound")
    prepared = prepare_physical_signal(trace.rt, trace.intensity, profile=profile)
    evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id=method_subject.method_subject_id,
        query_rt_min=group_center_rt,
        request_context_rt_start_min=trace.rt_min,
        request_context_rt_end_min=trace.rt_max,
        selection_policy_version=PHYSICAL_PEAK_GROUP_WINDOW_POLICY,
    )
    if evaluation_sink is not None:
        evaluation_sink(evaluation)
    if evaluation.state != "available":
        return None
    assert prepared.prepared is not None
    return mint_peak_observation_ref(
        processing_owners=processing_owners,
        sample=sample,
        trace=trace,
        integration=physical_peak_observation_integration(evaluation),
        selection_intensity=prepared.prepared.positive_signal,
        resolver_mode=resolver_mode,
        proposal_sources=("canonical_physical_segment",),
        physical_evaluation=evaluation,
        signal_processing_profile=profile,
        capture_stage="method_backfill",
    )


def _same_physical_peak(
    current: IntegrationResult,
    retained: IntegrationResult,
) -> bool:
    if (
        current.height_raw == retained.height_raw
        and current.rt_apex_min == retained.rt_apex_min
        and current.raw_apex_rt_min == retained.raw_apex_rt_min
    ):
        return True
    if (
        current.height_raw != retained.height_raw
        and retained.integration_method
        not in {
            "selected_envelope_gaussian15",
            "chrom_peak_segment_gaussian15",
        }
    ):
        return False
    return (
        bool(set(current.raw_scan_indices) & set(retained.raw_scan_indices))
        and current.rt_left_min <= retained.rt_apex_min <= current.rt_right_min
        and retained.rt_left_min <= current.rt_apex_min <= retained.rt_right_min
    )


def mint_captured_peak_observation(
    capture: SampleLocalCapture,
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> PeakObservationRef:
    if not isinstance(capture, SampleLocalCapture):
        raise SampleLocalCaptureError("observation requires SampleLocalCapture")
    try:
        validate_scope_owner_binding(
            capture.scope,
            processing_owners=processing_owners,
            sample=sample,
        )
    except SearchScopeContractError as exc:
        raise SampleLocalCaptureError(str(exc)) from exc
    selected = capture.selected_hypothesis
    if selected is None or not isinstance(selected, PeakHypothesis):
        raise SampleLocalCaptureError(
            "observation requires one selected PeakHypothesis"
        )
    if (
        selected.trace_group_id != capture.trace_group.trace_group_id
        or selected.analysis_mode != capture.scope.variant
        or not selected.audit.selected
    ):
        raise SampleLocalCaptureError(
            "observation hypothesis bypasses the captured selection identity"
        )
    if capture.physical_evaluation is not None:
        _validate_peak_observation_inputs(
            processing_owners=processing_owners,
            sample=sample,
            trace=capture.trace,
            integration=selected.integration,
        )
        canonical_sample_identity_id, content, provenance = (
            _project_captured_physical_observation(capture, selected)
        )
        if canonical_sample_identity_id != sample.identity.sample_identity_id:
            raise PeakObservationContractError(
                "capture observation references a different sample"
            )
        return _mint_peak_observation_artifact(
            processing_owners=processing_owners,
            content=content,
            generation_provenance=provenance,
            physical_evaluation=capture.physical_evaluation,
            capture_stage=(
                "reacquired_capture"
                if require_capture_binding(
                    capture.scope, capture.candidate_id
                ).source_physical_evaluation
                is not None
                else "initial_capture"
            ),
        )
    return mint_peak_observation_ref(
        processing_owners=processing_owners,
        sample=sample,
        trace=capture.trace,
        integration=selected.integration,
        selection_intensity=_captured_selection_intensity(capture),
        resolver_mode=capture.scope.method_constraints.peak_config.resolver_mode,
        proposal_sources=selected.audit.proposal_sources,
        physical_evaluation=capture.physical_evaluation,
        signal_processing_profile=capture.scope.method_constraints.signal_processing_profile,
    )


def _project_captured_physical_observation(
    capture: SampleLocalCapture,
    selected: PeakHypothesis,
) -> tuple[
    str,
    PeakObservationContent,
    PeakObservationGenerationProvenance,
]:
    """Reuse a capture-owner physical answer after rebinding its live trace."""

    binding = require_capture_binding(capture.scope, capture.candidate_id)
    capture.__post_init__()
    evaluation = capture.physical_evaluation
    if evaluation is None:
        raise PeakObservationContractError(
            "physical observation requires the capture owner answer"
        )
    if (
        capture.receipt_hypothesis is None
        or selected.integration != capture.receipt_hypothesis.integration
    ):
        raise PeakObservationContractError("capture selection changed")
    content = capture._owner_seal.physical_observation_content
    provenance = capture._owner_seal.physical_observation_provenance
    if content is None or provenance is None:
        raise PeakObservationContractError(
            "physical observation requires the capture owner projection"
        )
    return binding.sample_identity_id, content, provenance


def bind_owner_backed_capture_hypothesis(
    capture: SampleLocalCapture,
    *,
    observation: PeakObservationRef,
    hypothesis: PeakHypothesis,
) -> SampleLocalCapture:
    """Bind the exact runtime-owned hypothesis to its already captured signal."""

    if not isinstance(capture, SampleLocalCapture):
        raise SampleLocalCaptureError("owner binding requires SampleLocalCapture")
    if (
        not isinstance(observation, PeakObservationRef)
        or type(hypothesis) is not PeakHypothesis
    ):
        raise SampleLocalCaptureError(
            "owner binding requires exact observation and hypothesis values"
        )
    selected = capture.selected_hypothesis
    if (
        selected is None
        or observation not in hypothesis.peak_observation_refs
        or hypothesis.integration != observation.content.integration
    ):
        raise SampleLocalCaptureError(
            "owner hypothesis does not bind the captured physical selection"
        )
    try:
        if capture.physical_evaluation is None:
            binding = require_capture_binding(capture.scope, capture.candidate_id)
            validate_peak_observation_trace_binding(
                observation,
                canonical_sample_identity_id=binding.sample_identity_id,
                trace=capture.trace,
                integration=selected.integration,
                selection_intensity=_captured_selection_intensity(capture),
                resolver_mode=capture.scope.method_constraints.peak_config.resolver_mode,
                proposal_sources=selected.audit.proposal_sources,
                signal_processing_profile=capture.scope.method_constraints.signal_processing_profile,
            )
        else:
            _canonical_sample_identity_id, content, provenance = (
                _project_captured_physical_observation(capture, selected)
            )
            if (
                observation.content != content
                or observation.generation_provenance != provenance
            ):
                raise PeakObservationContractError("capture observation changed")
    except (PeakObservationContractError, SearchScopeContractError) as exc:
        raise SampleLocalCaptureError(
            "owner hypothesis does not bind the captured physical selection"
        ) from exc
    hypotheses = (hypothesis,)
    seal = _CaptureOwnerSeal(
        scope=capture.scope,
        candidate_id=capture.candidate_id,
        trace=capture.trace,
        trace_group=capture.trace_group,
        detection=capture.detection,
        hypotheses=hypotheses,
        receipt_hypothesis=capture.receipt_hypothesis,
        selected_hypothesis=hypothesis,
        physical_evaluation=capture.physical_evaluation,
        physical_selection_intensity=capture._owner_seal.physical_selection_intensity,
        physical_observation_content=(
            capture._owner_seal.physical_observation_content
        ),
        physical_observation_provenance=(
            capture._owner_seal.physical_observation_provenance
        ),
    )
    return SampleLocalCapture(
        _owner_seal=seal,
        scope=capture.scope,
        candidate_id=capture.candidate_id,
        trace=capture.trace,
        trace_group=capture.trace_group,
        detection=capture.detection,
        hypotheses=hypotheses,
        receipt_hypothesis=capture.receipt_hypothesis,
        selected_hypothesis=hypothesis,
        physical_evaluation=capture.physical_evaluation,
    )


def _captured_selection_intensity(capture: SampleLocalCapture) -> object | None:
    if capture.physical_evaluation is not None:
        return capture._owner_seal.physical_selection_intensity
    selected = capture.selected_hypothesis
    if selected is None or selected.integration.integration_method not in {
        "selected_envelope_gaussian15",
        "chrom_peak_segment_gaussian15",
    }:
        return None
    return gaussian15_positive_asls_residual_trace(
        capture.trace.intensity,
        asls_baseline(capture.trace.intensity),
        window_points=(
            capture.scope.method_constraints.peak_config.ms1_morphology_smoothing_window_points
        ),
    )


def _normalize_promoted_capture_hypothesis(
    hypothesis: PeakHypothesis,
    *,
    detection: Ms1TracePeakDetection,
    morphology_window_points: int,
) -> PeakHypothesis:
    integration = hypothesis.integration
    if integration.integration_method not in {
        "selected_envelope_gaussian15",
        "chrom_peak_segment_gaussian15",
    }:
        return hypothesis
    indices = integration.raw_scan_indices
    if not indices:
        return hypothesis
    left = min(indices)
    right = max(indices) + 1
    raw_apex_index = left + int(np.argmax(detection.intensity[left:right]))
    selection_trace = gaussian15_positive_asls_residual_trace(
        detection.intensity,
        asls_baseline(detection.intensity),
        window_points=morphology_window_points,
    )
    selection_apex_index = left + int(np.argmax(selection_trace[left:right]))
    return replace(
        hypothesis,
        integration=replace(
            integration,
            rt_apex_min=float(detection.rt[selection_apex_index]),
            raw_apex_rt_min=float(detection.rt[raw_apex_index]),
            height_raw=float(detection.intensity[raw_apex_index]),
            height_smoothed=float(selection_trace[selection_apex_index]),
        ),
    )


def _hypothesis_context(
    scope: SearchScope,
    *,
    query_mz: float,
    method_subject_id: str,
) -> tuple[str, str, str, str]:
    if isinstance(scope, TargetSearchScope):
        target = scope.target.definition.target
        return (
            target.label,
            "ISTD" if target.is_istd else "Analyte",
            target.istd_pair,
            target.label,
        )
    method_subject = cid_nl_method_subject_from_request(
        scope.request, method_subject_id=method_subject_id
    )
    label = f"untargeted:{query_mz:.8g}:{method_subject.label}"
    return label, "Analyte", "", label


__all__ = (
    "SampleLocalCapture",
    "SampleLocalCaptureError",
    "bind_owner_backed_capture_hypothesis",
    "capture_peak_hypotheses",
    "capture_physical_backfill_observation",
    "mint_captured_peak_observation",
    "rebind_untargeted_capture_scope",
)
