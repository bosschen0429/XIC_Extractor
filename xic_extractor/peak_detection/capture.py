from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import cast

import numpy as np

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.baseline import BaselineMethod
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    cid_nl_method_subject_by_id,
)
from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    PeakHypothesis,
    build_peak_hypotheses,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.ms1_trace_detection import (
    Ms1TracePeakDetection,
    detect_ms1_trace_peak,
)
from xic_extractor.peak_detection.observations import (
    PeakObservationContent,
    PeakObservationContractError,
    PeakObservationGenerationProvenance,
    PeakObservationRef,
    _project_peak_observation_values,
    mint_peak_observation_ref,
    mint_physical_peak_observations,
    physical_peak_observation_integration,
)
from xic_extractor.peak_detection.peak_integration import find_peak_and_area
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PHYSICAL_PEAK_BACKFILL_POLICY,
    PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY,
    PhysicalPeakEvaluation,
    PreparedSignalResult,
)
from xic_extractor.peak_detection.search_scope import (
    CaptureCandidateBinding,
    SearchScope,
    SearchScopeContractError,
    TargetSearchScope,
    UntargetedSearchScope,
    require_capture_binding,
)
from xic_extractor.peak_detection.traces import Trace, TraceGroup
from xic_extractor.sample_metadata import SampleMetadata


class SampleLocalCaptureError(ValueError):
    """Raised when shared sample-local capture cannot close one candidate."""


@dataclass(frozen=True, slots=True)
class SampleLocalCapture:
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
    label, role, istd_pair = _hypothesis_context(
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
    return SampleLocalCapture(
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
        scope.method_constraints != capture.scope.method_constraints
        or require_capture_binding(scope, capture.candidate_id)
        != require_capture_binding(capture.scope, capture.candidate_id)
    ):
        raise SampleLocalCaptureError("scope rebinding changed the capture question")
    return replace(
        capture,
        scope=scope,
    )


def _capture_trace_group(
    *, scope: SearchScope, binding: CaptureCandidateBinding, trace: Trace
) -> TraceGroup:
    if not isinstance(trace, Trace):
        raise SampleLocalCaptureError("capture trace must be an owner trace")
    label, role, istd_pair = _hypothesis_context(
        scope,
        query_mz=binding.query_mz,
        method_subject_id=binding.method_subject_id,
    )
    return TraceGroup(
        trace_group_id=f"{trace.sample_name}|{binding.candidate_id}",
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
    if (
        binding.physical_evaluation_mz is not None
        and binding.physical_evaluation_mz != trace.mz
    ):
        raise SampleLocalCaptureError(
            "physical capture m/z differs from the retained owner query"
        )
    retained = expected.retained_prepared_signal
    matches_trace = np.array_equal(trace.rt, expected.prepared.rt) and np.array_equal(
        trace.intensity, expected.prepared.raw
    )
    if not matches_trace:
        raise SampleLocalCaptureError(
            "physical capture data differs from the retained owner trace"
        )
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
            hypothesis_id=binding.candidate_id,
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
            sample_key=binding.sample_key,
            trace=trace,
            integration=selected.integration,
            selected_indices=selected.integration.raw_scan_indices,
            resolver_mode=scope.method_constraints.peak_config.resolver_mode,
            proposal_sources=selected.audit.proposal_sources,
            shared_trace_provenance=binding.acquired_trace_provenance,
        )
    detection = Ms1TracePeakDetection(
        status="detected" if selected is not None else "not_detected",
        rt=trace.rt,
        intensity=trace.intensity,
    )
    hypotheses = (selected,) if selected is not None else ()
    return SampleLocalCapture(
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
    sample: SampleMetadata,
    method_subject: CidNlMethodSubject,
    trace: Trace,
    query_rt_min: float,
    physical_mz: float,
    profile: SignalProcessingProfile,
    resolver_mode: str,
    search_rt_window: tuple[float, float] | None = None,
    location_reliable: bool = True,
    evaluation_sink: Callable[[PhysicalPeakEvaluation], None] | None = None,
) -> PeakObservationRef | None:
    """Capture a backfill question using its request-bound method and profile."""
    if (
        trace.mz != physical_mz
        or trace.ppm_tol != method_subject.precursor_tolerance_ppm
        or not trace.rt_min <= query_rt_min <= trace.rt_max
    ):
        raise SampleLocalCaptureError("physical backfill question is not request-bound")
    prepared = prepare_physical_signal(trace.rt, trace.intensity, profile=profile)
    evaluation = evaluate_physical_peak(
        prepared,
        query_rt_min=query_rt_min,
        request_context_rt_start_min=trace.rt_min,
        request_context_rt_end_min=trace.rt_max,
        selection_policy_version=(
            PHYSICAL_PEAK_BACKFILL_POLICY
            if location_reliable
            else PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY
        ),
        search_rt_window=search_rt_window,
    )
    if evaluation_sink is not None:
        evaluation_sink(evaluation)
    if evaluation.state != "available" or evaluation.requires_overlap_review:
        return None
    assert prepared.prepared is not None
    return mint_physical_peak_observations(
        sample=sample,
        trace=trace,
        evaluations=(evaluation,),
        resolver_mode=resolver_mode,
        signal_processing_profile=profile,
        capture_stage="method_backfill",
    )[0]


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
    sample: SampleMetadata,
) -> PeakObservationRef:
    if not isinstance(capture, SampleLocalCapture):
        raise SampleLocalCaptureError("observation requires SampleLocalCapture")
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
        sample_key, content, provenance = _project_captured_physical_observation(
            capture, selected
        )
        if sample_key != sample.sample_name:
            raise PeakObservationContractError(
                "capture observation references a different sample"
            )
        return PeakObservationRef(
            content=content,
            generation_provenance=provenance,
            source_trace=capture.trace,
            physical_evaluation=capture.physical_evaluation,
            signal_processing_profile=(
                capture.scope.method_constraints.signal_processing_profile
            ),
            capture_stage="initial_capture",
        )
    return mint_peak_observation_ref(
        sample=sample,
        trace=capture.trace,
        integration=selected.integration,
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
    content = capture.physical_observation_content
    provenance = capture.physical_observation_provenance
    if content is None or provenance is None:
        raise PeakObservationContractError(
            "physical observation requires the capture owner projection"
        )
    return binding.sample_key, content, provenance


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
        if capture.physical_evaluation is not None:
            _sample_key, content, provenance = _project_captured_physical_observation(
                capture, selected
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
    return replace(
        capture,
        hypotheses=(hypothesis,),
        selected_hypothesis=hypothesis,
    )


def _hypothesis_context(
    scope: SearchScope,
    *,
    query_mz: float,
    method_subject_id: str,
) -> tuple[str, str, str]:
    if isinstance(scope, TargetSearchScope):
        target = scope.target.definition.target
        return (
            target.label,
            "ISTD" if target.is_istd else "Analyte",
            target.istd_pair,
        )
    method_subject = cid_nl_method_subject_by_id(
        scope.method_subjects, method_subject_id
    )
    label = f"untargeted:{query_mz:.8g}:{method_subject.label}"
    return label, "Analyte", ""


__all__ = (
    "SampleLocalCapture",
    "SampleLocalCaptureError",
    "bind_owner_backed_capture_hypothesis",
    "capture_peak_hypotheses",
    "capture_physical_backfill_observation",
    "mint_captured_peak_observation",
    "rebind_untargeted_capture_scope",
)
