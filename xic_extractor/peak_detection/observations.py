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
    proposal_sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PeakObservation:
    """One sample-local MS1 signal, with an area only if it was resolved.

    Equality uses the measured signal only.  The generation provenance, source
    trace and evaluation are the context that produced it; the source trace is
    retained for in-memory physical-view comparison and neither changes the
    selected integral nor adds another persisted identity.
    """

    sample_key: str
    mz: float
    rt: tuple[float, ...]
    intensity: tuple[float, ...]
    integration: IntegrationResult | None
    generation_provenance: PeakObservationGenerationProvenance = field(compare=False)
    source_trace: Trace = field(compare=False)
    physical_evaluation: PhysicalPeakEvaluation | None = field(
        default=None, compare=False
    )
    capture_stage: str = field(default="initial_capture", compare=False)
    signal_processing_profile: SignalProcessingProfile | None = field(
        default=None, compare=False
    )
    background_contrast: float | None = field(default=None, compare=False)
    """Height over the median nearby bump of its channel; see background_contrast."""

    @property
    def physical_identity(self) -> PeakObservation:
        """This peak without advisory measurement quality, for in-process joins."""

        if self.integration is None or self.integration.signal_quality is None:
            return self
        return replace(self, integration=replace(self.integration, signal_quality=None))

    @property
    def rt_apex_min(self) -> float:
        assert self.integration is not None
        return self.integration.rt_apex_min

    @property
    def rt_left_min(self) -> float:
        return (
            self.integration.rt_left_min if self.integration is not None else self.rt[0]
        )

    @property
    def rt_right_min(self) -> float:
        return (
            self.integration.rt_right_min
            if self.integration is not None
            else self.rt[-1]
        )

    @property
    def height_raw(self) -> float:
        return (
            self.integration.height_raw
            if self.integration is not None
            else self.intensity[self.rt.index(self.rt_apex_min)]
        )

    @property
    def area_raw_counts_seconds(self) -> float | None:
        return (
            self.integration.area_raw_counts_seconds
            if self.integration is not None
            else None
        )

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
                repr(self.rt_apex_min),
                repr(self.height_raw),
            )
        )

    @property
    def measured_order_key(
        self,
    ) -> tuple[float, float, tuple[float, ...], tuple[float, ...]]:
        """Total order over distinct selected signals from raw measured values."""

        return (self.rt_apex_min, self.mz, self.rt, self.intensity)


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


def mint_physical_peak_observations(
    *,
    sample: SampleMetadata,
    trace: Trace,
    evaluations: tuple[PhysicalPeakEvaluation, ...],
    signal_processing_profile: SignalProcessingProfile,
    capture_stage: str = "initial_capture",
    trace_observation: PeakObservation | None = None,
    acquired_trace_provenance: PeakObservationGenerationProvenance | None = None,
    measured_mz: tuple[float, ...] | None = None,
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
            proposal_sources=("canonical_physical_segment",),
        )
    observations: list[PeakObservation] = []
    masses = (trace.mz,) * len(evaluations) if measured_mz is None else measured_mz
    for evaluation, mz in zip(evaluations, masses, strict=True):
        integration = physical_peak_observation_integration(evaluation)
        observation = _project_peak_observation(
            sample_key=sample.sample_name,
            trace=trace,
            mz=mz,
            integration=integration,
            selected_indices=integration.raw_scan_indices,
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
    mz: float,
    integration: IntegrationResult | None,
    selected_indices: tuple[int, ...],
    proposal_sources: tuple[str, ...],
    shared_trace_provenance: PeakObservationGenerationProvenance | None = None,
    physical_evaluation: PhysicalPeakEvaluation | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
    capture_stage: str = "initial_capture",
) -> PeakObservation:
    """Project signals and integration already validated by their input owner."""
    selected_rt = tuple(
        canonical_peak_value(float(trace.rt[index]), field="selected_rt")
        for index in selected_indices
    )
    selected_intensity = tuple(
        canonical_peak_value(float(trace.intensity[index]), field="selected_intensity")
        for index in selected_indices
    )
    normalized_integration = (
        replace(integration, raw_scan_indices=tuple(range(len(selected_indices))))
        if integration is not None
        else None
    )
    provenance = (
        replace(
            shared_trace_provenance,
            selected_trace_indices=selected_indices,
            proposal_sources=proposal_sources,
        )
        if shared_trace_provenance is not None
        else prepare_peak_observation_provenance(
            trace=trace,
            selected_indices=selected_indices,
            proposal_sources=proposal_sources,
        )
    )
    return PeakObservation(
        sample_key=sample_key,
        mz=float(mz),
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
        proposal_sources=tuple(sorted(set(proposal_sources))),
    )


def physical_peak_observation_integration(
    evaluation: PhysicalPeakEvaluation,
) -> IntegrationResult:
    """Bind the physical owner's raw descriptor to its exact selected samples."""
    segment = evaluation.answer.selected_segment
    integration = evaluation.integration
    if evaluation.state != "available" or segment is None or integration is None:
        raise PeakObservationContractError(
            "physical observation requires an available selected segment"
        )
    return replace(
        integration,
        raw_scan_indices=tuple(range(segment.start_index, segment.end_index)),
    )


def canonical_peak_value(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PeakObservationContractError(f"{field} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise PeakObservationContractError(f"{field} must be a finite number")
    return 0.0 if normalized == 0.0 else normalized


def index_physical_peaks(
    peaks: Iterable[PeakObservation],
) -> dict[PeakObservation, PeakObservation]:
    """Index peaks by physical identity; a later observation of one peak wins."""

    return {peak.physical_identity: peak for peak in peaks}


__all__ = [
    "PeakObservation",
    "PeakObservationContractError",
    "PeakObservationGenerationProvenance",
    "index_physical_peaks",
]
