"""Owner-backed physical MS1 peak-observation references.

``peak_detection`` owns the measured trace region and integration facts.  The
reference keeps physical content equality separate from the exact generation
context and producer attempt.  It grants no hypothesis, selection, or product
authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, cast

import numpy as np

from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.peak_detection.baseline import (
    asls_baseline,
    bounded_trace_interval,
    integrate_with_baseline,
)
from xic_extractor.peak_detection.evidence_facts import (
    PhysicalPeakEvaluation,
    evaluate_physical_peak,
    prepare_physical_signal,
    validate_bounded_physical_peak_evaluation,
)
from xic_extractor.peak_detection.integration import integrate_area_counts_seconds
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.ms1_morphology import (
    MS1_MORPHOLOGY_AREA_SOURCE,
    MS1_MORPHOLOGY_TRACE_METHOD,
    gaussian15_positive_asls_residual_metrics,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    IdentityContractError,
    ProcessingOwnerReferences,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "ms1_peak_observation_v1"
ARTIFACT_SEMANTIC_ROLE = "canonical_ms1_peak_observation"
VALIDATION_STATUS = "diagnostic_only"
MEDIA_TYPE = "application/json"
SERIALIZATION_FORMAT = "canonical-json-utf8-lf-v1"

_CONTENT_ID_PREFIX = "xic.peak-observation-content.v1:"
_SAMPLE_ID_PREFIX = "xic.sample.v1:"
_ACQUISITION_SOURCE_ID_PREFIX = "xic.acquisition-source.v1:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_PAYLOAD_FIELDS = frozenset(
    {
        "peak_observation_content_id",
        "canonical_sample_identity_id",
        "mz",
        "rt",
        "intensity",
        "integration",
    }
)
_INTEGRATION_PAYLOAD_FIELDS = frozenset(
    field.name for field in fields(IntegrationResult)
)
_INTEGRATION_TEXT_FIELDS = frozenset(
    {
        "integration_method",
        "area_uncertainty_formula_version",
        "area_uncertainty_noise_source",
        "baseline_type",
        "ms1_morphology_area_source",
        "ms1_morphology_trace_method",
    }
)
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.peak_detection.observations",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)


class PeakObservationContractError(ValueError):
    """Raised when physical content or generation provenance is inconsistent."""


@dataclass(frozen=True, slots=True)
class PeakObservationContent:
    """Exact sample-local selected MS1 signal and integration content."""

    peak_observation_content_id: str
    canonical_sample_identity_id: str
    mz: float
    rt: tuple[float, ...]
    intensity: tuple[float, ...]
    integration: IntegrationResult

    def __post_init__(self) -> None:
        _validate_typed_digest_id(
            self.peak_observation_content_id,
            prefix=_CONTENT_ID_PREFIX,
            field="peak_observation_content_id",
        )
        _validate_typed_digest_id(
            self.canonical_sample_identity_id,
            prefix=_SAMPLE_ID_PREFIX,
            field="canonical_sample_identity_id",
        )
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
        expected_id = _content_id(
            canonical_sample_identity_id=self.canonical_sample_identity_id,
            mz=mz,
            rt=rt,
            intensity=intensity,
            integration=self.integration,
        )
        if self.peak_observation_content_id != expected_id:
            raise PeakObservationContractError(
                "peak_observation_content_id mismatches physical content"
            )


@dataclass(frozen=True, slots=True)
class _VerifiedPersistedPeakObservationClaims:
    """Raw-signal claims that the persisted selected subtrace can prove."""

    peak_observation_content_id: str
    canonical_sample_identity_id: str
    mz: float
    raw_apex_rt_min: float
    height_raw: float
    area_raw_counts_seconds: float


def verify_persisted_peak_observation_claims(
    payload: object,
) -> _VerifiedPersistedPeakObservationClaims:
    """Verify only claims supported by retained selected RT/intensity samples."""

    content = _exact_payload_mapping(
        payload,
        expected_fields=_CONTENT_PAYLOAD_FIELDS,
        field="peak observation content",
    )
    integration_payload = _exact_payload_mapping(
        content["integration"],
        expected_fields=_INTEGRATION_PAYLOAD_FIELDS,
        field="peak observation integration",
    )
    for field_name in _INTEGRATION_TEXT_FIELDS:
        if not isinstance(integration_payload[field_name], str):
            raise PeakObservationContractError(
                f"peak observation integration {field_name} must be text"
            )
    boundary_sources = _payload_json_array(
        integration_payload["boundary_sources"],
        field="peak observation integration boundary_sources",
    )
    if any(not isinstance(value, str) for value in boundary_sources):
        raise PeakObservationContractError(
            "peak observation integration boundary_sources must contain text"
        )
    raw_scan_indices = _payload_json_array(
        integration_payload["raw_scan_indices"],
        field="peak observation integration raw_scan_indices",
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in raw_scan_indices
    ):
        raise PeakObservationContractError(
            "peak observation integration raw_scan_indices must contain integers"
        )
    integration_values = dict(integration_payload)
    integration_values["boundary_sources"] = boundary_sources
    integration_values["raw_scan_indices"] = raw_scan_indices
    try:
        integration = IntegrationResult(**cast(dict[str, Any], integration_values))
        content_id = _required_text(
            content["peak_observation_content_id"],
            field="peak_observation_content_id",
        )
        _validate_typed_digest_id(
            content_id,
            prefix=_CONTENT_ID_PREFIX,
            field="peak_observation_content_id",
        )
        sample_id = _required_text(
            content["canonical_sample_identity_id"],
            field="canonical_sample_identity_id",
        )
        _validate_typed_digest_id(
            sample_id,
            prefix=_SAMPLE_ID_PREFIX,
            field="canonical_sample_identity_id",
        )
        mz = _finite_float(content["mz"], field="mz")
        if mz <= 0.0:
            raise PeakObservationContractError("mz must be positive")
        rt = _finite_float_tuple(
            _payload_json_array(content["rt"], field="rt"),
            field="rt",
        )
        intensity = _finite_float_tuple(
            _payload_json_array(content["intensity"], field="intensity"),
            field="intensity",
        )
        if not rt or len(rt) != len(intensity):
            raise PeakObservationContractError(
                "selected rt and intensity must be matching non-empty tuples"
            )
        if any(right <= left for left, right in zip(rt, rt[1:], strict=False)):
            raise PeakObservationContractError(
                "selected rt values must be strictly increasing"
            )
        if integration.raw_scan_indices != tuple(range(len(rt))):
            raise PeakObservationContractError(
                "content integration indices must address the selected subtrace"
            )
        _validate_integration(integration, selected_rt=rt)
        expected_id = _content_id(
            canonical_sample_identity_id=sample_id,
            mz=mz,
            rt=rt,
            intensity=intensity,
            integration=integration,
        )
        if content_id != expected_id:
            raise PeakObservationContractError(
                "peak_observation_content_id mismatches physical content"
            )

        raw_apex_index = int(np.argmax(np.asarray(intensity, dtype=float)))
        raw_apex_rt_min = rt[raw_apex_index]
        height_raw = intensity[raw_apex_index]
        area_raw_counts_seconds = integrate_area_counts_seconds(
            np.asarray(intensity, dtype=float),
            np.asarray(rt, dtype=float),
            0,
            len(rt),
        )
        _require_close(
            integration.raw_apex_rt_min,
            raw_apex_rt_min,
            field="raw_apex_rt_min",
        )
        _require_close(integration.height_raw, height_raw, field="height_raw")
        _require_close(
            integration.area_raw_counts_seconds,
            area_raw_counts_seconds,
            field="area_raw_counts_seconds",
        )
        return _VerifiedPersistedPeakObservationClaims(
            peak_observation_content_id=content_id,
            canonical_sample_identity_id=sample_id,
            mz=mz,
            raw_apex_rt_min=raw_apex_rt_min,
            height_raw=height_raw,
            area_raw_counts_seconds=area_raw_counts_seconds,
        )
    except PeakObservationContractError:
        raise
    except (TypeError, ValueError) as exc:
        raise PeakObservationContractError(
            "persisted peak observation content is malformed"
        ) from exc


@dataclass(frozen=True, slots=True)
class PeakObservationGenerationProvenance:
    """Exact trace/query and peak-generation context for one observation ref."""

    full_trace_sha256: str
    selection_trace_sha256: str
    acquisition_source_id: str
    query_rt_min: float
    query_rt_max: float
    query_ppm_tol: float
    trace_source: str
    selected_trace_indices: tuple[int, ...]
    resolver_mode: str
    proposal_sources: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_sha256(self.full_trace_sha256, field="full_trace_sha256")
        _validate_sha256(
            self.selection_trace_sha256,
            field="selection_trace_sha256",
        )
        _validate_typed_digest_id(
            self.acquisition_source_id,
            prefix=_ACQUISITION_SOURCE_ID_PREFIX,
            field="acquisition_source_id",
        )
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
    """Physical content plus exact generation provenance and attempt artifact."""

    content: PeakObservationContent
    generation_provenance: PeakObservationGenerationProvenance
    artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        if not isinstance(self.content, PeakObservationContent):
            raise PeakObservationContractError(
                "content must be owner-normalized peak observation content"
            )
        if not isinstance(
            self.generation_provenance,
            PeakObservationGenerationProvenance,
        ):
            raise PeakObservationContractError(
                "generation_provenance must be owner-normalized"
            )
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise PeakObservationContractError(
                "artifact must be an owner-minted EvidenceArtifactRef"
            )
        if self.artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
            raise PeakObservationContractError(
                "artifact semantic contract is not owned by peak observations"
            )
        if self.artifact.upstream_artifact_ids:
            raise PeakObservationContractError(
                "peak observation must not invent artifact dependencies"
            )
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                canonical_peak_observation_ref_bytes(
                    self.content,
                    self.generation_provenance,
                ),
            )
        except ValueError as exc:
            raise PeakObservationContractError(
                "peak observation mismatches its immutable artifact"
            ) from exc


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
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        trace=trace,
        integration=integration,
        selection_intensity=selection_intensity,
        resolver_mode=resolver_mode,
        proposal_sources=proposal_sources,
        physical_evaluation=physical_evaluation,
        signal_processing_profile=signal_processing_profile,
    )
    return _mint_peak_observation_artifact(
        processing_owners=processing_owners,
        content=content,
        generation_provenance=generation_provenance,
    )


def _validate_peak_observation_inputs(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    trace: Trace,
    integration: IntegrationResult,
) -> None:
    _validate_processing_owner_sample(processing_owners, sample)
    if not isinstance(trace, Trace):
        raise PeakObservationContractError("trace must be an owner-produced Trace")
    if trace.sample_name not in {
        sample.metadata.sample_name,
        sample.metadata.raw_stem,
        sample.lookup_token,
    }:
        raise PeakObservationContractError("Trace references a different sample")
    acquisition_source = _request_acquisition_source(processing_owners, sample)
    if trace.acquisition_source_id != acquisition_source.acquisition_source_id:
        raise PeakObservationContractError(
            "Trace acquisition source mismatches the request binding"
        )
    if not isinstance(integration, IntegrationResult):
        raise PeakObservationContractError(
            "integration must be an IntegrationResult value"
        )


def _mint_peak_observation_artifact(
    *,
    processing_owners: ProcessingOwnerReferences,
    content: PeakObservationContent,
    generation_provenance: PeakObservationGenerationProvenance,
) -> PeakObservationRef:
    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=processing_owners.request.processing_request_id,
        producer_processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=canonical_peak_observation_ref_bytes(
            content,
            generation_provenance,
        ),
    )
    return PeakObservationRef(
        content=content,
        generation_provenance=generation_provenance,
        artifact=artifact,
    )


def validate_peak_observation_trace_binding(
    reference: PeakObservationRef,
    *,
    canonical_sample_identity_id: str,
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
        canonical_sample_identity_id=canonical_sample_identity_id,
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
    canonical_sample_identity_id: str,
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
        canonical_sample_identity_id=canonical_sample_identity_id,
        trace=trace,
        integration=integration,
        selection_values=selection_values,
        selected_indices=selected_indices,
        resolver_mode=resolver_mode,
        proposal_sources=proposal_sources,
    )


def _project_peak_observation_values(
    *,
    canonical_sample_identity_id: str,
    trace: Trace,
    integration: IntegrationResult,
    selection_values: np.ndarray,
    selected_indices: tuple[int, ...],
    resolver_mode: str,
    proposal_sources: tuple[str, ...],
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
    content_basis = _physical_content_basis(
        canonical_sample_identity_id=canonical_sample_identity_id,
        mz=float(trace.mz),
        rt=selected_rt,
        intensity=selected_intensity,
        integration=normalized_integration,
    )
    return (
        PeakObservationContent(
            peak_observation_content_id=_typed_digest(
                _CONTENT_ID_PREFIX,
                content_basis,
            ),
            canonical_sample_identity_id=canonical_sample_identity_id,
            mz=float(trace.mz),
            rt=selected_rt,
            intensity=selected_intensity,
            integration=normalized_integration,
        ),
        PeakObservationGenerationProvenance(
            full_trace_sha256=hashlib.sha256(_canonical_trace_bytes(trace)).hexdigest(),
            selection_trace_sha256=hashlib.sha256(
                _canonical_selection_trace_bytes(trace, selection_values)
            ).hexdigest(),
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
        ),
    )


def canonical_peak_observation_ref_bytes(
    content: PeakObservationContent,
    generation_provenance: PeakObservationGenerationProvenance,
) -> bytes:
    """Return canonical bytes for the exact content/provenance envelope."""

    if not isinstance(content, PeakObservationContent):
        raise PeakObservationContractError(
            "content must be owner-normalized peak observation content"
        )
    if not isinstance(
        generation_provenance,
        PeakObservationGenerationProvenance,
    ):
        raise PeakObservationContractError(
            "generation_provenance must be owner-normalized"
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "content": _content_payload(content),
        "generation_provenance": asdict(generation_provenance),
    }
    return _canonical_json_bytes(payload)


def validate_peak_observation_binding(
    reference: PeakObservationRef,
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> None:
    """Reject processing-request, attempt, or canonical-sample substitution."""

    if not isinstance(reference, PeakObservationRef):
        raise PeakObservationContractError(
            "reference must be an owner-minted PeakObservationRef"
        )
    _validate_processing_owner_sample(processing_owners, sample)
    acquisition_source = _request_acquisition_source(processing_owners, sample)
    artifact = reference.artifact
    if (
        artifact.producer_processing_request_id
        != processing_owners.request.processing_request_id
    ):
        raise PeakObservationContractError(
            "peak observation references a different processing request"
        )
    if (
        artifact.producer_processing_attempt_id
        != processing_owners.attempt.processing_attempt_id
    ):
        raise PeakObservationContractError(
            "peak observation references a different processing attempt"
        )
    if (
        reference.content.canonical_sample_identity_id
        != sample.identity.sample_identity_id
    ):
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


def _canonical_trace_bytes(trace: Trace) -> bytes:
    return _canonical_json_bytes(
        {
            "schema": "ms1_trace_generation_provenance_v1",
            "mz": _finite_float(float(trace.mz), field="trace.mz"),
            "rt": [_finite_float(float(value), field="trace.rt") for value in trace.rt],
            "intensity": [
                _finite_float(float(value), field="trace.intensity")
                for value in trace.intensity
            ],
            "query_rt_min": _finite_float(float(trace.rt_min), field="trace.rt_min"),
            "query_rt_max": _finite_float(float(trace.rt_max), field="trace.rt_max"),
            "query_ppm_tol": _finite_float(float(trace.ppm_tol), field="trace.ppm_tol"),
            "trace_source": trace.source,
            "acquisition_source_id": trace.acquisition_source_id,
        }
    )


def _canonical_selection_trace_bytes(
    trace: Trace,
    selection_intensity: np.ndarray,
) -> bytes:
    return _canonical_json_bytes(
        {
            "schema": "ms1_peak_selection_trace_v1",
            "rt": [
                _finite_float(float(value), field="selection.rt") for value in trace.rt
            ],
            "intensity": [
                _finite_float(float(value), field="selection.intensity")
                for value in selection_intensity
            ],
        }
    )


def _content_id(
    *,
    canonical_sample_identity_id: str,
    mz: float,
    rt: tuple[float, ...],
    intensity: tuple[float, ...],
    integration: IntegrationResult,
) -> str:
    return _typed_digest(
        _CONTENT_ID_PREFIX,
        _physical_content_basis(
            canonical_sample_identity_id=canonical_sample_identity_id,
            mz=mz,
            rt=rt,
            intensity=intensity,
            integration=integration,
        ),
    )


def _physical_content_basis(
    *,
    canonical_sample_identity_id: str,
    mz: float,
    rt: tuple[float, ...],
    intensity: tuple[float, ...],
    integration: IntegrationResult,
) -> dict[str, object]:
    return {
        "schema": "ms1_peak_observation_content_v1",
        "canonical_sample_identity_id": canonical_sample_identity_id,
        "mz": mz,
        "rt": list(rt),
        "intensity": list(intensity),
        "integration": asdict(integration),
    }


def _content_payload(content: PeakObservationContent) -> dict[str, object]:
    return {
        "peak_observation_content_id": content.peak_observation_content_id,
        "canonical_sample_identity_id": content.canonical_sample_identity_id,
        "mz": content.mz,
        "rt": list(content.rt),
        "intensity": list(content.intensity),
        "integration": asdict(content.integration),
    }


def _exact_payload_mapping(
    value: object,
    *,
    expected_fields: frozenset[str],
    field: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise PeakObservationContractError(f"{field} must be a JSON object")
    normalized = dict(value)
    actual_fields = frozenset(normalized)
    if actual_fields != expected_fields:
        missing = ", ".join(sorted(expected_fields - actual_fields))
        unexpected = ", ".join(sorted(actual_fields - expected_fields))
        detail = "; ".join(
            item
            for item in (
                f"missing fields: {missing}" if missing else "",
                f"unexpected fields: {unexpected}" if unexpected else "",
            )
            if item
        )
        raise PeakObservationContractError(f"{field} fields changed: {detail}")
    return normalized


def _payload_json_array(value: object, *, field: str) -> tuple[object, ...]:
    if not isinstance(value, list):
        raise PeakObservationContractError(f"{field} must be a JSON array")
    return tuple(value)


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
    _canonical_json_bytes(asdict(integration))


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
    validate_bounded_physical_peak_evaluation(evaluation)
    facts = evaluation.facts
    integration = evaluation.integration
    if evaluation.state != "available" or facts is None or integration is None:
        raise PeakObservationContractError(
            "physical observation requires available facts"
        )
    segment = next(
        segment
        for segment in evaluation.segment_universe.segments
        if segment.physical_segment_id == facts.physical_segment_id
    )
    return replace(
        integration,
        height_smoothed=facts.height,
        raw_scan_indices=tuple(range(segment.start_index, segment.end_index)),
        boundary_sources=(*integration.boundary_sources, facts.physical_segment_id),
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
        trace_content_id=physical_evaluation.prepared.trace_content_id,
    )
    actual = evaluate_physical_peak(
        prepared,
        selection_subject_id=physical_evaluation.question.selection_subject_id,
        query_rt_min=physical_evaluation.question.query_rt_min,
        request_context_rt_start_min=trace.rt_min,
        request_context_rt_end_min=trace.rt_max,
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


def _validate_processing_owner_sample(
    processing_owners: object,
    sample: object,
) -> None:
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise PeakObservationContractError(
            "processing_owners must be a validated ProcessingOwnerReferences"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise PeakObservationContractError(
            "sample must be an owner-resolved canonical sample"
        )
    if sample not in processing_owners.sample_resolutions:
        raise PeakObservationContractError(
            "sample references a different sample outside the processing owner closure"
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


def _typed_digest(prefix: str, basis: dict[str, object]) -> str:
    return f"{prefix}{hashlib.sha256(_canonical_json_bytes(basis)).hexdigest()}"


def _canonical_json_bytes(value: object) -> bytes:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PeakObservationContractError(
            "peak observation payload must be canonical JSON"
        ) from exc
    return (serialized + "\n").encode("utf-8")


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PeakObservationContractError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


def _validate_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise PeakObservationContractError(
            f"{field} must be a lower-case SHA-256 digest"
        )
    return value


def _validate_typed_digest_id(value: object, *, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise PeakObservationContractError(f"{field} must use the {prefix!r} prefix")
    _validate_sha256(value[len(prefix) :], field=field)
    return value


__all__ = [
    "ARTIFACT_SEMANTIC_ROLE",
    "PeakObservationContent",
    "PeakObservationContractError",
    "PeakObservationGenerationProvenance",
    "PeakObservationRef",
    "SCHEMA_VERSION",
    "canonical_peak_observation_ref_bytes",
    "mint_peak_observation_ref",
    "validate_peak_observation_binding",
    "validate_peak_observation_trace_binding",
    "verify_persisted_peak_observation_claims",
]
