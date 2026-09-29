"""Selected-integration authority projection for Backfill peak facts.

It deliberately contains no TSV, file I/O, or persistent row codecs.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.peak_detection.integration_result import IntegrationResult


SELECTED_INTEGRATION_AUTHORITY_SCHEMA_VERSION = (
    "selected_integration_authority_projection"
)
SELECTED_INTEGRATION_AUTHORITY_ID_PREFIX = "selected-integration-authority:"

SELECTED_INTEGRATION_AUTHORITY_FIELDS = (
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "raw_apex_rt_min",
    "rt_width_min",
    "height_raw",
    "height_smoothed",
    "area_raw_counts_seconds",
    "integration_method",
    "boundary_sources",
    "area_baseline_corrected",
    "baseline_type",
    "raw_scan_indices",
    "area_ms1_morphology",
    "ms1_morphology_area_source",
    "ms1_morphology_trace_method",
    "ms1_morphology_trace_window_points",
    "ms1_morphology_trace_effective_points",
)


def _safe_text(value: str, *, field: str) -> None:
    if any(character in value for character in ("\t", "\r", "\n", "\x00")):
        raise ValueError(f"{field} contains a forbidden control character")


def _float_text(value: float) -> str:
    return (0.0).hex() if value == 0.0 else value.hex()


def _compact_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _authority_id(
    prefix: str,
    schema_version: str,
    field_values: tuple[tuple[str, object], ...],
) -> str:
    payload: dict[str, object] = {"schema_version": schema_version}
    for name, value in field_values:
        payload[name] = _authority_value(value, field=name)
    return f"{prefix}{hashlib.sha256(_compact_json_bytes(payload)).hexdigest()}"


def _authority_value(value: object, *, field: str) -> object:
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str):
            _safe_text(value, field=field)
        return value
    if type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} must be finite")
        return _float_text(value)
    if isinstance(value, tuple):
        if any(type(item) not in {str, int} for item in value):
            raise TypeError(f"{field} tuple has no canonical authority codec")
        return list(value)
    if isinstance(value, Mapping):
        raise TypeError(f"{field} mappings require a frozen ordered schema")
    raise TypeError(f"{field} has no canonical authority codec")


@dataclass(frozen=True, slots=True)
class SelectedIntegrationAuthorityProjection:
    rt_left_min: float
    rt_apex_min: float
    rt_right_min: float
    raw_apex_rt_min: float
    rt_width_min: float
    height_raw: float
    height_smoothed: float
    area_raw_counts_seconds: float
    integration_method: str
    boundary_sources: tuple[str, ...]
    area_baseline_corrected: float | None
    baseline_type: str
    raw_scan_indices: tuple[int, ...]
    area_ms1_morphology: float | None
    ms1_morphology_area_source: str
    ms1_morphology_trace_method: str
    ms1_morphology_trace_window_points: int | None
    ms1_morphology_trace_effective_points: int | None

    @property
    def selected_integration_authority_content_id(self) -> str:
        return _authority_id(
            SELECTED_INTEGRATION_AUTHORITY_ID_PREFIX,
            SELECTED_INTEGRATION_AUTHORITY_SCHEMA_VERSION,
            tuple(
                (name, getattr(self, name))
                for name in SELECTED_INTEGRATION_AUTHORITY_FIELDS
            ),
        )


def selected_integration_authority_projection(
    integration: IntegrationResult | None,
) -> SelectedIntegrationAuthorityProjection:
    if integration is None:
        raise ValueError("selected integration is required")
    raw_scan_indices = tuple(getattr(integration, "raw_scan_indices", ()))
    if raw_scan_indices:
        raise ValueError("bounded selected integration must omit raw scan indices")
    return SelectedIntegrationAuthorityProjection(
        rt_left_min=integration.rt_left_min,
        rt_apex_min=integration.rt_apex_min,
        rt_right_min=integration.rt_right_min,
        raw_apex_rt_min=integration.raw_apex_rt_min,
        rt_width_min=integration.rt_width_min,
        height_raw=integration.height_raw,
        height_smoothed=integration.height_smoothed,
        area_raw_counts_seconds=integration.area_raw_counts_seconds,
        integration_method=integration.integration_method,
        boundary_sources=tuple(integration.boundary_sources),
        area_baseline_corrected=integration.area_baseline_corrected,
        baseline_type=integration.baseline_type,
        raw_scan_indices=raw_scan_indices,
        area_ms1_morphology=integration.area_ms1_morphology,
        ms1_morphology_area_source=integration.ms1_morphology_area_source,
        ms1_morphology_trace_method=integration.ms1_morphology_trace_method,
        ms1_morphology_trace_window_points=(
            integration.ms1_morphology_trace_window_points
        ),
        ms1_morphology_trace_effective_points=(
            integration.ms1_morphology_trace_effective_points
        ),
    )
