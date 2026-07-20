"""Sealed trace-evidence disposition for the Pt blind-review overlay.

The call is diagnostic routing over recorded trace evidence.  It is not a
chemical-presence, compound-identity, counting, or quantitation decision.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
    PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
    PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD,
    PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD,
    PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
)
from xic_extractor.discovery.pt_pattern_evidence_rule import (
    PtPatternEvidenceThresholds,
    evaluate_pt_pattern_evidence,
)
from xic_extractor.discovery.pt_pattern_review_packet import (
    ANCHOR_LOCAL_SCHEMA_VERSION as ANCHOR_PACKET_SCHEMA_VERSION,
)
from xic_extractor.discovery.pt_pattern_review_packet import (
    EVIDENCE_OWNER,
    FULL_CONTEXT_PACKET_COLUMNS,
    PACKET_SCHEMA_COLUMNS,
    PACKET_SCHEMA_ROLE,
    READINESS_TIER,
)
from xic_extractor.discovery.pt_pattern_review_packet import (
    SCHEMA_VERSION as PACKET_SCHEMA_VERSION,
)
from xic_extractor.discovery.pt_pattern_trace_evidence import (
    ANCHOR_SEGMENTATION_SUMMARY_COLUMNS,
    TRACE_DECISION_BASIS,
    TRACE_SMOOTH_METHOD,
    TRACE_SMOOTH_WINDOW_POINTS,
)
from xic_extractor.tabular_io import optional_float

SCHEMA_VERSION = "pt_pattern_blind_overlay.v4"
ANCHOR_LOCAL_SCHEMA_VERSION = "pt_pattern_blind_overlay.v5"
SMOOTH_METHOD = TRACE_SMOOTH_METHOD
SMOOTH_WINDOW_POINTS = TRACE_SMOOTH_WINDOW_POINTS
MACHINE_CALL_SCOPE = "trace_evidence_disposition_only"

LEGACY_PACKET_REQUIRED_COLUMNS = FULL_CONTEXT_PACKET_COLUMNS
PACKET_REQUIRED_COLUMNS = FULL_CONTEXT_PACKET_COLUMNS

MACHINE_CALL_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "packet_label",
    "source_label",
    "packet_row_id",
    "sample_stem",
    "candidate_id",
    "candidate_assignment_sha256",
    "trace_extraction_sha256",
    "trace_artifact_role",
    "machine_call",
    "machine_call_reason",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
    "min_median_rt_step_sec",
    "min_effective_smoothing_window_sec",
    "max_trace_point_count",
    "combined_pt_pattern_status",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "min_shifted_pearson_r_to_base",
    "max_abs_shape_shift_sec_to_base",
    "trace_ambiguity_status",
    "trace_ambiguity_flags",
    "shifted_only_position_labels",
    *ANCHOR_SEGMENTATION_SUMMARY_COLUMNS,
    "oracle_status",
)
LEGACY_MACHINE_CALL_COLUMNS = tuple(
    column
    for column in MACHINE_CALL_COLUMNS
    if column not in ANCHOR_SEGMENTATION_SUMMARY_COLUMNS
)
FULL_CONTEXT_MACHINE_CALL_COLUMNS = LEGACY_MACHINE_CALL_COLUMNS
ANCHOR_LOCAL_MACHINE_CALL_COLUMNS = MACHINE_CALL_COLUMNS
LEGACY_MACHINE_CALL_REQUIRED_COLUMNS = FULL_CONTEXT_MACHINE_CALL_COLUMNS
PACKET_TO_MACHINE_SCHEMA = {
    PACKET_SCHEMA_VERSION: SCHEMA_VERSION,
    ANCHOR_PACKET_SCHEMA_VERSION: ANCHOR_LOCAL_SCHEMA_VERSION,
}
MACHINE_SCHEMA_COLUMNS = {
    SCHEMA_VERSION: FULL_CONTEXT_MACHINE_CALL_COLUMNS,
    ANCHOR_LOCAL_SCHEMA_VERSION: ANCHOR_LOCAL_MACHINE_CALL_COLUMNS,
}
MACHINE_SCHEMA_ROLE = {
    SCHEMA_VERSION: PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
    ANCHOR_LOCAL_SCHEMA_VERSION: PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
}

MACHINE_CALL_THRESHOLDS = PtPatternEvidenceThresholds()


def validate_packet_rows(rows: Sequence[Mapping[str, object]]) -> None:
    if not rows:
        raise ValueError("review packet must contain at least one row")
    _validate_unique_row_identity(rows, label="review packet")
    effective_versions: set[str] = set()
    for row_index, row in enumerate(rows, start=2):
        schema_version = str(row.get("schema_version", "")).strip()
        expected_columns = PACKET_SCHEMA_COLUMNS.get(schema_version)
        expected_role = PACKET_SCHEMA_ROLE.get(schema_version)
        if expected_columns is None or expected_role is None:
            raise ValueError(
                f"packet row {row_index}: unsupported schema_version {schema_version!r}"
            )
        effective_versions.add(schema_version)
        _validate_exact_row_columns(
            row,
            expected_columns=expected_columns,
            row_index=row_index,
            label="packet",
        )
        _validate_lineage_fields(
            row,
            row_index=row_index,
            label="packet",
            expected_role=expected_role,
        )
        _validate_packet_envelope(row, row_index=row_index)
    if len(effective_versions) > 1:
        raise ValueError("review packet rows must use one schema_version")


def validate_machine_call_rows(rows: Sequence[Mapping[str, object]]) -> None:
    _validate_unique_row_identity(rows, label="machine-call")
    effective_versions: set[str] = set()
    for row_index, row in enumerate(rows, start=2):
        schema_version = str(row.get("schema_version", "")).strip()
        expected_columns = MACHINE_SCHEMA_COLUMNS.get(schema_version)
        expected_role = MACHINE_SCHEMA_ROLE.get(schema_version)
        if expected_columns is None or expected_role is None:
            raise ValueError(
                f"machine-call row {row_index}: unsupported schema_version "
                f"{schema_version!r}"
            )
        effective_versions.add(schema_version)
        _validate_exact_row_columns(
            row,
            expected_columns=expected_columns,
            row_index=row_index,
            label="machine-call",
        )
        _validate_lineage_fields(
            row,
            row_index=row_index,
            label="machine-call",
            expected_role=expected_role,
        )
    if len(effective_versions) > 1:
        raise ValueError("machine-call rows must use one schema_version")


def _validate_unique_row_identity(
    rows: Sequence[Mapping[str, object]],
    *,
    label: str,
) -> None:
    for column in ("packet_row_id", "candidate_id"):
        seen: set[str] = set()
        for row_index, row in enumerate(rows, start=2):
            value = str(row.get(column, "")).strip()
            if not value:
                raise ValueError(f"{label} row {row_index}: {column} must be non-empty")
            if value in seen:
                raise ValueError(f"duplicate {label} {column}: {value}")
            seen.add(value)


def _validate_exact_row_columns(
    row: Mapping[str, object],
    *,
    expected_columns: Sequence[str],
    row_index: int,
    label: str,
) -> None:
    expected = set(expected_columns)
    observed = set(row)
    missing = [column for column in expected_columns if column not in observed]
    unexpected = sorted(observed - expected)
    if missing or unexpected:
        raise ValueError(
            f"{label} row {row_index}: current schema columns mismatch; "
            f"missing={missing!r}; unexpected={unexpected!r}"
        )


def _validate_packet_envelope(
    row: Mapping[str, object],
    *,
    row_index: int,
) -> None:
    expected_values = {
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "oracle_status": "pending_human_review",
    }
    for field, expected in expected_values.items():
        if str(row.get(field, "")).strip() != expected:
            raise ValueError(
                f"packet row {row_index}: {field} must be {expected!r}"
            )
    for field in ("human_review_decision", "human_review_note"):
        if str(row.get(field, "")).strip():
            raise ValueError(f"packet row {row_index}: {field} must be blank")


def machine_call_row(row: Mapping[str, object]) -> dict[str, object]:
    machine_call, reason = classify_machine_call(row)
    packet_schema = str(row.get("schema_version", "")).strip()
    if packet_schema == PACKET_SCHEMA_VERSION:
        machine_schema = SCHEMA_VERSION
        has_anchor_segmentation = False
    elif packet_schema == ANCHOR_PACKET_SCHEMA_VERSION:
        machine_schema = ANCHOR_LOCAL_SCHEMA_VERSION
        has_anchor_segmentation = True
    else:
        raise ValueError(f"unsupported packet schema_version: {packet_schema!r}")
    return {
        "schema_version": machine_schema,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "packet_label": row.get("packet_label", ""),
        "source_label": row.get("source_label", ""),
        "packet_row_id": row.get("packet_row_id", ""),
        "sample_stem": row.get("sample_stem", ""),
        "candidate_id": row.get("candidate_id", ""),
        "candidate_assignment_sha256": row.get(
            PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
            "",
        ),
        "trace_extraction_sha256": row.get(
            PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD,
            "",
        ),
        "trace_artifact_role": row.get(
            PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD,
            "",
        ),
        "machine_call": machine_call,
        "machine_call_reason": reason,
        "trace_decision_basis": row.get("trace_decision_basis", ""),
        "trace_smoothing_method": row.get("trace_smoothing_method", ""),
        "trace_smoothing_window_points": row.get("trace_smoothing_window_points", ""),
        "min_median_rt_step_sec": row.get("min_median_rt_step_sec", ""),
        "min_effective_smoothing_window_sec": row.get(
            "min_effective_smoothing_window_sec", ""
        ),
        "max_trace_point_count": row.get("max_trace_point_count", ""),
        "combined_pt_pattern_status": row.get("combined_pt_pattern_status", ""),
        "non_base_positions_supported": row.get("non_base_positions_supported", ""),
        "max_abs_apex_delta_sec": row.get("max_abs_apex_delta_sec", ""),
        "min_pearson_r_to_base": row.get("min_pearson_r_to_base", ""),
        "min_active_overlap_fraction": row.get("min_active_overlap_fraction", ""),
        "min_shifted_pearson_r_to_base": row.get("min_shifted_pearson_r_to_base", ""),
        "max_abs_shape_shift_sec_to_base": row.get(
            "max_abs_shape_shift_sec_to_base", ""
        ),
        "trace_ambiguity_status": row.get("trace_ambiguity_status", ""),
        "trace_ambiguity_flags": row.get("trace_ambiguity_flags", ""),
        "shifted_only_position_labels": row.get("shifted_only_position_labels", ""),
        **{
            column: row.get(column, "")
            for column in ANCHOR_SEGMENTATION_SUMMARY_COLUMNS
            if has_anchor_segmentation
        },
        "oracle_status": row.get("oracle_status", ""),
    }


def machine_accept_candidate_ids(
    rows: Sequence[Mapping[str, object]],
) -> frozenset[str]:
    """Return candidates whose sealed disposition requires complete traces."""

    return frozenset(
        str(row.get("candidate_id", "")).strip()
        for row in rows
        if machine_call_row(row)["machine_call"] == "accept"
    )


def classify_machine_call(row: Mapping[str, object]) -> tuple[str, str]:
    packet_schema = str(row.get("schema_version", "")).strip()
    expected_role = PACKET_SCHEMA_ROLE.get(packet_schema)
    if expected_role is None or not _has_valid_lineage(row, expected_role):
        return "uncertain", "unsupported_or_stale_packet_lineage"

    basis = str(row.get("trace_decision_basis", "")).strip()
    smooth_method = str(row.get("trace_smoothing_method", "")).strip()
    smooth_window = optional_float(row.get("trace_smoothing_window_points"))
    if (
        basis != TRACE_DECISION_BASIS
        or smooth_method != SMOOTH_METHOD
        or smooth_window != float(SMOOTH_WINDOW_POINTS)
    ):
        return "uncertain", "unsupported_or_stale_trace_decision_basis"

    trace_ambiguity = str(row.get("trace_ambiguity_status", "")).strip()
    segmentation_status = str(row.get("anchor_segmentation_status", "")).strip()
    boundary_status = str(
        row.get("anchor_segmentation_boundary_review_status", "")
    ).strip()
    if trace_ambiguity == "review_required":
        return "uncertain", "trace_ambiguity_review_required"
    if boundary_status == "review_required":
        return "uncertain", "anchor_segmentation_boundary_review_required"

    if packet_schema == ANCHOR_PACKET_SCHEMA_VERSION:
        if trace_ambiguity != "clear":
            return "uncertain", "trace_ambiguity_not_clear"
        if segmentation_status != "clear":
            return "uncertain", "anchor_segmentation_not_clear"
        if boundary_status != "clear":
            return "uncertain", "anchor_segmentation_boundary_not_clear"
    elif trace_ambiguity not in {"", "clear"}:
        return "uncertain", "trace_ambiguity_not_clear"

    status = str(row.get("combined_pt_pattern_status", "")).strip()
    supported = optional_float(row.get("non_base_positions_supported"))
    min_pearson = optional_float(row.get("min_pearson_r_to_base"))

    decision = evaluate_pt_pattern_evidence(
        row,
        thresholds=MACHINE_CALL_THRESHOLDS,
    )
    if decision.call == "accept":
        return decision.call, decision.reason
    if decision.reason in {
        "shape_shift_above_threshold",
        "shifted_shape_review_only",
    }:
        return "reject", decision.reason

    if status in {
        "pt_pattern_candidate_incomplete",
        "pt_pattern_candidate_base_missing",
    }:
        return "reject", status
    if supported is not None and supported <= 1:
        return "reject", "one_or_fewer_non_base_positions_supported"
    if min_pearson is not None and min_pearson < 0.30:
        return "reject", "low_min_trace_shape_correlation"

    return "uncertain", "requires_visual_or_human_review"


def _validate_lineage_fields(
    row: Mapping[str, object],
    *,
    row_index: int,
    label: str,
    expected_role: str,
) -> None:
    if not _has_valid_lineage(row, expected_role):
        raise ValueError(
            f"{label} row {row_index}: invalid assignment/extraction lineage or role"
        )


def _has_valid_lineage(row: Mapping[str, object], expected_role: str) -> bool:
    return (
        str(row.get(PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD, "")).strip() == expected_role
        and re.fullmatch(
            r"[0-9a-f]{64}",
            str(row.get(PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD, "")).strip(),
        )
        is not None
        and re.fullmatch(
            r"[0-9a-f]{64}",
            str(row.get(PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD, "")).strip(),
        )
        is not None
    )
