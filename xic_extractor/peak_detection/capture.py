from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from xic_extractor.peak_detection.baseline import BaselineMethod
from xic_extractor.peak_detection.capture_boundary_policy import (
    promote_targeted_boundary_hypothesis,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    cid_nl_method_subject_from_request,
)
from xic_extractor.peak_detection.facade import find_peak_and_area
from xic_extractor.peak_detection.hypotheses import (
    PeakHypothesis,
    build_peak_hypotheses,
)
from xic_extractor.peak_detection.ms1_trace_detection import (
    Ms1TracePeakDetection,
    detect_ms1_trace_peak,
)
from xic_extractor.peak_detection.observations import (
    PeakObservationRef,
    mint_peak_observation_ref,
)
from xic_extractor.peak_detection.search_scope import (
    SearchScope,
    SearchScopeContractError,
    TargetSearchScope,
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
    detection = detect_ms1_trace_peak(
        trace.rt,
        trace.intensity,
        peak_config=peak_config,
        preferred_rt=binding.preferred_rt,
        peak_finder=find_peak_and_area,
    )
    label, role, istd_pair, common_evidence_source_id = _hypothesis_context(
        scope,
        query_mz=binding.query_mz,
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
        selected = receipt_hypothesis
        if (
            selected is not None
            and scope.method_constraints.boundary_policy == "targeted_gaussian15"
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
        resolver_mode=capture.scope.method_constraints.peak_config.resolver_mode,
        proposal_sources=selected.audit.proposal_sources,
    )


def _hypothesis_context(
    scope: SearchScope,
    *,
    query_mz: float,
) -> tuple[str, str, str, str]:
    if isinstance(scope, TargetSearchScope):
        target = scope.target.definition.target
        return (
            target.label,
            "ISTD" if target.is_istd else "Analyte",
            target.istd_pair,
            target.label,
        )
    method_subject = cid_nl_method_subject_from_request(scope.request)
    label = (
        f"untargeted:{query_mz:.8g}:"
        f"NL{method_subject.neutral_loss_da:.8g}"
    )
    return label, "Analyte", "", label


__all__ = (
    "SampleLocalCapture",
    "SampleLocalCaptureError",
    "capture_peak_hypotheses",
    "mint_captured_peak_observation",
)
