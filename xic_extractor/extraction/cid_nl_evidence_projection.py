"""Targeted diagnostic projection over canonical CID-NL evidence."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.neutral_loss import MS2ProductEvidence
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceContractError,
    CidNlEvidenceRef,
    validate_cid_nl_evidence_binding,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "targeted_cid_nl_evidence_projection_v1"
ARTIFACT_SEMANTIC_ROLE = "targeted_cid_nl_evidence_projection"
VALIDATION_STATUS = "diagnostic_only"
_TARGET_KEY_PREFIX = "xic.target-key.v1:"
_TARGET_DEFINITION_PREFIX = "xic.target-definition.v1:"
_SAMPLE_ID_PREFIX = "xic.sample.v1:"
_EVIDENCE_ARTIFACT_ID_PREFIX = "xic.evidence-artifact.v1:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.extraction.cid_nl_evidence_projection",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf-v1",
)


class TargetedCidNlProjectionError(ValueError):
    """Raised when Targeted context does not match canonical CID-NL evidence."""


@dataclass(frozen=True, slots=True)
class TargetedCidNlProjectionRef:
    """No-write Targeted context bound to one canonical evidence artifact."""

    canonical_sample_identity_id: str
    target_key_id: str
    target_definition_id: str
    canonical_evidence_artifact_id: str
    artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        _validate_typed_id(
            self.canonical_sample_identity_id,
            prefix=_SAMPLE_ID_PREFIX,
            field="canonical_sample_identity_id",
        )
        _validate_typed_id(
            self.target_key_id,
            prefix=_TARGET_KEY_PREFIX,
            field="target_key_id",
        )
        _validate_typed_id(
            self.target_definition_id,
            prefix=_TARGET_DEFINITION_PREFIX,
            field="target_definition_id",
        )
        _validate_typed_id(
            self.canonical_evidence_artifact_id,
            prefix=_EVIDENCE_ARTIFACT_ID_PREFIX,
            field="canonical_evidence_artifact_id",
        )
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise TargetedCidNlProjectionError(
                "artifact must be an owner-minted EvidenceArtifactRef"
            )
        if self.artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
            raise TargetedCidNlProjectionError(
                "artifact semantic contract is not owned by Targeted projection"
            )
        if self.artifact.upstream_artifact_ids != (
            self.canonical_evidence_artifact_id,
        ):
            raise TargetedCidNlProjectionError(
                "Targeted projection must depend on exactly one canonical evidence ref"
            )
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                canonical_targeted_cid_nl_projection_bytes(self),
            )
        except ValueError as exc:
            raise TargetedCidNlProjectionError(
                "Targeted projection content mismatches its immutable artifact"
            ) from exc


def mint_targeted_cid_nl_projection_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
    canonical_evidence: CidNlEvidenceRef,
    targeted_evidence: MS2ProductEvidence,
) -> TargetedCidNlProjectionRef:
    """Bind existing Targeted evidence without making a product decision."""

    if not isinstance(target, ResolvedTargetDefinition):
        raise TargetedCidNlProjectionError(
            "target must be an owner-resolved target definition"
        )
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise TargetedCidNlProjectionError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if processing_owners.target_snapshot is None:
        raise TargetedCidNlProjectionError(
            "Targeted projection requires the request-bound target-list snapshot"
        )
    if target.snapshot.snapshot_id != processing_owners.target_snapshot.snapshot_id:
        raise TargetedCidNlProjectionError(
            "Targeted target definition comes from a different target-list snapshot"
        )
    if not isinstance(targeted_evidence, MS2ProductEvidence):
        raise TargetedCidNlProjectionError(
            "targeted_evidence must be MS2ProductEvidence"
        )
    try:
        validate_cid_nl_evidence_binding(
            canonical_evidence,
            processing_owners=processing_owners,
            sample=sample,
        )
    except CidNlEvidenceContractError as exc:
        raise TargetedCidNlProjectionError(str(exc)) from exc

    observation = canonical_evidence.observation
    if targeted_evidence.acquisition_source_id != observation.acquisition_source_id:
        raise TargetedCidNlProjectionError(
            "Targeted acquisition source mismatches canonical CID-NL evidence"
        )
    if targeted_evidence.scan_number != observation.scan_number:
        raise TargetedCidNlProjectionError(
            "Targeted scan_number mismatches canonical CID-NL evidence"
        )
    target_value = target.definition.target
    if target_value.neutral_loss_da is None:
        raise TargetedCidNlProjectionError(
            "Targeted projection requires a configured neutral loss"
        )
    precursor_error_ppm = (
        abs(targeted_evidence.precursor_mz - target_value.mz)
        / target_value.mz
        * 1_000_000.0
    )
    if precursor_error_ppm > target_value.ppm_tol:
        raise TargetedCidNlProjectionError(
            "Targeted scan precursor is outside the exact target precursor "
            "tolerance"
        )
    _require_close(
        target_value.neutral_loss_da,
        observation.configured_neutral_loss_da,
        field="configured neutral loss",
    )
    _require_close(
        targeted_evidence.precursor_mz,
        observation.precursor_mz,
        field="precursor_mz",
    )
    _require_close(
        targeted_evidence.product_mz,
        observation.product_mz,
        field="product_mz",
    )
    _require_close(
        targeted_evidence.product_intensity,
        observation.product_intensity,
        field="product_intensity",
    )
    _require_close(
        targeted_evidence.scan_rt,
        observation.scan_rt_min,
        field="scan_rt",
    )
    _require_close(
        targeted_evidence.observed_loss_da,
        observation.observed_neutral_loss_da,
        field="observed_loss_da",
    )
    _require_close(
        targeted_evidence.observed_loss_error_ppm,
        observation.observed_loss_error_ppm,
        field="observed_loss_error_ppm",
        abs_tol=1e-9,
    )
    target_product_mz = target_value.mz - target_value.neutral_loss_da
    if target_product_mz <= 0.0:
        raise TargetedCidNlProjectionError(
            "Targeted definition produces a non-positive product m/z"
        )
    expected_target_product_ppm = (
        abs(targeted_evidence.product_mz - target_product_mz)
        / target_product_mz
        * 1_000_000.0
    )
    _require_close(
        targeted_evidence.target_product_ppm,
        expected_target_product_ppm,
        field="target definition binding",
        abs_tol=1e-9,
    )

    projection = TargetedCidNlProjectionRef(
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        target_key_id=target.definition.target_key.target_key_id,
        target_definition_id=target.definition.target_definition_id,
        canonical_evidence_artifact_id=(
            canonical_evidence.artifact.evidence_artifact_id
        ),
        artifact=_mint_evidence_artifact_ref(
            producer_processing_request_id=(
                processing_owners.request.processing_request_id
            ),
            producer_processing_attempt_id=(
                processing_owners.attempt.processing_attempt_id
            ),
            semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
            payload_bytes=_targeted_projection_bytes(
                canonical_sample_identity_id=sample.identity.sample_identity_id,
                target_key_id=target.definition.target_key.target_key_id,
                target_definition_id=target.definition.target_definition_id,
                canonical_evidence_artifact_id=(
                    canonical_evidence.artifact.evidence_artifact_id
                ),
            ),
            upstream_artifacts=(canonical_evidence.artifact,),
        ),
    )
    return projection


def canonical_targeted_cid_nl_projection_bytes(
    reference: TargetedCidNlProjectionRef,
) -> bytes:
    """Return exact Targeted projection bytes for verification only."""

    if not isinstance(reference, TargetedCidNlProjectionRef):
        raise TargetedCidNlProjectionError(
            "canonical serialization requires TargetedCidNlProjectionRef"
        )
    return _targeted_projection_bytes(
        canonical_sample_identity_id=reference.canonical_sample_identity_id,
        target_key_id=reference.target_key_id,
        target_definition_id=reference.target_definition_id,
        canonical_evidence_artifact_id=reference.canonical_evidence_artifact_id,
    )


def _targeted_projection_bytes(
    *,
    canonical_sample_identity_id: str,
    target_key_id: str,
    target_definition_id: str,
    canonical_evidence_artifact_id: str,
) -> bytes:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "projection_policy": "targeted_observation_reference_only",
        "canonical_sample_identity_id": canonical_sample_identity_id,
        "target_key_id": target_key_id,
        "target_definition_id": target_definition_id,
        "canonical_evidence_artifact_id": canonical_evidence_artifact_id,
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


def _require_close(
    observed: float,
    expected: float,
    *,
    field: str,
    abs_tol: float = 1e-12,
) -> None:
    if not math.isclose(
        float(observed),
        float(expected),
        rel_tol=1e-12,
        abs_tol=abs_tol,
    ):
        raise TargetedCidNlProjectionError(
            f"Targeted {field} mismatches canonical CID-NL evidence"
        )


def _validate_typed_id(value: object, *, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise TargetedCidNlProjectionError(f"{field} must use the {prefix!r} prefix")
    if _SHA256_RE.fullmatch(value[len(prefix) :]) is None:
        raise TargetedCidNlProjectionError(f"{field} must contain a SHA-256 digest")
    return value


__all__ = [
    "ARTIFACT_SEMANTIC_ROLE",
    "SCHEMA_VERSION",
    "TargetedCidNlProjectionError",
    "TargetedCidNlProjectionRef",
    "canonical_targeted_cid_nl_projection_bytes",
    "mint_targeted_cid_nl_projection_ref",
]
