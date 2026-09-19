from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.alignment._backfill_util import (
    allowlist_rows_by_family_sample_key,
    has_semicolon_token,
    rows_by_family_sample_key,
)
from xic_extractor.alignment.backfill_evidence_projection import (
    PRODUCT_AUTHORITY_SCOPE_FIELD,
    PRODUCT_AUTHORITY_SOURCE_FIELD,
    PRODUCT_AUTHORITY_STATUS_FIELD,
    PRODUCT_AUTHORIZED_SCOPE,
    PRODUCT_AUTHORIZED_STATUS,
)
from xic_extractor.alignment.promotion_policy import (
    ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
)
from xic_extractor.alignment.shared_peak_identity_explanation import (
    machine_evidence_support,
)
from xic_extractor.tabular_io import optional_float, text_value

SCHEMA_VERSION = "backfill_ms1_pattern_product_authority"
DEFAULT_MIN_ANCHOR_OWN_MAX_SHAPE_SIMILARITY = 0.5
_OWN_MAX_SHAPE_METRIC_SOURCE = "family_ms1_overlay_anchor_peak_own_max"

ALLOWLIST_COLUMNS = (
    "schema_version",
    "feature_family_id",
    "sample_stem",
    "authority_status",
    "authority_source",
    "authority_reason",
    "expected_overlay_trace_data_json",
    "expected_overlay_trace_data_sha256",
    "min_anchor_own_max_shape_similarity",
)

AUTHORITY_OUTPUT_COLUMNS = (
    PRODUCT_AUTHORITY_STATUS_FIELD,
    PRODUCT_AUTHORITY_SCOPE_FIELD,
    PRODUCT_AUTHORITY_SOURCE_FIELD,
    "product_authority_reason",
    "product_authority_overlay_trace_data_json",
    "product_authority_min_anchor_own_max_shape_similarity",
    "product_authority_observed_anchor_own_max_shape_similarity",
    "product_authority_overlay_trace_data_sha256",
    "product_authority_expected_overlay_trace_data_sha256",
)

AUDIT_COLUMNS = (
    "schema_version",
    "feature_family_id",
    "sample_stem",
    "authority_status",
    "decision",
    "decision_reason",
    "source_ms1_pattern_status",
    "source_ms1_pattern_evidence_level",
    "source_anchor_own_max_shape_similarity",
    "source_overlay_trace_data_status",
    "source_overlay_trace_data_json",
    "source_overlay_trace_data_sha256",
    "expected_overlay_trace_data_json",
    "expected_overlay_trace_data_sha256",
)


@dataclass(frozen=True)
class ProductAuthorityResult:
    authorized_rows: tuple[dict[str, str], ...]
    audit_rows: tuple[dict[str, str], ...]
    summary: dict[str, object]


@dataclass(frozen=True)
class _OverlayTraceDataValidation:
    status: str
    sha256: str = ""


@dataclass(frozen=True)
class _OverlayTraceDataFile:
    status: str
    sha256: str = ""
    data: Mapping[str, object] | None = None


@dataclass(frozen=True)
class _AuthorizationDecision:
    decision: str
    reason: str
    overlay_trace_data: _OverlayTraceDataValidation = _OverlayTraceDataValidation(
        "not_checked"
    )


def authorize_ms1_pattern_rows(
    *,
    ms1_pattern_rows: Sequence[Mapping[str, str]],
    allowlist_rows: Sequence[Mapping[str, str]],
    artifact_base_dir: Path | None = None,
) -> ProductAuthorityResult:
    source_by_key = _rows_by_key(ms1_pattern_rows)
    allowlist_by_key = _allowlist_by_key(allowlist_rows)
    trace_data_cache: dict[str, _OverlayTraceDataFile] = {}
    authorized_rows: list[dict[str, str]] = []
    audit_rows: list[dict[str, str]] = []
    for key in sorted(allowlist_by_key):
        allowlist_row = allowlist_by_key[key]
        source_row = source_by_key.get(key)
        authorization = _authorization_decision(
            source_row,
            allowlist_row,
            artifact_base_dir=artifact_base_dir,
            trace_data_cache=trace_data_cache,
        )
        audit_rows.append(
            _audit_row(
                key,
                allowlist_row=allowlist_row,
                source_row=source_row,
                authorization=authorization,
            )
        )
        if authorization.decision != "authorized" or source_row is None:
            continue
        authorized_rows.append(
            _authorized_row(
                source_row,
                allowlist_row=allowlist_row,
                overlay_trace_data=authorization.overlay_trace_data,
            )
        )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "allowlist_row_count": len(allowlist_rows),
        "source_row_count": len(ms1_pattern_rows),
        "authorized_row_count": len(authorized_rows),
        "rejected_row_count": len(audit_rows) - len(authorized_rows),
        "product_ready": False,
        "readiness_label": "product_authority_sidecar_candidate",
    }
    return ProductAuthorityResult(
        authorized_rows=tuple(authorized_rows),
        audit_rows=tuple(audit_rows),
        summary=summary,
    )


def output_columns(source_columns: Sequence[str]) -> tuple[str, ...]:
    columns = list(source_columns)
    for column in AUTHORITY_OUTPUT_COLUMNS:
        if column not in columns:
            columns.append(column)
    return tuple(columns)


def _rows_by_key(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    return rows_by_family_sample_key(
        rows,
        duplicate_label="backfill MS1 product authority source",
    )


def _allowlist_by_key(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    return allowlist_rows_by_family_sample_key(
        rows,
        expected_schema_version=SCHEMA_VERSION,
        schema_label="backfill MS1 product authority",
        missing_label="backfill MS1 product authority",
        duplicate_label="backfill MS1 product authority allowlist",
    )


def _authorization_decision(
    source_row: Mapping[str, str] | None,
    allowlist_row: Mapping[str, str],
    *,
    artifact_base_dir: Path | None,
    trace_data_cache: dict[str, _OverlayTraceDataFile],
) -> _AuthorizationDecision:
    if text_value(allowlist_row.get("authority_status")) != PRODUCT_AUTHORIZED_STATUS:
        return _reject("allowlist_status_not_product_authorized")
    if not text_value(allowlist_row.get("authority_source")):
        return _reject("allowlist_authority_source_missing")
    if source_row is None:
        return _reject("source_ms1_pattern_row_missing")
    if text_value(source_row.get("ms1_pattern_status")) not in {
        "supportive",
        "partial_support",
    }:
        return _reject("source_ms1_pattern_not_supportive")
    evidence_level = text_value(source_row.get("ms1_pattern_evidence_level"))
    if evidence_level != "trace_constellation":
        return _reject("source_ms1_pattern_not_trace_constellation")
    shape_source = text_value(source_row.get("shape_metric_source"))
    if shape_source != _OWN_MAX_SHAPE_METRIC_SOURCE:
        return _reject("source_shape_metric_not_anchor_own_max")
    if not text_value(source_row.get("family_ms1_overlay_trace_data_json")):
        return _reject("source_overlay_trace_json_missing")
    overlay_trace_data = _validate_overlay_trace_data(
        source_row,
        artifact_base_dir=artifact_base_dir,
        trace_data_cache=trace_data_cache,
    )
    if overlay_trace_data.status != "valid":
        return _reject(
            overlay_trace_data.status,
            overlay_trace_data=overlay_trace_data,
        )
    if not text_value(allowlist_row.get("expected_overlay_trace_data_json")):
        return _reject(
            "allowlist_overlay_trace_json_missing",
            overlay_trace_data=overlay_trace_data,
        )
    if not text_value(allowlist_row.get("expected_overlay_trace_data_sha256")):
        return _reject(
            "allowlist_overlay_trace_sha256_missing",
            overlay_trace_data=overlay_trace_data,
        )
    path_status = _expected_overlay_path_status(
        source_row,
        allowlist_row,
        artifact_base_dir=artifact_base_dir,
    )
    if path_status != "valid":
        return _reject(path_status, overlay_trace_data=overlay_trace_data)
    expected_sha256 = text_value(
        allowlist_row.get("expected_overlay_trace_data_sha256")
    ).upper()
    if overlay_trace_data.sha256 != expected_sha256:
        return _reject(
            "allowlist_overlay_trace_sha256_mismatch",
            overlay_trace_data=overlay_trace_data,
        )
    if (
        text_value(source_row.get("peak_quality_vector_basis"))
        != "family_ms1_overlay_raw_trace_vector"
    ):
        return _reject(
            "source_peak_quality_vector_missing",
            overlay_trace_data=overlay_trace_data,
        )
    if text_value(source_row.get("peak_quality_vector_status")) not in {
        "supportive",
        "partial_support",
    }:
        return _reject(
            "source_peak_quality_not_supportive",
            overlay_trace_data=overlay_trace_data,
        )
    if not has_semicolon_token(
        source_row.get("reason"),
        ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
    ):
        return _reject(
            "source_anchor_own_max_reason_missing",
            overlay_trace_data=overlay_trace_data,
        )
    observed = optional_float(source_row.get("anchor_peak_own_max_shape_similarity"))
    if observed is None:
        return _reject(
            "source_anchor_own_max_similarity_missing",
            overlay_trace_data=overlay_trace_data,
        )
    threshold = _threshold(allowlist_row)
    if threshold < DEFAULT_MIN_ANCHOR_OWN_MAX_SHAPE_SIMILARITY:
        return _reject(
            "allowlist_anchor_own_max_threshold_below_default",
            overlay_trace_data=overlay_trace_data,
        )
    if observed <= threshold:
        return _reject(
            "source_anchor_own_max_similarity_below_threshold",
            overlay_trace_data=overlay_trace_data,
        )
    return _AuthorizationDecision(
        "authorized",
        "product_authority_allowlist_and_anchor_own_max_supported",
        overlay_trace_data,
    )


def _authorized_row(
    source_row: Mapping[str, str],
    *,
    allowlist_row: Mapping[str, str],
    overlay_trace_data: _OverlayTraceDataValidation,
) -> dict[str, str]:
    row = {key: text_value(value) for key, value in source_row.items()}
    row["diagnostic_only"] = "FALSE"
    row[PRODUCT_AUTHORITY_STATUS_FIELD] = PRODUCT_AUTHORIZED_STATUS
    row[PRODUCT_AUTHORITY_SCOPE_FIELD] = PRODUCT_AUTHORIZED_SCOPE
    row[PRODUCT_AUTHORITY_SOURCE_FIELD] = text_value(
        allowlist_row.get("authority_source")
    )
    row["product_authority_reason"] = text_value(
        allowlist_row.get("authority_reason")
    ) or "reviewed_backfill_ms1_overlay_allowlist"
    row["product_authority_overlay_trace_data_json"] = text_value(
        source_row.get("family_ms1_overlay_trace_data_json")
    )
    row["product_authority_min_anchor_own_max_shape_similarity"] = _format_float(
        _threshold(allowlist_row)
    )
    row["product_authority_observed_anchor_own_max_shape_similarity"] = text_value(
        source_row.get("anchor_peak_own_max_shape_similarity")
    )
    row["product_authority_overlay_trace_data_sha256"] = overlay_trace_data.sha256
    row["product_authority_expected_overlay_trace_data_sha256"] = text_value(
        allowlist_row.get("expected_overlay_trace_data_sha256")
    ).upper()
    return row


def _audit_row(
    key: tuple[str, str],
    *,
    allowlist_row: Mapping[str, str],
    source_row: Mapping[str, str] | None,
    authorization: _AuthorizationDecision,
) -> dict[str, str]:
    source = source_row or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "feature_family_id": key[0],
        "sample_stem": key[1],
        "authority_status": text_value(allowlist_row.get("authority_status")),
        "decision": authorization.decision,
        "decision_reason": authorization.reason,
        "source_ms1_pattern_status": text_value(source.get("ms1_pattern_status")),
        "source_ms1_pattern_evidence_level": text_value(
            source.get("ms1_pattern_evidence_level")
        ),
        "source_anchor_own_max_shape_similarity": text_value(
            source.get("anchor_peak_own_max_shape_similarity")
        ),
        "source_overlay_trace_data_status": authorization.overlay_trace_data.status,
        "source_overlay_trace_data_json": text_value(
            source.get("family_ms1_overlay_trace_data_json")
        ),
        "source_overlay_trace_data_sha256": authorization.overlay_trace_data.sha256,
        "expected_overlay_trace_data_json": text_value(
            allowlist_row.get("expected_overlay_trace_data_json")
        ),
        "expected_overlay_trace_data_sha256": text_value(
            allowlist_row.get("expected_overlay_trace_data_sha256")
        ).upper(),
    }


def _threshold(row: Mapping[str, str]) -> float:
    value = optional_float(row.get("min_anchor_own_max_shape_similarity"))
    return (
        DEFAULT_MIN_ANCHOR_OWN_MAX_SHAPE_SIMILARITY
        if value is None
        else value
    )


def _format_float(value: float) -> str:
    return f"{value:.6g}"


def _reject(
    reason: str,
    *,
    overlay_trace_data: _OverlayTraceDataValidation = _OverlayTraceDataValidation(
        "not_checked"
    ),
) -> _AuthorizationDecision:
    return _AuthorizationDecision("rejected", reason, overlay_trace_data)


def _validate_overlay_trace_data(
    source_row: Mapping[str, str],
    *,
    artifact_base_dir: Path | None,
    trace_data_cache: dict[str, _OverlayTraceDataFile],
) -> _OverlayTraceDataValidation:
    path_text = text_value(source_row.get("family_ms1_overlay_trace_data_json"))
    relative_path = _bundle_relative_path(path_text, artifact_base_dir)
    if relative_path is None:
        return _OverlayTraceDataValidation(
            "source_overlay_trace_json_path_outside_bundle"
        )
    path = (
        Path(relative_path)
        if artifact_base_dir is None
        else artifact_base_dir.resolve() / Path(relative_path)
    )
    trace_file = trace_data_cache.get(relative_path)
    if trace_file is None:
        trace_file = _read_overlay_trace_data_file(path)
        trace_data_cache[relative_path] = trace_file
    if trace_file.status != "valid" or trace_file.data is None:
        return _OverlayTraceDataValidation(trace_file.status, trace_file.sha256)
    data = trace_file.data

    family_id = text_value(source_row.get("feature_family_id"))
    if text_value(data.get("family_id")) != family_id:
        return _OverlayTraceDataValidation("source_overlay_trace_family_mismatch")
    traces = data.get("traces")
    if not isinstance(traces, list):
        return _OverlayTraceDataValidation("source_overlay_trace_json_missing_traces")
    sample_stem = text_value(
        source_row.get("sample_stem") or source_row.get("sample_id")
    )
    matching_traces = [
        trace
        for trace in traces
        if isinstance(trace, dict)
        and text_value(trace.get("sample_stem")) == sample_stem
    ]
    if not matching_traces:
        return _OverlayTraceDataValidation("source_overlay_trace_sample_missing")
    if len(matching_traces) > 1:
        return _OverlayTraceDataValidation("source_overlay_trace_sample_duplicate")
    trace = matching_traces[0]
    rt = _numeric_sequence(trace.get("rt"))
    intensity = _numeric_sequence(trace.get("intensity"))
    if len(rt) < 3 or len(intensity) < 3 or len(rt) != len(intensity):
        return _OverlayTraceDataValidation("source_overlay_trace_vector_invalid")
    if optional_float(trace.get("absolute_own_max_shape_similarity")) is None:
        return _OverlayTraceDataValidation(
            "source_overlay_trace_own_max_similarity_missing"
        )
    return _OverlayTraceDataValidation(
        "valid",
        trace_file.sha256,
    )


def _read_overlay_trace_data_file(path: Path) -> _OverlayTraceDataFile:
    try:
        payload = path.read_bytes()
    except OSError:
        return _OverlayTraceDataFile("source_overlay_trace_json_unreadable")

    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _OverlayTraceDataFile("source_overlay_trace_json_invalid")
    if not isinstance(data, dict):
        return _OverlayTraceDataFile("source_overlay_trace_json_not_object")
    return _OverlayTraceDataFile(
        "valid",
        hashlib.sha256(payload).hexdigest().upper(),
        data,
    )


def _expected_overlay_path_status(
    source_row: Mapping[str, str],
    allowlist_row: Mapping[str, str],
    *,
    artifact_base_dir: Path | None,
) -> str:
    source_path = _bundle_relative_path(
        text_value(source_row.get("family_ms1_overlay_trace_data_json")),
        artifact_base_dir,
    )
    expected_path = _bundle_relative_path(
        text_value(allowlist_row.get("expected_overlay_trace_data_json")),
        artifact_base_dir,
    )
    if source_path is None or expected_path is None:
        return "allowlist_overlay_trace_json_path_outside_bundle"
    if source_path != expected_path:
        return "allowlist_overlay_trace_json_path_mismatch"
    return "valid"


def _bundle_relative_path(path_text: str, base_dir: Path | None) -> str | None:
    raw_path = Path(path_text)
    if raw_path.is_absolute():
        return None
    if base_dir is None:
        if any(part == ".." for part in raw_path.parts):
            return None
        return raw_path.as_posix()
    base = base_dir.resolve()
    resolved = (base / raw_path).resolve()
    try:
        relative = resolved.relative_to(base)
    except ValueError:
        return None
    return relative.as_posix()


def _numeric_sequence(value: object) -> tuple[float, ...]:
    if not isinstance(value, list):
        return ()
    parsed: list[float] = []
    for item in value:
        parsed_value = optional_float(item)
        if parsed_value is None:
            return ()
        parsed.append(parsed_value)
    return tuple(parsed)


def required_ms1_pattern_columns() -> tuple[str, ...]:
    return machine_evidence_support.MS1_PATTERN_COHERENCE_REQUIRED_COLUMNS
