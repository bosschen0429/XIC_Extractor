from __future__ import annotations

from dataclasses import dataclass, field

from xic_extractor.config import ExtractionConfig
from xic_extractor.peak_detection.baseline import BaselineMethod
from xic_extractor.peak_detection.capture_boundary_policy import (
    promote_targeted_boundary_hypothesis,
)
from xic_extractor.peak_detection.hypotheses import (
    CandidateMS2EvidenceMap,
    PeakHypothesis,
    build_peak_hypotheses,
)
from xic_extractor.peak_detection.ms1_trace_detection import (
    Ms1PeakFinder,
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
    require_capture_candidate,
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
    peak_config: ExtractionConfig,
    preferred_rt: float | None,
    peak_finder: Ms1PeakFinder,
    resolver_mode: str,
    context_id: str | None = None,
    candidate_ms2_evidence: CandidateMS2EvidenceMap | None = None,
    baseline_integration_method: BaselineMethod = "asls",
    ms1_morphology_smoothing_window_points: int | None = None,
) -> SampleLocalCapture:
    candidate = require_capture_candidate(scope, candidate_id)
    if not isinstance(trace, Trace):
        raise SampleLocalCaptureError("capture trace must be an owner trace")
    if trace.sample_name not in {
        sample.metadata.sample_name for sample in scope.samples
    }:
        raise SampleLocalCaptureError("capture trace sample is outside SearchScope")
    try:
        validate_scope_trace_coordinates(
            scope,
            mz=trace.mz,
            ppm_tol=trace.ppm_tol,
        )
    except SearchScopeContractError as exc:
        raise SampleLocalCaptureError(str(exc)) from exc
    if not isinstance(peak_config, ExtractionConfig):
        raise SampleLocalCaptureError("peak_config has the wrong owner type")
    detection = detect_ms1_trace_peak(
        trace.rt,
        trace.intensity,
        peak_config=peak_config,
        preferred_rt=preferred_rt,
        peak_finder=peak_finder,
    )
    label, role, istd_pair, common_evidence_source_id = _hypothesis_context(
        scope,
        context_id=context_id,
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
        expected_rt_min=preferred_rt,
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
            resolver_mode=resolver_mode,
            peak_result=detection.result,
            candidate_ms2_evidence=candidate_ms2_evidence,
            trace_group=trace_group,
            baseline_integration_method=baseline_integration_method,
            ms1_morphology_smoothing_window_points=(
                peak_config.ms1_morphology_smoothing_window_points
                if ms1_morphology_smoothing_window_points is None
                else ms1_morphology_smoothing_window_points
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
                    if ms1_morphology_smoothing_window_points is None
                    else ms1_morphology_smoothing_window_points
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
    resolver_mode: str,
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
        resolver_mode=resolver_mode,
        proposal_sources=selected.audit.proposal_sources,
    )


def _hypothesis_context(
    scope: SearchScope,
    *,
    context_id: str | None,
) -> tuple[str, str, str, str]:
    if isinstance(scope, TargetSearchScope):
        target = scope.target.definition.target
        return (
            target.label,
            "ISTD" if target.is_istd else "Analyte",
            target.istd_pair,
            target.label,
        )
    label = scope.method_context_id if context_id is None else context_id
    if not isinstance(label, str) or not label or label != label.strip():
        raise SampleLocalCaptureError(
            "Untargeted capture context_id must be normalized text"
        )
    return label, "Analyte", "", label


__all__ = (
    "SampleLocalCapture",
    "SampleLocalCaptureError",
    "capture_peak_hypotheses",
    "mint_captured_peak_observation",
)
