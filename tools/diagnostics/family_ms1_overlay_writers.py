"""TSV/JSON writers for legacy family-id peak-group MS1 overlays."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from tools.diagnostics.family_ms1_overlay_evidence import (
    _build_family_ms1_evidence_maps,
    _build_family_ms1_evidence_summary,
    _FamilyMs1EvidenceMaps,
    _global_trace_apex_delta,
    _local_to_global_max_ratio,
    _local_window_peak,
)
from tools.diagnostics.family_ms1_overlay_models import (
    FamilyMs1OverlayOutputs,
    TraceOverlayRow,
)
from tools.diagnostics.family_ms1_overlay_rendering import (
    render_family_ms1_overlay,
    render_hypothesis_ms1_overlay,
)
from xic_extractor.tabular_io import write_tsv

if TYPE_CHECKING:
    from xic_extractor.alignment.edge_scoring import DriftLookupProtocol


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
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_tsv = output_dir / f"{output_prefix}_trace_summary.tsv"
    trace_data_json = output_dir / f"{output_prefix}_trace_data.json"
    png_path = output_dir / f"{output_prefix}.png"
    pdf_path = output_dir / f"{output_prefix}.pdf" if write_pdf else None
    hypothesis_png_path = output_dir / f"{output_prefix}_hypothesis.png"
    hypothesis_pdf_path = (
        output_dir / f"{output_prefix}_hypothesis.pdf" if write_pdf else None
    )

    evidence_maps = _build_family_ms1_evidence_maps(rows)
    _write_summary(summary_tsv, rows, evidence_maps=evidence_maps)
    _write_trace_data(
        trace_data_json,
        rows=rows,
        family_id=family_id,
        mz=mz,
        ppm=ppm,
        rt_min=rt_min,
        rt_max=rt_max,
        family_center_rt=family_center_rt,
        provenance=provenance,
        evidence_maps=evidence_maps,
    )
    render_family_ms1_overlay(
        rows=rows,
        png_path=png_path,
        pdf_path=pdf_path,
        family_id=family_id,
        mz=mz,
        ppm=ppm,
        rt_min=rt_min,
        rt_max=rt_max,
        family_center_rt=family_center_rt,
        drift_lookup=drift_lookup,
        dpi=dpi,
    )
    render_hypothesis_ms1_overlay(
        rows=rows,
        png_path=hypothesis_png_path,
        pdf_path=hypothesis_pdf_path,
        family_id=family_id,
        mz=mz,
        ppm=ppm,
        rt_min=rt_min,
        rt_max=rt_max,
        family_center_rt=family_center_rt,
        dpi=dpi,
    )
    return FamilyMs1OverlayOutputs(
        png_path=png_path,
        pdf_path=pdf_path,
        summary_tsv=summary_tsv,
        trace_data_json=trace_data_json,
    )


def _write_summary(
    path: Path,
    rows: Sequence[TraceOverlayRow],
    *,
    evidence_maps: _FamilyMs1EvidenceMaps | None = None,
) -> None:
    resolved_evidence_maps = (
        _build_family_ms1_evidence_maps(rows)
        if evidence_maps is None
        else evidence_maps
    )
    (
        _normalized_by_row,
        window_facts_by_row,
        shape_similarity,
        absolute_shape_similarity,
    ) = resolved_evidence_maps
    fields = (
        "sample_stem",
        "status",
        "cell_area",
        "cell_height",
        "cell_apex_rt",
        "cell_start_rt",
        "cell_end_rt",
        "trace_max_intensity",
        "trace_apex_rt",
        "global_trace_apex_delta_min",
        "local_window_max_intensity",
        "local_window_apex_delta_min",
        "local_window_to_global_max_ratio",
        "region_shadow_verdict",
        "source_candidate_id",
        "highlight_group",
        "apex_aligned_shape_similarity",
        "absolute_own_max_shape_similarity",
    )
    write_tsv(
        path,
        [
            {
                "sample_stem": row.sample_stem,
                "status": row.status,
                "cell_area": _format_float(row.cell_area),
                "cell_height": _format_float(row.cell_height),
                "cell_apex_rt": _format_float(row.cell_apex_rt, digits=6),
                "cell_start_rt": _format_float(row.cell_start_rt, digits=6),
                "cell_end_rt": _format_float(row.cell_end_rt, digits=6),
                "trace_max_intensity": _format_float(row.trace_max_intensity),
                "trace_apex_rt": _format_float(row.trace_apex_rt, digits=6),
                "global_trace_apex_delta_min": _format_float(
                    _global_trace_apex_delta(
                        row,
                        window_facts=window_facts_by_row[row],
                    ),
                ),
                "local_window_max_intensity": _format_float(
                    _local_window_peak(
                        row,
                        window_facts=window_facts_by_row[row],
                    )[1],
                ),
                "local_window_apex_delta_min": _format_float(
                    _local_window_peak(
                        row,
                        window_facts=window_facts_by_row[row],
                    )[0],
                ),
                "local_window_to_global_max_ratio": _format_float(
                    _local_to_global_max_ratio(
                        row,
                        window_facts=window_facts_by_row[row],
                    ),
                ),
                "region_shadow_verdict": row.region_shadow_verdict,
                "source_candidate_id": row.source_candidate_id,
                "highlight_group": row.group,
                "apex_aligned_shape_similarity": _format_float(
                    shape_similarity.get(row.sample_stem),
                ),
                "absolute_own_max_shape_similarity": _format_float(
                    absolute_shape_similarity.get(row.sample_stem),
                ),
            }
            for row in rows
        ],
        fields,
        lineterminator="\n",
        formatter=str,
    )


def _write_trace_data(
    path: Path,
    *,
    rows: Sequence[TraceOverlayRow],
    family_id: str,
    mz: float,
    ppm: float,
    rt_min: float,
    rt_max: float,
    family_center_rt: float | None,
    provenance: Mapping[str, object] | None = None,
    evidence_maps: _FamilyMs1EvidenceMaps | None = None,
) -> None:
    resolved_evidence_maps = (
        _build_family_ms1_evidence_maps(rows)
        if evidence_maps is None
        else evidence_maps
    )
    (
        _normalized_by_row,
        window_facts_by_row,
        shape_similarity,
        absolute_shape_similarity,
    ) = resolved_evidence_maps
    data = {
        "family_id": family_id,
        "mz": mz,
        "ppm": ppm,
        "rt_min": rt_min,
        "rt_max": rt_max,
        "family_center_rt": family_center_rt,
        "provenance": dict(provenance or {}),
        "trace_count": len(rows),
        "evidence_summary": _build_family_ms1_evidence_summary(
            rows,
            evidence_maps=resolved_evidence_maps,
        ),
        "traces": [
            {
                "sample_stem": row.sample_stem,
                "status": row.status,
                "group": row.group,
                "cell_area": row.cell_area,
                "cell_height": row.cell_height,
                "cell_apex_rt": row.cell_apex_rt,
                "cell_start_rt": row.cell_start_rt,
                "cell_end_rt": row.cell_end_rt,
                "trace_max_intensity": row.trace_max_intensity,
                "trace_apex_rt": row.trace_apex_rt,
                "global_trace_apex_delta_min": _json_float(
                    _global_trace_apex_delta(
                        row,
                        window_facts=window_facts_by_row[row],
                    ),
                ),
                "local_window_max_intensity": _json_float(
                    _local_window_peak(
                        row,
                        window_facts=window_facts_by_row[row],
                    )[1],
                ),
                "local_window_apex_delta_min": _json_float(
                    _local_window_peak(
                        row,
                        window_facts=window_facts_by_row[row],
                    )[0],
                ),
                "local_window_to_global_max_ratio": _json_float(
                    _local_to_global_max_ratio(
                        row,
                        window_facts=window_facts_by_row[row],
                    ),
                ),
                "region_shadow_verdict": row.region_shadow_verdict,
                "source_candidate_id": row.source_candidate_id,
                "apex_aligned_shape_similarity": _json_float(
                    shape_similarity.get(row.sample_stem),
                ),
                "absolute_own_max_shape_similarity": _json_float(
                    absolute_shape_similarity.get(row.sample_stem),
                ),
                "rt": row.rt,
                "intensity": row.intensity,
            }
            for row in rows
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _format_float(value: float | None, *, digits: int = 6) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"{value:.{digits}g}"


def _json_float(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return float(value)
