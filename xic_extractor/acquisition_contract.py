"""Capability-aware no-I/O contract over the existing XIC request/trace seam."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Protocol

import numpy as np

from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.validation_primitives import (
    canonical_float_hex,
    canonical_json_bytes,
    make_unique_mapping_hook,
    parse_canonical_float_hex,
    require_integer,
    require_keys,
    require_list,
    require_mapping,
    require_optional_integer,
    require_owner_normalized_text,
)
from xic_extractor.xic_models import XICRequest, XICTrace


class AcquisitionContractError(ValueError):
    """Raised when an adapter cannot truthfully satisfy its declared capability."""


@dataclass(frozen=True, slots=True)
class RawAcquisitionSessionReceipt:
    """A successful vendor open, carried by its existing job/sample result."""

    acquisition_source: AcquisitionSourceIdentity


def _mint_raw_acquisition_session_receipt(
    acquisition_source: AcquisitionSourceIdentity,
) -> RawAcquisitionSessionReceipt:
    """RAW-reader seam, called only after the vendor handle has opened."""
    return RawAcquisitionSessionReceipt(acquisition_source=acquisition_source)


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
    acquisition_source: AcquisitionSourceIdentity
    adapter_namespace: str
    coordinate_kind: TraceCoordinateKind

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition_source, AcquisitionSourceIdentity):
            raise AcquisitionContractError(
                "acquisition_source must be an owner-minted identity"
            )
        _required_text(self.adapter_namespace, "adapter_namespace")
        if not isinstance(self.coordinate_kind, TraceCoordinateKind):
            raise AcquisitionContractError(
                "coordinate_kind must be a TraceCoordinateKind"
            )


@dataclass(frozen=True, slots=True)
class TraceCoordinate:
    capability: TraceAcquisitionCapability
    request: XICRequest
    first_scan: int | None
    last_scan: int | None
    trace_point_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.capability, TraceAcquisitionCapability):
            raise AcquisitionContractError(
                "capability must be a TraceAcquisitionCapability"
            )
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

    @property
    def coordinate_kind(self) -> TraceCoordinateKind:
        return self.capability.coordinate_kind


@dataclass(frozen=True, slots=True, eq=False)
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

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AcquiredTrace):
            return NotImplemented
        return (
            self.coordinate == other.coordinate
            and np.array_equal(self.trace.rt, other.trace.rt)
            and np.array_equal(self.trace.intensity, other.trace.intensity)
        )


_ACQUIRED_TRACE_SCHEMA_VERSION = "acquired_trace"
_ACQUIRED_TRACE_KEYS = (
    "schema_version",
    "coordinate",
    "trace",
)
_TRACE_COORDINATE_KEYS = (
    "capability",
    "request",
    "first_scan",
    "last_scan",
    "trace_point_count",
)
_XIC_REQUEST_KEYS = ("mz", "rt_min", "rt_max", "ppm_tol")
_TRACE_ARRAY_KEYS = ("rt", "intensity")


def serialize_acquired_trace(
    acquired_trace: AcquiredTrace, *, with_capability: bool = True
) -> bytes:
    """Canonically encode a complete acquired trace without owner recomputation.

    A caller that stores the acquisition capability once for many traces
    omits it here and supplies it again to ``deserialize_acquired_trace``.
    """

    content = _acquired_trace_content(acquired_trace, with_capability=with_capability)
    return _canonical_json_bytes(
        {
            "schema_version": _ACQUIRED_TRACE_SCHEMA_VERSION,
            **content,
        }
    )


def deserialize_acquired_trace(
    payload: bytes | str,
    *,
    expected_acquisition_source: AcquisitionSourceIdentity | None = None,
    capability: TraceAcquisitionCapability | None = None,
) -> AcquiredTrace:
    """Strictly reconstitute an acquired trace through acquisition-owner types.

    ``capability`` is the stored-once capability of a payload encoded without it.
    """

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
    _require_keys(
        coordinate_value,
        _TRACE_COORDINATE_KEYS if capability is None else _TRACE_COORDINATE_KEYS[1:],
        label="coordinate",
    )
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
    if capability is None:
        capability = _capability_from_payload(coordinate_value["capability"])
    elif not isinstance(capability, TraceAcquisitionCapability):
        raise AcquisitionContractError(
            "capability must be a TraceAcquisitionCapability"
        )
    if (
        expected_acquisition_source is not None
        and capability.acquisition_source != expected_acquisition_source
    ):
        raise AcquisitionContractError(
            "acquired trace crosses expected acquisition source"
        )
    coordinate = TraceCoordinate(
        capability=capability,
        request=request,
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
    content = _acquired_trace_content(
        acquired, with_capability="capability" in coordinate_value
    )
    canonical = _canonical_json_bytes(
        {"schema_version": _ACQUIRED_TRACE_SCHEMA_VERSION, **content}
    )
    if canonical != raw:
        raise AcquisitionContractError("acquired trace payload is not canonical")
    return acquired


def _acquired_trace_content(
    acquired_trace: AcquiredTrace, *, with_capability: bool = True
) -> dict[str, object]:
    if not isinstance(acquired_trace, AcquiredTrace):
        raise TypeError("acquired_trace must be an AcquiredTrace")
    coordinate = acquired_trace.coordinate
    request = coordinate.request
    capability = (
        {"capability": _capability_content(coordinate.capability)}
        if with_capability
        else {}
    )
    return {
        "coordinate": {
            **capability,
            "request": {
                "mz": _float_text(request.mz),
                "rt_min": _float_text(request.rt_min),
                "rt_max": _float_text(request.rt_max),
                "ppm_tol": _float_text(request.ppm_tol),
            },
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
        if any(trace.coordinate.capability != self.capability for trace in self.traces):
            raise AcquisitionContractError(
                "all trace coordinates must bind the batch capability"
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
        raise AcquisitionContractError("coordinate_kind must be a TraceCoordinateKind")
    namespace = _required_text(adapter_namespace, "adapter_namespace")
    return TraceAcquisitionCapability(
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
    return TraceCoordinate(
        capability=capability,
        request=request,
        first_scan=first_scan,
        last_scan=last_scan,
        trace_point_count=trace_point_count,
    )


def _capability_content(capability: TraceAcquisitionCapability) -> dict[str, object]:
    return {
        "acquisition_source": asdict(capability.acquisition_source),
        "adapter_namespace": capability.adapter_namespace,
        "coordinate_kind": capability.coordinate_kind.value,
    }


def _capability_from_payload(value: object) -> TraceAcquisitionCapability:
    capability = _required_mapping(value, label="capability")
    _require_keys(
        capability,
        ("acquisition_source", "adapter_namespace", "coordinate_kind"),
        label="capability",
    )
    source = _required_mapping(
        capability["acquisition_source"], label="acquisition source"
    )
    _require_keys(
        source,
        (
            "content_sha256",
            "byte_size",
            "acquisition_format",
            "observation_namespace",
            "local_file_revision",
        ),
        label="acquisition source",
    )
    revision_value = source["local_file_revision"]
    revision = None
    if revision_value is not None:
        revision_values = _required_list(revision_value, label="local_file_revision")
        if len(revision_values) != 5:
            raise AcquisitionContractError(
                "local_file_revision must contain five integers"
            )
        revision = tuple(
            require_integer(
                item,
                field="local_file_revision",
                error_factory=AcquisitionContractError,
                type_error_factory=AcquisitionContractError,
            )
            for item in revision_values
        )
    content_sha256 = source["content_sha256"]
    acquisition_source = AcquisitionSourceIdentity(
        content_sha256=None
        if content_sha256 is None
        else _required_text(content_sha256, "content_sha256"),
        byte_size=_nonnegative_integer(source["byte_size"], "byte_size"),
        acquisition_format=_required_text(
            source["acquisition_format"], "acquisition_format"
        ),
        observation_namespace=_required_text(
            source["observation_namespace"], "observation_namespace"
        ),
        local_file_revision=None
        if revision is None
        else (revision[0], revision[1], revision[2], revision[3], revision[4]),
    )
    try:
        kind = TraceCoordinateKind(
            _required_text(capability["coordinate_kind"], "coordinate_kind")
        )
    except ValueError as exc:
        raise AcquisitionContractError("unknown trace coordinate kind") from exc
    return TraceAcquisitionCapability(
        acquisition_source=acquisition_source,
        adapter_namespace=_required_text(
            capability["adapter_namespace"], "adapter_namespace"
        ),
        coordinate_kind=kind,
    )


_canonical_json_bytes = canonical_json_bytes


def _float_text(value: object) -> str:
    return canonical_float_hex(
        value,
        label="trace numeric values",
        error_factory=AcquisitionContractError,
        type_error_factory=AcquisitionContractError,
    )


def _float_from_text(value: object, *, field: str) -> float:
    return parse_canonical_float_hex(
        value,
        field=field,
        error_factory=AcquisitionContractError,
        type_error_factory=AcquisitionContractError,
    )


_unique_mapping = make_unique_mapping_hook(
    label="acquired trace payload",
    error_factory=AcquisitionContractError,
)


def _required_mapping(value: object, *, label: str) -> dict[str, object]:
    return require_mapping(value, label=label, error_factory=AcquisitionContractError)


def _required_list(value: object, *, label: str) -> list[object]:
    return require_list(value, label=label, error_factory=AcquisitionContractError)


def _require_keys(
    value: Mapping[str, object],
    keys: tuple[str, ...],
    *,
    label: str,
) -> None:
    require_keys(value, keys, label=label, error_factory=AcquisitionContractError)


def _optional_integer(value: object, field: str) -> int | None:
    return require_optional_integer(
        value,
        field=field,
        non_negative=True,
        error_factory=AcquisitionContractError,
        type_error_factory=AcquisitionContractError,
    )


def _required_text(value: object, field: str) -> str:
    return require_owner_normalized_text(
        value, field=field, error_factory=AcquisitionContractError
    )


def _adapter_chromatogram_call_count(adapter: BatchXICAdapter) -> int:
    value = adapter.raw_chromatogram_call_count
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AcquisitionContractError(
            "adapter must expose a non-negative raw chromatogram call counter"
        )
    return value


def _nonnegative_integer(value: object, field: str) -> int:
    return require_integer(
        value,
        field=field,
        non_negative=True,
        error_factory=AcquisitionContractError,
        type_error_factory=AcquisitionContractError,
    )


__all__ = [
    "AcquiredTrace",
    "AcquisitionContractError",
    "BatchXICAdapter",
    "RawAcquisitionSessionReceipt",
    "TraceAcquisitionBatch",
    "TraceAcquisitionCapability",
    "TraceCoordinate",
    "TraceCoordinateKind",
    "acquire_trace_batch",
    "deserialize_acquired_trace",
    "mint_trace_acquisition_capability",
    "serialize_acquired_trace",
]
