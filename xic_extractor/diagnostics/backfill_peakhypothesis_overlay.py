"""Overlay plots for anchored FULL COHORT PeakHypothesis review candidates."""

from __future__ import annotations

import html
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from xic_extractor.diagnostics.diagnostic_io import (
    format_diagnostic_value,
    optional_float,
    text_value,
    write_tsv,
)
from xic_extractor.peak_detection.baseline import asls_baseline
from xic_extractor.peak_detection.chrom_peak_segments import (
    ChromPeakSegment,
    ChromPeakSegmentPolicy,
    enumerate_chrom_peak_segments,
)
from xic_extractor.peak_detection.ms1_morphology import (
    DEFAULT_GAUSSIAN15_WINDOW_POINTS,
    MS1_MORPHOLOGY_AREA_SOURCE,
    gaussian15_morphology_trace,
)

SCHEMA_VERSION = "backfill_peakhypothesis_overlay"
SMOOTH_METHOD = "gaussian15_asls_residual"
SMOOTH_WINDOW_POINTS = DEFAULT_GAUSSIAN15_WINDOW_POINTS
MIN_MACHINE_SHAPE_CONTEXT_WIDTH_MIN = 0.60

OVERLAY_INDEX_COLUMNS = (
    "schema_version",
    "source_run_id",
    "review_item_id",
    "sample_stem",
    "source_peak_hypothesis_id",
    "source_feature_family_id",
    "full_cohort_matched_peak_hypothesis_id",
    "full_cohort_consolidation_winner_group_hypothesis_id",
    "candidate_mz",
    "candidate_anchor_rt",
    "candidate_peak_start_rt",
    "candidate_peak_end_rt",
    "winner_mz",
    "winner_rt",
    "rt_min",
    "rt_max",
    "ppm_tolerance",
    "smooth_method",
    "smooth_window_points",
    "machine_shape_decision",
    "machine_shape_reasons",
    "machine_shape_blockers",
    "machine_same_peak_verdict",
    "machine_same_peak_reasons",
    "machine_same_peak_blockers",
    "gaussian15_selected_segment_peak_count",
    "gaussian15_selected_segment_apex_rts",
    "gaussian15_shape_context_start_rt",
    "gaussian15_shape_context_end_rt",
    "gaussian15_lobe_start_rt",
    "gaussian15_lobe_end_rt",
    "gaussian15_lobe_apex_rt",
    "gaussian15_lobe_area",
    "gaussian15_lobe_area_source",
    "gaussian15_lobe_boundary_source",
    "gaussian15_lobe_segment_class",
    "candidate_point_count",
    "winner_point_count",
    "candidate_max_intensity",
    "winner_max_intensity",
    "review_focus",
    "png_path",
    "pdf_path",
)

_MACHINE_SHAPE_SEGMENT_POLICY = ChromPeakSegmentPolicy(
    min_scan_count=3,
    min_apex_residual=1.0,
    min_apex_fraction_of_context=0.20,
    morphology_trace_window_points=SMOOTH_WINDOW_POINTS,
)

TraceProvider = Callable[
    [Path, float, float, float, float],
    tuple[np.ndarray, np.ndarray],
]


@dataclass(frozen=True)
class FullCohortOverlayRequest:
    review_item_id: str
    sample_stem: str
    source_peak_hypothesis_id: str
    source_feature_family_id: str
    full_cohort_matched_peak_hypothesis_id: str
    full_cohort_consolidation_winner_group_hypothesis_id: str
    raw_file: Path
    candidate_mz: float
    candidate_anchor_rt: float
    candidate_peak_start_rt: float | None
    candidate_peak_end_rt: float | None
    winner_mz: float | None
    winner_rt: float | None
    rt_min: float
    rt_max: float
    ppm_tolerance: float
    full_cohort_cell_status: str
    full_cohort_primary_matrix_area: str
    full_cohort_include_in_primary_matrix: str
    full_cohort_consolidation_state: str
    review_focus: str


@dataclass(frozen=True)
class FullCohortOverlayOutputs:
    index_tsv: Path
    summary_json: Path
    gallery_html: Path
    plot_dir: Path


def build_overlay_requests(
    *,
    review_queue_rows: Sequence[dict[str, str]],
    full_cohort_review_rows: Sequence[dict[str, str]],
    discovery_batch_rows: Sequence[dict[str, str]],
    rt_padding_min: float = 0.75,
    ppm_tolerance: float = 20.0,
) -> tuple[FullCohortOverlayRequest, ...]:
    review_by_family = {
        _value(row, "feature_family_id"): row
        for row in full_cohort_review_rows
        if _value(row, "feature_family_id")
    }
    raw_by_sample = {
        _value(row, "sample_stem"): Path(_value(row, "raw_file"))
        for row in discovery_batch_rows
        if _value(row, "sample_stem") and _value(row, "raw_file")
    }
    requests: list[FullCohortOverlayRequest] = []
    for row in review_queue_rows:
        sample = _value(row, "sample_stem")
        raw_file = raw_by_sample.get(sample)
        if raw_file is None:
            raise ValueError(f"{sample}: missing RAW path in discovery batch index")
        matched_id = _value(row, "full_cohort_matched_peak_hypothesis_id")
        matched_review = review_by_family.get(matched_id, {})
        winner_id = _value(row, "full_cohort_consolidation_winner_group_hypothesis_id")
        winner_review = review_by_family.get(winner_id, {})
        candidate_mz = _required_float(
            row.get("full_cohort_anchor_mz") or matched_review.get("family_center_mz"),
            "candidate m/z",
            row,
        )
        candidate_rt = _required_float(
            row.get("full_cohort_anchor_rt") or matched_review.get("family_center_rt"),
            "candidate RT",
            row,
        )
        peak_start = optional_float(row.get("full_cohort_peak_start_rt"))
        peak_end = optional_float(row.get("full_cohort_peak_end_rt"))
        winner_mz = optional_float(winner_review.get("family_center_mz"))
        winner_rt = optional_float(winner_review.get("family_center_rt"))
        rt_min, rt_max = _rt_window(
            candidate_rt=candidate_rt,
            peak_start=peak_start,
            peak_end=peak_end,
            winner_rt=winner_rt,
            rt_padding_min=rt_padding_min,
        )
        requests.append(
            FullCohortOverlayRequest(
                review_item_id=_value(row, "review_item_id"),
                sample_stem=sample,
                source_peak_hypothesis_id=_value(row, "source_peak_hypothesis_id"),
                source_feature_family_id=_value(row, "source_feature_family_id"),
                full_cohort_matched_peak_hypothesis_id=matched_id,
                full_cohort_consolidation_winner_group_hypothesis_id=winner_id,
                raw_file=raw_file,
                candidate_mz=candidate_mz,
                candidate_anchor_rt=candidate_rt,
                candidate_peak_start_rt=peak_start,
                candidate_peak_end_rt=peak_end,
                winner_mz=winner_mz,
                winner_rt=winner_rt,
                rt_min=rt_min,
                rt_max=rt_max,
                ppm_tolerance=ppm_tolerance,
                full_cohort_cell_status=_value(row, "full_cohort_cell_status"),
                full_cohort_primary_matrix_area=_value(
                    row, "full_cohort_primary_matrix_area"
                ),
                full_cohort_include_in_primary_matrix=_value(
                    row,
                    "full_cohort_include_in_primary_matrix",
                ),
                full_cohort_consolidation_state=_value(
                    row, "full_cohort_consolidation_state"
                ),
                review_focus=_value(row, "review_focus"),
            )
        )
    return tuple(requests)


def write_full_cohort_overlay_outputs(
    output_dir: Path,
    requests: Sequence[FullCohortOverlayRequest],
    *,
    trace_provider: TraceProvider,
    source_run_id: str = "",
) -> FullCohortOverlayOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    plot_dir = output_dir / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    index_rows: list[dict[str, Any]] = []
    for rank, request in enumerate(requests, start=1):
        candidate_rt, candidate_intensity = trace_provider(
            request.raw_file,
            request.candidate_mz,
            request.rt_min,
            request.rt_max,
            request.ppm_tolerance,
        )
        if request.winner_mz is not None:
            if math.isclose(request.winner_mz, request.candidate_mz, rel_tol=1e-9):
                winner_rt = candidate_rt
                winner_intensity = candidate_intensity
            else:
                winner_rt, winner_intensity = trace_provider(
                    request.raw_file,
                    request.winner_mz,
                    request.rt_min,
                    request.rt_max,
                    request.ppm_tolerance,
                )
        else:
            winner_rt = np.array([], dtype=float)
            winner_intensity = np.array([], dtype=float)
        stem = _plot_stem(rank, request)
        png_path = plot_dir / f"{stem}.png"
        pdf_path = plot_dir / f"{stem}.pdf"
        _write_overlay_plot(
            request=request,
            candidate_rt=np.asarray(candidate_rt, dtype=float),
            candidate_intensity=np.asarray(candidate_intensity, dtype=float),
            winner_rt=np.asarray(winner_rt, dtype=float),
            winner_intensity=np.asarray(winner_intensity, dtype=float),
            png_path=png_path,
            pdf_path=pdf_path,
        )
        index_rows.append(
            _index_row(
                request=request,
                source_run_id=source_run_id,
                candidate_rt=np.asarray(candidate_rt, dtype=float),
                candidate_intensity=np.asarray(candidate_intensity, dtype=float),
                winner_intensity=np.asarray(winner_intensity, dtype=float),
                png_path=png_path.relative_to(output_dir),
                pdf_path=pdf_path.relative_to(output_dir),
            )
        )

    index_tsv = output_dir / "backfill_peakhypothesis_overlay_index.tsv"
    summary_json = output_dir / "backfill_peakhypothesis_overlay_summary.json"
    gallery_html = output_dir / "backfill_peakhypothesis_overlay_gallery.html"
    write_tsv(
        index_tsv,
        index_rows,
        OVERLAY_INDEX_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "overlay_count": len(index_rows),
        "smooth_method": SMOOTH_METHOD,
        "smooth_window_points": SMOOTH_WINDOW_POINTS,
        "matrix_contract_changed": False,
        "product_behavior_changed": False,
        "next_action": "feed_machine_same_peak_evidence_to_normal_peak_activation",
    }
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    gallery_html.write_text(_gallery_html(index_rows), encoding="utf-8")
    return FullCohortOverlayOutputs(
        index_tsv=index_tsv,
        summary_json=summary_json,
        gallery_html=gallery_html,
        plot_dir=plot_dir,
    )


def _write_overlay_plot(
    *,
    request: FullCohortOverlayRequest,
    candidate_rt: np.ndarray,
    candidate_intensity: np.ndarray,
    winner_rt: np.ndarray,
    winner_intensity: np.ndarray,
    png_path: Path,
    pdf_path: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    candidate_smooth = _baseline_plus_gaussian15(candidate_intensity)
    winner_smooth = _baseline_plus_gaussian15(winner_intensity)
    fig, axes = plt.subplots(
        2,
        1,
        figsize=(11.8, 7.2),
        sharex=True,
        constrained_layout=True,
        height_ratios=(1.35, 1.0),
    )
    raw_ax, norm_ax = axes
    raw_ax.plot(
        candidate_rt,
        candidate_intensity,
        color="#1f2937",
        linewidth=0.85,
        alpha=0.28,
        label=f"candidate raw m/z {request.candidate_mz:.5g}",
    )
    raw_ax.plot(
        candidate_rt,
        candidate_smooth,
        color="#111827",
        linewidth=1.65,
        label="candidate Gaussian15 smooth",
    )
    if len(winner_rt):
        raw_ax.plot(
            winner_rt,
            winner_intensity,
            color="#d97706",
            linewidth=0.8,
            alpha=0.30,
            label=(
                f"winner raw m/z {request.winner_mz:.5g}"
                if request.winner_mz
                else "winner"
            ),
        )
        raw_ax.plot(
            winner_rt,
            winner_smooth,
            color="#d97706",
            linewidth=1.55,
            label="winner Gaussian15 smooth",
        )
    norm_ax.plot(
        candidate_rt,
        _normalize(candidate_smooth),
        color="#1f2937",
        linewidth=1.55,
        label="candidate Gaussian15 normalized",
    )
    if len(winner_rt):
        norm_ax.plot(
            winner_rt,
            _normalize(winner_smooth),
            color="#d97706",
            linewidth=1.45,
            alpha=0.92,
            label="winner Gaussian15 normalized",
        )
    for ax in axes:
        _draw_review_marks(ax, request)
        ax.grid(alpha=0.20)
    raw_ax.set_ylabel("Intensity")
    norm_ax.set_ylabel("Gaussian15 max-normalized")
    norm_ax.set_xlabel("Retention time (min)")
    title = (
        f"{request.review_item_id}  {request.sample_stem}  "
        f"{request.source_feature_family_id} -> "
        f"{request.full_cohort_matched_peak_hypothesis_id}"
    )
    subtitle = (
        f"status={request.full_cohort_cell_status}; "
        f"area={request.full_cohort_primary_matrix_area}; "
        f"include_primary={request.full_cohort_include_in_primary_matrix}; "
        f"state={request.full_cohort_consolidation_state}; "
        "winner="
        f"{request.full_cohort_consolidation_winner_group_hypothesis_id or 'none'}"
    )
    raw_ax.set_title(f"{title}\n{subtitle}", loc="left", fontsize=10.2)
    handles, labels = raw_ax.get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="outside lower center",
        ncol=3,
        frameon=False,
        fontsize=8,
    )
    fig.savefig(png_path, dpi=180, bbox_inches="tight", facecolor="white")
    fig.savefig(pdf_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _draw_review_marks(ax: Any, request: FullCohortOverlayRequest) -> None:
    if (
        request.candidate_peak_start_rt is not None
        and request.candidate_peak_end_rt is not None
    ):
        ax.axvspan(
            request.candidate_peak_start_rt,
            request.candidate_peak_end_rt,
            color="#14b8a6",
            alpha=0.16,
            label="candidate peak window",
        )
    ax.axvline(
        request.candidate_anchor_rt,
        color="#0f766e",
        linewidth=1.05,
        linestyle="--",
        label="candidate anchor RT",
    )
    if request.winner_rt is not None:
        ax.axvline(
            request.winner_rt,
            color="#b45309",
            linewidth=1.0,
            linestyle=":",
            label="winner RT",
        )


def _index_row(
    *,
    request: FullCohortOverlayRequest,
    source_run_id: str,
    candidate_rt: np.ndarray,
    candidate_intensity: np.ndarray,
    winner_intensity: np.ndarray,
    png_path: Path,
    pdf_path: Path,
) -> dict[str, Any]:
    shape = _machine_shape_evidence(
        request=request,
        candidate_rt=candidate_rt,
        candidate_intensity=candidate_intensity,
    )
    same_peak = _machine_same_peak_evidence(request=request, shape=shape)
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "review_item_id": request.review_item_id,
        "sample_stem": request.sample_stem,
        "source_peak_hypothesis_id": request.source_peak_hypothesis_id,
        "source_feature_family_id": request.source_feature_family_id,
        "full_cohort_matched_peak_hypothesis_id": (
            request.full_cohort_matched_peak_hypothesis_id
        ),
        "full_cohort_consolidation_winner_group_hypothesis_id": (
            request.full_cohort_consolidation_winner_group_hypothesis_id
        ),
        "candidate_mz": request.candidate_mz,
        "candidate_anchor_rt": request.candidate_anchor_rt,
        "candidate_peak_start_rt": request.candidate_peak_start_rt,
        "candidate_peak_end_rt": request.candidate_peak_end_rt,
        "winner_mz": request.winner_mz,
        "winner_rt": request.winner_rt,
        "rt_min": request.rt_min,
        "rt_max": request.rt_max,
        "ppm_tolerance": request.ppm_tolerance,
        "smooth_method": SMOOTH_METHOD,
        "smooth_window_points": SMOOTH_WINDOW_POINTS,
        "machine_shape_decision": shape["machine_shape_decision"],
        "machine_shape_reasons": shape["machine_shape_reasons"],
        "machine_shape_blockers": shape["machine_shape_blockers"],
        "machine_same_peak_verdict": same_peak["machine_same_peak_verdict"],
        "machine_same_peak_reasons": same_peak["machine_same_peak_reasons"],
        "machine_same_peak_blockers": same_peak["machine_same_peak_blockers"],
        "gaussian15_selected_segment_peak_count": (
            shape["gaussian15_selected_segment_peak_count"]
        ),
        "gaussian15_selected_segment_apex_rts": (
            shape["gaussian15_selected_segment_apex_rts"]
        ),
        "gaussian15_shape_context_start_rt": (
            shape["gaussian15_shape_context_start_rt"]
        ),
        "gaussian15_shape_context_end_rt": shape["gaussian15_shape_context_end_rt"],
        "gaussian15_lobe_start_rt": shape["gaussian15_lobe_start_rt"],
        "gaussian15_lobe_end_rt": shape["gaussian15_lobe_end_rt"],
        "gaussian15_lobe_apex_rt": shape["gaussian15_lobe_apex_rt"],
        "gaussian15_lobe_area": shape["gaussian15_lobe_area"],
        "gaussian15_lobe_area_source": shape["gaussian15_lobe_area_source"],
        "gaussian15_lobe_boundary_source": shape["gaussian15_lobe_boundary_source"],
        "gaussian15_lobe_segment_class": shape["gaussian15_lobe_segment_class"],
        "candidate_point_count": len(candidate_intensity),
        "winner_point_count": len(winner_intensity),
        "candidate_max_intensity": _max_intensity(candidate_intensity),
        "winner_max_intensity": _max_intensity(winner_intensity),
        "review_focus": request.review_focus,
        "png_path": str(png_path).replace("\\", "/"),
        "pdf_path": str(pdf_path).replace("\\", "/"),
    }


def _machine_shape_evidence(
    *,
    request: FullCohortOverlayRequest,
    candidate_rt: np.ndarray,
    candidate_intensity: np.ndarray,
) -> dict[str, str]:
    start_rt = request.candidate_peak_start_rt
    end_rt = request.candidate_peak_end_rt
    if start_rt is None or end_rt is None or end_rt <= start_rt:
        return _shape_row(
            decision="shape_evidence_missing",
            reasons=(),
            blockers=("candidate_peak_window_missing",),
            apex_rts=(),
        )
    rt = np.asarray(candidate_rt, dtype=float)
    intensity = np.asarray(candidate_intensity, dtype=float)
    limit = min(rt.size, intensity.size)
    if limit < 3:
        return _shape_row(
            decision="shape_evidence_missing",
            reasons=(),
            blockers=("candidate_trace_too_short",),
            apex_rts=(),
        )
    rt = rt[:limit]
    intensity = intensity[:limit]
    finite = np.isfinite(rt) & np.isfinite(intensity)
    rt = rt[finite]
    intensity = intensity[finite]
    context_start, context_end = _machine_shape_context_bounds(start_rt, end_rt)
    baseline = _safe_asls(intensity)
    try:
        enumeration = enumerate_chrom_peak_segments(
            rt,
            intensity,
            baseline,
            quantitation_context_rt_start=context_start,
            quantitation_context_rt_end=context_end,
            policy=_MACHINE_SHAPE_SEGMENT_POLICY,
        )
    except (ValueError, FloatingPointError):
        return _shape_row(
            decision="shape_evidence_missing",
            reasons=(),
            blockers=("selected_shape_context_malformed_trace",),
            apex_rts=(),
            context_start_rt=context_start,
            context_end_rt=context_end,
        )
    if enumeration.status in {"MALFORMED_TRACE", "WINDOW_TOO_SHORT"}:
        return _shape_row(
            decision="shape_evidence_missing",
            reasons=(),
            blockers=(f"selected_shape_context_{enumeration.status.lower()}",),
            apex_rts=(),
            context_start_rt=context_start,
            context_end_rt=context_end,
        )
    if enumeration.status == "NO_SIGNAL":
        return _shape_row(
            decision="nonstandard_peak_shape",
            reasons=(),
            blockers=("selected_shape_context_no_positive_gaussian15_signal",),
            apex_rts=(),
            context_start_rt=context_start,
            context_end_rt=context_end,
        )
    segments = enumeration.segments
    apex_rts = tuple(segment.apex_rt_min for segment in segments)
    if not segments:
        return _shape_row(
            decision="nonstandard_peak_shape",
            reasons=(),
            blockers=("selected_shape_context_peak_apex_not_resolved",),
            apex_rts=(),
            context_start_rt=context_start,
            context_end_rt=context_end,
        )
    product_segments = tuple(
        segment
        for segment in segments
        if segment.segment_class in {"isolated_peak", "separate_peak"}
    )
    if len(product_segments) == 1 and len(segments) == 1:
        return _shape_row(
            decision="standard_peak_shape_supported",
            reasons=(
                "gaussian15_selected_shape_context_single_complete_unimodal_peak",
            ),
            blockers=(),
            apex_rts=apex_rts,
            context_start_rt=context_start,
            context_end_rt=context_end,
            lobe_segment=product_segments[0],
        )
    blockers = (
        ("selected_shape_context_multiple_gaussian15_peaks",)
        if len(product_segments) > 1
        else ("selected_shape_context_nonstandard_segment_class",)
    )
    return _shape_row(
        decision="nonstandard_peak_shape",
        reasons=(),
        blockers=blockers,
        apex_rts=apex_rts,
        context_start_rt=context_start,
        context_end_rt=context_end,
    )


def _machine_shape_context_bounds(
    start_rt: float,
    end_rt: float,
) -> tuple[float, float]:
    center_rt = (start_rt + end_rt) / 2.0
    context_width = max(
        end_rt - start_rt,
        MIN_MACHINE_SHAPE_CONTEXT_WIDTH_MIN,
    )
    return (
        center_rt - context_width / 2.0,
        center_rt + context_width / 2.0,
    )


def _machine_same_peak_evidence(
    *,
    request: FullCohortOverlayRequest,
    shape: dict[str, str],
) -> dict[str, str]:
    blockers: list[str] = []
    reasons: list[str] = []
    if not request.full_cohort_matched_peak_hypothesis_id:
        blockers.append("full_cohort_peak_hypothesis_missing")
    else:
        reasons.append("slice_gate_hypothesis_anchor_match")
    if request.full_cohort_cell_status not in {"detected", "backfilled"}:
        blockers.append("full_cohort_cell_not_detected_or_rescued")
    else:
        reasons.append(f"full_cohort_cell_status:{request.full_cohort_cell_status}")
    if shape.get("machine_shape_decision") != "standard_peak_shape_supported":
        blockers.append("machine_standard_peak_shape_not_supported")
    else:
        reasons.append("machine_gaussian15_standard_peak_shape_supported")
    if not _positive_number(shape.get("gaussian15_lobe_area", "")):
        blockers.append("machine_gaussian15_lobe_area_not_positive")
    else:
        reasons.append("machine_gaussian15_lobe_area_positive")
    if request.full_cohort_include_in_primary_matrix != "TRUE":
        reasons.append("non_primary_candidate_not_same_peak_blocker")
    if request.full_cohort_consolidation_state:
        reasons.append(
            f"full_cohort_consolidation_state:{request.full_cohort_consolidation_state}",
        )
    verdict = "same_peak_supported" if not blockers else "same_peak_not_supported"
    return {
        "machine_same_peak_verdict": verdict,
        "machine_same_peak_reasons": ";".join(reasons),
        "machine_same_peak_blockers": ";".join(blockers),
    }


def _shape_row(
    *,
    decision: str,
    reasons: Sequence[str],
    blockers: Sequence[str],
    apex_rts: Sequence[float],
    context_start_rt: float | None = None,
    context_end_rt: float | None = None,
    lobe_segment: ChromPeakSegment | None = None,
) -> dict[str, str]:
    return {
        "machine_shape_decision": decision,
        "machine_shape_reasons": ";".join(reasons),
        "machine_shape_blockers": ";".join(blockers),
        "gaussian15_selected_segment_peak_count": str(len(apex_rts)),
        "gaussian15_selected_segment_apex_rts": ";".join(
            f"{value:.6g}" for value in apex_rts
        ),
        "gaussian15_shape_context_start_rt": _format_optional_float(
            context_start_rt,
        ),
        "gaussian15_shape_context_end_rt": _format_optional_float(context_end_rt),
        "gaussian15_lobe_start_rt": _format_optional_float(
            None if lobe_segment is None else lobe_segment.interval.rt_start_min,
        ),
        "gaussian15_lobe_end_rt": _format_optional_float(
            None if lobe_segment is None else lobe_segment.interval.rt_end_min,
        ),
        "gaussian15_lobe_apex_rt": _format_optional_float(
            None if lobe_segment is None else lobe_segment.apex_rt_min,
        ),
        "gaussian15_lobe_area": _format_optional_float(
            None if lobe_segment is None else lobe_segment.morphology_area_shadow,
        ),
        "gaussian15_lobe_area_source": (
            MS1_MORPHOLOGY_AREA_SOURCE if lobe_segment is not None else ""
        ),
        "gaussian15_lobe_boundary_source": (
            lobe_segment.boundary_stop_reason if lobe_segment is not None else ""
        ),
        "gaussian15_lobe_segment_class": (
            lobe_segment.segment_class if lobe_segment is not None else ""
        ),
    }


def _gallery_html(index_rows: Sequence[dict[str, Any]]) -> str:
    cards = "\n".join(_gallery_card(row) for row in index_rows)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>FULL COHORT Hypothesis Overlay Review</title>
<style>
body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #111827; }}
h1 {{ font-size: 22px; margin-bottom: 4px; }}
.note {{ color: #4b5563; margin-bottom: 18px; }}
.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(520px, 1fr));
  gap: 18px;
}}
.card {{
  border: 1px solid #d1d5db;
  border-radius: 6px;
  padding: 12px;
  background: #ffffff;
}}
.meta {{ font-size: 12px; color: #374151; line-height: 1.45; margin-bottom: 8px; }}
img {{ width: 100%; height: auto; border: 1px solid #e5e7eb; }}
a {{ color: #0f766e; }}
</style>
</head>
<body>
<h1>FULL COHORT Hypothesis Overlay Review</h1>
<div class="note">
Review-only overlay gallery. Plots show raw XIC plus Gaussian15 smoothed XIC;
they do not change product matrices.
</div>
<div class="grid">
{cards}
</div>
</body>
</html>
"""


def _gallery_card(row: dict[str, Any]) -> str:
    png_path = html.escape(str(row["png_path"]))
    pdf_path = html.escape(str(row["pdf_path"]))
    title = html.escape(
        f"{row['review_item_id']} {row['source_feature_family_id']} -> "
        f"{row['full_cohort_matched_peak_hypothesis_id']}"
    )
    meta = html.escape(
        f"sample={row['sample_stem']}; winner="
        f"{row['full_cohort_consolidation_winner_group_hypothesis_id']}; "
        f"focus={row['review_focus']}; "
        f"anchor_rt={row['candidate_anchor_rt']}; winner_rt={row['winner_rt']}"
    )
    return f"""<section class="card">
<h2>{title}</h2>
<div class="meta">{meta} | <a href="{pdf_path}">PDF</a></div>
<img src="{png_path}" alt="{title}">
</section>"""


def _plot_stem(rank: int, request: FullCohortOverlayRequest) -> str:
    return (
        f"{rank:02d}_{_safe_token(request.review_item_id)}_"
        f"{_safe_token(request.source_feature_family_id)}_"
        f"{_safe_token(request.full_cohort_matched_peak_hypothesis_id)}_"
        f"{_safe_token(request.sample_stem)}"
    )


def _safe_token(value: str) -> str:
    token = "".join(ch if ch.isalnum() else "_" for ch in value)
    return "_".join(part for part in token.split("_") if part)[:80] or "blank"


def _normalize(values: np.ndarray) -> np.ndarray:
    max_value = _max_intensity(values)
    if max_value <= 0:
        return np.zeros_like(values, dtype=float)
    return np.asarray(values, dtype=float) / max_value


def _baseline_plus_gaussian15(intensity: np.ndarray) -> np.ndarray:
    values = np.asarray(intensity, dtype=float)
    if len(values) == 0:
        return values.copy()
    baseline = _safe_asls(values)
    residual = values - baseline
    return baseline + gaussian15_morphology_trace(
        residual,
        window_points=SMOOTH_WINDOW_POINTS,
    )


def _safe_asls(intensity: np.ndarray) -> np.ndarray:
    values = np.asarray(intensity, dtype=float)
    if len(values) < 3:
        return np.zeros_like(values, dtype=float)
    return asls_baseline(values)


def _max_intensity(values: np.ndarray) -> float:
    if len(values) == 0:
        return 0.0
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    return float(np.max(finite)) if len(finite) else 0.0


def _format_optional_float(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return ""
    return f"{float(value):.6g}"


def _positive_number(value: object) -> bool:
    parsed = optional_float(value)
    return parsed is not None and math.isfinite(parsed) and parsed > 0.0


def _rt_window(
    *,
    candidate_rt: float,
    peak_start: float | None,
    peak_end: float | None,
    winner_rt: float | None,
    rt_padding_min: float,
) -> tuple[float, float]:
    values = [candidate_rt]
    for value in (peak_start, peak_end, winner_rt):
        if value is not None and math.isfinite(value):
            values.append(value)
    rt_min = max(0.0, min(values) - rt_padding_min)
    rt_max = max(values) + rt_padding_min
    return (round(rt_min, 6), round(rt_max, 6))


def _required_float(
    value: object,
    label: str,
    row: dict[str, str],
) -> float:
    parsed = optional_float(value)
    if parsed is None:
        raise ValueError(
            f"{row.get('review_item_id', '<unknown>')}: missing {label}",
        )
    return parsed


def _value(row: dict[str, str], column: str) -> str:
    return text_value(row.get(column, ""))
