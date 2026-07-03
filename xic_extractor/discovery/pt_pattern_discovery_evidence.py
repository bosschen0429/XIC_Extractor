"""Discovery-facing Pt pattern evidence rows.

This module bridges feature-level Pt candidates and optional trace-summary
evidence. It does not read RAW files or grant matrix/export authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.discovery.delta_mass_patterns import PtPatternCandidate
from xic_extractor.discovery.pt_pattern_evidence_rule import (
    DEFAULT_THRESHOLDS,
    PtPatternEvidenceThresholds,
    evaluate_pt_pattern_evidence,
    pt_pattern_rule_id,
)
from xic_extractor.tabular_io import read_delimited_rows

SCHEMA_VERSION = "pt_pattern_discovery_evidence.v1"
EVIDENCE_RULE_OWNER = "xic_extractor.discovery.pt_pattern_evidence_rule"
TRACE_EVIDENCE_NOT_EVALUATED = "not_evaluated"
TRACE_EVIDENCE_EVALUATED = "evaluated"
NO_WRITE_AUTHORITY = False
TRACE_SUMMARY_EVIDENCE_REQUIRED_COLUMNS = (
    "candidate_id",
    "combined_pt_pattern_status",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
)


def load_pt_trace_summary_rows(path: Path) -> tuple[dict[str, str], ...]:
    """Load trace diagnosis summary rows for optional Discovery evidence join."""

    return tuple(
        read_delimited_rows(
            path,
            required_columns=TRACE_SUMMARY_EVIDENCE_REQUIRED_COLUMNS,
            delimiter=",",
            encoding="utf-8-sig",
        )
    )


def build_pt_pattern_evidence_rows(
    candidates: Sequence[PtPatternCandidate],
    *,
    trace_summary_rows: Sequence[Mapping[str, object]] | None = None,
    thresholds: PtPatternEvidenceThresholds = DEFAULT_THRESHOLDS,
) -> tuple[dict[str, object], ...]:
    """Build one Discovery evidence row per Pt candidate.

    Feature-level Pt matching creates the seed candidate. Trace-level
    detected/non-detected status is only evaluated when a matching trace
    summary row is supplied.
    """

    trace_by_candidate_id = (
        _trace_rows_by_candidate_id(trace_summary_rows)
        if trace_summary_rows is not None
        else {}
    )
    trace_summary_provided = trace_summary_rows is not None
    return tuple(
        _evidence_row(
            candidate,
            trace_row=trace_by_candidate_id.get(candidate.candidate_id),
            trace_summary_provided=trace_summary_provided,
            thresholds=thresholds,
        )
        for candidate in candidates
    )


def _trace_rows_by_candidate_id(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    by_id: dict[str, Mapping[str, object]] = {}
    for row in rows:
        candidate_id = str(row.get("candidate_id", "")).strip()
        if not candidate_id:
            raise ValueError("trace summary row is missing candidate_id")
        if candidate_id in by_id:
            raise ValueError(f"duplicate trace summary candidate_id: {candidate_id}")
        by_id[candidate_id] = row
    return by_id


def _evidence_row(
    candidate: PtPatternCandidate,
    *,
    trace_row: Mapping[str, object] | None,
    trace_summary_provided: bool,
    thresholds: PtPatternEvidenceThresholds,
) -> dict[str, object]:
    row = _base_evidence_row(candidate, thresholds=thresholds)
    if trace_row is None:
        row.update(
            {
                "trace_evidence_status": TRACE_EVIDENCE_NOT_EVALUATED,
                "trace_rule_call": TRACE_EVIDENCE_NOT_EVALUATED,
                "trace_detection_status": TRACE_EVIDENCE_NOT_EVALUATED,
                "trace_rule_reason": (
                    "trace_summary_missing_for_candidate"
                    if trace_summary_provided
                    else "trace_summary_not_provided"
                ),
            }
        )
        return row

    decision = evaluate_pt_pattern_evidence(trace_row, thresholds=thresholds)
    row.update(
        {
            "scan_pattern_seed_status": _trace_value(
                trace_row, "scan_pattern_seed_status", default="full_pattern_seeded"
            ),
            "trace_evidence_status": TRACE_EVIDENCE_EVALUATED,
            "trace_rule_call": decision.call,
            "trace_detection_status": decision.detection_status,
            "trace_rule_reason": decision.reason,
            "combined_pt_pattern_status": _trace_value(
                trace_row, "combined_pt_pattern_status"
            ),
            "non_base_positions_supported": _trace_value(
                trace_row, "non_base_positions_supported"
            ),
            "max_abs_apex_delta_sec": _trace_value(
                trace_row, "max_abs_apex_delta_sec"
            ),
            "min_pearson_r_to_base": _trace_value(
                trace_row, "min_pearson_r_to_base"
            ),
            "min_active_overlap_fraction": _trace_value(
                trace_row, "min_active_overlap_fraction"
            ),
            "min_shifted_pearson_r_to_base": _trace_value(
                trace_row, "min_shifted_pearson_r_to_base"
            ),
            "max_abs_shape_shift_sec_to_base": _trace_value(
                trace_row, "max_abs_shape_shift_sec_to_base"
            ),
            "trace_ambiguity_status": _trace_value(
                trace_row, "trace_ambiguity_status"
            ),
            "trace_ambiguity_flags": _trace_value(
                trace_row, "trace_ambiguity_flags"
            ),
            "shifted_only_position_labels": _trace_value(
                trace_row, "shifted_only_position_labels"
            ),
        }
    )
    return row


def _base_evidence_row(
    candidate: PtPatternCandidate,
    *,
    thresholds: PtPatternEvidenceThresholds,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "sample_stem": candidate.sample_stem,
        "method_label": candidate.method_label,
        "pattern_id": candidate.pattern_id,
        "candidate_id": candidate.candidate_id,
        "candidate_state": candidate.candidate_state,
        "scan_pattern_seed_status": "full_pattern_seeded",
        "trace_evidence_status": "",
        "trace_rule_call": "",
        "trace_detection_status": "",
        "trace_rule_reason": "",
        "evidence_rule_id": pt_pattern_rule_id(thresholds),
        "evidence_rule_owner": EVIDENCE_RULE_OWNER,
        "required_combined_status": thresholds.required_combined_status,
        "min_non_base_positions_supported": (
            thresholds.min_non_base_positions_supported
        ),
        "max_abs_apex_delta_threshold_sec": thresholds.max_abs_apex_delta_sec,
        "min_pearson_r_to_base_threshold": thresholds.min_pearson_r_to_base,
        "min_active_overlap_fraction_threshold": (
            thresholds.min_active_overlap_fraction
        ),
        "min_shifted_pearson_r_to_base_threshold": (
            thresholds.min_shifted_pearson_r_to_base
        ),
        "max_abs_shape_shift_sec_to_base_threshold": (
            thresholds.max_abs_shape_shift_sec_to_base
        ),
        "combined_pt_pattern_status": "",
        "non_base_positions_supported": "",
        "max_abs_apex_delta_sec": "",
        "min_pearson_r_to_base": "",
        "min_active_overlap_fraction": "",
        "min_shifted_pearson_r_to_base": "",
        "max_abs_shape_shift_sec_to_base": "",
        "trace_ambiguity_status": "",
        "trace_ambiguity_flags": "",
        "shifted_only_position_labels": "",
        "matrix_write_authority": NO_WRITE_AUTHORITY,
        "counted_detection_authority": NO_WRITE_AUTHORITY,
        "export_activation_authority": NO_WRITE_AUTHORITY,
    }


def _trace_value(
    row: Mapping[str, object],
    field: str,
    *,
    default: object = "",
) -> object:
    value = row.get(field, default)
    if value is None:
        return ""
    return value
