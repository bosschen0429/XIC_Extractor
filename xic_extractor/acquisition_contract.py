"""Source-bound native scan acquisition."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.validation_primitives import (
    require_integer,
    require_owner_normalized_text,
)
from xic_extractor.xic_models import XICRequest, XICTrace

NATIVE_SCAN_COORDINATE_KIND = "native_scan"
"""Persisted coordinate kind of every acquired trace; native scans are the only one."""


class AcquisitionContractError(ValueError):
    """Raised when an adapter cannot truthfully satisfy its declared capability."""


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

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition_source, AcquisitionSourceIdentity):
            raise AcquisitionContractError(
                "acquisition_source must be an owner-minted identity"
            )
        _required_text(self.adapter_namespace, "adapter_namespace")


@dataclass(frozen=True, slots=True)
class TraceCoordinate:
    capability: TraceAcquisitionCapability
    request: XICRequest
    first_scan: int
    last_scan: int
    trace_point_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.capability, TraceAcquisitionCapability):
            raise AcquisitionContractError(
                "capability must be a TraceAcquisitionCapability"
            )
        if not isinstance(self.request, XICRequest):
            raise AcquisitionContractError("request must be an XICRequest")
        if (
            isinstance(self.trace_point_count, bool)
            or not isinstance(self.trace_point_count, int)
            or self.trace_point_count < 0
        ):
            raise AcquisitionContractError(
                "trace_point_count must be a non-negative integer"
            )
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


@dataclass(frozen=True, slots=True)
class TraceAcquisitionBatch:
    capability: TraceAcquisitionCapability
    traces: tuple[AcquiredTrace, ...]
    adapter_chromatogram_call_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.capability, TraceAcquisitionCapability):
            raise AcquisitionContractError(
                "capability must be a TraceAcquisitionCapability"
            )
        if not isinstance(self.traces, tuple) or any(
            not isinstance(trace, AcquiredTrace) for trace in self.traces
        ):
            raise AcquisitionContractError("traces must contain AcquiredTrace values")
        _nonnegative_integer(
            self.adapter_chromatogram_call_count,
            "adapter_chromatogram_call_count",
        )
        if any(trace.coordinate.capability != self.capability for trace in self.traces):
            raise AcquisitionContractError(
                "all trace coordinates must bind the batch capability"
            )

    @property
    def logical_xic_request_count(self) -> int:
        return len(self.traces)

    @property
    def trace_point_count(self) -> int:
        return sum(len(item.trace.rt) for item in self.traces)


def mint_trace_acquisition_capability(
    acquisition_source: AcquisitionSourceIdentity,
    *,
    adapter_namespace: str,
) -> TraceAcquisitionCapability:
    if not isinstance(acquisition_source, AcquisitionSourceIdentity):
        raise AcquisitionContractError(
            "acquisition_source must be an owner-minted identity"
        )
    namespace = _required_text(adapter_namespace, "adapter_namespace")
    return TraceAcquisitionCapability(
        acquisition_source=acquisition_source,
        adapter_namespace=namespace,
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
        first_scan, last_scan = adapter.scan_window_for_request(request)
        coordinate = TraceCoordinate(
            capability=capability,
            request=request,
            first_scan=first_scan,
            last_scan=last_scan,
            trace_point_count=len(trace.rt),
        )
        acquired.append(AcquiredTrace(coordinate=coordinate, trace=trace))
    return TraceAcquisitionBatch(
        capability=capability,
        traces=tuple(acquired),
        adapter_chromatogram_call_count=call_count_after - call_count_before,
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
    "NATIVE_SCAN_COORDINATE_KIND",
    "AcquiredTrace",
    "AcquisitionContractError",
    "BatchXICAdapter",
    "TraceAcquisitionBatch",
    "TraceAcquisitionCapability",
    "TraceCoordinate",
    "acquire_trace_batch",
    "mint_trace_acquisition_capability",
]
