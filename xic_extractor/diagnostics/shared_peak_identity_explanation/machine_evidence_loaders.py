from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from xic_extractor.tabular_io import read_tsv_required

from .schema import (
    PEAK_HYPOTHESIS_SELECTION_COLUMNS,
    RT_MODE_EVIDENCE_COLUMNS,
)

CWT_SHAPE_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "cwt_status",
    "cwt_nearest_apex_rt",
    "cwt_apex_delta_sec",
    "cwt_boundary_width_sec",
    "cwt_prominence",
    "cwt_region_scan_count",
    "cwt_quality_flags",
    "cwt_shape_status",
)
TIER2_TRACE_REQUIRED_COLUMNS = (
    "feature_family_id",
    "raw_trace_reread_status",
    "scan_support_score",
    "trace_scan_count",
    "scan_availability_score",
    "trace_signal_to_noise_proxy",
    "trace_apex_prominence_score",
    "challenge_blockers",
)
CANDIDATE_MS2_PATTERN_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "candidate_ms2_pattern_status",
    "candidate_ms2_evidence_level",
    "diagnostic_only",
)
MS1_PATTERN_COHERENCE_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "ms1_pattern_status",
    "ms1_pattern_evidence_level",
    "apex_coherence_sec",
    "boundary_overlap_score",
    "shape_correlation_score",
    "relative_pattern_stability_score",
    "local_interference_score",
    "constellation_peak_count",
    "reference_peak_count",
    "drift_compatible_status",
    "reason",
    "diagnostic_only",
)
QC_MS1_PATTERN_REFERENCE_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "qc_reference_status",
    "qc_reference_evidence_level",
    "target_injection_order",
    "nearest_qc_sample_stem",
    "nearest_qc_injection_order",
    "nearest_qc_injection_order_delta",
    "target_apex_rt",
    "nearest_qc_apex_rt",
    "target_minus_qc_apex_delta_sec",
    "target_qc_apex_abs_delta_sec",
    "target_qc_shape_similarity",
    "target_local_window_to_global_max_ratio",
    "nearest_qc_local_window_to_global_max_ratio",
    "reason",
    "diagnostic_only",
)
MATRIX_RT_DRIFT_POLICY_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "matrix_rt_drift_status",
    "drift_evidence_level",
    "raw_rt_delta_sec",
    "drift_corrected_delta_sec",
    "matrix_shift_sec",
    "drift_reference_count",
    "drift_reference_source",
    "drift_compatible_status",
    "reason",
    "diagnostic_only",
)
SAMPLE_NEGATIVE_EVIDENCE_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "negative_evidence_class",
    "negative_evidence_detail",
    "negative_evidence_level",
    "reason",
    "diagnostic_only",
)
RT_MODE_EVIDENCE_REQUIRED_COLUMNS = tuple(
    column
    for column in RT_MODE_EVIDENCE_COLUMNS
    if column != "rt_mode_evidence_schema_version"
)
PEAK_HYPOTHESIS_SELECTION_REQUIRED_COLUMNS = tuple(
    column
    for column in PEAK_HYPOTHESIS_SELECTION_COLUMNS
    if column != "peak_hypothesis_selection_schema_version"
)

_CANDIDATE_MS2_OBSERVED_LEVELS = frozenset(
    {"sample_candidate_aligned", "sample_boundary_aligned"}
)
_CANDIDATE_MS2_SUPPORT_STATUSES = frozenset({"supportive", "partial_support"})


def _float_or_none(value: object) -> float | None:
    text = str(value or "").strip().strip("'")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int_or_none(value: object) -> int | None:
    number = _float_or_none(value)
    if number is None:
        return None
    return int(number)


def load_cwt_shape_evidence(
    path: Path | None,
) -> dict[tuple[str, str], Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, CWT_SHAPE_REQUIRED_COLUMNS)
    return {
        (row["feature_family_id"], row["sample_stem"]): row
        for row in rows
    }


def load_tier2_trace_evidence(path: Path | None) -> dict[str, Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, TIER2_TRACE_REQUIRED_COLUMNS)
    return {row["feature_family_id"]: row for row in rows}


def load_candidate_ms2_pattern_evidence(
    path: Path | None,
) -> dict[tuple[str, str], Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, CANDIDATE_MS2_PATTERN_REQUIRED_COLUMNS)
    return {
        (row["feature_family_id"], row["sample_stem"]): row
        for row in rows
    }


def _family_ms2_required_tag_by_family(
    rows_by_key: Mapping[tuple[str, str], Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    observed: dict[str, Mapping[str, str]] = {}
    for (family_id, sample_stem), row in rows_by_key.items():
        if not _has_family_ms2_required_tag(row):
            continue
        if family_id and family_id not in observed:
            observed[family_id] = {
                **row,
                "feature_family_id": family_id,
                "sample_stem": sample_stem,
            }
    return observed


def _has_family_ms2_required_tag(row: Mapping[str, str] | None) -> bool:
    if not row:
        return False
    if row.get("candidate_ms2_pattern_status") not in _CANDIDATE_MS2_SUPPORT_STATUSES:
        return False
    if row.get("candidate_ms2_evidence_level") not in _CANDIDATE_MS2_OBSERVED_LEVELS:
        return False
    count_values = (
        row.get("raw_ms2_strict_nl_scan_count"),
        row.get("matched_neutral_loss_count"),
        row.get("source_matched_tag_count"),
    )
    return any(
        (count := _int_or_none(value)) is not None and count >= 1
        for value in count_values
    )


def load_ms1_pattern_coherence_evidence(
    path: Path | None,
) -> dict[tuple[str, str], Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, MS1_PATTERN_COHERENCE_REQUIRED_COLUMNS)
    return {
        (row["feature_family_id"], row["sample_stem"]): row
        for row in rows
    }


def load_qc_ms1_pattern_reference_evidence(
    path: Path | None,
) -> dict[tuple[str, str], Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, QC_MS1_PATTERN_REFERENCE_REQUIRED_COLUMNS)
    return {
        (row["feature_family_id"], row["sample_stem"]): row
        for row in rows
    }


def load_matrix_rt_drift_policy_evidence(
    path: Path | None,
) -> dict[tuple[str, str], Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, MATRIX_RT_DRIFT_POLICY_REQUIRED_COLUMNS)
    return {
        (row["feature_family_id"], row["sample_stem"]): row
        for row in rows
    }


def load_sample_negative_evidence(
    path: Path | None,
) -> dict[tuple[str, str], Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, SAMPLE_NEGATIVE_EVIDENCE_REQUIRED_COLUMNS)
    return {
        (row["feature_family_id"], row["sample_stem"]): row
        for row in rows
    }


def load_rt_mode_evidence(
    path: Path | None,
) -> dict[tuple[str, str], Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, RT_MODE_EVIDENCE_REQUIRED_COLUMNS)
    return {
        (row["feature_family_id"], row["sample_stem"]): row
        for row in rows
    }


def load_peak_hypothesis_selection(
    path: Path | None,
) -> dict[tuple[str, str], Mapping[str, str]]:
    if path is None:
        return {}
    rows = read_tsv_required(path, PEAK_HYPOTHESIS_SELECTION_REQUIRED_COLUMNS)
    return {
        (row["feature_family_id"], row["sample_stem"]): row
        for row in rows
    }


__all__ = [
    "CANDIDATE_MS2_PATTERN_REQUIRED_COLUMNS",
    "CWT_SHAPE_REQUIRED_COLUMNS",
    "MATRIX_RT_DRIFT_POLICY_REQUIRED_COLUMNS",
    "MS1_PATTERN_COHERENCE_REQUIRED_COLUMNS",
    "PEAK_HYPOTHESIS_SELECTION_REQUIRED_COLUMNS",
    "QC_MS1_PATTERN_REFERENCE_REQUIRED_COLUMNS",
    "RT_MODE_EVIDENCE_REQUIRED_COLUMNS",
    "SAMPLE_NEGATIVE_EVIDENCE_REQUIRED_COLUMNS",
    "TIER2_TRACE_REQUIRED_COLUMNS",
    "_family_ms2_required_tag_by_family",
    "_has_family_ms2_required_tag",
    "load_candidate_ms2_pattern_evidence",
    "load_cwt_shape_evidence",
    "load_matrix_rt_drift_policy_evidence",
    "load_ms1_pattern_coherence_evidence",
    "load_peak_hypothesis_selection",
    "load_qc_ms1_pattern_reference_evidence",
    "load_rt_mode_evidence",
    "load_sample_negative_evidence",
    "load_tier2_trace_evidence",
]
