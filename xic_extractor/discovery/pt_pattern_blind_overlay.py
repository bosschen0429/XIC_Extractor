"""Blind MS1/XIC overlay review surface for Pt pattern candidates."""

from __future__ import annotations

import html
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Literal, assert_never

from xic_extractor.discovery.pt_pattern_blind_overlay_call import (
    MACHINE_CALL_COLUMNS,
    PACKET_REQUIRED_COLUMNS,
    SCHEMA_VERSION,
    SMOOTH_METHOD,
    SMOOTH_WINDOW_POINTS,
    classify_machine_call,
    machine_call_row,
    validate_machine_call_rows,
    validate_packet_rows,
)
from xic_extractor.discovery.pt_pattern_blind_overlay_writer import (
    TRACE_POINT_REQUIRED_COLUMNS,
    run_blind_overlay,
)
from xic_extractor.peak_detection.ms1_shape_identity import (
    gaussian_smooth_values,
)
from xic_extractor.tabular_io import optional_float

__all__ = (
    "MACHINE_CALL_COLUMNS",
    "PACKET_REQUIRED_COLUMNS",
    "SCHEMA_VERSION",
    "SMOOTH_METHOD",
    "SMOOTH_WINDOW_POINTS",
    "TRACE_POINT_REQUIRED_COLUMNS",
    "classify_machine_call",
    "machine_call_row",
    "run_blind_overlay",
    "validate_machine_call_rows",
    "validate_packet_rows",
)

VISUAL_TIME_SMOOTH_WINDOW_SEC = 20.0
OverlayNormalization = Literal["own_max", "plus1_apex"]

POSITION_ORDER = ("base", "pt_plus_1", "pt_plus_2", "pt_plus_4")
POSITION_COLORS = {
    "base": "#111827",
    "pt_plus_1": "#2563eb",
    "pt_plus_2": "#059669",
    "pt_plus_4": "#dc2626",
}
POSITION_LINE_STYLES = {
    "base": "",
    "pt_plus_1": "9 4",
    "pt_plus_2": "3 3",
    "pt_plus_4": "12 3 2 3",
}
POSITION_LABELS = {
    "base": "base",
    "pt_plus_1": "+1.002111",
    "pt_plus_2": "+2.002271",
    "pt_plus_4": "+4.005213",
}

def render_blind_overlay_html(
    *,
    packet_rows: Sequence[Mapping[str, str]],
    traces_by_candidate: Mapping[str, Mapping[str, Sequence[tuple[float, float]]]],
    context_traces_by_candidate: Mapping[
        str, Mapping[str, Sequence[tuple[float, float]]]
    ],
    has_separate_context: bool,
    title: str,
) -> str:
    cards = "\n".join(
        _render_case_card(
            index=index,
            row=row,
            traces=traces_by_candidate.get(str(row.get("candidate_id", "")), {}),
            context_traces=context_traces_by_candidate.get(
                str(row.get("candidate_id", "")), {}
            ),
            has_separate_context=has_separate_context,
        )
        for index, row in enumerate(packet_rows, start=1)
    )
    escaped_title = html.escape(title)
    trace_explanation = _trace_explanation(has_separate_context)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    *, *::before, *::after {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      max-width: 100%;
      font-family: Arial, "Microsoft JhengHei", sans-serif;
      background: #f8fafc;
      color: #111827;
    }}
    h1, .meta, .case-meta, .boundary-evidence, .note {{
      overflow-wrap: anywhere;
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
      width: 100%;
      min-width: 0;
      max-width: 1480px;
      margin: 0 auto;
      padding: 20px;
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 16px;
    }}
    section {{
      min-width: 0;
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
    .boundary-evidence {{
      border: 1px solid #f59e0b;
      border-radius: 6px;
      background: #fffbeb;
      color: #78350f;
      font-size: 12px;
      line-height: 1.5;
      margin-bottom: 10px;
      padding: 8px 10px;
    }}
    .boundary-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 2px 12px;
      margin-top: 4px;
    }}
    .svg-wrap {{
      border: 1px solid #e5e7eb;
      border-radius: 6px;
      background: #ffffff;
      overflow: hidden;
      aspect-ratio: 760 / 260;
    }}
    .svg-wrap svg {{
      display: block;
      width: 100%;
      height: auto;
    }}
    .overlay-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }}
    .overlay-panel {{
      min-width: 0;
    }}
    .overlay-title {{
      color: #374151;
      font-size: 12px;
      font-weight: 700;
      margin-bottom: 4px;
    }}
    .legend,
    .panel-legend {{
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
      header {{ position: static; padding: 18px 16px 10px; }}
      .case-meta {{ grid-template-columns: 1fr; }}
      .boundary-grid {{ grid-template-columns: 1fr; }}
      .overlay-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{escaped_title}</h1>
    <div class="meta">
      盲測疊圖：本頁刻意隱藏 final machine call 與既有 trace support 判斷，
      但顯示 anchor-local 選段的 diagnostic 邊界證據。請先依 packet row id
      在 review packet CSV 記錄人工判斷，再開啟密封的 machine-call 檔案。
      boundary/ambiguity 僅作 assessability gate；review_required 只會導向
      uncertain，不作正向 accept/reject evidence。
      {trace_explanation}
    </div>
  </header>
  <main>
    {cards}
  </main>
</body>
</html>
"""


def _trace_explanation(has_separate_context: bool) -> str:
    if has_separate_context:
        return (
            "每個案例分開顯示全視窗背景、anchor 局部峰型，以及保留位置間"
            "強度比例的局部圖。背景圖與峰型圖採各自最大值正規化；強度圖固定以"
            "+1 apex 為共同分母。密封機器判斷仍使用 packet 內既有 metrics。"
        )
    return (
        "每個案例依序顯示原始儀器 trace、Gaussian15 點數平滑 trace，以及"
        "20 秒時間平滑 trace。每條 trace 均以自身最大值正規化；密封機器判斷"
        "使用 packet 內既有 decision metrics，不會出現在本頁。"
    )


def _render_case_card(
    *,
    index: int,
    row: Mapping[str, str],
    traces: Mapping[str, Sequence[tuple[float, float]]],
    context_traces: Mapping[str, Sequence[tuple[float, float]]],
    has_separate_context: bool,
) -> str:
    candidate_id = str(row.get("candidate_id", ""))
    packet_row_id = html.escape(str(row.get("packet_row_id", "")))
    source_label = html.escape(str(row.get("source_label", "")))
    sample_stem = html.escape(str(row.get("sample_stem", "")))
    base_mz = html.escape(str(row.get("base_mz", "")))
    base_rt = html.escape(str(row.get("base_rt_apex_min", "")))
    effective_smoothing = html.escape(
        str(row.get("min_effective_smoothing_window_sec", ""))
    )
    max_trace_point_count = html.escape(str(row.get("max_trace_point_count", "")))
    case_label = html.escape(_case_label(index=index))
    overlay_panels = _render_overlay_panels(
        traces,
        context_traces=context_traces,
        has_separate_context=has_separate_context,
    )
    boundary_evidence = _render_boundary_evidence(row)
    point_count = sum(len(points) for points in traces.values())
    return f"""<section>
  <h2>案例 {case_label}</h2>
  <div class="case-meta">
    <div><strong>資料列</strong>: {packet_row_id}</div>
    <div><strong>來源</strong>: {source_label}</div>
    <div><strong>樣本</strong>: {sample_stem}</div>
    <div><strong>base m/z</strong>: {base_mz}</div>
    <div><strong>base RT</strong>: {base_rt} min</div>
    <div><strong>trace 點數</strong>: {point_count}</div>
    <div><strong>有效平滑窗</strong>: {effective_smoothing} sec</div>
    <div><strong>單條 trace 最大點數</strong>: {max_trace_point_count}</div>
  </div>
  {boundary_evidence}
  {overlay_panels}
  <div class="note">候選 ID: {html.escape(candidate_id)}</div>
</section>"""


def _render_boundary_evidence(row: Mapping[str, str]) -> str:
    status = str(row.get("anchor_segmentation_status", "")).strip()
    if not status:
        return ""
    values = {
        "selection": status,
        "boundary": row.get("anchor_segmentation_boundary_review_status", ""),
        "common_start": row.get("anchor_segmentation_common_rt_start_min", ""),
        "common_end": row.get("anchor_segmentation_common_rt_end_min", ""),
        "start_owner": row.get(
            "anchor_segmentation_common_start_owner_labels", ""
        ),
        "end_owner": row.get("anchor_segmentation_common_end_owner_labels", ""),
        "max_left_clip": row.get("anchor_segmentation_max_left_clip_sec", ""),
        "max_right_clip": row.get("anchor_segmentation_max_right_clip_sec", ""),
        "min_retained": row.get(
            "anchor_segmentation_min_common_retained_fraction", ""
        ),
        "right_residual": row.get(
            "anchor_segmentation_max_common_right_residual_fraction", ""
        ),
        "review_positions": row.get(
            "anchor_segmentation_boundary_review_position_labels", ""
        ),
        "descending_right": row.get(
            "anchor_segmentation_descending_at_common_right_position_labels", ""
        ),
    }
    escaped = {key: html.escape(str(value)) for key, value in values.items()}
    common_interval = f"{escaped['common_start']}–{escaped['common_end']} min"
    return f"""<div class="boundary-evidence">
    <strong>Anchor 局部邊界證據（diagnostic only；僅作 assessability gate）</strong>
    <div class="boundary-grid">
      <div>選段狀態: {escaped['selection']}</div>
      <div>邊界 review: {escaped['boundary']}</div>
      <div>共同區間: {common_interval}</div>
      <div>共同左界 owner: {escaped['start_owner']}</div>
      <div>共同右界 owner: {escaped['end_owner']}</div>
      <div>需 review 位置: {escaped['review_positions']}</div>
      <div>最大左側截斷: {escaped['max_left_clip']} sec</div>
      <div>最大右側截斷: {escaped['max_right_clip']} sec</div>
      <div>最小保留比例: {escaped['min_retained']}</div>
      <div>右界 residual/apex 最大值: {escaped['right_residual']}</div>
      <div>共同右界仍下降: {escaped['descending_right']}</div>
    </div>
  </div>"""


def _case_label(*, index: int) -> str:
    return f"Case {index:02d}"


def _render_overlay_panels(
    traces: Mapping[str, Sequence[tuple[float, float]]],
    *,
    context_traces: Mapping[str, Sequence[tuple[float, float]]],
    has_separate_context: bool,
) -> str:
    panels: tuple[
        tuple[
            str,
            dict[str, tuple[tuple[float, float], ...]],
            OverlayNormalization,
        ],
        ...,
    ]
    if has_separate_context:
        panels = (
            (
                "全視窗背景（各自最大值 = 1）",
                _prepare_traces(context_traces, mode="point"),
                "own_max",
            ),
            (
                "Anchor 局部峰型（各自最大值 = 1）",
                _prepare_traces(traces, mode="point"),
                "own_max",
            ),
            (
                "Anchor 局部強度（+1 apex = 1）",
                _prepare_traces(traces, mode="point"),
                "plus1_apex",
            ),
        )
    else:
        panels = (
            ("原始儀器 trace", _prepare_traces(traces, mode="raw"), "own_max"),
            (
                "Gaussian15 點數平滑 trace",
                _prepare_traces(traces, mode="point"),
                "own_max",
            ),
            (
                f"{VISUAL_TIME_SMOOTH_WINDOW_SEC:g} 秒時間平滑 trace",
                _prepare_traces(traces, mode="time"),
                "own_max",
            ),
        )
    panel_legend = _render_position_legend(css_class="panel-legend")
    return (
        '<div class="overlay-grid">'
        + "".join(
            '<div class="overlay-panel">'
            f'<div class="overlay-title">{html.escape(title)}</div>'
            '<div class="svg-wrap">'
            f"{_render_overlay_svg(panel_traces, normalization=normalization)}"
            "</div>"
            f"{panel_legend}"
            "</div>"
            for title, panel_traces, normalization in panels
        )
        + "</div>"
    )


def _render_position_legend(*, css_class: str) -> str:
    items = "".join(
        (
            '<span><span class="swatch" '
            f'style="background:{POSITION_COLORS[label]}"></span>'
            f"{html.escape(POSITION_LABELS[label])}</span>"
        )
        for label in POSITION_ORDER
    )
    return f'<div class="{html.escape(css_class)}">{items}</div>'


def _prepare_traces(
    traces: Mapping[str, Sequence[tuple[float, float]]],
    *,
    mode: Literal["raw", "point", "time"],
) -> dict[str, tuple[tuple[float, float], ...]]:
    if mode == "raw":
        return {
            label: _clean_trace_points(traces.get(label, ()))
            for label in POSITION_ORDER
        }
    if mode == "point":
        return {
            label: smooth_trace_points(traces.get(label, ()))
            for label in POSITION_ORDER
        }
    return {
        label: smooth_trace_points_by_time(
            traces.get(label, ()),
            window_sec=VISUAL_TIME_SMOOTH_WINDOW_SEC,
        )
        for label in POSITION_ORDER
    }


def _render_overlay_svg(
    traces: Mapping[str, Sequence[tuple[float, float]]],
    *,
    normalization: OverlayNormalization = "own_max",
    width: int = 760,
    height: int = 260,
) -> str:
    left = 52
    right = 18
    top = 18
    bottom = 34
    plot_traces = {
        label: _clean_trace_points(traces.get(label, ())) for label in POSITION_ORDER
    }
    all_rt = [
        rt_value
        for label in POSITION_ORDER
        for rt_value, _intensity in plot_traces.get(label, ())
        if math.isfinite(rt_value)
    ]
    if not all_rt:
        return (
            f'<svg viewBox="0 0 {width} {height}" width="100%" '
            'preserveAspectRatio="xMidYMid meet" role="img" '
            'aria-label="no trace points">'
            '<text x="20" y="40" fill="#6b7280">No trace points</text></svg>'
        )
    rt_min = min(all_rt)
    rt_max = max(all_rt)
    if rt_min == rt_max:
        rt_min -= 0.5
        rt_max += 0.5

    match normalization:
        case "own_max":
            denominator_by_label = {
                label: max((value for _rt, value in points), default=0.0)
                for label, points in plot_traces.items()
            }
            y_axis_max = 1.0
            scale_label = "each max = 1"
            aria_label = "normalized Pt pattern traces"
        case "plus1_apex":
            plus1_apex = max(
                (value for _rt, value in plot_traces.get("pt_plus_1", ())),
                default=0.0,
            )
            if plus1_apex <= 0.0:
                return _render_shared_scale_unavailable_svg(
                    width=width,
                    height=height,
                )
            shared_denominator = plus1_apex
            denominator_by_label = {
                label: shared_denominator for label in POSITION_ORDER
            }
            y_axis_max = max(
                1.0,
                max(
                    (
                        value / shared_denominator
                        for points in plot_traces.values()
                        for _rt, value in points
                    ),
                    default=1.0,
                ),
            )
            scale_label = f"+1 apex = 1; y max = {y_axis_max:.2f}"
            aria_label = "shared-scale Pt pattern traces"
        case unreachable:
            assert_never(unreachable)

    plot_width = width - left - right
    plot_height = height - top - bottom

    def x(rt_value: float) -> float:
        return left + ((rt_value - rt_min) / (rt_max - rt_min)) * plot_width

    def y(normalized: float) -> float:
        return top + (1.0 - normalized / y_axis_max) * plot_height

    paths: list[str] = []
    for label in POSITION_ORDER:
        points = list(plot_traces.get(label, ()))
        denominator = denominator_by_label[label]
        if not points or denominator <= 0.0:
            continue
        d = " ".join(
            ("M" if index == 0 else "L")
            + f"{x(rt_value):.2f},{y(max(0.0, intensity / denominator)):.2f}"
            for index, (rt_value, intensity) in enumerate(points)
        )
        dash_array = POSITION_LINE_STYLES[label]
        dash_attribute = f' stroke-dasharray="{dash_array}"' if dash_array else ""
        paths.append(
            f'<path d="{d}" fill="none" stroke="{POSITION_COLORS[label]}"'
            f'{dash_attribute} stroke-width="2.25" stroke-linecap="round" '
            'opacity="0.88" vector-effect="non-scaling-stroke" />'
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
        f'<text x="{left}" y="{height - 10}" font-size="16" fill="#6b7280">'
        f"{rt_min:.3f} min</text>"
        f'<text x="{width - right - 70}" y="{height - 10}" font-size="16" '
        f'fill="#6b7280">{rt_max:.3f} min</text>'
        f'<text x="10" y="{top + 12}" font-size="16" fill="#6b7280">'
        f"{html.escape(scale_label)}</text>"
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="{aria_label}">'
        f"{grid}{axis}{''.join(paths)}</svg>"
    )


def _render_shared_scale_unavailable_svg(*, width: int, height: int) -> str:
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        'preserveAspectRatio="xMidYMid meet" role="img" '
        'aria-label="shared-scale unavailable">'
        '<rect width="100%" height="100%" fill="#ffffff" />'
        '<text x="24" y="48" font-size="16" fill="#6b7280">'
        "+1 apex is missing or zero; shared scale unavailable</text></svg>"
    )


def smooth_trace_points(
    points: Sequence[tuple[float, float]],
    *,
    window_points: int = SMOOTH_WINDOW_POINTS,
) -> tuple[tuple[float, float], ...]:
    sorted_points = _clean_trace_points(points)
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


def smooth_trace_points_by_time(
    points: Sequence[tuple[float, float]],
    *,
    window_sec: float = VISUAL_TIME_SMOOTH_WINDOW_SEC,
) -> tuple[tuple[float, float], ...]:
    sorted_points = _clean_trace_points(points)
    if len(sorted_points) < 3 or window_sec <= 0.0:
        return sorted_points
    half_window_min = (window_sec / 60.0) / 2.0
    sigma_min = (window_sec / 60.0) / 6.0
    if sigma_min <= 0.0:
        return sorted_points
    smoothed: list[tuple[float, float]] = []
    for center_rt, _center_intensity in sorted_points:
        weighted_sum = 0.0
        weight_total = 0.0
        for rt_value, intensity in sorted_points:
            delta = rt_value - center_rt
            if abs(delta) > half_window_min:
                continue
            weight = math.exp(-0.5 * (delta / sigma_min) ** 2)
            weighted_sum += intensity * weight
            weight_total += weight
        smoothed.append(
            (
                center_rt,
                max(0.0, weighted_sum / weight_total)
                if weight_total > 0.0
                else _center_intensity,
            )
        )
    return tuple(smoothed)


def _clean_trace_points(
    points: Sequence[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    return tuple(
        (float(rt_value), max(0.0, float(intensity)))
        for rt_value, intensity in sorted(points)
        if math.isfinite(rt_value) and math.isfinite(intensity)
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
            label: tuple(sorted(points)) for label, points in by_position.items()
        }
        for candidate_id, by_position in grouped.items()
    }
