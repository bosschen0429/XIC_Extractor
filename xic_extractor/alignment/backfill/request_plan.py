"""Backfill request planning and identity contracts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NamedTuple

from xic_extractor.alignment.backfill.runtime_projection import (
    BackfillRuntimeFeature,
)
from xic_extractor.alignment.config import BackfillXicBackend
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.xic_models import XICRequest

BackfillWindowStrategy = Literal["exact", "super-window"]
BACKFILL_WINDOW_STRATEGIES: tuple[BackfillWindowStrategy, ...] = (
    "exact",
    "super-window",
)
BackfillValidationMode = Literal["primary", "prefilter", "validation"]
BACKFILL_VALIDATION_MODES: tuple[BackfillValidationMode, ...] = (
    "primary",
    "prefilter",
    "validation",
)
BACKFILL_XIC_BACKENDS: tuple[BackfillXicBackend, ...] = (
    "raw",
    "ms1_index",
    "ms1_index_hybrid",
)
BACKFILL_TRACE_RESULT_SEMANTICS = "owner_backfill_xic_trace"
_REQUEST_IDENTITY_PREFIX = "xic.owner-backfill-request:"
_LOGICAL_REQUEST_PREFIX = "xic.owner-backfill-logical-request:"
_RUNTIME_SAMPLE_PROJECTION_PREFIX = "xic.owner-backfill-runtime-sample:"


class BackfillRequestItem(NamedTuple):
    feature: BackfillRuntimeFeature
    sample_stem: str
    request: XICRequest
    preferred_rt: float


@dataclass(frozen=True, slots=True)
class BackfillRequestIdentity:
    request_identity_id: str
    sample_stem: str
    acquisition_source: AcquisitionSourceIdentity
    request: XICRequest
    backend: BackfillXicBackend
    window_strategy: BackfillWindowStrategy
    superwindow_span_factor: int
    validation_mode: BackfillValidationMode
    result_semantics: str


@dataclass(frozen=True, slots=True)
class BackfillLogicalRequestMember:
    logical_request_id: str
    logical_index: int
    group_hypothesis_id: str
    item: BackfillRequestItem


def _request_basis(request: XICRequest) -> dict[str, float]:
    _validate_request_coordinates(request)
    return {
        "mz": request.mz,
        "rt_min": request.rt_min,
        "rt_max": request.rt_max,
        "ppm_tol": request.ppm_tol,
    }


def _validate_request_coordinates(request: XICRequest) -> None:
    if any(
        not math.isfinite(value)
        for value in (request.mz, request.rt_min, request.rt_max, request.ppm_tol)
    ):
        raise ValueError("exact request semantics require finite coordinates")


def _typed_id(prefix: str, basis: Mapping[str, object]) -> str:
    payload = json.dumps(
        basis,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return f"{prefix}{hashlib.sha256(payload).hexdigest()}"


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be non-empty owner-normalized text")
    return value
