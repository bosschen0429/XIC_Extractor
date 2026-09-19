"""Cross-artifact identity contracts for Pt pattern diagnostic evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Hashable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Literal, TypeVar

from xic_extractor.discovery.delta_mass_patterns import PT_PATTERN
from xic_extractor.discovery.pt_pattern_evidence_rule import (
    EVIDENCE_RULE_OWNER,
    MAX_NON_BASE_POSITIONS,
    VALID_COMBINED_PT_PATTERN_STATUSES,
    PtPatternEvidenceThresholds,
    effective_max_abs_apex_delta_sec,
    evaluate_pt_pattern_evidence,
    pt_pattern_rule_id,
)
from xic_extractor.mass_accuracy import is_within_mz_tolerance
from xic_extractor.tabular_io import optional_float

PT_PATTERN_RAW_TRACE_BASIS = "raw_vendor_trace"

PT_PATTERN_ANCHOR_SEGMENTATION_SUMMARY_COLUMNS = (
    "anchor_segmentation_status",
    "anchor_segmentation_reason",
    "anchor_segmentation_common_rt_start_min",
    "anchor_segmentation_common_rt_end_min",
    "anchor_segmentation_common_duration_sec",
    "anchor_segmentation_common_start_owner_labels",
    "anchor_segmentation_common_end_owner_labels",
    "anchor_segmentation_boundary_review_status",
    "anchor_segmentation_boundary_review_position_labels",
    "anchor_segmentation_max_left_clip_sec",
    "anchor_segmentation_max_right_clip_sec",
    "anchor_segmentation_min_common_retained_fraction",
    "anchor_segmentation_max_common_left_residual_fraction",
    "anchor_segmentation_max_common_right_residual_fraction",
    "anchor_segmentation_descending_at_common_right_position_labels",
)

PT_PATTERN_ANCHOR_SEGMENTATION_METRIC_COLUMNS = (
    "anchor_segment_rt_start_min",
    "anchor_segment_rt_end_min",
    "anchor_segment_duration_sec",
    "anchor_segment_class",
    "anchor_segment_boundary_stop_reason",
    "anchor_segment_owns_common_start",
    "anchor_segment_owns_common_end",
    "anchor_segment_left_clip_sec",
    "anchor_segment_right_clip_sec",
    "anchor_segment_common_retained_fraction",
    "anchor_segment_common_left_residual_fraction",
    "anchor_segment_common_right_residual_fraction",
    "anchor_segment_common_left_trend",
    "anchor_segment_common_right_trend",
)

PT_PATTERN_TRACE_SUMMARY_COLUMNS = (
    "schema_version",
    "sample_stem",
    "candidate_id",
    "candidate_assignment_sha256",
    "trace_extraction_sha256",
    "trace_artifact_role",
    "scan_pattern_seed_status",
    "base_mz",
    "base_rt_apex_min",
    "rt_half_window_sec",
    "ppm",
    "mz_source",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
    "min_median_rt_step_sec",
    "min_effective_smoothing_window_sec",
    "max_trace_point_count",
    "positions_requested",
    "positions_with_points",
    "positions_with_signal",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "min_shifted_pearson_r_to_base",
    "max_abs_shape_shift_sec_to_base",
    "raw_max_abs_apex_delta_sec",
    "raw_min_pearson_r_to_base",
    "raw_min_active_overlap_fraction",
    "trace_ambiguity_status",
    "trace_ambiguity_flags",
    "shifted_only_position_labels",
    "trace_support_status",
    "combined_pt_pattern_status",
    "reason",
)

PT_PATTERN_TRACE_METRIC_COLUMNS = (
    "schema_version",
    "sample_stem",
    "candidate_id",
    "candidate_assignment_sha256",
    "trace_extraction_sha256",
    "trace_artifact_role",
    "position_label",
    "target_mz",
    "rt_min",
    "rt_max",
    "ppm",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
    "point_count",
    "median_rt_step_sec",
    "effective_smoothing_window_sec",
    "raw_max_intensity",
    "raw_apex_rt_min",
    "raw_apex_delta_sec_to_base",
    "raw_smoothed_apex_delta_sec",
    "raw_height_ratio_to_base",
    "raw_pearson_r_to_base",
    "raw_active_overlap_fraction",
    "max_intensity",
    "apex_rt_min",
    "apex_delta_sec_to_base",
    "height_ratio_to_base",
    "pearson_r_to_base",
    "active_overlap_fraction",
    "shifted_pearson_r_to_base",
    "shape_shift_sec_to_base",
    "trace_quality_flags",
    "trace_support_status",
    "reason",
)

PT_PATTERN_TRACE_POINT_COLUMNS = (
    "schema_version",
    "sample_stem",
    "candidate_id",
    "candidate_assignment_sha256",
    "trace_extraction_sha256",
    "trace_artifact_role",
    "position_label",
    "intensity_basis",
    "rt_min",
    "intensity",
)

PT_PATTERN_ANCHOR_LOCAL_SUMMARY_COLUMNS = (
    *PT_PATTERN_TRACE_SUMMARY_COLUMNS[:-3],
    *PT_PATTERN_ANCHOR_SEGMENTATION_SUMMARY_COLUMNS,
    *PT_PATTERN_TRACE_SUMMARY_COLUMNS[-3:],
)

PT_PATTERN_ANCHOR_LOCAL_METRIC_COLUMNS = (
    *PT_PATTERN_TRACE_METRIC_COLUMNS[:-3],
    *PT_PATTERN_ANCHOR_SEGMENTATION_METRIC_COLUMNS,
    *PT_PATTERN_TRACE_METRIC_COLUMNS[-3:],
)

PT_PATTERN_ANCHOR_LOCAL_TRACE_POINT_COLUMNS = PT_PATTERN_TRACE_POINT_COLUMNS

_TRACE_TEXT_IDENTITY_FIELDS = ("candidate_id", "sample_stem")
PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION = "pt_pattern_trace_diagnosis.v4"
PT_PATTERN_ANCHOR_LOCAL_TRACE_SUMMARY_SCHEMA_VERSION = (
    "pt_pattern_anchor_local_trace_diagnosis.v3"
)
PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSIONS = (
    PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION,
    PT_PATTERN_ANCHOR_LOCAL_TRACE_SUMMARY_SCHEMA_VERSION,
)
PT_PATTERN_EVIDENCE_SCHEMA_VERSION = "pt_pattern_discovery_evidence.v3"
PT_PATTERN_CANDIDATE_ASSIGNMENT_TEXT_FIELDS = (
    "sample_stem",
    "method_label",
    "pattern_id",
    "candidate_id",
    "base_feature_id",
    "pt_plus_1_feature_id",
    "pt_plus_2_feature_id",
    "pt_plus_4_feature_id",
)
PT_PATTERN_CANDIDATE_ASSIGNMENT_NUMERIC_FIELDS = (
    "base_mz",
    "base_rt_apex_min",
    "pt_plus_1_mz",
    "pt_plus_1_expected_mz",
    "pt_plus_2_mz",
    "pt_plus_2_expected_mz",
    "pt_plus_4_mz",
    "pt_plus_4_expected_mz",
)
PT_PATTERN_CANDIDATE_ASSIGNMENT_COLUMNS = (
    *PT_PATTERN_CANDIDATE_ASSIGNMENT_TEXT_FIELDS,
    *PT_PATTERN_CANDIDATE_ASSIGNMENT_NUMERIC_FIELDS,
)
PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD = "candidate_assignment_sha256"
PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD = "trace_extraction_sha256"
PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD = "trace_artifact_role"
PT_PATTERN_TRACE_FULL_CONTEXT_ROLE: Literal["full_context"] = "full_context"
PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE: Literal["anchor_local"] = "anchor_local"
PT_PATTERN_TRACE_BUNDLE_MANIFEST_VERSION = "pt_pattern_trace_bundle_manifest.v1"
PT_PATTERN_TRACE_EXTRACTION_FINGERPRINT_VERSION = "pt_pattern_trace_extraction.v1"
PT_PATTERN_TRACE_SCHEMA_ROLE = {
    PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION: PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
    PT_PATTERN_ANCHOR_LOCAL_TRACE_SUMMARY_SCHEMA_VERSION: (
        PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE
    ),
}
PT_PATTERN_EVIDENCE_TEXT_IDENTITY_FIELDS = (
    "candidate_id",
    "sample_stem",
    "method_label",
    "pattern_id",
    "candidate_state",
)
PT_PATTERN_EVIDENCE_TRACE_LINEAGE_FIELDS = (
    "trace_schema_version",
    PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD,
    PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD,
    "trace_manifest_sha256",
)
PT_PATTERN_NO_AUTHORITY_FIELDS = (
    "matrix_write_authority",
    "counted_detection_authority",
    "export_activation_authority",
)
_NUMERIC_IDENTITY_FIELDS = ("base_mz", "base_rt_apex_min")
PT_PATTERN_EVIDENCE_CONTRACT_COLUMNS = (
    "schema_version",
    "sample_stem",
    "method_label",
    "pattern_id",
    "candidate_id",
    PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
    *PT_PATTERN_EVIDENCE_TRACE_LINEAGE_FIELDS,
    *_NUMERIC_IDENTITY_FIELDS,
    "candidate_state",
    "scan_pattern_seed_status",
    "trace_evidence_status",
    "trace_rule_call",
    "trace_detection_status",
    "trace_rule_reason",
    "evidence_rule_id",
    "evidence_rule_owner",
    "required_combined_status",
    "min_non_base_positions_supported",
    "configured_max_abs_apex_delta_threshold_sec",
    "max_abs_apex_delta_scan_step_multiplier",
    "min_median_rt_step_sec",
    "effective_max_abs_apex_delta_threshold_sec",
    "min_pearson_r_to_base_threshold",
    "min_active_overlap_fraction_threshold",
    "min_shifted_pearson_r_to_base_threshold",
    "max_abs_shape_shift_sec_to_base_threshold",
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
    *PT_PATTERN_NO_AUTHORITY_FIELDS,
)
_VALID_CANDIDATE_STATES = {
    "pt_pattern_candidate",
    "pt_pattern_cid_nl_supported",
}
_VALID_COMBINED_STATUSES = VALID_COMBINED_PT_PATTERN_STATUSES
_NOT_EVALUATED_REASONS = {
    "trace_summary_missing_for_candidate",
    "trace_summary_not_provided",
}
_NON_BASE_POSITION_LABELS = {"pt_plus_1", "pt_plus_2", "pt_plus_4"}
_AMBIGUITY_FLAG_TOKENS = {
    "low_scan_density",
    "short_effective_smoothing_window",
    "raw_smoothed_apex_mismatch",
    "shifted_only_support_present",
    "raw_shape_weak_for_shifted_support",
    "smoothed_shape_weak_for_shifted_support",
    "plus4_shifted_only_support",
    "plus4_window_tail_dominance",
    "large_shape_shift",
    "position_shift_direction_mixed",
    "isolated_position_shift",
}
_VALID_TRACE_EVIDENCE_STATES = {
    ("evaluated", "accept", "detected"),
    ("evaluated", "reject", "non_detected"),
    ("not_evaluated", "not_evaluated", "not_evaluated"),
}
_TRACE_REGENERATION_HINT = (
    " Regenerate the trace summary from the same candidate CSV before retrying."
)
_EVIDENCE_REGENERATION_HINT = (
    " Regenerate the evidence from the same candidate CSV before retrying."
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_PT_DELTA_BY_LABEL = {
    position.label: position.delta_da for position in PT_PATTERN.positions
}


def prepare_pt_trace_summary_rows_for_csv(
    rows: tuple[Mapping[str, object], ...],
) -> tuple[dict[str, object], ...]:
    """Preserve candidate identity coordinates across the summary CSV boundary."""

    prepared_rows: list[dict[str, object]] = []
    for row in rows:
        _required_sha256(
            row,
            field=PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
            side="trace summary",
        )
        prepared = dict(row)
        for field in _NUMERIC_IDENTITY_FIELDS:
            value = _required_finite_number(row, field=field, side="trace summary")
            prepared[field] = format_pt_machine_float(value)
        prepared_rows.append(prepared)
    return tuple(prepared_rows)


def format_pt_machine_float(value: float) -> str:
    """Format a Pt machine-handoff float without losing binary precision."""

    if not math.isfinite(value):
        return ""
    if value.is_integer():
        return str(int(value))
    return repr(value)


def pt_pattern_candidate_assignment_fingerprint(
    candidate_row: Mapping[str, object],
) -> str:
    """Hash the exact feature assignment and m/z payload evaluated downstream.

    This is deliberately separate from ``candidate_id``: the stable hypothesis
    identity remains base-anchored, while this fingerprint invalidates stale
    trace/evidence artifacts when the assigned +1/+2/+4 features change.
    """

    payload: list[tuple[str, str]] = [("contract", "pt_candidate_assignment.v1")]
    candidate_text = {
        field: _required_text(candidate_row, field=field, side="candidate")
        for field in PT_PATTERN_CANDIDATE_ASSIGNMENT_TEXT_FIELDS
    }
    if candidate_text["pattern_id"] != PT_PATTERN.pattern_id:
        raise ValueError(f"candidate pattern_id must be {PT_PATTERN.pattern_id!r}")
    feature_ids = tuple(
        candidate_text[field]
        for field in (
            "base_feature_id",
            "pt_plus_1_feature_id",
            "pt_plus_2_feature_id",
            "pt_plus_4_feature_id",
        )
    )
    if len(set(feature_ids)) != len(feature_ids):
        raise ValueError("candidate feature assignment IDs must be distinct")
    payload.extend(
        (field, candidate_text[field])
        for field in PT_PATTERN_CANDIDATE_ASSIGNMENT_TEXT_FIELDS
    )
    candidate_numeric: dict[str, float] = {}
    for field in PT_PATTERN_CANDIDATE_ASSIGNMENT_NUMERIC_FIELDS:
        value = _required_finite_number(candidate_row, field=field, side="candidate")
        if field == "base_rt_apex_min":
            if value < 0.0:
                raise ValueError("candidate base_rt_apex_min must be >= 0")
        elif value <= 0.0:
            raise ValueError(f"candidate {field} must be > 0")
        candidate_numeric[field] = value
        payload.append((field, format_pt_machine_float(value)))
    _validate_pt_assignment_geometry(
        candidate_row,
        candidate_numeric=candidate_numeric,
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def pt_pattern_trace_extraction_fingerprint(
    *,
    candidates_csv_sha256: str,
    raw_sha256: str,
    source_sample_stem: str,
    limit: int,
    offset: int,
    candidate_assignments: Sequence[tuple[str, str]],
    ppm: float,
    rt_half_window_sec: float,
    mz_source: str,
    active_fraction: float,
    min_pearson_r: float,
    max_apex_delta_sec: float,
    trace_decision_basis: str,
    trace_smoothing_method: str,
    trace_smoothing_window_points: int,
    anchor_segmentation_selector: str,
) -> str:
    """Fingerprint the exact input selection and settings behind trace artifacts."""

    normalized_assignments: list[tuple[str, str]] = []
    seen_candidate_ids: set[str] = set()
    for candidate_id, assignment_sha256 in candidate_assignments:
        normalized_candidate_id = str(candidate_id).strip()
        if not normalized_candidate_id:
            raise ValueError("trace extraction candidate_id must be non-empty")
        if normalized_candidate_id in seen_candidate_ids:
            raise ValueError(
                "trace extraction candidate assignments contain duplicate "
                "candidate_id: " + normalized_candidate_id
            )
        seen_candidate_ids.add(normalized_candidate_id)
        normalized_assignments.append(
            (
                normalized_candidate_id,
                _required_sha256(
                    {PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD: (assignment_sha256)},
                    field=PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
                    side="trace extraction",
                ),
            )
        )

    normalized_mz_source = str(mz_source).strip()
    if normalized_mz_source not in {"expected", "observed"}:
        raise ValueError("trace extraction mz_source must be 'expected' or 'observed'")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("trace extraction limit must be an integer > 0")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("trace extraction offset must be an integer >= 0")

    numeric_payload = {
        "ppm": ppm,
        "rt_half_window_sec": rt_half_window_sec,
        "active_fraction": active_fraction,
        "min_pearson_r": min_pearson_r,
        "max_apex_delta_sec": max_apex_delta_sec,
    }
    normalized_numeric = {
        field: format_pt_machine_float(
            _required_finite_number(
                numeric_payload,
                field=field,
                side="trace extraction",
            )
        )
        for field in numeric_payload
    }
    payload = {
        "contract": PT_PATTERN_TRACE_EXTRACTION_FINGERPRINT_VERSION,
        "candidates_csv_sha256": _required_sha256(
            {"candidates_csv_sha256": candidates_csv_sha256},
            field="candidates_csv_sha256",
            side="trace extraction",
        ),
        "raw_sha256": _required_sha256(
            {"raw_sha256": raw_sha256},
            field="raw_sha256",
            side="trace extraction",
        ),
        "source_sample_stem": _required_text(
            {"source_sample_stem": source_sample_stem},
            field="source_sample_stem",
            side="trace extraction",
        ),
        "limit": limit,
        "offset": offset,
        "candidate_assignments": sorted(normalized_assignments),
        **normalized_numeric,
        "mz_source": normalized_mz_source,
        "trace_decision_basis": str(trace_decision_basis).strip(),
        "trace_smoothing_method": str(trace_smoothing_method).strip(),
        "trace_smoothing_window_points": trace_smoothing_window_points,
        "anchor_segmentation_selector": str(anchor_segmentation_selector).strip(),
        "full_trace_schema_version": PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION,
        "anchor_trace_schema_version": (
            PT_PATTERN_ANCHOR_LOCAL_TRACE_SUMMARY_SCHEMA_VERSION
        ),
    }
    for field in (
        "trace_decision_basis",
        "trace_smoothing_method",
        "anchor_segmentation_selector",
    ):
        if not payload[field]:
            raise ValueError(f"trace extraction {field} must be non-empty")
    if (
        isinstance(trace_smoothing_window_points, bool)
        or not isinstance(trace_smoothing_window_points, int)
        or trace_smoothing_window_points <= 0
    ):
        raise ValueError(
            "trace extraction trace_smoothing_window_points must be an integer > 0"
        )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_pt_assignment_geometry(
    candidate_row: Mapping[str, object],
    *,
    candidate_numeric: Mapping[str, float],
) -> None:
    base_mz = candidate_numeric["base_mz"]
    observed_positions = [base_mz]
    for label in ("pt_plus_1", "pt_plus_2", "pt_plus_4"):
        observed_field = f"{label}_mz"
        expected_field = f"{label}_expected_mz"
        observed_mz = candidate_numeric[observed_field]
        expected_mz = candidate_numeric[expected_field]
        theoretical_mz = base_mz + _PT_DELTA_BY_LABEL[label]
        serialization_tolerance_da = _assignment_serialization_tolerance_da(
            candidate_row.get("base_mz"),
            candidate_row.get(expected_field),
            magnitude=max(base_mz, expected_mz),
        )
        if not math.isclose(
            expected_mz,
            theoretical_mz,
            rel_tol=0.0,
            abs_tol=serialization_tolerance_da,
        ):
            raise ValueError(
                f"candidate {expected_field} must equal base_mz + "
                f"{_PT_DELTA_BY_LABEL[label]:g} Da within serialization "
                "precision"
            )
        if not is_within_mz_tolerance(
            observed_mz,
            expected_mz,
            tolerance_ppm=PT_PATTERN.mz_tolerance_ppm,
        ):
            raise ValueError(
                f"candidate {observed_field} must be within "
                f"{PT_PATTERN.mz_tolerance_ppm:g} ppm of {expected_field}"
            )
        observed_positions.append(observed_mz)
    if any(
        right <= left
        for left, right in zip(
            observed_positions,
            observed_positions[1:],
        )
    ):
        raise ValueError("candidate Pt position m/z values must be strictly ascending")


def _assignment_serialization_tolerance_da(
    *raw_values: object,
    magnitude: float,
) -> float:
    represented_half_ulps = sum(
        _serialized_decimal_half_ulp(value) for value in raw_values
    )
    six_sig_combined_ulp = 10.0 ** (math.floor(math.log10(magnitude)) - 5)
    return min(represented_half_ulps, six_sig_combined_ulp) + 1e-12


def _serialized_decimal_half_ulp(value: object) -> float:
    try:
        normalized = Decimal(str(value).strip()).normalize()
    except InvalidOperation:
        return 0.0
    exponent = normalized.as_tuple().exponent
    if not isinstance(exponent, int):
        return 0.0
    if exponent >= 0:
        return 0.5
    return float(Decimal(5).scaleb(exponent - 1))


def validate_pt_candidate_trace_summary_identity(
    candidate_row: Mapping[str, object],
    trace_summary_row: Mapping[str, object],
) -> None:
    """Fail closed unless candidate and trace summary identify the same peak."""

    observed_schema = _required_text(
        trace_summary_row,
        field="schema_version",
        side="trace summary",
    )
    if observed_schema not in PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSIONS:
        raise ValueError(
            f"unsupported trace summary schema_version {observed_schema!r}; "
            "expected one of "
            f"{PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSIONS!r}." + _TRACE_REGENERATION_HINT
        )

    _validate_pt_artifact_identity(
        candidate_row,
        trace_summary_row,
        artifact_side="trace summary",
        text_fields=_TRACE_TEXT_IDENTITY_FIELDS,
        regeneration_hint=_TRACE_REGENERATION_HINT,
    )
    _validate_candidate_assignment_fingerprint(
        candidate_row,
        trace_summary_row,
        artifact_side="trace summary",
        regeneration_hint=_TRACE_REGENERATION_HINT,
    )


def validate_pt_candidate_evidence_identity(
    candidate_row: Mapping[str, object],
    evidence_row: Mapping[str, object],
) -> None:
    """Fail closed unless evidence was generated for this candidate payload."""

    _validate_pt_artifact_identity(
        candidate_row,
        evidence_row,
        artifact_side="evidence",
        text_fields=PT_PATTERN_EVIDENCE_TEXT_IDENTITY_FIELDS,
        regeneration_hint=_EVIDENCE_REGENERATION_HINT,
    )
    _validate_candidate_assignment_fingerprint(
        candidate_row,
        evidence_row,
        artifact_side="evidence",
        regeneration_hint=_EVIDENCE_REGENERATION_HINT,
    )


def validate_pt_pattern_evidence_row_contract(
    row: Mapping[str, object],
    *,
    side: str = "evidence row",
) -> None:
    """Validate the standalone, versioned portion of one evidence row."""

    _validate_schema_version(
        row,
        expected=PT_PATTERN_EVIDENCE_SCHEMA_VERSION,
        side=side,
        regeneration_hint=_EVIDENCE_REGENERATION_HINT,
    )
    identity_values = {
        field: _required_text(row, field=field, side=side)
        for field in PT_PATTERN_EVIDENCE_TEXT_IDENTITY_FIELDS
    }
    if identity_values["pattern_id"] != PT_PATTERN.pattern_id:
        raise ValueError(f"{side} pattern_id must be {PT_PATTERN.pattern_id!r}")
    if identity_values["candidate_state"] not in _VALID_CANDIDATE_STATES:
        raise ValueError(
            f"{side} candidate_state must be one of {sorted(_VALID_CANDIDATE_STATES)!r}"
        )
    numeric_identity = {
        field: _required_finite_number(row, field=field, side=side)
        for field in _NUMERIC_IDENTITY_FIELDS
    }
    if numeric_identity["base_mz"] <= 0:
        raise ValueError(f"{side} base_mz must be > 0")
    if numeric_identity["base_rt_apex_min"] < 0:
        raise ValueError(f"{side} base_rt_apex_min must be >= 0")
    _required_sha256(
        row,
        field=PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
        side=side,
    )
    trace_state = tuple(
        _required_text(row, field=field, side=side)
        for field in (
            "trace_evidence_status",
            "trace_rule_call",
            "trace_detection_status",
        )
    )
    if trace_state not in _VALID_TRACE_EVIDENCE_STATES:
        raise ValueError(
            f"{side} has invalid trace evidence state: {trace_state!r}."
            + _EVIDENCE_REGENERATION_HINT
        )
    trace_reason = _required_text(
        row,
        field="trace_rule_reason",
        side=side,
    )
    _validate_evidence_trace_lineage(
        row,
        trace_evidence_status=trace_state[0],
        trace_rule_reason=trace_reason,
        side=side,
    )
    seed_status = _required_text(
        row,
        field="scan_pattern_seed_status",
        side=side,
    )
    if seed_status != "full_pattern_seeded":
        raise ValueError(
            f"{side} scan_pattern_seed_status must be 'full_pattern_seeded'"
        )
    thresholds = _evidence_thresholds(row, side=side)
    observed_rule_id = _required_text(
        row,
        field="evidence_rule_id",
        side=side,
    )
    expected_rule_id = pt_pattern_rule_id(thresholds)
    if observed_rule_id != expected_rule_id:
        raise ValueError(
            f"{side} evidence_rule_id must match the persisted threshold "
            f"configuration: {expected_rule_id!r}"
        )
    rule_owner = _required_text(
        row,
        field="evidence_rule_owner",
        side=side,
    )
    if rule_owner != EVIDENCE_RULE_OWNER:
        raise ValueError(f"{side} evidence_rule_owner must be {EVIDENCE_RULE_OWNER!r}")
    if trace_state[0] == "not_evaluated":
        _validate_not_evaluated_evidence(row, side=side)
    else:
        _validate_evaluated_evidence(row, side=side, thresholds=thresholds)
    for field in PT_PATTERN_NO_AUTHORITY_FIELDS:
        _required_false_authority(row, field=field, side=side)


def _validate_evidence_trace_lineage(
    row: Mapping[str, object],
    *,
    trace_evidence_status: str,
    trace_rule_reason: str,
    side: str,
) -> None:
    if (
        trace_evidence_status == "not_evaluated"
        and trace_rule_reason == "trace_summary_not_provided"
    ):
        for field in PT_PATTERN_EVIDENCE_TRACE_LINEAGE_FIELDS:
            _require_blank(row, field=field, side=side)
        return

    schema_version = _required_text(
        row,
        field="trace_schema_version",
        side=side,
    )
    if schema_version != PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION:
        raise ValueError(
            f"{side} trace_schema_version must be the full-context trace schema "
            f"{PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION!r}"
        )
    artifact_role = _required_text(
        row,
        field=PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD,
        side=side,
    )
    if artifact_role != PT_PATTERN_TRACE_FULL_CONTEXT_ROLE:
        raise ValueError(
            f"{side} trace_artifact_role must be "
            f"{PT_PATTERN_TRACE_FULL_CONTEXT_ROLE!r} for Discovery evidence"
        )
    _required_sha256(
        row,
        field=PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD,
        side=side,
    )
    _required_sha256(
        row,
        field="trace_manifest_sha256",
        side=side,
    )


def _evidence_thresholds(
    row: Mapping[str, object],
    *,
    side: str,
) -> PtPatternEvidenceThresholds:
    return PtPatternEvidenceThresholds(
        required_combined_status=_required_text(
            row,
            field="required_combined_status",
            side=side,
        ),
        min_non_base_positions_supported=_required_integer_number(
            row,
            field="min_non_base_positions_supported",
            side=side,
            minimum=1,
            maximum=MAX_NON_BASE_POSITIONS,
        ),
        max_abs_apex_delta_sec=_required_bounded_number(
            row,
            field="configured_max_abs_apex_delta_threshold_sec",
            side=side,
            exclusive_minimum=0.0,
        ),
        max_abs_apex_delta_scan_step_multiplier=_required_bounded_number(
            row,
            field="max_abs_apex_delta_scan_step_multiplier",
            side=side,
            exclusive_minimum=0.0,
        ),
        min_pearson_r_to_base=_required_bounded_number(
            row,
            field="min_pearson_r_to_base_threshold",
            side=side,
            minimum=0.0,
            maximum=1.0,
        ),
        min_active_overlap_fraction=_required_bounded_number(
            row,
            field="min_active_overlap_fraction_threshold",
            side=side,
            minimum=0.0,
            maximum=1.0,
        ),
        min_shifted_pearson_r_to_base=_required_bounded_number(
            row,
            field="min_shifted_pearson_r_to_base_threshold",
            side=side,
            minimum=0.0,
            maximum=1.0,
        ),
        max_abs_shape_shift_sec_to_base=_required_bounded_number(
            row,
            field="max_abs_shape_shift_sec_to_base_threshold",
            side=side,
            exclusive_minimum=0.0,
        ),
    )


def _validate_not_evaluated_evidence(
    row: Mapping[str, object],
    *,
    side: str,
) -> None:
    reason = _required_text(row, field="trace_rule_reason", side=side)
    if reason not in _NOT_EVALUATED_REASONS:
        raise ValueError(
            f"{side} trace_rule_reason must be one of "
            f"{sorted(_NOT_EVALUATED_REASONS)!r} when trace is not evaluated"
        )
    for field in (
        "min_median_rt_step_sec",
        "effective_max_abs_apex_delta_threshold_sec",
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
    ):
        _require_blank(row, field=field, side=side)


def _validate_evaluated_evidence(
    row: Mapping[str, object],
    *,
    side: str,
    thresholds: PtPatternEvidenceThresholds,
) -> None:
    combined_status = _required_text(
        row,
        field="combined_pt_pattern_status",
        side=side,
    )
    if combined_status not in _VALID_COMBINED_STATUSES:
        raise ValueError(
            f"{side} combined_pt_pattern_status must be one of "
            f"{sorted(_VALID_COMBINED_STATUSES)!r}"
        )
    _optional_integer_number(
        row,
        field="non_base_positions_supported",
        side=side,
        minimum=0,
        maximum=MAX_NON_BASE_POSITIONS,
    )
    _optional_bounded_number(
        row,
        field="max_abs_apex_delta_sec",
        side=side,
        minimum=0.0,
    )
    _optional_bounded_number(
        row,
        field="min_pearson_r_to_base",
        side=side,
        minimum=-1.0,
        maximum=1.0,
    )
    _optional_bounded_number(
        row,
        field="min_active_overlap_fraction",
        side=side,
        minimum=0.0,
        maximum=1.0,
    )
    _optional_bounded_number(
        row,
        field="min_shifted_pearson_r_to_base",
        side=side,
        minimum=-1.0,
        maximum=1.0,
    )
    _optional_bounded_number(
        row,
        field="max_abs_shape_shift_sec_to_base",
        side=side,
        minimum=0.0,
    )
    _optional_bounded_number(
        row,
        field="min_median_rt_step_sec",
        side=side,
        exclusive_minimum=0.0,
    )
    observed_effective_threshold = _required_bounded_number(
        row,
        field="effective_max_abs_apex_delta_threshold_sec",
        side=side,
        exclusive_minimum=0.0,
    )
    expected_effective_threshold = effective_max_abs_apex_delta_sec(
        row,
        thresholds=thresholds,
    )
    if not math.isclose(
        observed_effective_threshold,
        expected_effective_threshold,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise ValueError(
            f"{side} effective_max_abs_apex_delta_threshold_sec must match "
            "the persisted configured threshold and scan interval"
        )
    _validate_trace_ambiguity(row, side=side)
    decision = evaluate_pt_pattern_evidence(row, thresholds=thresholds)
    observed = (
        _required_text(row, field="trace_rule_call", side=side),
        _required_text(row, field="trace_detection_status", side=side),
        _required_text(row, field="trace_rule_reason", side=side),
    )
    expected = (decision.call, decision.detection_status, decision.reason)
    if observed != expected:
        raise ValueError(
            f"{side} trace decision fields do not match rule replay: "
            f"observed={observed!r}, expected={expected!r}"
        )


def _validate_trace_ambiguity(
    row: Mapping[str, object],
    *,
    side: str,
) -> None:
    status = _required_text(row, field="trace_ambiguity_status", side=side)
    if status not in {"clear", "review_required"}:
        raise ValueError(
            f"{side} trace_ambiguity_status must be 'clear' or 'review_required'"
        )
    flags_text = _required_text(row, field="trace_ambiguity_flags", side=side)
    shifted_labels_text = _required_text(
        row,
        field="shifted_only_position_labels",
        side=side,
    )
    flags = [] if flags_text == "none" else flags_text.split(";")
    if flags and flags_text != ";".join(sorted(set(flags))):
        raise ValueError(f"{side} trace_ambiguity_flags must be unique and sorted")
    position_flag_values: list[str] = []
    for flag in flags:
        if flag.startswith("shifted_only_positions="):
            position_flag_values.append(flag.removeprefix("shifted_only_positions="))
        elif flag not in _AMBIGUITY_FLAG_TOKENS:
            raise ValueError(
                f"{side} trace_ambiguity_flags contains unsupported token {flag!r}"
            )
    if shifted_labels_text == "none":
        shifted_labels: list[str] = []
    else:
        shifted_labels = shifted_labels_text.split(",")
        if shifted_labels_text != ",".join(sorted(set(shifted_labels))):
            raise ValueError(
                f"{side} shifted_only_position_labels must be unique and sorted"
            )
        if not set(shifted_labels).issubset(_NON_BASE_POSITION_LABELS):
            raise ValueError(
                f"{side} shifted_only_position_labels contains invalid position"
            )
    expected_status = "review_required" if flags else "clear"
    if status != expected_status:
        raise ValueError(
            f"{side} trace_ambiguity_status does not match trace_ambiguity_flags"
        )
    if shifted_labels:
        expected_position_flag = ",".join(shifted_labels)
        if position_flag_values != [expected_position_flag] or (
            "shifted_only_support_present" not in flags
        ):
            raise ValueError(f"{side} shifted-only labels must match ambiguity flags")
    elif position_flag_values or "shifted_only_support_present" in flags:
        raise ValueError(f"{side} shifted-only ambiguity flags require position labels")


_EvidenceKey = TypeVar("_EvidenceKey", bound=Hashable)


def validate_pt_candidate_evidence_envelope(
    candidate_by_key: Mapping[_EvidenceKey, Mapping[str, object]],
    evidence_by_key: Mapping[_EvidenceKey, Mapping[str, object]],
    *,
    evidence_rows: Sequence[Mapping[str, object]],
) -> None:
    """Validate one complete, versioned evidence row per candidate."""

    for row_index, row in enumerate(evidence_rows, start=2):
        validate_pt_pattern_evidence_row_contract(
            row,
            side=f"evidence row {row_index}",
        )
    remaining_rows = [dict(row) for row in evidence_rows]
    for mapped_row in evidence_by_key.values():
        mapped_value = dict(mapped_row)
        match_index = next(
            (
                index
                for index, raw_row in enumerate(remaining_rows)
                if raw_row == mapped_value
            ),
            None,
        )
        if match_index is None:
            raise ValueError(
                "evidence_by_key must be built from evidence_rows without "
                "replacement or collapse." + _EVIDENCE_REGENERATION_HINT
            )
        remaining_rows.pop(match_index)
    if remaining_rows:
        raise ValueError(
            "evidence_by_key must be built from evidence_rows without "
            "replacement or collapse." + _EVIDENCE_REGENERATION_HINT
        )

    candidate_keys = set(candidate_by_key)
    evidence_keys = set(evidence_by_key)
    if candidate_keys != evidence_keys:
        missing = sorted(
            (_display_key(key) for key in candidate_keys - evidence_keys),
        )
        extra = sorted(
            (_display_key(key) for key in evidence_keys - candidate_keys),
        )
        details = []
        if missing:
            details.append("missing evidence keys: " + ", ".join(missing))
        if extra:
            details.append("extra evidence keys: " + ", ".join(extra))
        raise ValueError(
            "candidate/evidence join must be exactly one-to-one; "
            + "; ".join(details)
            + "."
            + _EVIDENCE_REGENERATION_HINT
        )

    for key in candidate_by_key:
        validate_pt_candidate_evidence_identity(
            candidate_by_key[key],
            evidence_by_key[key],
        )


def _display_key(value: Hashable) -> str:
    if isinstance(value, tuple):
        return "/".join(str(part) for part in value)
    return str(value)


def _validate_schema_version(
    row: Mapping[str, object],
    *,
    expected: str,
    side: str,
    regeneration_hint: str,
) -> None:
    observed = _required_text(row, field="schema_version", side=side)
    if observed != expected:
        raise ValueError(
            f"unsupported {side} schema_version {observed!r}; expected "
            f"{expected!r}." + regeneration_hint
        )


def _validate_candidate_assignment_fingerprint(
    candidate_row: Mapping[str, object],
    artifact_row: Mapping[str, object],
    *,
    artifact_side: str,
    regeneration_hint: str,
) -> None:
    expected = pt_pattern_candidate_assignment_fingerprint(candidate_row)
    observed = _required_sha256(
        artifact_row,
        field=PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
        side=artifact_side,
    )
    if observed != expected:
        candidate_id = _required_text(
            candidate_row,
            field="candidate_id",
            side="candidate",
        )
        raise ValueError(
            f"{PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD} does not match "
            f"between candidate and {artifact_side} for candidate_id "
            f"{candidate_id!r}: {expected!r} != {observed!r}." + regeneration_hint
        )


def _validate_pt_artifact_identity(
    candidate_row: Mapping[str, object],
    artifact_row: Mapping[str, object],
    *,
    artifact_side: str,
    text_fields: tuple[str, ...],
    regeneration_hint: str,
) -> None:
    """Compare immutable candidate identity across one diagnostic handoff."""

    candidate_text = {
        field: _required_text(candidate_row, field=field, side="candidate")
        for field in text_fields
    }
    artifact_text = {
        field: _required_text(artifact_row, field=field, side=artifact_side)
        for field in text_fields
    }
    for field in text_fields:
        if candidate_text[field] != artifact_text[field]:
            raise ValueError(
                f"{field} does not match between candidate and {artifact_side}: "
                f"{candidate_text[field]!r} != {artifact_text[field]!r}."
                + regeneration_hint
            )

    candidate_numeric = {
        field: _required_finite_number(candidate_row, field=field, side="candidate")
        for field in _NUMERIC_IDENTITY_FIELDS
    }
    artifact_numeric = {
        field: _required_finite_number(
            artifact_row,
            field=field,
            side=artifact_side,
        )
        for field in _NUMERIC_IDENTITY_FIELDS
    }
    for field in _NUMERIC_IDENTITY_FIELDS:
        if candidate_numeric[field] != artifact_numeric[field]:
            raise ValueError(
                f"{field} does not match between candidate and {artifact_side} for "
                f"candidate_id {candidate_text['candidate_id']!r}: "
                f"{candidate_numeric[field]!r} != {artifact_numeric[field]!r}."
                + regeneration_hint
            )


def _required_text(
    row: Mapping[str, object],
    *,
    field: str,
    side: str,
) -> str:
    raw_value = row.get(field)
    if raw_value is None or isinstance(raw_value, bool):
        raise ValueError(f"{side} {field} must be non-empty")
    value = str(raw_value).strip()
    if not value:
        raise ValueError(f"{side} {field} must be non-empty")
    return value


def _required_finite_number(
    row: Mapping[str, object],
    *,
    field: str,
    side: str,
) -> float:
    value = optional_float(row.get(field))
    if value is None:
        raise ValueError(f"{side} {field} must be a finite number")
    return value


def _required_bounded_number(
    row: Mapping[str, object],
    *,
    field: str,
    side: str,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float:
    value = _required_finite_number(row, field=field, side=side)
    _validate_number_bounds(
        value,
        field=field,
        side=side,
        minimum=minimum,
        maximum=maximum,
        exclusive_minimum=exclusive_minimum,
    )
    return value


def _optional_bounded_number(
    row: Mapping[str, object],
    *,
    field: str,
    side: str,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float | None:
    raw_value = row.get(field)
    if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
        return None
    value = optional_float(raw_value)
    if value is None:
        raise ValueError(f"{side} {field} must be a finite number or blank")
    _validate_number_bounds(
        value,
        field=field,
        side=side,
        minimum=minimum,
        maximum=maximum,
        exclusive_minimum=exclusive_minimum,
    )
    return value


def _required_integer_number(
    row: Mapping[str, object],
    *,
    field: str,
    side: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = _required_finite_number(row, field=field, side=side)
    if not value.is_integer():
        raise ValueError(f"{side} {field} must be an integer")
    parsed = int(value)
    _validate_number_bounds(
        float(parsed),
        field=field,
        side=side,
        minimum=float(minimum) if minimum is not None else None,
        maximum=float(maximum) if maximum is not None else None,
    )
    return parsed


def _optional_integer_number(
    row: Mapping[str, object],
    *,
    field: str,
    side: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    value = _optional_bounded_number(
        row,
        field=field,
        side=side,
    )
    if value is None:
        return None
    if not value.is_integer():
        raise ValueError(f"{side} {field} must be an integer or blank")
    parsed = int(value)
    _validate_number_bounds(
        float(parsed),
        field=field,
        side=side,
        minimum=float(minimum) if minimum is not None else None,
        maximum=float(maximum) if maximum is not None else None,
    )
    return parsed


def _validate_number_bounds(
    value: float,
    *,
    field: str,
    side: str,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: float | None = None,
) -> None:
    if minimum is not None and value < minimum:
        raise ValueError(f"{side} {field} must be >= {minimum:g}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{side} {field} must be <= {maximum:g}")
    if exclusive_minimum is not None and value <= exclusive_minimum:
        raise ValueError(f"{side} {field} must be > {exclusive_minimum:g}")


def _require_blank(
    row: Mapping[str, object],
    *,
    field: str,
    side: str,
) -> None:
    value = row.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        return
    raise ValueError(f"{side} {field} must be blank when trace is not evaluated")


def _required_sha256(
    row: Mapping[str, object],
    *,
    field: str,
    side: str,
) -> str:
    value = _required_text(row, field=field, side=side)
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{side} {field} must be a lowercase SHA-256 digest")
    return value


def _required_false_authority(
    row: Mapping[str, object],
    *,
    field: str,
    side: str,
) -> None:
    value = row.get(field)
    if value is False:
        return
    if isinstance(value, str) and value.strip() == "FALSE":
        return
    raise ValueError(f"{side} {field} must be FALSE")
