"""Owner-path-supplied durable status marker for one runtime attempt.

The marker is runtime status metadata only.  Its caller owns the path,
namespace, retention, and any later publication decision.
"""

from __future__ import annotations

import errno
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from xic_extractor.execution._finalization import (
    RuntimeFinalizationContractError,
)
from xic_extractor.execution._lifecycle import AttemptRuntime, AttemptTerminalOutcome
from xic_extractor.execution.attempt import RuntimeFinalizationReceipt
from xic_extractor.processing_identity import (
    PROCESSING_REQUEST_ID_PREFIX,
    IdentityContractError,
    validate_typed_identity_id,
)
from xic_extractor.validation_primitives import (
    require_attempt_uuid,
    require_owner_normalized_text,
)

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
    terminal_outcome: AttemptTerminalOutcome | None = None


@dataclass(frozen=True, slots=True)
class OwnerPathRuntimeMarker:
    """Atomically replace one caller-located launch marker with finalization."""

    path: Path
    artifact_namespace: str
    allow_failed_attempt_retry: bool = False

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
                if (
                    self.allow_failed_attempt_retry
                    and existing.state is RuntimeMarkerState.FINALIZED
                    and existing.terminal_outcome
                    in {AttemptTerminalOutcome.FAILED, AttemptTerminalOutcome.CANCELLED}
                    and existing.artifact_namespace == self.artifact_namespace
                    and existing.processing_attempt_id != proposed.processing_attempt_id
                ):
                    self._archive_failed_attempt(existing)
                else:
                    raise RuntimeFinalizationContractError(
                        "runtime marker path already contains a different attempt scope"
                    )
            self._write(proposed)
            return proposed

    def _archive_failed_attempt(self, snapshot: RuntimeMarkerSnapshot) -> None:
        """Keep the terminal receipt before replacing the current-attempt pointer.

        Both writes occur under the current marker's transaction lock. If the
        new launch write fails, the old marker remains retryable and its archive
        is accepted on the next try only when the receipt is identical.
        """
        attempt_key = snapshot.processing_attempt_id
        archive = OwnerPathRuntimeMarker(
            self.path.with_name(f"{self.path.stem}.{attempt_key}{self.path.suffix}"),
            self.artifact_namespace,
        )
        retained = archive.read_optional()
        if retained is None:
            archive._write(snapshot)
        elif retained != snapshot:
            raise RuntimeFinalizationContractError(
                "runtime attempt archive contains conflicting finalization"
            )

    def __call__(
        self,
        receipt: RuntimeFinalizationReceipt,
    ) -> RuntimeFinalizationReceipt:
        """Persist exactly one typed finalization result."""

        if not isinstance(receipt, RuntimeFinalizationReceipt):
            raise RuntimeFinalizationContractError(
                "runtime marker requires a RuntimeFinalizationReceipt"
            )
        coordinator = receipt.coordinator
        accepted = receipt.supervisor
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
                terminal_outcome=accepted.terminal_outcome,
            )
            if launch.state is RuntimeMarkerState.FINALIZED:
                if launch != finalized:
                    raise RuntimeFinalizationContractError(
                        "runtime marker already contains conflicting finalization"
                    )
                return receipt
            self._write(finalized)
            return receipt

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
                "runtime marker is not valid JSON"
            ) from exc
        snapshot = _snapshot_from_payload(payload)
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
            except PermissionError as exc:
                if os.name != "nt":
                    raise
                winerror = getattr(exc, "winerror", None)
                transient_lock_denial = winerror in {32, 33}
                # A competing owner can remove its lock before this denial is handled.
                if winerror is None and exc.errno == errno.EACCES:
                    probe_path = lock_path.with_name(
                        f".{lock_path.name}.{time.monotonic_ns()}.probe"
                    )
                    try:
                        probe_descriptor = os.open(
                            probe_path,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                        )
                    except PermissionError:
                        raise exc from None
                    try:
                        os.close(probe_descriptor)
                    finally:
                        probe_path.unlink(missing_ok=True)
                    transient_lock_denial = True
                if not transient_lock_denial:
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeFinalizationContractError(
                        "runtime marker transaction is locked; concurrent or stale "
                        "owner lock must be resolved before retry"
                    ) from exc
                time.sleep(_MARKER_LOCK_POLL_SECONDS)
                continue
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
    return json.dumps(
        {
            "schema_version": "execution_runtime_status_marker.v3",
            "state": snapshot.state.value,
            "processing_request_id": snapshot.processing_request_id,
            "processing_attempt_id": snapshot.processing_attempt_id,
            "artifact_namespace": snapshot.artifact_namespace,
            "manifest_work_unit_keys": list(snapshot.manifest_work_unit_keys),
            "terminal_outcome": (
                None
                if snapshot.terminal_outcome is None
                else snapshot.terminal_outcome.value
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _snapshot_from_payload(payload: Any) -> RuntimeMarkerSnapshot:
    if not isinstance(payload, dict):
        raise RuntimeFinalizationContractError("runtime marker must be an object")
    if payload.get("schema_version") != "execution_runtime_status_marker.v3":
        raise RuntimeFinalizationContractError(
            "runtime marker schema_version is unsupported; "
            "rerun in a new output directory"
        )
    expected_keys = {
        "schema_version",
        "state",
        "processing_request_id",
        "processing_attempt_id",
        "artifact_namespace",
        "manifest_work_unit_keys",
        "terminal_outcome",
    }
    if set(payload) != expected_keys:
        raise RuntimeFinalizationContractError(
            "runtime marker fields do not match the status schema"
        )
    try:
        state = RuntimeMarkerState(payload["state"])
        terminal_outcome = (
            None
            if payload["terminal_outcome"] is None
            else AttemptTerminalOutcome(payload["terminal_outcome"])
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeFinalizationContractError(
            "runtime marker contains invalid enum values"
        ) from exc
    work_unit_keys = payload["manifest_work_unit_keys"]
    if not isinstance(work_unit_keys, list) or any(
        not isinstance(key, str) or not key for key in work_unit_keys
    ):
        raise RuntimeFinalizationContractError(
            "runtime marker work keys must be non-empty text"
        )
    if state is RuntimeMarkerState.LAUNCHED and terminal_outcome is not None:
        raise RuntimeFinalizationContractError(
            "launch marker cannot contain finalization fields"
        )
    if state is RuntimeMarkerState.FINALIZED and terminal_outcome is None:
        raise RuntimeFinalizationContractError(
            "finalized marker requires terminal outcome"
        )
    try:
        request_id = validate_typed_identity_id(
            payload["processing_request_id"],
            prefix=PROCESSING_REQUEST_ID_PREFIX,
            field="processing_request_id",
        )
        attempt_id = require_attempt_uuid(
            payload["processing_attempt_id"], error_factory=IdentityContractError
        )
    except IdentityContractError as exc:
        raise RuntimeFinalizationContractError(
            "runtime marker contains an invalid processing identity"
        ) from exc
    return RuntimeMarkerSnapshot(
        state=state,
        processing_request_id=request_id,
        processing_attempt_id=attempt_id,
        artifact_namespace=_required_text(
            payload["artifact_namespace"], field="artifact_namespace"
        ),
        manifest_work_unit_keys=tuple(work_unit_keys),
        terminal_outcome=terminal_outcome,
    )


def _required_text(value: object, *, field: str) -> str:
    return require_owner_normalized_text(
        value, field=field, error_factory=RuntimeFinalizationContractError
    )


__all__ = [
    "OwnerPathRuntimeMarker",
    "RuntimeMarkerSnapshot",
    "RuntimeMarkerState",
]
