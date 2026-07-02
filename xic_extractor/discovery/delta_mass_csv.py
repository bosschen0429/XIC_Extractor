"""CSV and JSON IO for Pt pattern Delta Mass Discovery.

Implements the Detected Features CSV v1 loader and the Pt Candidate / Pt
Review CSV v1, Pt Discovery Evidence CSV v1, and Pt Sidecar JSON v2 writers
described in the Public Contract
Details section of
``docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md``.

Column tuples and field rules are copied verbatim from that plan and are
pinned by the approved contract tests in ``tests/test_delta_mass_csv.py``.
"""

from __future__ import annotations

import csv
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
from xic_extractor.discovery.detected_features import DetectedFeature
from xic_extractor.tabular_io import escape_excel_formula, write_delimited_rows

DETECTED_FEATURES_COLUMNS = (
    "sample_stem",
    "feature_id",
    "source_feature_row_id",
    "mz",
    "rt_apex_min",
    "rt_start_min",
    "rt_end_min",
    "height",
    "area",
    "trace_quality",
    "shape_score",
    "source",
)

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
    "combined_pt_pattern_status",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "matrix_write_authority",
    "counted_detection_authority",
    "export_activation_authority",
)

_SIDECAR_SCHEMA_VERSION = "pt_pattern_sidecar.v2"

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
    strings (not resolved local absolute paths), per the Pt Sidecar JSON v2
    contract.
    """

    method_label: str
    pattern: DeltaMassPattern
    detected_features_csv: str
    cid_nl_candidates_csv: str | None
    candidates: Sequence[PtPatternCandidate]
    trace_summary_csv: str | None = None
    evidence_rows: Sequence[Mapping[str, object]] = ()


def load_detected_features_csv(path: Path) -> tuple[DetectedFeature, ...]:
    """Load and validate the Detected Features CSV v1 input contract.

    Required non-empty fields: ``sample_stem``, ``feature_id``, ``mz``,
    ``rt_apex_min``, ``height``, ``trace_quality``, ``source``. ``height``
    must additionally be ``> 0``. ``mz``, ``rt_apex_min``, ``height``, and
    any non-blank optional numeric field must be finite. Rows with invalid
    required fields raise ``ValueError`` rather than being silently skipped.
    """

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    features: list[DetectedFeature] = []
    for row_index, row in enumerate(rows):
        features.append(_parse_detected_feature(row, path=path, row_index=row_index))
    return tuple(features)


def _parse_detected_feature(
    row: dict[str, str], *, path: Path, row_index: int
) -> DetectedFeature:
    def _row_label() -> str:
        return f"{path} row {row_index}"

    sample_stem = _required_text(row.get("sample_stem"), "sample_stem", _row_label())
    feature_id = _required_text(row.get("feature_id"), "feature_id", _row_label())
    trace_quality = _required_text(
        row.get("trace_quality"), "trace_quality", _row_label()
    )
    source = _required_text(row.get("source"), "source", _row_label())

    mz = _required_finite_float(row.get("mz"), "mz", _row_label())
    rt_apex_min = _required_finite_float(
        row.get("rt_apex_min"), "rt_apex_min", _row_label()
    )
    height = _required_finite_float(row.get("height"), "height", _row_label())
    if height <= 0:
        raise ValueError(
            f"{_row_label()} has invalid height: must be > 0, got {height!r}"
        )

    return DetectedFeature(
        sample_stem=sample_stem,
        feature_id=feature_id,
        source_feature_row_id=_optional_text(row.get("source_feature_row_id")),
        mz=mz,
        rt_apex_min=rt_apex_min,
        rt_start_min=_optional_finite_float(
            row.get("rt_start_min"), "rt_start_min", _row_label()
        ),
        rt_end_min=_optional_finite_float(
            row.get("rt_end_min"), "rt_end_min", _row_label()
        ),
        height=height,
        area=_optional_finite_float(row.get("area"), "area", _row_label()),
        trace_quality=trace_quality,
        shape_score=_optional_finite_float(
            row.get("shape_score"), "shape_score", _row_label()
        ),
        source=source,
    )


def _required_text(value: str | None, field: str, row_label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{row_label} is missing required field {field!r}")
    return text


def _optional_text(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _required_finite_float(value: str | None, field: str, row_label: str) -> float:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{row_label} is missing required field {field!r}")
    try:
        parsed = float(text)
    except ValueError as error:
        raise ValueError(
            f"{row_label} has non-numeric required field {field!r}: {value!r}"
        ) from error
    if not math.isfinite(parsed):
        raise ValueError(
            f"{row_label} has non-finite required field {field!r}: {value!r}"
        )
    return parsed


def _optional_finite_float(
    value: str | None, field: str, row_label: str
) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError as error:
        raise ValueError(
            f"{row_label} has non-numeric optional field {field!r}: {value!r}"
        ) from error
    if not math.isfinite(parsed):
        raise ValueError(
            f"{row_label} has non-finite optional field {field!r}: {value!r}"
        )
    return parsed


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
    """Write the Pt Sidecar JSON v2 contract."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": _SIDECAR_SCHEMA_VERSION,
        "method_label": result.method_label,
        "pattern": _pattern_payload(result.pattern),
        "inputs": {
            "detected_features_csv": result.detected_features_csv,
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
    return (
        f"{candidate.position_count}/{candidate.required_position_count} positions; "
        f"{candidate.local_signal_support_status} local signal; "
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
