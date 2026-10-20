"""Shared valid Pt Discovery evidence rows for boundary tests."""

from __future__ import annotations

from collections.abc import Mapping

from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_EVIDENCE_SCHEMA_VERSION,
    PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
    PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION,
    pt_pattern_candidate_assignment_fingerprint,
)
from xic_extractor.discovery.pt_pattern_evidence_rule import (
    DEFAULT_THRESHOLDS,
    EVIDENCE_RULE_OWNER,
    pt_pattern_rule_id,
)


def pt_evidence_row(
    candidate: Mapping[str, object],
    *,
    trace_rule_call: str = "accept",
) -> dict[str, object]:
    """Return one internally coherent evidence row for ``candidate``."""

    if trace_rule_call not in {"accept", "reject", "not_evaluated"}:
        raise ValueError(f"unsupported fixture trace_rule_call {trace_rule_call!r}")
    evaluated = trace_rule_call != "not_evaluated"
    accepted = trace_rule_call == "accept"
    row: dict[str, object] = {
        "schema_version": PT_PATTERN_EVIDENCE_SCHEMA_VERSION,
        "sample_stem": candidate["sample_stem"],
        "method_label": candidate["method_label"],
        "pattern_id": candidate["pattern_id"],
        "candidate_id": candidate["candidate_id"],
        "candidate_assignment_sha256": (
            pt_pattern_candidate_assignment_fingerprint(candidate)
        ),
        "trace_schema_version": (
            PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION if evaluated else ""
        ),
        "trace_artifact_role": (
            PT_PATTERN_TRACE_FULL_CONTEXT_ROLE if evaluated else ""
        ),
        "trace_extraction_sha256": "e" * 64 if evaluated else "",
        "trace_manifest_sha256": "f" * 64 if evaluated else "",
        "base_mz": candidate["base_mz"],
        "base_rt_apex_min": candidate["base_rt_apex_min"],
        "candidate_state": candidate.get(
            "candidate_state",
            "pt_pattern_candidate",
        ),
        "scan_pattern_seed_status": "full_pattern_seeded",
        "trace_evidence_status": "evaluated" if evaluated else "not_evaluated",
        "trace_rule_call": trace_rule_call,
        "trace_detection_status": (
            "detected" if accepted else "non_detected" if evaluated else "not_evaluated"
        ),
        "trace_rule_reason": (
            "full_pattern_close_apex_shape_overlap"
            if accepted
            else "combined_status_not_detected"
            if evaluated
            else "trace_summary_not_provided"
        ),
        "evidence_rule_id": pt_pattern_rule_id(DEFAULT_THRESHOLDS),
        "evidence_rule_owner": EVIDENCE_RULE_OWNER,
        "required_combined_status": DEFAULT_THRESHOLDS.required_combined_status,
        "min_non_base_positions_supported": (
            DEFAULT_THRESHOLDS.min_non_base_positions_supported
        ),
        "configured_max_abs_apex_delta_threshold_sec": (
            DEFAULT_THRESHOLDS.max_abs_apex_delta_sec
        ),
        "max_abs_apex_delta_scan_step_multiplier": (
            DEFAULT_THRESHOLDS.max_abs_apex_delta_scan_step_multiplier
        ),
        "min_median_rt_step_sec": "",
        "effective_max_abs_apex_delta_threshold_sec": (
            DEFAULT_THRESHOLDS.max_abs_apex_delta_sec if evaluated else ""
        ),
        "min_pearson_r_to_base_threshold": (DEFAULT_THRESHOLDS.min_pearson_r_to_base),
        "min_active_overlap_fraction_threshold": (
            DEFAULT_THRESHOLDS.min_active_overlap_fraction
        ),
        "min_shifted_pearson_r_to_base_threshold": (
            DEFAULT_THRESHOLDS.min_shifted_pearson_r_to_base
        ),
        "max_abs_shape_shift_sec_to_base_threshold": (
            DEFAULT_THRESHOLDS.max_abs_shape_shift_sec_to_base
        ),
        "combined_pt_pattern_status": (
            "pt_pattern_detected_diagnostic"
            if accepted
            else "pt_pattern_candidate_partial"
            if evaluated
            else ""
        ),
        "non_base_positions_supported": 3 if evaluated else "",
        "max_abs_apex_delta_sec": 0.4 if evaluated else "",
        "min_pearson_r_to_base": 0.95 if evaluated else "",
        "min_active_overlap_fraction": 0.7 if evaluated else "",
        "min_shifted_pearson_r_to_base": "",
        "max_abs_shape_shift_sec_to_base": "",
        "trace_ambiguity_status": "clear" if evaluated else "",
        "trace_ambiguity_flags": "none" if evaluated else "",
        "shifted_only_position_labels": "none" if evaluated else "",
        "matrix_write_authority": "FALSE",
        "counted_detection_authority": "FALSE",
        "export_activation_authority": "FALSE",
    }
    return row
