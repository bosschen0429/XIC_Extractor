"""CSV and JSON IO for Pt pattern Delta Mass Discovery.

Re-exports the Detected Features CSV v1 loader owned by
``xic_extractor.discovery.detected_features`` and implements the Pt Candidate /
Pt Review CSV v1, Pt Discovery Evidence CSV v2, and Pt Sidecar JSON v3 writers
described by the current Discovery contract in ``docs/product/discovery.md``.

Column tuples and field rules are pinned by the approved contract tests in
``tests/test_delta_mass_csv.py``.
"""

from __future__ import annotations

import json
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
from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_EVIDENCE_CONTRACT_COLUMNS,
    format_pt_machine_float,
)
from xic_extractor.discovery.pt_pattern_discovery_evidence import (
    canonical_pt_pattern_evidence_csv_bytes,
)
from xic_extractor.tabular_io import (
    escape_excel_formula,
    write_delimited_rows,
)

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

PT_EVIDENCE_COLUMNS = PT_PATTERN_EVIDENCE_CONTRACT_COLUMNS

_SIDECAR_SCHEMA_VERSION = "pt_pattern_sidecar.v4"

# Formula escaping is a presentation concern, so machine identifiers in the
# candidate/evidence contracts must remain byte-for-byte joinable. The compact
# review CSV is the spreadsheet-facing surface and keeps formula protection for
# its displayed identifiers as well as prose.
_CANDIDATE_ESCAPED_FIELDS = frozenset(
    {
        "reason",
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

    CLI-provided input path fields are recorded as the supplied path strings
    (not resolved local absolute paths), per the Pt Sidecar JSON v3 contract.
    """

    method_label: str
    pattern: DeltaMassPattern
    detected_features_csv: str
    external_feature_table_tsv: str | None
    mzmine_feature_table_csv: str | None
    cid_nl_candidates_csv: str | None
    candidates: Sequence[PtPatternCandidate]
    trace_summary_csv: str | None = None
    trace_metrics_csv: str | None = None
    trace_manifest_json: str | None = None
    trace_summary_csv_sha256: str | None = None
    trace_metrics_csv_sha256: str | None = None
    trace_manifest_json_sha256: str | None = None
    trace_schema_version: str | None = None
    trace_artifact_role: str | None = None
    trace_extraction_sha256: str | None = None
    evidence_rows: Sequence[Mapping[str, object]] = ()
    effective_mz_tolerance_ppm: float | None = None
    effective_rt_tolerance_sec: float | None = None


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
    rows = [
        _format_pt_row(
            _candidate_row(candidate),
            escaped_fields=_CANDIDATE_ESCAPED_FIELDS,
        )
        for candidate in candidates
    ]
    write_delimited_rows(
        path,
        rows,
        PT_CANDIDATE_COLUMNS,
        extrasaction="raise",
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
    rows = [
        _format_pt_row(
            _review_row(candidate),
            escaped_fields=_REVIEW_ESCAPED_FIELDS,
        )
        for candidate in candidates
    ]
    write_delimited_rows(
        path,
        rows,
        PT_REVIEW_COLUMNS,
        extrasaction="raise",
    )
    return path


def write_pt_pattern_evidence_csv(
    path: Path,
    evidence_rows: Sequence[Mapping[str, object]],
) -> Path:
    """Write the complete, fail-closed Pt Discovery Evidence CSV v2 contract."""

    payload_bytes = canonical_pt_pattern_evidence_csv_bytes(evidence_rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload_bytes)
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
        "pattern": _pattern_payload(
            result.pattern,
            effective_mz_tolerance_ppm=result.effective_mz_tolerance_ppm,
            effective_rt_tolerance_sec=result.effective_rt_tolerance_sec,
        ),
        "inputs": {
            "detected_features_csv": result.detected_features_csv,
            "external_feature_table_tsv": result.external_feature_table_tsv,
            "mzmine_feature_table_csv": result.mzmine_feature_table_csv,
            "cid_nl_candidates_csv": result.cid_nl_candidates_csv,
            "trace_summary_csv": result.trace_summary_csv,
            "trace_metrics_csv": result.trace_metrics_csv,
            "trace_manifest_json": result.trace_manifest_json,
            "trace_summary_csv_sha256": result.trace_summary_csv_sha256,
            "trace_metrics_csv_sha256": result.trace_metrics_csv_sha256,
            "trace_manifest_json_sha256": result.trace_manifest_json_sha256,
            "trace_schema_version": result.trace_schema_version,
            "trace_artifact_role": result.trace_artifact_role,
            "trace_extraction_sha256": result.trace_extraction_sha256,
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


def _pattern_payload(
    pattern: DeltaMassPattern,
    *,
    effective_mz_tolerance_ppm: float | None,
    effective_rt_tolerance_sec: float | None,
) -> dict[str, Any]:
    return {
        "pattern_id": pattern.pattern_id,
        "charge": pattern.charge,
        "polarity": pattern.polarity,
        "positions": [
            {"label": position.label, "delta_da": position.delta_da}
            for position in pattern.positions
        ],
        "mz_tolerance_ppm": (
            pattern.mz_tolerance_ppm
            if effective_mz_tolerance_ppm is None
            else effective_mz_tolerance_ppm
        ),
        "rt_tolerance_sec": (
            pattern.rt_tolerance_sec
            if effective_rt_tolerance_sec is None
            else effective_rt_tolerance_sec
        ),
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


def _format_pt_row(
    row: Mapping[str, object],
    *,
    escaped_fields: frozenset[str],
) -> dict[str, str]:
    return {
        column: _format_pt_value(
            value,
            escape_formula=column in escaped_fields,
        )
        for column, value in row.items()
    }


def _format_pt_value(value: object, *, escape_formula: bool) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        # These CSVs are machine handoffs, not display-only reports. Python's
        # repr is the shortest decimal that round-trips to the same binary
        # float, avoiding ppm-scale center drift from the old ``.6g`` format.
        return format_pt_machine_float(value)
    if isinstance(value, int):
        return str(value)
    text = str(value)
    return escape_excel_formula(text) if escape_formula else text
