from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from xic_extractor.configuration.models import ConfigError, Target
from xic_extractor.configuration.targets import (
    ISOTOPE_LABEL_TYPES,
    PAIRED_RT_RELATIONS,
)
from xic_extractor.rt_prior_library import LibraryEntry
from xic_extractor.tabular_io import write_tsv

TARGET_PAIR_RT_CALIBRATION_SCHEMA_VERSION = "target_pair_rt_calibration"

TARGET_PAIR_RT_CALIBRATION_FIELDS = (
    "schema_version",
    "target_config_hash",
    "source_artifact",
    "source_hash",
    "source_hash_status",
    "target_label",
    "paired_istd_label",
    "pair_rt_delta_min",
    "delta_source",
    "point_count",
    "rt_delta_median_min",
    "rt_delta_mad_min",
    "rt_delta_direction",
    "isotope_label_type",
    "paired_rt_relation",
    "calibration_status",
    "calibration_level",
    "product_transfer_status",
)

SOURCE_HASH_STATUSES = frozenset({"present", "missing", "mismatch"})
DELTA_SOURCES = frozenset(
    {"mixstds_clean_standard", "biological_high_confidence", "config_fallback"}
)
RT_DELTA_DIRECTIONS = frozenset({"target_later", "target_earlier", "near_zero"})
CALIBRATION_STATUSES = frozenset(
    {"usable", "insufficient", "conflicting", "review_only"}
)
CALIBRATION_LEVELS = frozenset(
    {"clean_standard_only", "biological_transfer", "row_approved", "config_only"}
)
PRODUCT_TRANSFER_STATUSES = frozenset(
    {"not_assessed", "validated", "row_approved", "blocked"}
)


@dataclass(frozen=True)
class TargetPairRTCalibrationRow:
    schema_version: str
    target_config_hash: str
    source_artifact: str
    source_hash: str
    source_hash_status: str
    target_label: str
    paired_istd_label: str
    pair_rt_delta_min: float
    delta_source: str
    point_count: int
    rt_delta_median_min: float
    rt_delta_mad_min: float | None
    rt_delta_direction: str
    isotope_label_type: str
    paired_rt_relation: str
    calibration_status: str
    calibration_level: str
    product_transfer_status: str
    target_hash_status: str = "not_checked"
    activation_block_reason: str = ""


def load_target_pair_rt_calibration(
    path: Path,
    *,
    expected_target_config_hash: str | None = None,
) -> tuple[TargetPairRTCalibrationRow, ...]:
    if not path.exists():
        raise ConfigError(f"{path}: file is missing")
    rows: list[TargetPairRTCalibrationRow] = []
    seen: set[tuple[str, str]] = set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        _require_columns(path, reader.fieldnames)
        for row_number, raw in enumerate(reader, start=2):
            row = _parse_calibration_row(
                path,
                row_number,
                raw,
                expected_target_config_hash=expected_target_config_hash,
            )
            key = (row.target_label, row.paired_istd_label)
            if key in seen:
                raise ConfigError(
                    f"{path}: row {row_number} duplicate "
                    "(target_label, paired_istd_label)"
                )
            seen.add(key)
            rows.append(row)
    return tuple(rows)


def rt_prior_library_from_target_pair_calibration(
    rows: Iterable[TargetPairRTCalibrationRow],
) -> dict[tuple[str, str], LibraryEntry]:
    """Expose activated target-pair calibration rows as analyte RT priors."""
    out: dict[tuple[str, str], LibraryEntry] = {}
    for row in rows:
        if row.activation_block_reason:
            continue
        out[(row.target_label, "analyte")] = LibraryEntry(
            config_hash=row.target_config_hash,
            target_label=row.target_label,
            role="analyte",
            istd_pair=row.paired_istd_label,
            median_delta_rt=row.pair_rt_delta_min,
            sigma_delta_rt=row.rt_delta_mad_min,
            median_abs_rt=None,
            sigma_abs_rt=None,
            n_samples=row.point_count,
            updated_at=row.source_artifact,
        )
    return out


def write_target_pair_rt_calibration_tsv(
    path: Path,
    rows: Sequence[TargetPairRTCalibrationRow],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(
        path,
        tuple(_calibration_row_to_dict(row) for row in rows),
        TARGET_PAIR_RT_CALIBRATION_FIELDS,
        encoding="utf-8-sig",
    )


def calibration_rows_from_rt_prior_library(
    entries: Mapping[tuple[str, str], LibraryEntry],
    *,
    targets: Iterable[Target],
    target_config_hash: str,
    source_artifact: str,
    source_hash: str = "",
) -> tuple[TargetPairRTCalibrationRow, ...]:
    targets_by_label = {target.label: target for target in targets}
    rows: list[TargetPairRTCalibrationRow] = []
    for entry in entries.values():
        if entry.role.lower() != "analyte" or entry.median_delta_rt is None:
            continue
        target = targets_by_label.get(entry.target_label)
        paired_istd = targets_by_label.get(entry.istd_pair)
        rows.append(
            TargetPairRTCalibrationRow(
                schema_version=TARGET_PAIR_RT_CALIBRATION_SCHEMA_VERSION,
                target_config_hash=target_config_hash,
                source_artifact=source_artifact,
                source_hash=source_hash,
                source_hash_status="present" if source_hash else "missing",
                target_label=entry.target_label,
                paired_istd_label=entry.istd_pair,
                pair_rt_delta_min=entry.median_delta_rt,
                delta_source="biological_high_confidence",
                point_count=entry.n_samples,
                rt_delta_median_min=entry.median_delta_rt,
                rt_delta_mad_min=entry.sigma_delta_rt,
                rt_delta_direction=_delta_direction(entry.median_delta_rt),
                isotope_label_type=(
                    paired_istd.isotope_label_type
                    if paired_istd is not None
                    else "unknown"
                ),
                paired_rt_relation=(
                    target.paired_rt_relation if target is not None else "none"
                ),
                calibration_status="usable"
                if entry.n_samples > 0
                else "insufficient",
                calibration_level="biological_transfer",
                product_transfer_status="not_assessed",
                activation_block_reason=(
                    "" if source_hash else "missing_source_hash"
                ),
            )
        )
    return tuple(rows)


def _require_columns(path: Path, fieldnames: Sequence[str] | None) -> None:
    available = set(fieldnames or ())
    for field in TARGET_PAIR_RT_CALIBRATION_FIELDS:
        if field not in available:
            raise ConfigError(f"{path}: missing required column {field}")


def _parse_calibration_row(
    path: Path,
    row_number: int,
    row: Mapping[str, str],
    *,
    expected_target_config_hash: str | None,
) -> TargetPairRTCalibrationRow:
    values = {
        field: str(row.get(field, "")).strip()
        for field in TARGET_PAIR_RT_CALIBRATION_FIELDS
    }
    _require_literal(
        path,
        row_number,
        "schema_version",
        values["schema_version"],
        {TARGET_PAIR_RT_CALIBRATION_SCHEMA_VERSION},
    )
    source_hash_status = _require_literal(
        path,
        row_number,
        "source_hash_status",
        values["source_hash_status"],
        SOURCE_HASH_STATUSES,
    )
    _validate_source_hash(path, row_number, values["source_hash"], source_hash_status)
    parsed = TargetPairRTCalibrationRow(
        schema_version=values["schema_version"],
        target_config_hash=_require_nonblank(
            path, row_number, "target_config_hash", values["target_config_hash"]
        ),
        source_artifact=_require_nonblank(
            path, row_number, "source_artifact", values["source_artifact"]
        ),
        source_hash=values["source_hash"],
        source_hash_status=source_hash_status,
        target_label=_require_nonblank(
            path, row_number, "target_label", values["target_label"]
        ),
        paired_istd_label=_require_nonblank(
            path, row_number, "paired_istd_label", values["paired_istd_label"]
        ),
        pair_rt_delta_min=_parse_float(
            path, row_number, "pair_rt_delta_min", values["pair_rt_delta_min"]
        ),
        delta_source=_require_literal(
            path,
            row_number,
            "delta_source",
            values["delta_source"],
            DELTA_SOURCES,
        ),
        point_count=_parse_int(
            path, row_number, "point_count", values["point_count"]
        ),
        rt_delta_median_min=_parse_float(
            path,
            row_number,
            "rt_delta_median_min",
            values["rt_delta_median_min"],
        ),
        rt_delta_mad_min=_parse_optional_float(
            path,
            row_number,
            "rt_delta_mad_min",
            values["rt_delta_mad_min"],
        ),
        rt_delta_direction=_require_literal(
            path,
            row_number,
            "rt_delta_direction",
            values["rt_delta_direction"],
            RT_DELTA_DIRECTIONS,
        ),
        isotope_label_type=_require_literal(
            path,
            row_number,
            "isotope_label_type",
            values["isotope_label_type"] or "unknown",
            ISOTOPE_LABEL_TYPES,
        ),
        paired_rt_relation=_require_literal(
            path,
            row_number,
            "paired_rt_relation",
            values["paired_rt_relation"] or "none",
            PAIRED_RT_RELATIONS,
        ),
        calibration_status=_require_literal(
            path,
            row_number,
            "calibration_status",
            values["calibration_status"],
            CALIBRATION_STATUSES,
        ),
        calibration_level=_require_literal(
            path,
            row_number,
            "calibration_level",
            values["calibration_level"],
            CALIBRATION_LEVELS,
        ),
        product_transfer_status=_require_literal(
            path,
            row_number,
            "product_transfer_status",
            values["product_transfer_status"],
            PRODUCT_TRANSFER_STATUSES,
        ),
    )
    return _with_blocking_status(
        parsed,
        expected_target_config_hash=expected_target_config_hash,
    )


def _with_blocking_status(
    row: TargetPairRTCalibrationRow,
    *,
    expected_target_config_hash: str | None,
) -> TargetPairRTCalibrationRow:
    target_hash_status = "not_checked"
    reasons: list[str] = []
    if expected_target_config_hash is not None:
        target_hash_status = (
            "match"
            if row.target_config_hash == expected_target_config_hash
            else "mismatch"
        )
        if target_hash_status == "mismatch":
            reasons.append("target_config_hash_mismatch")
    if row.source_hash_status == "mismatch":
        reasons.append("source_hash_mismatch")
    elif row.source_hash_status == "missing":
        reasons.append("missing_source_hash")
    if row.calibration_status != "usable":
        reasons.append(f"calibration_status:{row.calibration_status}")
    if row.product_transfer_status not in {"validated", "row_approved"}:
        reasons.append(f"product_transfer_status:{row.product_transfer_status}")
    return replace(
        row,
        target_hash_status=target_hash_status,
        activation_block_reason=";".join(dict.fromkeys(reasons)),
    )


def _calibration_row_to_dict(row: TargetPairRTCalibrationRow) -> dict[str, str]:
    return {
        "schema_version": row.schema_version,
        "target_config_hash": row.target_config_hash,
        "source_artifact": row.source_artifact,
        "source_hash": row.source_hash,
        "source_hash_status": row.source_hash_status,
        "target_label": row.target_label,
        "paired_istd_label": row.paired_istd_label,
        "pair_rt_delta_min": _format_float(row.pair_rt_delta_min),
        "delta_source": row.delta_source,
        "point_count": str(row.point_count),
        "rt_delta_median_min": _format_float(row.rt_delta_median_min),
        "rt_delta_mad_min": (
            "" if row.rt_delta_mad_min is None else _format_float(row.rt_delta_mad_min)
        ),
        "rt_delta_direction": row.rt_delta_direction,
        "isotope_label_type": row.isotope_label_type,
        "paired_rt_relation": row.paired_rt_relation,
        "calibration_status": row.calibration_status,
        "calibration_level": row.calibration_level,
        "product_transfer_status": row.product_transfer_status,
    }


def _require_nonblank(path: Path, row_number: int, field: str, value: str) -> str:
    if not value:
        raise ConfigError(f"{path}: row {row_number} column {field} must not be empty")
    return value


def _require_literal(
    path: Path,
    row_number: int,
    field: str,
    value: str,
    allowed: frozenset[str] | set[str],
) -> str:
    normalized = value.strip().lower()
    if normalized not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ConfigError(
            f"{path}: row {row_number} column {field}={value!r} "
            f"must be one of {allowed_text}"
        )
    return normalized


def _validate_source_hash(
    path: Path,
    row_number: int,
    source_hash: str,
    source_hash_status: str,
) -> None:
    if source_hash_status == "missing":
        if source_hash:
            raise ConfigError(
                f"{path}: row {row_number} source_hash must be empty when "
                "source_hash_status=missing"
            )
        return
    if not source_hash:
        raise ConfigError(
            f"{path}: row {row_number} source_hash must not be empty when "
            f"source_hash_status={source_hash_status}"
        )


def _parse_float(path: Path, row_number: int, field: str, value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(
            f"{path}: row {row_number} column {field}={value!r} must be numeric"
        ) from exc


def _parse_optional_float(
    path: Path,
    row_number: int,
    field: str,
    value: str,
) -> float | None:
    if not value:
        return None
    return _parse_float(path, row_number, field, value)


def _parse_int(path: Path, row_number: int, field: str, value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(
            f"{path}: row {row_number} column {field}={value!r} must be an integer"
        ) from exc
    if parsed < 0:
        raise ConfigError(f"{path}: row {row_number} column {field} must be >= 0")
    return parsed


def _delta_direction(delta: float) -> str:
    if abs(delta) < 1e-9:
        return "near_zero"
    return "target_later" if delta > 0 else "target_earlier"


def _format_float(value: float) -> str:
    return f"{value:.6f}"
