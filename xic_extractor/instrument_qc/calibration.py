from __future__ import annotations

import csv
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from xic_extractor.injection_rolling import read_injection_order

MIN_BATCH_ROWS = 3

SDOLEK_TREND_REQUIRED_COLUMNS = {
    "sample_name",
    "compound",
    "identity_evidence",
    "status",
    "apex_rt_min",
    "area",
    "base_width_min",
    "reference_rt_min",
    "rt_delta_to_reference_min",
    "reference_base_width_min",
    "base_width_ratio_to_reference",
}


@dataclass(frozen=True)
class SDOLEKTrendRow:
    sample_name: str
    compound: str
    identity_evidence: str
    status: str
    apex_rt_min: float | None
    area: float | None
    base_width_min: float | None
    reference_rt_min: float | None
    rt_delta_to_reference_min: float | None
    reference_base_width_min: float | None
    base_width_ratio_to_reference: float | None


@dataclass(frozen=True)
class CalibrationMetadataStatus:
    injection_order_source: str
    injection_order_status: str
    source_contract: str
    matched_injection_order_rows: int
    unmatched_injection_order_rows: int
    reason: str


@dataclass(frozen=True)
class CalibratedSDOLEKTrendRow:
    sample_name: str
    compound: str
    identity_evidence: str
    injection_order: int | None
    status: str
    apex_rt_min: float | None
    area: float | None
    base_width_min: float | None
    reference_rt_min: float | None
    rt_delta_to_reference_min: float | None
    reference_base_width_min: float | None
    base_width_ratio_to_reference: float | None
    compound_batch_median_rt_min: float | None
    rt_delta_to_batch_median_min: float | None
    compound_batch_median_area: float | None
    log2_area_ratio_to_batch_median: float | None
    compound_batch_median_width_min: float | None
    width_ratio_to_batch_median: float | None
    prior_conflict_flags: tuple[str, ...]
    batch_trend_flags: tuple[str, ...]
    review_bucket: str
    review_reason: str


@dataclass(frozen=True)
class SDOLEKCalibrationResult:
    rows: tuple[CalibratedSDOLEKTrendRow, ...]
    metadata_source_status: dict[str, str]
    calibration_metadata_status: CalibrationMetadataStatus


def load_sdolek_trend_rows(path: Path) -> tuple[SDOLEKTrendRow, ...]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = set(reader.fieldnames or ())
        missing = sorted(SDOLEK_TREND_REQUIRED_COLUMNS - fieldnames)
        if missing:
            raise ValueError(
                "Missing required SDOLEK trend columns: " + ", ".join(missing)
            )
        return tuple(_sdolek_row_from_dict(row) for row in reader)


def load_metadata_source_status(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    status = payload.get("metadata_source_status", {})
    if not isinstance(status, dict):
        return {}
    return {str(key): str(value) for key, value in status.items()}


def calibrate_sdolek_rows(
    trend_rows: Iterable[SDOLEKTrendRow],
    *,
    metadata_source_status: dict[str, str] | None = None,
    injection_order_source: Path | None = None,
) -> SDOLEKCalibrationResult:
    row_list = tuple(trend_rows)
    injection_order, metadata_status = _read_docs_injection_order(
        injection_order_source,
        row_list,
    )
    medians = _compound_medians(row_list)
    calibrated = tuple(
        _calibrate_row(
            row,
            injection_order=injection_order.get(row.sample_name),
            medians=medians,
        )
        for row in row_list
    )
    return SDOLEKCalibrationResult(
        rows=calibrated,
        metadata_source_status=metadata_source_status or {},
        calibration_metadata_status=metadata_status,
    )


def _sdolek_row_from_dict(row: dict[str, str]) -> SDOLEKTrendRow:
    return SDOLEKTrendRow(
        sample_name=row["sample_name"],
        compound=row["compound"],
        identity_evidence=row["identity_evidence"],
        status=row["status"],
        apex_rt_min=_optional_float(row["apex_rt_min"]),
        area=_optional_float(row["area"]),
        base_width_min=_optional_float(row["base_width_min"]),
        reference_rt_min=_optional_float(row["reference_rt_min"]),
        rt_delta_to_reference_min=_optional_float(row["rt_delta_to_reference_min"]),
        reference_base_width_min=_optional_float(row["reference_base_width_min"]),
        base_width_ratio_to_reference=_optional_float(
            row["base_width_ratio_to_reference"]
        ),
    )


def _read_docs_injection_order(
    path: Path | None,
    rows: tuple[SDOLEKTrendRow, ...],
) -> tuple[dict[str, int], CalibrationMetadataStatus]:
    if path is None:
        return {}, CalibrationMetadataStatus(
            injection_order_source="",
            injection_order_status="missing",
            source_contract="method_docs_only",
            matched_injection_order_rows=0,
            unmatched_injection_order_rows=len(rows),
            reason="No docs-derived injection-order source supplied.",
        )
    if path.name.casefold().startswith("sampleinfo"):
        return {}, CalibrationMetadataStatus(
            injection_order_source=str(path),
            injection_order_status="invalid",
            source_contract="method_docs_only",
            matched_injection_order_rows=0,
            unmatched_injection_order_rows=len(rows),
            reason="SampleInfo is downstream evidence, not a method-doc source.",
        )
    injection_order = read_injection_order(path)
    matched = sum(1 for row in rows if row.sample_name in injection_order)
    unmatched = len(rows) - matched
    status = "provided" if unmatched == 0 else "partial_match"
    reason = (
        "Docs-derived injection-order source matched all rows."
        if unmatched == 0
        else "Docs-derived injection-order source did not match every row."
    )
    return injection_order, CalibrationMetadataStatus(
        injection_order_source=str(path),
        injection_order_status=status,
        source_contract="method_docs_only",
        matched_injection_order_rows=matched,
        unmatched_injection_order_rows=unmatched,
        reason=reason,
    )


def _compound_medians(
    rows: tuple[SDOLEKTrendRow, ...],
) -> dict[str, tuple[float, float, float] | None]:
    compounds = sorted({row.compound for row in rows})
    medians: dict[str, tuple[float, float, float] | None] = {}
    for compound in compounds:
        detected = [
            row
            for row in rows
            if row.compound == compound
            and row.status == "detected"
            and row.apex_rt_min is not None
            and row.area is not None
            and row.base_width_min is not None
        ]
        if len(detected) < MIN_BATCH_ROWS:
            medians[compound] = None
            continue
        rt_values = [row.apex_rt_min for row in detected if row.apex_rt_min is not None]
        area_values = [row.area for row in detected if row.area is not None]
        width_values = [
            row.base_width_min for row in detected if row.base_width_min is not None
        ]
        medians[compound] = (
            float(statistics.median(rt_values)),
            float(statistics.median(area_values)),
            float(statistics.median(width_values)),
        )
    return medians


def _calibrate_row(
    row: SDOLEKTrendRow,
    *,
    injection_order: int | None,
    medians: dict[str, tuple[float, float, float] | None],
) -> CalibratedSDOLEKTrendRow:
    median_values = medians.get(row.compound)
    median_rt = median_area = median_width = None
    rt_delta = log2_area_ratio = width_ratio = None
    if median_values is not None:
        median_rt, median_area, median_width = median_values
        if row.apex_rt_min is not None:
            rt_delta = row.apex_rt_min - median_rt
        if row.area is not None and median_area > 0 and row.area > 0:
            log2_area_ratio = math.log2(row.area / median_area)
        if row.base_width_min is not None and median_width > 0:
            width_ratio = row.base_width_min / median_width

    prior_flags = _prior_conflict_flags(row)
    batch_flags = _batch_trend_flags(
        rt_delta=rt_delta,
        log2_area_ratio=log2_area_ratio,
        width_ratio=width_ratio,
    )
    bucket, reason = _review_bucket(
        row,
        prior_flags=prior_flags,
        batch_flags=batch_flags,
        median_values=median_values,
        injection_order=injection_order,
    )
    return CalibratedSDOLEKTrendRow(
        sample_name=row.sample_name,
        compound=row.compound,
        identity_evidence=row.identity_evidence,
        injection_order=injection_order,
        status=row.status,
        apex_rt_min=row.apex_rt_min,
        area=row.area,
        base_width_min=row.base_width_min,
        reference_rt_min=row.reference_rt_min,
        rt_delta_to_reference_min=row.rt_delta_to_reference_min,
        reference_base_width_min=row.reference_base_width_min,
        base_width_ratio_to_reference=row.base_width_ratio_to_reference,
        compound_batch_median_rt_min=median_rt,
        rt_delta_to_batch_median_min=rt_delta,
        compound_batch_median_area=median_area,
        log2_area_ratio_to_batch_median=log2_area_ratio,
        compound_batch_median_width_min=median_width,
        width_ratio_to_batch_median=width_ratio,
        prior_conflict_flags=prior_flags,
        batch_trend_flags=batch_flags,
        review_bucket=bucket,
        review_reason=reason,
    )


def _prior_conflict_flags(row: SDOLEKTrendRow) -> tuple[str, ...]:
    flags: list[str] = []
    if (
        row.rt_delta_to_reference_min is not None
        and abs(row.rt_delta_to_reference_min) > 0.50
    ):
        flags.append("PRIOR_RT_SHIFT")
    width_ratio = row.base_width_ratio_to_reference
    if width_ratio is not None and (width_ratio < 0.50 or width_ratio > 1.75):
        flags.append("PRIOR_WIDTH_SHIFT")
    return tuple(flags)


def _batch_trend_flags(
    *,
    rt_delta: float | None,
    log2_area_ratio: float | None,
    width_ratio: float | None,
) -> tuple[str, ...]:
    flags: list[str] = []
    if rt_delta is not None and abs(rt_delta) > 0.20:
        flags.append("BATCH_RT_OUTLIER")
    if log2_area_ratio is not None:
        if log2_area_ratio < -1.0:
            flags.append("BATCH_AREA_DROP")
        elif log2_area_ratio > 1.0:
            flags.append("BATCH_AREA_RISE")
    if width_ratio is not None and (width_ratio < 0.60 or width_ratio > 1.60):
        flags.append("BATCH_WIDTH_OUTLIER")
    return tuple(flags)


def _review_bucket(
    row: SDOLEKTrendRow,
    *,
    prior_flags: tuple[str, ...],
    batch_flags: tuple[str, ...],
    median_values: tuple[float, float, float] | None,
    injection_order: int | None,
) -> tuple[str, str]:
    if row.status != "detected":
        return "not_detected_or_error", "SDOLEK trend row was not detected."
    if median_values is None:
        return (
            "identity_evidence_insufficient",
            "Fewer than 3 detected rows; batch-relative trend not interpreted.",
        )
    if "BATCH_RT_OUTLIER" in batch_flags and injection_order is not None:
        return "rt_drift_review", "RT deviates from compound batch median."
    if "BATCH_AREA_DROP" in batch_flags or "BATCH_AREA_RISE" in batch_flags:
        return "sensitivity_trend_review", "Area ratio deviates from batch median."
    if "BATCH_WIDTH_OUTLIER" in batch_flags:
        return "width_definition_review", "Width deviates from compound batch median."
    if "PRIOR_RT_SHIFT" in prior_flags:
        return (
            "prior_reference_mismatch",
            "NoSplit prior RT disagrees while batch-relative trend is stable.",
        )
    if "PRIOR_WIDTH_SHIFT" in prior_flags:
        return (
            "width_definition_review",
            "NoSplit prior width and SDOLEK integration width are not comparable.",
        )
    return "stable_ms1_trend", "Detected row is stable relative to batch medians."


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
