"""Matplotlib rendering helpers for legacy family-id peak-group MS1 overlays."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np

if TYPE_CHECKING:
    from xic_extractor.alignment.edge_scoring import DriftLookupProtocol
    from xic_extractor.peak_detection.ms1_shape_identity import OwnMaxTrace

from tools.diagnostics.family_ms1_overlay_evidence import (
    FamilyMs1EvidenceContext,
    _absolute_own_max_normalized_trace,
    _apex_aligned_normalized_trace,
    _build_family_ms1_evidence_context,
    _gaussian_smooth_values,
    _global_trace_apex_delta,
)
from tools.diagnostics.family_ms1_overlay_models import (
    APEX_ALIGN_HALF_WINDOW_MIN,
    GLOBAL_APEX_CONFLICT_DELTA_MIN,
    SHAPE_SUPPORT_MIN,
    TraceOverlayRow,
)
from tools.diagnostics.family_ms1_overlay_rendering_styles import (
    DETECTED_COLOR,
    PLOT_GAUSSIAN_SMOOTH_POINTS,
    RESCUED_MEDIAN_COLOR,
    _draw_center_rt,
    _line_style,
    _plot_unified_legend,
    _point_style,
    _stable_jitter,
)

_SELECTED_PEAK_STATUSES = frozenset({"detected", "backfilled"})


def render_family_ms1_overlay(
    *,
    rows: Sequence[TraceOverlayRow],
    png_path: Path,
    pdf_path: Path | None,
    family_id: str,
    mz: float,
    ppm: float,
    rt_min: float,
    rt_max: float,
    family_center_rt: float | None,
    drift_lookup: DriftLookupProtocol | None = None,
    dpi: int = 140,
) -> None:
    context = _build_family_ms1_evidence_context(rows)
    _render_family_ms1_overlay_from_context(
        rows=rows,
        context=context,
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


def _render_family_ms1_overlay_from_context(
    *,
    rows: Sequence[TraceOverlayRow],
    context: FamilyMs1EvidenceContext,
    png_path: Path,
    pdf_path: Path | None,
    family_id: str,
    mz: float,
    ppm: float,
    rt_min: float,
    rt_max: float,
    family_center_rt: float | None,
    drift_lookup: DriftLookupProtocol | None = None,
    dpi: int = 140,
) -> None:
    context.require_exact_rows(rows)
    normalized_by_row = context.normalized_by_row
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Accepted for CLI/backcompat while the peak-group context overlay intentionally
    # stays two-panel. Drift/iRT belongs to the typed hypothesis/mode evidence
    # slice, not this peak-group context image.
    _ = drift_lookup
    rescued = [row for row in rows if row.status == "backfilled"]
    focus_rows = _selected_peak_focus_rows(rows)

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
        cast(Any, _family_overlay_panel_layout()),
        figsize=(14, 6.8),
        constrained_layout=True,
        gridspec_kw={"height_ratios": [1.0, 0.18]},
    )
    _plot_normalized_overlay(
        axes["norm"],
        focus_rows,
        family_center_rt=family_center_rt,
        rt_min=rt_min,
        rt_max=rt_max,
        normalized_by_row=normalized_by_row,
        total_trace_count=len(rows),
    )
    _plot_raw_highlights(
        axes["raw"],
        focus_rows,
        family_center_rt=family_center_rt,
        rt_min=rt_min,
        rt_max=rt_max,
        normalized_by_row=normalized_by_row,
        total_trace_count=len(rows),
    )
    _plot_unified_legend(axes["legend"])

    fig.suptitle(
        (
            f"{family_id} peak-group MS1 pattern context: "
            f"m/z {_format_mz(mz)} +/-{ppm:g} ppm\n"
            f"selected RT segment {_selected_peak_segment_label(focus_rows)}; "
            f"{len(rows)} traces; "
            f"{len([row for row in rows if row.group == 'detected_seed'])} "
            "detected NL seeds; "
            f"{len(rescued)} rescued MS1 backfill cells; "
            f"{len(focus_rows)} selected-peak membership traces"
        ),
    )
    fig.savefig(png_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    if pdf_path is not None:
        fig.savefig(pdf_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_hypothesis_ms1_overlay(
    *,
    rows: Sequence[TraceOverlayRow],
    png_path: Path,
    pdf_path: Path | None,
    family_id: str,
    mz: float,
    ppm: float,
    rt_min: float,
    rt_max: float,
    family_center_rt: float | None,
    dpi: int = 140,
) -> None:
    context = _build_family_ms1_evidence_context(rows)
    _render_hypothesis_ms1_overlay_from_context(
        rows=rows,
        context=context,
        png_path=png_path,
        pdf_path=pdf_path,
        family_id=family_id,
        mz=mz,
        ppm=ppm,
        rt_min=rt_min,
        rt_max=rt_max,
        family_center_rt=family_center_rt,
        dpi=dpi,
    )


def _render_hypothesis_ms1_overlay_from_context(
    *,
    rows: Sequence[TraceOverlayRow],
    context: FamilyMs1EvidenceContext,
    png_path: Path,
    pdf_path: Path | None,
    family_id: str,
    mz: float,
    ppm: float,
    rt_min: float,
    rt_max: float,
    family_center_rt: float | None,
    dpi: int = 140,
) -> None:
    context.require_exact_rows(rows)
    normalized_by_row = context.normalized_by_row
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    focus_rows = _selected_peak_focus_rows(rows)
    focus_detected = [row for row in focus_rows if row.group == "detected_seed"]

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
        cast(Any, _hypothesis_overlay_panel_layout()),
        figsize=(14, 6.8),
        constrained_layout=True,
        gridspec_kw={"height_ratios": [1.0, 0.18]},
    )
    _plot_apex_aligned_overlay(
        axes["aligned"],
        focus_rows,
        anchor_rows=focus_detected,
        normalized_by_row=normalized_by_row,
        total_trace_count=len(rows),
    )
    _plot_raw_highlights(
        axes["raw"],
        focus_rows,
        family_center_rt=family_center_rt,
        rt_min=rt_min,
        rt_max=rt_max,
        normalized_by_row=normalized_by_row,
        total_trace_count=len(rows),
    )
    _plot_unified_legend(axes["legend"])

    fig.suptitle(
        (
            f"{family_id} hypothesis MS1 evidence: "
            f"m/z {_format_mz(mz)} +/-{ppm:g} ppm\n"
            f"selected RT segment {_selected_peak_segment_label(focus_rows)}; "
            f"{len(focus_detected)} detected NL anchor traces; "
            f"{len(focus_rows)} selected-peak membership traces"
            f"{_single_anchor_review_note(focus_detected)}"
        ),
    )
    fig.savefig(png_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    if pdf_path is not None:
        fig.savefig(pdf_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _family_overlay_panel_layout() -> list[list[str]]:
    return [
        ["norm", "raw"],
        ["legend", "legend"],
    ]


def _format_mz(value: float) -> str:
    return f"{value:.4f}"


def _hypothesis_overlay_panel_layout() -> list[list[str]]:
    return [
        ["aligned", "raw"],
        ["legend", "legend"],
    ]


def _single_anchor_review_note(rows: Sequence[TraceOverlayRow]) -> str:
    if len(rows) == 1:
        return "; single-anchor review only"
    return ""


def _selected_peak_segment_label(rows: Sequence[TraceOverlayRow]) -> str:
    bounds = _selected_peak_segment_bounds(rows, align_to_apex=False)
    if bounds is None:
        return "unavailable"
    start, end = bounds
    return f"{start:g}-{end:g} min"


def _plot_normalized_overlay(
    ax: Any,
    rows: Sequence[TraceOverlayRow],
    *,
    family_center_rt: float | None,
    rt_min: float,
    rt_max: float,
    normalized_by_row: Mapping[TraceOverlayRow, OwnMaxTrace],
    total_trace_count: int | None = None,
) -> None:
    total_trace_count = total_trace_count or len(rows)
    _draw_selected_peak_segment(ax, rows)
    for row in rows:
        rt, normalized = _absolute_own_max_normalized_trace(
            row,
            normalized_trace=normalized_by_row[row],
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
    _plot_group_median_trace(
        ax,
        rows,
        group="backfilled",
        color=RESCUED_MEDIAN_COLOR,
        label="rescued median",
        rt_min=rt_min,
        rt_max=rt_max,
        linestyle="--",
        normalized_by_row=normalized_by_row,
    )
    _draw_center_rt(ax, family_center_rt)
    ax.set_title("Family RT context: own-max scaled selected-peak membership")
    ax.set_xlabel("RT (min)")
    ax.set_ylabel("Per-trace scaled intensity (0-1; not abundance)")
    ax.set_xlim(*_selected_peak_window_bounds(rows, rt_min=rt_min, rt_max=rt_max))
    ax.set_ylim(-0.03, 1.08)
    ax.grid(True, alpha=0.2)
    _add_panel_note(
        ax,
        (
            "Shaded band = selected/cell peak segment "
            f"{_selected_peak_segment_label(rows)}; "
            f"{len(rows)}/{total_trace_count} detected/rescued traces shown."
        ),
    )


def _plot_irt_overlay(
    ax: Any,
    rows: Sequence[TraceOverlayRow],
    *,
    drift_lookup: DriftLookupProtocol | None,
    rt_min: float,
    rt_max: float,
) -> None:
    plotted = 0
    corrected_min = math.inf
    corrected_max = -math.inf
    for row in rows:
        delta = (
            drift_lookup.sample_delta_min(row.sample_stem)
            if drift_lookup is not None
            else None
        )
        if delta is None:
            continue
        rt = np.asarray(row.rt, dtype=float) - delta
        intensity = _gaussian_smooth(
            np.asarray(row.intensity, dtype=float),
            points=PLOT_GAUSSIAN_SMOOTH_POINTS,
        )
        if rt.size == 0 or row.trace_max_intensity <= 0:
            continue
        max_intensity = float(np.max(intensity)) if intensity.size else 0.0
        if max_intensity <= 0:
            continue
        color, alpha, line_width, zorder = _line_style(row.group)
        ax.plot(
            rt,
            intensity / max_intensity,
            color=color,
            alpha=alpha,
            lw=line_width,
            zorder=zorder,
        )
        plotted += 1
        corrected_min = min(corrected_min, float(rt.min()))
        corrected_max = max(corrected_max, float(rt.max()))
    ax.set_title(
        "Drift-corrected (iRT) RT context: per-sample shift = -ISTD drift "
        "(traces collapsing here = same peak with drift)",
    )
    ax.set_xlabel("Drift-corrected RT (min)")
    ax.set_ylabel("Per-trace scaled intensity (0-1)")
    ax.set_ylim(-0.03, 1.08)
    ax.grid(True, alpha=0.2)
    if plotted == 0:
        ax.text(
            0.5,
            0.5,
            "drift evidence not supplied — iRT panel unavailable",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=10,
            color="#888888",
        )
    else:
        # Frame the drift-corrected data so no shifted trace is clipped, while
        # still spanning at least the original RT window for comparability.
        ax.set_xlim(min(rt_min, corrected_min), max(rt_max, corrected_max))
        _add_panel_note(
            ax,
            "Compare against the absolute-RT panel: convergence here means the "
            "apparent split is drift, not separate peaks.",
        )


def _plot_raw_highlights(
    ax: Any,
    rows: Sequence[TraceOverlayRow],
    *,
    family_center_rt: float | None,
    rt_min: float,
    rt_max: float,
    normalized_by_row: Mapping[TraceOverlayRow, OwnMaxTrace],
    total_trace_count: int | None = None,
) -> None:
    total_trace_count = total_trace_count or len(rows)
    labels_seen: set[str] = set()
    _draw_selected_peak_segment(ax, rows)
    for row in rows:
        normalized_trace = normalized_by_row[row]
        rt, normalized = _absolute_own_max_normalized_trace(
            row,
            normalized_trace=normalized_trace,
        )
        if rt.size == 0 or normalized_trace.max_intensity is None:
            continue
        intensity = normalized * normalized_trace.max_intensity
        color, alpha, line_width, _zorder = _line_style(row.group)
        label = _trace_group_label(row)
        ax.plot(
            rt,
            intensity,
            color=color,
            lw=line_width,
            alpha=alpha,
            label=None if label in labels_seen else label,
        )
        labels_seen.add(label)
    _draw_center_rt(ax, family_center_rt)
    ax.set_title("Selected-peak raw intensity: DDA trigger / signal height")
    ax.set_xlabel("RT (min)")
    ax.set_ylabel("Raw MS1 intensity (smoothed)")
    ax.set_xlim(*_selected_peak_window_bounds(rows, rt_min=rt_min, rt_max=rt_max))
    ax.grid(True, alpha=0.2)
    _add_panel_note(
        ax,
        f"Raw height for selected peak {_selected_peak_segment_label(rows)} only; "
        f"{len(rows)}/{total_trace_count} detected/rescued traces shown.",
    )


def _plot_apex_aligned_overlay(
    ax: Any,
    rows: Sequence[TraceOverlayRow],
    *,
    total_trace_count: int | None = None,
    anchor_rows: Sequence[TraceOverlayRow] = (),
    normalized_by_row: Mapping[TraceOverlayRow, OwnMaxTrace],
) -> None:
    total_trace_count = total_trace_count or len(rows)
    detected_anchor_rows = tuple(anchor_rows) or tuple(
        row for row in rows if row.group == "detected_seed"
    )
    _draw_selected_peak_segment(
        ax,
        detected_anchor_rows or rows,
        align_to_apex=True,
    )
    for row in rows:
        rt, normalized = _apex_aligned_normalized_trace(
            row,
            normalized_trace=normalized_by_row[row],
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
    _plot_group_median_trace(
        ax,
        rows,
        group="backfilled",
        color=RESCUED_MEDIAN_COLOR,
        label="rescued median",
        rt_min=-APEX_ALIGN_HALF_WINDOW_MIN,
        rt_max=APEX_ALIGN_HALF_WINDOW_MIN,
        linestyle="--",
        align_to_apex=True,
        normalized_by_row=normalized_by_row,
    )
    ax.axvline(0.0, color="black", lw=1, ls="--", alpha=0.6)
    ax.set_title("Detected-anchor apex-aligned MS1 shape")
    ax.set_xlabel("RT relative to selected/cell apex (min)")
    ax.set_ylabel("Per-trace scaled intensity (0-1)")
    ax.set_xlim(-APEX_ALIGN_HALF_WINDOW_MIN, APEX_ALIGN_HALF_WINDOW_MIN)
    ax.set_ylim(-0.03, 1.08)
    ax.grid(True, alpha=0.2)
    _add_panel_note(
        ax,
        "Reference = detected NL peak segment "
        f"{_selected_peak_segment_label(detected_anchor_rows or rows)}; "
        "traces are aligned to each row's selected/cell apex; "
        f"{len(rows)}/{total_trace_count} detected/rescued traces shown.",
    )


def _selected_peak_focus_rows(
    rows: Sequence[TraceOverlayRow],
) -> tuple[TraceOverlayRow, ...]:
    return tuple(row for row in rows if row.status in _SELECTED_PEAK_STATUSES)


def _trace_group_label(row: TraceOverlayRow) -> str:
    if row.group == "detected_seed":
        return "detected NL seed"
    if row.group == "top_rescued_ms1_area":
        return "top rescued MS1 area"
    if row.group == "pooled_qc":
        return "pooled QC"
    return "other rescued"


def _selected_peak_window_bounds(
    rows: Sequence[TraceOverlayRow],
    *,
    rt_min: float,
    rt_max: float,
    padding_min: float = 0.25,
) -> tuple[float, float]:
    start_end = _selected_peak_segment_bounds(rows, align_to_apex=False)
    if start_end is None:
        return rt_min, rt_max
    start, end = start_end
    left = max(rt_min, start - padding_min)
    right = min(rt_max, end + padding_min)
    if right <= left:
        return rt_min, rt_max
    return left, right


def _draw_selected_peak_segment(
    ax: Any,
    rows: Sequence[TraceOverlayRow],
    *,
    align_to_apex: bool = False,
) -> None:
    bounds = _selected_peak_segment_bounds(rows, align_to_apex=align_to_apex)
    if bounds is None:
        return
    start, end = bounds
    ax.axvspan(
        start,
        end,
        color="#F0E442",
        alpha=0.18,
        zorder=0,
        label="selected/cell peak segment",
    )


def _selected_peak_segment_bounds(
    rows: Sequence[TraceOverlayRow],
    *,
    align_to_apex: bool,
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
        start = row.cell_start_rt
        end = row.cell_end_rt
        if align_to_apex:
            apex = row.cell_apex_rt
            if apex is None or not math.isfinite(apex):
                continue
            start -= apex
            end -= apex
        starts.append(start)
        ends.append(end)
    if not starts or not ends:
        return None
    return min(starts), max(ends)


def _add_panel_note(ax: Any, text: str) -> None:
    ax.text(
        0.01,
        0.02,
        text,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=7.5,
        color="0.25",
        bbox={
            "boxstyle": "round,pad=0.22",
            "facecolor": "white",
            "edgecolor": "0.82",
            "alpha": 0.86,
        },
    )


def _plot_group_median_trace(
    ax: Any,
    rows: Sequence[TraceOverlayRow],
    *,
    group: str,
    color: str,
    label: str,
    rt_min: float,
    rt_max: float,
    linestyle: str = "-",
    align_to_apex: bool = False,
    normalized_by_row: Mapping[TraceOverlayRow, OwnMaxTrace],
) -> None:
    grid = np.linspace(rt_min, rt_max, 250)
    normalized_traces: list[np.ndarray] = []
    for row in rows:
        if group == "backfilled":
            include = row.status == "backfilled"
        else:
            include = row.group == group
        if not include:
            continue
        if align_to_apex:
            rt, normalized = _apex_aligned_normalized_trace(
                row,
                normalized_trace=normalized_by_row[row],
            )
        else:
            rt, normalized = _absolute_own_max_normalized_trace(
                row,
                normalized_trace=normalized_by_row[row],
            )
        if rt.size < 2:
            continue
        interp = np.interp(grid, rt, normalized, left=np.nan, right=np.nan)
        normalized_traces.append(interp)
    if not normalized_traces:
        return
    stack = np.vstack(normalized_traces)
    finite_columns = np.isfinite(stack).any(axis=0)
    if not np.any(finite_columns):
        return
    plot_grid = grid[finite_columns]
    median = np.nanmedian(stack[:, finite_columns], axis=0)
    ax.plot(
        plot_grid,
        median,
        color=color,
        lw=2.6,
        ls=linestyle,
        alpha=0.95,
        label=label,
        zorder=6,
    )


def _plot_area_distribution(ax: Any, rows: Sequence[TraceOverlayRow]) -> None:
    detected_areas = [
        row.cell_area
        for row in rows
        if row.status == "detected" and row.cell_area is not None
    ]
    min_detected_area = min(detected_areas) if detected_areas else None
    rescued_above_min = 0
    for row in rows:
        if row.status not in _SELECTED_PEAK_STATUSES or row.cell_area is None:
            continue
        if (
            row.status == "backfilled"
            and min_detected_area is not None
            and row.cell_area >= min_detected_area
        ):
            rescued_above_min += 1
        x_pos = 0 if row.status == "detected" else 1
        jitter = _stable_jitter(row.sample_stem, width=0.18)
        color, alpha, size = _point_style(row)
        ax.scatter(
            x_pos + jitter,
            row.cell_area,
            s=size,
            color=color,
            alpha=alpha,
            edgecolors="none",
        )
    if min_detected_area is not None and min_detected_area > 0:
        ax.axhline(
            min_detected_area,
            color=DETECTED_COLOR,
            lw=1,
            ls=":",
            alpha=0.65,
        )
        ax.text(
            1.02,
            min_detected_area,
            f"{rescued_above_min} rescued >= min detected",
            va="bottom",
            ha="left",
            fontsize=8,
            color=DETECTED_COLOR,
        )
    ax.set_yscale("log")
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["detected\nNL seed", "rescued\nMS1 backfill"])
    ax.set_ylabel("Integrated cell area, log scale")
    ax.set_title("Cell area by evidence source")
    ax.grid(True, axis="y", alpha=0.2)


def _plot_trace_apex_delta_distribution(
    ax: Any,
    detected: Sequence[TraceOverlayRow],
    rescued: Sequence[TraceOverlayRow],
) -> None:
    for row in [*detected, *rescued]:
        delta = _global_trace_apex_delta(row)
        if delta is None or not math.isfinite(delta):
            continue
        x_pos = 1 if row.status == "detected" else 2
        jitter = _stable_jitter(row.sample_stem, width=0.16)
        color, alpha, _size = _point_style(row)
        ax.scatter(
            x_pos + jitter,
            delta,
            s=24,
            color=color,
            alpha=alpha,
            edgecolors="none",
        )
    ax.axhline(0.0, color="black", lw=1, ls="-", alpha=0.45)
    ax.axhline(
        GLOBAL_APEX_CONFLICT_DELTA_MIN,
        color="0.35",
        lw=1,
        ls=":",
        alpha=0.7,
    )
    ax.axhline(
        -GLOBAL_APEX_CONFLICT_DELTA_MIN,
        color="0.35",
        lw=1,
        ls=":",
        alpha=0.7,
    )
    ax.set_xticks([1, 2])
    ax.set_xticklabels(["detected\nNL seed", "rescued\nMS1 backfill"])
    ax.set_ylabel("Global trace apex - selected apex (min)")
    ax.set_title("Neighboring MS1 apex interference")
    ax.grid(True, axis="y", alpha=0.2)


def _plot_shape_similarity(
    ax: Any,
    rows: Sequence[TraceOverlayRow],
    shape_similarity: Mapping[str, float | None],
) -> None:
    for row in rows:
        value = shape_similarity.get(row.sample_stem)
        if value is None or not math.isfinite(value):
            continue
        if row.status not in _SELECTED_PEAK_STATUSES:
            continue
        x_pos = 0 if row.status == "detected" else 1
        jitter = _stable_jitter(row.sample_stem, width=0.18)
        color, alpha, size = _point_style(row)
        ax.scatter(
            x_pos + jitter,
            value,
            s=size,
            color=color,
            alpha=alpha,
            edgecolors="none",
        )
    ax.axhline(0.80, color="0.35", lw=1, ls=":", alpha=0.7)
    ax.axhline(SHAPE_SUPPORT_MIN, color="0.45", lw=1, ls="--", alpha=0.55)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["detected\nNL seed", "rescued\nMS1 backfill"])
    ax.set_ylim(-1.05, 1.05)
    ax.set_ylabel("Apex-aligned shape similarity")
    ax.set_title("Shape similarity after apex alignment")
    ax.grid(True, axis="y", alpha=0.2)


def _gaussian_smooth(values: np.ndarray, *, points: int) -> np.ndarray:
    return _gaussian_smooth_values(values, points=points)
