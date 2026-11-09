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

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
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
    processing_attempt_id: str
    method_subject_id: str
    candidate_id: str | None
    capture_stage: str
    trace: Trace
    physical_evaluation: PhysicalPeakEvaluation | None
    observation_ref: PeakObservationRef | None = None


def mint_peak_observation_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    trace: Trace,
    integration: IntegrationResult,
    resolver_mode: str,
    proposal_sources: tuple[str, ...],
    physical_evaluation: PhysicalPeakEvaluation | None = None,
    signal_processing_profile: SignalProcessingProfile | None = None,
    capture_stage: str = "initial_capture",
) -> PeakObservationRef:
    """Project an already selected integration into sample-local peak content."""
    content, generation_provenance = _project_peak_observation_values(
        sample_key=sample.identity.sample_key,
        trace=trace,
        integration=integration,
        selected_indices=integration.raw_scan_indices,
        resolver_mode=resolver_mode,
        proposal_sources=proposal_sources,
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
    observations: list[PeakObservationRef] = []
    for evaluation in evaluations:
        integration = physical_peak_observation_integration(evaluation)
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
]
