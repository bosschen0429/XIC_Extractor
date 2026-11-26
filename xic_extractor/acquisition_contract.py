"""Source-bound native scan acquisition."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.validation_primitives import require_owner_normalized_text
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


@dataclass(frozen=True, slots=True)
class TraceCoordinate:
    capability: TraceAcquisitionCapability
    request: XICRequest
    first_scan: int
    last_scan: int
    trace_point_count: int


@dataclass(frozen=True, slots=True, eq=False)
class AcquiredTrace:
    coordinate: TraceCoordinate
    trace: XICTrace

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
    require_owner_normalized_text(
        adapter_namespace,
        field="adapter_namespace",
        error_factory=AcquisitionContractError,
    )
    return TraceAcquisitionCapability(
        acquisition_source=acquisition_source,
        adapter_namespace=adapter_namespace,
    )


def acquire_trace_batch(
    capability: TraceAcquisitionCapability,
    requests: tuple[XICRequest, ...],
    adapter: BatchXICAdapter,
) -> TraceAcquisitionBatch:
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
        if (
            isinstance(first_scan, bool)
            or isinstance(last_scan, bool)
            or first_scan < 0
            or last_scan < first_scan
        ):
            raise AcquisitionContractError(
                "scan coordinates require an ordered non-negative window"
            )
        rt = np.asarray(trace.rt)
        intensity = np.asarray(trace.intensity)
        if rt.ndim != 1 or intensity.ndim != 1 or rt.shape != intensity.shape:
            raise AcquisitionContractError(
                "trace arrays must be matching one-dimensional arrays"
            )
        if not np.all(np.isfinite(rt)) or not np.all(np.isfinite(intensity)):
            raise AcquisitionContractError("trace values must be finite")
        coordinate = TraceCoordinate(
            capability=capability,
            request=request,
            first_scan=first_scan,
            last_scan=last_scan,
            trace_point_count=len(rt),
        )
        acquired.append(AcquiredTrace(coordinate=coordinate, trace=trace))
    return TraceAcquisitionBatch(
        capability=capability,
        traces=tuple(acquired),
        adapter_chromatogram_call_count=call_count_after - call_count_before,
    )


def _adapter_chromatogram_call_count(adapter: BatchXICAdapter) -> int:
    value = adapter.raw_chromatogram_call_count
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AcquisitionContractError(
            "adapter must expose a non-negative raw chromatogram call counter"
        )
    return value


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
