"""Threshold calibration for the bounded Pt pattern manual oracle."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.discovery.pt_pattern_blind_overlay import classify_machine_call
from xic_extractor.discovery.pt_pattern_oracle_promotion import PROMOTION_COLUMNS
from xic_extractor.discovery.pt_pattern_review_packet import PACKET_COLUMNS
from xic_extractor.tabular_io import (
    file_sha256,
    optional_float,
    read_delimited_rows,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_oracle_threshold_calibration.v1"
READINESS_TIER = "diagnostic_only"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_oracle_thresholds"
AUTHORITY_SCOPE = "threshold_calibration_only"

REQUIRED_COMBINED_STATUS = "pt_pattern_detected_diagnostic"
MIN_SUPPORTED_POSITIONS = 3
DEFAULT_APEX_DELTA_MAX_SEC = 10.0
DEFAULT_PEARSON_MIN = 0.90
DEFAULT_OVERLAP_MIN = 0.50

APEX_DELTA_GRID_SEC = (2.0, 3.0, 5.0, 10.0, 20.0, 30.0)
PEARSON_GRID = (0.50, 0.70, 0.80, 0.85, 0.90, 0.95, 0.99)
OVERLAP_GRID = (0.25, 0.50, 0.70, 0.80, 0.90, 0.95)

THRESHOLD_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "authority_scope",
    "calibration_id",
    "rule_id",
    "recommendation_rank",
    "recommendation_reason",
    "required_combined_status",
    "min_non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "row_count",
    "oracle_accept_count",
    "oracle_reject_count",
    "predicted_accept_count",
    "predicted_reject_count",
    "true_positive",
    "false_positive",
    "true_negative",
    "false_negative",
    "precision",
    "recall",
    "specificity",
    "f1",
    "accuracy",
    "balanced_accuracy",
)

CASE_AUDIT_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "authority_scope",
    "calibration_id",
    "recommended_rule_id",
    "packet_row_id",
    "dataset",
    "case_index",
    "sample_stem",
    "candidate_id",
    "oracle_call",
    "oracle_detection_status",
    "recommended_prediction",
    "calibration_outcome",
    "combined_pt_pattern_status",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "base_mz",
    "base_rt_apex_min",
    "machine_call",
    "machine_call_reason",
    "human_review_note",
)


@dataclass(frozen=True)
class CalibrationCase:
    packet_row_id: str
    dataset: str
    case_index: str
    sample_stem: str
    candidate_id: str
    oracle_call: str
    oracle_detection_status: str
    combined_status: str
    non_base_positions_supported: float | None
    max_abs_apex_delta_sec: float | None
    min_pearson_r_to_base: float | None
    min_active_overlap_fraction: float | None
    base_mz: str
    base_rt_apex_min: str
    machine_call: str
    machine_call_reason: str
    human_review_note: str
    review_row: Mapping[str, str]


def run_threshold_calibration(
    *,
    oracle_promotion_csv: Path,
    review_packet_csvs: Sequence[Path],
    output_dir: Path,
    calibration_id: str,
) -> dict[str, Path]:
    oracle_rows = read_delimited_rows(
        oracle_promotion_csv,
        required_columns=PROMOTION_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    review_rows = _read_review_packets(review_packet_csvs)
    cases = build_calibration_cases(
        oracle_rows=oracle_rows,
        review_rows=review_rows,
    )
    threshold_rows = evaluate_threshold_grid(
        cases=cases,
        calibration_id=calibration_id,
    )
    recommended = threshold_rows[0]
    case_rows = build_case_audit_rows(
        cases=cases,
        recommended_rule=recommended,
        calibration_id=calibration_id,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    threshold_csv = output_dir / "pt_pattern_oracle_threshold_candidates.csv"
    case_audit_csv = output_dir / "pt_pattern_oracle_threshold_case_audit.csv"
    summary_json = output_dir / "pt_pattern_oracle_threshold_summary.json"
    write_delimited_rows(threshold_csv, threshold_rows, THRESHOLD_COLUMNS)
    write_delimited_rows(case_audit_csv, case_rows, CASE_AUDIT_COLUMNS)

    oracle_counts = Counter(case.oracle_call for case in cases)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "authority_scope": AUTHORITY_SCOPE,
        "calibration_id": calibration_id,
        "oracle_promotion_csv": str(oracle_promotion_csv),
        "oracle_promotion_csv_sha256": file_sha256(oracle_promotion_csv),
        "review_packet_csvs": [str(path) for path in review_packet_csvs],
        "review_packet_csv_sha256s": {
            str(path): file_sha256(path) for path in review_packet_csvs
        },
        "row_count": len(cases),
        "oracle_call_counts": dict(sorted(oracle_counts.items())),
        "threshold_candidate_count": len(threshold_rows),
        "recommended_rule": _summary_rule(recommended),
        "baseline_machine_call_confusion": baseline_machine_call_confusion(cases),
        "calibration_status": _calibration_status(recommended),
        "changes_matrix": False,
        "changes_counted_detection": False,
        "changes_export": False,
        "outputs": {
            "threshold_candidates_csv": str(threshold_csv),
            "case_audit_csv": str(case_audit_csv),
        },
    }
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "threshold_candidates_csv": threshold_csv,
        "case_audit_csv": case_audit_csv,
        "summary_json": summary_json,
    }


def build_calibration_cases(
    *,
    oracle_rows: Sequence[Mapping[str, str]],
    review_rows: Sequence[Mapping[str, str]],
) -> tuple[CalibrationCase, ...]:
    if not oracle_rows:
        raise ValueError("oracle rows must be non-empty")
    by_packet_row_id = _unique_review_rows(review_rows)
    cases: list[CalibrationCase] = []
    seen: set[str] = set()
    for oracle in oracle_rows:
        packet_row_id = _required_text(oracle, "packet_row_id")
        if packet_row_id in seen:
            raise ValueError(f"duplicate oracle packet_row_id: {packet_row_id}")
        seen.add(packet_row_id)
        review = by_packet_row_id.get(packet_row_id)
        if review is None:
            raise ValueError(f"oracle row missing review packet: {packet_row_id}")
        cases.append(_calibration_case(oracle, review))
    return tuple(cases)


def evaluate_threshold_grid(
    *,
    cases: Sequence[CalibrationCase],
    calibration_id: str,
) -> tuple[dict[str, object], ...]:
    if not calibration_id.strip():
        raise ValueError("calibration_id must be non-empty")
    rows: list[dict[str, object]] = []
    for apex_delta in APEX_DELTA_GRID_SEC:
        for pearson_min in PEARSON_GRID:
            for overlap_min in OVERLAP_GRID:
                rows.append(
                    _threshold_row(
                        cases=cases,
                        calibration_id=calibration_id,
                        apex_delta_max_sec=apex_delta,
                        pearson_min=pearson_min,
                        overlap_min=overlap_min,
                    )
                )
    rows = sorted(rows, key=_recommendation_key)
    for rank, row in enumerate(rows, start=1):
        row["recommendation_rank"] = rank
        row["recommendation_reason"] = (
            "selected_no_false_positive_max_recall_default_anchor"
            if rank == 1
            else ""
        )
    return tuple(rows)


def build_case_audit_rows(
    *,
    cases: Sequence[CalibrationCase],
    recommended_rule: Mapping[str, object],
    calibration_id: str,
) -> tuple[dict[str, object], ...]:
    rule_id = _required_object_text(recommended_rule, "rule_id")
    apex_delta = _required_threshold_float(
        recommended_rule,
        "max_abs_apex_delta_sec",
    )
    pearson_min = _required_threshold_float(
        recommended_rule,
        "min_pearson_r_to_base",
    )
    overlap_min = _required_threshold_float(
        recommended_rule,
        "min_active_overlap_fraction",
    )
    rows: list[dict[str, object]] = []
    for case in cases:
        predicted = (
            "accept"
            if _rule_accepts(
                case,
                apex_delta_max_sec=apex_delta,
                pearson_min=pearson_min,
                overlap_min=overlap_min,
            )
            else "reject"
        )
        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "readiness_tier": READINESS_TIER,
                "evidence_owner": EVIDENCE_OWNER,
                "authority_scope": AUTHORITY_SCOPE,
                "calibration_id": calibration_id,
                "recommended_rule_id": rule_id,
                "packet_row_id": case.packet_row_id,
                "dataset": case.dataset,
                "case_index": case.case_index,
                "sample_stem": case.sample_stem,
                "candidate_id": case.candidate_id,
                "oracle_call": case.oracle_call,
                "oracle_detection_status": case.oracle_detection_status,
                "recommended_prediction": predicted,
                "calibration_outcome": _case_outcome(case.oracle_call, predicted),
                "combined_pt_pattern_status": case.combined_status,
                "non_base_positions_supported": case.non_base_positions_supported,
                "max_abs_apex_delta_sec": case.max_abs_apex_delta_sec,
                "min_pearson_r_to_base": case.min_pearson_r_to_base,
                "min_active_overlap_fraction": case.min_active_overlap_fraction,
                "base_mz": case.base_mz,
                "base_rt_apex_min": case.base_rt_apex_min,
                "machine_call": case.machine_call,
                "machine_call_reason": case.machine_call_reason,
                "human_review_note": case.human_review_note,
            }
        )
    return tuple(rows)


def baseline_machine_call_confusion(
    cases: Sequence[CalibrationCase],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for case in cases:
        prediction, _reason = classify_machine_call(case.review_row)
        counts[f"predicted_{prediction}"] += 1
        if prediction == "uncertain":
            counts[f"uncertain_oracle_{case.oracle_call}"] += 1
        else:
            counts[_case_outcome(case.oracle_call, prediction)] += 1
    return dict(sorted(counts.items()))


def _threshold_row(
    *,
    cases: Sequence[CalibrationCase],
    calibration_id: str,
    apex_delta_max_sec: float,
    pearson_min: float,
    overlap_min: float,
) -> dict[str, object]:
    counts: Counter[str] = Counter()
    for case in cases:
        predicted = (
            "accept"
            if _rule_accepts(
                case,
                apex_delta_max_sec=apex_delta_max_sec,
                pearson_min=pearson_min,
                overlap_min=overlap_min,
            )
            else "reject"
        )
        counts[_case_outcome(case.oracle_call, predicted)] += 1
        counts[f"oracle_{case.oracle_call}"] += 1
        counts[f"predicted_{predicted}"] += 1

    true_positive = counts["true_positive"]
    false_positive = counts["false_positive"]
    true_negative = counts["true_negative"]
    false_negative = counts["false_negative"]
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    specificity = _ratio(true_negative, true_negative + false_positive)
    f1 = (
        None
        if precision is None or recall is None or precision + recall == 0
        else 2 * precision * recall / (precision + recall)
    )
    accuracy = _ratio(
        true_positive + true_negative,
        true_positive + true_negative + false_positive + false_negative,
    )
    balanced_accuracy = (
        None
        if recall is None or specificity is None
        else (recall + specificity) / 2
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "authority_scope": AUTHORITY_SCOPE,
        "calibration_id": calibration_id,
        "rule_id": _rule_id(apex_delta_max_sec, pearson_min, overlap_min),
        "recommendation_rank": "",
        "recommendation_reason": "",
        "required_combined_status": REQUIRED_COMBINED_STATUS,
        "min_non_base_positions_supported": MIN_SUPPORTED_POSITIONS,
        "max_abs_apex_delta_sec": apex_delta_max_sec,
        "min_pearson_r_to_base": pearson_min,
        "min_active_overlap_fraction": overlap_min,
        "row_count": len(cases),
        "oracle_accept_count": counts["oracle_accept"],
        "oracle_reject_count": counts["oracle_reject"],
        "predicted_accept_count": counts["predicted_accept"],
        "predicted_reject_count": counts["predicted_reject"],
        "true_positive": true_positive,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "f1": f1,
        "accuracy": accuracy,
        "balanced_accuracy": balanced_accuracy,
    }


def _rule_accepts(
    case: CalibrationCase,
    *,
    apex_delta_max_sec: float,
    pearson_min: float,
    overlap_min: float,
) -> bool:
    return (
        case.combined_status == REQUIRED_COMBINED_STATUS
        and case.non_base_positions_supported is not None
        and case.non_base_positions_supported >= MIN_SUPPORTED_POSITIONS
        and case.max_abs_apex_delta_sec is not None
        and case.max_abs_apex_delta_sec <= apex_delta_max_sec
        and case.min_pearson_r_to_base is not None
        and case.min_pearson_r_to_base >= pearson_min
        and case.min_active_overlap_fraction is not None
        and case.min_active_overlap_fraction >= overlap_min
    )


def _recommendation_key(row: Mapping[str, object]) -> tuple[float, ...]:
    return (
        _metric(row, "false_positive"),
        -_metric(row, "true_positive"),
        _metric(row, "false_negative"),
        abs(_metric(row, "max_abs_apex_delta_sec") - DEFAULT_APEX_DELTA_MAX_SEC),
        abs(
            _metric(row, "min_active_overlap_fraction")
            - DEFAULT_OVERLAP_MIN
        ),
        abs(_metric(row, "min_pearson_r_to_base") - DEFAULT_PEARSON_MIN),
    )


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


def _calibration_case(
    oracle: Mapping[str, str],
    review: Mapping[str, str],
) -> CalibrationCase:
    oracle_call = _required_text(oracle, "oracle_call").lower()
    if oracle_call not in {"accept", "reject"}:
        raise ValueError(f"unsupported oracle_call: {oracle_call!r}")
    return CalibrationCase(
        packet_row_id=_required_text(oracle, "packet_row_id"),
        dataset=_required_text(oracle, "dataset"),
        case_index=_required_text(oracle, "case_index"),
        sample_stem=_required_text(oracle, "sample_stem"),
        candidate_id=_required_text(oracle, "candidate_id"),
        oracle_call=oracle_call,
        oracle_detection_status=_required_text(oracle, "oracle_detection_status"),
        combined_status=_required_text(review, "combined_pt_pattern_status"),
        non_base_positions_supported=optional_float(
            review.get("non_base_positions_supported")
        ),
        max_abs_apex_delta_sec=optional_float(
            review.get("max_abs_apex_delta_sec")
        ),
        min_pearson_r_to_base=optional_float(review.get("min_pearson_r_to_base")),
        min_active_overlap_fraction=optional_float(
            review.get("min_active_overlap_fraction")
        ),
        base_mz=_required_text(oracle, "base_mz"),
        base_rt_apex_min=_required_text(oracle, "base_rt_apex_min"),
        machine_call=oracle.get("machine_call", ""),
        machine_call_reason=oracle.get("machine_call_reason", ""),
        human_review_note=oracle.get("human_review_note", ""),
        review_row=review,
    )


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


def _summary_rule(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "rule_id": row["rule_id"],
        "required_combined_status": row["required_combined_status"],
        "min_non_base_positions_supported": row[
            "min_non_base_positions_supported"
        ],
        "max_abs_apex_delta_sec": row["max_abs_apex_delta_sec"],
        "min_pearson_r_to_base": row["min_pearson_r_to_base"],
        "min_active_overlap_fraction": row["min_active_overlap_fraction"],
        "true_positive": row["true_positive"],
        "false_positive": row["false_positive"],
        "true_negative": row["true_negative"],
        "false_negative": row["false_negative"],
        "precision": row["precision"],
        "recall": row["recall"],
        "f1": row["f1"],
    }


def _calibration_status(recommended: Mapping[str, object]) -> str:
    if _metric(recommended, "false_positive") == 0 and _metric(
        recommended,
        "true_positive",
    ) > 0:
        return "threshold_candidate_ready_for_review"
    return "no_zero_false_positive_rule_found"


def _rule_id(
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


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _metric(row: Mapping[str, object], key: str) -> float:
    value = optional_float(row.get(key))
    if value is None:
        raise ValueError(f"{key} must be numeric")
    return value


def _required_threshold_float(row: Mapping[str, object], key: str) -> float:
    value = optional_float(row.get(key))
    if value is None:
        raise ValueError(f"{key} must be numeric")
    return value


def _required_object_text(row: Mapping[str, object], key: str) -> str:
    value = str(row.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} must be non-empty")
    return value


def _required_text(row: Mapping[str, str], key: str) -> str:
    value = str(row.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} must be non-empty")
    return value
