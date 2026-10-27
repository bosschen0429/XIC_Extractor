"""Immutable evidence-artifact references for no-write lineage composition.

This module owns only workflow-neutral content and reference value objects.
Producer modules own semantic role/schema selection and call the public mint
helpers through narrow owner APIs. Mint helpers validate inputs; immutable
references carry their results without repeating payload validation.
Storage, runtime lifecycle, delivery, and publication authority deliberately
remain outside this contract.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.validation_primitives import (
    require_lower_sha256,
    require_owner_normalized_text,
)


@dataclass(frozen=True, slots=True)
class EvidenceLocator:
    """Immutable provenance locator pointing to a completed evidence DB."""

    report_path: Path
    matrix_path: Path
    matrix_sha256: str
    processing_request_id: str
    processing_attempt_id: str
    schema_version: str


class EvidenceArtifactContractError(ValueError):
    """Raised when an immutable evidence-artifact reference is inconsistent."""


_ARTIFACT_CONTENT_ID_PREFIX = "xic.artifact-content:"
_ARTIFACT_SEMANTIC_CONTRACT_ID_PREFIX = "xic.artifact-semantic-contract:"
_EVIDENCE_ARTIFACT_ID_PREFIX = "xic.evidence-artifact:"
_PROCESSING_REQUEST_ID_PREFIX = "xic.processing-request:"
_PROCESSING_ATTEMPT_ID_PREFIX = "xic.processing-attempt:"


@dataclass(frozen=True, slots=True)
class ArtifactSemanticContractRef:
    """Producer-owned fixed meaning for one artifact role and schema."""

    semantic_contract_id: str
    owner_namespace: str
    semantic_role: str
    schema_version: str
    validation_status: str
    media_type: str
    serialization_format: str

    def __post_init__(self) -> None:
        semantic_contract_id = _validate_typed_digest_id(
            self.semantic_contract_id,
            prefix=_ARTIFACT_SEMANTIC_CONTRACT_ID_PREFIX,
            field="semantic_contract_id",
        )
        basis = _semantic_contract_basis(
            owner_namespace=_required_text(
                self.owner_namespace,
                field="owner_namespace",
            ),
            semantic_role=_required_text(
                self.semantic_role,
                field="semantic_role",
            ),
            schema_version=_required_text(
                self.schema_version,
                field="schema_version",
            ),
            validation_status=_required_text(
                self.validation_status,
                field="validation_status",
            ),
            media_type=_required_text(self.media_type, field="media_type"),
            serialization_format=_required_text(
                self.serialization_format,
                field="serialization_format",
            ),
        )
        expected_id = _typed_digest(
            _ARTIFACT_SEMANTIC_CONTRACT_ID_PREFIX,
            basis,
        )
        if semantic_contract_id != expected_id:
            raise EvidenceArtifactContractError(
                "semantic_contract_id mismatches producer-owned role/schema content"
            )


@dataclass(frozen=True, slots=True)
class ArtifactContentIdentity:
    """Exact-byte identity independent of path and producer attempt."""

    artifact_content_id: str
    content_sha256: str
    byte_size: int
    media_type: str
    serialization_format: str


@dataclass(frozen=True, slots=True)
class EvidenceArtifactRef:
    """One immutable producer-attempt artifact instance."""

    evidence_artifact_id: str
    producer_processing_request_id: str
    producer_processing_attempt_id: str
    semantic_contract: ArtifactSemanticContractRef
    content_identity: ArtifactContentIdentity
    upstream_artifact_ids: tuple[str, ...] = ()


def mint_artifact_semantic_contract_ref(
    *,
    owner_namespace: str,
    semantic_role: str,
    schema_version: str,
    validation_status: str,
    media_type: str,
    serialization_format: str,
) -> ArtifactSemanticContractRef:
    """Mint one fixed producer contract; producer modules own all arguments."""

    basis = _semantic_contract_basis(
        owner_namespace=_required_text(owner_namespace, field="owner_namespace"),
        semantic_role=_required_text(semantic_role, field="semantic_role"),
        schema_version=_required_text(schema_version, field="schema_version"),
        validation_status=_required_text(
            validation_status,
            field="validation_status",
        ),
        media_type=_required_text(media_type, field="media_type"),
        serialization_format=_required_text(
            serialization_format,
            field="serialization_format",
        ),
    )
    return ArtifactSemanticContractRef(
        semantic_contract_id=_typed_digest(
            _ARTIFACT_SEMANTIC_CONTRACT_ID_PREFIX,
            basis,
        ),
        owner_namespace=owner_namespace,
        semantic_role=semantic_role,
        schema_version=schema_version,
        validation_status=validation_status,
        media_type=media_type,
        serialization_format=serialization_format,
    )


def mint_evidence_artifact_ref(
    *,
    producer_processing_request_id: str,
    producer_processing_attempt_id: str,
    semantic_contract: ArtifactSemanticContractRef,
    payload_bytes: bytes,
    upstream_artifacts: Sequence[EvidenceArtifactRef] = (),
) -> EvidenceArtifactRef:
    """Compose an owner-selected contract with exact bytes and attempt identity."""

    request_id = _validate_typed_digest_id(
        producer_processing_request_id,
        prefix=_PROCESSING_REQUEST_ID_PREFIX,
        field="producer_processing_request_id",
    )
    attempt_id = _validate_typed_digest_id(
        producer_processing_attempt_id,
        prefix=_PROCESSING_ATTEMPT_ID_PREFIX,
        field="producer_processing_attempt_id",
    )
    if not isinstance(semantic_contract, ArtifactSemanticContractRef):
        raise EvidenceArtifactContractError("semantic_contract must be producer-owned")
    if isinstance(upstream_artifacts, (str, bytes)):
        raise EvidenceArtifactContractError(
            "upstream_artifacts must contain immutable artifact references"
        )
    try:
        upstream_refs = tuple(upstream_artifacts)
    except TypeError as exc:
        raise EvidenceArtifactContractError(
            "upstream_artifacts must contain immutable artifact references"
        ) from exc
    if any(not isinstance(item, EvidenceArtifactRef) for item in upstream_refs):
        raise EvidenceArtifactContractError(
            "upstream_artifacts must contain immutable artifact references"
        )
    upstream_ids = tuple(sorted({item.evidence_artifact_id for item in upstream_refs}))
    if len(upstream_ids) != len(upstream_refs):
        raise EvidenceArtifactContractError(
            "upstream_artifacts must not contain duplicates"
        )
    content_identity = _build_artifact_content_identity(
        payload_bytes,
        media_type=semantic_contract.media_type,
        serialization_format=semantic_contract.serialization_format,
    )
    basis = _evidence_artifact_basis(
        producer_processing_request_id=request_id,
        producer_processing_attempt_id=attempt_id,
        semantic_contract=semantic_contract,
        content_identity=content_identity,
        upstream_artifact_ids=upstream_ids,
    )
    artifact_id = _typed_digest(_EVIDENCE_ARTIFACT_ID_PREFIX, basis)
    if artifact_id in upstream_ids:
        raise EvidenceArtifactContractError(
            "an evidence artifact cannot depend on itself"
        )
    # Input joins and canonicalization are complete; construction adds no input
    # that would require hashing these same identities and upstream IDs again.
    return EvidenceArtifactRef(
        evidence_artifact_id=artifact_id,
        producer_processing_request_id=request_id,
        producer_processing_attempt_id=attempt_id,
        semantic_contract=semantic_contract,
        content_identity=content_identity,
        upstream_artifact_ids=upstream_ids,
    )


def _build_artifact_content_identity(
    payload_bytes: bytes,
    *,
    media_type: str,
    serialization_format: str,
) -> ArtifactContentIdentity:
    if not isinstance(payload_bytes, bytes):
        raise EvidenceArtifactContractError("payload_bytes must be exact bytes")
    normalized_media_type = _required_text(media_type, field="media_type")
    normalized_serialization_format = _required_text(
        serialization_format,
        field="serialization_format",
    )
    content_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    basis = _content_identity_basis(
        content_sha256=content_sha256,
        byte_size=len(payload_bytes),
        media_type=normalized_media_type,
        serialization_format=normalized_serialization_format,
    )
    return ArtifactContentIdentity(
        artifact_content_id=_typed_digest(_ARTIFACT_CONTENT_ID_PREFIX, basis),
        content_sha256=content_sha256,
        byte_size=len(payload_bytes),
        media_type=normalized_media_type,
        serialization_format=normalized_serialization_format,
    )


def _semantic_contract_basis(
    *,
    owner_namespace: str,
    semantic_role: str,
    schema_version: str,
    validation_status: str,
    media_type: str,
    serialization_format: str,
) -> dict[str, object]:
    return {
        "schema": "artifact_semantic_contract",
        "owner_namespace": owner_namespace,
        "semantic_role": semantic_role,
        "schema_version": schema_version,
        "validation_status": validation_status,
        "media_type": media_type,
        "serialization_format": serialization_format,
    }


def _content_identity_basis(
    *,
    content_sha256: str,
    byte_size: int,
    media_type: str,
    serialization_format: str,
) -> dict[str, object]:
    return {
        "schema": "artifact_content_identity",
        "content_sha256": content_sha256,
        "byte_size": byte_size,
        "media_type": media_type,
        "serialization_format": serialization_format,
    }


def _evidence_artifact_basis(
    *,
    producer_processing_request_id: str,
    producer_processing_attempt_id: str,
    semantic_contract: ArtifactSemanticContractRef,
    content_identity: ArtifactContentIdentity,
    upstream_artifact_ids: tuple[str, ...],
) -> dict[str, object]:
    return {
        "schema": "evidence_artifact_reference",
        "producer_processing_request_id": producer_processing_request_id,
        "producer_processing_attempt_id": producer_processing_attempt_id,
        "semantic_contract_id": semantic_contract.semantic_contract_id,
        "owner_namespace": semantic_contract.owner_namespace,
        "semantic_role": semantic_contract.semantic_role,
        "artifact_schema_version": semantic_contract.schema_version,
        "validation_status": semantic_contract.validation_status,
        "artifact_content_id": content_identity.artifact_content_id,
        "upstream_artifact_ids": list(upstream_artifact_ids),
    }


def _typed_digest(prefix: str, basis: Mapping[str, object]) -> str:
    digest = hashlib.sha256(_canonical_json(basis).encode("utf-8")).hexdigest()
    return f"{prefix}{digest}"


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EvidenceArtifactContractError(
            "artifact identity payload must be canonical JSON"
        ) from exc


def _required_text(value: object, *, field: str) -> str:
    return require_owner_normalized_text(
        value, field=field, error_factory=EvidenceArtifactContractError
    )


def _validate_sha256(value: object, *, field: str) -> str:
    return require_lower_sha256(
        value,
        field=field,
        error_factory=lambda msg: EvidenceArtifactContractError(
            f"{field} must be a lower-case SHA-256 digest"
        ),
    )


def _validate_typed_digest_id(
    value: object,
    *,
    prefix: str,
    field: str,
) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise EvidenceArtifactContractError(f"{field} must use the {prefix!r} prefix")
    _validate_sha256(value[len(prefix) :], field=field)
    return value


__all__ = [
    "ArtifactContentIdentity",
    "ArtifactSemanticContractRef",
    "EvidenceArtifactContractError",
    "EvidenceArtifactRef",
    "mint_artifact_semantic_contract_ref",
    "mint_evidence_artifact_ref",
]
