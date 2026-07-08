"""Diagnostic gate replay for Pt pattern review packets."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.discovery.pt_pattern_evidence_rule import (
    DEFAULT_APEX_DELTA_MAX_SEC,
    DEFAULT_OVERLAP_MIN,
    DEFAULT_PEARSON_MIN,
    DEFAULT_SHAPE_SHIFT_MAX_SEC,
    DEFAULT_SHIFTED_PEARSON_MIN,
    MIN_SUPPORTED_POSITIONS,
    REQUIRED_COMBINED_STATUS,
    PtPatternEvidenceThresholds,
    effective_max_abs_apex_delta_sec,
    evaluate_pt_pattern_evidence,
    pt_pattern_rule_id,
)
from xic_extractor.discovery.pt_pattern_oracle_promotion import PROMOTION_COLUMNS
from xic_extractor.discovery.pt_pattern_review_packet import PACKET_COLUMNS
from xic_extractor.tabular_io import (
    file_sha256,
    optional_float,
    read_delimited_rows,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_diagnostic_gate.v1"
READINESS_TIER = "diagnostic_only"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_diagnostic_gate"
AUTHORITY_SCOPE = "diagnostic_gate_replay_only"

TRACE_SUMMARY_GATE_REQUIRED_COLUMNS = (
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
    "trace_support_status",
    "combined_pt_pattern_status",
)


@dataclass(frozen=True)
class TraceSummaryInput:
    source_label: str
    path: Path


GATE_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "authority_scope",
    "gate_id",
    "rule_id",
    "packet_row_id",
    "source_label",
    "sample_stem",
    "candidate_id",
    "diagnostic_gate_call",
    "diagnostic_detection_status",
    "gate_reason",
    "required_combined_status",
    "min_non_base_positions_supported",
    "max_abs_apex_delta_sec_threshold",
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
    "base_mz",
    "base_rt_apex_min",
    "oracle_call",
    "oracle_detection_status",
    "replay_outcome",
)


def run_diagnostic_gate(
    *,
    review_packet_csvs: Sequence[Path] = (),
    trace_summary_csvs: Sequence[TraceSummaryInput] = (),
    output_dir: Path,
    gate_id: str,
    oracle_promotion_csv: Path | None = None,
    apex_delta_max_sec: float = DEFAULT_APEX_DELTA_MAX_SEC,
    pearson_min: float = DEFAULT_PEARSON_MIN,
    overlap_min: float = DEFAULT_OVERLAP_MIN,
    shifted_pearson_min: float | None = None,
    shape_shift_max_sec: float | None = None,
) -> dict[str, Path]:
    thresholds = _thresholds(
        apex_delta_max_sec=apex_delta_max_sec,
        pearson_min=pearson_min,
        overlap_min=overlap_min,
        shifted_pearson_min=shifted_pearson_min,
        shape_shift_max_sec=shape_shift_max_sec,
    )
    review_rows = _read_review_packets(review_packet_csvs)
    trace_summary_rows = _read_trace_summary_inputs(trace_summary_csvs)
    gate_input_rows = [*review_rows, *trace_summary_rows]
    if not gate_input_rows:
        raise ValueError(
            "at least one review packet CSV or trace summary CSV is required"
        )
    oracle_rows = _read_oracle_rows(oracle_promotion_csv)
    gate_rows = build_diagnostic_gate_rows(
        review_rows=gate_input_rows,
        oracle_rows=oracle_rows,
        gate_id=gate_id,
        thresholds=thresholds,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    gate_rows_csv = output_dir / "pt_pattern_diagnostic_gate_rows.csv"
    summary_json = output_dir / "pt_pattern_diagnostic_gate_summary.json"
    write_delimited_rows(gate_rows_csv, gate_rows, GATE_COLUMNS)

    call_counts = Counter(str(row["diagnostic_gate_call"]) for row in gate_rows)
    replay_counts = Counter(
        str(row["replay_outcome"]) for row in gate_rows if row["replay_outcome"]
    )
    oracle_counts = Counter(
        str(row["oracle_call"]) for row in gate_rows if row["oracle_call"]
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "authority_scope": AUTHORITY_SCOPE,
        "gate_id": gate_id,
        "rule_id": pt_pattern_rule_id(thresholds),
        "required_combined_status": thresholds.required_combined_status,
        "min_non_base_positions_supported": (
            thresholds.min_non_base_positions_supported
        ),
        "max_abs_apex_delta_sec": thresholds.max_abs_apex_delta_sec,
        "max_abs_apex_delta_scan_step_multiplier": (
            thresholds.max_abs_apex_delta_scan_step_multiplier
        ),
        "min_pearson_r_to_base": thresholds.min_pearson_r_to_base,
        "min_active_overlap_fraction": thresholds.min_active_overlap_fraction,
        "min_shifted_pearson_r_to_base": (
            thresholds.min_shifted_pearson_r_to_base
        ),
        "max_abs_shape_shift_sec_to_base": (
            thresholds.max_abs_shape_shift_sec_to_base
        ),
        "review_packet_csvs": [str(path) for path in review_packet_csvs],
        "review_packet_csv_sha256s": {
            str(path): file_sha256(path) for path in review_packet_csvs
        },
        "trace_summary_csvs": [
            f"{item.source_label}={item.path}" for item in trace_summary_csvs
        ],
        "trace_summary_csv_sha256s": {
            f"{item.source_label}={item.path}": file_sha256(item.path)
            for item in trace_summary_csvs
        },
        "oracle_promotion_csv": str(oracle_promotion_csv or ""),
        "oracle_promotion_csv_sha256": (
            file_sha256(oracle_promotion_csv) if oracle_promotion_csv else ""
        ),
        "row_count": len(gate_rows),
        "diagnostic_gate_call_counts": dict(sorted(call_counts.items())),
        "oracle_call_counts": dict(sorted(oracle_counts.items())),
        "replay_outcome_counts": dict(sorted(replay_counts.items())),
        "replay_status": _replay_status(replay_counts, oracle_rows),
        "changes_matrix": False,
        "changes_counted_detection": False,
        "changes_export": False,
        "outputs": {
            "gate_rows_csv": str(gate_rows_csv),
        },
    }
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"gate_rows_csv": gate_rows_csv, "summary_json": summary_json}


def build_diagnostic_gate_rows(
    *,
    review_rows: Sequence[Mapping[str, str]],
    oracle_rows: Sequence[Mapping[str, str]] = (),
    gate_id: str,
    apex_delta_max_sec: float = DEFAULT_APEX_DELTA_MAX_SEC,
    pearson_min: float = DEFAULT_PEARSON_MIN,
    overlap_min: float = DEFAULT_OVERLAP_MIN,
    thresholds: PtPatternEvidenceThresholds | None = None,
) -> tuple[dict[str, object], ...]:
    if not gate_id.strip():
        raise ValueError("gate_id must be non-empty")
    thresholds = thresholds or _thresholds(
        apex_delta_max_sec=apex_delta_max_sec,
        pearson_min=pearson_min,
        overlap_min=overlap_min,
    )
    by_packet_row_id = _unique_review_rows(review_rows)
    oracle_by_packet_row_id = _unique_oracle_rows(oracle_rows)
    missing = sorted(set(oracle_by_packet_row_id) - set(by_packet_row_id))
    if missing:
        raise ValueError(
            "oracle row missing review packet: " + ", ".join(missing[:5])
        )

    rows: list[dict[str, object]] = []
    for review in sorted(
        review_rows,
        key=lambda row: (
            _required_text(row, "source_label"),
            optional_float(row.get("base_rt_apex_min")) or float("inf"),
            optional_float(row.get("base_mz")) or float("inf"),
            _required_text(row, "packet_row_id"),
        ),
    ):
        packet_row_id = _required_text(review, "packet_row_id")
        decision = evaluate_pt_pattern_evidence(
            review,
            thresholds=thresholds,
        )
        effective_apex_threshold = effective_max_abs_apex_delta_sec(
            review,
            thresholds=thresholds,
        )
        oracle = oracle_by_packet_row_id.get(packet_row_id, {})
        oracle_call = str(oracle.get("oracle_call", "")).strip().lower()
        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "readiness_tier": READINESS_TIER,
                "evidence_owner": EVIDENCE_OWNER,
                "authority_scope": AUTHORITY_SCOPE,
                "gate_id": gate_id,
                "rule_id": pt_pattern_rule_id(thresholds),
                "packet_row_id": packet_row_id,
                "source_label": review.get("source_label", ""),
                "sample_stem": review.get("sample_stem", ""),
                "candidate_id": review.get("candidate_id", ""),
                "diagnostic_gate_call": decision.call,
                "diagnostic_detection_status": decision.detection_status,
                "gate_reason": decision.reason,
                "required_combined_status": thresholds.required_combined_status,
                "min_non_base_positions_supported": (
                    thresholds.min_non_base_positions_supported
                ),
                "max_abs_apex_delta_sec_threshold": (
                    effective_apex_threshold
                ),
                "min_pearson_r_to_base_threshold": (
                    thresholds.min_pearson_r_to_base
                ),
                "min_active_overlap_fraction_threshold": (
                    thresholds.min_active_overlap_fraction
                ),
                "min_shifted_pearson_r_to_base_threshold": (
                    thresholds.min_shifted_pearson_r_to_base
                ),
                "max_abs_shape_shift_sec_to_base_threshold": (
                    thresholds.max_abs_shape_shift_sec_to_base
                ),
                "combined_pt_pattern_status": review.get(
                    "combined_pt_pattern_status", ""
                ),
                "non_base_positions_supported": review.get(
                    "non_base_positions_supported", ""
                ),
                "max_abs_apex_delta_sec": review.get("max_abs_apex_delta_sec", ""),
                "min_pearson_r_to_base": review.get("min_pearson_r_to_base", ""),
                "min_active_overlap_fraction": review.get(
                    "min_active_overlap_fraction", ""
                ),
                "min_shifted_pearson_r_to_base": review.get(
                    "min_shifted_pearson_r_to_base", ""
                ),
                "max_abs_shape_shift_sec_to_base": review.get(
                    "max_abs_shape_shift_sec_to_base", ""
                ),
                "base_mz": review.get("base_mz", ""),
                "base_rt_apex_min": review.get("base_rt_apex_min", ""),
                "oracle_call": oracle_call,
                "oracle_detection_status": oracle.get(
                    "oracle_detection_status", ""
                ),
                "replay_outcome": (
                    _case_outcome(oracle_call, decision.call)
                    if oracle_call
                    else ""
                ),
            }
        )
    return tuple(rows)


def diagnostic_gate_call(
    row: Mapping[str, object],
    *,
    apex_delta_max_sec: float = DEFAULT_APEX_DELTA_MAX_SEC,
    pearson_min: float = DEFAULT_PEARSON_MIN,
    overlap_min: float = DEFAULT_OVERLAP_MIN,
    shifted_pearson_min: float | None = None,
    shape_shift_max_sec: float | None = None,
) -> tuple[str, str]:
    decision = evaluate_pt_pattern_evidence(
        row,
        thresholds=_thresholds(
            apex_delta_max_sec=apex_delta_max_sec,
            pearson_min=pearson_min,
            overlap_min=overlap_min,
            shifted_pearson_min=shifted_pearson_min,
            shape_shift_max_sec=shape_shift_max_sec,
        ),
    )
    return decision.call, decision.reason


def _read_review_packets(paths: Sequence[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        rows.extend(
            read_delimited_rows(
                path,
                required_columns=PACKET_COLUMNS,
                delimiter=",",
                encoding="utf-8-sig",
            )
        )
    return rows


def _read_trace_summary_inputs(
    inputs: Sequence[TraceSummaryInput],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in inputs:
        source_label = item.source_label.strip()
        if not source_label:
            raise ValueError("trace summary source_label must be non-empty")
        rows.extend(
            _trace_summary_gate_row(row, source_label=source_label)
            for row in read_delimited_rows(
                item.path,
                required_columns=TRACE_SUMMARY_GATE_REQUIRED_COLUMNS,
                delimiter=",",
                encoding="utf-8-sig",
            )
        )
    return rows


def _trace_summary_gate_row(
    row: Mapping[str, str],
    *,
    source_label: str,
) -> dict[str, str]:
    candidate_id = _required_text(row, "candidate_id")
    return {
        "source_label": source_label,
        "packet_row_id": f"{source_label}|{candidate_id}",
        "sample_stem": row.get("sample_stem", ""),
        "candidate_id": candidate_id,
        "combined_pt_pattern_status": row.get("combined_pt_pattern_status", ""),
        "non_base_positions_supported": row.get(
            "non_base_positions_supported", ""
        ),
        "max_abs_apex_delta_sec": row.get("max_abs_apex_delta_sec", ""),
        "min_pearson_r_to_base": row.get("min_pearson_r_to_base", ""),
        "min_active_overlap_fraction": row.get(
            "min_active_overlap_fraction", ""
        ),
        "min_shifted_pearson_r_to_base": row.get(
            "min_shifted_pearson_r_to_base", ""
        ),
        "max_abs_shape_shift_sec_to_base": row.get(
            "max_abs_shape_shift_sec_to_base", ""
        ),
        "min_effective_smoothing_window_sec": row.get(
            "min_effective_smoothing_window_sec", ""
        ),
        "trace_smoothing_window_points": row.get(
            "trace_smoothing_window_points", ""
        ),
        "base_mz": row.get("base_mz", ""),
        "base_rt_apex_min": row.get("base_rt_apex_min", ""),
    }


def _read_oracle_rows(path: Path | None) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    return tuple(
        read_delimited_rows(
            path,
            required_columns=PROMOTION_COLUMNS,
            delimiter=",",
            encoding="utf-8-sig",
        )
    )


def _unique_review_rows(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    by_id: dict[str, Mapping[str, str]] = {}
    for row in rows:
        packet_row_id = _required_text(row, "packet_row_id")
        if packet_row_id in by_id:
            raise ValueError(f"duplicate review packet_row_id: {packet_row_id}")
        by_id[packet_row_id] = row
    return by_id


def _unique_oracle_rows(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    by_id: dict[str, Mapping[str, str]] = {}
    for row in rows:
        packet_row_id = _required_text(row, "packet_row_id")
        if packet_row_id in by_id:
            raise ValueError(f"duplicate oracle packet_row_id: {packet_row_id}")
        oracle_call = _required_text(row, "oracle_call").lower()
        if oracle_call not in {"accept", "reject"}:
            raise ValueError(f"unsupported oracle_call: {oracle_call!r}")
        by_id[packet_row_id] = row
    return by_id


def _case_outcome(oracle_call: str, predicted_call: str) -> str:
    if oracle_call == "accept" and predicted_call == "accept":
        return "true_positive"
    if oracle_call == "reject" and predicted_call == "accept":
        return "false_positive"
    if oracle_call == "reject" and predicted_call == "reject":
        return "true_negative"
    if oracle_call == "accept" and predicted_call == "reject":
        return "false_negative"
    raise ValueError(f"unsupported call pair: {oracle_call!r}, {predicted_call!r}")


def _replay_status(
    replay_counts: Mapping[str, int],
    oracle_rows: Sequence[Mapping[str, str]],
) -> str:
    if not oracle_rows:
        return "no_oracle_replay"
    if (
        int(replay_counts.get("false_positive", 0)) == 0
        and int(replay_counts.get("false_negative", 0)) == 0
    ):
        return "diagnostic_gate_replay_passed"
    return "diagnostic_gate_replay_failed"


def _thresholds(
    apex_delta_max_sec: float,
    pearson_min: float,
    overlap_min: float,
    shifted_pearson_min: float | None = None,
    shape_shift_max_sec: float | None = None,
) -> PtPatternEvidenceThresholds:
    return PtPatternEvidenceThresholds(
        required_combined_status=REQUIRED_COMBINED_STATUS,
        min_non_base_positions_supported=MIN_SUPPORTED_POSITIONS,
        max_abs_apex_delta_sec=apex_delta_max_sec,
        min_pearson_r_to_base=pearson_min,
        min_active_overlap_fraction=overlap_min,
        min_shifted_pearson_r_to_base=(
            DEFAULT_SHIFTED_PEARSON_MIN
            if shifted_pearson_min is None
            else shifted_pearson_min
        ),
        max_abs_shape_shift_sec_to_base=(
            DEFAULT_SHAPE_SHIFT_MAX_SEC
            if shape_shift_max_sec is None
            else shape_shift_max_sec
        ),
    )


def _required_text(row: Mapping[str, str], key: str) -> str:
    value = str(row.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} must be non-empty")
    return value
