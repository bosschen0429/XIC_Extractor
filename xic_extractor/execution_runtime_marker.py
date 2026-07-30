"""Owner-path-supplied durable recovery marker for one runtime attempt.

The marker is runtime recovery metadata only.  Its caller owns the path,
namespace, retention, and any later publication decision.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from xic_extractor.execution_runtime import AttemptRuntime, AttemptTerminalOutcome
from xic_extractor.execution_runtime_finalization import (
    CoordinatorTerminalRecord,
    RuntimeArtifactDisposition,
    RuntimeFinalizationContractError,
    SupervisorFinalizationRecord,
    accept_supervisor_finalization,
    canonical_coordinator_terminal_record_bytes,
)
from xic_extractor.execution_runtime_observability import (
    canonical_runtime_observability_ledger_bytes,
)
from xic_extractor.processing_identity import ProcessingAttemptIdentity

_MARKER_LOCK_TIMEOUT_SECONDS = 2.0
_MARKER_LOCK_POLL_SECONDS = 0.01


class RuntimeMarkerState(str, Enum):
    LAUNCHED = "launched"
    FINALIZED = "finalized"


@dataclass(frozen=True, slots=True)
class RuntimeMarkerSnapshot:
    state: RuntimeMarkerState
    processing_request_id: str
    processing_attempt_id: str
    artifact_namespace: str
    manifest_work_unit_keys: tuple[str, ...]
    coordinator_record_sha256: str | None = None
    coordinator_record_bytes: bytes | None = None
    observability_ledger_sha256: str | None = None
    observability_ledger_bytes: bytes | None = None
    terminal_outcome: AttemptTerminalOutcome | None = None
    artifact_dispositions: tuple[tuple[str, RuntimeArtifactDisposition], ...] = ()


@dataclass(frozen=True, slots=True)
class CoordinatorLossRecovery:
    terminal_outcome: AttemptTerminalOutcome
    finalization_acknowledged: bool
    quarantine_artifact_namespace: bool
    artifact_namespace: str
    coordinator_record_bytes: bytes | None = None
    observability_ledger_bytes: bytes | None = None
    artifact_dispositions: tuple[tuple[str, RuntimeArtifactDisposition], ...] = ()


@dataclass(frozen=True, slots=True)
class OwnerPathRuntimeMarker:
    """Atomically replace one caller-located launch marker with finalization."""

    path: Path
    artifact_namespace: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path) or not self.path.name:
            raise RuntimeFinalizationContractError(
                "path must be an owner-supplied marker file path"
            )
        _required_text(self.artifact_namespace, field="artifact_namespace")

    def record_launch(self, runtime: AttemptRuntime) -> RuntimeMarkerSnapshot:
        if not isinstance(runtime, AttemptRuntime):
            raise RuntimeFinalizationContractError("runtime must be an AttemptRuntime")
        attempt = runtime.manifest.attempt
        proposed = RuntimeMarkerSnapshot(
            state=RuntimeMarkerState.LAUNCHED,
            processing_request_id=attempt.processing_request_id,
            processing_attempt_id=attempt.processing_attempt_id,
            artifact_namespace=self.artifact_namespace,
            manifest_work_unit_keys=runtime.manifest.work_unit_keys,
        )
        with self._exclusive_transaction():
            existing = self.read_optional()
            if existing is not None:
                if existing == proposed:
                    return existing
                if (
                    existing.state is RuntimeMarkerState.FINALIZED
                    and _same_attempt_scope(existing, proposed)
                ):
                    raise RuntimeFinalizationContractError(
                        "runtime attempt is already durably finalized"
                    )
                raise RuntimeFinalizationContractError(
                    "runtime marker path already contains a different attempt scope"
                )
            self._write(proposed)
            return proposed

    def __call__(
        self,
        coordinator: CoordinatorTerminalRecord,
        proposed: SupervisorFinalizationRecord,
    ) -> SupervisorFinalizationRecord:
        accepted = accept_supervisor_finalization(
            coordinator,
            proposed,
            observability_ledger=proposed.observability_ledger,
        )
        coordinator_bytes = canonical_coordinator_terminal_record_bytes(coordinator)
        observability_bytes = canonical_runtime_observability_ledger_bytes(
            accepted.observability_ledger
        )
        with self._exclusive_transaction():
            launch = self.read()
            attempt = coordinator.state.runtime.manifest.attempt
            expected_scope = RuntimeMarkerSnapshot(
                state=RuntimeMarkerState.LAUNCHED,
                processing_request_id=attempt.processing_request_id,
                processing_attempt_id=attempt.processing_attempt_id,
                artifact_namespace=self.artifact_namespace,
                manifest_work_unit_keys=(
                    coordinator.state.runtime.manifest.work_unit_keys
                ),
            )
            if not _same_attempt_scope(launch, expected_scope):
                raise RuntimeFinalizationContractError(
                    "runtime finalization does not match the durable launch marker"
                )
            finalized = RuntimeMarkerSnapshot(
                state=RuntimeMarkerState.FINALIZED,
                processing_request_id=launch.processing_request_id,
                processing_attempt_id=launch.processing_attempt_id,
                artifact_namespace=launch.artifact_namespace,
                manifest_work_unit_keys=launch.manifest_work_unit_keys,
                coordinator_record_sha256=accepted.coordinator_record_sha256,
                coordinator_record_bytes=coordinator_bytes,
                observability_ledger_sha256=hashlib.sha256(
                    observability_bytes
                ).hexdigest(),
                observability_ledger_bytes=observability_bytes,
                terminal_outcome=accepted.terminal_outcome,
                artifact_dispositions=accepted.artifact_dispositions,
            )
            if launch.state is RuntimeMarkerState.FINALIZED:
                if launch != finalized:
                    raise RuntimeFinalizationContractError(
                        "runtime marker already contains conflicting finalization"
                    )
                return accepted
            self._write(finalized)
            return accepted

    def recover_after_coordinator_loss(
        self,
        attempt: ProcessingAttemptIdentity,
    ) -> CoordinatorLossRecovery:
        if not isinstance(attempt, ProcessingAttemptIdentity):
            raise RuntimeFinalizationContractError(
                "attempt must be an owner-issued ProcessingAttemptIdentity"
            )
        snapshot = self.read()
        if (
            snapshot.processing_request_id != attempt.processing_request_id
            or snapshot.processing_attempt_id != attempt.processing_attempt_id
            or snapshot.artifact_namespace != self.artifact_namespace
        ):
            raise RuntimeFinalizationContractError(
                "runtime marker does not match the requested recovery scope"
            )
        if snapshot.state is RuntimeMarkerState.LAUNCHED:
            return CoordinatorLossRecovery(
                terminal_outcome=AttemptTerminalOutcome.FAILED,
                finalization_acknowledged=False,
                quarantine_artifact_namespace=True,
                artifact_namespace=snapshot.artifact_namespace,
            )
        if snapshot.terminal_outcome is None:
            raise RuntimeFinalizationContractError(
                "finalized runtime marker is missing terminal outcome"
            )
        return CoordinatorLossRecovery(
            terminal_outcome=snapshot.terminal_outcome,
            finalization_acknowledged=True,
            quarantine_artifact_namespace=False,
            artifact_namespace=snapshot.artifact_namespace,
            coordinator_record_bytes=snapshot.coordinator_record_bytes,
            observability_ledger_bytes=snapshot.observability_ledger_bytes,
            artifact_dispositions=snapshot.artifact_dispositions,
        )

    def read(self) -> RuntimeMarkerSnapshot:
        snapshot = self.read_optional()
        if snapshot is None:
            raise RuntimeFinalizationContractError("runtime marker does not exist")
        return snapshot

    def read_optional(self) -> RuntimeMarkerSnapshot | None:
        try:
            payload_bytes = self.path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            payload = json.loads(payload_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeFinalizationContractError(
                "runtime marker is not valid canonical JSON"
            ) from exc
        snapshot = _snapshot_from_payload(payload)
        if _canonical_snapshot_bytes(snapshot) != payload_bytes:
            raise RuntimeFinalizationContractError(
                "runtime marker bytes are not canonical"
            )
        return snapshot

    def _write(self, snapshot: RuntimeMarkerSnapshot) -> None:
        if not self.path.parent.is_dir():
            raise RuntimeFinalizationContractError(
                "owner-supplied runtime marker parent must already exist"
            )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(_canonical_snapshot_bytes(snapshot))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            if os.name != "nt":
                directory_descriptor = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    @contextmanager
    def _exclusive_transaction(self) -> Iterator[None]:
        if not self.path.parent.is_dir():
            raise RuntimeFinalizationContractError(
                "owner-supplied runtime marker parent must already exist"
            )
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        deadline = time.monotonic() + _MARKER_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeFinalizationContractError(
                        "runtime marker transaction is locked; concurrent or stale "
                        "owner lock must be resolved before retry"
                    ) from exc
                time.sleep(_MARKER_LOCK_POLL_SECONDS)
                continue
            os.close(descriptor)
            break
        try:
            yield
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def _same_attempt_scope(
    left: RuntimeMarkerSnapshot,
    right: RuntimeMarkerSnapshot,
) -> bool:
    return (
        left.processing_request_id == right.processing_request_id
        and left.processing_attempt_id == right.processing_attempt_id
        and left.artifact_namespace == right.artifact_namespace
        and left.manifest_work_unit_keys == right.manifest_work_unit_keys
    )


def _canonical_snapshot_bytes(snapshot: RuntimeMarkerSnapshot) -> bytes:
    payload = _snapshot_payload(snapshot)
    payload["marker_sha256"] = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _snapshot_payload(snapshot: RuntimeMarkerSnapshot) -> dict[str, Any]:
    return {
        "artifact_dispositions": [
            [artifact_id, disposition.value]
            for artifact_id, disposition in snapshot.artifact_dispositions
        ],
        "artifact_namespace": snapshot.artifact_namespace,
        "coordinator_record_base64": (
            None
            if snapshot.coordinator_record_bytes is None
            else base64.b64encode(snapshot.coordinator_record_bytes).decode("ascii")
        ),
        "coordinator_record_sha256": snapshot.coordinator_record_sha256,
        "observability_ledger_base64": (
            None
            if snapshot.observability_ledger_bytes is None
            else base64.b64encode(snapshot.observability_ledger_bytes).decode("ascii")
        ),
        "observability_ledger_sha256": snapshot.observability_ledger_sha256,
        "manifest_work_unit_keys": list(snapshot.manifest_work_unit_keys),
        "processing_attempt_id": snapshot.processing_attempt_id,
        "processing_request_id": snapshot.processing_request_id,
        "schema_version": "execution_runtime_recovery_marker_v3",
        "state": snapshot.state.value,
        "terminal_outcome": (
            None
            if snapshot.terminal_outcome is None
            else snapshot.terminal_outcome.value
        ),
    }


def _snapshot_from_payload(payload: Any) -> RuntimeMarkerSnapshot:
    if not isinstance(payload, dict):
        raise RuntimeFinalizationContractError("runtime marker must be an object")
    expected_keys = {
        "artifact_dispositions",
        "artifact_namespace",
        "coordinator_record_base64",
        "coordinator_record_sha256",
        "observability_ledger_base64",
        "observability_ledger_sha256",
        "manifest_work_unit_keys",
        "marker_sha256",
        "processing_attempt_id",
        "processing_request_id",
        "schema_version",
        "state",
        "terminal_outcome",
    }
    if set(payload) != expected_keys:
        raise RuntimeFinalizationContractError(
            "runtime marker fields do not match the recovery schema"
        )
    if payload["schema_version"] != "execution_runtime_recovery_marker_v3":
        raise RuntimeFinalizationContractError(
            "runtime marker schema_version is unsupported"
        )
    try:
        state = RuntimeMarkerState(payload["state"])
        terminal_outcome = (
            None
            if payload["terminal_outcome"] is None
            else AttemptTerminalOutcome(payload["terminal_outcome"])
        )
        dispositions = tuple(
            (
                _required_text(artifact_id, field="artifact_id"),
                RuntimeArtifactDisposition(disposition),
            )
            for artifact_id, disposition in payload["artifact_dispositions"]
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeFinalizationContractError(
            "runtime marker contains invalid enum or disposition values"
        ) from exc
    work_unit_keys = payload["manifest_work_unit_keys"]
    if not isinstance(work_unit_keys, list) or any(
        not isinstance(key, str) or not key for key in work_unit_keys
    ):
        raise RuntimeFinalizationContractError(
            "runtime marker work keys must be non-empty text"
        )
    coordinator_sha256 = payload["coordinator_record_sha256"]
    if coordinator_sha256 is not None and (
        not isinstance(coordinator_sha256, str)
        or len(coordinator_sha256) != 64
        or any(character not in "0123456789abcdef" for character in coordinator_sha256)
    ):
        raise RuntimeFinalizationContractError(
            "runtime marker coordinator digest must be lowercase SHA-256"
        )
    coordinator_base64 = payload["coordinator_record_base64"]
    try:
        coordinator_bytes = (
            None
            if coordinator_base64 is None
            else base64.b64decode(coordinator_base64, validate=True)
        )
    except (binascii.Error, ValueError, TypeError) as exc:
        raise RuntimeFinalizationContractError(
            "runtime marker coordinator record must be canonical base64"
        ) from exc
    if coordinator_base64 is not None and not isinstance(coordinator_base64, str):
        raise RuntimeFinalizationContractError(
            "runtime marker coordinator record must be canonical base64"
        )
    observability_sha256 = payload["observability_ledger_sha256"]
    if observability_sha256 is not None and (
        not isinstance(observability_sha256, str)
        or len(observability_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in observability_sha256
        )
    ):
        raise RuntimeFinalizationContractError(
            "runtime marker observability digest must be lowercase SHA-256"
        )
    observability_base64 = payload["observability_ledger_base64"]
    try:
        observability_bytes = (
            None
            if observability_base64 is None
            else base64.b64decode(observability_base64, validate=True)
        )
    except (binascii.Error, ValueError, TypeError) as exc:
        raise RuntimeFinalizationContractError(
            "runtime marker observability ledger must be canonical base64"
        ) from exc
    if observability_base64 is not None and not isinstance(observability_base64, str):
        raise RuntimeFinalizationContractError(
            "runtime marker observability ledger must be canonical base64"
        )
    snapshot = RuntimeMarkerSnapshot(
        state=state,
        processing_request_id=_required_text(
            payload["processing_request_id"], field="processing_request_id"
        ),
        processing_attempt_id=_required_text(
            payload["processing_attempt_id"], field="processing_attempt_id"
        ),
        artifact_namespace=_required_text(
            payload["artifact_namespace"], field="artifact_namespace"
        ),
        manifest_work_unit_keys=tuple(work_unit_keys),
        coordinator_record_sha256=coordinator_sha256,
        coordinator_record_bytes=coordinator_bytes,
        observability_ledger_sha256=observability_sha256,
        observability_ledger_bytes=observability_bytes,
        terminal_outcome=terminal_outcome,
        artifact_dispositions=dispositions,
    )
    marker_sha256 = payload["marker_sha256"]
    if (
        not isinstance(marker_sha256, str)
        or len(marker_sha256) != 64
        or any(character not in "0123456789abcdef" for character in marker_sha256)
        or marker_sha256
        != hashlib.sha256(
            json.dumps(
                _snapshot_payload(snapshot),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
    ):
        raise RuntimeFinalizationContractError(
            "runtime marker digest does not match its recovery content"
        )
    if state is RuntimeMarkerState.LAUNCHED and (
        coordinator_sha256 is not None
        or coordinator_bytes is not None
        or observability_sha256 is not None
        or observability_bytes is not None
        or terminal_outcome is not None
        or dispositions
    ):
        raise RuntimeFinalizationContractError(
            "launch marker cannot contain finalization fields"
        )
    if state is RuntimeMarkerState.FINALIZED and (
        coordinator_sha256 is None
        or coordinator_bytes is None
        or observability_sha256 is None
        or observability_bytes is None
        or terminal_outcome is None
    ):
        raise RuntimeFinalizationContractError(
            "finalized marker requires coordinator and observability bytes, digests, "
            "and terminal outcome"
        )
    if (
        coordinator_bytes is not None
        and hashlib.sha256(coordinator_bytes).hexdigest() != coordinator_sha256
    ):
        raise RuntimeFinalizationContractError(
            "runtime marker coordinator digest does not match persisted bytes"
        )
    if (
        observability_bytes is not None
        and hashlib.sha256(observability_bytes).hexdigest() != observability_sha256
    ):
        raise RuntimeFinalizationContractError(
            "runtime marker observability digest does not match persisted bytes"
        )
    return snapshot


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RuntimeFinalizationContractError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


__all__ = [
    "CoordinatorLossRecovery",
    "OwnerPathRuntimeMarker",
    "RuntimeMarkerSnapshot",
    "RuntimeMarkerState",
]
