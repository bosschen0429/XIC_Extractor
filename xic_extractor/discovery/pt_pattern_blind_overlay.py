"""Blind MS1/XIC overlay review surface for Pt pattern candidates."""

from __future__ import annotations

import html
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_review_packet import (
    EVIDENCE_OWNER,
    READINESS_TIER,
)
from xic_extractor.discovery.pt_pattern_trace_evidence import (
    TRACE_DECISION_BASIS,
    TRACE_SMOOTH_METHOD,
    TRACE_SMOOTH_WINDOW_POINTS,
)
from xic_extractor.peak_detection.ms1_shape_identity import (
    gaussian_smooth_values,
)
from xic_extractor.tabular_io import (
    optional_float,
    read_delimited_rows,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_blind_overlay.v2"
SMOOTH_METHOD = TRACE_SMOOTH_METHOD
SMOOTH_WINDOW_POINTS = TRACE_SMOOTH_WINDOW_POINTS

PACKET_REQUIRED_COLUMNS = (
    "packet_label",
    "source_label",
    "packet_row_id",
    "sample_stem",
    "candidate_id",
    "base_mz",
    "base_rt_apex_min",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
    "combined_pt_pattern_status",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "oracle_status",
)

TRACE_POINT_REQUIRED_COLUMNS = (
    "candidate_id",
    "position_label",
    "rt_min",
    "intensity",
)

MACHINE_CALL_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "packet_label",
    "source_label",
    "packet_row_id",
    "candidate_id",
    "machine_call",
    "machine_call_reason",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
    "combined_pt_pattern_status",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "oracle_status",
)

POSITION_ORDER = ("base", "pt_plus_1", "pt_plus_2", "pt_plus_4")
POSITION_COLORS = {
    "base": "#111827",
    "pt_plus_1": "#2563eb",
    "pt_plus_2": "#059669",
    "pt_plus_4": "#dc2626",
}
POSITION_LABELS = {
    "base": "base",
    "pt_plus_1": "+1.0021",
    "pt_plus_2": "+2.0023",
    "pt_plus_4": "+4.0052",
}


def run_blind_overlay(
    *,
    review_packet_csv: Path,
    trace_points_csv: Path,
    output_dir: Path,
    title: str,
) -> dict[str, Path]:
    packet_rows = read_delimited_rows(
        review_packet_csv,
        required_columns=PACKET_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    trace_points = read_delimited_rows(
        trace_points_csv,
        required_columns=TRACE_POINT_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    traces_by_candidate = _load_traces_by_candidate(trace_points)
    machine_rows = tuple(machine_call_row(row) for row in packet_rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "pt_pattern_blind_overlay_gallery.html"
    machine_calls_csv = output_dir / "pt_pattern_machine_calls_sealed.csv"
    summary_json = output_dir / "pt_pattern_blind_overlay_summary.json"

    html_path.write_text(
        render_blind_overlay_html(
            packet_rows=packet_rows,
            traces_by_candidate=traces_by_candidate,
            title=title,
        ),
        encoding="utf-8",
    )
    write_delimited_rows(machine_calls_csv, machine_rows, MACHINE_CALL_COLUMNS)

    summary = {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "review_packet_csv": str(review_packet_csv),
        "trace_points_csv": str(trace_points_csv),
        "output_dir": str(output_dir),
        "case_count": len(packet_rows),
        "smooth_method": SMOOTH_METHOD,
        "smooth_window_points": SMOOTH_WINDOW_POINTS,
        "trace_decision_basis": TRACE_DECISION_BASIS,
        "smooth_applies_to": "gallery_html_and_sealed_machine_calls",
        "sealed_machine_calls_written": True,
        "blind_to_machine_calls_in_html": True,
        "oracle_status": "pending_human_review",
        "diagnostic_only": True,
        "outputs": {
            "gallery_html": str(html_path),
            "machine_calls_csv": str(machine_calls_csv),
        },
    }
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "gallery_html": html_path,
        "machine_calls_csv": machine_calls_csv,
        "summary_json": summary_json,
    }


def machine_call_row(row: Mapping[str, str]) -> dict[str, object]:
    machine_call, reason = classify_machine_call(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "packet_label": row.get("packet_label", ""),
        "source_label": row.get("source_label", ""),
        "packet_row_id": row.get("packet_row_id", ""),
        "candidate_id": row.get("candidate_id", ""),
        "machine_call": machine_call,
        "machine_call_reason": reason,
        "trace_decision_basis": row.get("trace_decision_basis", ""),
        "trace_smoothing_method": row.get("trace_smoothing_method", ""),
        "trace_smoothing_window_points": row.get(
            "trace_smoothing_window_points", ""
        ),
        "combined_pt_pattern_status": row.get("combined_pt_pattern_status", ""),
        "non_base_positions_supported": row.get("non_base_positions_supported", ""),
        "max_abs_apex_delta_sec": row.get("max_abs_apex_delta_sec", ""),
        "min_pearson_r_to_base": row.get("min_pearson_r_to_base", ""),
        "min_active_overlap_fraction": row.get("min_active_overlap_fraction", ""),
        "oracle_status": row.get("oracle_status", ""),
    }


def classify_machine_call(row: Mapping[str, str]) -> tuple[str, str]:
    basis = str(row.get("trace_decision_basis", "")).strip()
    smooth_method = str(row.get("trace_smoothing_method", "")).strip()
    smooth_window = optional_float(row.get("trace_smoothing_window_points"))
    if (
        basis != TRACE_DECISION_BASIS
        or smooth_method != SMOOTH_METHOD
        or smooth_window != float(SMOOTH_WINDOW_POINTS)
    ):
        return "uncertain", "unsupported_or_stale_trace_decision_basis"

    status = str(row.get("combined_pt_pattern_status", "")).strip()
    supported = optional_float(row.get("non_base_positions_supported"))
    apex_delta = optional_float(row.get("max_abs_apex_delta_sec"))
    min_pearson = optional_float(row.get("min_pearson_r_to_base"))
    min_overlap = optional_float(row.get("min_active_overlap_fraction"))

    if (
        status == "pt_pattern_detected_diagnostic"
        and supported is not None
        and supported >= 3
        and apex_delta is not None
        and apex_delta <= 10.0
        and min_pearson is not None
        and min_pearson >= 0.70
        and min_overlap is not None
        and min_overlap >= 0.50
    ):
        return "accept", "all_positions_trace_supported_with_close_apex_and_shape"

    if status in {
        "pt_pattern_candidate_incomplete",
        "pt_pattern_candidate_base_missing",
    }:
        return "reject", status
    if supported is not None and supported <= 1:
        return "reject", "one_or_fewer_non_base_positions_supported"
    if min_pearson is not None and min_pearson < 0.30:
        return "reject", "low_min_trace_shape_correlation"

    return "uncertain", "requires_visual_or_human_review"


def render_blind_overlay_html(
    *,
    packet_rows: Sequence[Mapping[str, str]],
    traces_by_candidate: Mapping[str, Mapping[str, Sequence[tuple[float, float]]]],
    title: str,
) -> str:
    cards = "\n".join(
        _render_case_card(
            index=index,
            row=row,
            traces=traces_by_candidate.get(str(row.get("candidate_id", "")), {}),
        )
        for index, row in enumerate(packet_rows, start=1)
    )
    escaped_title = html.escape(title)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <title>{escaped_title}</title>
  <style>
    body {{
      margin: 0;
      font-family: Arial, "Microsoft JhengHei", sans-serif;
      background: #f8fafc;
      color: #111827;
    }}
    header {{
      padding: 24px 32px 12px;
      border-bottom: 1px solid #d1d5db;
      background: #ffffff;
      position: sticky;
      top: 0;
      z-index: 2;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 22px;
      letter-spacing: 0;
    }}
    .meta {{
      color: #4b5563;
      font-size: 13px;
      line-height: 1.5;
    }}
    main {{
      max-width: 1240px;
      margin: 0 auto;
      padding: 20px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(520px, 1fr));
      gap: 16px;
    }}
    section {{
      background: #ffffff;
      border: 1px solid #d1d5db;
      border-radius: 8px;
      padding: 14px;
    }}
    h2 {{
      margin: 0 0 8px;
      font-size: 15px;
      letter-spacing: 0;
    }}
    .case-meta {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 4px 12px;
      color: #374151;
      font-size: 12px;
      margin-bottom: 10px;
    }}
    .svg-wrap {{
      border: 1px solid #e5e7eb;
      border-radius: 6px;
      background: #ffffff;
      overflow: hidden;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 8px;
      font-size: 12px;
      color: #374151;
    }}
    .swatch {{
      display: inline-block;
      width: 16px;
      height: 3px;
      margin-right: 5px;
      vertical-align: middle;
    }}
    .note {{
      color: #6b7280;
      font-size: 12px;
      margin-top: 8px;
    }}
    @media (max-width: 640px) {{
      main {{ grid-template-columns: 1fr; padding: 12px; }}
      header {{ padding: 18px 16px 10px; }}
      .case-meta {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{escaped_title}</h1>
    <div class="meta">
      Blind review overlay. This HTML intentionally hides machine calls and
      diagnostic status buckets. Use the packet row id to record your own call
      in the review packet CSV before opening the sealed machine-call file.
      Overlay curves and sealed machine calls use the same Gaussian15-smoothed
      decision metrics. The trace-point CSV remains the raw vendor trace.
    </div>
  </header>
  <main>
    {cards}
  </main>
</body>
</html>
"""


def _render_case_card(
    *,
    index: int,
    row: Mapping[str, str],
    traces: Mapping[str, Sequence[tuple[float, float]]],
) -> str:
    candidate_id = str(row.get("candidate_id", ""))
    packet_row_id = html.escape(str(row.get("packet_row_id", "")))
    source_label = html.escape(str(row.get("source_label", "")))
    sample_stem = html.escape(str(row.get("sample_stem", "")))
    base_mz = html.escape(str(row.get("base_mz", "")))
    base_rt = html.escape(str(row.get("base_rt_apex_min", "")))
    svg = _render_overlay_svg(traces)
    legend = "".join(
        (
            '<span><span class="swatch" '
            f'style="background:{POSITION_COLORS[label]}"></span>'
            f"{html.escape(POSITION_LABELS[label])}</span>"
        )
        for label in POSITION_ORDER
    )
    point_count = sum(len(points) for points in traces.values())
    return f"""<section>
  <h2>Case {index:02d}</h2>
  <div class="case-meta">
    <div><strong>packet row</strong>: {packet_row_id}</div>
    <div><strong>source</strong>: {source_label}</div>
    <div><strong>sample</strong>: {sample_stem}</div>
    <div><strong>base m/z</strong>: {base_mz}</div>
    <div><strong>base RT</strong>: {base_rt} min</div>
    <div><strong>trace points</strong>: {point_count}</div>
  </div>
  <div class="svg-wrap">{svg}</div>
  <div class="legend">{legend}</div>
  <div class="note">candidate: {html.escape(candidate_id)}</div>
</section>"""


def _render_overlay_svg(
    traces: Mapping[str, Sequence[tuple[float, float]]],
    *,
    width: int = 760,
    height: int = 260,
) -> str:
    left = 52
    right = 18
    top = 18
    bottom = 34
    smoothed_traces = {
        label: smooth_trace_points(traces.get(label, ()))
        for label in POSITION_ORDER
    }
    all_rt = [
        rt_value
        for label in POSITION_ORDER
        for rt_value, _intensity in smoothed_traces.get(label, ())
        if math.isfinite(rt_value)
    ]
    if not all_rt:
        return (
            f'<svg viewBox="0 0 {width} {height}" width="100%" '
            f'height="{height}" role="img" aria-label="no trace points">'
            '<text x="20" y="40" fill="#6b7280">No trace points</text></svg>'
        )
    rt_min = min(all_rt)
    rt_max = max(all_rt)
    if rt_min == rt_max:
        rt_min -= 0.5
        rt_max += 0.5

    plot_width = width - left - right
    plot_height = height - top - bottom

    def x(rt_value: float) -> float:
        return left + ((rt_value - rt_min) / (rt_max - rt_min)) * plot_width

    def y(normalized: float) -> float:
        return top + (1.0 - normalized) * plot_height

    paths: list[str] = []
    for label in POSITION_ORDER:
        points = list(smoothed_traces.get(label, ()))
        if not points:
            continue
        max_intensity = max((value for _rt, value in points), default=0.0)
        if max_intensity <= 0:
            continue
        d = " ".join(
            (
                "M" if index == 0 else "L"
            )
            + f"{x(rt_value):.2f},{y(max(0.0, intensity / max_intensity)):.2f}"
            for index, (rt_value, intensity) in enumerate(points)
        )
        paths.append(
            f'<path d="{d}" fill="none" stroke="{POSITION_COLORS[label]}" '
            'stroke-width="2" vector-effect="non-scaling-stroke" />'
        )

    grid = "\n".join(
        f'<line x1="{left}" y1="{top + plot_height * frac:.2f}" '
        f'x2="{width - right}" y2="{top + plot_height * frac:.2f}" '
        'stroke="#e5e7eb" stroke-width="1" />'
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0)
    )
    axis = (
        f'<line x1="{left}" y1="{height - bottom}" x2="{width - right}" '
        f'y2="{height - bottom}" stroke="#9ca3af" />'
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" '
        'stroke="#9ca3af" />'
        f'<text x="{left}" y="{height - 10}" font-size="11" fill="#6b7280">'
        f'{rt_min:.3f} min</text>'
        f'<text x="{width - right - 70}" y="{height - 10}" font-size="11" '
        f'fill="#6b7280">{rt_max:.3f} min</text>'
        f'<text x="10" y="{top + 12}" font-size="11" fill="#6b7280">'
        'normalized</text>'
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'height="{height}" role="img" aria-label="normalized Pt pattern traces">'
        f"{grid}{axis}{''.join(paths)}</svg>"
    )


def smooth_trace_points(
    points: Sequence[tuple[float, float]],
    *,
    window_points: int = SMOOTH_WINDOW_POINTS,
) -> tuple[tuple[float, float], ...]:
    sorted_points = tuple(
        (float(rt_value), max(0.0, float(intensity)))
        for rt_value, intensity in sorted(points)
        if math.isfinite(rt_value) and math.isfinite(intensity)
    )
    if len(sorted_points) < 3:
        return sorted_points
    smoothed = gaussian_smooth_values(
        [intensity for _rt_value, intensity in sorted_points],
        points=window_points,
    )
    return tuple(
        (rt_value, max(0.0, smoothed_intensity))
        for (rt_value, _raw_intensity), smoothed_intensity in zip(
            sorted_points,
            smoothed,
            strict=True,
        )
    )


def _load_traces_by_candidate(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, dict[str, tuple[tuple[float, float], ...]]]:
    grouped: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        candidate_id = str(row.get("candidate_id", "")).strip()
        position_label = str(row.get("position_label", "")).strip()
        rt_value = optional_float(row.get("rt_min"))
        intensity = optional_float(row.get("intensity"))
        if not candidate_id or not position_label:
            continue
        if rt_value is None or intensity is None:
            continue
        grouped[candidate_id][position_label].append((rt_value, intensity))
    return {
        candidate_id: {
            label: tuple(sorted(points))
            for label, points in by_position.items()
        }
        for candidate_id, by_position in grouped.items()
    }
