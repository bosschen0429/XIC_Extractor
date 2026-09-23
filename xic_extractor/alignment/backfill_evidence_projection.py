from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from xic_extractor.alignment._backfill_util import rows_by_family_sample_key
from xic_extractor.alignment.shared_peak_identity_explanation import (
    machine_evidence_support as evidence_support,
)
from xic_extractor.tabular_io import read_tsv_required, text_value

BACKFILL_PROJECTION_COLUMNS = (
    "backfill_ms1_pattern_status",
    "backfill_ms1_pattern_evidence_level",
    "backfill_ms1_product_authority_status",
    "backfill_ms1_product_authority_scope",
    "backfill_ms1_product_authority_source",
    "backfill_ms1_product_authority_reason",
    "backfill_ms1_product_authority_evidence_sha256",
    "backfill_qc_reference_status",
    "backfill_qc_reference_evidence_level",
    "backfill_matrix_rt_drift_status",
    "backfill_drift_evidence_level",
    "backfill_drift_compatible_status",
    "backfill_drift_corrected_delta_sec",
    "backfill_candidate_ms2_pattern_status",
    "backfill_candidate_ms2_evidence_level",
    "backfill_candidate_ms2_product_authority_status",
    "backfill_candidate_ms2_product_authority_scope",
    "backfill_candidate_ms2_product_authority_source",
    "backfill_candidate_ms2_product_authority_reason",
    "backfill_candidate_ms2_product_authority_evidence_sha256",
    "backfill_ms2_trigger_scan_count",
    "backfill_strict_nl_scan_count",
    "backfill_ms2_trace_strength",
    "backfill_dda_missing_nl_policy_status",
    "backfill_family_ms2_required_tag_status",
    "backfill_evidence_reason",
)

PRODUCT_AUTHORITY_STATUS_FIELD = "product_authority_status"
PRODUCT_AUTHORITY_SCOPE_FIELD = "product_authority_scope"
PRODUCT_AUTHORITY_SOURCE_FIELD = "product_authority_source"
PRODUCT_AUTHORIZED_STATUS = "product_authorized"
PRODUCT_AUTHORIZED_SCOPE = "feature_family_sample"

_DDA_NON_DISPOSITIVE_TRIGGER_SCAN_MIN = 3
_DDA_NON_DISPOSITIVE_TRACE_STRENGTHS = frozenset({"moderate", "strong"})
_MS1_PATTERN_PROJECTION = {
    "ms1_pattern_status": "backfill_ms1_pattern_status",
    "ms1_pattern_evidence_level": "backfill_ms1_pattern_evidence_level",
}
_MS1_AUTHORITY_PROJECTION = {
    PRODUCT_AUTHORITY_STATUS_FIELD: "backfill_ms1_product_authority_status",
    PRODUCT_AUTHORITY_SCOPE_FIELD: "backfill_ms1_product_authority_scope",
    PRODUCT_AUTHORITY_SOURCE_FIELD: "backfill_ms1_product_authority_source",
    "product_authority_reason": "backfill_ms1_product_authority_reason",
    "product_authority_overlay_trace_data_sha256": (
        "backfill_ms1_product_authority_evidence_sha256"
    ),
}
_QC_REFERENCE_PROJECTION = {
    "qc_reference_status": "backfill_qc_reference_status",
    "qc_reference_evidence_level": "backfill_qc_reference_evidence_level",
}
_RT_DRIFT_PROJECTION = {
    "matrix_rt_drift_status": "backfill_matrix_rt_drift_status",
    "drift_evidence_level": "backfill_drift_evidence_level",
    "drift_compatible_status": "backfill_drift_compatible_status",
    "drift_corrected_delta_sec": "backfill_drift_corrected_delta_sec",
}
_CANDIDATE_MS2_PROJECTION = {
    "candidate_ms2_pattern_status": "backfill_candidate_ms2_pattern_status",
    "candidate_ms2_evidence_level": "backfill_candidate_ms2_evidence_level",
    "raw_ms2_trigger_scan_count": "backfill_ms2_trigger_scan_count",
    "raw_ms2_strict_nl_scan_count": "backfill_strict_nl_scan_count",
    "raw_ms2_trace_strength": "backfill_ms2_trace_strength",
}
_CANDIDATE_MS2_AUTHORITY_PROJECTION = {
    PRODUCT_AUTHORITY_STATUS_FIELD: "backfill_candidate_ms2_product_authority_status",
    PRODUCT_AUTHORITY_SCOPE_FIELD: "backfill_candidate_ms2_product_authority_scope",
    PRODUCT_AUTHORITY_SOURCE_FIELD: "backfill_candidate_ms2_product_authority_source",
    "product_authority_reason": "backfill_candidate_ms2_product_authority_reason",
    "product_authority_candidate_ms2_source_row_sha256": (
        "backfill_candidate_ms2_product_authority_evidence_sha256"
    ),
}


def load_candidate_ms2_pattern_rows(
    path: Path | None,
) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    return read_tsv_required(
        path,
        evidence_support.CANDIDATE_MS2_PATTERN_REQUIRED_COLUMNS,
    )


def load_ms1_pattern_coherence_rows(
    path: Path | None,
) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    return read_tsv_required(
        path,
        evidence_support.MS1_PATTERN_COHERENCE_REQUIRED_COLUMNS,
    )


def load_qc_ms1_pattern_reference_rows(
    path: Path | None,
) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    return read_tsv_required(
        path,
        evidence_support.QC_MS1_PATTERN_REFERENCE_REQUIRED_COLUMNS,
    )


def load_matrix_rt_drift_policy_rows(
    path: Path | None,
) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    return read_tsv_required(
        path,
        evidence_support.MATRIX_RT_DRIFT_POLICY_REQUIRED_COLUMNS,
    )


def project_backfill_evidence_to_cells(
    *,
    cell_rows: Sequence[Mapping[str, str]],
    candidate_ms2_pattern_rows: Sequence[Mapping[str, str]] = (),
    ms1_pattern_coherence_rows: Sequence[Mapping[str, str]] = (),
    qc_ms1_pattern_reference_rows: Sequence[Mapping[str, str]] = (),
    matrix_rt_drift_policy_rows: Sequence[Mapping[str, str]] = (),
) -> tuple[dict[str, str], ...]:
    candidate_ms2 = _rows_by_key(
        candidate_ms2_pattern_rows,
        source_name="candidate_ms2_pattern",
    )
    ms1_pattern = _rows_by_key(
        ms1_pattern_coherence_rows,
        source_name="ms1_pattern_coherence",
    )
    qc_reference = _rows_by_key(
        qc_ms1_pattern_reference_rows,
        source_name="qc_ms1_pattern_reference",
    )
    rt_drift = _rows_by_key(
        matrix_rt_drift_policy_rows,
        source_name="matrix_rt_drift_policy",
    )
    projected_rows: list[dict[str, str]] = []
    for cell in cell_rows:
        row = dict(cell)
        _ensure_projection_columns(row)
        _clear_projection_fields(row, BACKFILL_PROJECTION_COLUMNS)
        if text_value(row.get("status")) != "backfilled":
            projected_rows.append(row)
            continue

        family_id = text_value(row.get("feature_family_id"))
        sample_stem = text_value(row.get("sample_stem") or row.get("sample_id"))
        if not family_id or not sample_stem:
            projected_rows.append(row)
            continue

        key = (family_id, sample_stem)
        source_tokens: list[str] = []
        ms1_pattern_row = _product_authorized_row(ms1_pattern.get(key))
        if _copy_projection(
            row,
            ms1_pattern_row,
            _MS1_PATTERN_PROJECTION,
        ):
            _copy_projection(
                row,
                ms1_pattern_row,
                _MS1_AUTHORITY_PROJECTION,
            )
            source_tokens.append("ms1_pattern_coherence")
            _append_source_reason(source_tokens, ms1_pattern_row)
        if _copy_projection(
            row,
            _product_authorized_row(qc_reference.get(key)),
            _QC_REFERENCE_PROJECTION,
        ):
            source_tokens.append("qc_ms1_pattern_reference")
        if _copy_projection(
            row,
            _product_authorized_row(rt_drift.get(key)),
            _RT_DRIFT_PROJECTION,
        ):
            source_tokens.append("matrix_rt_drift_policy")

        candidate = _product_authorized_row(candidate_ms2.get(key))
        if _copy_projection(
            row,
            candidate,
            _CANDIDATE_MS2_PROJECTION,
        ):
            _copy_projection(
                row,
                candidate,
                _CANDIDATE_MS2_AUTHORITY_PROJECTION,
            )
            source_tokens.append("candidate_ms2_pattern")
        if _dda_missing_nl_not_dispositive(candidate):
            row["backfill_dda_missing_nl_policy_status"] = "not_dispositive"
            _append_unique(source_tokens, "dda_missing_nl_policy")

        row["backfill_evidence_reason"] = _merged_reason(
            row.get("backfill_evidence_reason", ""),
            source_tokens,
        )
        projected_rows.append(row)
    return tuple(projected_rows)


def _rows_by_key(
    rows: Sequence[Mapping[str, str]],
    *,
    source_name: str,
) -> dict[tuple[str, str], Mapping[str, str]]:
    return rows_by_family_sample_key(
        rows,
        duplicate_label=f"{source_name} backfill evidence sidecar",
    )


def _dda_missing_nl_not_dispositive(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    if row.get("candidate_ms2_pattern_status") != "not_observed":
        return False
    if (
        row.get("candidate_ms2_evidence_level")
        != "sample_boundary_no_observed_pattern"
    ):
        return False
    trigger_count = _int_or_none(row.get("raw_ms2_trigger_scan_count"))
    if trigger_count is None or trigger_count < _DDA_NON_DISPOSITIVE_TRIGGER_SCAN_MIN:
        return False
    strict_nl_count = _int_or_none(row.get("raw_ms2_strict_nl_scan_count"))
    if strict_nl_count not in {0, None}:
        return False
    return (
        row.get("raw_ms2_trace_strength") in _DDA_NON_DISPOSITIVE_TRACE_STRENGTHS
        or row.get("raw_ms2_diagnostic_product_absence_reason")
        == "product_outside_diagnostic_window"
    )


def _copy_projection(
    row: dict[str, str],
    source: Mapping[str, str] | None,
    mapping: Mapping[str, str],
) -> bool:
    if source is None:
        return False
    copied = False
    for source_field, destination_field in mapping.items():
        value = text_value(source.get(source_field))
        if value:
            row[destination_field] = value
            copied = True
    return copied


def _product_authorized_row(
    row: Mapping[str, str] | None,
) -> Mapping[str, str] | None:
    if row is None:
        return None
    if text_value(row.get("diagnostic_only")).upper() != "FALSE":
        return None
    if text_value(row.get(PRODUCT_AUTHORITY_STATUS_FIELD)) != PRODUCT_AUTHORIZED_STATUS:
        return None
    if text_value(row.get(PRODUCT_AUTHORITY_SCOPE_FIELD)) != PRODUCT_AUTHORIZED_SCOPE:
        return None
    if not text_value(row.get(PRODUCT_AUTHORITY_SOURCE_FIELD)):
        return None
    return row


def _ensure_projection_columns(row: dict[str, str]) -> None:
    for column in BACKFILL_PROJECTION_COLUMNS:
        row.setdefault(column, "")


def _clear_projection_fields(
    row: dict[str, str],
    fields: Iterable[str],
) -> None:
    for field in fields:
        row[field] = ""


def _merged_reason(existing: object, tokens: Sequence[str]) -> str:
    parts = [part for part in text_value(existing).split(";") if part]
    for token in tokens:
        _append_unique(parts, token)
    return ";".join(parts)


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _append_source_reason(
    values: list[str],
    source: Mapping[str, str] | None,
) -> None:
    if source is None:
        return
    reason = text_value(source.get("reason"))
    if not reason:
        return
    for token in reason.split(";"):
        _append_unique(values, token.strip())


def _int_or_none(value: object) -> int | None:
    try:
        text = text_value(value)
        return int(float(text)) if text else None
    except (TypeError, ValueError):
        return None
