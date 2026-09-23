"""Render peak-group RT interpretation panels from legacy family-id MS1 traces."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import numpy as np

from tools.diagnostics.family_ms1_overlay_evidence import (
    _gaussian_smooth_values,
    build_family_ms1_evidence_summary,
)
from tools.diagnostics.family_ms1_overlay_models import (
    APEX_ALIGN_HALF_WINDOW_MIN,
    TraceOverlayRow,
)
from tools.diagnostics.family_ms1_overlay_rendering import (
    _add_panel_note,
    _draw_selected_peak_segment,
    _selected_peak_focus_rows,
    _selected_peak_segment_label,
    _selected_peak_window_bounds,
)
from tools.diagnostics.family_ms1_overlay_rendering_styles import (
    PLOT_GAUSSIAN_SMOOTH_POINTS,
    RESCUED_MEDIAN_COLOR,
    _draw_center_rt,
    _line_style,
    _plot_unified_legend,
)
from xic_extractor.tabular_io import write_tsv

if TYPE_CHECKING:
    from xic_extractor.alignment.edge_scoring import DriftLookupProtocol

ApexSource = Literal["cell", "trace"]
SOURCE_FAMILY_COLORS = (
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#56B4E9",
    "#E69F00",
)


@dataclass(frozen=True)
class TraceDataBundle:
    family_id: str
    mz: float
    ppm: float
    rt_min: float
    rt_max: float
    family_center_rt: float | None
    output_prefix: str
    rows: tuple[TraceOverlayRow, ...]
    evidence_summary: dict[str, object]


@dataclass(frozen=True)
class SourceFamilyShift:
    source_family: str
    rows: tuple[TraceOverlayRow, ...]
    median_cell_apex_rt: float | None
    shift_to_reference_min: float | None
    is_reference: bool
    shift_basis: str = "median_cell_apex"
    shape_similarity_to_reference: float | None = None


NormalizedTrace = tuple[np.ndarray, np.ndarray]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    outputs = run_alignment_experiment(
        trace_data_json=args.trace_data_json,
        output_dir=args.output_dir,
        output_prefix=args.output_prefix,
        cell_evidence_tsv=args.cell_evidence_tsv,
        reference_source_family=args.reference_source_family,
        targeted_workbook=args.targeted_workbook,
        sample_info=args.sample_info,
        render_images=not args.no_images,
        dpi=args.dpi,
    )
    if not args.no_images and "png_path" in outputs:
        print(f"Alignment experiment PNG: {outputs['png_path']}")
    print(f"Alignment experiment summary TSV: {outputs['summary_tsv']}")
    if "source_summary_tsv" in outputs:
        print(f"Source-family summary TSV: {outputs['source_summary_tsv']}")
        print(f"Source-family shift summary TSV: {outputs['source_shift_summary_tsv']}")
        print(
            "Source-family best-shift summary TSV: "
            f"{outputs['source_best_shift_summary_tsv']}"
        )
        if not args.no_images:
            print(f"Source-family split PNG: {outputs['source_split_png']}")
            print(
                "Source-family shift alignment PNG: "
                f"{outputs['source_shift_png']}"
            )
            print(
                "Source-family best-shift alignment PNG: "
                f"{outputs['source_best_shift_png']}"
            )
    return 0


def run_alignment_experiment(
    *,
    trace_data_json: Path,
    output_dir: Path,
    output_prefix: str | None = None,
    cell_evidence_tsv: Path | None = None,
    source_family_by_sample: Mapping[str, str] | None = None,
    reference_source_family: str | None = None,
    targeted_workbook: Path | None = None,
    sample_info: Path | None = None,
    render_images: bool = True,
    write_auxiliary_summaries: bool = True,
    dpi: int = 140,
) -> dict[str, Path]:
    bundle = load_trace_data_bundle(trace_data_json)
    resolved_output_prefix = (
        output_prefix or f"{bundle.output_prefix}_alignment_experiment"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    png_path = output_dir / f"{resolved_output_prefix}.png"
    summary_tsv = output_dir / f"{resolved_output_prefix}_summary.tsv"
    source_split_png = output_dir / f"{resolved_output_prefix}_source_family_split.png"
    source_summary_tsv = (
        output_dir / f"{resolved_output_prefix}_source_family_summary.tsv"
    )
    source_shift_png = (
        output_dir / f"{resolved_output_prefix}_source_family_shift_alignment.png"
    )
    source_shift_summary_tsv = (
        output_dir / f"{resolved_output_prefix}_source_family_shift_summary.tsv"
    )
    source_best_shift_png = (
        output_dir / f"{resolved_output_prefix}_source_family_best_shift_alignment.png"
    )
    source_best_shift_summary_tsv = (
        output_dir / f"{resolved_output_prefix}_source_family_best_shift_summary.tsv"
    )
    drift_lookup = _build_drift_lookup(
        targeted_workbook=targeted_workbook,
        sample_info=sample_info,
    )
    if render_images:
        render_alignment_experiment(
            rows=bundle.rows,
            png_path=png_path,
            family_id=bundle.family_id,
            mz=bundle.mz,
            ppm=bundle.ppm,
            rt_min=bundle.rt_min,
            rt_max=bundle.rt_max,
            family_center_rt=bundle.family_center_rt,
            drift_lookup=drift_lookup,
            evidence_summary=bundle.evidence_summary,
            dpi=dpi,
        )
    if write_auxiliary_summaries:
        write_alignment_experiment_summary(
            summary_tsv,
            rows=bundle.rows,
            drift_lookup=drift_lookup,
            evidence_summary=bundle.evidence_summary,
        )
    resolved_source_family_by_sample = dict(
        source_family_by_sample
        if source_family_by_sample is not None
        else (
            load_source_family_by_sample(cell_evidence_tsv, family_id=bundle.family_id)
            if cell_evidence_tsv is not None
            else {}
        ),
    )
    outputs = {"summary_tsv": summary_tsv}
    if render_images:
        outputs["png_path"] = png_path
    if resolved_source_family_by_sample:
        if render_images:
            render_source_family_split(
                rows=bundle.rows,
                source_family_by_sample=resolved_source_family_by_sample,
                png_path=source_split_png,
                family_id=bundle.family_id,
                mz=bundle.mz,
                ppm=bundle.ppm,
                rt_min=bundle.rt_min,
                rt_max=bundle.rt_max,
                dpi=dpi,
            )
        if write_auxiliary_summaries:
            write_source_family_summary(
                source_summary_tsv,
                rows=bundle.rows,
                source_family_by_sample=resolved_source_family_by_sample,
            )
        if render_images or write_auxiliary_summaries:
            shifts = build_source_family_shift_plan(
                bundle.rows,
                source_family_by_sample=resolved_source_family_by_sample,
                reference_source_family=reference_source_family,
            )
            if render_images:
                render_source_family_shift_alignment(
                    rows=bundle.rows,
                    shifts=shifts,
                    png_path=source_shift_png,
                    family_id=bundle.family_id,
                    mz=bundle.mz,
                    ppm=bundle.ppm,
                    rt_min=bundle.rt_min,
                    rt_max=bundle.rt_max,
                    dpi=dpi,
                )
            if write_auxiliary_summaries:
                write_source_family_shift_summary(
                    source_shift_summary_tsv,
                    family_id=bundle.family_id,
                    shifts=shifts,
                )
        best_shifts = build_source_family_best_shift_plan(
            bundle.rows,
            source_family_by_sample=resolved_source_family_by_sample,
            reference_source_family=reference_source_family,
            rt_min=bundle.rt_min,
            rt_max=bundle.rt_max,
        )
        if render_images:
            render_source_family_shift_alignment(
                rows=bundle.rows,
                shifts=best_shifts,
                png_path=source_best_shift_png,
                family_id=bundle.family_id,
                mz=bundle.mz,
                ppm=bundle.ppm,
                rt_min=bundle.rt_min,
                rt_max=bundle.rt_max,
                dpi=dpi,
            )
        write_source_family_shift_summary(
            source_best_shift_summary_tsv,
            family_id=bundle.family_id,
            shifts=best_shifts,
        )
        outputs["source_best_shift_summary_tsv"] = source_best_shift_summary_tsv
        if write_auxiliary_summaries:
            outputs.update(
                {
                    "source_summary_tsv": source_summary_tsv,
                    "source_shift_summary_tsv": source_shift_summary_tsv,
                },
            )
        if render_images:
            outputs.update(
                {
                    "source_split_png": source_split_png,
                    "source_shift_png": source_shift_png,
                    "source_best_shift_png": source_best_shift_png,
                },
            )
    return outputs


def load_trace_data_bundle(path: Path) -> TraceDataBundle:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = tuple(_trace_row_from_json(row) for row in payload.get("traces", ()))
    output_prefix = str(
        payload.get("provenance", {}).get("output_prefix")
        or path.name.replace("_trace_data.json", "")
    )
    return TraceDataBundle(
        family_id=str(payload["family_id"]),
        mz=float(payload["mz"]),
        ppm=float(payload["ppm"]),
        rt_min=float(payload["rt_min"]),
        rt_max=float(payload["rt_max"]),
        family_center_rt=_optional_float(payload.get("family_center_rt")),
        output_prefix=output_prefix,
        rows=rows,
        evidence_summary=dict(payload.get("evidence_summary") or {}),
    )


def load_source_family_by_sample(path: Path, *, family_id: str) -> dict[str, str]:
    return load_source_family_by_family_sample(
        path,
        family_ids=(family_id,),
    ).get(family_id, {})


def load_source_family_by_family_sample(
    path: Path,
    *,
    family_ids: Sequence[str] | None = None,
) -> dict[str, dict[str, str]]:
    wanted = set(family_ids) if family_ids is not None else None
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        out: dict[str, dict[str, str]] = {}
        for row in reader:
            family = str(row.get("feature_family_id", "")).strip()
            if not family or (wanted is not None and family not in wanted):
                continue
            sample = str(row.get("sample_stem", "")).strip()
            if not sample:
                continue
            match = re.search(r"source_family=(FAM\d+)", row.get("reason", ""))
            out.setdefault(family, {})[sample] = match.group(1) if match else "(none)"
        return out


def render_alignment_experiment(
    *,
    rows: Sequence[TraceOverlayRow],
    png_path: Path,
    family_id: str,
    mz: float,
    ppm: float,
    rt_min: float,
    rt_max: float,
    family_center_rt: float | None,
    drift_lookup: "DriftLookupProtocol | None" = None,
    evidence_summary: dict[str, object] | None = None,
    dpi: int = 140,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    focus_rows = _selected_peak_focus_rows(rows)
    resolved_evidence_summary: dict[str, object] = dict(
        evidence_summary or build_family_ms1_evidence_summary(rows),
    )
    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.titlesize": 11,
            "axes.labelsize": 9,
            "legend.fontsize": 8,
            "figure.titlesize": 13,
        }
    )
    fig, axes = plt.subplot_mosaic(
        [
            ["cell", "trace"],
            ["absolute", "drift"],
            ["legend", "legend"],
        ],
        figsize=(14, 10.2),
        constrained_layout=True,
        gridspec_kw={"height_ratios": [1.0, 1.0, 0.14]},
    )
    _plot_relative_alignment(
        axes["cell"],
        focus_rows,
        apex_source="cell",
        title="Current view: selected/cell-apex aligned shape",
        panel_note=(
            "Current hypothesis-left interpretation. Sensitive to selected "
            "cell apex/boundary quality."
        ),
    )
    _plot_relative_alignment(
        axes["trace"],
        focus_rows,
        apex_source="trace",
        title="Shape-only control: own-max/trace-apex aligned",
        panel_note=(
            "RT evidence is intentionally removed here. Use this only for "
            "shape similarity, not same-peak proof by itself."
        ),
    )
    _plot_absolute_context(
        axes["absolute"],
        focus_rows,
        family_center_rt=family_center_rt,
        rt_min=rt_min,
        rt_max=rt_max,
        evidence_summary=resolved_evidence_summary,
        total_trace_count=len(rows),
    )
    _plot_drift_corrected_context(
        axes["drift"],
        focus_rows,
        drift_lookup=drift_lookup,
        rt_min=rt_min,
        rt_max=rt_max,
        evidence_summary=resolved_evidence_summary,
    )
    _plot_unified_legend(axes["legend"])
    fig.suptitle(
        (
            f"{family_id} MS1 RT interpretation experiment: m/z {_format_mz(mz)} "
            f"+/-{ppm:g} ppm\n"
            "Same raw traces, Gaussian15 smooth; panels differ only by RT "
            "coordinate interpretation."
        ),
    )
    fig.savefig(png_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_source_family_split(
    *,
    rows: Sequence[TraceOverlayRow],
    source_family_by_sample: dict[str, str],
    png_path: Path,
    family_id: str,
    mz: float,
    ppm: float,
    rt_min: float,
    rt_max: float,
    dpi: int = 140,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    groups = _source_family_groups(rows, source_family_by_sample)
    panel_count = max(len(groups), 1)
    ncols = 2 if panel_count > 1 else 1
    nrows = math.ceil(panel_count / ncols)
    fig, axes = plt.subplots(
        nrows + 1,
        ncols,
        figsize=(14, 4.5 * nrows + 1.4),
        constrained_layout=True,
        squeeze=False,
        gridspec_kw={"height_ratios": [*[1.0] * nrows, 0.16]},
    )
    plot_axes = [
        axes[row_index][col_index]
        for row_index in range(nrows)
        for col_index in range(ncols)
    ]
    for ax, (source_family, group_rows) in zip(plot_axes, groups, strict=False):
        _plot_source_family_absolute_panel(
            ax,
            group_rows,
            source_family=source_family,
            rt_min=rt_min,
            rt_max=rt_max,
        )
    for ax in plot_axes[len(groups) :]:
        ax.axis("off")
    legend_ax = axes[-1][0]
    _plot_unified_legend(legend_ax)
    if ncols > 1:
        axes[-1][1].axis("off")
    fig.suptitle(
        (
            f"{family_id} source-family split context: "
            f"m/z {_format_mz(mz)} +/-{ppm:g} ppm\n"
            "Rows are split by source_family provenance before visual review."
        ),
    )
    fig.savefig(png_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def build_source_family_shift_plan(
    rows: Sequence[TraceOverlayRow],
    *,
    source_family_by_sample: dict[str, str],
    reference_source_family: str | None = None,
) -> tuple[SourceFamilyShift, ...]:
    groups = _source_family_groups(rows, source_family_by_sample)
    if not groups:
        return ()
    medians = {
        source_family: _median(
            [
                row.cell_apex_rt
                for row in group_rows
                if row.cell_apex_rt is not None and math.isfinite(row.cell_apex_rt)
            ],
        )
        for source_family, group_rows in groups
    }
    reference = reference_source_family
    if reference not in medians or medians.get(reference) is None:
        reference = _default_reference_source_family(groups, medians)
    reference_median = medians.get(reference)
    shifts: list[SourceFamilyShift] = []
    for source_family, group_rows in groups:
        median_apex = medians.get(source_family)
        shift = (
            reference_median - median_apex
            if reference_median is not None and median_apex is not None
            else None
        )
        shifts.append(
            SourceFamilyShift(
                source_family=source_family,
                rows=group_rows,
                median_cell_apex_rt=median_apex,
                shift_to_reference_min=shift,
                is_reference=source_family == reference,
            ),
        )
    return tuple(shifts)


def build_source_family_best_shift_plan(
    rows: Sequence[TraceOverlayRow],
    *,
    source_family_by_sample: dict[str, str],
    reference_source_family: str | None = None,
    rt_min: float | None = None,
    rt_max: float | None = None,
    shift_min: float = -2.2,
    shift_max: float = 1.0,
    shift_step: float = 0.01,
) -> tuple[SourceFamilyShift, ...]:
    groups = _source_family_groups(rows, source_family_by_sample)
    if not groups:
        return ()
    medians = {
        source_family: _median(
            [
                row.cell_apex_rt
                for row in group_rows
                if row.cell_apex_rt is not None and math.isfinite(row.cell_apex_rt)
            ],
        )
        for source_family, group_rows in groups
    }
    reference = reference_source_family
    group_by_source = {
        source_family: group_rows for source_family, group_rows in groups
    }
    if reference not in group_by_source:
        reference = _default_reference_source_family(groups, medians)
    bounds = _source_family_correlation_bounds(groups, rt_min=rt_min, rt_max=rt_max)
    if bounds is None:
        return ()
    grid = np.linspace(bounds[0], bounds[1], 600)
    normalized_by_source = {
        source_family: _source_family_normalized_traces(group_rows)
        for source_family, group_rows in group_by_source.items()
    }
    reference_curve = _source_family_shifted_median_curve_from_normalized_traces(
        normalized_by_source[reference],
        shift_min=0.0,
        grid=grid,
    )
    shifts: list[SourceFamilyShift] = []
    for source_family, group_rows in groups:
        shift: float | None
        if source_family == reference:
            shift = 0.0
            similarity = _pearson_similarity(reference_curve, reference_curve)
        else:
            shift, similarity = _best_source_family_shape_shift(
                normalized_by_source[source_family],
                reference_curve=reference_curve,
                grid=grid,
                shift_min=shift_min,
                shift_max=shift_max,
                shift_step=shift_step,
            )
        shifts.append(
            SourceFamilyShift(
                source_family=source_family,
                rows=group_rows,
                median_cell_apex_rt=medians.get(source_family),
                shift_to_reference_min=shift,
                is_reference=source_family == reference,
                shift_basis="median_shape_correlation",
                shape_similarity_to_reference=similarity,
            ),
        )
    return tuple(shifts)


def render_source_family_shift_alignment(
    *,
    rows: Sequence[TraceOverlayRow],
    shifts: Sequence[SourceFamilyShift],
    png_path: Path,
    family_id: str,
    mz: float,
    ppm: float,
    rt_min: float,
    rt_max: float,
    dpi: int = 140,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _ = rows
    color_by_source = _source_family_color_map(shifts)
    shifted_bounds = _source_family_shifted_window_bounds(
        shifts,
        rt_min=rt_min,
        rt_max=rt_max,
    )
    fig, axes = plt.subplot_mosaic(
        [
            ["raw", "shifted"],
            ["median", "median"],
            ["legend", "legend"],
        ],
        figsize=(14, 10),
        constrained_layout=True,
        gridspec_kw={"height_ratios": [1.0, 1.0, 0.15]},
    )
    _plot_source_family_shift_raw_panel(
        axes["raw"],
        shifts,
        color_by_source=color_by_source,
        rt_min=rt_min,
        rt_max=rt_max,
    )
    _plot_source_family_shifted_panel(
        axes["shifted"],
        shifts,
        color_by_source=color_by_source,
        rt_min=shifted_bounds[0],
        rt_max=shifted_bounds[1],
    )
    _plot_source_family_shifted_medians(
        axes["median"],
        shifts,
        color_by_source=color_by_source,
        rt_min=shifted_bounds[0],
        rt_max=shifted_bounds[1],
    )
    _plot_source_family_shift_legend(
        axes["legend"],
        shifts,
        color_by_source=color_by_source,
    )
    fig.suptitle(
        (
            f"{family_id} source-family {_source_family_shift_basis_title(shifts)}: "
            f"m/z {_format_mz(mz)} "
            f"+/-{ppm:g} ppm\n"
            f"{_source_family_shift_basis_caption(shifts)} No per-trace manual "
            "alignment is used."
        ),
    )
    fig.savefig(png_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def write_alignment_experiment_summary(
    path: Path,
    *,
    rows: Sequence[TraceOverlayRow],
    drift_lookup: "DriftLookupProtocol | None" = None,
    evidence_summary: dict[str, object] | None = None,
) -> None:
    focus_rows = _selected_peak_focus_rows(rows)
    resolved_evidence_summary: dict[str, object] = dict(
        evidence_summary or build_family_ms1_evidence_summary(rows),
    )
    records = [
        {
            "method": "cell_apex_alignment",
            "plotted_traces": _relative_alignment_count(focus_rows, "cell"),
            "total_focus_traces": len(focus_rows),
            "basis": "rt - selected/cell apex",
            "interpretation": "current hypothesis view; selected-boundary sensitive",
        },
        {
            "method": "trace_apex_alignment",
            "plotted_traces": _relative_alignment_count(focus_rows, "trace"),
            "total_focus_traces": len(focus_rows),
            "basis": "rt - own trace max apex",
            "interpretation": "shape-only control; RT evidence removed",
        },
        {
            "method": "absolute_rt_context",
            "plotted_traces": _absolute_context_count(focus_rows),
            "total_focus_traces": len(focus_rows),
            "basis": "raw RT",
            "interpretation": "retains RT and signal-height evidence",
        },
        {
            "method": "drift_corrected_rt_context",
            "plotted_traces": _drift_context_count(focus_rows, drift_lookup),
            "total_focus_traces": len(focus_rows),
            "basis": "rt - per-sample ISTD drift delta",
            "interpretation": (
                "available only when targeted ISTD drift lookup is supplied"
            ),
        },
    ]
    fields = (
        "method",
        "plotted_traces",
        "total_focus_traces",
        "basis",
        "interpretation",
        "absolute_trace_apex_cluster_fraction",
        "absolute_own_max_shape_supported_fraction",
        "cell_apex_shape_supported_fraction",
    )
    _write_rows_tsv(
        path,
        fields,
        [
            {
                **record,
                "absolute_trace_apex_cluster_fraction": _summary_float(
                    resolved_evidence_summary,
                    "absolute_trace_apex_cluster_fraction",
                ),
                "absolute_own_max_shape_supported_fraction": _summary_float(
                    resolved_evidence_summary,
                    "absolute_own_max_shape_supported_fraction",
                ),
                "cell_apex_shape_supported_fraction": _summary_float(
                    resolved_evidence_summary,
                    "shape_supported_fraction",
                ),
            }
            for record in records
        ],
    )


def write_source_family_shift_summary(
    path: Path,
    *,
    family_id: str,
    shifts: Sequence[SourceFamilyShift],
) -> None:
    fields = (
        "feature_family_id",
        "source_family",
        "is_reference",
        "trace_count",
        "detected_count",
        "shift_basis",
        "median_cell_apex_rt",
        "shift_to_reference_min",
        "shift_to_reference_sec",
        "shape_similarity_to_reference_after_group_shift",
    )
    similarities = (
        {}
        if all(shift.shape_similarity_to_reference is not None for shift in shifts)
        else _source_family_shift_similarity(shifts)
    )
    _write_rows_tsv(
        path,
        fields,
        [
            {
                "feature_family_id": family_id,
                "source_family": shift.source_family,
                "is_reference": str(shift.is_reference).upper(),
                "trace_count": len(shift.rows),
                "detected_count": sum(
                    1 for row in shift.rows if row.status == "detected"
                ),
                "shift_basis": shift.shift_basis,
                "median_cell_apex_rt": _format_optional_float(
                    shift.median_cell_apex_rt,
                ),
                "shift_to_reference_min": _format_optional_float(
                    shift.shift_to_reference_min,
                ),
                "shift_to_reference_sec": _format_optional_float(
                    (
                        shift.shift_to_reference_min * 60.0
                        if shift.shift_to_reference_min is not None
                        else None
                    ),
                ),
                "shape_similarity_to_reference_after_group_shift": (
                    _format_optional_float(
                        shift.shape_similarity_to_reference
                        if shift.shape_similarity_to_reference is not None
                        else similarities.get(shift.source_family),
                    )
                ),
            }
            for shift in shifts
        ],
    )


def write_source_family_summary(
    path: Path,
    *,
    rows: Sequence[TraceOverlayRow],
    source_family_by_sample: dict[str, str],
) -> None:
    fields = (
        "source_family",
        "trace_count",
        "detected_count",
        "backfilled_count",
        "median_cell_apex_rt",
        "min_cell_apex_rt",
        "max_cell_apex_rt",
    )
    output_rows: list[dict[str, object]] = []
    for source_family, group_rows in _source_family_groups(
        rows,
        source_family_by_sample,
    ):
        apexes = [
            row.cell_apex_rt
            for row in group_rows
            if row.cell_apex_rt is not None and math.isfinite(row.cell_apex_rt)
        ]
        output_rows.append(
            {
                "source_family": source_family,
                "trace_count": len(group_rows),
                "detected_count": sum(
                    1 for row in group_rows if row.status == "detected"
                ),
                "backfilled_count": sum(
                    1 for row in group_rows if row.status == "backfilled"
                ),
                "median_cell_apex_rt": _format_optional_float(_median(apexes)),
                "min_cell_apex_rt": _format_optional_float(
                    min(apexes) if apexes else None,
                ),
                "max_cell_apex_rt": _format_optional_float(
                    max(apexes) if apexes else None,
                ),
            },
        )
    _write_rows_tsv(path, fields, output_rows)


def _write_rows_tsv(
    path: Path,
    fields: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(path, rows, fields, lineterminator="\n")


def _source_family_shift_basis_title(shifts: Sequence[SourceFamilyShift]) -> str:
    if _uses_shape_correlation_shift(shifts):
        return "best-shift shape experiment"
    return "median-apex group-shift experiment"


def _source_family_shift_basis_caption(shifts: Sequence[SourceFamilyShift]) -> str:
    if _uses_shape_correlation_shift(shifts):
        return (
            "Each source-family receives one median-shape correlation shift; "
            "raw RT evidence is retained in the left panel."
        )
    return "Each source-family receives one median-apex shift."


def _source_family_shift_xlabel(shifts: Sequence[SourceFamilyShift]) -> str:
    if _uses_shape_correlation_shift(shifts):
        return "RT after source-family median-shape best shift (min)"
    return "RT after source-family median-apex shift (min)"


def _source_family_shift_panel_note(shifts: Sequence[SourceFamilyShift]) -> str:
    if _uses_shape_correlation_shift(shifts):
        return "Shift maximizes source-family median-shape correlation."
    return "Shift = reference median apex - source-family median apex."


def _uses_shape_correlation_shift(shifts: Sequence[SourceFamilyShift]) -> bool:
    return any(shift.shift_basis == "median_shape_correlation" for shift in shifts)


def _plot_source_family_shift_raw_panel(
    ax: Any,
    shifts: Sequence[SourceFamilyShift],
    *,
    color_by_source: dict[str, str],
    rt_min: float,
    rt_max: float,
) -> None:
    all_rows = tuple(row for shift in shifts for row in shift.rows)
    _draw_selected_peak_segment(ax, all_rows)
    for shift in shifts:
        color = color_by_source[shift.source_family]
        for row in shift.rows:
            rt, normalized = _absolute_normalized_trace(
                row,
                smooth_points=PLOT_GAUSSIAN_SMOOTH_POINTS,
            )
            if rt.size == 0:
                continue
            ax.plot(
                rt,
                normalized,
                color=color,
                alpha=_source_line_alpha(row),
                lw=_source_line_width(row),
                zorder=_source_zorder(row),
            )
        _draw_center_rt(ax, shift.median_cell_apex_rt)
    ax.set_title("Raw RT by source-family provenance")
    ax.set_xlabel("RT (min)")
    ax.set_ylabel("Per-trace scaled intensity (0-1)")
    ax.set_xlim(*_selected_peak_window_bounds(all_rows, rt_min=rt_min, rt_max=rt_max))
    ax.set_ylim(-0.03, 1.08)
    ax.grid(True, alpha=0.2)
    _add_panel_note(ax, "No shift applied.")


def _plot_source_family_shifted_panel(
    ax: Any,
    shifts: Sequence[SourceFamilyShift],
    *,
    color_by_source: dict[str, str],
    rt_min: float,
    rt_max: float,
) -> None:
    for shift in shifts:
        if shift.shift_to_reference_min is None:
            continue
        color = color_by_source[shift.source_family]
        _draw_source_family_shifted_segment(ax, shift)
        for row in shift.rows:
            rt, normalized = _source_family_shifted_trace(
                row,
                shift_min=shift.shift_to_reference_min,
                smooth_points=PLOT_GAUSSIAN_SMOOTH_POINTS,
            )
            if rt.size == 0:
                continue
            ax.plot(
                rt,
                normalized,
                color=color,
                alpha=_source_line_alpha(row),
                lw=_source_line_width(row),
                zorder=_source_zorder(row),
            )
    reference = next((shift for shift in shifts if shift.is_reference), None)
    _draw_center_rt(ax, reference.median_cell_apex_rt if reference else None)
    ax.set_title("Group-shifted RT: one shift per source-family")
    ax.set_xlabel(_source_family_shift_xlabel(shifts))
    ax.set_ylabel("Per-trace scaled intensity (0-1)")
    ax.set_xlim(rt_min, rt_max)
    ax.set_ylim(-0.03, 1.08)
    ax.grid(True, alpha=0.2)
    _add_panel_note(
        ax,
        _source_family_shift_panel_note(shifts),
    )


def _plot_source_family_shifted_medians(
    ax: Any,
    shifts: Sequence[SourceFamilyShift],
    *,
    color_by_source: dict[str, str],
    rt_min: float,
    rt_max: float,
) -> None:
    similarities = _source_family_shift_similarity(
        shifts,
        rt_min=rt_min,
        rt_max=rt_max,
    )
    for shift in shifts:
        if shift.shift_to_reference_min is None:
            continue
        grid, median = _source_family_shifted_median_curve(
            shift.rows,
            shift_min=shift.shift_to_reference_min,
            rt_min=rt_min,
            rt_max=rt_max,
        )
        if grid.size == 0:
            continue
        label = shift.source_family
        similarity = similarities.get(shift.source_family)
        if similarity is not None and not shift.is_reference:
            label = f"{label} r={similarity:.3f}"
        ax.plot(
            grid,
            median,
            color=color_by_source[shift.source_family],
            lw=3.0 if shift.is_reference else 2.4,
            alpha=0.95,
            label=label,
        )
    reference = next((shift for shift in shifts if shift.is_reference), None)
    _draw_center_rt(ax, reference.median_cell_apex_rt if reference else None)
    ax.set_title("Shifted source-family median shapes")
    ax.set_xlabel(_source_family_shift_xlabel(shifts))
    ax.set_ylabel("Median scaled intensity (0-1)")
    ax.set_xlim(rt_min, rt_max)
    ax.set_ylim(-0.03, 1.08)
    ax.grid(True, alpha=0.2)
    ax.legend(loc="upper right", frameon=False)
    _add_panel_note(
        ax,
        "Median curves compare group-level translated peak patterns.",
    )


def _plot_relative_alignment(
    ax: Any,
    rows: Sequence[TraceOverlayRow],
    *,
    apex_source: ApexSource,
    title: str,
    panel_note: str,
) -> None:
    _draw_relative_selected_peak_segment(ax, rows, apex_source=apex_source)
    plotted = 0
    for row in rows:
        rt, normalized = _relative_aligned_normalized_trace(
            row,
            apex_source=apex_source,
            smooth_points=PLOT_GAUSSIAN_SMOOTH_POINTS,
        )
        if rt.size == 0:
            continue
        color, alpha, line_width, zorder = _line_style(row.group)
        ax.plot(
            rt,
            normalized,
            color=color,
            alpha=alpha,
            lw=line_width,
            zorder=zorder,
        )
        plotted += 1
    _plot_relative_group_median_trace(
        ax,
        rows,
        apex_source=apex_source,
        group="backfilled",
        color=RESCUED_MEDIAN_COLOR,
        label="rescued median",
    )
    ax.axvline(0.0, color="black", lw=1, ls="--", alpha=0.6)
    ax.set_title(title)
    ax.set_xlabel(_relative_xlabel(apex_source))
    ax.set_ylabel("Per-trace scaled intensity (0-1)")
    ax.set_xlim(-APEX_ALIGN_HALF_WINDOW_MIN, APEX_ALIGN_HALF_WINDOW_MIN)
    ax.set_ylim(-0.03, 1.08)
    ax.grid(True, alpha=0.2)
    _add_panel_note(
        ax,
        f"{panel_note} Plotted {plotted}/{len(rows)} selected traces.",
    )


def _plot_source_family_absolute_panel(
    ax: Any,
    rows: Sequence[TraceOverlayRow],
    *,
    source_family: str,
    rt_min: float,
    rt_max: float,
) -> None:
    _draw_selected_peak_segment(ax, rows)
    for row in rows:
        rt, normalized = _absolute_normalized_trace(
            row,
            smooth_points=PLOT_GAUSSIAN_SMOOTH_POINTS,
        )
        if rt.size == 0:
            continue
        color, alpha, line_width, zorder = _line_style(row.group)
        ax.plot(
            rt,
            normalized,
            color=color,
            alpha=alpha,
            lw=line_width,
            zorder=zorder,
        )
    apexes = [
        row.cell_apex_rt
        for row in rows
        if row.cell_apex_rt is not None and math.isfinite(row.cell_apex_rt)
    ]
    median_apex = _median(apexes)
    _draw_center_rt(ax, median_apex)
    detected_count = sum(1 for row in rows if row.status == "detected")
    ax.set_title(
        (
            f"{source_family}: {len(rows)} traces, {detected_count} detected, "
            f"median apex={_format_optional_float(median_apex)}"
        ),
    )
    ax.set_xlabel("RT (min)")
    ax.set_ylabel("Per-trace scaled intensity (0-1)")
    ax.set_xlim(*_selected_peak_window_bounds(rows, rt_min=rt_min, rt_max=rt_max))
    ax.set_ylim(-0.03, 1.08)
    ax.grid(True, alpha=0.2)
    _add_panel_note(
        ax,
        "Raw RT; filtered only by source_family provenance.",
    )


def _plot_absolute_context(
    ax: Any,
    rows: Sequence[TraceOverlayRow],
    *,
    family_center_rt: float | None,
    rt_min: float,
    rt_max: float,
    evidence_summary: dict[str, object],
    total_trace_count: int,
) -> None:
    _draw_selected_peak_segment(ax, rows)
    for row in rows:
        rt, normalized = _absolute_normalized_trace(
            row,
            smooth_points=PLOT_GAUSSIAN_SMOOTH_POINTS,
        )
        if rt.size == 0:
            continue
        color, alpha, line_width, zorder = _line_style(row.group)
        ax.plot(
            rt,
            normalized,
            color=color,
            alpha=alpha,
            lw=line_width,
            zorder=zorder,
        )
    _draw_center_rt(ax, family_center_rt)
    ax.set_title("Absolute RT context: own-max scaled traces")
    ax.set_xlabel("RT (min)")
    ax.set_ylabel("Per-trace scaled intensity (0-1)")
    ax.set_xlim(*_selected_peak_window_bounds(rows, rt_min=rt_min, rt_max=rt_max))
    ax.set_ylim(-0.03, 1.08)
    ax.grid(True, alpha=0.2)
    cluster_fraction = _summary_float(
        evidence_summary,
        "absolute_trace_apex_cluster_fraction",
    )
    _add_panel_note(
        ax,
        (
            f"Retains RT. Selected segment {_selected_peak_segment_label(rows)}; "
            f"{len(rows)}/{total_trace_count} detected/rescued traces shown. "
            f"own-max cluster={cluster_fraction}"
        ),
    )


def _plot_drift_corrected_context(
    ax: Any,
    rows: Sequence[TraceOverlayRow],
    *,
    drift_lookup: "DriftLookupProtocol | None",
    rt_min: float,
    rt_max: float,
    evidence_summary: dict[str, object],
) -> None:
    plotted = 0
    corrected_min = math.inf
    corrected_max = -math.inf
    _draw_drift_corrected_selected_peak_segment(ax, rows, drift_lookup=drift_lookup)
    for row in rows:
        rt, normalized = _drift_corrected_normalized_trace(
            row,
            drift_lookup=drift_lookup,
            smooth_points=PLOT_GAUSSIAN_SMOOTH_POINTS,
        )
        if rt.size == 0:
            continue
        color, alpha, line_width, zorder = _line_style(row.group)
        ax.plot(
            rt,
            normalized,
            color=color,
            alpha=alpha,
            lw=line_width,
            zorder=zorder,
        )
        plotted += 1
        corrected_min = min(corrected_min, float(np.min(rt)))
        corrected_max = max(corrected_max, float(np.max(rt)))
    ax.set_title("Drift-corrected RT context: rt - ISTD delta")
    ax.set_xlabel("Drift-corrected RT (min)")
    ax.set_ylabel("Per-trace scaled intensity (0-1)")
    ax.set_ylim(-0.03, 1.08)
    ax.grid(True, alpha=0.2)
    if plotted == 0:
        ax.set_xlim(rt_min, rt_max)
        ax.text(
            0.5,
            0.5,
            "drift source unavailable\nprovide --targeted-workbook + --sample-info",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=10,
            color="#777777",
        )
        _add_panel_note(
            ax,
            (
                "This is not negative evidence; it only means the current "
                "trace JSON run did not carry drift/iRT evidence."
            ),
        )
        return
    ax.set_xlim(min(rt_min, corrected_min), max(rt_max, corrected_max))
    shape_support = _summary_float(
        evidence_summary,
        "absolute_own_max_shape_supported_fraction",
    )
    _add_panel_note(
        ax,
        (
            f"Drift-corrected traces shown: {plotted}/{len(rows)}. "
            f"absolute own-max support={shape_support}"
        ),
    )


def _source_family_groups(
    rows: Sequence[TraceOverlayRow],
    source_family_by_sample: dict[str, str],
) -> list[tuple[str, tuple[TraceOverlayRow, ...]]]:
    grouped: dict[str, list[TraceOverlayRow]] = {}
    for row in _selected_peak_focus_rows(rows):
        source_family = source_family_by_sample.get(row.sample_stem, "(missing)")
        grouped.setdefault(source_family, []).append(row)
    return [
        (source_family, tuple(group_rows))
        for source_family, group_rows in sorted(
            grouped.items(),
            key=lambda item: (-len(item[1]), item[0]),
        )
    ]


def _source_family_shifted_window_bounds(
    shifts: Sequence[SourceFamilyShift],
    *,
    rt_min: float,
    rt_max: float,
    padding_min: float = 0.25,
) -> tuple[float, float]:
    starts: list[float] = []
    ends: list[float] = []
    for shift in shifts:
        if shift.shift_to_reference_min is None:
            continue
        for row in shift.rows:
            if row.cell_start_rt is None or row.cell_end_rt is None:
                continue
            starts.append(row.cell_start_rt + shift.shift_to_reference_min)
            ends.append(row.cell_end_rt + shift.shift_to_reference_min)
    if not starts or not ends:
        return rt_min, rt_max
    left = min(starts) - padding_min
    right = max(ends) + padding_min
    if right <= left:
        return rt_min, rt_max
    return left, right


def _source_family_shift_similarity(
    shifts: Sequence[SourceFamilyShift],
    *,
    rt_min: float | None = None,
    rt_max: float | None = None,
) -> dict[str, float | None]:
    reference = next((shift for shift in shifts if shift.is_reference), None)
    if reference is None or reference.shift_to_reference_min is None:
        return {}
    if rt_min is None or rt_max is None:
        rt_min, rt_max = _source_family_shifted_window_bounds(
            shifts,
            rt_min=0.0,
            rt_max=1.0,
            padding_min=0.0,
        )
    grid = np.linspace(rt_min, rt_max, 300)
    reference_curve = _source_family_shifted_median_curve_on_grid(
        reference.rows,
        shift_min=reference.shift_to_reference_min,
        grid=grid,
    )
    if grid.size == 0:
        return {}
    out: dict[str, float | None] = {}
    for shift in shifts:
        if shift.shift_to_reference_min is None:
            out[shift.source_family] = None
            continue
        curve = _source_family_shifted_median_curve_on_grid(
            shift.rows,
            shift_min=shift.shift_to_reference_min,
            grid=grid,
        )
        out[shift.source_family] = _pearson_similarity(curve, reference_curve)
    return out


def _source_family_shifted_median_curve(
    rows: Sequence[TraceOverlayRow],
    *,
    shift_min: float,
    rt_min: float,
    rt_max: float,
) -> tuple[np.ndarray, np.ndarray]:
    grid = np.linspace(rt_min, rt_max, 300)
    curve = _source_family_shifted_median_curve_on_grid(
        rows,
        shift_min=shift_min,
        grid=grid,
    )
    finite = np.isfinite(curve)
    if not np.any(finite):
        return np.array([], dtype=float), np.array([], dtype=float)
    return grid[finite], curve[finite]


def _source_family_shifted_median_curve_on_grid(
    rows: Sequence[TraceOverlayRow],
    *,
    shift_min: float,
    grid: np.ndarray,
) -> np.ndarray:
    return _source_family_shifted_median_curve_from_normalized_traces(
        _source_family_normalized_traces(rows),
        shift_min=shift_min,
        grid=grid,
    )


def _source_family_normalized_traces(
    rows: Sequence[TraceOverlayRow],
    *,
    smooth_points: int = PLOT_GAUSSIAN_SMOOTH_POINTS,
) -> tuple[NormalizedTrace, ...]:
    traces: list[NormalizedTrace] = []
    for row in rows:
        rt, normalized = _absolute_normalized_trace(row, smooth_points=smooth_points)
        if rt.size < 2:
            continue
        traces.append((rt, normalized))
    return tuple(traces)


def _source_family_shifted_median_curve_from_normalized_traces(
    traces: Sequence[NormalizedTrace],
    *,
    shift_min: float,
    grid: np.ndarray,
) -> np.ndarray:
    out = np.full(grid.shape, np.nan, dtype=float)
    if not traces:
        return out
    if len(traces) == 1:
        rt, normalized = traces[0]
        return np.interp(grid, rt + shift_min, normalized, left=np.nan, right=np.nan)
    stack = np.vstack(
        [
            np.interp(grid, rt + shift_min, normalized, left=np.nan, right=np.nan)
            for rt, normalized in traces
        ],
    )
    finite_columns = np.isfinite(stack).any(axis=0)
    if not np.any(finite_columns):
        return out
    out[finite_columns] = np.nanmedian(stack[:, finite_columns], axis=0)
    return out


def _source_family_shifted_median_curve_matrix_from_normalized_traces(
    traces: Sequence[NormalizedTrace],
    *,
    shifts: np.ndarray,
    grid: np.ndarray,
) -> np.ndarray:
    out: np.ndarray = np.full((shifts.size, grid.size), np.nan, dtype=float)
    if not traces or shifts.size == 0:
        return out
    if len(traces) == 1:
        rt, normalized = traces[0]
        return np.vstack(
            [
                np.interp(
                    grid,
                    rt + float(shift),
                    normalized,
                    left=np.nan,
                    right=np.nan,
                )
                for shift in shifts
            ],
        )
    stack = np.stack(
        [
            np.vstack(
                [
                    np.interp(
                        grid,
                        rt + float(shift),
                        normalized,
                        left=np.nan,
                        right=np.nan,
                    )
                    for shift in shifts
                ],
            )
            for rt, normalized in traces
        ],
        axis=1,
    )
    finite_columns = np.isfinite(stack).any(axis=1)
    if not np.any(finite_columns):
        return out
    medians = _nanmedian_small_axis1(stack)
    out[finite_columns] = medians[finite_columns]
    return out


def _nanmedian_small_axis1(values: np.ndarray) -> np.ndarray:
    valid_counts = np.sum(~np.isnan(values), axis=1)
    ordered = np.sort(values, axis=1)
    lower_indices = np.clip((valid_counts - 1) // 2, 0, values.shape[1] - 1)
    upper_indices = np.clip(valid_counts // 2, 0, values.shape[1] - 1)
    lower = np.take_along_axis(ordered, lower_indices[:, np.newaxis, :], axis=1)[
        :,
        0,
        :,
    ]
    upper = np.take_along_axis(ordered, upper_indices[:, np.newaxis, :], axis=1)[
        :,
        0,
        :,
    ]
    medians = (lower + upper) / 2.0
    medians[valid_counts == 0] = np.nan
    return medians


def _best_source_family_shape_shift(
    traces: Sequence[NormalizedTrace],
    *,
    reference_curve: np.ndarray,
    grid: np.ndarray,
    shift_min: float,
    shift_max: float,
    shift_step: float,
) -> tuple[float | None, float | None]:
    if shift_step <= 0:
        raise ValueError("shift_step must be positive")
    best_shift: float | None = None
    best_similarity: float | None = None
    shifts = np.arange(shift_min, shift_max + shift_step / 2.0, shift_step)
    curves = _source_family_shifted_median_curve_matrix_from_normalized_traces(
        traces,
        shifts=shifts,
        grid=grid,
    )
    similarities = _pearson_similarity_vector(curves, reference_curve)
    finite = np.isfinite(similarities)
    if not np.any(finite):
        return None, None
    finite_indices = np.flatnonzero(finite)
    best_index = int(finite_indices[int(np.argmax(similarities[finite]))])
    best_shift = float(shifts[best_index])
    best_similarity = _pearson_similarity(curves[best_index], reference_curve)
    return best_shift, best_similarity


def _source_family_correlation_bounds(
    groups: Sequence[tuple[str, tuple[TraceOverlayRow, ...]]],
    *,
    rt_min: float | None,
    rt_max: float | None,
) -> tuple[float, float] | None:
    if rt_min is not None and rt_max is not None and rt_max > rt_min:
        return rt_min, rt_max
    values: list[float] = []
    for _, rows in groups:
        for row in rows:
            values.extend(value for value in row.rt if math.isfinite(value))
    if not values:
        return None
    left = min(values)
    right = max(values)
    if right <= left:
        return None
    return left, right


def _default_reference_source_family(
    groups: Sequence[tuple[str, tuple[TraceOverlayRow, ...]]],
    medians: dict[str, float | None],
) -> str:
    candidates = [
        (source_family, rows)
        for source_family, rows in groups
        if medians.get(source_family) is not None
    ]
    detected_candidates = [
        (source_family, rows)
        for source_family, rows in candidates
        if any(row.status == "detected" for row in rows)
    ]
    ordered = detected_candidates or candidates or list(groups)
    return max(ordered, key=lambda item: len(item[1]))[0]


def _relative_aligned_normalized_trace(
    row: TraceOverlayRow,
    *,
    apex_source: ApexSource,
    smooth_points: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    apex_rt = _apex_rt(row, apex_source)
    if apex_rt is None or not math.isfinite(apex_rt):
        return np.array([], dtype=float), np.array([], dtype=float)
    rt = np.asarray(row.rt, dtype=float) - apex_rt
    intensity = np.asarray(row.intensity, dtype=float)
    mask = (
        np.isfinite(rt)
        & np.isfinite(intensity)
        & (rt >= -APEX_ALIGN_HALF_WINDOW_MIN)
        & (rt <= APEX_ALIGN_HALF_WINDOW_MIN)
    )
    if not np.any(mask):
        return np.array([], dtype=float), np.array([], dtype=float)
    local_intensity = _gaussian_smooth_values(intensity[mask], points=smooth_points)
    local_max = float(np.max(local_intensity)) if local_intensity.size else 0.0
    if local_max <= 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    return rt[mask], local_intensity / local_max


def _absolute_normalized_trace(
    row: TraceOverlayRow,
    *,
    smooth_points: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    rt = np.asarray(row.rt, dtype=float)
    intensity = np.asarray(row.intensity, dtype=float)
    mask = np.isfinite(rt) & np.isfinite(intensity)
    if not np.any(mask):
        return np.array([], dtype=float), np.array([], dtype=float)
    rt = rt[mask]
    intensity = _gaussian_smooth_values(intensity[mask], points=smooth_points)
    max_intensity = float(np.max(intensity)) if intensity.size else 0.0
    if max_intensity <= 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    return rt, intensity / max_intensity


def _source_family_shifted_trace(
    row: TraceOverlayRow,
    *,
    shift_min: float,
    smooth_points: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    rt, normalized = _absolute_normalized_trace(row, smooth_points=smooth_points)
    if rt.size == 0:
        return rt, normalized
    return rt + shift_min, normalized


def _drift_corrected_normalized_trace(
    row: TraceOverlayRow,
    *,
    drift_lookup: "DriftLookupProtocol | None",
    smooth_points: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    if drift_lookup is None:
        return np.array([], dtype=float), np.array([], dtype=float)
    delta = drift_lookup.sample_delta_min(row.sample_stem)
    if delta is None or not math.isfinite(delta):
        return np.array([], dtype=float), np.array([], dtype=float)
    rt, normalized = _absolute_normalized_trace(row, smooth_points=smooth_points)
    if rt.size == 0:
        return rt, normalized
    return rt - delta, normalized


def _plot_relative_group_median_trace(
    ax: Any,
    rows: Sequence[TraceOverlayRow],
    *,
    apex_source: ApexSource,
    group: str,
    color: str,
    label: str,
) -> None:
    grid = np.linspace(
        -APEX_ALIGN_HALF_WINDOW_MIN,
        APEX_ALIGN_HALF_WINDOW_MIN,
        250,
    )
    normalized_traces: list[np.ndarray] = []
    for row in rows:
        include = (
            row.status == "backfilled" if group == "backfilled" else row.group == group
        )
        if not include:
            continue
        rt, normalized = _relative_aligned_normalized_trace(
            row,
            apex_source=apex_source,
            smooth_points=PLOT_GAUSSIAN_SMOOTH_POINTS,
        )
        if rt.size < 2:
            continue
        normalized_traces.append(
            np.interp(grid, rt, normalized, left=np.nan, right=np.nan),
        )
    if not normalized_traces:
        return
    stack = np.vstack(normalized_traces)
    finite_columns = np.isfinite(stack).any(axis=0)
    if not np.any(finite_columns):
        return
    median = np.nanmedian(stack[:, finite_columns], axis=0)
    ax.plot(
        grid[finite_columns],
        median,
        color=color,
        lw=2.6,
        ls="--",
        alpha=0.95,
        label=label,
        zorder=6,
    )


def _draw_relative_selected_peak_segment(
    ax: Any,
    rows: Sequence[TraceOverlayRow],
    *,
    apex_source: ApexSource,
) -> None:
    bounds = _selected_peak_segment_bounds_relative(rows, apex_source=apex_source)
    if bounds is None:
        return
    start, end = bounds
    ax.axvspan(start, end, color="#F0E442", alpha=0.18, zorder=0)


def _draw_drift_corrected_selected_peak_segment(
    ax: Any,
    rows: Sequence[TraceOverlayRow],
    *,
    drift_lookup: "DriftLookupProtocol | None",
) -> None:
    bounds = _drift_corrected_selected_segment_bounds(rows, drift_lookup=drift_lookup)
    if bounds is None:
        return
    start, end = bounds
    ax.axvspan(start, end, color="#F0E442", alpha=0.18, zorder=0)


def _draw_source_family_shifted_segment(
    ax: Any,
    shift: SourceFamilyShift,
) -> None:
    starts: list[float] = []
    ends: list[float] = []
    if shift.shift_to_reference_min is None:
        return
    for row in shift.rows:
        if row.cell_start_rt is None or row.cell_end_rt is None:
            continue
        starts.append(row.cell_start_rt + shift.shift_to_reference_min)
        ends.append(row.cell_end_rt + shift.shift_to_reference_min)
    if not starts or not ends:
        return
    ax.axvspan(min(starts), max(ends), color="#F0E442", alpha=0.08, zorder=0)


def _selected_peak_segment_bounds_relative(
    rows: Sequence[TraceOverlayRow],
    *,
    apex_source: ApexSource,
) -> tuple[float, float] | None:
    starts: list[float] = []
    ends: list[float] = []
    for row in rows:
        if (
            row.cell_start_rt is None
            or row.cell_end_rt is None
            or not math.isfinite(row.cell_start_rt)
            or not math.isfinite(row.cell_end_rt)
        ):
            continue
        apex_rt = _apex_rt(row, apex_source)
        if apex_rt is None or not math.isfinite(apex_rt):
            continue
        starts.append(row.cell_start_rt - apex_rt)
        ends.append(row.cell_end_rt - apex_rt)
    if not starts or not ends:
        return None
    return min(starts), max(ends)


def _drift_corrected_selected_segment_bounds(
    rows: Sequence[TraceOverlayRow],
    *,
    drift_lookup: "DriftLookupProtocol | None",
) -> tuple[float, float] | None:
    if drift_lookup is None:
        return None
    starts: list[float] = []
    ends: list[float] = []
    for row in rows:
        if row.cell_start_rt is None or row.cell_end_rt is None:
            continue
        delta = drift_lookup.sample_delta_min(row.sample_stem)
        if delta is None or not math.isfinite(delta):
            continue
        starts.append(row.cell_start_rt - delta)
        ends.append(row.cell_end_rt - delta)
    if not starts or not ends:
        return None
    return min(starts), max(ends)


def _relative_alignment_count(
    rows: Sequence[TraceOverlayRow],
    apex_source: ApexSource,
) -> int:
    return sum(
        1
        for row in rows
        if _relative_aligned_normalized_trace(
            row,
            apex_source=apex_source,
            smooth_points=PLOT_GAUSSIAN_SMOOTH_POINTS,
        )[0].size
    )


def _absolute_context_count(rows: Sequence[TraceOverlayRow]) -> int:
    return sum(
        1
        for row in rows
        if _absolute_normalized_trace(
            row,
            smooth_points=PLOT_GAUSSIAN_SMOOTH_POINTS,
        )[0].size
    )


def _drift_context_count(
    rows: Sequence[TraceOverlayRow],
    drift_lookup: "DriftLookupProtocol | None",
) -> int:
    return sum(
        1
        for row in rows
        if _drift_corrected_normalized_trace(
            row,
            drift_lookup=drift_lookup,
            smooth_points=PLOT_GAUSSIAN_SMOOTH_POINTS,
        )[0].size
    )


def _apex_rt(row: TraceOverlayRow, apex_source: ApexSource) -> float | None:
    if apex_source == "cell":
        return row.cell_apex_rt
    return row.trace_apex_rt


def _relative_xlabel(apex_source: ApexSource) -> str:
    if apex_source == "cell":
        return "RT relative to selected/cell apex (min)"
    return "RT relative to own max / trace apex (min)"


def _plot_source_family_shift_legend(
    ax: Any,
    shifts: Sequence[SourceFamilyShift],
    *,
    color_by_source: dict[str, str],
) -> None:
    from matplotlib.lines import Line2D

    ax.axis("off")
    handles = []
    for shift in shifts:
        shift_text = _format_optional_float(shift.shift_to_reference_min)
        reference_text = " reference" if shift.is_reference else f" shift={shift_text}m"
        handles.append(
            Line2D(
                [0],
                [0],
                color=color_by_source[shift.source_family],
                lw=2.6,
                label=f"{shift.source_family}{reference_text}",
            ),
        )
    handles.extend(
        [
            Line2D([0], [0], color="0.15", lw=2.6, label="detected trace"),
            Line2D([0], [0], color="0.15", lw=1.0, alpha=0.35, label="rescued trace"),
        ],
    )
    ax.legend(
        handles=handles,
        loc="center",
        ncol=min(4, max(1, len(handles))),
        frameon=False,
        handlelength=3.0,
        columnspacing=1.6,
    )


def _source_family_color_map(
    shifts: Sequence[SourceFamilyShift],
) -> dict[str, str]:
    return {
        shift.source_family: SOURCE_FAMILY_COLORS[index % len(SOURCE_FAMILY_COLORS)]
        for index, shift in enumerate(shifts)
    }


def _source_line_alpha(row: TraceOverlayRow) -> float:
    if row.status == "detected":
        return 0.95
    if row.group == "pooled_qc":
        return 0.55
    return 0.32


def _source_line_width(row: TraceOverlayRow) -> float:
    return 2.0 if row.status == "detected" else 0.95


def _source_zorder(row: TraceOverlayRow) -> int:
    return 5 if row.status == "detected" else 2


def _pearson_similarity(
    values: np.ndarray,
    reference: np.ndarray,
) -> float | None:
    mask = np.isfinite(values) & np.isfinite(reference)
    if int(np.sum(mask)) < 5:
        return None
    x = values[mask]
    y = reference[mask]
    x_std = float(np.std(x))
    y_std = float(np.std(y))
    if x_std <= 1e-12 or y_std <= 1e-12:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def _pearson_similarity_vector(
    values: np.ndarray,
    reference: np.ndarray,
) -> np.ndarray:
    if values.ndim != 2:
        raise ValueError("values must be a 2D array")
    finite = np.isfinite(values) & np.isfinite(reference)
    counts = finite.sum(axis=1).astype(float)
    safe_counts = np.where(counts > 0.0, counts, 1.0)
    masked_values = np.where(finite, values, 0.0)
    masked_reference = np.where(finite, reference, 0.0)
    sum_x = masked_values.sum(axis=1)
    sum_y = masked_reference.sum(axis=1)
    mean_x = sum_x / safe_counts
    mean_y = sum_y / safe_counts
    var_x = (np.square(masked_values).sum(axis=1) / safe_counts) - np.square(mean_x)
    var_y = (np.square(masked_reference).sum(axis=1) / safe_counts) - np.square(mean_y)
    cov = (masked_values * masked_reference).sum(axis=1) / safe_counts - (
        mean_x * mean_y
    )
    similarities = np.full(values.shape[0], np.nan, dtype=float)
    valid = (counts >= 5.0) & (var_x > 1e-24) & (var_y > 1e-24)
    similarities[valid] = cov[valid] / np.sqrt(var_x[valid] * var_y[valid])
    return similarities


def _build_drift_lookup(
    *,
    targeted_workbook: Path | None,
    sample_info: Path | None,
) -> "DriftLookupProtocol | None":
    if targeted_workbook is None and sample_info is None:
        return None
    if targeted_workbook is None or sample_info is None:
        raise ValueError(
            "--targeted-workbook and --sample-info must be supplied together",
        )
    from xic_extractor.alignment.drift_evidence import (
        read_targeted_istd_drift_evidence,
    )

    return cast(
        "DriftLookupProtocol",
        read_targeted_istd_drift_evidence(targeted_workbook, sample_info),
    )


def _trace_row_from_json(data: dict[str, object]) -> TraceOverlayRow:
    return TraceOverlayRow(
        sample_stem=str(data.get("sample_stem", "")),
        status=str(data.get("status", "")),
        group=str(data.get("group", "")),
        cell_area=_optional_float(data.get("cell_area")),
        cell_height=_optional_float(data.get("cell_height")),
        cell_apex_rt=_optional_float(data.get("cell_apex_rt")),
        cell_start_rt=_optional_float(data.get("cell_start_rt")),
        cell_end_rt=_optional_float(data.get("cell_end_rt")),
        trace_max_intensity=_optional_float(data.get("trace_max_intensity")) or 0.0,
        trace_apex_rt=_optional_float(data.get("trace_apex_rt")),
        region_shadow_verdict=str(data.get("region_shadow_verdict", "")),
        source_candidate_id=str(data.get("source_candidate_id", "")),
        rt=_float_tuple(data.get("rt")),
        intensity=_float_tuple(data.get("intensity")),
    )


def _float_tuple(values: object) -> tuple[float, ...]:
    if values is None or isinstance(values, str):
        return ()
    if not isinstance(values, Iterable):
        return ()
    return tuple(float(value) for value in values)


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        text = str(value).strip()
        return float(text) if text else None
    except ValueError:
        return None


def _summary_float(summary: dict[str, object], key: str) -> str:
    value = _optional_float(summary.get(key))
    if value is None:
        return ""
    return f"{value:.3f}"


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.4f}"


def _format_mz(value: float) -> str:
    return f"{value:.4f}"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-data-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-prefix")
    parser.add_argument(
        "--cell-evidence-tsv",
        type=Path,
        help=(
            "Optional alignment_backfill_cell_evidence.tsv used to split traces "
            "by source_family provenance."
        ),
    )
    parser.add_argument(
        "--reference-source-family",
        help=(
            "Optional source_family ID used as the group-shift reference. "
            "Defaults to the largest source-family with detected evidence."
        ),
    )
    parser.add_argument(
        "--targeted-workbook",
        type=Path,
        help="Optional targeted ISTD workbook for drift-corrected RT context.",
    )
    parser.add_argument(
        "--sample-info",
        type=Path,
        help="Optional sample metadata used with --targeted-workbook.",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help=(
            "Write machine-readable summary TSVs without rendering PNG review "
            "images."
        ),
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=140,
        help="Render DPI for review PNGs (default 140).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
