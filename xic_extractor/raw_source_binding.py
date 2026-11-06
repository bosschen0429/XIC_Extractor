"""Verify local RAW revisions at file admission, replay and vendor-open handoffs."""

import os
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    IdentityContractError,
    fingerprint_file_content,
)
from xic_extractor.raw_reader_errors import RawReaderError

THERMO_RAW_ACQUISITION_FORMAT = "thermo_raw"
THERMO_RAW_OBSERVATION_NAMESPACE = "thermo_raw_file_reader:Device.MS:1"


@dataclass(frozen=True)
class _VerifiedAcquisitionSourceBinding:
    source: AcquisitionSourceIdentity
    path_identity: tuple[int, int, int, int, int]


def _verify_expected_acquisition_source(
    path: Path,
    expected: AcquisitionSourceIdentity | None,
) -> _VerifiedAcquisitionSourceBinding | None:
    if expected is None:
        return None
    if not isinstance(expected, AcquisitionSourceIdentity):
        raise RawReaderError(
            "expected_acquisition_source must be an AcquisitionSourceIdentity"
        )
    if expected.local_file_revision is not None:
        return bind_expected_path_identity(path, expected, expected.local_file_revision)
    if (
        expected.acquisition_format != THERMO_RAW_ACQUISITION_FORMAT
        or expected.observation_namespace != THERMO_RAW_OBSERVATION_NAMESPACE
    ):
        raise RawReaderError(
            "expected acquisition source does not describe the Thermo RAW "
            "MS-controller-1 adapter"
        )
    try:
        before = path.stat()
        fingerprint = fingerprint_file_content(path)
        after = path.stat()
    except IdentityContractError as exc:
        raise RawReaderError(
            "unable to verify the expected acquisition source"
        ) from exc
    except OSError as exc:
        raise RawReaderError(
            "unable to verify the expected acquisition source"
        ) from exc
    if not os.path.samestat(before, after) or stat_identity(before) != stat_identity(
        after
    ):
        raise RawReaderError("RAW source path changed while it was being verified")
    if (
        fingerprint.content_sha256 != expected.content_sha256
        or fingerprint.byte_size != expected.byte_size
    ):
        raise RawReaderError(
            "RAW path content does not match expected acquisition source"
        )
    return _VerifiedAcquisitionSourceBinding(
        source=expected,
        path_identity=stat_identity(after),
    )


def bind_expected_path_identity(
    path: Path,
    expected: AcquisitionSourceIdentity | None,
    expected_path_identity: tuple[int, int, int, int, int],
) -> _VerifiedAcquisitionSourceBinding:
    if not isinstance(expected, AcquisitionSourceIdentity):
        raise RawReaderError(
            "expected path identity requires an AcquisitionSourceIdentity"
        )
    if (
        expected.acquisition_format != THERMO_RAW_ACQUISITION_FORMAT
        or expected.observation_namespace != THERMO_RAW_OBSERVATION_NAMESPACE
    ):
        raise RawReaderError(
            "expected acquisition source does not describe the Thermo RAW "
            "MS-controller-1 adapter"
        )
    try:
        current = stat_identity(path.stat())
    except OSError as exc:
        raise RawReaderError("unable to verify the expected RAW path state") from exc
    if current != expected_path_identity or current[2] != expected.byte_size:
        raise RawReaderError(
            "RAW path state does not match expected acquisition source"
        )
    return _VerifiedAcquisitionSourceBinding(
        source=expected,
        path_identity=current,
    )


def _verify_source_path_still_bound(
    path: Path,
    binding: _VerifiedAcquisitionSourceBinding | None,
    *,
    verify_content: bool = True,
) -> None:
    if binding is None:
        return
    if not verify_content:
        try:
            current_path_identity = stat_identity(path.stat())
        except OSError as exc:
            raise RawReaderError(
                "RAW source path changed while the vendor handle was opening"
            ) from exc
        if current_path_identity != binding.path_identity:
            raise RawReaderError(
                "RAW source path changed while the vendor handle was opening"
            )
        return
    try:
        current_binding = _verify_expected_acquisition_source(
            path,
            binding.source,
        )
    except RawReaderError as exc:
        raise RawReaderError(
            "RAW source content or path changed while the vendor handle was opening"
        ) from exc
    if (
        current_binding is None
        or current_binding.path_identity != binding.path_identity
    ):
        raise RawReaderError(
            "RAW source content or path changed while the vendor handle was opening"
        )


def stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
