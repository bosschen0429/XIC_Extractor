from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from xic_extractor.instrument_qc.calibration import (
    CalibratedSDOLEKTrendRow,
    CalibrationMetadataStatus,
    SDOLEKCalibrationResult,
)
from xic_extractor.tabular_io import write_tsv

CALIBRATED_TREND_TSV_COLUMNS = [
    "sample_name",
    "compound",
    "identity_evidence",
    "injection_order",
    "status",
    "apex_rt_min",
    "area",
    "base_width_min",
    "reference_rt_min",
    "rt_delta_to_reference_min",
    "reference_base_width_min",
    "base_width_ratio_to_reference",
    "compound_batch_median_rt_min",
    "rt_delta_to_batch_median_min",
    "compound_batch_median_area",
    "log2_area_ratio_to_batch_median",
    "compound_batch_median_width_min",
    "width_ratio_to_batch_median",
    "prior_conflict_flags",
    "batch_trend_flags",
    "review_bucket",
    "review_reason",
]


def write_calibrated_trend_tsv(
    path: Path,
    rows: Iterable[CalibratedSDOLEKTrendRow],
) -> None:
    row_list = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(
        path,
        tuple(_calibrated_row_to_dict(row) for row in row_list),
        CALIBRATED_TREND_TSV_COLUMNS,
        formatter=str,
    )


def write_calibration_summary_json(
    path: Path,
    result: SDOLEKCalibrationResult,
) -> None:
    payload = {
        "verdict": calibration_verdict(result),
        "summary": calibration_summary(result.rows),
        "metadata_source_status": result.metadata_source_status,
        "calibration_metadata_status": _metadata_status_to_dict(
            result.calibration_metadata_status
        ),
        "rows": [_calibrated_row_to_dict(row) for row in result.rows],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_calibration_review_markdown(
    path: Path,
    result: SDOLEKCalibrationResult,
    *,
    top_n: int = 12,
) -> None:
    metadata = result.calibration_metadata_status
    status_counts = _counts(row.status for row in result.rows)
    compound_counts = _counts(row.compound for row in result.rows)
    lines = [
        "# Instrument QC SDOLEK Calibration Review",
        "",
        f"- verdict: `{calibration_verdict(result)}`",
        "- identity evidence: `MS1_ONLY` "
        "(trend evidence, not chemical identity confirmation)",
        f"- total rows: {len(result.rows)}",
        f"- detected rows: {status_counts.get('detected', 0)}",
        f"- SDO rows: {compound_counts.get('SDO', 0)}",
        f"- LEK rows: {compound_counts.get('LEK', 0)}",
        f"- injection order status: `{metadata.injection_order_status}`",
        f"- matched injection-order rows: {metadata.matched_injection_order_rows}",
        f"- unmatched injection-order rows: {metadata.unmatched_injection_order_rows}",
        f"- metadata note: {metadata.reason}",
        "",
        "## Top Observations",
        "",
        f"- top RT observation: {top_rt_observation(result.rows)}",
        f"- top area observation: {top_area_observation(result.rows)}",
        f"- top prior observation: {top_prior_observation(result.rows)}",
        "",
        "## Top Review Rows",
        "",
    ]
    review_rows = top_review_rows(result.rows, top_n=top_n)
    if not review_rows:
        lines.append("No review rows.")
    else:
        lines.append(
            "| sample | compound | order | bucket | "
            "prior flags | batch flags | reason |"
        )
        lines.append("|---|---|---:|---|---|---|---|")
        for row in review_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _escape_md(row.sample_name),
                        _escape_md(row.compound),
                        "" if row.injection_order is None else str(row.injection_order),
                        _escape_md(row.review_bucket),
                        _escape_md(";".join(row.prior_conflict_flags)),
                        _escape_md(";".join(row.batch_trend_flags)),
                        _escape_md(row.review_reason),
                    ]
                )
                + " |"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def calibration_verdict(result: SDOLEKCalibrationResult) -> str:
    if not result.rows:
        return "insufficient_evidence"
    status = result.calibration_metadata_status.injection_order_status
    if status == "invalid":
        return "metadata_incomplete"
    detected = [row for row in result.rows if row.status == "detected"]
    if len(detected) < 3:
        return "insufficient_evidence"
    return "review_ready"


def calibration_summary(
    rows: Iterable[CalibratedSDOLEKTrendRow],
) -> dict[str, object]:
    row_list = list(rows)
    return {
        "total_rows": len(row_list),
        "status_counts": _counts(row.status for row in row_list),
        "compound_counts": _counts(row.compound for row in row_list),
        "review_bucket_counts": _counts(row.review_bucket for row in row_list),
        "prior_flag_counts": _counts(
            flag for row in row_list for flag in row.prior_conflict_flags
        ),
        "batch_flag_counts": _counts(
            flag for row in row_list for flag in row.batch_trend_flags
        ),
    }


def top_review_rows(
    rows: Iterable[CalibratedSDOLEKTrendRow],
    *,
    top_n: int,
) -> list[CalibratedSDOLEKTrendRow]:
    priority = {
        "not_detected_or_error": 0,
        "identity_evidence_insufficient": 1,
        "rt_drift_review": 2,
        "sensitivity_trend_review": 3,
        "width_definition_review": 4,
        "prior_reference_mismatch": 5,
        "stable_ms1_trend": 6,
    }
    review_rows = [
        row for row in rows if row.review_bucket != "stable_ms1_trend"
    ]
    return sorted(
        review_rows,
        key=lambda row: (
            priority.get(row.review_bucket, 99),
            row.injection_order is None,
            row.compound,
            row.sample_name,
        ),
    )[:top_n]


def top_rt_observation(rows: Iterable[CalibratedSDOLEKTrendRow]) -> str:
    rt_rows = [
        row for row in rows if row.rt_delta_to_batch_median_min is not None
    ]
    if not rt_rows:
        return "batch RT evidence unavailable"
    row = max(rt_rows, key=lambda item: abs(item.rt_delta_to_batch_median_min or 0.0))
    return (
        f"{row.sample_name} {row.compound} has RT delta "
        f"{row.rt_delta_to_batch_median_min:.3f} min to batch median"
    )


def top_area_observation(rows: Iterable[CalibratedSDOLEKTrendRow]) -> str:
    area_rows = [
        row for row in rows if row.log2_area_ratio_to_batch_median is not None
    ]
    if not area_rows:
        return "batch area evidence unavailable"
    row = max(
        area_rows,
        key=lambda item: abs(item.log2_area_ratio_to_batch_median or 0.0),
    )
    return (
        f"{row.sample_name} {row.compound} has log2 area ratio "
        f"{row.log2_area_ratio_to_batch_median:.3f} to batch median"
    )


def top_prior_observation(rows: Iterable[CalibratedSDOLEKTrendRow]) -> str:
    flagged = [row for row in rows if row.prior_conflict_flags]
    if not flagged:
        return "NoSplit prior comparison has no review flags"
    flag_counts = _counts(flag for row in flagged for flag in row.prior_conflict_flags)
    return ", ".join(f"{flag}={count}" for flag, count in sorted(flag_counts.items()))


def _calibrated_row_to_dict(row: CalibratedSDOLEKTrendRow) -> dict[str, object]:
    return {
        "sample_name": row.sample_name,
        "compound": row.compound,
        "identity_evidence": row.identity_evidence,
        "injection_order": "" if row.injection_order is None else row.injection_order,
        "status": row.status,
        "apex_rt_min": _optional_number(row.apex_rt_min),
        "area": _optional_number(row.area),
        "base_width_min": _optional_number(row.base_width_min),
        "reference_rt_min": _optional_number(row.reference_rt_min),
        "rt_delta_to_reference_min": _optional_number(row.rt_delta_to_reference_min),
        "reference_base_width_min": _optional_number(row.reference_base_width_min),
        "base_width_ratio_to_reference": _optional_number(
            row.base_width_ratio_to_reference
        ),
        "compound_batch_median_rt_min": _optional_number(
            row.compound_batch_median_rt_min
        ),
        "rt_delta_to_batch_median_min": _optional_number(
            row.rt_delta_to_batch_median_min
        ),
        "compound_batch_median_area": _optional_number(
            row.compound_batch_median_area
        ),
        "log2_area_ratio_to_batch_median": _optional_number(
            row.log2_area_ratio_to_batch_median
        ),
        "compound_batch_median_width_min": _optional_number(
            row.compound_batch_median_width_min
        ),
        "width_ratio_to_batch_median": _optional_number(
            row.width_ratio_to_batch_median
        ),
        "prior_conflict_flags": ";".join(row.prior_conflict_flags),
        "batch_trend_flags": ";".join(row.batch_trend_flags),
        "review_bucket": row.review_bucket,
        "review_reason": row.review_reason,
    }


def _metadata_status_to_dict(status: CalibrationMetadataStatus) -> dict[str, object]:
    return {
        "injection_order_source": status.injection_order_source,
        "injection_order_status": status.injection_order_status,
        "source_contract": status.source_contract,
        "matched_injection_order_rows": status.matched_injection_order_rows,
        "unmatched_injection_order_rows": status.unmatched_injection_order_rows,
        "reason": status.reason,
    }


def _optional_number(value: float | None) -> object:
    if value is None:
        return ""
    return value


def _counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _escape_md(value: str) -> str:
    return value.replace("|", "\\|")
