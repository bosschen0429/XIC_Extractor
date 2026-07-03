"""Manual-review packet builder for Pt pattern candidates."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.tabular_io import (
    optional_float,
    read_delimited_rows,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_manual_review_packet.v2"
READINESS_TIER = "diagnostic_only"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_trace_evidence"

SUMMARY_REQUIRED_COLUMNS = (
    "sample_stem",
    "candidate_id",
    "scan_pattern_seed_status",
    "base_mz",
    "base_rt_apex_min",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
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

CANDIDATE_REQUIRED_COLUMNS = (
    "sample_stem",
    "method_label",
    "candidate_id",
    "candidate_state",
    "review_priority",
    "evidence_tier",
    "evidence_score",
    "base_mz",
    "base_rt_apex_min",
    "cid_nl_base_status",
    "cid_nl_base_join_mode",
)

METRIC_REQUIRED_COLUMNS = (
    "candidate_id",
    "position_label",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
    "apex_delta_sec_to_base",
    "pearson_r_to_base",
    "active_overlap_fraction",
    "shifted_pearson_r_to_base",
    "shape_shift_sec_to_base",
    "raw_apex_delta_sec_to_base",
    "raw_smoothed_apex_delta_sec",
    "raw_pearson_r_to_base",
    "raw_active_overlap_fraction",
    "trace_quality_flags",
    "trace_support_status",
    "reason",
)

POSITION_LABELS = ("pt_plus_1", "pt_plus_2", "pt_plus_4")
STATUS_ORDER = (
    "pt_pattern_detected_diagnostic",
    "pt_pattern_candidate_partial",
    "pt_pattern_candidate_incomplete",
    "pt_pattern_candidate_review",
    "pt_pattern_candidate_base_missing",
)

PACKET_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "packet_label",
    "source_label",
    "packet_row_id",
    "review_bucket",
    "sample_stem",
    "candidate_id",
    "method_label",
    "candidate_state",
    "review_priority",
    "evidence_tier",
    "evidence_score",
    "base_mz",
    "base_rt_apex_min",
    "cid_nl_base_status",
    "cid_nl_base_join_mode",
    "scan_pattern_seed_status",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
    "min_effective_smoothing_window_sec",
    "max_trace_point_count",
    "trace_support_status",
    "combined_pt_pattern_status",
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
    "trace_reason",
    "pt_plus_1_trace_support_status",
    "pt_plus_1_trace_quality_flags",
    "pt_plus_1_median_rt_step_sec",
    "pt_plus_1_effective_smoothing_window_sec",
    "pt_plus_1_pearson_r_to_base",
    "pt_plus_1_active_overlap_fraction",
    "pt_plus_1_apex_delta_sec_to_base",
    "pt_plus_1_shifted_pearson_r_to_base",
    "pt_plus_1_shape_shift_sec_to_base",
    "pt_plus_1_raw_smoothed_apex_delta_sec",
    "pt_plus_1_raw_pearson_r_to_base",
    "pt_plus_1_raw_active_overlap_fraction",
    "pt_plus_1_raw_apex_delta_sec_to_base",
    "pt_plus_2_trace_support_status",
    "pt_plus_2_trace_quality_flags",
    "pt_plus_2_median_rt_step_sec",
    "pt_plus_2_effective_smoothing_window_sec",
    "pt_plus_2_pearson_r_to_base",
    "pt_plus_2_active_overlap_fraction",
    "pt_plus_2_apex_delta_sec_to_base",
    "pt_plus_2_shifted_pearson_r_to_base",
    "pt_plus_2_shape_shift_sec_to_base",
    "pt_plus_2_raw_smoothed_apex_delta_sec",
    "pt_plus_2_raw_pearson_r_to_base",
    "pt_plus_2_raw_active_overlap_fraction",
    "pt_plus_2_raw_apex_delta_sec_to_base",
    "pt_plus_4_trace_support_status",
    "pt_plus_4_trace_quality_flags",
    "pt_plus_4_median_rt_step_sec",
    "pt_plus_4_effective_smoothing_window_sec",
    "pt_plus_4_pearson_r_to_base",
    "pt_plus_4_active_overlap_fraction",
    "pt_plus_4_apex_delta_sec_to_base",
    "pt_plus_4_shifted_pearson_r_to_base",
    "pt_plus_4_shape_shift_sec_to_base",
    "pt_plus_4_raw_smoothed_apex_delta_sec",
    "pt_plus_4_raw_pearson_r_to_base",
    "pt_plus_4_raw_active_overlap_fraction",
    "pt_plus_4_raw_apex_delta_sec_to_base",
    "human_review_decision",
    "human_review_note",
    "oracle_status",
)


def build_review_packet_rows(
    *,
    candidate_rows: Sequence[Mapping[str, str]],
    summary_rows: Sequence[Mapping[str, str]],
    metric_rows: Sequence[Mapping[str, str]],
    packet_label: str,
    source_label: str,
    max_per_status: int,
) -> tuple[dict[str, object], ...]:
    if not packet_label.strip():
        raise ValueError("packet_label must be non-empty")
    if not source_label.strip():
        raise ValueError("source_label must be non-empty")
    if max_per_status <= 0:
        raise ValueError("max_per_status must be > 0")

    candidates_by_id = _unique_by_candidate_id(candidate_rows, label="candidate")
    metrics_by_candidate = _metrics_by_candidate(metric_rows)
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)

    for summary in summary_rows:
        candidate_id = _required_text(summary, "candidate_id")
        candidate = candidates_by_id.get(candidate_id)
        if candidate is None:
            continue
        combined_status = _required_text(summary, "combined_pt_pattern_status")
        row = _packet_row(
            candidate=candidate,
            summary=summary,
            metrics_by_position=metrics_by_candidate.get(candidate_id, {}),
            packet_label=packet_label,
            source_label=source_label,
        )
        grouped[combined_status].append(row)

    output_rows: list[dict[str, object]] = []
    for status in STATUS_ORDER:
        status_rows = sorted(grouped.pop(status, []), key=_packet_sort_key)
        output_rows.extend(status_rows[:max_per_status])
    for status in sorted(grouped):
        status_rows = sorted(grouped[status], key=_packet_sort_key)
        output_rows.extend(status_rows[:max_per_status])
    return tuple(output_rows)


def run_review_packet(
    *,
    candidates_csv: Path,
    trace_summary_csv: Path,
    trace_metrics_csv: Path,
    output_dir: Path,
    packet_label: str,
    source_label: str,
    max_per_status: int,
) -> dict[str, Path]:
    candidate_rows = read_delimited_rows(
        candidates_csv,
        required_columns=CANDIDATE_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    summary_rows = read_delimited_rows(
        trace_summary_csv,
        required_columns=SUMMARY_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    metric_rows = read_delimited_rows(
        trace_metrics_csv,
        required_columns=METRIC_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )

    packet_rows = build_review_packet_rows(
        candidate_rows=candidate_rows,
        summary_rows=summary_rows,
        metric_rows=metric_rows,
        packet_label=packet_label,
        source_label=source_label,
        max_per_status=max_per_status,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    packet_csv = output_dir / "pt_pattern_manual_review_packet.csv"
    sidecar_json = output_dir / "pt_pattern_manual_review_packet.json"
    write_delimited_rows(packet_csv, packet_rows, PACKET_COLUMNS)

    status_counts = Counter(
        str(row["combined_pt_pattern_status"]) for row in packet_rows
    )
    sidecar = {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "packet_label": packet_label,
        "source_label": source_label,
        "candidates_csv": str(candidates_csv),
        "trace_summary_csv": str(trace_summary_csv),
        "trace_metrics_csv": str(trace_metrics_csv),
        "output_dir": str(output_dir),
        "max_per_status": max_per_status,
        "packet_row_count": len(packet_rows),
        "combined_pt_pattern_status_counts": dict(sorted(status_counts.items())),
        "human_review_decision_values": ("accepted", "rejected", "uncertain"),
        "oracle_status": "pending_human_review",
        "diagnostic_only": True,
        "outputs": {
            "packet_csv": str(packet_csv),
        },
    }
    sidecar_json.write_text(
        json.dumps(sidecar, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"packet_csv": packet_csv, "sidecar_json": sidecar_json}


def _packet_row(
    *,
    candidate: Mapping[str, str],
    summary: Mapping[str, str],
    metrics_by_position: Mapping[str, Mapping[str, str]],
    packet_label: str,
    source_label: str,
) -> dict[str, object]:
    candidate_id = _required_text(summary, "candidate_id")
    row: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "packet_label": packet_label,
        "source_label": source_label,
        "packet_row_id": f"{source_label}|{candidate_id}",
        "review_bucket": _review_bucket(
            _required_text(summary, "combined_pt_pattern_status")
        ),
        "sample_stem": _required_text(summary, "sample_stem"),
        "candidate_id": candidate_id,
        "method_label": candidate.get("method_label", ""),
        "candidate_state": candidate.get("candidate_state", ""),
        "review_priority": candidate.get("review_priority", ""),
        "evidence_tier": candidate.get("evidence_tier", ""),
        "evidence_score": candidate.get("evidence_score", ""),
        "base_mz": _preferred_value(summary, candidate, "base_mz"),
        "base_rt_apex_min": _preferred_value(summary, candidate, "base_rt_apex_min"),
        "cid_nl_base_status": candidate.get("cid_nl_base_status", ""),
        "cid_nl_base_join_mode": candidate.get("cid_nl_base_join_mode", ""),
        "scan_pattern_seed_status": summary.get("scan_pattern_seed_status", ""),
        "trace_decision_basis": summary.get("trace_decision_basis", ""),
        "trace_smoothing_method": summary.get("trace_smoothing_method", ""),
        "trace_smoothing_window_points": summary.get(
            "trace_smoothing_window_points", ""
        ),
        "min_effective_smoothing_window_sec": summary.get(
            "min_effective_smoothing_window_sec", ""
        ),
        "max_trace_point_count": summary.get("max_trace_point_count", ""),
        "trace_support_status": summary.get("trace_support_status", ""),
        "combined_pt_pattern_status": summary.get("combined_pt_pattern_status", ""),
        "non_base_positions_supported": summary.get(
            "non_base_positions_supported", ""
        ),
        "max_abs_apex_delta_sec": summary.get("max_abs_apex_delta_sec", ""),
        "min_pearson_r_to_base": summary.get("min_pearson_r_to_base", ""),
        "min_active_overlap_fraction": summary.get(
            "min_active_overlap_fraction", ""
        ),
        "min_shifted_pearson_r_to_base": summary.get(
            "min_shifted_pearson_r_to_base", ""
        ),
        "max_abs_shape_shift_sec_to_base": summary.get(
            "max_abs_shape_shift_sec_to_base", ""
        ),
        "raw_max_abs_apex_delta_sec": summary.get(
            "raw_max_abs_apex_delta_sec", ""
        ),
        "raw_min_pearson_r_to_base": summary.get("raw_min_pearson_r_to_base", ""),
        "raw_min_active_overlap_fraction": summary.get(
            "raw_min_active_overlap_fraction", ""
        ),
        "trace_ambiguity_status": summary.get("trace_ambiguity_status", ""),
        "trace_ambiguity_flags": summary.get("trace_ambiguity_flags", ""),
        "shifted_only_position_labels": summary.get(
            "shifted_only_position_labels", ""
        ),
        "trace_reason": summary.get("reason", ""),
        "human_review_decision": "",
        "human_review_note": "",
        "oracle_status": "pending_human_review",
    }
    for position_label in POSITION_LABELS:
        metric = metrics_by_position.get(position_label, {})
        prefix = position_label
        row[f"{prefix}_trace_support_status"] = metric.get(
            "trace_support_status", ""
        )
        row[f"{prefix}_trace_quality_flags"] = metric.get(
            "trace_quality_flags", ""
        )
        row[f"{prefix}_median_rt_step_sec"] = metric.get(
            "median_rt_step_sec", ""
        )
        row[f"{prefix}_effective_smoothing_window_sec"] = metric.get(
            "effective_smoothing_window_sec", ""
        )
        row[f"{prefix}_pearson_r_to_base"] = metric.get("pearson_r_to_base", "")
        row[f"{prefix}_active_overlap_fraction"] = metric.get(
            "active_overlap_fraction", ""
        )
        row[f"{prefix}_apex_delta_sec_to_base"] = metric.get(
            "apex_delta_sec_to_base", ""
        )
        row[f"{prefix}_shifted_pearson_r_to_base"] = metric.get(
            "shifted_pearson_r_to_base", ""
        )
        row[f"{prefix}_shape_shift_sec_to_base"] = metric.get(
            "shape_shift_sec_to_base", ""
        )
        row[f"{prefix}_raw_smoothed_apex_delta_sec"] = metric.get(
            "raw_smoothed_apex_delta_sec", ""
        )
        row[f"{prefix}_raw_pearson_r_to_base"] = metric.get(
            "raw_pearson_r_to_base", ""
        )
        row[f"{prefix}_raw_active_overlap_fraction"] = metric.get(
            "raw_active_overlap_fraction", ""
        )
        row[f"{prefix}_raw_apex_delta_sec_to_base"] = metric.get(
            "raw_apex_delta_sec_to_base", ""
        )
    return row


def _unique_by_candidate_id(
    rows: Sequence[Mapping[str, str]], *, label: str
) -> dict[str, Mapping[str, str]]:
    by_id: dict[str, Mapping[str, str]] = {}
    duplicates: list[str] = []
    for row in rows:
        candidate_id = _required_text(row, "candidate_id")
        if candidate_id in by_id:
            duplicates.append(candidate_id)
        by_id[candidate_id] = row
    if duplicates:
        joined = ", ".join(sorted(set(duplicates))[:5])
        raise ValueError(f"duplicate {label} candidate_id values: {joined}")
    return by_id


def _metrics_by_candidate(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, dict[str, Mapping[str, str]]]:
    grouped: dict[str, dict[str, Mapping[str, str]]] = defaultdict(dict)
    duplicates: list[str] = []
    for row in rows:
        candidate_id = _required_text(row, "candidate_id")
        position_label = _required_text(row, "position_label")
        if position_label in grouped[candidate_id]:
            duplicates.append(f"{candidate_id}:{position_label}")
        grouped[candidate_id][position_label] = row
    if duplicates:
        joined = ", ".join(sorted(set(duplicates))[:5])
        raise ValueError(f"duplicate metric candidate/position values: {joined}")
    return grouped


def _review_bucket(combined_status: str) -> str:
    if combined_status == "pt_pattern_detected_diagnostic":
        return "detected_diagnostic_review"
    if combined_status == "pt_pattern_candidate_partial":
        return "partial_support_review"
    if combined_status == "pt_pattern_candidate_incomplete":
        return "incomplete_signal_review"
    if combined_status == "pt_pattern_candidate_base_missing":
        return "base_missing_review"
    return "manual_review"


def _packet_sort_key(row: Mapping[str, object]) -> tuple[float, float, str]:
    return (
        optional_float(row.get("base_rt_apex_min")) or float("inf"),
        optional_float(row.get("base_mz")) or float("inf"),
        str(row.get("candidate_id", "")),
    )


def _preferred_value(
    primary: Mapping[str, str],
    fallback: Mapping[str, str],
    field: str,
) -> str:
    value = str(primary.get(field, "")).strip()
    if value:
        return value
    return str(fallback.get(field, "")).strip()


def _required_text(row: Mapping[str, str], field: str) -> str:
    value = str(row.get(field, "")).strip()
    if not value:
        raise ValueError(f"missing required field {field!r}")
    return value
