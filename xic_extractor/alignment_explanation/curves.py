"""Validate selected persisted curve envelopes without acquiring or reprocessing."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import fields
from typing import Any

from xic_extractor.alignment_explanation.errors import ExplanationError
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.signal_quality import SignalQuality

VALID_CURVE_KINDS = frozenset(
    {
        "initial_capture",
        "backfill_observation",
    }
)
ALLOWED_CURVE_PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "sample_id",
        "candidate_id",
        "physical_peak_key",
        "trace_coordinate",
        "curves",
        "integration",
        "provenance",
    }
)


_CURVE_KEYS = frozenset({"rt", "raw", "baseline", "smoothed", "positive"})
_COORDINATE_KEYS = frozenset({"mz", "rt_min", "rt_max", "ppm_tol"})
_PROFILE_KEYS = frozenset(field.name for field in fields(SignalProcessingProfile))
_INTEGRATION_KEYS = frozenset(field.name for field in fields(IntegrationResult))
_SIGNAL_QUALITY_KEYS = frozenset(field.name for field in fields(SignalQuality))
_PROVENANCE_KEYS = frozenset(
    {
        "profile_version",
        "acquisition_source_id",
        "processing_attempt_id",
        "selected_trace_indices",
        "resolver_mode",
        "proposal_sources",
        "method_subject_id",
        "profile",
        "physical_state",
        "physical_reason",
    }
)


def validate_curve_payload(
    payload: Mapping[str, Any],
    *,
    sample_id: str,
    kind: str,
    record_id: str,
) -> None:
    """Validate the selected persisted envelope once at its storage boundary."""
    if kind not in VALID_CURVE_KINDS:
        raise ExplanationError(f"undeclared curve kind: {kind}; rerun Alignment")
    if not isinstance(payload, Mapping):
        raise ExplanationError("curve payload must be an object")
    extra = set(payload) - ALLOWED_CURVE_PAYLOAD_KEYS
    if extra:
        raise ExplanationError(
            f"undeclared curve payload keys: {sorted(extra)}; rerun Alignment"
        )
    if set(payload) != ALLOWED_CURVE_PAYLOAD_KEYS:
        raise ExplanationError("curve payload missing required fields; rerun Alignment")
    if payload["schema_version"] != "curve_payload":
        raise ExplanationError(
            "不支援此曲線資料格式；請重新執行 Alignment 產生新輸出。"
        )
    if payload["kind"] != kind or payload["sample_id"] != sample_id:
        raise ExplanationError("curve payload crosses sample or kind binding")
    if not isinstance(sample_id, str) or not sample_id or not record_id:
        raise ExplanationError("curve payload missing sample or record identity")
    provenance = payload["provenance"]
    if not isinstance(provenance, Mapping) or set(provenance) != _PROVENANCE_KEYS:
        raise ExplanationError(
            "undeclared or missing provenance fields; rerun Alignment"
        )
    profile = provenance.get("profile")
    if profile is not None and (
        not isinstance(profile, Mapping) or set(profile) != _PROFILE_KEYS
    ):
        raise ExplanationError("undeclared or missing profile fields; rerun Alignment")
    if record_id not in (
        payload["physical_peak_key"],
        payload["candidate_id"],
        provenance.get("method_subject_id"),
    ):
        raise ExplanationError("curve payload crosses record binding")

    curves = payload["curves"]
    if not isinstance(curves, Mapping) or set(curves) != _CURVE_KEYS:
        raise ExplanationError(
            "undeclared or missing curve array fields; rerun Alignment"
        )
    rt = curves["rt"]
    if not isinstance(rt, (list, tuple)):
        raise ExplanationError("curve RT must be an array")
    if not rt and (
        provenance.get("physical_state") != "unavailable"
        or payload["physical_peak_key"] is not None
        or payload["integration"] is not None
        or any(
            curves[name] is not None for name in ("baseline", "smoothed", "positive")
        )
    ):
        raise ExplanationError(
            "empty acquisition cannot claim observed or processed evidence"
        )
    for name, values in curves.items():
        if values is None and name not in {"rt", "raw"}:
            continue
        if not isinstance(values, (list, tuple)) or len(values) != len(rt):
            raise ExplanationError(f"curve array length mismatch: {name}")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in values
        ):
            raise ExplanationError(f"curve values must be finite numbers: {name}")
    if any(right <= left for left, right in zip(rt, rt[1:], strict=False)):
        raise ExplanationError("curve RT must be strictly increasing")

    coordinate = payload["trace_coordinate"]
    if not isinstance(coordinate, Mapping) or set(coordinate) != _COORDINATE_KEYS:
        raise ExplanationError(
            "undeclared or missing trace coordinate fields; rerun Alignment"
        )
    for name, value in coordinate.items():
        if value is None and name == "ppm_tol":
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ExplanationError(f"invalid trace coordinate: {name}")
    if coordinate["mz"] <= 0 or coordinate["rt_min"] > coordinate["rt_max"]:
        raise ExplanationError("invalid trace coordinate bounds")
    if coordinate["ppm_tol"] is not None and coordinate["ppm_tol"] <= 0:
        raise ExplanationError("invalid trace coordinate tolerance")

    integration = payload["integration"]
    if integration is not None:
        if (
            not isinstance(integration, Mapping)
            or set(integration) != _INTEGRATION_KEYS
        ):
            raise ExplanationError(
                "undeclared or missing integration fields; rerun Alignment"
            )
        quality = integration["signal_quality"]
        if quality is not None and (
            not isinstance(quality, Mapping) or set(quality) != _SIGNAL_QUALITY_KEYS
        ):
            raise ExplanationError(
                "undeclared or missing signal quality fields; rerun Alignment"
            )
        for name, value in integration.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ExplanationError(f"nonfinite integration value: {name}")
