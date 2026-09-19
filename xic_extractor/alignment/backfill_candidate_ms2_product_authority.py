from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from xic_extractor.alignment._backfill_util import (
    allowlist_rows_by_family_sample_key,
    rows_by_family_sample_key,
)
from xic_extractor.alignment.backfill_evidence_projection import (
    PRODUCT_AUTHORITY_SCOPE_FIELD,
    PRODUCT_AUTHORITY_SOURCE_FIELD,
    PRODUCT_AUTHORITY_STATUS_FIELD,
    PRODUCT_AUTHORIZED_SCOPE,
    PRODUCT_AUTHORIZED_STATUS,
)
from xic_extractor.alignment.shared_peak_identity_explanation import (
    candidate_ms2_pattern,
)
from xic_extractor.tabular_io import optional_float, text_value

SCHEMA_VERSION = "backfill_candidate_ms2_pattern_product_authority"
DEFAULT_MIN_CANDIDATE_MS2_SIMILARITY_SCORE = 0.5
_SUPPORTED_STATUSES = frozenset({"supportive", "partial_support"})
_SUPPORTED_LEVELS = frozenset(
    {"sample_candidate_aligned", "sample_boundary_aligned"}
)
_EXPECTED_SOURCE_SCHEMA_VERSION = (
    candidate_ms2_pattern.CANDIDATE_MS2_PATTERN_SCHEMA_VERSION
)
_DIRECT_CANDIDATE_ALIGNMENT_SOURCE = "discovery_source_candidate"
_RAW_BOUNDARY_ALIGNMENT_SOURCE = "raw_boundary_scan"

ALLOWLIST_COLUMNS = (
    "schema_version",
    "feature_family_id",
    "sample_stem",
    "authority_status",
    "authority_source",
    "authority_reason",
    "expected_candidate_ms2_pattern_status",
    "expected_candidate_ms2_evidence_level",
    "expected_ms2_alignment_source",
    "expected_candidate_ms2_source_row_sha256",
    "min_candidate_ms2_similarity_score",
)

AUTHORITY_OUTPUT_COLUMNS = (
    PRODUCT_AUTHORITY_STATUS_FIELD,
    PRODUCT_AUTHORITY_SCOPE_FIELD,
    PRODUCT_AUTHORITY_SOURCE_FIELD,
    "product_authority_reason",
    "product_authority_min_candidate_ms2_similarity_score",
    "product_authority_observed_candidate_ms2_similarity_score",
    "product_authority_candidate_ms2_source_row_sha256",
    "product_authority_expected_candidate_ms2_source_row_sha256",
)

AUDIT_COLUMNS = (
    "schema_version",
    "feature_family_id",
    "sample_stem",
    "authority_status",
    "decision",
    "decision_reason",
    "source_candidate_ms2_pattern_status",
    "source_candidate_ms2_evidence_level",
    "source_candidate_ms2_similarity_score",
    "source_ms2_alignment_source",
    "source_candidate_ms2_source_row_sha256",
    "expected_candidate_ms2_pattern_status",
    "expected_candidate_ms2_evidence_level",
    "expected_ms2_alignment_source",
    "expected_candidate_ms2_source_row_sha256",
)

_HASH_EXCLUDED_COLUMNS = frozenset(
    {
        PRODUCT_AUTHORITY_STATUS_FIELD,
        PRODUCT_AUTHORITY_SCOPE_FIELD,
        PRODUCT_AUTHORITY_SOURCE_FIELD,
        *AUTHORITY_OUTPUT_COLUMNS,
    }
)


@dataclass(frozen=True)
class ProductAuthorityResult:
    authorized_rows: tuple[dict[str, str], ...]
    audit_rows: tuple[dict[str, str], ...]
    summary: dict[str, object]


@dataclass(frozen=True)
class _AuthorizationDecision:
    decision: str
    reason: str
    source_row_sha256: str = ""


def authorize_candidate_ms2_pattern_rows(
    *,
    candidate_ms2_pattern_rows: Sequence[Mapping[str, str]],
    allowlist_rows: Sequence[Mapping[str, str]],
) -> ProductAuthorityResult:
    source_by_key = _rows_by_key(candidate_ms2_pattern_rows)
    allowlist_by_key = _allowlist_by_key(allowlist_rows)
    authorized_rows: list[dict[str, str]] = []
    audit_rows: list[dict[str, str]] = []
    for key in sorted(allowlist_by_key):
        allowlist_row = allowlist_by_key[key]
        source_row = source_by_key.get(key)
        authorization = _authorization_decision(source_row, allowlist_row)
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
                source_row_sha256=authorization.source_row_sha256,
            )
        )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "allowlist_row_count": len(allowlist_rows),
        "source_row_count": len(candidate_ms2_pattern_rows),
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


def source_row_sha256(row: Mapping[str, str]) -> str:
    payload = {
        key: text_value(value)
        for key, value in sorted(row.items())
        if key not in _HASH_EXCLUDED_COLUMNS
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def required_candidate_ms2_pattern_columns() -> tuple[str, ...]:
    return candidate_ms2_pattern.CANDIDATE_MS2_PATTERN_COLUMNS


def _rows_by_key(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    return rows_by_family_sample_key(
        rows,
        duplicate_label="backfill Candidate MS2 product authority source",
    )


def _allowlist_by_key(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    return allowlist_rows_by_family_sample_key(
        rows,
        expected_schema_version=SCHEMA_VERSION,
        schema_label="backfill Candidate MS2 product authority",
        missing_label="backfill Candidate MS2 product authority",
        duplicate_label="backfill Candidate MS2 product authority allowlist",
    )


def _authorization_decision(
    source_row: Mapping[str, str] | None,
    allowlist_row: Mapping[str, str],
) -> _AuthorizationDecision:
    if text_value(allowlist_row.get("authority_status")) != PRODUCT_AUTHORIZED_STATUS:
        return _reject("allowlist_status_not_product_authorized")
    if not text_value(allowlist_row.get("authority_source")):
        return _reject("allowlist_authority_source_missing")
    if source_row is None:
        return _reject("source_candidate_ms2_pattern_row_missing")
    source_hash = source_row_sha256(source_row)
    missing_source_columns = _missing_source_columns(source_row)
    if missing_source_columns:
        return _reject(
            "source_candidate_ms2_full_producer_columns_missing",
            source_row_sha256=source_hash,
        )
    if (
        text_value(source_row.get("candidate_ms2_pattern_schema_version"))
        != _EXPECTED_SOURCE_SCHEMA_VERSION
    ):
        return _reject(
            "source_candidate_ms2_schema_version_mismatch",
            source_row_sha256=source_hash,
        )
    if text_value(source_row.get("diagnostic_only")).upper() != "TRUE":
        return _reject(
            "source_candidate_ms2_not_diagnostic_only",
            source_row_sha256=source_hash,
        )
    source_status = text_value(source_row.get("candidate_ms2_pattern_status"))
    if source_status not in _SUPPORTED_STATUSES:
        return _reject(
            "source_candidate_ms2_pattern_not_supportive",
            source_row_sha256=source_hash,
        )
    source_level = text_value(source_row.get("candidate_ms2_evidence_level"))
    if source_level not in _SUPPORTED_LEVELS:
        return _reject(
            "source_candidate_ms2_evidence_level_not_product_context",
            source_row_sha256=source_hash,
        )
    provenance_status = _product_source_provenance_status(source_row)
    if provenance_status != "valid":
        return _reject(provenance_status, source_row_sha256=source_hash)
    expected_status = text_value(
        allowlist_row.get("expected_candidate_ms2_pattern_status")
    )
    if expected_status != source_status:
        return _reject(
            "allowlist_candidate_ms2_pattern_status_mismatch",
            source_row_sha256=source_hash,
        )
    expected_level = text_value(
        allowlist_row.get("expected_candidate_ms2_evidence_level")
    )
    if expected_level != source_level:
        return _reject(
            "allowlist_candidate_ms2_evidence_level_mismatch",
            source_row_sha256=source_hash,
        )
    expected_alignment_source = text_value(
        allowlist_row.get("expected_ms2_alignment_source")
    )
    source_alignment = text_value(source_row.get("ms2_alignment_source"))
    if expected_alignment_source != source_alignment:
        return _reject(
            "allowlist_ms2_alignment_source_mismatch",
            source_row_sha256=source_hash,
        )
    expected_hash = text_value(
        allowlist_row.get("expected_candidate_ms2_source_row_sha256")
    ).upper()
    if not expected_hash:
        return _reject(
            "allowlist_candidate_ms2_source_row_sha256_missing",
            source_row_sha256=source_hash,
        )
    if expected_hash != source_hash:
        return _reject(
            "allowlist_candidate_ms2_source_row_sha256_mismatch",
            source_row_sha256=source_hash,
        )
    observed_score = optional_float(source_row.get("candidate_ms2_similarity_score"))
    if observed_score is None:
        return _reject(
            "source_candidate_ms2_similarity_score_missing",
            source_row_sha256=source_hash,
        )
    threshold = _threshold(allowlist_row)
    if threshold < DEFAULT_MIN_CANDIDATE_MS2_SIMILARITY_SCORE:
        return _reject(
            "allowlist_candidate_ms2_similarity_threshold_below_default",
            source_row_sha256=source_hash,
        )
    if observed_score < threshold:
        return _reject(
            "source_candidate_ms2_similarity_score_below_threshold",
            source_row_sha256=source_hash,
        )
    return _AuthorizationDecision(
        "authorized",
        "product_authority_allowlist_and_candidate_ms2_supported",
        source_hash,
    )


def _authorized_row(
    source_row: Mapping[str, str],
    *,
    allowlist_row: Mapping[str, str],
    source_row_sha256: str,
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
    ) or "reviewed_backfill_candidate_ms2_allowlist"
    row["product_authority_min_candidate_ms2_similarity_score"] = _format_float(
        _threshold(allowlist_row)
    )
    row["product_authority_observed_candidate_ms2_similarity_score"] = text_value(
        source_row.get("candidate_ms2_similarity_score")
    )
    row["product_authority_candidate_ms2_source_row_sha256"] = source_row_sha256
    row["product_authority_expected_candidate_ms2_source_row_sha256"] = text_value(
        allowlist_row.get("expected_candidate_ms2_source_row_sha256")
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
        "source_candidate_ms2_pattern_status": text_value(
            source.get("candidate_ms2_pattern_status")
        ),
        "source_candidate_ms2_evidence_level": text_value(
            source.get("candidate_ms2_evidence_level")
        ),
        "source_candidate_ms2_similarity_score": text_value(
            source.get("candidate_ms2_similarity_score")
        ),
        "source_ms2_alignment_source": text_value(source.get("ms2_alignment_source")),
        "source_candidate_ms2_source_row_sha256": authorization.source_row_sha256,
        "expected_candidate_ms2_pattern_status": text_value(
            allowlist_row.get("expected_candidate_ms2_pattern_status")
        ),
        "expected_candidate_ms2_evidence_level": text_value(
            allowlist_row.get("expected_candidate_ms2_evidence_level")
        ),
        "expected_ms2_alignment_source": text_value(
            allowlist_row.get("expected_ms2_alignment_source")
        ),
        "expected_candidate_ms2_source_row_sha256": text_value(
            allowlist_row.get("expected_candidate_ms2_source_row_sha256")
        ).upper(),
    }


def _threshold(row: Mapping[str, str]) -> float:
    value = optional_float(row.get("min_candidate_ms2_similarity_score"))
    return (
        DEFAULT_MIN_CANDIDATE_MS2_SIMILARITY_SCORE
        if value is None
        else value
    )


def _product_source_provenance_status(row: Mapping[str, str]) -> str:
    level = text_value(row.get("candidate_ms2_evidence_level"))
    alignment_source = text_value(row.get("ms2_alignment_source"))
    if level == "sample_candidate_aligned":
        if alignment_source != _DIRECT_CANDIDATE_ALIGNMENT_SOURCE:
            return "source_candidate_ms2_direct_alignment_source_mismatch"
        if not text_value(row.get("source_candidate_id")):
            return "source_candidate_ms2_direct_candidate_id_missing"
        if text_value(row.get("source_candidate_status")) != "found":
            return "source_candidate_ms2_direct_candidate_not_found"
        if _int_value(row.get("source_matched_tag_count")) <= 0:
            return "source_candidate_ms2_direct_matched_tag_count_missing"
        if _int_value(row.get("matched_neutral_loss_count")) <= 0:
            return "source_candidate_ms2_direct_neutral_loss_count_missing"
        return "valid"
    if level == "sample_boundary_aligned":
        if alignment_source != _RAW_BOUNDARY_ALIGNMENT_SOURCE:
            return "source_candidate_ms2_boundary_alignment_source_mismatch"
        if _int_value(row.get("raw_ms2_trigger_scan_count")) <= 0:
            return "source_candidate_ms2_boundary_trigger_scan_missing"
        if _int_value(row.get("raw_ms2_strict_nl_scan_count")) <= 0:
            return "source_candidate_ms2_boundary_strict_nl_missing"
        if _int_value(row.get("raw_ms2_trace_product_point_count")) <= 0:
            return "source_candidate_ms2_boundary_product_trace_missing"
        if text_value(row.get("raw_reader_runtime")) != "pythonnet":
            return "source_candidate_ms2_boundary_raw_reader_runtime_missing"
        return "valid"
    return "source_candidate_ms2_evidence_level_not_product_context"


def _missing_source_columns(row: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        column
        for column in candidate_ms2_pattern.CANDIDATE_MS2_PATTERN_COLUMNS
        if column not in row
    )


def _int_value(value: object) -> int:
    try:
        return int(text_value(value))
    except ValueError:
        return 0


def _format_float(value: float) -> str:
    return f"{value:.6g}"


def _reject(reason: str, *, source_row_sha256: str = "") -> _AuthorizationDecision:
    return _AuthorizationDecision("rejected", reason, source_row_sha256)
