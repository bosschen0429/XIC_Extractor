"""Canonical no-write CID-NL evidence references for the bounded tracer.

The owner freezes only scientific fields shared by Targeted product evidence
and Untargeted Discovery seeds.  Workflow constraints, candidate identity,
selection, and delivery authority remain in their respective adapters.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.processing_identity import (
    ProcessingAttemptIdentity,
    ProcessingOwnerReferences,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "cid_nl_evidence_observation_v2"
ARTIFACT_SEMANTIC_ROLE = "canonical_cid_nl_observation"
VALIDATION_STATUS = "diagnostic_only"
MEDIA_TYPE = "application/json"
SERIALIZATION_FORMAT = "canonical-json-utf8-lf-v1"
ACQUISITION_MODE = "dda_ddms2"

_SAMPLE_ID_PREFIX = "xic.sample.v1:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.peak_detection.cid_nl_evidence",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)


class CidNlEvidenceContractError(ValueError):
    """Raised when canonical CID-NL evidence is forged or rebound."""


@dataclass(frozen=True, slots=True)
class CidNlEvidenceObservation:
    """The measured neutral-loss facts shared by both workflow adapters."""

    canonical_sample_identity_id: str
    acquisition_mode: str
    scan_number: int
    precursor_mz: float
    product_mz: float
    configured_neutral_loss_da: float
    observed_neutral_loss_da: float
    observed_loss_error_ppm: float
    scan_rt_min: float
    product_intensity: float

    def __post_init__(self) -> None:
        _validate_typed_id(
            self.canonical_sample_identity_id,
            prefix=_SAMPLE_ID_PREFIX,
            field="canonical_sample_identity_id",
        )
        if self.acquisition_mode != ACQUISITION_MODE:
            raise CidNlEvidenceContractError(
                f"acquisition_mode must be {ACQUISITION_MODE!r}"
            )
        if (
            isinstance(self.scan_number, bool)
            or not isinstance(self.scan_number, int)
            or self.scan_number <= 0
        ):
            raise CidNlEvidenceContractError("scan_number must be a positive integer")
        for field in (
            "precursor_mz",
            "product_mz",
            "configured_neutral_loss_da",
            "observed_neutral_loss_da",
            "observed_loss_error_ppm",
            "scan_rt_min",
            "product_intensity",
        ):
            object.__setattr__(
                self,
                field,
                _finite_float(getattr(self, field), field=field),
            )
        if self.precursor_mz <= 0.0 or self.product_mz <= 0.0:
            raise CidNlEvidenceContractError(
                "precursor_mz and product_mz must be positive"
            )
        if self.configured_neutral_loss_da <= 0.0:
            raise CidNlEvidenceContractError(
                "configured_neutral_loss_da must be positive"
            )
        if self.observed_neutral_loss_da <= 0.0:
            raise CidNlEvidenceContractError(
                "observed_neutral_loss_da must be positive"
            )
        if self.observed_loss_error_ppm < 0.0:
            raise CidNlEvidenceContractError(
                "observed_loss_error_ppm must be non-negative"
            )
        if self.scan_rt_min < 0.0:
            raise CidNlEvidenceContractError("scan_rt_min must be non-negative")
        if self.product_intensity <= 0.0:
            raise CidNlEvidenceContractError("product_intensity must be positive")

        measured_loss = self.precursor_mz - self.product_mz
        if not math.isclose(
            self.observed_neutral_loss_da,
            measured_loss,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise CidNlEvidenceContractError(
                "observed_neutral_loss_da must equal precursor_mz - product_mz"
            )
        expected_error_ppm = (
            abs(measured_loss - self.configured_neutral_loss_da)
            / self.configured_neutral_loss_da
            * 1_000_000.0
        )
        if not math.isclose(
            self.observed_loss_error_ppm,
            expected_error_ppm,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise CidNlEvidenceContractError(
                "observed_loss_error_ppm mismatches measured neutral loss"
            )


@dataclass(frozen=True, slots=True)
class CidNlEvidenceRef:
    """Owner-minted observation plus its immutable attempt artifact."""

    observation: CidNlEvidenceObservation
    artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        if not isinstance(self.observation, CidNlEvidenceObservation):
            raise CidNlEvidenceContractError(
                "observation must be owner-normalized CID-NL evidence"
            )
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise CidNlEvidenceContractError(
                "artifact must be an owner-minted EvidenceArtifactRef"
            )
        if self.artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
            raise CidNlEvidenceContractError(
                "artifact semantic contract is not owned by canonical CID-NL evidence"
            )
        if self.artifact.upstream_artifact_ids:
            raise CidNlEvidenceContractError(
                "canonical CID-NL observation must not invent artifact dependencies"
            )
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                canonical_cid_nl_evidence_bytes(self.observation),
            )
        except ValueError as exc:
            raise CidNlEvidenceContractError(
                "canonical CID-NL observation mismatches its immutable artifact"
            ) from exc


def mint_cid_nl_evidence_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    scan_number: int,
    precursor_mz: float,
    product_mz: float,
    configured_neutral_loss_da: float,
    observed_neutral_loss_da: float,
    observed_loss_error_ppm: float,
    scan_rt_min: float,
    product_intensity: float,
) -> CidNlEvidenceRef:
    """Mint one exact measured observation without reading an acquisition source."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise CidNlEvidenceContractError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise CidNlEvidenceContractError(
            "sample must be an owner-resolved canonical sample"
        )
    if sample not in processing_owners.sample_resolutions:
        raise CidNlEvidenceContractError(
            "sample references a different sample outside the processing owner closure"
        )
    observation = CidNlEvidenceObservation(
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        acquisition_mode=ACQUISITION_MODE,
        scan_number=scan_number,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        configured_neutral_loss_da=configured_neutral_loss_da,
        observed_neutral_loss_da=observed_neutral_loss_da,
        observed_loss_error_ppm=observed_loss_error_ppm,
        scan_rt_min=scan_rt_min,
        product_intensity=product_intensity,
    )
    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=processing_owners.request.processing_request_id,
        producer_processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=canonical_cid_nl_evidence_bytes(observation),
    )
    return CidNlEvidenceRef(observation=observation, artifact=artifact)


def canonical_cid_nl_evidence_bytes(
    observation: CidNlEvidenceObservation,
) -> bytes:
    """Return the canonical exact bytes owned by the shared evidence module."""

    if not isinstance(observation, CidNlEvidenceObservation):
        raise CidNlEvidenceContractError(
            "canonical serialization requires CidNlEvidenceObservation"
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "canonical_sample_identity_id": observation.canonical_sample_identity_id,
        "acquisition_mode": observation.acquisition_mode,
        "scan_number": observation.scan_number,
        "precursor_mz": observation.precursor_mz,
        "product_mz": observation.product_mz,
        "configured_neutral_loss_da": observation.configured_neutral_loss_da,
        "observed_neutral_loss_da": observation.observed_neutral_loss_da,
        "observed_loss_error_ppm": observation.observed_loss_error_ppm,
        "scan_rt_min": observation.scan_rt_min,
        "product_intensity": observation.product_intensity,
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def validate_cid_nl_evidence_binding(
    reference: CidNlEvidenceRef,
    *,
    processing_attempt: ProcessingAttemptIdentity,
    sample: ResolvedCanonicalSampleIdentity,
) -> None:
    """Reject sample, request, attempt, or semantic-contract substitution."""

    if not isinstance(reference, CidNlEvidenceRef):
        raise CidNlEvidenceContractError(
            "reference must be an owner-minted CidNlEvidenceRef"
        )
    if not isinstance(processing_attempt, ProcessingAttemptIdentity):
        raise CidNlEvidenceContractError(
            "processing_attempt must be an owner-issued ProcessingAttemptIdentity"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise CidNlEvidenceContractError(
            "sample must be an owner-resolved canonical sample"
        )
    artifact = reference.artifact
    if (
        artifact.producer_processing_request_id
        != processing_attempt.processing_request_id
    ):
        raise CidNlEvidenceContractError(
            "canonical CID-NL evidence references a different processing request"
        )
    if (
        artifact.producer_processing_attempt_id
        != processing_attempt.processing_attempt_id
    ):
        raise CidNlEvidenceContractError(
            "canonical CID-NL evidence references a different processing attempt"
        )
    if (
        reference.observation.canonical_sample_identity_id
        != sample.identity.sample_identity_id
    ):
        raise CidNlEvidenceContractError(
            "canonical CID-NL evidence references a different sample"
        )


def _finite_float(value: float, *, field: str) -> float:
    if isinstance(value, bool):
        raise CidNlEvidenceContractError(f"{field} must be a finite number")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise CidNlEvidenceContractError(f"{field} must be a finite number") from exc
    if not math.isfinite(normalized):
        raise CidNlEvidenceContractError(f"{field} must be a finite number")
    return normalized


def _validate_typed_id(value: object, *, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise CidNlEvidenceContractError(f"{field} must use the {prefix!r} prefix")
    if _SHA256_RE.fullmatch(value[len(prefix) :]) is None:
        raise CidNlEvidenceContractError(f"{field} must contain a SHA-256 digest")
    return value


__all__ = [
    "ACQUISITION_MODE",
    "ARTIFACT_SEMANTIC_ROLE",
    "CidNlEvidenceContractError",
    "CidNlEvidenceObservation",
    "CidNlEvidenceRef",
    "SCHEMA_VERSION",
    "canonical_cid_nl_evidence_bytes",
    "mint_cid_nl_evidence_ref",
    "validate_cid_nl_evidence_binding",
]
