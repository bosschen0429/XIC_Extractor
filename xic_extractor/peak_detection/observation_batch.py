"""Capability-bound batch acquisition into owner-backed peak observations.

This companion composes the existing acquisition and physical-observation
owners.  It accepts owner-supplied integration facts and validates them against
the acquired signal; it does not select peaks, interpret provider evidence, or
grant product authority.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace

import numpy as np

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    BatchXICAdapter,
    TraceAcquisitionBatch,
    TraceAcquisitionCapability,
    acquire_trace_batch,
)
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.observations import (
    PeakObservationRef,
    _canonical_trace_bytes,
    mint_peak_observation_ref,
)
from xic_extractor.peak_detection.traces import trace_from_xic_request
from xic_extractor.processing_identity import (
    IdentityContractError,
    ProcessingOwnerReferences,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.xic_models import XICRequest

SCHEMA_VERSION = "peak_observation_batch"
VALIDATION_STATUS = "diagnostic_only"


class PeakObservationBatchError(ValueError):
    """Raised when acquisition and physical observations do not close."""


@dataclass(frozen=True, slots=True)
class PeakObservationPlan:
    """Owner-supplied integration facts for exactly one acquisition request."""

    request: XICRequest
    integration: IntegrationResult
    resolver_mode: str
    proposal_sources: tuple[str, ...]
    selection_intensity: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request, XICRequest):
            raise PeakObservationBatchError("request must be an XICRequest")
        if not isinstance(self.integration, IntegrationResult):
            raise PeakObservationBatchError(
                "integration must be an owner-supplied IntegrationResult"
            )
        _required_text(self.resolver_mode, field="resolver_mode")
        if not isinstance(self.proposal_sources, tuple):
            raise PeakObservationBatchError("proposal_sources must be a tuple")
        normalized_sources = tuple(
            sorted(
                {
                    _required_text(value, field="proposal_source")
                    for value in self.proposal_sources
                }
            )
        )
        if normalized_sources != self.proposal_sources:
            raise PeakObservationBatchError(
                "proposal_sources must be unique and canonical-sorted"
            )
        if self.selection_intensity is not None:
            if not isinstance(self.selection_intensity, tuple) or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in self.selection_intensity
            ):
                raise PeakObservationBatchError(
                    "selection_intensity must contain finite numeric values"
                )
            object.__setattr__(
                self,
                "selection_intensity",
                tuple(float(value) for value in self.selection_intensity),
            )


@dataclass(frozen=True, slots=True)
class PeakObservationBatch:
    """One immutable acquisition receipt and its physical observations."""

    processing_request_id: str
    processing_attempt_id: str
    canonical_sample_identity_id: str
    acquisition: TraceAcquisitionBatch
    observations: tuple[PeakObservationRef, ...]
    _acquisition_trace_sha256: tuple[str, ...] = field(
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        _required_prefixed_id(
            self.processing_request_id,
            prefix="xic.processing-request:",
            field="processing_request_id",
        )
        _required_prefixed_id(
            self.processing_attempt_id,
            prefix="xic.processing-attempt:",
            field="processing_attempt_id",
        )
        _required_prefixed_id(
            self.canonical_sample_identity_id,
            prefix="xic.sample:",
            field="canonical_sample_identity_id",
        )
        if not isinstance(self.acquisition, TraceAcquisitionBatch):
            raise PeakObservationBatchError(
                "acquisition must be an owner-produced TraceAcquisitionBatch"
            )
        object.__setattr__(
            self,
            "acquisition",
            _freeze_acquisition(self.acquisition),
        )
        if not isinstance(self.observations, tuple) or any(
            not isinstance(observation, PeakObservationRef)
            for observation in self.observations
        ):
            raise PeakObservationBatchError(
                "observations must contain PeakObservationRef values"
            )
        if len(self.observations) != len(self.acquisition.traces):
            raise PeakObservationBatchError(
                "observations must match the acquired request cardinality"
            )
        object.__setattr__(
            self,
            "_acquisition_trace_sha256",
            tuple(
                _acquired_trace_sha256(self.acquisition.capability, acquired)
                for acquired in self.acquisition.traces
            ),
        )
        for acquired, observation in zip(
            self.acquisition.traces,
            self.observations,
            strict=True,
        ):
            _validate_observation_binding(self, acquired, observation)

    @property
    def observation_artifacts(self) -> tuple[EvidenceArtifactRef, ...]:
        return tuple(observation.artifact for observation in self.observations)


def produce_peak_observation_batch(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    capability: TraceAcquisitionCapability,
    plans: tuple[PeakObservationPlan, ...],
    adapter: BatchXICAdapter,
) -> PeakObservationBatch:
    """Acquire once, then validate owner-supplied integrations against signals."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise PeakObservationBatchError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise PeakObservationBatchError(
            "sample must be a resolved canonical sample identity"
        )
    if not isinstance(capability, TraceAcquisitionCapability):
        raise PeakObservationBatchError(
            "capability must be a TraceAcquisitionCapability"
        )
    if not isinstance(plans, tuple) or not plans or any(
        not isinstance(plan, PeakObservationPlan) for plan in plans
    ):
        raise PeakObservationBatchError(
            "plans must be a non-empty PeakObservationPlan tuple"
        )
    try:
        request_source = resolve_request_acquisition_source(
            processing_owners=processing_owners,
            sample=sample,
        )
    except IdentityContractError as exc:
        raise PeakObservationBatchError(str(exc)) from exc
    if capability.acquisition_source != request_source:
        raise PeakObservationBatchError(
            "capability acquisition source mismatches the sample request binding"
        )
    acquisition = acquire_trace_batch(
        capability,
        tuple(plan.request for plan in plans),
        adapter,
    )
    observations: list[PeakObservationRef] = []
    for plan, acquired in zip(plans, acquisition.traces, strict=True):
        if (
            plan.selection_intensity is not None
            and len(plan.selection_intensity) != len(acquired.trace.rt)
        ):
            raise PeakObservationBatchError(
                "selection_intensity must match the acquired trace length"
            )
        trace = trace_from_xic_request(
            sample_name=sample.metadata.sample_name,
            request=plan.request,
            xic_trace=acquired.trace,
            source=_trace_source(capability),
            acquisition_source_id=(
                capability.acquisition_source.acquisition_source_id
            ),
        )
        observations.append(
            mint_peak_observation_ref(
                processing_owners=processing_owners,
                sample=sample,
                trace=trace,
                integration=plan.integration,
                selection_intensity=plan.selection_intensity,
                resolver_mode=plan.resolver_mode,
                proposal_sources=plan.proposal_sources,
            )
        )
    return PeakObservationBatch(
        processing_request_id=processing_owners.request.processing_request_id,
        processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition=acquisition,
        observations=tuple(observations),
    )


def canonical_peak_observation_batch_bytes(batch: PeakObservationBatch) -> bytes:
    """Return the deterministic acquisition/observation closure receipt."""

    if not isinstance(batch, PeakObservationBatch):
        raise PeakObservationBatchError("batch must be a PeakObservationBatch")
    _validate_acquisition_snapshot(batch)
    acquisition = batch.acquisition
    payload = {
        "schema_version": SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "processing_request_id": batch.processing_request_id,
        "processing_attempt_id": batch.processing_attempt_id,
        "canonical_sample_identity_id": batch.canonical_sample_identity_id,
        "capability": {
            "capability_id": acquisition.capability.capability_id,
            "acquisition_source_id": (
                acquisition.capability.acquisition_source.acquisition_source_id
            ),
            "adapter_namespace": acquisition.capability.adapter_namespace,
            "coordinate_kind": acquisition.capability.coordinate_kind.value,
        },
        "counts": {
            "logical_xic_request_count": acquisition.logical_xic_request_count,
            "xic_batch_count": acquisition.xic_batch_count,
            "adapter_chromatogram_call_count": (
                acquisition.adapter_chromatogram_call_count
            ),
            "trace_point_count": acquisition.trace_point_count,
        },
        "items": [
            {
                "coordinate": {
                    "trace_coordinate_id": acquired.coordinate.trace_coordinate_id,
                    "coordinate_kind": acquired.coordinate.coordinate_kind.value,
                    "first_scan": acquired.coordinate.first_scan,
                    "last_scan": acquired.coordinate.last_scan,
                    "trace_point_count": acquired.coordinate.trace_point_count,
                    "request": _request_payload(acquired.coordinate.request),
                },
                "observation": {
                    "peak_observation_content_id": (
                        observation.content.peak_observation_content_id
                    ),
                    "evidence_artifact_id": observation.artifact.evidence_artifact_id,
                    "artifact_content_id": (
                        observation.artifact.content_identity.artifact_content_id
                    ),
                    "content_sha256": (
                        observation.artifact.content_identity.content_sha256
                    ),
                    "byte_size": observation.artifact.content_identity.byte_size,
                },
            }
            for acquired, observation in zip(
                acquisition.traces,
                batch.observations,
                strict=True,
            )
        ],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _validate_observation_binding(
    batch: PeakObservationBatch,
    acquired: AcquiredTrace,
    observation: PeakObservationRef,
) -> None:
    coordinate = acquired.coordinate
    request = coordinate.request
    artifact = observation.artifact
    if (
        artifact.producer_processing_request_id != batch.processing_request_id
        or artifact.producer_processing_attempt_id != batch.processing_attempt_id
    ):
        raise PeakObservationBatchError(
            "observation artifact references a different processing attempt"
        )
    if (
        observation.content.canonical_sample_identity_id
        != batch.canonical_sample_identity_id
    ):
        raise PeakObservationBatchError("observation references a different sample")
    source_id = batch.acquisition.capability.acquisition_source.acquisition_source_id
    provenance = observation.generation_provenance
    if provenance.acquisition_source_id != source_id:
        raise PeakObservationBatchError(
            "observation references a different acquisition source"
        )
    if (
        provenance.query_rt_min != request.rt_min
        or provenance.query_rt_max != request.rt_max
        or provenance.query_ppm_tol != request.ppm_tol
        or observation.content.mz != request.mz
    ):
        raise PeakObservationBatchError(
            "observation generation query mismatches the acquired request"
        )
    trace = trace_from_xic_request(
        sample_name="validation-only",
        request=request,
        xic_trace=acquired.trace,
        source=_trace_source(batch.acquisition.capability),
        acquisition_source_id=source_id,
    )
    if provenance.full_trace_sha256 != hashlib.sha256(
        _canonical_trace_bytes(trace)
    ).hexdigest():
        raise PeakObservationBatchError(
            "observation full trace mismatches the acquisition batch"
        )


def _freeze_acquisition(
    acquisition: TraceAcquisitionBatch,
) -> TraceAcquisitionBatch:
    frozen_traces: list[AcquiredTrace] = []
    for acquired in acquisition.traces:
        rt = np.array(acquired.trace.rt, dtype=float, copy=True)
        intensity = np.array(acquired.trace.intensity, dtype=float, copy=True)
        rt.setflags(write=False)
        intensity.setflags(write=False)
        frozen_traces.append(
            replace(
                acquired,
                trace=type(acquired.trace)(rt=rt, intensity=intensity),
            )
        )
    return replace(acquisition, traces=tuple(frozen_traces))


def _acquired_trace_sha256(
    capability: TraceAcquisitionCapability,
    acquired: AcquiredTrace,
) -> str:
    trace = trace_from_xic_request(
        sample_name="validation-only",
        request=acquired.coordinate.request,
        xic_trace=acquired.trace,
        source=_trace_source(capability),
        acquisition_source_id=(
            capability.acquisition_source.acquisition_source_id
        ),
    )
    return hashlib.sha256(_canonical_trace_bytes(trace)).hexdigest()


def _validate_acquisition_snapshot(batch: PeakObservationBatch) -> None:
    observed = tuple(
        _acquired_trace_sha256(batch.acquisition.capability, acquired)
        for acquired in batch.acquisition.traces
    )
    if observed != batch._acquisition_trace_sha256:
        raise PeakObservationBatchError(
            "acquisition trace changed after the immutable batch was minted"
        )


def _trace_source(capability: TraceAcquisitionCapability) -> str:
    return f"{capability.adapter_namespace}:{capability.coordinate_kind.value}"


def _request_payload(request: XICRequest) -> dict[str, float]:
    return {
        "mz": float(request.mz),
        "rt_min": float(request.rt_min),
        "rt_max": float(request.rt_max),
        "ppm_tol": float(request.ppm_tol),
    }


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PeakObservationBatchError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


def _required_prefixed_id(value: object, *, prefix: str, field: str) -> str:
    text = _required_text(value, field=field)
    if not text.startswith(prefix):
        raise PeakObservationBatchError(f"{field} must use the owner prefix")
    return text


__all__ = [
    "PeakObservationBatch",
    "PeakObservationBatchError",
    "PeakObservationPlan",
    "canonical_peak_observation_batch_bytes",
    "produce_peak_observation_batch",
]
