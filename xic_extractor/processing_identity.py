"""RAW source fingerprints checked where acquisition files are handed off."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter


class IdentityContractError(ValueError):
    """Raised when an immutable identity cannot satisfy its contract."""


class FingerprintCancelledError(IdentityContractError):
    """Raised when a caller cancels an in-progress content fingerprint."""


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_digest(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise IdentityContractError(f"{field} must be a lower-case SHA-256 hex digest")
    return value


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
    """One source bound either to exact content or a local filesystem revision."""

    content_sha256: str | None
    byte_size: int
    acquisition_format: str
    observation_namespace: str
    local_file_revision: tuple[int, int, int, int, int] | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.byte_size, bool)
            or not isinstance(self.byte_size, int)
            or self.byte_size < 0
        ):
            raise IdentityContractError("byte_size must be a non-negative integer")
        _require_nonempty_text(self.acquisition_format, field="acquisition_format")
        _require_nonempty_text(
            self.observation_namespace, field="observation_namespace"
        )
        if self.local_file_revision is None:
            _validate_digest(self.content_sha256, field="content_sha256")
        else:
            revision = self.local_file_revision
            if (
                self.content_sha256 is not None
                or not isinstance(revision, tuple)
                or len(revision) != 5
                or any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in revision
                )
                or revision[2] != self.byte_size
            ):
                raise IdentityContractError("invalid local acquisition file revision")

    @property
    def acquisition_source_id(self) -> str:
        """The source's file fingerprint: content digest or local file revision."""

        if self.local_file_revision is None:
            return f"sha256:{self.content_sha256}"
        return "local:" + ":".join(str(value) for value in self.local_file_revision)


@dataclass(frozen=True, slots=True)
class AcquisitionSourceExpectation:
    """Source identity plus the exact local path state observed with its request."""

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


def build_local_acquisition_source_identity(
    path: str | Path,
    *,
    acquisition_format: str,
    observation_namespace: str,
    should_cancel: Callable[[], bool] | None = None,
) -> AcquisitionSourceIdentity:
    """Bind a local file revision, without reading bytes or claiming byte equality."""
    if should_cancel is not None and should_cancel():
        raise InterruptedError("Acquisition source preparation was cancelled")
    state = Path(path).stat()
    if not stat.S_ISREG(state.st_mode):
        raise IdentityContractError("acquisition source must be a regular file")
    revision = (
        state.st_dev,
        state.st_ino,
        state.st_size,
        state.st_mtime_ns,
        state.st_ctime_ns,
    )
    return AcquisitionSourceIdentity(
        content_sha256=None,
        byte_size=state.st_size,
        acquisition_format=acquisition_format,
        observation_namespace=observation_namespace,
        local_file_revision=revision,
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
    return AcquisitionSourceIdentity(
        content_sha256=content_sha256,
        byte_size=byte_size,
        acquisition_format=acquisition_format,
        observation_namespace=observation_namespace,
    )


__all__ = [
    "AcquisitionSourceExpectation",
    "AcquisitionSourceIdentity",
    "FileContentFingerprint",
    "IdentityContractError",
    "build_acquisition_source_identity",
    "build_local_acquisition_source_identity",
    "fingerprint_file_content",
]
