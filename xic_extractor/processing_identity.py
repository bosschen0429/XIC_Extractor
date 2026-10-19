"""Immutable, no-RAW identities for the first processing lineage boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from xic_extractor.artifact_lineage import (
    EvidenceArtifactClosureError,
)
from xic_extractor.artifact_lineage import (
    canonical_evidence_artifact_closure as _validate_artifact_closure,
)
from xic_extractor.configuration.target_identity import (
    TargetListSnapshot,
    target_list_snapshot_payload,
)
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadataSnapshot,
)


class IdentityContractError(ValueError):
    """Raised when an immutable identity cannot satisfy its contract."""


class FingerprintCancelledError(IdentityContractError):
    """Raised when a caller cancels an in-progress content fingerprint."""


_ACQUISITION_SOURCE_PREFIX = "xic.acquisition-source:"
_PROCESSING_REQUEST_PREFIX = "xic.processing-request:"
_PROCESSING_ATTEMPT_PREFIX = "xic.processing-attempt:"
PROCESSING_REQUEST_ID_PREFIX = _PROCESSING_REQUEST_PREFIX
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def _canonical_json(value: Any) -> str:
    """Serialize JSON-compatible values into the contract's canonical form."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise IdentityContractError("identity payload is not canonical JSON") from exc


def canonical_identity_basis_bytes(value: Mapping[str, object]) -> bytes:
    """Return canonical UTF-8 JSON bytes for a closed typed identity basis."""

    if not isinstance(value, Mapping):
        raise IdentityContractError("identity basis must be a JSON object")
    _validate_json_object_keys(value)
    return _canonical_json(dict(value)).encode("utf-8")


def mint_typed_identity(
    prefix: str, basis: Mapping[str, object]
) -> tuple[str, bytes]:
    """Encode an owner-built basis once and return its identity and exact bytes."""

    if not isinstance(prefix, str) or not prefix or not prefix.endswith(":"):
        raise IdentityContractError(
            "typed identity prefix must be non-empty and end in ':'"
        )
    basis_bytes = canonical_identity_basis_bytes(basis)
    return f"{prefix}{hashlib.sha256(basis_bytes).hexdigest()}", basis_bytes


def typed_identity_digest(prefix: str, basis_bytes: bytes) -> str:
    """Mint a typed SHA-256 identity from exact canonical basis bytes."""

    if not isinstance(prefix, str) or not prefix or not prefix.endswith(":"):
        raise IdentityContractError(
            "typed identity prefix must be non-empty and end in ':'"
        )
    if not isinstance(basis_bytes, bytes) or not basis_bytes:
        raise IdentityContractError("identity basis must be non-empty UTF-8 bytes")
    # Repeated owner checks rebuild equal immutable basis bytes. Reuse only
    # their canonical-byte proof; callers still rebuild and compare live bases.
    # The entry/size limits retain at most 16 MiB of basis payloads. Subclasses
    # keep the original path because they may override decode or equality.
    if type(basis_bytes) is bytes and len(basis_bytes) <= 4096:
        digest = _cached_canonical_basis_digest(basis_bytes)
    else:
        digest = _canonical_basis_digest(basis_bytes)
    return f"{prefix}{digest}"


def _canonical_basis_digest(basis_bytes: bytes) -> str:
    try:
        parsed = json.loads(basis_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentityContractError(
            "identity basis must be canonical UTF-8 JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise IdentityContractError("identity basis must be a JSON object")
    if canonical_identity_basis_bytes(parsed) != basis_bytes:
        raise IdentityContractError("identity basis bytes are not canonical JSON")
    return hashlib.sha256(basis_bytes).hexdigest()


_cached_canonical_basis_digest = lru_cache(maxsize=4096)(_canonical_basis_digest)


def validate_typed_identity_id(
    identity_id: object,
    *,
    prefix: str,
    field: str,
) -> str:
    """Validate a typed identity whose authoritative basis is external."""

    return _validate_typed_id(identity_id, prefix=prefix, field=field)


def _canonical_object_json(value: Mapping[str, object]) -> str:
    if not isinstance(value, Mapping):
        raise IdentityContractError("scientific_inputs must be a JSON object")
    _validate_json_object_keys(value)
    try:
        snapshot = _canonical_json(dict(value))
        parsed = json.loads(snapshot)
    except json.JSONDecodeError as exc:
        raise IdentityContractError("scientific_inputs must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise IdentityContractError("scientific_inputs must be a JSON object")
    return snapshot


def _validate_json_object_keys(value: object) -> None:
    value_type = type(value)
    if value_type in (str, int, float, bool, type(None)):
        return
    if value_type is dict or isinstance(value, Mapping):
        for key, nested_value in cast("Mapping[object, object]", value).items():
            if not isinstance(key, str):
                raise IdentityContractError("JSON object keys must be strings")
            _validate_json_object_keys(nested_value)
    elif value_type in (list, tuple) or isinstance(value, (list, tuple)):
        for nested_value in cast("list[object] | tuple[object, ...]", value):
            _validate_json_object_keys(nested_value)


def _validate_digest(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise IdentityContractError(f"{field} must be a lower-case SHA-256 hex digest")
    return value


def _validate_typed_id(value: object, *, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise IdentityContractError(f"{field} must use the {prefix!r} identity prefix")
    digest = value[len(prefix) :]
    _validate_digest(digest, field=field)
    return value


def _typed_digest(prefix: str, basis: Mapping[str, object]) -> str:
    return mint_typed_identity(prefix, basis)[0]


def _require_nonempty_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdentityContractError(f"{field} must be non-empty text")
    return value


@dataclass(frozen=True, slots=True)
class FileContentFingerprint:
    """Path-independent full-file content facts from one opened handle."""

    content_sha256: str
    byte_size: int

    def __post_init__(self) -> None:
        _validate_digest(self.content_sha256, field="content_sha256")
        if (
            isinstance(self.byte_size, bool)
            or not isinstance(self.byte_size, int)
            or self.byte_size < 0
        ):
            raise IdentityContractError("byte_size must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class AcquisitionSourceIdentity:
    """Content-bound identity for one acquisition source."""

    acquisition_source_id: str
    content_sha256: str
    byte_size: int
    acquisition_format: str
    observation_namespace: str

    def __post_init__(self) -> None:
        source_id = _validate_typed_id(
            self.acquisition_source_id,
            prefix=_ACQUISITION_SOURCE_PREFIX,
            field="acquisition_source_id",
        )
        content_sha256 = _validate_digest(self.content_sha256, field="content_sha256")
        if (
            isinstance(self.byte_size, bool)
            or not isinstance(self.byte_size, int)
            or self.byte_size < 0
        ):
            raise IdentityContractError("byte_size must be a non-negative integer")
        acquisition_format = _require_nonempty_text(
            self.acquisition_format, field="acquisition_format"
        )
        observation_namespace = _require_nonempty_text(
            self.observation_namespace, field="observation_namespace"
        )
        expected_id = _typed_digest(
            _ACQUISITION_SOURCE_PREFIX,
            {
                "schema": "acquisition_source_identity",
                "content_sha256": content_sha256,
                "byte_size": self.byte_size,
                "acquisition_format": acquisition_format,
                "observation_namespace": observation_namespace,
            },
        )
        if source_id != expected_id:
            raise IdentityContractError(
                "acquisition_source_id rebind mismatches immutable content"
            )


@dataclass(frozen=True, slots=True)
class AcquisitionSourceExpectation:
    """Content identity plus the exact local path state observed with its request."""

    source: AcquisitionSourceIdentity
    path_identity: tuple[int, int, int, int, int]

    def __post_init__(self) -> None:
        if not isinstance(self.source, AcquisitionSourceIdentity):
            raise IdentityContractError("source must be an AcquisitionSourceIdentity")
        if (
            not isinstance(self.path_identity, tuple)
            or len(self.path_identity) != 5
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in self.path_identity
            )
        ):
            raise IdentityContractError(
                "path_identity must be a five-integer stat identity"
            )
        if self.path_identity[2] != self.source.byte_size:
            raise IdentityContractError(
                "path_identity size mismatches acquisition source content"
            )


@dataclass(frozen=True, slots=True)
class AcquisitionInputBinding:
    """Request-local adapter key and injection order for one source."""

    acquisition_source_id: str
    adapter_input_key: str
    injection_order: int

    def __post_init__(self) -> None:
        _require_nonempty_text(
            self.acquisition_source_id, field="acquisition_source_id"
        )
        _require_nonempty_text(self.adapter_input_key, field="adapter_input_key")
        if (
            isinstance(self.injection_order, bool)
            or not isinstance(self.injection_order, int)
            or self.injection_order <= 0
        ):
            raise IdentityContractError("injection_order must be a positive integer")


def _request_basis(
    *,
    acquisition_sources: tuple[AcquisitionSourceIdentity, ...],
    acquisition_bindings: tuple[AcquisitionInputBinding, ...],
    scientific_input_schema_version: str,
    processing_algorithm_version: str,
    processing_contract_version: str,
    normalized_scientific_inputs_json: str,
) -> dict[str, object]:
    return {
        "schema": "processing_request_identity",
        "acquisition_sources": [
            source.acquisition_source_id for source in acquisition_sources
        ],
        "acquisition_bindings": [
            {
                "acquisition_source_id": binding.acquisition_source_id,
                "adapter_input_key": binding.adapter_input_key,
                "injection_order": binding.injection_order,
            }
            for binding in acquisition_bindings
        ],
        "scientific_input_schema_version": scientific_input_schema_version,
        "processing_algorithm_version": processing_algorithm_version,
        "processing_contract_version": processing_contract_version,
        "normalized_scientific_inputs": json.loads(normalized_scientific_inputs_json),
    }


@dataclass(frozen=True, slots=True)
class ProcessingRequestIdentity:
    """Immutable normalized scientific request over identified sources."""

    processing_request_id: str
    acquisition_sources: tuple[AcquisitionSourceIdentity, ...]
    acquisition_bindings: tuple[AcquisitionInputBinding, ...]
    scientific_input_schema_version: str
    processing_algorithm_version: str
    processing_contract_version: str
    normalized_scientific_inputs_json: str

    def __post_init__(self) -> None:
        request_id = _validate_typed_id(
            self.processing_request_id,
            prefix=_PROCESSING_REQUEST_PREFIX,
            field="processing_request_id",
        )
        if (
            not isinstance(self.acquisition_sources, tuple)
            or not self.acquisition_sources
        ):
            raise IdentityContractError("acquisition_sources must be a non-empty tuple")
        if not isinstance(self.acquisition_bindings, tuple):
            raise IdentityContractError("acquisition_bindings must be a tuple")
        if any(
            not isinstance(source, AcquisitionSourceIdentity)
            for source in self.acquisition_sources
        ):
            raise IdentityContractError(
                "acquisition_sources contains an invalid identity"
            )
        if any(
            not isinstance(binding, AcquisitionInputBinding)
            for binding in self.acquisition_bindings
        ):
            raise IdentityContractError(
                "acquisition_bindings contains an invalid binding"
            )

        source_ids = tuple(
            source.acquisition_source_id for source in self.acquisition_sources
        )
        if len(set(source_ids)) != len(source_ids):
            raise IdentityContractError(
                "duplicate acquisition source identities are not allowed"
            )
        if source_ids != tuple(sorted(source_ids)):
            raise IdentityContractError("acquisition_sources must be canonical-sorted")
        bindings_key = tuple(
            (
                binding.acquisition_source_id,
                binding.adapter_input_key,
                binding.injection_order,
            )
            for binding in self.acquisition_bindings
        )
        if bindings_key != tuple(sorted(bindings_key)):
            raise IdentityContractError("acquisition_bindings must be canonical-sorted")
        adapter_input_keys = tuple(
            binding.adapter_input_key for binding in self.acquisition_bindings
        )
        if len(set(adapter_input_keys)) != len(adapter_input_keys):
            raise IdentityContractError("adapter_input_key values must be unique")
        orders = tuple(binding.injection_order for binding in self.acquisition_bindings)
        if len(set(orders)) != len(orders):
            raise IdentityContractError("injection_order values must be unique")
        bound_source_ids = {
            binding.acquisition_source_id for binding in self.acquisition_bindings
        }
        if bound_source_ids != set(source_ids):
            raise IdentityContractError("bindings must cover each source exactly once")
        if len(self.acquisition_bindings) != len(source_ids):
            raise IdentityContractError("bindings must cover each source exactly once")

        schema_version = _require_nonempty_text(
            self.scientific_input_schema_version,
            field="scientific_input_schema_version",
        )
        algorithm_version = _require_nonempty_text(
            self.processing_algorithm_version,
            field="processing_algorithm_version",
        )
        contract_version = _require_nonempty_text(
            self.processing_contract_version,
            field="processing_contract_version",
        )
        normalized_json = self.normalized_scientific_inputs_json
        if not isinstance(normalized_json, str):
            raise IdentityContractError(
                "normalized_scientific_inputs_json must be text"
            )
        try:
            parsed = json.loads(normalized_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise IdentityContractError(
                "normalized_scientific_inputs_json must be canonical JSON"
            ) from exc
        if not isinstance(parsed, dict) or _canonical_json(parsed) != normalized_json:
            raise IdentityContractError(
                "normalized_scientific_inputs_json must be canonical JSON object"
            )

        expected_id = _typed_digest(
            _PROCESSING_REQUEST_PREFIX,
            _request_basis(
                acquisition_sources=self.acquisition_sources,
                acquisition_bindings=self.acquisition_bindings,
                scientific_input_schema_version=schema_version,
                processing_algorithm_version=algorithm_version,
                processing_contract_version=contract_version,
                normalized_scientific_inputs_json=normalized_json,
            ),
        )
        if request_id != expected_id:
            raise IdentityContractError(
                "processing_request_id rebind mismatches immutable request"
            )


def _attempt_basis(
    *,
    processing_request_id: str,
    attempt_token: str,
    retry_of_attempt_id: str | None,
) -> dict[str, object]:
    return {
        "schema": "processing_attempt_identity",
        "processing_request_id": processing_request_id,
        "attempt_token": attempt_token,
        "retry_of_attempt_id": retry_of_attempt_id,
    }


@dataclass(frozen=True, slots=True)
class ProcessingAttemptIdentity:
    """Unique execution identity referencing one processing request."""

    processing_attempt_id: str
    processing_request_id: str
    attempt_token: str
    retry_of: ProcessingAttemptIdentity | None = None

    @property
    def retry_of_attempt_id(self) -> str | None:
        """Typed predecessor ID without discarding its validated reference."""

        if self.retry_of is None:
            return None
        return self.retry_of.processing_attempt_id

    def __post_init__(self) -> None:
        attempt_id = _validate_typed_id(
            self.processing_attempt_id,
            prefix=_PROCESSING_ATTEMPT_PREFIX,
            field="processing_attempt_id",
        )
        request_id = _validate_typed_id(
            self.processing_request_id,
            prefix=_PROCESSING_REQUEST_PREFIX,
            field="processing_request_id",
        )
        if (
            not isinstance(self.attempt_token, str)
            or _TOKEN_RE.fullmatch(self.attempt_token) is None
        ):
            raise IdentityContractError(
                "attempt_token must be a lower-case 32-character hex token"
            )
        retry_of = self.retry_of
        if retry_of is not None:
            if not isinstance(retry_of, ProcessingAttemptIdentity):
                raise IdentityContractError(
                    "retry_of must be a ProcessingAttemptIdentity"
                )
            if retry_of.processing_request_id != request_id:
                raise IdentityContractError(
                    "retry predecessor references a different processing request"
                )
            if retry_of.processing_attempt_id == attempt_id:
                raise IdentityContractError("an attempt cannot retry itself")
        expected_id = _typed_digest(
            _PROCESSING_ATTEMPT_PREFIX,
            _attempt_basis(
                processing_request_id=request_id,
                attempt_token=self.attempt_token,
                retry_of_attempt_id=(
                    retry_of.processing_attempt_id if retry_of is not None else None
                ),
            ),
        )
        if attempt_id != expected_id:
            raise IdentityContractError(
                "processing_attempt_id rebind mismatches immutable attempt"
            )


def retry_predecessor_attempt_ids(
    attempt: ProcessingAttemptIdentity,
) -> tuple[str, ...]:
    """Return the complete owner-validated retry predecessor chain."""

    if not isinstance(attempt, ProcessingAttemptIdentity):
        raise IdentityContractError(
            "attempt must be an owner-issued ProcessingAttemptIdentity"
        )
    predecessor_ids: list[str] = []
    seen = {attempt.processing_attempt_id}
    predecessor = attempt.retry_of
    while predecessor is not None:
        predecessor_id = predecessor.processing_attempt_id
        if predecessor_id in seen:
            raise IdentityContractError("retry predecessor chain must be acyclic")
        if predecessor.processing_request_id != attempt.processing_request_id:
            raise IdentityContractError(
                "retry predecessor chain references a different processing request"
            )
        seen.add(predecessor_id)
        predecessor_ids.append(predecessor.processing_attempt_id)
        predecessor = predecessor.retry_of
    return tuple(predecessor_ids)


def canonical_evidence_artifact_closure(
    *,
    producer_attempt: ProcessingAttemptIdentity,
    artifacts: tuple[EvidenceArtifactRef, ...],
) -> tuple[EvidenceArtifactRef, ...]:
    """Validate artifacts against the producer's complete retry chain."""

    if not isinstance(producer_attempt, ProcessingAttemptIdentity):
        raise IdentityContractError(
            "producer_attempt must be an owner-issued ProcessingAttemptIdentity"
        )
    return _validate_artifact_closure(
        processing_request_id=producer_attempt.processing_request_id,
        processing_attempt_id=producer_attempt.processing_attempt_id,
        dependency_processing_attempt_ids=retry_predecessor_attempt_ids(
            producer_attempt
        ),
        artifacts=artifacts,
    )


def canonical_current_attempt_artifact_closure(
    *,
    producer_attempt: ProcessingAttemptIdentity,
    artifacts: tuple[EvidenceArtifactRef, ...],
) -> tuple[EvidenceArtifactRef, ...]:
    """Validate artifacts produced only by the current attempt."""

    if not isinstance(producer_attempt, ProcessingAttemptIdentity):
        raise IdentityContractError(
            "producer_attempt must be an owner-issued ProcessingAttemptIdentity"
        )
    return _validate_artifact_closure(
        processing_request_id=producer_attempt.processing_request_id,
        processing_attempt_id=producer_attempt.processing_attempt_id,
        dependency_processing_attempt_ids=(),
        artifacts=artifacts,
    )


@dataclass(frozen=True, slots=True)
class ProcessingOwnerReferences:
    """Validated composition of lineage and owner-minted identity references."""

    request: ProcessingRequestIdentity
    attempt: ProcessingAttemptIdentity
    sample_snapshot: SampleMetadataSnapshot
    sample_resolutions: tuple[ResolvedCanonicalSampleIdentity, ...]
    target_snapshot: TargetListSnapshot | None = None
    evidence_artifacts: tuple[EvidenceArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.request, ProcessingRequestIdentity):
            raise IdentityContractError("request must be a ProcessingRequestIdentity")
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise IdentityContractError("attempt must be a ProcessingAttemptIdentity")
        if self.attempt.processing_request_id != self.request.processing_request_id:
            raise IdentityContractError(
                "attempt references a different processing request"
            )
        if not isinstance(self.sample_snapshot, SampleMetadataSnapshot):
            raise IdentityContractError("sample_snapshot must be owner-minted")
        if not isinstance(self.sample_resolutions, tuple) or any(
            not isinstance(resolution, ResolvedCanonicalSampleIdentity)
            for resolution in self.sample_resolutions
        ):
            raise IdentityContractError(
                "sample_resolutions must contain owner-resolved sample identities"
            )
        if any(
            resolution.snapshot.snapshot_id != self.sample_snapshot.snapshot_id
            for resolution in self.sample_resolutions
        ):
            raise IdentityContractError(
                "sample resolutions must come from the same sample metadata snapshot"
            )
        expected_tokens = sorted(
            binding.adapter_input_key for binding in self.request.acquisition_bindings
        )
        resolved_tokens = sorted(
            resolution.lookup_token for resolution in self.sample_resolutions
        )
        if resolved_tokens != expected_tokens:
            raise IdentityContractError(
                "sample resolutions must cover each request acquisition binding"
            )
        binding_orders_by_key = {
            binding.adapter_input_key: binding.injection_order
            for binding in self.request.acquisition_bindings
        }
        for resolution in self.sample_resolutions:
            metadata_order = resolution.metadata.injection_order
            if (
                metadata_order is not None
                and metadata_order != binding_orders_by_key[resolution.lookup_token]
            ):
                raise IdentityContractError(
                    "sample metadata injection_order must match request binding"
                )
        sample_ids = tuple(
            resolution.identity.sample_identity_id
            for resolution in self.sample_resolutions
        )
        if len(set(sample_ids)) != len(sample_ids):
            raise IdentityContractError(
                "each request acquisition binding must resolve to a distinct "
                "canonical sample identity"
            )
        request_inputs: Mapping[str, object] = json.loads(
            self.request.normalized_scientific_inputs_json
        )
        if "target_list_snapshot" in request_inputs and self.target_snapshot is None:
            raise IdentityContractError(
                "target_snapshot is required when the processing request pins one"
            )
        if self.target_snapshot is not None:
            if not isinstance(self.target_snapshot, TargetListSnapshot):
                raise IdentityContractError("target_snapshot must be owner-minted")
            _validate_request_target_snapshot(request_inputs, self.target_snapshot)
        try:
            canonical_artifacts = canonical_evidence_artifact_closure(
                producer_attempt=self.attempt,
                artifacts=self.evidence_artifacts,
            )
        except EvidenceArtifactClosureError as exc:
            raise IdentityContractError(str(exc)) from exc
        if canonical_artifacts != self.evidence_artifacts:
            raise IdentityContractError("evidence_artifacts must be canonical-sorted")



def _validate_request_target_snapshot(
    request_inputs: Mapping[str, object],
    target_snapshot: TargetListSnapshot,
) -> None:
    expected_payload = target_list_snapshot_payload(target_snapshot)
    if request_inputs.get("target_list_snapshot") != expected_payload:
        raise IdentityContractError(
            "processing request does not pin the bound target-list snapshot"
        )


def build_acquisition_source_identity(
    path: str | Path,
    *,
    acquisition_format: str,
    observation_namespace: str,
    should_cancel: Callable[[], bool] | None = None,
) -> AcquisitionSourceIdentity:
    """Hash one regular file and bind its acquisition observation namespace."""

    fingerprint = fingerprint_file_content(path, should_cancel=should_cancel)
    return _build_acquisition_source_identity(
        content_sha256=fingerprint.content_sha256,
        byte_size=fingerprint.byte_size,
        acquisition_format=acquisition_format,
        observation_namespace=observation_namespace,
    )


def fingerprint_file_content(
    path: str | Path,
    *,
    observation: Callable[[Mapping[str, object]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> FileContentFingerprint:
    """Hash one stable regular-file handle and reject path replacement."""

    started = perf_counter() if observation is not None else None
    try:
        source_path = Path(path)
    except (TypeError, ValueError) as exc:
        error = IdentityContractError("acquisition source path is invalid")
        _notify_fingerprint_observation(
            observation,
            path=path,
            elapsed_sec=0.0 if started is None else perf_counter() - started,
            byte_size=None,
            availability="failed",
            reason=str(error),
        )
        raise error from exc
    byte_size = 0
    try:
        with source_path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise IdentityContractError(
                    "content fingerprint path must be a regular file"
                )
            digest = hashlib.sha256()
            byte_size = 0
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                byte_size += len(chunk)
                if should_cancel is not None and should_cancel():
                    raise FingerprintCancelledError("content fingerprint was cancelled")
            after = os.fstat(handle.fileno())
        path_after = source_path.stat()
        stat_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(
            getattr(before, field) != getattr(after, field) for field in stat_fields
        ):
            raise IdentityContractError("source content mutated during hashing")
        # Windows may publish timestamp metadata from a path lookup at a different
        # moment than from the already-open handle.  Replacement is an object-
        # identity question; content mutation is already guarded by the two handle
        # snapshots above.
        if not os.path.samestat(after, path_after):
            raise IdentityContractError("source path was replaced during hashing")
        if byte_size != after.st_size:
            raise IdentityContractError("source byte size changed during hashing")
    except IdentityContractError as exc:
        _notify_fingerprint_observation(
            observation,
            path=path,
            elapsed_sec=0.0 if started is None else perf_counter() - started,
            byte_size=byte_size,
            availability="failed",
            reason=str(exc),
        )
        raise
    except (OSError, ValueError) as exc:
        _notify_fingerprint_observation(
            observation,
            path=path,
            elapsed_sec=0.0 if started is None else perf_counter() - started,
            byte_size=byte_size,
            availability="failed",
            reason=str(exc),
        )
        raise IdentityContractError(
            f"unable to hash acquisition source {source_path}"
        ) from exc

    result = FileContentFingerprint(
        content_sha256=digest.hexdigest(),
        byte_size=byte_size,
    )
    _notify_fingerprint_observation(
        observation,
        path=path,
        elapsed_sec=0.0 if started is None else perf_counter() - started,
        byte_size=byte_size,
        availability="measured",
        reason="",
    )
    return result


def _notify_fingerprint_observation(
    observation: Callable[[Mapping[str, object]], None] | None,
    *,
    path: str | Path,
    elapsed_sec: float,
    byte_size: int | None,
    availability: str,
    reason: str,
) -> None:
    if observation is None:
        return
    try:
        observation(
            {
                "metric": "fingerprint",
                "scope": str(path),
                "calls": 1,
                "bytes": byte_size,
                "elapsed_sec": max(0.0, elapsed_sec),
                "availability": availability,
                "reason": reason,
            }
        )
    except Exception:
        # Diagnostic callbacks must not alter identity semantics.
        return


def _build_acquisition_source_identity(
    *,
    content_sha256: str,
    byte_size: int,
    acquisition_format: str,
    observation_namespace: str,
) -> AcquisitionSourceIdentity:
    content_sha256 = _validate_digest(content_sha256, field="content_sha256")
    if isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size < 0:
        raise IdentityContractError("byte_size must be a non-negative integer")
    acquisition_format = _require_nonempty_text(
        acquisition_format, field="acquisition_format"
    )
    observation_namespace = _require_nonempty_text(
        observation_namespace,
        field="observation_namespace",
    )
    source_id = _typed_digest(
        _ACQUISITION_SOURCE_PREFIX,
        {
            "schema": "acquisition_source_identity",
            "content_sha256": content_sha256,
            "byte_size": byte_size,
            "acquisition_format": acquisition_format,
            "observation_namespace": observation_namespace,
        },
    )
    return AcquisitionSourceIdentity(
        acquisition_source_id=source_id,
        content_sha256=content_sha256,
        byte_size=byte_size,
        acquisition_format=acquisition_format,
        observation_namespace=observation_namespace,
    )


def build_processing_request_identity(
    *,
    acquisition_sources: tuple[AcquisitionSourceIdentity, ...],
    acquisition_bindings: tuple[AcquisitionInputBinding, ...],
    scientific_input_schema_version: str,
    processing_algorithm_version: str,
    processing_contract_version: str,
    scientific_inputs: Mapping[str, object],
) -> ProcessingRequestIdentity:
    """Build a canonical request identity from resolved scientific inputs."""

    if not isinstance(acquisition_sources, tuple):
        try:
            acquisition_sources = tuple(acquisition_sources)
        except (TypeError, ValueError) as exc:
            raise IdentityContractError("acquisition_sources must be iterable") from exc
    if not isinstance(acquisition_bindings, tuple):
        try:
            acquisition_bindings = tuple(acquisition_bindings)
        except (TypeError, ValueError) as exc:
            raise IdentityContractError(
                "acquisition_bindings must be iterable"
            ) from exc
    if any(
        not isinstance(source, AcquisitionSourceIdentity)
        for source in acquisition_sources
    ):
        raise IdentityContractError("acquisition_sources contains an invalid identity")
    if any(
        not isinstance(binding, AcquisitionInputBinding)
        for binding in acquisition_bindings
    ):
        raise IdentityContractError("acquisition_bindings contains an invalid binding")
    normalized_json = _canonical_object_json(scientific_inputs)
    source_ids = tuple(source.acquisition_source_id for source in acquisition_sources)
    if source_ids != tuple(sorted(source_ids)):
        acquisition_sources = tuple(
            sorted(acquisition_sources, key=lambda source: source.acquisition_source_id)
        )
    bindings_key = tuple(
        (
            binding.acquisition_source_id,
            binding.adapter_input_key,
            binding.injection_order,
        )
        for binding in acquisition_bindings
    )
    if bindings_key != tuple(sorted(bindings_key)):
        acquisition_bindings = tuple(
            sorted(
                acquisition_bindings,
                key=lambda binding: (
                    binding.acquisition_source_id,
                    binding.adapter_input_key,
                    binding.injection_order,
                ),
            )
        )
    basis = _request_basis(
        acquisition_sources=acquisition_sources,
        acquisition_bindings=acquisition_bindings,
        scientific_input_schema_version=scientific_input_schema_version,
        processing_algorithm_version=processing_algorithm_version,
        processing_contract_version=processing_contract_version,
        normalized_scientific_inputs_json=normalized_json,
    )
    request_id = _typed_digest(_PROCESSING_REQUEST_PREFIX, basis)
    return ProcessingRequestIdentity(
        processing_request_id=request_id,
        acquisition_sources=acquisition_sources,
        acquisition_bindings=acquisition_bindings,
        scientific_input_schema_version=scientific_input_schema_version,
        processing_algorithm_version=processing_algorithm_version,
        processing_contract_version=processing_contract_version,
        normalized_scientific_inputs_json=normalized_json,
    )


def issue_processing_attempt(
    request: ProcessingRequestIdentity,
    *,
    retry_of: ProcessingAttemptIdentity | None = None,
) -> ProcessingAttemptIdentity:
    """Issue a fresh attempt token, optionally linked to a same-request retry."""

    if not isinstance(request, ProcessingRequestIdentity):
        raise IdentityContractError("request must be a ProcessingRequestIdentity")
    if retry_of is not None:
        if not isinstance(retry_of, ProcessingAttemptIdentity):
            raise IdentityContractError("retry_of must be a ProcessingAttemptIdentity")
        if retry_of.processing_request_id != request.processing_request_id:
            raise IdentityContractError(
                "retry predecessor references a different processing request"
            )
    attempt_token = uuid.uuid4().hex
    attempt_id = _typed_digest(
        _PROCESSING_ATTEMPT_PREFIX,
        _attempt_basis(
            processing_request_id=request.processing_request_id,
            attempt_token=attempt_token,
            retry_of_attempt_id=retry_of.processing_attempt_id if retry_of else None,
        ),
    )
    return ProcessingAttemptIdentity(
        processing_attempt_id=attempt_id,
        processing_request_id=request.processing_request_id,
        attempt_token=attempt_token,
        retry_of=retry_of,
    )


def compose_processing_owner_references(
    *,
    request: ProcessingRequestIdentity,
    attempt: ProcessingAttemptIdentity,
    sample_snapshot: SampleMetadataSnapshot,
    sample_resolutions: tuple[ResolvedCanonicalSampleIdentity, ...],
    target_snapshot: TargetListSnapshot | None = None,
    evidence_artifacts: tuple[EvidenceArtifactRef, ...] = (),
) -> ProcessingOwnerReferences:
    """Compose owner references without minting or inferring either identity."""

    try:
        canonical_artifacts = tuple(
            sorted(
                evidence_artifacts,
                key=lambda artifact: artifact.evidence_artifact_id,
            )
        )
    except (AttributeError, TypeError) as exc:
        raise IdentityContractError(
            "evidence_artifacts must contain owner-minted EvidenceArtifactRef values"
        ) from exc
    return ProcessingOwnerReferences(
        request=request,
        attempt=attempt,
        sample_snapshot=sample_snapshot,
        sample_resolutions=sample_resolutions,
        target_snapshot=target_snapshot,
        evidence_artifacts=canonical_artifacts,
    )


def resolve_request_acquisition_source(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> AcquisitionSourceIdentity:
    """Resolve one sample's exact request-bound acquisition source reference."""

    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise IdentityContractError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise IdentityContractError(
            "sample must be an owner-resolved canonical sample identity"
        )
    if sample not in processing_owners.sample_resolutions:
        raise IdentityContractError(
            "sample references a different sample outside the processing owner "
            "acquisition closure"
        )
    matching_bindings = tuple(
        binding
        for binding in processing_owners.request.acquisition_bindings
        if binding.adapter_input_key == sample.lookup_token
    )
    if len(matching_bindings) != 1:
        raise IdentityContractError(
            "sample must resolve to exactly one request acquisition binding"
        )
    acquisition_source_id = matching_bindings[0].acquisition_source_id
    matching_sources = tuple(
        source
        for source in processing_owners.request.acquisition_sources
        if source.acquisition_source_id == acquisition_source_id
    )
    if len(matching_sources) != 1:
        raise IdentityContractError(
            "request acquisition binding must resolve to exactly one source identity"
        )
    return matching_sources[0]


__all__ = [
    "AcquisitionInputBinding",
    "AcquisitionSourceIdentity",
    "FileContentFingerprint",
    "IdentityContractError",
    "PROCESSING_REQUEST_ID_PREFIX",
    "ProcessingAttemptIdentity",
    "ProcessingOwnerReferences",
    "ProcessingRequestIdentity",
    "build_acquisition_source_identity",
    "build_processing_request_identity",
    "canonical_identity_basis_bytes",
    "mint_typed_identity",
    "canonical_current_attempt_artifact_closure",
    "canonical_evidence_artifact_closure",
    "compose_processing_owner_references",
    "fingerprint_file_content",
    "issue_processing_attempt",
    "retry_predecessor_attempt_ids",
    "resolve_request_acquisition_source",
    "typed_identity_digest",
    "validate_typed_identity_id",
]
