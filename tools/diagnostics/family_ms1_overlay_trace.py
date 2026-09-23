"""Trace loading helpers for legacy family-id peak-group MS1 overlays."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from tools.diagnostics.family_ms1_overlay_models import (
    REQUIRED_ALIGNMENT_COLUMNS,
    FamilyCell,
    TraceOverlayRow,
)


def load_family_cells(alignment_cells: Path, family_id: str) -> list[FamilyCell]:
    families = load_family_cells_for_families(alignment_cells, (family_id,))
    rows = families.get(family_id, [])
    if not rows:
        raise ValueError(f"No alignment cells found for family `{family_id}`")
    return rows


def load_family_cells_for_families(
    alignment_cells: Path,
    family_ids: Sequence[str],
) -> dict[str, list[FamilyCell]]:
    requested = {family_id for family_id in family_ids if family_id}
    if not requested:
        return {}
    rows_by_family: dict[str, list[FamilyCell]] = {
        family_id: [] for family_id in requested
    }
    with alignment_cells.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        _require_columns(reader.fieldnames or (), REQUIRED_ALIGNMENT_COLUMNS)
        for row in reader:
            family_id = row.get("feature_family_id", "")
            if family_id not in requested:
                continue
            rows_by_family[family_id].append(
                FamilyCell(
                    sample_stem=row["sample_stem"],
                    status=row["status"],
                    area=_parse_float(row.get("area")),
                    height=_parse_float(row.get("height")),
                    apex_rt=_parse_float(row.get("apex_rt")),
                    peak_start_rt=_parse_float(row.get("peak_start_rt")),
                    peak_end_rt=_parse_float(row.get("peak_end_rt")),
                    region_shadow_verdict=row.get("region_shadow_verdict", ""),
                    source_candidate_id=row.get("source_candidate_id", ""),
                )
            )
    return {
        family_id: sorted(rows, key=lambda row: row.area or 0.0, reverse=True)
        for family_id, rows in rows_by_family.items()
    }


def extract_family_trace_rows(
    *,
    cells: Sequence[FamilyCell],
    raw_dir: Path,
    dll_dir: Path,
    mz: float,
    rt_min: float,
    rt_max: float,
    ppm: float,
    max_highlight_rescued: int = 8,
) -> list[TraceOverlayRow]:
    from xic_extractor.raw_reader import open_raw
    from xic_extractor.xic_models import XICRequest

    groups = assign_highlight_groups(
        cells,
        max_highlight_rescued=max_highlight_rescued,
    )
    rows: list[TraceOverlayRow] = []
    for cell in cells:
        raw_path = raw_dir / f"{cell.sample_stem}.raw"
        if not raw_path.is_file():
            raise FileNotFoundError(f"RAW file not found: {raw_path}")
        with open_raw(raw_path, dll_dir) as raw:
            trace = raw.extract_xic_many(
                (XICRequest(mz=mz, rt_min=rt_min, rt_max=rt_max, ppm_tol=ppm),)
            )[0]
        rows.append(
            trace_row_from_arrays(
                cell,
                groups[cell.sample_stem],
                trace.rt,
                trace.intensity,
            )
        )
    return rows


def assign_highlight_groups(
    cells: Sequence[FamilyCell],
    *,
    max_highlight_rescued: int = 8,
) -> dict[str, str]:
    detected = {cell.sample_stem for cell in cells if cell.status == "detected"}
    top_rescued = {
        cell.sample_stem
        for cell in sorted(
            (
                cell
                for cell in cells
                if cell.status == "backfilled" and "QC" not in cell.sample_stem
            ),
            key=lambda item: item.area or 0.0,
            reverse=True,
        )[: max(0, max_highlight_rescued)]
    }
    groups: dict[str, str] = {}
    for cell in cells:
        if cell.sample_stem in detected:
            groups[cell.sample_stem] = "detected_seed"
        elif "QC" in cell.sample_stem:
            groups[cell.sample_stem] = "pooled_qc"
        elif cell.sample_stem in top_rescued:
            groups[cell.sample_stem] = "top_rescued_ms1_area"
        else:
            groups[cell.sample_stem] = "rescued_other"
    return groups


def trace_row_from_arrays(
    cell: FamilyCell,
    group: str,
    rt: object,
    intensity: object,
) -> TraceOverlayRow:
    rt_array = np.asarray(rt, dtype=float)
    intensity_array = np.asarray(intensity, dtype=float)
    if rt_array.ndim != 1 or intensity_array.ndim != 1:
        raise ValueError("Trace arrays must be one-dimensional")
    if rt_array.shape != intensity_array.shape:
        raise ValueError("Trace RT and intensity arrays must have equal length")
    max_intensity = float(np.max(intensity_array)) if intensity_array.size else 0.0
    if intensity_array.size and max_intensity > 0:
        apex_index = int(np.argmax(intensity_array))
        trace_apex_rt: float | None = float(rt_array[apex_index])
    else:
        trace_apex_rt = None
    return TraceOverlayRow(
        sample_stem=cell.sample_stem,
        status=cell.status,
        group=group,
        cell_area=cell.area,
        cell_height=cell.height,
        cell_apex_rt=cell.apex_rt,
        cell_start_rt=cell.peak_start_rt,
        cell_end_rt=cell.peak_end_rt,
        trace_max_intensity=max_intensity,
        trace_apex_rt=trace_apex_rt,
        region_shadow_verdict=cell.region_shadow_verdict,
        source_candidate_id=cell.source_candidate_id,
        rt=tuple(float(value) for value in rt_array),
        intensity=tuple(float(value) for value in intensity_array),
    )


def _require_columns(actual: Sequence[str], required: Sequence[str]) -> None:
    missing = [field for field in required if field not in actual]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")


def _parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None
