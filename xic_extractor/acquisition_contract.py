"""Capability-aware no-I/O contract over the existing XIC request/trace seam."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

import numpy as np

from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.xic_models import XICRequest, XICTrace


class AcquisitionContractError(ValueError):
    """Raised when an adapter cannot truthfully satisfy its declared capability."""


class TraceCoordinateKind(str, Enum):
    NATIVE_SCAN = "native_scan"
    CONVERSION_SCAN = "conversion_scan"
    RT_ONLY = "rt_only"


class BatchXICAdapter(Protocol):
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
        if len(self.trace.rt) != self.coordinate.trace_point_count:
            raise AcquisitionContractError("trace length mismatches trace coordinates")
        if not np.all(np.isfinite(self.trace.rt)) or not np.all(
            np.isfinite(self.trace.intensity)
        ):
            raise AcquisitionContractError("trace values must be finite")


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
            "adapter_chromatogram_call_count": 1,
            "trace_point_count": sum(len(item.trace.rt) for item in self.traces),
        }
        for field, expected in expected_counts.items():
            if getattr(self, field) != expected:
                raise AcquisitionContractError(
                    f"{field} mismatches the acquired batch content"
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
    traces = tuple(adapter.extract_xic_many(requests))
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
        adapter_chromatogram_call_count=1,
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


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AcquisitionContractError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


__all__ = [
    "AcquiredTrace",
    "AcquisitionContractError",
    "BatchXICAdapter",
    "TraceAcquisitionBatch",
    "TraceAcquisitionCapability",
    "TraceCoordinate",
    "TraceCoordinateKind",
    "acquire_trace_batch",
    "mint_trace_acquisition_capability",
]
