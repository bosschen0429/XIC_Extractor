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
    accepted = _integer(ratio.get("primary_row_count"))
    background = _integer(ratio.get("background_row_count"))
    total = _integer(interference.get("input_accept_count"))
    hotspot = _integer(interference.get("rt_12p5_13p5_accept_count"))
    overlap = _integer(interference.get("rt_12p5_13p5_overlap_candidate_count"))
    body = "".join(
        [
            '<section class="verdict"><div><h2>先查 candidate assembly，不要先調 ratio gate</h2><p>目前最明確的問題是候選在 13 分鐘大量群聚，以及 inclusion list 中「四個 feature 都存在、卻沒有組成 candidate」的漏失。ratio 只能幫人工排序與解釋，還不能決定 detected。</p></div><aside class="signal"><strong>下一個 evidence loop</strong><span>先 replay 17 個 Top102 與 5 個 known rows，定位 m/z pairing、RT grouping 或 feature source 的第一個斷點。</span></aside></section>',
            '<section class="metric-strip">',
            _metric(accepted, "ratio 分析主群"),
            _metric(hotspot, f"集中在 12.5-13.5 min（總數 {total}）"),
            _metric(overlap, f"其中具 overlap（窗口內 {hotspot}）"),
            "</section>",
            '<section class="report-section"><div class="section-head"><h2>1. 候選集中在哪裡</h2><p>356 個原始 accepted seeds 中，194 個落在 12.5-13.5 min；其中 177 個又帶有 overlap candidate。13 分鐘不是少數異常，而是整批資料的主結構。</p></div>',
            _rt_hotspot_chart(total, hotspot, overlap),
            f'<p class="takeaway">13 分鐘窗口包含 {percent(hotspot, total)} 的候選；窗口內 {percent(overlap, hotspot)} 帶 overlap。這支持 interference 假說值得追，但尚未證明物理機制。</p></section>',
            '<section class="report-section"><div class="section-head"><h2>2. Ratio 能不能分開兩群</h2><p>線段是 IQR，中間點是 median，三個公式共用 log 尺度。accepted 與 background 線段大量重疊時，median 差距不能直接升級成 hard gate。</p></div>',
            _ratio_chart(primary_ratio_summary, background_ratio_summary),
            '<p class="takeaway">+1/base 幾乎重疊；+1/+2 只有小幅位移；+1/+4 差距最大，但 accepted 本身也很分散。結論是可排序、不可定生死。</p></section>',
            '<section class="report-section"><div class="section-head"><h2>3. +1 門檻是不是漏失來源</h2><p>25k 門檻保留 146/146 accepted。50k 或 100k 只少 1 個，但 200k 後召回快速下降。</p></div>',
            _threshold_chart(plus1_seed),
            '<p class="takeaway">25k 不是目前 146 筆候選的漏失根源。繼續調低門檻只會擴大搜尋池，不能解釋已知物為何沒有組成 candidate。</p></section>',
            '<section class="report-section"><div class="section-head"><h2>4. 已知清單掉在哪一層</h2><p>比較 feature 已齊、candidate 已組成、最後 accepted 三個階段。優先查的是「all 4 features 已存在，但 candidate 沒出現」。</p></div>',
            _assembly_chart(inclusion),
            '<p class="takeaway">Top102 有 17 筆、manual known 有 5 筆四個位置都存在卻沒組成 candidate。這比 ratio tuning 更接近可修的根因。</p></section>',
            '<section class="next-step"><h2>後續只做一件事</h2><p>對 22 筆 all-4-exist-but-no-candidate 做逐層 replay，輸出每一步為何沒有連成同一個 Pt group。找到第一個系統性斷點後，再決定要改 feature table、m/z pairing 或 RT grouping。</p></section>',
            _scope_details(summary, background),
        ]
    )
    return report_shell(
        title="Pt Pattern 現況總覽",
        meta=f"{text_value(summary.get('report_id'))} · diagnostic_only · no RAW",
        body=body,
    )


def _metric(value: int, label: str) -> str:
    return f'<div class="metric"><strong>{value}</strong><span>{escape(label)}</span></div>'


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
        '<p class="chart-subtitle">同一分母 356；內層越接近外層，代表 13 分鐘結構越主導整批資料</p>'
        '<svg data-chart="rt-hotspot" viewBox="0 0 940 210" role="img" aria-label="13 minute hotspot concentration">'
        f'{"".join(rows)}</svg></div>'
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
        parts.append(f'<text class="label" x="12" y="{y + 10:.1f}">{escape(label)}</text>')
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
        '<p class="chart-subtitle">點上數字 = 146 中保留的數量；X 軸為 log threshold</p>'
        '<svg data-chart="plus1-threshold" viewBox="0 0 940 285" role="img" aria-label="plus one threshold sensitivity">'
        f'<line class="grid" x1="{left}" y1="{top}" x2="{left + width}" y2="{top}"/>'
        f'<line class="grid" x1="{left}" y1="{top + height / 2}" x2="{left + width}" y2="{top + height / 2}"/>'
        f'<line class="axis" x1="{left}" y1="{top + height}" x2="{left + width}" y2="{top + height}"/>'
        f'<text x="18" y="{top + 4}">100%</text><text x="28" y="{top + height / 2 + 4}">50%</text><text x="35" y="{top + height + 4}">0%</text>'
        f'<polyline points="{" ".join(path)}" fill="none" stroke="#b77b18" stroke-width="4" stroke-linejoin="round"/>'
        f'{"".join(marks)}</svg></div>'
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
        f'{"".join(_stage_row(label, value, total, highlight) for label, value, highlight in stages)}'
        f'<p class="stage-note">{no_candidate} 筆 all 4 exist，但沒有 candidate</p></div>'
    )


def _stage_row(label: str, value: int, total: int, highlight: bool) -> str:
    css_class = "stage-row highlight" if highlight else "stage-row"
    return (
        f'<div class="{css_class}"><span>{escape(label)}</span>'
        f'<div class="stage-track"><div class="stage-fill" style="width:{_fraction_width(value, total, 100):.1f}%"></div></div>'
        f'<strong>{value}</strong></div>'
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


def _scope_details(summary: Mapping[str, object], background: int) -> str:
    return (
        '<details><summary>技術範圍與限制</summary><div class="table-wrap"><table>'
        '<thead><tr><th>項目</th><th>狀態</th></tr></thead><tbody>'
        f'<tr><td>report id</td><td><code>{escape(text_value(summary.get("report_id")))}</code></td></tr>'
        f'<tr><td>background rows</td><td>{background}</td></tr>'
        '<tr><td>RAW access</td><td>無</td></tr>'
        '<tr><td>detector / counted detection / Matrix</td><td>全部未改</td></tr>'
        '</tbody></table></div></details>'
    )
