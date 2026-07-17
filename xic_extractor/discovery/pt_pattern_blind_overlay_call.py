"""Sealed machine-call projection for the Pt blind-review overlay."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from xic_extractor.discovery.pt_pattern_evidence_rule import (
    PtPatternEvidenceThresholds,
    evaluate_pt_pattern_evidence,
)
from xic_extractor.discovery.pt_pattern_review_packet import (
    ANCHOR_LOCAL_SCHEMA_VERSION as ANCHOR_PACKET_SCHEMA_VERSION,
)
from xic_extractor.discovery.pt_pattern_review_packet import (
    EVIDENCE_OWNER,
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

SCHEMA_VERSION = "pt_pattern_blind_overlay.v2"
ANCHOR_LOCAL_SCHEMA_VERSION = "pt_pattern_blind_overlay.v3"
SMOOTH_METHOD = TRACE_SMOOTH_METHOD
SMOOTH_WINDOW_POINTS = TRACE_SMOOTH_WINDOW_POINTS

PACKET_REQUIRED_COLUMNS = (
    "packet_label",
    "source_label",
    "packet_row_id",
    "sample_stem",
    "candidate_id",
    "base_mz",
    "base_rt_apex_min",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
    "combined_pt_pattern_status",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "min_shifted_pearson_r_to_base",
    "max_abs_shape_shift_sec_to_base",
    "oracle_status",
)

MACHINE_CALL_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "packet_label",
    "source_label",
    "packet_row_id",
    "candidate_id",
    "machine_call",
    "machine_call_reason",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
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

MACHINE_CALL_THRESHOLDS = PtPatternEvidenceThresholds()


def validate_packet_rows(rows: Sequence[Mapping[str, str]]) -> None:
    effective_versions: set[str] = set()
    anchor_columns = set(ANCHOR_SEGMENTATION_SUMMARY_COLUMNS)
    for row_index, row in enumerate(rows, start=2):
        schema_version = str(row.get("schema_version", "")).strip()
        present_anchor_columns = anchor_columns.intersection(row)
        if schema_version in {"", PACKET_SCHEMA_VERSION}:
            effective_versions.add(PACKET_SCHEMA_VERSION)
            if present_anchor_columns:
                raise ValueError(
                    f"packet row {row_index}: v2 must not contain anchor-local columns"
                )
        elif schema_version == ANCHOR_PACKET_SCHEMA_VERSION:
            effective_versions.add(ANCHOR_PACKET_SCHEMA_VERSION)
            if present_anchor_columns != anchor_columns:
                raise ValueError(
                    f"packet row {row_index}: v3 requires all anchor-local columns"
                )
        else:
            raise ValueError(
                f"packet row {row_index}: unsupported schema_version "
                f"{schema_version!r}"
            )
    if len(effective_versions) > 1:
        raise ValueError("review packet rows must use one schema_version")


def machine_call_row(row: Mapping[str, str]) -> dict[str, object]:
    machine_call, reason = classify_machine_call(row)
    has_anchor_segmentation = all(
        column in row for column in ANCHOR_SEGMENTATION_SUMMARY_COLUMNS
    )
    return {
        "schema_version": (
            ANCHOR_LOCAL_SCHEMA_VERSION if has_anchor_segmentation else SCHEMA_VERSION
        ),
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "packet_label": row.get("packet_label", ""),
        "source_label": row.get("source_label", ""),
        "packet_row_id": row.get("packet_row_id", ""),
        "candidate_id": row.get("candidate_id", ""),
        "machine_call": machine_call,
        "machine_call_reason": reason,
        "trace_decision_basis": row.get("trace_decision_basis", ""),
        "trace_smoothing_method": row.get("trace_smoothing_method", ""),
        "trace_smoothing_window_points": row.get("trace_smoothing_window_points", ""),
        "min_effective_smoothing_window_sec": row.get(
            "min_effective_smoothing_window_sec", ""
        ),
        "max_trace_point_count": row.get("max_trace_point_count", ""),
        "combined_pt_pattern_status": row.get("combined_pt_pattern_status", ""),
        "non_base_positions_supported": row.get("non_base_positions_supported", ""),
        "max_abs_apex_delta_sec": row.get("max_abs_apex_delta_sec", ""),
        "min_pearson_r_to_base": row.get("min_pearson_r_to_base", ""),
        "min_active_overlap_fraction": row.get("min_active_overlap_fraction", ""),
        "min_shifted_pearson_r_to_base": row.get(
            "min_shifted_pearson_r_to_base", ""
        ),
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


def classify_machine_call(row: Mapping[str, str]) -> tuple[str, str]:
    basis = str(row.get("trace_decision_basis", "")).strip()
    smooth_method = str(row.get("trace_smoothing_method", "")).strip()
    smooth_window = optional_float(row.get("trace_smoothing_window_points"))
    if (
        basis != TRACE_DECISION_BASIS
        or smooth_method != SMOOTH_METHOD
        or smooth_window != float(SMOOTH_WINDOW_POINTS)
    ):
        return "uncertain", "unsupported_or_stale_trace_decision_basis"

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
