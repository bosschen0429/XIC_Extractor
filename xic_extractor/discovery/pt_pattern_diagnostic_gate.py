"""Diagnostic gate replay for Pt pattern review packets."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_oracle_promotion import PROMOTION_COLUMNS
from xic_extractor.discovery.pt_pattern_oracle_thresholds import (
    DEFAULT_APEX_DELTA_MAX_SEC,
    DEFAULT_OVERLAP_MIN,
    DEFAULT_PEARSON_MIN,
    MIN_SUPPORTED_POSITIONS,
    REQUIRED_COMBINED_STATUS,
)
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
    "combined_pt_pattern_status",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "base_mz",
    "base_rt_apex_min",
    "oracle_call",
    "oracle_detection_status",
    "replay_outcome",
)


def run_diagnostic_gate(
    *,
    review_packet_csvs: Sequence[Path],
    output_dir: Path,
    gate_id: str,
    oracle_promotion_csv: Path | None = None,
    apex_delta_max_sec: float = DEFAULT_APEX_DELTA_MAX_SEC,
    pearson_min: float = DEFAULT_PEARSON_MIN,
    overlap_min: float = DEFAULT_OVERLAP_MIN,
) -> dict[str, Path]:
    review_rows = _read_review_packets(review_packet_csvs)
    oracle_rows = _read_oracle_rows(oracle_promotion_csv)
    gate_rows = build_diagnostic_gate_rows(
        review_rows=review_rows,
        oracle_rows=oracle_rows,
        gate_id=gate_id,
        apex_delta_max_sec=apex_delta_max_sec,
        pearson_min=pearson_min,
        overlap_min=overlap_min,
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
        "rule_id": _rule_id(
            apex_delta_max_sec=apex_delta_max_sec,
            pearson_min=pearson_min,
            overlap_min=overlap_min,
        ),
        "required_combined_status": REQUIRED_COMBINED_STATUS,
        "min_non_base_positions_supported": MIN_SUPPORTED_POSITIONS,
        "max_abs_apex_delta_sec": apex_delta_max_sec,
        "min_pearson_r_to_base": pearson_min,
        "min_active_overlap_fraction": overlap_min,
        "review_packet_csvs": [str(path) for path in review_packet_csvs],
        "review_packet_csv_sha256s": {
            str(path): file_sha256(path) for path in review_packet_csvs
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
) -> tuple[dict[str, object], ...]:
    if not gate_id.strip():
        raise ValueError("gate_id must be non-empty")
    _validate_thresholds(
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
        gate_call, reason = diagnostic_gate_call(
            review,
            apex_delta_max_sec=apex_delta_max_sec,
            pearson_min=pearson_min,
            overlap_min=overlap_min,
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
                "rule_id": _rule_id(
                    apex_delta_max_sec=apex_delta_max_sec,
                    pearson_min=pearson_min,
                    overlap_min=overlap_min,
                ),
                "packet_row_id": packet_row_id,
                "source_label": review.get("source_label", ""),
                "sample_stem": review.get("sample_stem", ""),
                "candidate_id": review.get("candidate_id", ""),
                "diagnostic_gate_call": gate_call,
                "diagnostic_detection_status": (
                    "detected" if gate_call == "accept" else "non_detected"
                ),
                "gate_reason": reason,
                "required_combined_status": REQUIRED_COMBINED_STATUS,
                "min_non_base_positions_supported": MIN_SUPPORTED_POSITIONS,
                "max_abs_apex_delta_sec_threshold": apex_delta_max_sec,
                "min_pearson_r_to_base_threshold": pearson_min,
                "min_active_overlap_fraction_threshold": overlap_min,
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
                "base_mz": review.get("base_mz", ""),
                "base_rt_apex_min": review.get("base_rt_apex_min", ""),
                "oracle_call": oracle_call,
                "oracle_detection_status": oracle.get(
                    "oracle_detection_status", ""
                ),
                "replay_outcome": (
                    _case_outcome(oracle_call, gate_call) if oracle_call else ""
                ),
            }
        )
    return tuple(rows)


def diagnostic_gate_call(
    row: Mapping[str, str],
    *,
    apex_delta_max_sec: float = DEFAULT_APEX_DELTA_MAX_SEC,
    pearson_min: float = DEFAULT_PEARSON_MIN,
    overlap_min: float = DEFAULT_OVERLAP_MIN,
) -> tuple[str, str]:
    status = str(row.get("combined_pt_pattern_status", "")).strip()
    if status != REQUIRED_COMBINED_STATUS:
        return "reject", "combined_status_not_detected"

    supported = optional_float(row.get("non_base_positions_supported"))
    apex_delta = optional_float(row.get("max_abs_apex_delta_sec"))
    pearson = optional_float(row.get("min_pearson_r_to_base"))
    overlap = optional_float(row.get("min_active_overlap_fraction"))
    if (
        supported is None
        or apex_delta is None
        or pearson is None
        or overlap is None
    ):
        return "reject", "missing_required_metric"
    if supported < MIN_SUPPORTED_POSITIONS:
        return "reject", "insufficient_non_base_positions"
    if apex_delta > apex_delta_max_sec:
        return "reject", "apex_delta_above_threshold"
    if pearson < pearson_min:
        return "reject", "pearson_below_threshold"
    if overlap < overlap_min:
        return "reject", "overlap_below_threshold"
    return "accept", "full_pattern_close_apex_shape_overlap"


def _read_review_packets(paths: Sequence[Path]) -> list[dict[str, str]]:
    if not paths:
        raise ValueError("at least one review packet CSV is required")
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


def _rule_id(
    *,
    apex_delta_max_sec: float,
    pearson_min: float,
    overlap_min: float,
) -> str:
    return (
        "pt_v2_fullpattern"
        f"_apex_le_{_token(apex_delta_max_sec)}s"
        f"_pearson_ge_{_token(pearson_min)}"
        f"_overlap_ge_{_token(overlap_min)}"
    )


def _token(value: float) -> str:
    return f"{value:g}".replace(".", "p")


def _validate_thresholds(
    *,
    apex_delta_max_sec: float,
    pearson_min: float,
    overlap_min: float,
) -> None:
    if apex_delta_max_sec <= 0:
        raise ValueError("apex_delta_max_sec must be > 0")
    if not 0 <= pearson_min <= 1:
        raise ValueError("pearson_min must be between 0 and 1")
    if not 0 <= overlap_min <= 1:
        raise ValueError("overlap_min must be between 0 and 1")


def _required_text(row: Mapping[str, str], key: str) -> str:
    value = str(row.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} must be non-empty")
    return value
