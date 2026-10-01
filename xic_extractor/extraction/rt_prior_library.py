from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.configuration.models import Target
from xic_extractor.tabular_io import write_delimited_rows
from xic_extractor.target_pair_rt_calibration import (
    TARGET_PAIR_RT_CALIBRATION_SCHEMA_VERSION,
    TargetPairRTCalibrationRow,
)

LIBRARY_FIELDNAMES = (
    "config_hash",
    "target_label",
    "role",
    "istd_pair",
    "median_delta_rt",
    "sigma_delta_rt",
    "median_abs_rt",
    "sigma_abs_rt",
    "n_samples",
    "updated_at",
)


@dataclass(frozen=True)
class LibraryEntry:
    config_hash: str
    target_label: str
    role: str
    istd_pair: str
    median_delta_rt: float | None
    sigma_delta_rt: float | None
    median_abs_rt: float | None
    sigma_abs_rt: float | None
    n_samples: int
    updated_at: str


def load_library(path: Path, config_hash: str) -> dict[tuple[str, str], LibraryEntry]:
    if not path.exists():
        return {}
    out: dict[tuple[str, str], LibraryEntry] = {}
    with path.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("config_hash") != config_hash:
                continue
            entry = LibraryEntry(
                config_hash=row["config_hash"],
                target_label=row["target_label"],
                role=row["role"],
                istd_pair=row.get("istd_pair") or "",
                median_delta_rt=_opt_float(row.get("median_delta_rt")),
                sigma_delta_rt=_opt_float(row.get("sigma_delta_rt")),
                median_abs_rt=_opt_float(row.get("median_abs_rt")),
                sigma_abs_rt=_opt_float(row.get("sigma_abs_rt")),
                n_samples=int((row.get("n_samples") or "0").strip() or 0),
                updated_at=row.get("updated_at") or "",
            )
            out[(entry.target_label, entry.role)] = entry
    return out


def write_pending_update(library_path: Path, entries: list[LibraryEntry]) -> Path:
    """Write proposed library rows without mutating the main library."""
    pending = library_path.with_suffix(".pending.csv")
    pending.parent.mkdir(parents=True, exist_ok=True)
    write_delimited_rows(
        pending,
        [_entry_row(entry) for entry in entries],
        LIBRARY_FIELDNAMES,
    )
    return pending


def _entry_row(entry: LibraryEntry) -> dict[str, str]:
    return {
        "config_hash": entry.config_hash,
        "target_label": entry.target_label,
        "role": entry.role,
        "istd_pair": entry.istd_pair,
        "median_delta_rt": _format_optional_float(entry.median_delta_rt),
        "sigma_delta_rt": _format_optional_float(entry.sigma_delta_rt),
        "median_abs_rt": _format_optional_float(entry.median_abs_rt),
        "sigma_abs_rt": _format_optional_float(entry.sigma_abs_rt),
        "n_samples": str(entry.n_samples),
        "updated_at": entry.updated_at,
    }


def _opt_float(value: str | None) -> float | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized == "":
        return None
    return float(normalized)


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"


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



def _delta_direction(delta: float) -> str:
    if abs(delta) < 1e-9:
        return "near_zero"
    return "target_later" if delta > 0 else "target_earlier"
