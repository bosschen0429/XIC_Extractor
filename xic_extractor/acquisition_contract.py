from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from xic_extractor.processing_lineage import AcquisitionSourceRef


class AcquisitionContractError(ValueError):
    """Raised when source capability or observation provenance is insufficient."""


class TraceCoordinateMode(str, Enum):
    NATIVE_SCAN = "native_scan"
    CONVERSION_SCAN = "conversion_scan"
    RT_ONLY = "rt_only"


@dataclass(frozen=True)
class AcquisitionCapability:
    source_id: str
    supported_coordinate_modes: tuple[TraceCoordinateMode, ...]
    source_provenance_verified: bool

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise AcquisitionContractError("source_id is required")
        if not self.source_provenance_verified:
            raise AcquisitionContractError(
                "acquisition capability requires verified source provenance"
            )
        if not self.supported_coordinate_modes:
            raise AcquisitionContractError(
                "supported_coordinate_modes must not be empty"
            )
        if len(self.supported_coordinate_modes) != len(
            set(self.supported_coordinate_modes)
        ):
            raise AcquisitionContractError(
                "supported_coordinate_modes must be unique"
            )


@dataclass(frozen=True)
class TraceQuery:
    query_id: str
    source: AcquisitionSourceRef
    mz: float
    ppm_tol: float
    rt_min: float
    rt_max: float
    coordinate_mode: TraceCoordinateMode
    scan_predicate: str

    def __post_init__(self) -> None:
        if not self.query_id.strip():
            raise AcquisitionContractError("query_id is required")
        if not math.isfinite(self.mz) or self.mz <= 0:
            raise AcquisitionContractError("mz must be finite and > 0")
        if not math.isfinite(self.ppm_tol) or self.ppm_tol <= 0:
            raise AcquisitionContractError("ppm_tol must be finite and > 0")
        if (
            not math.isfinite(self.rt_min)
            or not math.isfinite(self.rt_max)
            or self.rt_min > self.rt_max
        ):
            raise AcquisitionContractError(
                "RT bounds must be finite and rt_min <= rt_max"
            )
        if not self.scan_predicate.strip():
            raise AcquisitionContractError("scan_predicate is required")


@dataclass(frozen=True)
class TraceObservation:
    query_id: str
    source_id: str
    coordinate_mode: TraceCoordinateMode
    rt: tuple[float, ...]
    intensity: tuple[float, ...]
    observation_ids: tuple[str, ...] = ()
    conversion_provenance: str = ""

    def __post_init__(self) -> None:
        if not self.query_id.strip() or not self.source_id.strip():
            raise AcquisitionContractError("query_id and source_id are required")
        if len(self.rt) != len(self.intensity):
            raise AcquisitionContractError(
                "trace RT and intensity arrays must have matching lengths"
            )
        if any(not math.isfinite(value) for value in self.rt + self.intensity):
            raise AcquisitionContractError("trace arrays must contain finite values")
        if any(left > right for left, right in zip(self.rt, self.rt[1:])):
            raise AcquisitionContractError("trace RT values must be non-decreasing")
        if self.coordinate_mode is TraceCoordinateMode.RT_ONLY:
            if self.observation_ids or self.conversion_provenance:
                raise AcquisitionContractError(
                    "rt_only observations cannot claim scan identity or conversion"
                )
        elif len(self.observation_ids) != len(self.rt):
            raise AcquisitionContractError(
                "scan-qualified observations require one observation ID per point"
            )
        if (
            self.coordinate_mode is TraceCoordinateMode.CONVERSION_SCAN
            and not self.conversion_provenance.strip()
        ):
            raise AcquisitionContractError(
                "conversion_scan observations require conversion provenance"
            )
        if (
            self.coordinate_mode is TraceCoordinateMode.NATIVE_SCAN
            and self.conversion_provenance
        ):
            raise AcquisitionContractError(
                "native_scan observations cannot claim conversion provenance"
            )


def validate_trace_capability(
    capability: AcquisitionCapability,
    query: TraceQuery,
) -> None:
    if capability.source_id != query.source.source_id:
        raise AcquisitionContractError("capability source does not match trace query")
    if query.coordinate_mode not in capability.supported_coordinate_modes:
        raise AcquisitionContractError(
            f"source does not support {query.coordinate_mode.value} coordinates"
        )


def validate_trace_observation(
    query: TraceQuery,
    observation: TraceObservation,
) -> None:
    if observation.query_id != query.query_id:
        raise AcquisitionContractError("observation query_id mismatch")
    if observation.source_id != query.source.source_id:
        raise AcquisitionContractError("observation source_id mismatch")
    if observation.coordinate_mode is not query.coordinate_mode:
        raise AcquisitionContractError("observation coordinate mode mismatch")
    if observation.rt and (
        observation.rt[0] < query.rt_min or observation.rt[-1] > query.rt_max
    ):
        raise AcquisitionContractError("observation lies outside requested RT bounds")


def require_scan_identity(observation: TraceObservation) -> tuple[str, ...]:
    if observation.coordinate_mode is TraceCoordinateMode.RT_ONLY:
        raise AcquisitionContractError(
            "rt_only observation cannot satisfy scan-identity requirement"
        )
    return observation.observation_ids
