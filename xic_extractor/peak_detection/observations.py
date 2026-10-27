"""Owner-backed physical MS1 peak-observation references.

``peak_detection`` owns the measured trace region and integration facts.  The
reference keeps physical content equality separate from the exact generation
context.  It grants no hypothesis, selection, or product
authority.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

import numpy as np

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.baseline import (
    asls_baseline,
    bounded_trace_interval,
    integrate_with_baseline,
)
from xic_extractor.peak_detection.integration import integrate_area_counts_seconds
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.ms1_morphology import (
    MS1_MORPHOLOGY_AREA_SOURCE,
    MS1_MORPHOLOGY_TRACE_METHOD,
    gaussian15_positive_asls_residual_metrics,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    IdentityContractError,
    ProcessingOwnerReferences,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class PeakObservationContractError(ValueError):
    """Raised when physical content or generation provenance is inconsistent."""


@dataclass(frozen=True, slots=True)
class PeakObservationContent:
    """Exact sample-local selected MS1 signal and integration content."""

    sample_key: str
    mz: float
    rt: tuple[float, ...]
    intensity: tuple[float, ...]
    integration: IntegrationResult

    def __post_init__(self) -> None:
        mz = _finite_float(self.mz, field="mz")
        if mz <= 0.0:
            raise PeakObservationContractError("mz must be positive")
        rt = _finite_float_tuple(self.rt, field="rt")
        intensity = _finite_float_tuple(self.intensity, field="intensity")
        if not rt or len(rt) != len(intensity):
            raise PeakObservationContractError(
                "selected rt and intensity must be matching non-empty tuples"
            )
        if any(right <= left for left, right in zip(rt, rt[1:], strict=False)):
            raise PeakObservationContractError(
                "selected rt values must be strictly increasing"
            )
        if not isinstance(self.integration, IntegrationResult):
            raise PeakObservationContractError(
                "integration must be an owner-produced IntegrationResult"
            )
        if self.integration.raw_scan_indices != tuple(range(len(rt))):
            raise PeakObservationContractError(
                "content integration indices must address the selected subtrace"
            )
        _validate_integration(self.integration, selected_rt=rt)
        object.__setattr__(self, "mz", mz)
        object.__setattr__(self, "rt", rt)
        object.__setattr__(self, "intensity", intensity)

    @property
    def physical_identity(self) -> PeakObservationContent:
        """This peak without advisory measurement quality, for in-process joins."""

        if self.integration.signal_quality is None:
            return self
        return replace(self, integration=replace(self.integration, signal_quality=None))

    @property
    def physical_peak_key(self) -> str:
        """Measured coordinates that locate this peak in a written report."""

        return "|".join(
            (
                self.sample_key,
                repr(self.mz),
                repr(self.rt[0]),
                repr(self.rt[-1]),
                str(len(self.rt)),
                repr(self.integration.rt_apex_min),
                repr(self.integration.height_raw),
            )
        )

    @property
    def measured_order_key(
        self,
    ) -> tuple[float, float, tuple[float, ...], tuple[float, ...]]:
        """Total order over distinct selected signals from raw measured values."""

        return (self.integration.rt_apex_min, self.mz, self.rt, self.intensity)


@dataclass(frozen=True, slots=True)
class PeakObservationGenerationProvenance:
    """Exact trace/query and peak-generation context for one observation ref."""

    acquisition_source_id: str
    query_rt_min: float
    query_rt_max: float
    query_ppm_tol: float
    trace_source: str
    selected_trace_indices: tuple[int, ...]
    resolver_mode: str
    proposal_sources: tuple[str, ...]

    def __post_init__(self) -> None:
        rt_min = _finite_float(self.query_rt_min, field="query_rt_min")
        rt_max = _finite_float(self.query_rt_max, field="query_rt_max")
        ppm_tol = _finite_float(self.query_ppm_tol, field="query_ppm_tol")
        if rt_min > rt_max:
            raise PeakObservationContractError("query_rt_min must be <= query_rt_max")
        if ppm_tol <= 0.0:
            raise PeakObservationContractError("query_ppm_tol must be positive")
        object.__setattr__(self, "query_rt_min", rt_min)
        object.__setattr__(self, "query_rt_max", rt_max)
        object.__setattr__(self, "query_ppm_tol", ppm_tol)
        if not isinstance(self.trace_source, str):
            raise PeakObservationContractError("trace_source must be text")
        _validate_selected_indices(self.selected_trace_indices)
        _required_text(self.resolver_mode, field="resolver_mode")
        if not isinstance(self.proposal_sources, tuple):
            raise PeakObservationContractError("proposal_sources must be a tuple")
        normalized_sources = tuple(
            sorted(
                {
                    _required_text(value, field="proposal_source")
                    for value in self.proposal_sources
                }
            )
        )
        if normalized_sources != self.proposal_sources:
            raise PeakObservationContractError(
                "proposal_sources must be unique and canonical-sorted"
            )


@dataclass(frozen=True, slots=True)
class PeakObservationRef:
    """Selected content and the acquired context already bound by provenance.

    The source trace is retained for in-memory physical-view comparison; it
    neither changes the selected integral nor adds another persisted identity.
    """

    content: PeakObservationContent
    generation_provenance: PeakObservationGenerationProvenance
    source_trace: Trace = field(compare=False)
    physical_evaluation: PhysicalPeakEvaluation | None = field(
        default=None, compare=False
    )
    capture_stage: str = field(default="initial_capture", compare=False)
    signal_processing_profile: SignalProcessingProfile | None = field(
        default=None, compare=False
    )


@dataclass(frozen=True, slots=True)
class RetainedCaptureEvidence:
    """Actual capture inputs and output, with no scientific decision authority."""

    sample_key: str
    processing_request_id: str
    processing_attempt_id: str
    method_subject_id: str
    candidate_id: str | None
    capture_stage: str
    trace: Trace
    physical_evaluation: PhysicalPeakEvaluation | None
    observation_ref: PeakObservationRef | None = None
    source_physical_evaluation: PhysicalPeakEvaluation | None = None


def mint_peak_observation_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    trace: Trace,
    integration: IntegrationResult,
    selection_intensity: object | None = None,
    resolver_mode: str,
    proposal_sources: tuple[str, ...],
    physical_evaluation: PhysicalPeakEvaluation | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
    capture_stage: str = "initial_capture",
) -> PeakObservationRef:
    """Mint one no-RAW observation after binding integration to exact signals.

    ``selection_intensity`` is the full detection/smoothing trace used for the
    selected apex.  Raw intensity is the default when no separate selection
    transform was used.  The owner rejects independently assembled integration
    facts that contradict either signal before it mints content or artifact
    identity.
    """

    _validate_peak_observation_inputs(
        processing_owners=processing_owners,
        sample=sample,
        trace=trace,
        integration=integration,
    )

    content, generation_provenance = _peak_observation_values(
        sample_key=sample.identity.sample_key,
        trace=trace,
        integration=integration,
        selection_intensity=selection_intensity,
        resolver_mode=resolver_mode,
        proposal_sources=proposal_sources,
        physical_evaluation=physical_evaluation,
        signal_processing_profile=signal_processing_profile,
    )
    return PeakObservationRef(
        content=content,
        generation_provenance=generation_provenance,
        source_trace=trace,
        physical_evaluation=physical_evaluation,
        signal_processing_profile=signal_processing_profile,
        capture_stage=capture_stage,
    )


def mint_physical_peak_observations(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    trace: Trace,
    evaluations: tuple[PhysicalPeakEvaluation, ...],
    signal_processing_profile: SignalProcessingProfile,
    resolver_mode: str,
    capture_stage: str = "initial_capture",
    trace_observation: PeakObservationRef | None = None,
    acquired_trace_provenance: PeakObservationGenerationProvenance | None = None,
) -> tuple[PeakObservationRef, ...]:
    """Project retained physical-owner answers for one trace without re-solving it.

    Acquisition/physical decoding owns evaluation validity. This handoff checks
    the current sample, trace and profile once; candidate projections share their
    query provenance, but retain their own selected region and artifact.
    Independently assembled integrations use ``mint_peak_observation_ref``.
    """
    if not evaluations:
        return ()
    retained = evaluations[0].retained_prepared_signal
    if retained is None or retained.profile != signal_processing_profile:
        raise PeakObservationContractError(
            "physical observations lost retained profile"
        )
    if not (
        np.array_equal(trace.rt, retained.rt)
        and np.array_equal(trace.intensity, retained.raw)
    ):
        raise PeakObservationContractError("physical observations cross retained trace")
    provenance = acquired_trace_provenance
    if trace_observation is not None:
        if (
            trace_observation.source_trace is not trace
            or trace_observation.signal_processing_profile != signal_processing_profile
        ):
            raise PeakObservationContractError(
                "shared observation crosses trace/profile"
            )
        validate_peak_observation_binding(
            trace_observation,
            processing_owners=processing_owners,
            sample=sample,
        )
        provenance = replace(
            trace_observation.generation_provenance,
            resolver_mode=resolver_mode,
            proposal_sources=("canonical_physical_segment",),
        )
    observations: list[PeakObservationRef] = []
    for evaluation in evaluations:
        if (
            evaluation.retained_prepared_signal is not retained
            and evaluation.retained_prepared_signal != retained
        ):
            raise PeakObservationContractError(
                "physical observations cross trace context"
            )
        integration = physical_peak_observation_integration(evaluation)
        if not observations:
            _validate_peak_observation_inputs(
                processing_owners=processing_owners,
                sample=sample,
                trace=trace,
                integration=integration,
            )
        content, provenance = _project_peak_observation_values(
            sample_key=sample.identity.sample_key,
            trace=trace,
            integration=integration,
            selected_indices=integration.raw_scan_indices,
            resolver_mode=resolver_mode,
            proposal_sources=("canonical_physical_segment",),
            shared_trace_provenance=provenance,
        )
        observations.append(
            PeakObservationRef(
                content=content,
                generation_provenance=provenance,
                source_trace=trace,
                physical_evaluation=evaluation,
                signal_processing_profile=signal_processing_profile,
                capture_stage=capture_stage,
            )
        )
    return tuple(observations)


def _validate_peak_observation_inputs(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    trace: Trace,
    integration: IntegrationResult,
) -> None:
    acquisition_source = _request_acquisition_source(processing_owners, sample)
    if not isinstance(trace, Trace):
        raise PeakObservationContractError("trace must be an owner-produced Trace")
    if trace.sample_name not in {
        sample.metadata.sample_name,
        sample.metadata.raw_stem,
        sample.lookup_token,
    }:
        raise PeakObservationContractError("Trace references a different sample")
    if trace.acquisition_source_id != acquisition_source.acquisition_source_id:
        raise PeakObservationContractError(
            "Trace acquisition source mismatches the request binding"
        )
    if not isinstance(integration, IntegrationResult):
        raise PeakObservationContractError(
            "integration must be an IntegrationResult value"
        )


def validate_peak_observation_trace_binding(
    reference: PeakObservationRef,
    *,
    sample_key: str,
    trace: Trace,
    integration: IntegrationResult,
    selection_intensity: object | None = None,
    resolver_mode: str,
    proposal_sources: tuple[str, ...],
    physical_evaluation: PhysicalPeakEvaluation | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
) -> None:
    """Reject an observation reconstructed from any other capture trace."""

    if not isinstance(reference, PeakObservationRef):
        raise PeakObservationContractError(
            "reference must be an owner-minted PeakObservationRef"
        )
    expected_content, expected_provenance = _peak_observation_values(
        sample_key=sample_key,
        trace=trace,
        integration=integration,
        selection_intensity=selection_intensity,
        resolver_mode=resolver_mode,
        proposal_sources=proposal_sources,
        physical_evaluation=physical_evaluation,
        signal_processing_profile=signal_processing_profile,
    )
    if (
        reference.content != expected_content
        or reference.generation_provenance != expected_provenance
    ):
        raise PeakObservationContractError(
            "peak observation does not bind the exact captured trace"
        )


def _peak_observation_values(
    *,
    sample_key: str,
    trace: Trace,
    integration: IntegrationResult,
    selection_intensity: object | None,
    resolver_mode: str,
    proposal_sources: tuple[str, ...],
    physical_evaluation: PhysicalPeakEvaluation | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
) -> tuple[PeakObservationContent, PeakObservationGenerationProvenance]:
    if not isinstance(trace, Trace):
        raise PeakObservationContractError("trace must be an owner-produced Trace")
    if not isinstance(integration, IntegrationResult):
        raise PeakObservationContractError(
            "integration must be an IntegrationResult value"
        )
    selection_values = _selection_trace_values(trace, selection_intensity)
    selected_indices = _validate_selected_indices(integration.raw_scan_indices)
    if selected_indices[-1] >= trace.scan_count:
        raise PeakObservationContractError(
            "selected trace indices exceed the supplied Trace"
        )
    _validate_trace_region_binding(trace, integration, selected_indices)
    _validate_signal_integration_binding(
        trace,
        selection_values,
        integration,
        selected_indices,
        physical_evaluation=physical_evaluation,
        signal_processing_profile=signal_processing_profile,
    )
    return _project_peak_observation_values(
        sample_key=sample_key,
        trace=trace,
        integration=integration,
        selected_indices=selected_indices,
        resolver_mode=resolver_mode,
        proposal_sources=proposal_sources,
    )


def _project_peak_observation_values(
    *,
    sample_key: str,
    trace: Trace,
    integration: IntegrationResult,
    selected_indices: tuple[int, ...],
    resolver_mode: str,
    proposal_sources: tuple[str, ...],
    shared_trace_provenance: PeakObservationGenerationProvenance | None = None,
) -> tuple[PeakObservationContent, PeakObservationGenerationProvenance]:
    """Project signals and integration already validated by their input owner."""
    selected_rt = tuple(
        _finite_float(float(trace.rt[index]), field="selected_rt")
        for index in selected_indices
    )
    selected_intensity = tuple(
        _finite_float(float(trace.intensity[index]), field="selected_intensity")
        for index in selected_indices
    )
    normalized_integration = replace(
        integration,
        raw_scan_indices=tuple(range(len(selected_indices))),
    )
    return (
        _owner_minted_peak_observation_content(
            sample_key=sample_key,
            mz=float(trace.mz),
            rt=selected_rt,
            intensity=selected_intensity,
            integration=normalized_integration,
        ),
        replace(
            shared_trace_provenance,
            selected_trace_indices=selected_indices,
            resolver_mode=resolver_mode,
            proposal_sources=proposal_sources,
        )
        if shared_trace_provenance is not None
        else prepare_peak_observation_provenance(
            trace=trace,
            selected_indices=selected_indices,
            resolver_mode=resolver_mode,
            proposal_sources=proposal_sources,
        ),
    )


def prepare_peak_observation_provenance(
    *,
    trace: Trace,
    selected_indices: tuple[int, ...],
    resolver_mode: str,
    proposal_sources: tuple[str, ...],
) -> PeakObservationGenerationProvenance:
    """Retain query and selection context without hashing signal arrays."""
    return PeakObservationGenerationProvenance(
        acquisition_source_id=trace.acquisition_source_id,
        query_rt_min=float(trace.rt_min),
        query_rt_max=float(trace.rt_max),
        query_ppm_tol=float(trace.ppm_tol),
        trace_source=trace.source,
        selected_trace_indices=selected_indices,
        resolver_mode=_required_text(resolver_mode, field="resolver_mode"),
        proposal_sources=tuple(
            sorted(
                {
                    _required_text(value, field="proposal_source")
                    for value in proposal_sources
                }
            )
        ),
    )


def _owner_minted_peak_observation_content(
    *,
    sample_key: str,
    mz: float,
    rt: tuple[float, ...],
    intensity: tuple[float, ...],
    integration: IntegrationResult,
) -> PeakObservationContent:
    """Issue content from values normalized by the observation owner once."""

    content = object.__new__(PeakObservationContent)
    object.__setattr__(
        content,
        "sample_key",
        sample_key,
    )
    object.__setattr__(content, "mz", mz)
    object.__setattr__(content, "rt", rt)
    object.__setattr__(content, "intensity", intensity)
    object.__setattr__(content, "integration", integration)
    return content


def validate_peak_observation_binding(
    reference: PeakObservationRef,
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> None:
    """Check the sample and RAW source against the acquisition request."""

    if not isinstance(reference, PeakObservationRef):
        raise PeakObservationContractError(
            "reference must be an owner-minted PeakObservationRef"
        )
    acquisition_source = _request_acquisition_source(processing_owners, sample)
    if reference.content.sample_key != sample.identity.sample_key:
        raise PeakObservationContractError(
            "peak observation references a different sample"
        )
    if (
        reference.generation_provenance.acquisition_source_id
        != acquisition_source.acquisition_source_id
    ):
        raise PeakObservationContractError(
            "peak observation references a different acquisition source"
        )


def _validate_integration(
    integration: IntegrationResult,
    *,
    selected_rt: tuple[float, ...],
) -> None:
    if not (
        integration.rt_left_min <= integration.rt_apex_min <= integration.rt_right_min
    ):
        raise PeakObservationContractError(
            "integration RT bounds must contain the selected apex"
        )
    if not (
        selected_rt[0] <= integration.rt_apex_min <= selected_rt[-1]
        and selected_rt[0] <= integration.raw_apex_rt_min <= selected_rt[-1]
    ):
        raise PeakObservationContractError(
            "integration apex is outside the selected trace region"
        )
    if not math.isclose(
        integration.rt_width_min,
        integration.rt_right_min - integration.rt_left_min,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise PeakObservationContractError(
            "integration rt_width_min mismatches its RT bounds"
        )


def _validate_trace_region_binding(
    trace: Trace,
    integration: IntegrationResult,
    selected_indices: tuple[int, ...],
) -> None:
    if trace.scan_count < 2:
        raise PeakObservationContractError(
            "Trace must contain at least two scans for an integrated observation"
        )
    trace_rt_min = float(trace.rt[0])
    trace_rt_max = float(trace.rt[-1])
    if not (
        trace_rt_min <= integration.rt_left_min <= trace_rt_max
        and trace_rt_min <= integration.rt_right_min <= trace_rt_max
    ):
        raise PeakObservationContractError(
            "integration RT bounds must remain inside the supplied Trace"
        )
    left_index = _nearest_trace_index(trace, integration.rt_left_min)
    right_index = _nearest_trace_index(trace, integration.rt_right_min) + 1
    expected_left, expected_right = bounded_trace_interval(
        left_index,
        right_index,
        trace.scan_count,
    )
    if selected_indices != tuple(range(expected_left, expected_right)):
        raise PeakObservationContractError(
            "integration RT bounds do not map to the selected trace region"
        )


def _selection_trace_values(
    trace: Trace,
    selection_intensity: object | None,
) -> np.ndarray:
    try:
        values = np.asarray(
            trace.intensity if selection_intensity is None else selection_intensity,
            dtype=float,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise PeakObservationContractError(
            "selection_intensity must be a finite one-dimensional trace"
        ) from exc
    if values.ndim != 1 or values.shape != trace.intensity.shape:
        raise PeakObservationContractError(
            "selection_intensity must match the supplied Trace shape"
        )
    if not np.all(np.isfinite(values)):
        raise PeakObservationContractError(
            "selection_intensity must contain only finite values"
        )
    return values


def _validate_signal_integration_binding(
    trace: Trace,
    selection_intensity: np.ndarray,
    integration: IntegrationResult,
    selected_indices: tuple[int, ...],
    *,
    physical_evaluation: PhysicalPeakEvaluation | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
) -> None:
    left_index = selected_indices[0]
    right_index = selected_indices[-1] + 1
    raw_segment = np.asarray(trace.intensity[left_index:right_index], dtype=float)
    raw_apex_index = left_index + int(np.argmax(raw_segment))
    _require_close(
        integration.raw_apex_rt_min,
        float(trace.rt[raw_apex_index]),
        field="raw_apex_rt_min",
    )
    _require_close(
        integration.height_raw,
        float(trace.intensity[raw_apex_index]),
        field="height_raw",
    )

    selection_apex_index = _nearest_trace_index(trace, integration.rt_apex_min)
    if selection_apex_index not in selected_indices:
        raise PeakObservationContractError(
            "rt_apex_min must address the selected trace region"
        )
    _require_close(
        integration.rt_apex_min,
        float(trace.rt[selection_apex_index]),
        field="rt_apex_min",
    )
    selection_segment = selection_intensity[left_index:right_index]
    expected_selection_height = float(np.max(selection_segment))
    _require_close(
        integration.height_smoothed,
        expected_selection_height,
        field="height_smoothed",
    )
    _require_close(
        float(selection_intensity[selection_apex_index]),
        expected_selection_height,
        field="rt_apex_min",
    )

    expected_area = integrate_area_counts_seconds(
        np.asarray(trace.intensity, dtype=float),
        np.asarray(trace.rt, dtype=float),
        left_index,
        right_index,
    )
    _require_close(
        integration.area_raw_counts_seconds,
        expected_area,
        field="area_raw_counts_seconds",
    )
    if physical_evaluation is not None or signal_processing_profile is not None:
        _validate_physical_integration_facts(
            trace,
            selection_intensity,
            integration,
            physical_evaluation=physical_evaluation,
            profile=signal_processing_profile,
        )
        return
    baseline_values = _validate_baseline_integration_facts(
        trace,
        integration,
        left_index=left_index,
        right_index=right_index,
    )
    _validate_morphology_integration_facts(
        trace,
        integration,
        baseline_values=baseline_values,
        left_index=left_index,
        right_index=right_index,
    )


def physical_peak_observation_integration(
    evaluation: PhysicalPeakEvaluation,
) -> IntegrationResult:
    """Bind the physical owner's raw descriptor to its exact selected samples."""
    facts = evaluation.facts
    integration = evaluation.integration
    if evaluation.state != "available" or facts is None or integration is None:
        raise PeakObservationContractError(
            "physical observation requires available facts"
        )
    segment = evaluation.answer.selected_segment
    assert segment is not None
    return replace(
        integration,
        height_smoothed=facts.height,
        raw_scan_indices=tuple(range(segment.start_index, segment.end_index)),
    )


def _validate_physical_integration_facts(
    trace: Trace,
    selection_intensity: np.ndarray,
    integration: IntegrationResult,
    *,
    physical_evaluation: PhysicalPeakEvaluation | None,
    profile: SignalProcessingProfile | None,
) -> None:
    if physical_evaluation is None or profile is None:
        raise PeakObservationContractError(
            "physical observation requires its profile and answer"
        )
    prepared = prepare_physical_signal(
        trace.rt,
        trace.intensity,
        profile=profile,
    )
    actual = evaluate_physical_peak(
        prepared,
        query_rt_min=physical_evaluation.question.query_rt_min,
        request_context_rt_start_min=trace.rt_min,
        request_context_rt_end_min=trace.rt_max,
        selection_policy_version=physical_evaluation.question.selection_policy_version,
        search_rt_window=physical_evaluation.question.search_rt_window,
    )
    if (
        actual != physical_evaluation
        or prepared.prepared is None
        or not np.array_equal(selection_intensity, prepared.prepared.positive_signal)
        or integration != physical_peak_observation_integration(actual)
    ):
        raise PeakObservationContractError(
            "physical integration contradicts the owner answer"
        )


def _validate_baseline_integration_facts(
    trace: Trace,
    integration: IntegrationResult,
    *,
    left_index: int,
    right_index: int,
) -> np.ndarray | None:
    baseline_declared = any(
        (
            integration.area_baseline_corrected is not None,
            integration.area_uncertainty is not None,
            bool(integration.area_uncertainty_formula_version),
            integration.baseline_residual_mad is not None,
            bool(integration.area_uncertainty_noise_source),
            bool(integration.baseline_type),
            integration.baseline_score is not None,
        )
    )
    if not baseline_declared:
        return None
    if integration.baseline_type != "asls":
        raise PeakObservationContractError(
            "integration baseline_type is not the owner-supported 'asls' contract"
        )

    intensity = np.asarray(trace.intensity, dtype=float)
    rt = np.asarray(trace.rt, dtype=float)
    baseline_values = asls_baseline(intensity)
    expected = integrate_with_baseline(
        intensity,
        rt,
        left_index,
        right_index,
        baseline_method="asls",
        baseline_values=baseline_values,
    )
    _require_optional_close(
        integration.area_baseline_corrected,
        expected.area_baseline_corrected,
        field="area_baseline_corrected",
    )
    _require_optional_close(
        integration.area_uncertainty,
        expected.area_uncertainty,
        field="area_uncertainty",
    )
    _require_equal(
        integration.area_uncertainty_formula_version,
        expected.area_uncertainty_formula_version,
        field="area_uncertainty_formula_version",
    )
    _require_optional_close(
        integration.baseline_residual_mad,
        expected.baseline_residual_mad,
        field="baseline_residual_mad",
    )
    _require_equal(
        integration.area_uncertainty_noise_source,
        expected.area_uncertainty_noise_source,
        field="area_uncertainty_noise_source",
    )
    _require_optional_close(
        integration.baseline_score,
        expected.baseline_score,
        field="baseline_score",
    )
    return baseline_values


def _validate_morphology_integration_facts(
    trace: Trace,
    integration: IntegrationResult,
    *,
    baseline_values: np.ndarray | None,
    left_index: int,
    right_index: int,
) -> None:
    morphology_declared = any(
        (
            integration.area_ms1_morphology is not None,
            bool(integration.ms1_morphology_area_source),
            bool(integration.ms1_morphology_trace_method),
            integration.ms1_morphology_trace_window_points is not None,
            integration.ms1_morphology_trace_effective_points is not None,
        )
    )
    if not morphology_declared:
        return
    if baseline_values is None:
        raise PeakObservationContractError(
            "integration area_ms1_morphology requires validated asls baseline facts"
        )
    _require_equal(
        integration.ms1_morphology_area_source,
        MS1_MORPHOLOGY_AREA_SOURCE,
        field="ms1_morphology_area_source",
    )
    _require_equal(
        integration.ms1_morphology_trace_method,
        MS1_MORPHOLOGY_TRACE_METHOD,
        field="ms1_morphology_trace_method",
    )
    window_points = integration.ms1_morphology_trace_window_points
    if window_points is None:
        raise PeakObservationContractError(
            "integration ms1_morphology_trace_window_points is required"
        )
    expected = gaussian15_positive_asls_residual_metrics(
        np.asarray(trace.rt, dtype=float),
        np.asarray(trace.intensity, dtype=float),
        baseline_values,
        left_index,
        right_index,
        window_points=window_points,
    )
    _require_optional_close(
        integration.area_ms1_morphology,
        expected.area_positive_asls_residual,
        field="area_ms1_morphology",
    )
    _require_equal(
        integration.ms1_morphology_trace_window_points,
        expected.trace_window_points,
        field="ms1_morphology_trace_window_points",
    )
    _require_equal(
        integration.ms1_morphology_trace_effective_points,
        expected.trace_effective_points,
        field="ms1_morphology_trace_effective_points",
    )


def _require_close(actual: float, expected: float, *, field: str) -> None:
    if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9):
        raise PeakObservationContractError(
            f"integration {field} contradicts the supplied signal"
        )


def _require_optional_close(
    actual: float | None,
    expected: float | None,
    *,
    field: str,
) -> None:
    if actual is None or expected is None:
        if actual is not expected:
            raise PeakObservationContractError(
                f"integration {field} contradicts the supplied signal"
            )
        return
    _require_close(actual, expected, field=field)


def _require_equal(actual: object, expected: object, *, field: str) -> None:
    if actual != expected:
        raise PeakObservationContractError(
            f"integration {field} contradicts the supplied signal"
        )


def _nearest_trace_index(trace: Trace, value: float) -> int:
    return min(
        range(trace.scan_count),
        key=lambda index: abs(float(trace.rt[index]) - value),
    )


def _request_acquisition_source(
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> AcquisitionSourceIdentity:
    try:
        return resolve_request_acquisition_source(
            processing_owners=processing_owners,
            sample=sample,
        )
    except IdentityContractError as exc:
        raise PeakObservationContractError(str(exc)) from exc


def _validate_selected_indices(values: object) -> tuple[int, ...]:
    if not isinstance(values, tuple) or not values:
        raise PeakObservationContractError("raw_scan_indices must be a non-empty tuple")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise PeakObservationContractError("raw_scan_indices must contain integers")
    if any(value < 0 for value in values):
        raise PeakObservationContractError("raw_scan_indices must be non-negative")
    if any(right <= left for left, right in zip(values, values[1:], strict=False)):
        raise PeakObservationContractError(
            "raw_scan_indices must be unique and strictly increasing"
        )
    if any(right != left + 1 for left, right in zip(values, values[1:], strict=False)):
        raise PeakObservationContractError("raw_scan_indices must be contiguous")
    return values


def _finite_float_tuple(value: object, *, field: str) -> tuple[float, ...]:
    if not isinstance(value, tuple):
        raise PeakObservationContractError(f"{field} must be a tuple")
    return tuple(_finite_float(item, field=field) for item in value)


def _finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PeakObservationContractError(f"{field} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise PeakObservationContractError(f"{field} must be a finite number")
    return 0.0 if normalized == 0.0 else normalized


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PeakObservationContractError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


def index_physical_peaks(
    peaks: Iterable[PeakObservationRef],
) -> dict[PeakObservationContent, PeakObservationRef]:
    """Index peaks by physical identity; a later ref of one peak replaces it."""

    return {peak.content.physical_identity: peak for peak in peaks}


def same_selected_peak(left: PeakObservationRef, right: PeakObservationRef) -> bool:
    """Whether two observations selected the same integrated MS1 peak."""

    a = left.content.integration
    b = right.content.integration
    return (
        a.rt_apex_min == b.rt_apex_min
        and a.rt_left_min == b.rt_left_min
        and a.rt_right_min == b.rt_right_min
        and a.height_raw == b.height_raw
    )


__all__ = [
    "PeakObservationContent",
    "PeakObservationContractError",
    "PeakObservationGenerationProvenance",
    "PeakObservationRef",
    "index_physical_peaks",
    "mint_peak_observation_ref",
    "same_selected_peak",
    "validate_peak_observation_binding",
    "validate_peak_observation_trace_binding",
]
