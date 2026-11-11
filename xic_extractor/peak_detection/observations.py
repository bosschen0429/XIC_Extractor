"""Owner-backed physical MS1 peak observations.

``peak_detection`` owns the measured trace region and integration facts.  An
observation compares by its measured signal only; the exact generation context
travels with it without changing that equality.  It grants no hypothesis,
selection, or product authority.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.sample_metadata import SampleMetadata


class PeakObservationContractError(ValueError):
    """Raised when physical content or generation provenance is inconsistent."""


@dataclass(frozen=True, slots=True)
class PeakObservationGenerationProvenance:
    """Exact trace/query and peak-generation context for one observation."""

    acquisition_source_id: str
    query_rt_min: float
    query_rt_max: float
    query_ppm_tol: float
    trace_source: str
    selected_trace_indices: tuple[int, ...]
    resolver_mode: str
    proposal_sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PeakObservation:
    """One sample-local selected MS1 signal, its integration, and its context.

    Equality uses the measured signal only.  The generation provenance, source
    trace and evaluation are the context that produced it; the source trace is
    retained for in-memory physical-view comparison and neither changes the
    selected integral nor adds another persisted identity.
    """

    sample_key: str
    mz: float
    rt: tuple[float, ...]
    intensity: tuple[float, ...]
    integration: IntegrationResult
    generation_provenance: PeakObservationGenerationProvenance = field(compare=False)
    source_trace: Trace = field(compare=False)
    physical_evaluation: PhysicalPeakEvaluation | None = field(
        default=None, compare=False
    )
    capture_stage: str = field(default="initial_capture", compare=False)
    signal_processing_profile: SignalProcessingProfile | None = field(
        default=None, compare=False
    )

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
    def physical_identity(self) -> PeakObservation:
        """This peak without advisory measurement quality, for in-process joins."""

        if self.integration.signal_quality is None:
            return self
        return replace(self, integration=replace(self.integration, signal_quality=None))

    @property
    def physical_peak_key(self) -> str:
        """Measured coordinates that locate this peak in a written report.

        The XIC query is part of the locator: the same selected scans read in
        another RT window or tolerance can integrate differently (baseline and
        smoothing see another trace), and each such measurement is reported.
        """

        provenance = self.generation_provenance
        return "|".join(
            (
                self.sample_key,
                repr(self.mz),
                repr(self.rt[0]),
                repr(self.rt[-1]),
                str(len(self.rt)),
                repr(self.integration.rt_apex_min),
                repr(self.integration.height_raw),
                repr(provenance.query_rt_min),
                repr(provenance.query_rt_max),
                repr(provenance.query_ppm_tol),
            )
        )

    @property
    def measured_order_key(
        self,
    ) -> tuple[float, float, tuple[float, ...], tuple[float, ...]]:
        """Total order over distinct selected signals from raw measured values."""

        return (self.integration.rt_apex_min, self.mz, self.rt, self.intensity)


@dataclass(frozen=True, slots=True)
class RetainedCaptureEvidence:
    """Actual capture inputs and output, with no scientific decision authority."""

    sample_key: str
    processing_attempt_id: str
    method_subject_id: str
    candidate_id: str | None
    capture_stage: str
    trace: Trace
    physical_evaluation: PhysicalPeakEvaluation | None
    observation_ref: PeakObservation | None = None


def mint_peak_observation(
    *,
    sample: SampleMetadata,
    trace: Trace,
    integration: IntegrationResult,
    resolver_mode: str,
    proposal_sources: tuple[str, ...],
    physical_evaluation: PhysicalPeakEvaluation | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
    capture_stage: str = "initial_capture",
) -> PeakObservation:
    """Project an already selected integration into one sample-local peak."""
    return _project_peak_observation(
        sample_key=sample.sample_name,
        trace=trace,
        integration=integration,
        selected_indices=integration.raw_scan_indices,
        resolver_mode=resolver_mode,
        proposal_sources=proposal_sources,
        physical_evaluation=physical_evaluation,
        signal_processing_profile=signal_processing_profile,
        capture_stage=capture_stage,
    )


def mint_physical_peak_observations(
    *,
    sample: SampleMetadata,
    trace: Trace,
    evaluations: tuple[PhysicalPeakEvaluation, ...],
    signal_processing_profile: SignalProcessingProfile,
    resolver_mode: str,
    capture_stage: str = "initial_capture",
    trace_observation: PeakObservation | None = None,
    acquired_trace_provenance: PeakObservationGenerationProvenance | None = None,
) -> tuple[PeakObservation, ...]:
    """Project retained physical-owner answers for one trace without re-solving it.

    Acquisition and physical decoding establish evaluation validity. Candidate
    projections share query provenance and retain their own selected region.
    """
    if not evaluations:
        return ()
    provenance = acquired_trace_provenance
    if trace_observation is not None:
        provenance = replace(
            trace_observation.generation_provenance,
            resolver_mode=resolver_mode,
            proposal_sources=("canonical_physical_segment",),
        )
    observations: list[PeakObservation] = []
    for evaluation in evaluations:
        integration = physical_peak_observation_integration(evaluation)
        observation = _project_peak_observation(
            sample_key=sample.sample_name,
            trace=trace,
            integration=integration,
            selected_indices=integration.raw_scan_indices,
            resolver_mode=resolver_mode,
            proposal_sources=("canonical_physical_segment",),
            shared_trace_provenance=provenance,
            physical_evaluation=evaluation,
            signal_processing_profile=signal_processing_profile,
            capture_stage=capture_stage,
        )
        provenance = observation.generation_provenance
        observations.append(observation)
    return tuple(observations)


def _project_peak_observation(
    *,
    sample_key: str,
    trace: Trace,
    integration: IntegrationResult,
    selected_indices: tuple[int, ...],
    resolver_mode: str,
    proposal_sources: tuple[str, ...],
    shared_trace_provenance: PeakObservationGenerationProvenance | None = None,
    physical_evaluation: PhysicalPeakEvaluation | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
    capture_stage: str = "initial_capture",
) -> PeakObservation:
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
    provenance = (
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
        )
    )
    return _owner_minted_peak_observation(
        sample_key=sample_key,
        mz=float(trace.mz),
        rt=selected_rt,
        intensity=selected_intensity,
        integration=normalized_integration,
        generation_provenance=provenance,
        source_trace=trace,
        physical_evaluation=physical_evaluation,
        signal_processing_profile=signal_processing_profile,
        capture_stage=capture_stage,
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


def _owner_minted_peak_observation(
    *,
    sample_key: str,
    mz: float,
    rt: tuple[float, ...],
    intensity: tuple[float, ...],
    integration: IntegrationResult,
    generation_provenance: PeakObservationGenerationProvenance,
    source_trace: Trace,
    physical_evaluation: PhysicalPeakEvaluation | None,
    signal_processing_profile: SignalProcessingProfile | None,
    capture_stage: str,
) -> PeakObservation:
    """Issue an observation from values normalized by the observation owner once."""

    observation = object.__new__(PeakObservation)
    object.__setattr__(observation, "sample_key", sample_key)
    object.__setattr__(observation, "mz", mz)
    object.__setattr__(observation, "rt", rt)
    object.__setattr__(observation, "intensity", intensity)
    object.__setattr__(observation, "integration", integration)
    object.__setattr__(observation, "generation_provenance", generation_provenance)
    object.__setattr__(observation, "source_trace", source_trace)
    object.__setattr__(observation, "physical_evaluation", physical_evaluation)
    object.__setattr__(
        observation, "signal_processing_profile", signal_processing_profile
    )
    object.__setattr__(observation, "capture_stage", capture_stage)
    return observation


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
    peaks: Iterable[PeakObservation],
) -> dict[PeakObservation, PeakObservation]:
    """Index peaks by physical identity; a later observation of one peak wins."""

    return {peak.physical_identity: peak for peak in peaks}


def same_selected_peak(left: PeakObservation, right: PeakObservation) -> bool:
    """Whether two observations selected the same integrated MS1 peak."""

    a = left.integration
    b = right.integration
    return (
        a.rt_apex_min == b.rt_apex_min
        and a.rt_left_min == b.rt_left_min
        and a.rt_right_min == b.rt_right_min
        and a.height_raw == b.height_raw
    )


__all__ = [
    "PeakObservation",
    "PeakObservationContractError",
    "PeakObservationGenerationProvenance",
    "index_physical_peaks",
    "mint_peak_observation",
    "same_selected_peak",
]
