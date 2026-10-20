"""Chart-first HTML renderer for the Pt pattern hypothesis synthesis."""

# HTML and CSS literals stay readable as complete tags.
# ruff: noqa: E501

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from xic_extractor.discovery.pt_pattern_report_style import (
    escape,
    number,
    percent,
    report_shell,
)
from xic_extractor.tabular_io import optional_float, text_value

_RATIO_VISUALS = (
    ("plus1_over_base_height_ratio", "+1 / base"),
    ("plus1_over_plus2_height_ratio", "+1 / +2"),
    ("plus1_over_plus4_height_ratio", "+1 / +4"),
)


def render_hypothesis_synthesis_html(
    *,
    summary: Mapping[str, object],
    primary_ratio_summary: Sequence[Mapping[str, object]],
    background_ratio_summary: Sequence[Mapping[str, object]],
) -> str:
    """Render one decision-first visual surface from existing diagnostics."""

    ratio = _mapping(summary.get("ratio"))
    interference = _mapping(summary.get("interference"))
    plus1_seed = _mapping(summary.get("plus1_seed"))
    inclusion = _mapping(summary.get("inclusion_list"))
    accepted = _ratio_population_count(ratio, prefix="primary")
    background = _ratio_population_count(ratio, prefix="background")
    total = _integer(interference.get("input_accept_count"))
    hotspot = _integer(interference.get("rt_12p5_13p5_accept_count"))
    overlap = _integer(interference.get("rt_12p5_13p5_overlap_candidate_count"))
    interference_available = interference.get("available") is True
    plus1_available = plus1_seed.get("available") is True
    inclusion_available = inclusion.get("available") is True
    top102 = _mapping(inclusion.get("top102"))
    manual_known = _mapping(inclusion.get("manual_known"))
    top102_misses = _integer(top102.get("features_all4_exist_but_no_candidate"))
    manual_known_misses = _integer(
        manual_known.get("features_all4_exist_but_no_candidate")
    )
    assembly_misses = top102_misses + manual_known_misses
    if inclusion_available and assembly_misses > 0:
        verdict_title = "先查 candidate assembly，不要先調 ratio gate"
        verdict_text = (
            "輸入的 inclusion artifact 顯示有 feature 已齊、卻沒有組成 "
            "candidate 的 rows。ratio 仍只能幫人工排序與解釋，不能決定 "
            "detected。"
        )
        next_loop = (
            f"先 replay {top102_misses} 個 Top102 與 "
            f"{manual_known_misses} 個 manual-known rows，定位 m/z pairing、"
            "RT grouping 或 feature source 的第一個斷點。"
        )
    elif interference_available:
        verdict_title = "目前先保留 interference 與 ratio 兩條假說"
        verdict_text = (
            "已有 RT hotspot evidence，但沒有足夠 assembly evidence 可宣告 "
            "root cause；ratio 也仍是 diagnostic signal。"
        )
        next_loop = "補齊 assembly artifact，再比較第一個可重現斷點。"
    else:
        verdict_title = "目前只有 ratio artifact"
        verdict_text = (
            "可以比較 ratio 分布，但尚未提供 interference、+1 threshold 或 "
            "assembly evidence，因此不能判定主要漏失來源。"
        )
        next_loop = "先補一個能區分 assembly 與 ratio 假說的 bounded artifact。"
    hotspot_description = (
        f"{total} 個原始 accepted seeds 中，{hotspot} 個落在 "
        f"12.5-13.5 min；其中 {overlap} 個帶有 overlap candidate。"
        if interference_available
        else "未提供 interference artifact；RT hotspot 狀態未知。"
    )
    plus1_description = (
        "下圖依輸入 artifact 顯示各 +1 threshold 的 recovered count 與 "
        "recall；此 report 不把任一 threshold 升格為 detector gate。"
        if plus1_available
        else "未提供 +1 threshold artifact；threshold sensitivity 狀態未知。"
    )
    assembly_description = (
        f"Top102 有 {top102_misses} 筆、manual known 有 "
        f"{manual_known_misses} 筆四個位置都存在卻沒組成 candidate。"
        if inclusion_available
        else "未提供 inclusion artifact；assembly 漏失數未知。"
    )
    body = "".join(
        [
            f'<section class="verdict"><div><h2>{escape(verdict_title)}</h2><p>{escape(verdict_text)}</p></div><aside class="signal"><strong>下一個 evidence loop</strong><span>{escape(next_loop)}</span></aside></section>',
            '<section class="metric-strip">',
            _metric(accepted, "ratio 分析主群"),
            _metric(
                hotspot if interference_available else "n/a",
                (
                    f"集中在 12.5-13.5 min（總數 {total}）"
                    if interference_available
                    else "12.5-13.5 min（未提供）"
                ),
            ),
            _metric(
                overlap if interference_available else "n/a",
                (
                    f"其中具 overlap（窗口內 {hotspot}）"
                    if interference_available
                    else "overlap（未提供）"
                ),
            ),
            "</section>",
            f'<section class="report-section"><div class="section-head"><h2>1. 候選集中在哪裡</h2><p>{escape(hotspot_description)}</p></div>',
            (
                _rt_hotspot_chart(total, hotspot, overlap)
                if interference_available
                else _unavailable_chart("未提供 interference artifact")
            ),
            f'<p class="takeaway">13 分鐘窗口包含 {percent(hotspot, total)} 的候選；窗口內 {percent(overlap, hotspot)} 帶 overlap。這支持 interference 假說值得追，但尚未證明物理機制。</p></section>',
            '<section class="report-section"><div class="section-head"><h2>2. Ratio 能不能分開兩群</h2><p>線段是 IQR，中間點是 median，三個公式共用 log 尺度。accepted 與 background 線段大量重疊時，median 差距不能直接升級成 hard gate。</p></div>',
            _ratio_chart(primary_ratio_summary, background_ratio_summary),
            '<p class="takeaway">比較各公式的 median 與 IQR；是否分離必須由本次輸入判讀，不能沿用舊 dataset 結論，也不能直接定生死。</p></section>',
            f'<section class="report-section"><div class="section-head"><h2>3. +1 門檻是不是漏失來源</h2><p>{escape(plus1_description)}</p></div>',
            (
                _threshold_chart(plus1_seed)
                if plus1_available
                else _unavailable_chart("未提供 +1 threshold artifact")
            ),
            '<p class="takeaway">+1 intensity 在此仍是 seed/prioritization 診斷，不是 hard detector gate。</p></section>',
            '<section class="report-section"><div class="section-head"><h2>4. 已知清單掉在哪一層</h2><p>比較 feature 已齊、candidate 已組成、最後 accepted 三個階段。優先查的是「all 4 features 已存在，但 candidate 沒出現」。</p></div>',
            (
                _assembly_chart(inclusion)
                if inclusion_available
                else _unavailable_chart("未提供 inclusion artifact")
            ),
            f'<p class="takeaway">{escape(assembly_description)}</p></section>',
            f'<section class="next-step"><h2>後續只做一件事</h2><p>{escape(next_loop)}</p></section>',
            _scope_details(summary, background),
        ]
    )
    return report_shell(
        title="Pt Pattern 現況總覽",
        meta=f"{text_value(summary.get('report_id'))} · diagnostic_only · no RAW",
        body=body,
    )


def _metric(value: int | str, label: str) -> str:
    return (
        f'<div class="metric"><strong>{escape(str(value))}</strong>'
        f"<span>{escape(label)}</span></div>"
    )


def _unavailable_chart(message: str) -> str:
    return f'<div class="chart-shell">{escape(message)}。</div>'


def _ratio_population_count(ratio: Mapping[str, object], *, prefix: str) -> str:
    common_count = ratio.get(f"{prefix}_row_count")
    if isinstance(common_count, int) and not isinstance(common_count, bool):
        return str(common_count)
    count_map = _mapping(ratio.get(f"{prefix}_row_count_by_ratio"))
    counts = sorted(
        {
            value
            for value in count_map.values()
            if isinstance(value, int) and not isinstance(value, bool)
        }
    )
    if not counts:
        return "0"
    if len(counts) == 1:
        return str(counts[0])
    return f"{counts[0]}–{counts[-1]}（依 ratio）"


def _rt_hotspot_chart(total: int, hotspot: int, overlap: int) -> str:
    width = 760.0
    values = (
        ("all RT", total, "#bcd8d2"),
        ("12.5-13.5 min", hotspot, "#b77b18"),
        ("hotspot + overlap", overlap, "#b5522c"),
    )
    rows: list[str] = []
    for index, (label, value, color) in enumerate(values):
        y = 30 + index * 58
        bar = _fraction_width(value, total, width)
        rows.append(
            f'<text class="label" x="18" y="{y + 20}">{escape(label)}</text>'
            f'<rect x="150" y="{y}" width="{bar:.1f}" height="32" fill="{color}"/>'
            f'<text class="value" x="{min(160 + bar, 920):.1f}" y="{y + 21}">{value}</text>'
        )
    return (
        '<div class="chart-shell"><p class="chart-title">Accepted seeds across RT concentration</p>'
        f'<p class="chart-subtitle">同一分母 {total}；內層越接近外層，代表 13 分鐘結構越主導整批資料</p>'
        '<svg data-chart="rt-hotspot" viewBox="0 0 940 210" role="img" aria-label="13 minute hotspot concentration">'
        f"{''.join(rows)}</svg></div>"
    )


def _ratio_chart(
    primary_rows: Sequence[Mapping[str, object]],
    background_rows: Sequence[Mapping[str, object]],
) -> str:
    primary = _summary_map(primary_rows)
    background = _summary_map(background_rows)
    left, width = 178.0, 690.0
    parts = [
        '<div class="chart-shell"><p class="chart-title">Height ratio separation</p>',
        '<p class="chart-subtitle">median + IQR，log scale；gold = accepted，teal = background</p>',
        '<svg data-chart="ratio-separation" viewBox="0 0 940 330" role="img" aria-label="accepted and background ratio separation">',
    ]
    for tick in (0.1, 0.3, 1.0, 3.0, 10.0, 30.0):
        x = _log_x(tick, left, width)
        parts.append(
            f'<line class="grid" x1="{x:.1f}" y1="34" x2="{x:.1f}" y2="286"/>'
            f'<text x="{x:.1f}" y="312" text-anchor="middle">{number(tick)}</text>'
        )
    for index, (key, label) in enumerate(_RATIO_VISUALS):
        y = 74.0 + index * 82.0
        parts.append(
            f'<text class="label" x="12" y="{y + 10:.1f}">{escape(label)}</text>'
        )
        parts.extend(_interval_marks(primary.get(key), y, left, width, True))
        parts.extend(_interval_marks(background.get(key), y + 29, left, width, False))
    parts.append(
        '</svg><div class="legend"><span><i class="swatch gold"></i>accepted</span>'
        '<span><i class="swatch"></i>background</span></div></div>'
    )
    return "".join(parts)


def _threshold_chart(plus1_seed: Mapping[str, object]) -> str:
    thresholds = _mapping(plus1_seed.get("thresholds"))
    points: list[tuple[float, float, int]] = []
    for key, raw in thresholds.items():
        threshold = optional_float(key)
        row = _mapping(raw)
        recall = optional_float(row.get("current_146_accept_recall_fraction"))
        recovered = _integer(row.get("current_146_accept_recovered"))
        if threshold is not None and recall is not None:
            points.append((threshold, recall, recovered))
    points.sort()
    if not points:
        return '<div class="chart-shell">沒有 +1 threshold 資料。</div>'
    left, top, width, height = 96.0, 28.0, 760.0, 190.0
    path: list[str] = []
    marks: list[str] = []
    for threshold, recall, recovered in points:
        x = left + _log_fraction(threshold, points[0][0], points[-1][0]) * width
        y = top + (1.0 - min(max(recall, 0.0), 1.0)) * height
        path.append(f"{x:.1f},{y:.1f}")
        marks.append(
            f'<circle class="primary-dot" cx="{x:.1f}" cy="{y:.1f}" r="6"/>'
            f'<text class="value" x="{x:.1f}" y="{y - 12:.1f}" text-anchor="middle">{recovered}</text>'
            f'<text x="{x:.1f}" y="252" text-anchor="middle">{number(threshold, digits=2)}</text>'
        )
    return (
        '<div class="chart-shell"><p class="chart-title">Accepted recovery by +1 height threshold</p>'
        '<p class="chart-subtitle">點上數字 = 輸入 artifact 的 recovered count；X 軸為 log threshold</p>'
        '<svg data-chart="plus1-threshold" viewBox="0 0 940 285" role="img" aria-label="plus one threshold sensitivity">'
        f'<line class="grid" x1="{left}" y1="{top}" x2="{left + width}" y2="{top}"/>'
        f'<line class="grid" x1="{left}" y1="{top + height / 2}" x2="{left + width}" y2="{top + height / 2}"/>'
        f'<line class="axis" x1="{left}" y1="{top + height}" x2="{left + width}" y2="{top + height}"/>'
        f'<text x="18" y="{top + 4}">100%</text><text x="28" y="{top + height / 2 + 4}">50%</text><text x="35" y="{top + height + 4}">0%</text>'
        f'<polyline points="{" ".join(path)}" fill="none" stroke="#b77b18" stroke-width="4" stroke-linejoin="round"/>'
        f"{''.join(marks)}</svg></div>"
    )


def _assembly_chart(inclusion: Mapping[str, object]) -> str:
    panels = [
        _stage_panel("Top102", _mapping(inclusion.get("top102"))),
        _stage_panel("Manual known", _mapping(inclusion.get("manual_known"))),
    ]
    return (
        '<div class="chart-shell" data-chart="assembly-stage">'
        '<p class="chart-title">Feature → candidate → accepted</p>'
        '<p class="chart-subtitle">每個 panel 以該清單總數為 100%；gold 標出 candidate assembly 斷點</p>'
        f'<div class="stage-grid">{"".join(panels)}</div></div>'
    )


def _stage_panel(title: str, row: Mapping[str, object]) -> str:
    total = _integer(row.get("row_count"))
    no_candidate = _integer(row.get("features_all4_exist_but_no_candidate"))
    stages = (
        ("清單總數", total, False),
        ("四個 feature", _integer(row.get("feature_all4_exist_20ppm")), False),
        ("組成 candidate", _integer(row.get("candidate_match_rows_20ppm")), True),
        ("最後 accepted", _integer(row.get("accepted_match_rows_20ppm")), False),
    )
    return (
        f'<div class="stage-panel"><h3>{escape(title)}</h3>'
        f"{''.join(_stage_row(label, value, total, highlight) for label, value, highlight in stages)}"
        f'<p class="stage-note">{no_candidate} 筆 all 4 exist，但沒有 candidate</p></div>'
    )


def _stage_row(label: str, value: int, total: int, highlight: bool) -> str:
    css_class = "stage-row highlight" if highlight else "stage-row"
    return (
        f'<div class="{css_class}"><span>{escape(label)}</span>'
        f'<div class="stage-track"><div class="stage-fill" style="width:{_fraction_width(value, total, 100):.1f}%"></div></div>'
        f"<strong>{value}</strong></div>"
    )


def _interval_marks(
    row: Mapping[str, object] | None,
    y: float,
    left: float,
    width: float,
    primary: bool,
) -> list[str]:
    if row is None:
        return []
    q25 = optional_float(row.get("q25"))
    median = optional_float(row.get("median"))
    q75 = optional_float(row.get("q75"))
    if q25 is None or median is None or q75 is None:
        return []
    x1, xm, x2 = (
        _log_x(q25, left, width),
        _log_x(median, left, width),
        _log_x(q75, left, width),
    )
    line_class = "primary-line" if primary else "background-line"
    dot_class = "primary-dot" if primary else "background-dot"
    radius = 6 if primary else 5
    return [
        f'<line class="{line_class}" x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}"/>',
        f'<circle class="{dot_class}" cx="{xm:.1f}" cy="{y:.1f}" r="{radius}"/>',
        f'<text class="value" x="{min(xm + 10, 885):.1f}" y="{y + 4:.1f}">{number(median, digits=4)}</text>',
    ]


def _summary_map(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    return {text_value(row.get("ratio_name")): row for row in rows}


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _integer(value: object) -> int:
    return int(optional_float(value) or 0)


def _fraction_width(value: int, total: int, width: float) -> float:
    if total <= 0:
        return 0.0
    return min(max(value / total, 0.0), 1.0) * width


def _log_x(value: float, left: float, width: float) -> float:
    bounded = min(max(value, 0.1), 30.0)
    return left + (math.log10(bounded) + 1.0) / math.log10(300.0) * width


def _log_fraction(value: float, minimum: float, maximum: float) -> float:
    if minimum <= 0 or maximum <= minimum:
        return 0.0
    return math.log(value / minimum) / math.log(maximum / minimum)


def _scope_details(summary: Mapping[str, object], background: str) -> str:
    return (
        '<details><summary>技術範圍與限制</summary><div class="table-wrap"><table>'
        "<thead><tr><th>項目</th><th>狀態</th></tr></thead><tbody>"
        f"<tr><td>report id</td><td><code>{escape(text_value(summary.get('report_id')))}</code></td></tr>"
        f"<tr><td>background rows</td><td>{escape(background)}</td></tr>"
        "<tr><td>RAW access</td><td>無</td></tr>"
        "<tr><td>detector / counted detection / Matrix</td><td>全部未改</td></tr>"
        "</tbody></table></div></details>"
    )
