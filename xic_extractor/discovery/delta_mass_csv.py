"""CSV and JSON IO for Pt pattern Delta Mass Discovery.

Re-exports the Detected Features CSV v1 loader owned by
``xic_extractor.discovery.detected_features`` and implements the Pt Candidate /
Pt Review CSV v1, Pt Discovery Evidence CSV v1, and Pt Sidecar JSON v3 writers
described in the Public Contract Details section of
``docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md``.

Column tuples and field rules are copied verbatim from that plan and are
pinned by the approved contract tests in ``tests/test_delta_mass_csv.py``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from xic_extractor.discovery.delta_mass_patterns import (
    DeltaMassPattern,
    PtPatternCandidate,
)
from xic_extractor.discovery.detected_features import (
    DETECTED_FEATURES_COLUMNS as _DETECTED_FEATURES_COLUMNS,
)
from xic_extractor.discovery.detected_features import (
    load_detected_features_csv as _load_detected_features_csv,
)
from xic_extractor.tabular_io import escape_excel_formula, write_delimited_rows

DETECTED_FEATURES_COLUMNS = _DETECTED_FEATURES_COLUMNS
load_detected_features_csv = _load_detected_features_csv

PT_CANDIDATE_COLUMNS = (
    "schema_version",
    "sample_stem",
    "method_label",
    "pattern_id",
    "candidate_id",
    "candidate_state",
    "review_priority",
    "evidence_tier",
    "evidence_score",
    "base_feature_id",
    "base_mz",
    "base_rt_apex_min",
    "base_height",
    "base_area",
    "position_count",
    "required_position_count",
    "rt_span_sec",
    "pattern_rt_status",
    "local_signal_support_status",
    "cross_sample_support_status",
    "ratio_basis",
    "cid_nl_base_status",
    "cid_nl_base_candidate_id",
    "cid_nl_base_join_mode",
    "reason",
    "pt_plus_1_feature_id",
    "pt_plus_1_mz",
    "pt_plus_1_expected_mz",
    "pt_plus_1_mz_error_ppm",
    "pt_plus_1_delta_error_da",
    "pt_plus_1_rt_delta_sec",
    "pt_plus_1_height_ratio_to_base",
    "pt_plus_1_area_ratio_to_base",
    "pt_plus_2_feature_id",
    "pt_plus_2_mz",
    "pt_plus_2_expected_mz",
    "pt_plus_2_mz_error_ppm",
    "pt_plus_2_delta_error_da",
    "pt_plus_2_rt_delta_sec",
    "pt_plus_2_height_ratio_to_base",
    "pt_plus_2_area_ratio_to_base",
    "pt_plus_4_feature_id",
    "pt_plus_4_mz",
    "pt_plus_4_expected_mz",
    "pt_plus_4_mz_error_ppm",
    "pt_plus_4_delta_error_da",
    "pt_plus_4_rt_delta_sec",
    "pt_plus_4_height_ratio_to_base",
    "pt_plus_4_area_ratio_to_base",
)

PT_REVIEW_COLUMNS = (
    "review_priority",
    "evidence_tier",
    "evidence_score",
    "sample_stem",
    "candidate_id",
    "method_label",
    "base_mz",
    "base_rt_apex_min",
    "rt_span_sec",
    "position_count",
    "cid_nl_base_status",
    "cid_nl_base_join_mode",
    "reason",
    "review_note",
)

PT_EVIDENCE_COLUMNS = (
    "schema_version",
    "sample_stem",
    "method_label",
    "pattern_id",
    "candidate_id",
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
    "max_abs_apex_delta_threshold_sec",
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
    "matrix_write_authority",
    "counted_detection_authority",
    "export_activation_authority",
)

_SIDECAR_SCHEMA_VERSION = "pt_pattern_sidecar.v3"

# Free-text / sample-derived fields that can plausibly start with a
# formula-trigger character ("=", "+", "-", "@") and therefore need Excel
# formula escaping when written to CSV. Controlled enums, schema/pattern
# identifiers, and numeric fields are excluded because they cannot start
# with those characters by construction.
_CANDIDATE_ESCAPED_FIELDS = frozenset(
    {
        "sample_stem",
        "candidate_id",
        "base_feature_id",
        "cid_nl_base_candidate_id",
        "reason",
        "pt_plus_1_feature_id",
        "pt_plus_2_feature_id",
        "pt_plus_4_feature_id",
    }
)

_REVIEW_ESCAPED_FIELDS = frozenset(
    {
        "sample_stem",
        "candidate_id",
        "reason",
        "review_note",
    }
)


@dataclass(frozen=True)
class DeltaMassDiscoveryResult:
    """A completed Pt pattern Discovery run, ready for sidecar JSON output.

    ``detected_features_csv``, ``cid_nl_candidates_csv``, and
    ``trace_summary_csv`` are recorded as the literal CLI-provided path
    strings (not resolved local absolute paths), per the Pt Sidecar JSON v3
    contract.
    """

    method_label: str
    pattern: DeltaMassPattern
    detected_features_csv: str
    external_feature_table_tsv: str | None
    mzmine_feature_table_csv: str | None
    cid_nl_candidates_csv: str | None
    candidates: Sequence[PtPatternCandidate]
    trace_summary_csv: str | None = None
    evidence_rows: Sequence[Mapping[str, object]] = ()


def write_pt_pattern_candidates_csv(
    path: Path,
    candidates: Sequence[PtPatternCandidate],
) -> Path:
    """Write the Pt Candidate CSV v1 contract.

    Writes a header-only file when ``candidates`` is empty. Candidate order
    is preserved as given (the matcher / pipeline is responsible for final
    ordering; this writer does not re-sort).
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [_candidate_row(candidate) for candidate in candidates]
    write_delimited_rows(
        path,
        rows,
        PT_CANDIDATE_COLUMNS,
        extrasaction="raise",
        formatter=_format_pt_value,
    )
    return path


def write_pt_pattern_review_csv(
    path: Path,
    candidates: Sequence[PtPatternCandidate],
) -> Path:
    """Write the Pt Review CSV v1 contract.

    Uses the same candidate ordering as ``write_pt_pattern_candidates_csv``
    (this writer does not re-sort either), so callers that pass the same
    sequence to both writers get matching row order between the two files.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [_review_row(candidate) for candidate in candidates]
    write_delimited_rows(
        path,
        rows,
        PT_REVIEW_COLUMNS,
        extrasaction="raise",
        formatter=_format_pt_value,
    )
    return path


def write_pt_pattern_evidence_csv(
    path: Path,
    evidence_rows: Sequence[Mapping[str, object]],
) -> Path:
    """Write the Pt Discovery Evidence CSV v1 contract."""

    path.parent.mkdir(parents=True, exist_ok=True)
    write_delimited_rows(
        path,
        evidence_rows,
        PT_EVIDENCE_COLUMNS,
        extrasaction="raise",
        formatter=_format_pt_value,
    )
    return path


def write_pt_pattern_sidecar_json(
    path: Path,
    *,
    result: DeltaMassDiscoveryResult,
) -> Path:
    """Write the Pt Sidecar JSON v3 contract."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": _SIDECAR_SCHEMA_VERSION,
        "method_label": result.method_label,
        "pattern": _pattern_payload(result.pattern),
        "inputs": {
            "detected_features_csv": result.detected_features_csv,
            "external_feature_table_tsv": result.external_feature_table_tsv,
            "mzmine_feature_table_csv": result.mzmine_feature_table_csv,
            "cid_nl_candidates_csv": result.cid_nl_candidates_csv,
            "trace_summary_csv": result.trace_summary_csv,
        },
        "summary": _summary_payload(
            result.candidates,
            evidence_rows=result.evidence_rows,
        ),
        "candidates": [asdict(candidate) for candidate in result.candidates],
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return path


def _pattern_payload(pattern: DeltaMassPattern) -> dict[str, Any]:
    return {
        "pattern_id": pattern.pattern_id,
        "charge": pattern.charge,
        "polarity": pattern.polarity,
        "positions": [
            {"label": position.label, "delta_da": position.delta_da}
            for position in pattern.positions
        ],
        "mz_tolerance_ppm": pattern.mz_tolerance_ppm,
        "rt_tolerance_sec": pattern.rt_tolerance_sec,
    }


def _summary_payload(
    candidates: Sequence[PtPatternCandidate],
    *,
    evidence_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    cid_nl_supported_count = sum(
        1
        for candidate in candidates
        if candidate.candidate_state == "pt_pattern_cid_nl_supported"
        and candidate.cid_nl_base_status == "matched"
    )
    return {
        "candidate_count": len(candidates),
        "pt_only_candidate_count": len(candidates) - cid_nl_supported_count,
        "cid_nl_supported_candidate_count": cid_nl_supported_count,
        "evidence_row_count": len(evidence_rows),
        "trace_rule_call_counts": _count_by_text_field(
            evidence_rows, "trace_rule_call"
        ),
        "trace_evidence_status_counts": _count_by_text_field(
            evidence_rows, "trace_evidence_status"
        ),
        "trace_detection_status_counts": _count_by_text_field(
            evidence_rows, "trace_detection_status"
        ),
        "trace_rule_reason_counts": _count_by_text_field(
            evidence_rows, "trace_rule_reason"
        ),
    }


def _count_by_text_field(
    rows: Sequence[Mapping[str, object]], field: str
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(field, "")).strip()
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _candidate_row(candidate: PtPatternCandidate) -> dict[str, object]:
    row: dict[str, object] = {
        column: getattr(candidate, column) for column in PT_CANDIDATE_COLUMNS
    }
    return row


def _review_row(candidate: PtPatternCandidate) -> dict[str, object]:
    return {
        "review_priority": candidate.review_priority,
        "evidence_tier": candidate.evidence_tier,
        "evidence_score": candidate.evidence_score,
        "sample_stem": candidate.sample_stem,
        "candidate_id": candidate.candidate_id,
        "method_label": candidate.method_label,
        "base_mz": candidate.base_mz,
        "base_rt_apex_min": candidate.base_rt_apex_min,
        "rt_span_sec": candidate.rt_span_sec,
        "position_count": candidate.position_count,
        "cid_nl_base_status": candidate.cid_nl_base_status,
        "cid_nl_base_join_mode": candidate.cid_nl_base_join_mode,
        "reason": candidate.reason,
        "review_note": _build_review_note(candidate),
    }


def _build_review_note(candidate: PtPatternCandidate) -> str:
    support_text = (
        "feature-source support only"
        if candidate.local_signal_support_status == "feature_source_detected"
        else f"{candidate.local_signal_support_status} local signal"
    )
    return (
        f"{candidate.position_count}/{candidate.required_position_count} positions; "
        f"{support_text}; "
        f"{candidate.pattern_rt_status} RT"
    )


def _format_pt_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return f"{value:.6g}"
    if isinstance(value, int):
        return str(value)
    return escape_excel_formula(str(value))
