"""Capability-aware no-I/O contract over the existing XIC request/trace seam."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass
from dataclasses import field as dataclass_field
from enum import Enum
from typing import Protocol, final

import numpy as np

from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.xic_models import XICRequest, XICTrace


class AcquisitionContractError(ValueError):
    """Raised when an adapter cannot truthfully satisfy its declared capability."""


_RAW_SESSION_CAPABILITY_MINT_TOKEN = object()
_RAW_SESSION_RECEIPT_MINT_TOKEN = object()


@final
@dataclass(frozen=True, slots=True)
class RawAcquisitionSessionCapability:
    """Unique parent-issued authority for one physical RAW-open session."""

    capability_id: str
    session_nonce: str
    acquisition_source: AcquisitionSourceIdentity
    _mint_token: InitVar[object] = dataclass_field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("RawAcquisitionSessionCapability is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RAW_SESSION_CAPABILITY_MINT_TOKEN:
            raise AcquisitionContractError(
                "RAW session capabilities must be minted by acquisition owner"
            )
        if not isinstance(self.acquisition_source, AcquisitionSourceIdentity):
            raise AcquisitionContractError(
                "acquisition_source must be an owner-minted identity"
            )
        nonce = self.session_nonce
        if (
            not isinstance(nonce, str)
            or len(nonce) != 32
            or any(character not in "0123456789abcdef" for character in nonce)
        ):
            raise AcquisitionContractError(
                "session_nonce must be a lower-case 32-character hex token"
            )
        expected = _raw_session_capability_id(
            self.acquisition_source,
            session_nonce=nonce,
        )
        if self.capability_id != expected:
            raise AcquisitionContractError(
                "capability_id mismatches RAW source and session nonce"
            )


@final
@dataclass(frozen=True, slots=True)
class RawAcquisitionSessionReceipt:
    """RAW-reader-minted proof that one bound vendor handle opened."""

    receipt_id: str
    capability: RawAcquisitionSessionCapability
    _mint_token: InitVar[object] = dataclass_field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("RawAcquisitionSessionReceipt is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RAW_SESSION_RECEIPT_MINT_TOKEN:
            raise AcquisitionContractError(
                "RAW session receipts must be minted by raw_reader"
            )
        if not isinstance(self.capability, RawAcquisitionSessionCapability):
            raise AcquisitionContractError(
                "capability must be an owner-minted RAW session capability"
            )
        expected = _typed_id(
            "xic.raw-acquisition-session-receipt.v1:",
            {
                "schema": "raw_acquisition_session_receipt_v1",
                "capability_id": self.capability.capability_id,
                "acquisition_source_id": (
                    self.capability.acquisition_source.acquisition_source_id
                ),
                "raw_open_count": 1,
            },
        )
        if self.receipt_id != expected:
            raise AcquisitionContractError(
                "receipt_id mismatches RAW session capability"
            )


def mint_raw_acquisition_session_capability(
    acquisition_source: AcquisitionSourceIdentity,
) -> RawAcquisitionSessionCapability:
    """Mint one unique, pickleable capability for a planned RAW open."""

    if not isinstance(acquisition_source, AcquisitionSourceIdentity):
        raise AcquisitionContractError(
            "acquisition_source must be an owner-minted identity"
        )
    session_nonce = secrets.token_hex(16)
    return RawAcquisitionSessionCapability(
        capability_id=_raw_session_capability_id(
            acquisition_source,
            session_nonce=session_nonce,
        ),
        session_nonce=session_nonce,
        acquisition_source=acquisition_source,
        _mint_token=_RAW_SESSION_CAPABILITY_MINT_TOKEN,
    )


def _mint_raw_acquisition_session_receipt(
    capability: RawAcquisitionSessionCapability,
) -> RawAcquisitionSessionReceipt:
    """Private RAW-reader seam; mint only after the vendor handle has opened."""

    if not isinstance(capability, RawAcquisitionSessionCapability):
        raise AcquisitionContractError(
            "capability must be an owner-minted RAW session capability"
        )
    return RawAcquisitionSessionReceipt(
        receipt_id=_typed_id(
            "xic.raw-acquisition-session-receipt.v1:",
            {
                "schema": "raw_acquisition_session_receipt_v1",
                "capability_id": capability.capability_id,
                "acquisition_source_id": (
                    capability.acquisition_source.acquisition_source_id
                ),
                "raw_open_count": 1,
            },
        ),
        capability=capability,
        _mint_token=_RAW_SESSION_RECEIPT_MINT_TOKEN,
    )


def _raw_session_capability_id(
    acquisition_source: AcquisitionSourceIdentity,
    *,
    session_nonce: str,
) -> str:
    return _typed_id(
        "xic.raw-acquisition-session-capability.v1:",
        {
            "schema": "raw_acquisition_session_capability_v1",
            "acquisition_source_id": acquisition_source.acquisition_source_id,
            "session_nonce": session_nonce,
        },
    )


class TraceCoordinateKind(str, Enum):
    NATIVE_SCAN = "native_scan"
    CONVERSION_SCAN = "conversion_scan"
    RT_ONLY = "rt_only"


class BatchXICAdapter(Protocol):
    @property
    def raw_chromatogram_call_count(self) -> int: ...

    def extract_xic_many(
        self, requests: Sequence[XICRequest]
    ) -> Sequence[XICTrace]: ...

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]: ...


@dataclass(frozen=True, slots=True)
class TraceAcquisitionCapability:
    capability_id: str
    acquisition_source: AcquisitionSourceIdentity
    adapter_namespace: str
    coordinate_kind: TraceCoordinateKind

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition_source, AcquisitionSourceIdentity):
            raise AcquisitionContractError(
                "acquisition_source must be an owner-minted identity"
            )
        namespace = _required_text(self.adapter_namespace, "adapter_namespace")
        if not isinstance(self.coordinate_kind, TraceCoordinateKind):
            raise AcquisitionContractError(
                "coordinate_kind must be a TraceCoordinateKind"
            )
        expected = _typed_id(
            "xic.trace-acquisition-capability.v1:",
            {
                "schema": "trace_acquisition_capability_v1",
                "acquisition_source_id": (
                    self.acquisition_source.acquisition_source_id
                ),
                "adapter_namespace": namespace,
                "coordinate_kind": self.coordinate_kind.value,
                "batch_xic": True,
            },
        )
        if self.capability_id != expected:
            raise AcquisitionContractError(
                "capability_id mismatches source and adapter capability"
            )


@dataclass(frozen=True, slots=True)
class TraceCoordinate:
    trace_coordinate_id: str
    capability_id: str
    acquisition_source_id: str
    request: XICRequest
    coordinate_kind: TraceCoordinateKind
    first_scan: int | None
    last_scan: int | None
    trace_point_count: int

    def __post_init__(self) -> None:
        _required_text(self.capability_id, "capability_id")
        _required_text(self.acquisition_source_id, "acquisition_source_id")
        if not isinstance(self.request, XICRequest):
            raise AcquisitionContractError("request must be an XICRequest")
        if not isinstance(self.coordinate_kind, TraceCoordinateKind):
            raise AcquisitionContractError(
                "coordinate_kind must be a TraceCoordinateKind"
            )
        if (
            isinstance(self.trace_point_count, bool)
            or not isinstance(self.trace_point_count, int)
            or self.trace_point_count < 0
        ):
            raise AcquisitionContractError(
                "trace_point_count must be a non-negative integer"
            )
        if self.coordinate_kind is TraceCoordinateKind.RT_ONLY:
            if self.first_scan is not None or self.last_scan is not None:
                raise AcquisitionContractError("rt_only coordinates cannot claim scans")
        else:
            if (
                isinstance(self.first_scan, bool)
                or not isinstance(self.first_scan, int)
                or isinstance(self.last_scan, bool)
                or not isinstance(self.last_scan, int)
                or self.first_scan < 0
                or self.last_scan < self.first_scan
            ):
                raise AcquisitionContractError(
                    "scan coordinates require an ordered non-negative window"
                )
        expected = _typed_id(
            "xic.trace-coordinate.v1:",
            _trace_coordinate_basis(
                capability_id=self.capability_id,
                acquisition_source_id=self.acquisition_source_id,
                request=self.request,
                coordinate_kind=self.coordinate_kind,
                first_scan=self.first_scan,
                last_scan=self.last_scan,
                trace_point_count=self.trace_point_count,
            ),
        )
        if self.trace_coordinate_id != expected:
            raise AcquisitionContractError(
                "trace_coordinate_id mismatches exact request coordinates"
            )


@dataclass(frozen=True, slots=True)
class AcquiredTrace:
    coordinate: TraceCoordinate
    trace: XICTrace

    def __post_init__(self) -> None:
        if not isinstance(self.coordinate, TraceCoordinate):
            raise AcquisitionContractError("coordinate must be a TraceCoordinate")
        if not isinstance(self.trace, XICTrace):
            raise AcquisitionContractError("trace must be an XICTrace")
        rt = np.asarray(self.trace.rt)
        intensity = np.asarray(self.trace.intensity)
        if rt.ndim != 1 or intensity.ndim != 1 or rt.shape != intensity.shape:
            raise AcquisitionContractError(
                "trace arrays must be matching one-dimensional arrays"
            )
        if len(rt) != self.coordinate.trace_point_count:
            raise AcquisitionContractError("trace length mismatches trace coordinates")
        if not np.all(np.isfinite(rt)) or not np.all(np.isfinite(intensity)):
            raise AcquisitionContractError("trace values must be finite")


_ACQUIRED_TRACE_SCHEMA_VERSION = "acquired_trace_v1"
_ACQUIRED_TRACE_KEYS = (
    "schema_version",
    "acquired_trace_content_id",
    "coordinate",
    "trace",
)
_TRACE_COORDINATE_KEYS = (
    "trace_coordinate_id",
    "capability_id",
    "acquisition_source_id",
    "request",
    "coordinate_kind",
    "first_scan",
    "last_scan",
    "trace_point_count",
)
_XIC_REQUEST_KEYS = ("mz", "rt_min", "rt_max", "ppm_tol")
_TRACE_ARRAY_KEYS = ("rt", "intensity")


def acquired_trace_content_id(acquired_trace: AcquiredTrace) -> str:
    """Return the acquisition-owner identity of coordinates and trace content."""

    content = _acquired_trace_content(acquired_trace)
    return _typed_id("xic.acquired-trace-content.v1:", content)


def serialize_acquired_trace(acquired_trace: AcquiredTrace) -> bytes:
    """Canonically encode a complete acquired trace without owner recomputation."""

    content = _acquired_trace_content(acquired_trace)
    return _canonical_json_bytes(
        {
            "schema_version": _ACQUIRED_TRACE_SCHEMA_VERSION,
            "acquired_trace_content_id": _typed_id(
                "xic.acquired-trace-content.v1:", content
            ),
            **content,
        }
    )


def deserialize_acquired_trace(
    payload: bytes | str,
    *,
    expected_acquisition_source_id: str | None = None,
    expected_trace_coordinate_id: str | None = None,
    expected_trace_content_id: str | None = None,
) -> AcquiredTrace:
    """Strictly reconstitute an acquired trace through acquisition-owner types."""

    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not isinstance(raw, bytes):
        raise TypeError("acquired trace payload must be bytes or str")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_mapping)
    except UnicodeDecodeError as exc:
        raise AcquisitionContractError("acquired trace payload must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise AcquisitionContractError(
            "acquired trace payload must be valid JSON"
        ) from exc
    root = _required_mapping(value, label="acquired trace")
    _require_keys(root, _ACQUIRED_TRACE_KEYS, label="acquired trace")
    if root["schema_version"] != _ACQUIRED_TRACE_SCHEMA_VERSION:
        raise AcquisitionContractError("unsupported acquired trace schema_version")

    coordinate_value = _required_mapping(root["coordinate"], label="coordinate")
    _require_keys(coordinate_value, _TRACE_COORDINATE_KEYS, label="coordinate")
    request_value = _required_mapping(
        coordinate_value["request"], label="coordinate request"
    )
    _require_keys(request_value, _XIC_REQUEST_KEYS, label="coordinate request")
    request = XICRequest(
        mz=_float_from_text(request_value["mz"], field="request.mz"),
        rt_min=_float_from_text(request_value["rt_min"], field="request.rt_min"),
        rt_max=_float_from_text(request_value["rt_max"], field="request.rt_max"),
        ppm_tol=_float_from_text(request_value["ppm_tol"], field="request.ppm_tol"),
    )
    try:
        coordinate_kind = TraceCoordinateKind(
            _required_text(coordinate_value["coordinate_kind"], "coordinate_kind")
        )
    except ValueError as exc:
        raise AcquisitionContractError("unknown trace coordinate kind") from exc
    coordinate = TraceCoordinate(
        trace_coordinate_id=_required_text(
            coordinate_value["trace_coordinate_id"], "trace_coordinate_id"
        ),
        capability_id=_required_text(
            coordinate_value["capability_id"], "capability_id"
        ),
        acquisition_source_id=_required_text(
            coordinate_value["acquisition_source_id"], "acquisition_source_id"
        ),
        request=request,
        coordinate_kind=coordinate_kind,
        first_scan=_optional_integer(coordinate_value["first_scan"], "first_scan"),
        last_scan=_optional_integer(coordinate_value["last_scan"], "last_scan"),
        trace_point_count=_nonnegative_integer(
            coordinate_value["trace_point_count"], "trace_point_count"
        ),
    )
    trace_value = _required_mapping(root["trace"], label="trace")
    _require_keys(trace_value, _TRACE_ARRAY_KEYS, label="trace")
    trace = XICTrace.from_arrays(
        tuple(
            _float_from_text(item, field="trace.rt")
            for item in _required_list(trace_value["rt"], label="trace.rt")
        ),
        tuple(
            _float_from_text(item, field="trace.intensity")
            for item in _required_list(
                trace_value["intensity"], label="trace.intensity"
            )
        ),
    )
    acquired = AcquiredTrace(coordinate=coordinate, trace=trace)
    content_id = acquired_trace_content_id(acquired)
    persisted_content_id = _required_text(
        root["acquired_trace_content_id"], "acquired_trace_content_id"
    )
    if persisted_content_id != content_id:
        raise AcquisitionContractError("acquired trace content identity mismatch")
    for actual, expected, label in (
        (
            coordinate.acquisition_source_id,
            expected_acquisition_source_id,
            "acquisition source",
        ),
        (
            coordinate.trace_coordinate_id,
            expected_trace_coordinate_id,
            "trace coordinate",
        ),
        (content_id, expected_trace_content_id, "trace content"),
    ):
        if expected is not None and actual != expected:
            raise AcquisitionContractError(f"acquired trace crosses expected {label}")
    if serialize_acquired_trace(acquired) != raw:
        raise AcquisitionContractError("acquired trace payload is not canonical")
    return acquired


def _acquired_trace_content(acquired_trace: AcquiredTrace) -> dict[str, object]:
    if not isinstance(acquired_trace, AcquiredTrace):
        raise TypeError("acquired_trace must be an AcquiredTrace")
    # Re-run the value contract in case an unsafe caller bypassed construction.
    AcquiredTrace(coordinate=acquired_trace.coordinate, trace=acquired_trace.trace)
    coordinate = acquired_trace.coordinate
    request = coordinate.request
    return {
        "coordinate": {
            "trace_coordinate_id": coordinate.trace_coordinate_id,
            "capability_id": coordinate.capability_id,
            "acquisition_source_id": coordinate.acquisition_source_id,
            "request": {
                "mz": _float_text(request.mz),
                "rt_min": _float_text(request.rt_min),
                "rt_max": _float_text(request.rt_max),
                "ppm_tol": _float_text(request.ppm_tol),
            },
            "coordinate_kind": coordinate.coordinate_kind.value,
            "first_scan": coordinate.first_scan,
            "last_scan": coordinate.last_scan,
            "trace_point_count": coordinate.trace_point_count,
        },
        "trace": {
            "rt": [_float_text(value) for value in acquired_trace.trace.rt],
            "intensity": [
                _float_text(value) for value in acquired_trace.trace.intensity
            ],
        },
    }


@dataclass(frozen=True, slots=True)
class TraceAcquisitionBatch:
    capability: TraceAcquisitionCapability
    traces: tuple[AcquiredTrace, ...]
    logical_xic_request_count: int
    xic_batch_count: int
    adapter_chromatogram_call_count: int
    trace_point_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.capability, TraceAcquisitionCapability):
            raise AcquisitionContractError(
                "capability must be a TraceAcquisitionCapability"
            )
        if not isinstance(self.traces, tuple) or any(
            not isinstance(trace, AcquiredTrace) for trace in self.traces
        ):
            raise AcquisitionContractError("traces must contain AcquiredTrace values")
        expected_counts = {
            "logical_xic_request_count": len(self.traces),
            "xic_batch_count": 1,
            "trace_point_count": sum(len(item.trace.rt) for item in self.traces),
        }
        for field, expected in expected_counts.items():
            if getattr(self, field) != expected:
                raise AcquisitionContractError(
                    f"{field} mismatches the acquired batch content"
                )
        _nonnegative_integer(
            self.adapter_chromatogram_call_count,
            "adapter_chromatogram_call_count",
        )
        if any(
            trace.coordinate.capability_id != self.capability.capability_id
            for trace in self.traces
        ):
            raise AcquisitionContractError(
                "all trace coordinates must bind the batch capability"
            )
        expected_source_id = (
            self.capability.acquisition_source.acquisition_source_id
        )
        if any(
            trace.coordinate.acquisition_source_id != expected_source_id
            for trace in self.traces
        ):
            raise AcquisitionContractError(
                "all trace coordinates must bind the capability source"
            )
        if any(
            trace.coordinate.coordinate_kind is not self.capability.coordinate_kind
            for trace in self.traces
        ):
            raise AcquisitionContractError(
                "all trace coordinates must bind the capability coordinate kind"
            )


def mint_trace_acquisition_capability(
    acquisition_source: AcquisitionSourceIdentity,
    *,
    adapter_namespace: str,
    coordinate_kind: TraceCoordinateKind,
) -> TraceAcquisitionCapability:
    if not isinstance(acquisition_source, AcquisitionSourceIdentity):
        raise AcquisitionContractError(
            "acquisition_source must be an owner-minted identity"
        )
    if not isinstance(coordinate_kind, TraceCoordinateKind):
        raise AcquisitionContractError(
            "coordinate_kind must be a TraceCoordinateKind"
        )
    namespace = _required_text(adapter_namespace, "adapter_namespace")
    capability_id = _typed_id(
        "xic.trace-acquisition-capability.v1:",
        {
            "schema": "trace_acquisition_capability_v1",
            "acquisition_source_id": acquisition_source.acquisition_source_id,
            "adapter_namespace": namespace,
            "coordinate_kind": coordinate_kind.value,
            "batch_xic": True,
        },
    )
    return TraceAcquisitionCapability(
        capability_id=capability_id,
        acquisition_source=acquisition_source,
        adapter_namespace=namespace,
        coordinate_kind=coordinate_kind,
    )


def acquire_trace_batch(
    capability: TraceAcquisitionCapability,
    requests: tuple[XICRequest, ...],
    adapter: BatchXICAdapter,
) -> TraceAcquisitionBatch:
    if not isinstance(capability, TraceAcquisitionCapability):
        raise AcquisitionContractError(
            "capability must be a TraceAcquisitionCapability"
        )
    if not isinstance(requests, tuple) or any(
        not isinstance(request, XICRequest) for request in requests
    ):
        raise AcquisitionContractError("requests must be an XICRequest tuple")
    if not requests:
        raise AcquisitionContractError("requests must be non-empty")
    call_count_before = _adapter_chromatogram_call_count(adapter)
    traces = tuple(adapter.extract_xic_many(requests))
    call_count_after = _adapter_chromatogram_call_count(adapter)
    if call_count_after < call_count_before:
        raise AcquisitionContractError(
            "adapter chromatogram call counter must be monotonic"
        )
    if len(traces) != len(requests):
        raise AcquisitionContractError(
            "batch adapter must return exactly one trace per request"
        )
    acquired: list[AcquiredTrace] = []
    for request, trace in zip(requests, traces, strict=True):
        if not isinstance(trace, XICTrace):
            raise AcquisitionContractError("batch adapter returned an invalid trace")
        first_scan: int | None = None
        last_scan: int | None = None
        if capability.coordinate_kind is not TraceCoordinateKind.RT_ONLY:
            first_scan, last_scan = adapter.scan_window_for_request(request)
        coordinate = _mint_trace_coordinate(
            capability,
            request=request,
            first_scan=first_scan,
            last_scan=last_scan,
            trace_point_count=len(trace.rt),
        )
        acquired.append(AcquiredTrace(coordinate=coordinate, trace=trace))
    point_count = sum(len(item.trace.rt) for item in acquired)
    return TraceAcquisitionBatch(
        capability=capability,
        traces=tuple(acquired),
        logical_xic_request_count=len(requests),
        xic_batch_count=1,
        adapter_chromatogram_call_count=call_count_after - call_count_before,
        trace_point_count=point_count,
    )


def _mint_trace_coordinate(
    capability: TraceAcquisitionCapability,
    *,
    request: XICRequest,
    first_scan: int | None,
    last_scan: int | None,
    trace_point_count: int,
) -> TraceCoordinate:
    basis = _trace_coordinate_basis(
        capability_id=capability.capability_id,
        acquisition_source_id=capability.acquisition_source.acquisition_source_id,
        request=request,
        coordinate_kind=capability.coordinate_kind,
        first_scan=first_scan,
        last_scan=last_scan,
        trace_point_count=trace_point_count,
    )
    return TraceCoordinate(
        trace_coordinate_id=_typed_id("xic.trace-coordinate.v1:", basis),
        capability_id=capability.capability_id,
        acquisition_source_id=capability.acquisition_source.acquisition_source_id,
        request=request,
        coordinate_kind=capability.coordinate_kind,
        first_scan=first_scan,
        last_scan=last_scan,
        trace_point_count=trace_point_count,
    )


def _trace_coordinate_basis(
    *,
    capability_id: str,
    acquisition_source_id: str,
    request: XICRequest,
    coordinate_kind: TraceCoordinateKind,
    first_scan: int | None,
    last_scan: int | None,
    trace_point_count: int,
) -> dict[str, object]:
    return {
        "schema": "trace_coordinate_v1",
        "capability_id": capability_id,
        "acquisition_source_id": acquisition_source_id,
        "request": {
            "mz": request.mz,
            "rt_min": request.rt_min,
            "rt_max": request.rt_max,
            "ppm_tol": request.ppm_tol,
        },
        "coordinate_kind": coordinate_kind.value,
        "first_scan": first_scan,
        "last_scan": last_scan,
        "trace_point_count": trace_point_count,
    }


def _typed_id(prefix: str, basis: dict[str, object]) -> str:
    payload = json.dumps(
        basis, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return f"{prefix}{hashlib.sha256(payload).hexdigest()}"


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _float_text(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.floating)):
        raise AcquisitionContractError("trace numeric values must be numbers")
    number = float(value)
    if not np.isfinite(number):
        raise AcquisitionContractError("trace numeric values must be finite")
    return (0.0 if number == 0.0 else number).hex()


def _float_from_text(value: object, *, field: str) -> float:
    if not isinstance(value, str):
        raise AcquisitionContractError(f"{field} must be hexadecimal text")
    try:
        number = float.fromhex(value)
    except ValueError as exc:
        raise AcquisitionContractError(f"{field} must be hexadecimal text") from exc
    if not np.isfinite(number) or _float_text(number) != value:
        raise AcquisitionContractError(f"{field} must be canonical finite text")
    return number


def _unique_mapping(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AcquisitionContractError(
                f"acquired trace payload has duplicate field: {key}"
            )
        result[key] = value
    return result


def _required_mapping(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AcquisitionContractError(f"{label} must be one JSON object")
    return value


def _required_list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise AcquisitionContractError(f"{label} must be one JSON array")
    return value


def _require_keys(
    value: Mapping[str, object],
    keys: tuple[str, ...],
    *,
    label: str,
) -> None:
    if tuple(value) != keys:
        raise AcquisitionContractError(
            f"{label} fields must be exactly {keys}; got {tuple(value)}"
        )


def _optional_integer(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_integer(value, field)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AcquisitionContractError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


def _adapter_chromatogram_call_count(adapter: object) -> int:
    value = getattr(adapter, "raw_chromatogram_call_count", None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AcquisitionContractError(
            "adapter must expose a non-negative raw chromatogram call counter"
        )
    return value


def _nonnegative_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AcquisitionContractError(
            f"{field} must be a non-negative integer"
        )
    return value


__all__ = [
    "AcquiredTrace",
    "AcquisitionContractError",
    "BatchXICAdapter",
    "RawAcquisitionSessionCapability",
    "RawAcquisitionSessionReceipt",
    "TraceAcquisitionBatch",
    "TraceAcquisitionCapability",
    "TraceCoordinate",
    "TraceCoordinateKind",
    "acquired_trace_content_id",
    "acquire_trace_batch",
    "deserialize_acquired_trace",
    "mint_raw_acquisition_session_capability",
    "mint_trace_acquisition_capability",
    "serialize_acquired_trace",
]
