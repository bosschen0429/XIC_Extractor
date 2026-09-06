from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import cast

import numpy as np

from xic_extractor.peak_detection.baseline import BaselineMethod, asls_baseline
from xic_extractor.peak_detection.capture_boundary_policy import (
    promote_targeted_boundary_hypothesis,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    CidNlMethodSubjectContractError,
    cid_nl_method_subject_batch_from_request,
    cid_nl_method_subject_from_request,
    validate_cid_nl_method_subject_binding,
)
from xic_extractor.peak_detection.evidence_facts import (
    PhysicalPeakEvaluation,
    evaluate_physical_peak,
    prepare_physical_signal,
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
    PeakObservationContractError,
    PeakObservationRef,
    mint_peak_observation_ref,
    physical_peak_observation_integration,
    validate_peak_observation_trace_binding,
)
from xic_extractor.peak_detection.search_scope import (
    PHYSICAL_CAPTURE_INPUT_KEY,
    CaptureCandidateBinding,
    SearchScope,
    SearchScopeContractError,
    TargetSearchScope,
    require_capture_binding,
    validate_scope_owner_binding,
    validate_scope_trace_coordinates,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
    canonical_signal_processing_profile_bytes,
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
    candidate = binding.candidate_id
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
    peak_config = scope.method_constraints.peak_config
    label, role, istd_pair, common_evidence_source_id = _hypothesis_context(
        scope,
        query_mz=binding.query_mz,
        method_subject_id=binding.method_subject_id,
    )
    trace_group = TraceGroup(
        trace_group_id="|".join(
            (
                scope.request.processing_request_id,
                trace.sample_name,
                candidate,
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
    if scope.method_constraints.signal_processing_profile is not None:
        return _capture_physical_peak(
            scope=scope,
            binding=binding,
            trace=trace,
            trace_group=trace_group,
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
        if (
            selected is not None
            and (
                binding.retained_selected_integration is None
                or binding.retained_selected_integration.integration_method
                in (
                    "selected_envelope_gaussian15",
                    "chrom_peak_segment_gaussian15",
                )
            )
            and scope.method_constraints.boundary_policy == "targeted_gaussian15"
            and binding.selection_policy == "capture_default"
        ):
            selected = promote_targeted_boundary_hypothesis(
                selected,
                rt=detection.rt,
                intensity=detection.intensity,
                context_rt_start=float(detection.rt[0]),
                context_rt_end=float(detection.rt[-1]),
                morphology_window_points=(
                    peak_config.ms1_morphology_smoothing_window_points
                ),
            ).hypothesis
            selected = _normalize_promoted_capture_hypothesis(
                selected,
                detection=detection,
                morphology_window_points=(
                    peak_config.ms1_morphology_smoothing_window_points
                ),
            )
            hypotheses = tuple(
                selected if item.hypothesis_id == selected.hypothesis_id else item
                for item in hypotheses
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
    prepared = prepare_physical_signal(
        trace.rt,
        trace.intensity,
        profile=profile,
        trace_content_id=expected.prepared.trace_content_id,
    )
    evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id=expected.question.selection_subject_id,
        query_rt_min=binding.preferred_rt,
        request_context_rt_start_min=binding.query_rt_min,
        request_context_rt_end_min=binding.query_rt_max,
    )
    if evaluation != expected:
        raise SampleLocalCaptureError(
            "physical capture changed the retained owner answer"
        )
    selected: PeakHypothesis | None = None
    selection_intensity: tuple[float, ...] | None = None
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
                proposal_sources=("canonical_physical_segment_v1",),
                selected=True,
                selection_reference_rt_min=binding.preferred_rt,
            ),
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


def capture_physical_recovery_observation(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    method_subject: CidNlMethodSubject,
    trace: Trace,
    group_center_rt: float,
    profile: SignalProcessingProfile,
    resolver_mode: str,
) -> PeakObservationRef | None:
    """Capture a recovery question using its request-bound method and profile."""
    validate_cid_nl_method_subject_binding(
        method_subject, processing_owners=processing_owners
    )
    payload = json.loads(processing_owners.request.normalized_scientific_inputs_json)
    physical_inputs = payload.get(PHYSICAL_CAPTURE_INPUT_KEY)
    if (
        not isinstance(physical_inputs, dict)
        or physical_inputs.get("schema_version") != "physical_capture_inputs_v1"
        or physical_inputs.get("profile")
        != json.loads(canonical_signal_processing_profile_bytes(profile))
        or trace.mz != method_subject.precursor_mz
        or trace.ppm_tol != method_subject.precursor_tolerance_ppm
        or not trace.rt_min <= group_center_rt <= trace.rt_max
    ):
        raise SampleLocalCaptureError("physical recovery question is not request-bound")
    prepared = prepare_physical_signal(trace.rt, trace.intensity, profile=profile)
    evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id=method_subject.method_subject_id,
        query_rt_min=group_center_rt,
        request_context_rt_start_min=trace.rt_min,
        request_context_rt_end_min=trace.rt_max,
    )
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
        proposal_sources=("canonical_physical_segment_v1",),
        physical_evaluation=evaluation,
        signal_processing_profile=profile,
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
        binding = require_capture_binding(capture.scope, capture.candidate_id)
        validate_peak_observation_trace_binding(
            observation,
            canonical_sample_identity_id=binding.sample_identity_id,
            trace=capture.trace,
            integration=selected.integration,
            selection_intensity=_captured_selection_intensity(capture),
            resolver_mode=capture.scope.method_constraints.peak_config.resolver_mode,
            proposal_sources=selected.audit.proposal_sources,
            physical_evaluation=capture.physical_evaluation,
            signal_processing_profile=capture.scope.method_constraints.signal_processing_profile,
        )
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
    try:
        method_subject = cid_nl_method_subject_from_request(scope.request)
    except CidNlMethodSubjectContractError:
        batch = cid_nl_method_subject_batch_from_request(scope.request)
        method_subject = next(
            subject
            for subject in batch.subjects
            if subject.method_subject_id == method_subject_id
        )
    label = f"untargeted:{query_mz:.8g}:{method_subject.label}"
    return label, "Analyte", "", label


__all__ = (
    "SampleLocalCapture",
    "SampleLocalCaptureError",
    "bind_owner_backed_capture_hypothesis",
    "capture_peak_hypotheses",
    "capture_physical_recovery_observation",
    "mint_captured_peak_observation",
)
