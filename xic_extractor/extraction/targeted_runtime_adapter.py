"""Targeted process-result handoff into the mechanical execution runtime.

The Targeted producer owns the result-envelope role and canonical result
identity payload.  The shared evidence-artifact module owns immutable exact-byte
references, while the runtime only settles opaque manifest work keys.  Legacy
``RawFileExtractionResult`` values are deliberately not serialized or hashed by
this boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.execution_runtime import (
    AttemptRuntime,
    WorkUnitSettlement,
    WorkUnitTerminalOutcome,
    settle_work_unit,
)
from xic_extractor.processing_identity import ProcessingAttemptIdentity


class TargetedRuntimeAdapterError(ValueError):
    """Raised when a Targeted process result cannot settle its runtime work."""


_SCHEMA_VERSION = "targeted_process_result_identity_v1"
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.extraction.targeted_runtime_adapter",
    semantic_role="targeted_process_worker_result_identity",
    schema_version=_SCHEMA_VERSION,
    validation_status="diagnostic_only",
    media_type="application/vnd.xic.targeted-process-result-identity+json",
    serialization_format="canonical-json-v1",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class TargetedProcessResultRef:
    """Targeted-owned immutable result ref bound to one manifest work key."""

    work_unit_key: str
    owner_result_content_sha256: str
    owner_result_byte_size: int
    artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        key = _required_text(self.work_unit_key, field="work_unit_key")
        if (
            not isinstance(self.owner_result_content_sha256, str)
            or _SHA256_RE.fullmatch(self.owner_result_content_sha256) is None
        ):
            raise TargetedRuntimeAdapterError(
                "owner_result_content_sha256 must be a lower-case SHA-256 digest"
            )
        if (
            isinstance(self.owner_result_byte_size, bool)
            or not isinstance(self.owner_result_byte_size, int)
            or self.owner_result_byte_size < 0
        ):
            raise TargetedRuntimeAdapterError(
                "owner_result_byte_size must be a non-negative integer"
            )
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise TargetedRuntimeAdapterError(
                "artifact must be an owner-minted EvidenceArtifactRef"
            )
        if self.artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
            raise TargetedRuntimeAdapterError(
                "artifact must use the Targeted process-result semantic contract"
            )
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                _canonical_result_identity_bytes(
                    work_unit_key=key,
                    owner_result_content_sha256=self.owner_result_content_sha256,
                    owner_result_byte_size=self.owner_result_byte_size,
                ),
            )
        except ValueError as exc:
            raise TargetedRuntimeAdapterError(
                "artifact does not bind this Targeted result identity"
            ) from exc


@dataclass(frozen=True, slots=True)
class TargetedProcessWorkerResultEnvelope:
    """One pickleable producer-owned result ready for runtime settlement."""

    attempt: ProcessingAttemptIdentity
    work_unit_key: str
    outcome: WorkUnitTerminalOutcome
    owner_result_ref: TargetedProcessResultRef | None = None
    failure_message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise TargetedRuntimeAdapterError(
                "attempt must be an owner-issued ProcessingAttemptIdentity"
            )
        artifact = None
        if self.outcome is WorkUnitTerminalOutcome.SUCCEEDED:
            if not isinstance(self.owner_result_ref, TargetedProcessResultRef):
                raise TargetedRuntimeAdapterError(
                    "succeeded result envelope requires a "
                    "TargetedProcessResultRef"
                )
            if self.owner_result_ref.work_unit_key != self.work_unit_key:
                raise TargetedRuntimeAdapterError(
                    "owner_result_ref must bind the envelope work_unit_key"
                )
            artifact = self.owner_result_ref.artifact
        elif self.owner_result_ref is not None:
            raise TargetedRuntimeAdapterError(
                "only succeeded result envelope may carry owner_result_ref"
            )
        try:
            WorkUnitSettlement(
                attempt=self.attempt,
                work_unit_key=self.work_unit_key,
                outcome=self.outcome,
                owner_result_ref=artifact,
            )
        except ValueError as exc:
            raise TargetedRuntimeAdapterError(str(exc)) from exc
        if self.outcome is WorkUnitTerminalOutcome.SUCCEEDED:
            if self.failure_message is not None:
                raise TargetedRuntimeAdapterError(
                    "succeeded result envelope cannot carry failure_message"
                )
        elif (
            not isinstance(self.failure_message, str)
            or not self.failure_message.strip()
        ):
            raise TargetedRuntimeAdapterError(
                "failed or cancelled result envelope requires failure_message"
            )


@dataclass(frozen=True, slots=True)
class TargetedRuntimeApplicationResult:
    """Settled Targeted runtime state and manifest-ordered successful refs."""

    runtime: AttemptRuntime
    successful_result_refs: tuple[tuple[str, TargetedProcessResultRef], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, AttemptRuntime):
            raise TargetedRuntimeAdapterError("runtime must be an AttemptRuntime")
        expected_keys = tuple(
            settlement.work_unit_key
            for key in self.runtime.manifest.work_unit_keys
            for settlement in self.runtime.settlements
            if settlement.work_unit_key == key
            and settlement.outcome is WorkUnitTerminalOutcome.SUCCEEDED
        )
        if expected_keys != tuple(key for key, _ref in self.successful_result_refs):
            raise TargetedRuntimeAdapterError(
                "successful_result_refs must follow settled manifest order"
            )
        by_key = {
            settlement.work_unit_key: settlement
            for settlement in self.runtime.settlements
        }
        for key, reference in self.successful_result_refs:
            if (
                not isinstance(reference, TargetedProcessResultRef)
                or reference.work_unit_key != key
                or reference.artifact != by_key[key].owner_result_ref
            ):
                raise TargetedRuntimeAdapterError(
                    "successful result ref must match the accepted settlement"
                )


def canonical_targeted_process_result_identity_bytes(
    *,
    work_unit_key: str,
    owner_result_payload: bytes,
) -> bytes:
    """Bind one opaque owner payload by exact digest without serializing it."""

    normalized_key = _required_text(work_unit_key, field="work_unit_key")
    if not isinstance(owner_result_payload, bytes):
        raise TargetedRuntimeAdapterError("owner_result_payload must be exact bytes")
    return _canonical_result_identity_bytes(
        work_unit_key=normalized_key,
        owner_result_content_sha256=hashlib.sha256(owner_result_payload).hexdigest(),
        owner_result_byte_size=len(owner_result_payload),
    )


def _canonical_result_identity_bytes(
    *,
    work_unit_key: str,
    owner_result_content_sha256: str,
    owner_result_byte_size: int,
) -> bytes:
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "work_unit_key": work_unit_key,
        "owner_result_content_sha256": owner_result_content_sha256,
        "owner_result_byte_size": owner_result_byte_size,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def mint_targeted_process_result_ref(
    *,
    processing_attempt: ProcessingAttemptIdentity,
    work_unit_key: str,
    owner_result_payload: bytes,
    upstream_artifacts: Sequence[EvidenceArtifactRef] = (),
) -> TargetedProcessResultRef:
    """Mint the Targeted producer's immutable result identity reference."""

    if not isinstance(processing_attempt, ProcessingAttemptIdentity):
        raise TargetedRuntimeAdapterError(
            "processing_attempt must be an owner-issued ProcessingAttemptIdentity"
        )
    key = _required_text(work_unit_key, field="work_unit_key")
    if not isinstance(owner_result_payload, bytes):
        raise TargetedRuntimeAdapterError("owner_result_payload must be exact bytes")
    content_sha256 = hashlib.sha256(owner_result_payload).hexdigest()
    byte_size = len(owner_result_payload)
    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=processing_attempt.processing_request_id,
        producer_processing_attempt_id=processing_attempt.processing_attempt_id,
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=_canonical_result_identity_bytes(
            work_unit_key=key,
            owner_result_content_sha256=content_sha256,
            owner_result_byte_size=byte_size,
        ),
        upstream_artifacts=upstream_artifacts,
    )
    return TargetedProcessResultRef(
        work_unit_key=key,
        owner_result_content_sha256=content_sha256,
        owner_result_byte_size=byte_size,
        artifact=artifact,
    )


def settle_targeted_process_worker_results(
    runtime: AttemptRuntime,
    envelopes: Sequence[TargetedProcessWorkerResultEnvelope],
) -> TargetedRuntimeApplicationResult:
    """Settle a closed Targeted result batch through the existing reducer."""

    if not isinstance(runtime, AttemptRuntime):
        raise TargetedRuntimeAdapterError("runtime must be an AttemptRuntime")
    if isinstance(envelopes, (str, bytes)):
        raise TargetedRuntimeAdapterError(
            "envelopes must contain TargetedProcessWorkerResultEnvelope values"
        )
    try:
        batch = tuple(envelopes)
    except TypeError as exc:
        raise TargetedRuntimeAdapterError(
            "envelopes must contain TargetedProcessWorkerResultEnvelope values"
        ) from exc
    current = runtime
    accepted_refs: dict[str, TargetedProcessResultRef] = {}
    for envelope in batch:
        if not isinstance(envelope, TargetedProcessWorkerResultEnvelope):
            raise TargetedRuntimeAdapterError(
                "envelopes must contain TargetedProcessWorkerResultEnvelope values"
            )
        if envelope.attempt != current.manifest.attempt:
            raise TargetedRuntimeAdapterError(
                "worker result envelope references a different runtime attempt"
            )
        current = settle_work_unit(
            current,
            work_unit_key=envelope.work_unit_key,
            outcome=envelope.outcome,
            owner_result_ref=(
                None
                if envelope.owner_result_ref is None
                else envelope.owner_result_ref.artifact
            ),
        )
        if (
            envelope.outcome is WorkUnitTerminalOutcome.SUCCEEDED
            and envelope.work_unit_key
            in {settlement.work_unit_key for settlement in current.settlements}
            and envelope.work_unit_key not in accepted_refs
        ):
            if not isinstance(envelope.owner_result_ref, TargetedProcessResultRef):
                raise TargetedRuntimeAdapterError(
                    "succeeded envelope lost its TargetedProcessResultRef"
                )
            accepted_refs[envelope.work_unit_key] = envelope.owner_result_ref
    if not current.all_manifest_work_settled:
        missing = tuple(
            key
            for key in current.manifest.work_unit_keys
            if key
            not in {settlement.work_unit_key for settlement in current.settlements}
        )
        raise TargetedRuntimeAdapterError(
            f"worker result batch did not settle manifest work: {missing!r}"
        )
    refs = tuple(
        (key, accepted_refs[key])
        for key in current.manifest.work_unit_keys
        if key in accepted_refs
    )
    return TargetedRuntimeApplicationResult(
        runtime=current,
        successful_result_refs=refs,
    )


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TargetedRuntimeAdapterError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


__all__ = [
    "TargetedProcessResultRef",
    "TargetedProcessWorkerResultEnvelope",
    "TargetedRuntimeAdapterError",
    "TargetedRuntimeApplicationResult",
    "canonical_targeted_process_result_identity_bytes",
    "mint_targeted_process_result_ref",
    "settle_targeted_process_worker_results",
]
