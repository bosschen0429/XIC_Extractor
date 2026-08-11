"""Render MS1 overlay evidence for one legacy family-id peak-group row."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.diagnostics.family_ms1_overlay_evidence import (
    _absolute_own_max_shape_similarity,
    _apex_aligned_normalized_trace,
    _apex_aligned_shape_similarity,
    _build_family_ms1_evidence_maps,
    _build_family_ms1_evidence_summary,
    _gaussian_smooth_values,
    _global_trace_apex_delta,
    _local_to_global_max_ratio,
    _local_window_peak,
    _median_value,
    build_family_ms1_evidence_summary,
)
from tools.diagnostics.family_ms1_overlay_models import (
    APEX_ALIGN_HALF_WINDOW_MIN,
    GLOBAL_APEX_CONFLICT_DELTA_MIN,
    REQUIRED_ALIGNMENT_COLUMNS,
    SHAPE_SUPPORT_MIN,
    FamilyCell,
    FamilyMs1OverlayOutputs,
    TraceOverlayRow,
)
from tools.diagnostics.family_ms1_overlay_rendering import (
    _add_panel_note,
    _gaussian_smooth,
    _hypothesis_overlay_panel_layout,
    _plot_apex_aligned_overlay,
    _plot_area_distribution,
    _plot_group_median_trace,
    _plot_normalized_overlay,
    _plot_raw_highlights,
    _plot_shape_similarity,
    _plot_trace_apex_delta_distribution,
    render_family_ms1_overlay,
    render_hypothesis_ms1_overlay,
)
from tools.diagnostics.family_ms1_overlay_rendering_styles import (
    DETECTED_COLOR,
    OTHER_TRACE_COLOR,
    PLOT_GAUSSIAN_SMOOTH_POINTS,
    QC_COLOR,
    RESCUED_COLOR,
    RESCUED_MEDIAN_COLOR,
    _draw_center_rt,
    _line_style,
    _plot_unified_legend,
    _point_style,
    _stable_jitter,
)
from tools.diagnostics.family_ms1_overlay_trace import (
    _parse_float,
    _require_columns,
    assign_highlight_groups,
    extract_family_trace_rows,
    load_family_cells,
    load_family_cells_for_families,
    trace_row_from_arrays,
)
from tools.diagnostics.family_ms1_overlay_writers import (
    _format_float,
    _json_float,
    _write_summary,
    _write_trace_data,
)
from tools.diagnostics.family_ms1_overlay_writers import (
    write_family_ms1_overlay_outputs as _render_family_ms1_overlay_outputs,
)
from xic_extractor.alignment.edge_scoring import DriftLookupProtocol

__all__ = [
    "APEX_ALIGN_HALF_WINDOW_MIN",
    "DETECTED_COLOR",
    "FamilyCell",
    "FamilyMs1OverlayOutputs",
    "GLOBAL_APEX_CONFLICT_DELTA_MIN",
    "OTHER_TRACE_COLOR",
    "PLOT_GAUSSIAN_SMOOTH_POINTS",
    "QC_COLOR",
    "REQUIRED_ALIGNMENT_COLUMNS",
    "RESCUED_COLOR",
    "RESCUED_MEDIAN_COLOR",
    "SHAPE_SUPPORT_MIN",
    "TraceOverlayRow",
    "_add_panel_note",
    "_absolute_own_max_shape_similarity",
    "_apex_aligned_normalized_trace",
    "_apex_aligned_shape_similarity",
    "_build_family_ms1_evidence_maps",
    "_build_family_ms1_evidence_summary",
    "_draw_center_rt",
    "_format_float",
    "_gaussian_smooth",
    "_gaussian_smooth_values",
    "_global_trace_apex_delta",
    "_hypothesis_overlay_panel_layout",
    "_json_float",
    "_line_style",
    "_local_to_global_max_ratio",
    "_local_window_peak",
    "_median_value",
    "_parse_float",
    "_plot_apex_aligned_overlay",
    "_plot_area_distribution",
    "_plot_group_median_trace",
    "_plot_normalized_overlay",
    "_plot_raw_highlights",
    "_plot_shape_similarity",
    "_plot_trace_apex_delta_distribution",
    "_plot_unified_legend",
    "_point_style",
    "_require_columns",
    "_render_family_ms1_overlay_outputs",
    "_stable_jitter",
    "_write_summary",
    "_write_trace_data",
    "_write_family_ms1_overlay_transaction",
    "assign_highlight_groups",
    "build_family_ms1_evidence_summary",
    "extract_family_trace_rows",
    "load_family_cells",
    "load_family_cells_for_families",
    "main",
    "np",
    "render_family_ms1_overlay",
    "render_hypothesis_ms1_overlay",
    "trace_row_from_arrays",
    "write_family_ms1_overlay_outputs",
]


def write_family_ms1_overlay_outputs(
    *,
    rows: Sequence[TraceOverlayRow],
    output_dir: Path,
    output_prefix: str,
    family_id: str,
    mz: float,
    ppm: float,
    rt_min: float,
    rt_max: float,
    family_center_rt: float | None,
    provenance: Mapping[str, object] | None = None,
    drift_lookup: DriftLookupProtocol | None = None,
    write_pdf: bool = True,
    dpi: int = 140,
) -> FamilyMs1OverlayOutputs:
    """Compatibility wrapper around one complete evidence/output transaction."""

    outputs, _evidence_summary = _write_family_ms1_overlay_transaction(
        rows=rows,
        output_dir=output_dir,
        output_prefix=output_prefix,
        family_id=family_id,
        mz=mz,
        ppm=ppm,
        rt_min=rt_min,
        rt_max=rt_max,
        family_center_rt=family_center_rt,
        provenance=provenance,
        drift_lookup=drift_lookup,
        write_pdf=write_pdf,
        dpi=dpi,
    )
    return outputs


def _write_family_ms1_overlay_transaction(
    *,
    rows: Sequence[TraceOverlayRow],
    output_dir: Path,
    output_prefix: str,
    family_id: str,
    mz: float,
    ppm: float,
    rt_min: float,
    rt_max: float,
    family_center_rt: float | None,
    provenance: Mapping[str, object] | None = None,
    drift_lookup: DriftLookupProtocol | None = None,
    write_pdf: bool = True,
    dpi: int = 140,
) -> tuple[
    FamilyMs1OverlayOutputs,
    dict[str, int | float | str | bool | None],
]:
    (
        normalized_by_row,
        window_facts_by_row,
        shape_similarity,
        absolute_shape_similarity,
    ) = _build_family_ms1_evidence_maps(rows)
    evidence_summary = _build_family_ms1_evidence_summary(
        rows,
        window_facts_by_row=window_facts_by_row,
        shape_similarity=shape_similarity,
        absolute_shape_similarity=absolute_shape_similarity,
    )
    outputs = _render_family_ms1_overlay_outputs(
        rows=rows,
        output_dir=output_dir,
        output_prefix=output_prefix,
        family_id=family_id,
        mz=mz,
        ppm=ppm,
        rt_min=rt_min,
        rt_max=rt_max,
        family_center_rt=family_center_rt,
        normalized_by_row=normalized_by_row,
        window_facts_by_row=window_facts_by_row,
        shape_similarity=shape_similarity,
        absolute_shape_similarity=absolute_shape_similarity,
        evidence_summary=evidence_summary,
        provenance=provenance,
        drift_lookup=drift_lookup,
        write_pdf=write_pdf,
        dpi=dpi,
    )
    return outputs, evidence_summary


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    cells = load_family_cells(args.alignment_cells, args.family_id)
    rows = extract_family_trace_rows(
        cells=cells,
        raw_dir=args.raw_dir,
        dll_dir=args.dll_dir,
        mz=args.mz,
        rt_min=args.rt_min,
        rt_max=args.rt_max,
        ppm=args.ppm,
        max_highlight_rescued=args.max_highlight_rescued,
    )
    center_rt = (
        args.family_center_rt
        if args.family_center_rt is not None
        else _median_value(row.cell_apex_rt for row in rows)
    )
    prefix = args.output_prefix or f"{args.family_id.lower()}_ms1_overlay"
    # Compatibility only: these args used to feed an iRT panel, but the current
    # peak-group context overlay is intentionally two-panel and must not fail on a
    # no-op drift artifact.
    drift_lookup = None
    outputs, evidence_summary = _write_family_ms1_overlay_transaction(
        rows=rows,
        output_dir=args.output_dir,
        output_prefix=prefix,
        family_id=args.family_id,
        mz=args.mz,
        ppm=args.ppm,
        rt_min=args.rt_min,
        rt_max=args.rt_max,
        family_center_rt=center_rt,
        drift_lookup=drift_lookup,
    )
    print(f"MS1 overlay PNG: {outputs.png_path}")
    print(f"MS1 overlay PDF: {outputs.pdf_path}")
    print(f"Trace summary TSV: {outputs.summary_tsv}")
    print(f"Trace data JSON: {outputs.trace_data_json}")
    print(f"Peak-group MS1 verdict: {evidence_summary['family_verdict']}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--alignment-cells",
        "--alignment-cell-evidence",
        dest="alignment_cells",
        type=Path,
        required=True,
        help=(
            "Cell evidence TSV: compact alignment_backfill_cell_evidence.tsv "
            "or legacy alignment_cells.tsv."
        ),
    )
    parser.add_argument("--family-id", required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument("--mz", type=float, required=True)
    parser.add_argument("--rt-min", type=float, required=True)
    parser.add_argument("--rt-max", type=float, required=True)
    parser.add_argument("--ppm", type=float, default=10.0)
    parser.add_argument("--max-highlight-rescued", type=int, default=8)
    parser.add_argument("--family-center-rt", type=float)
    parser.add_argument(
        "--targeted-workbook",
        type=Path,
        help=(
            "Targeted XIC workbook (with ISTD rows). Accepted for compatibility; "
            "the current peak-group context overlay stays two-panel and does not "
            "render an iRT panel."
        ),
    )
    parser.add_argument(
        "--sample-info",
        type=Path,
        help=(
            "Sample info table for ISTD drift provenance. Accepted for "
            "compatibility; not rendered by the current peak-group context overlay."
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-prefix")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
