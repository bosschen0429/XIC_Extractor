"""Chart-first HTML renderer for Pt pattern ratio profiles."""

# HTML and CSS literals stay readable as complete tags.
# ruff: noqa: E501

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from xic_extractor.discovery.pt_pattern_report_style import (
    escape,
    number,
    report_shell,
)
from xic_extractor.tabular_io import optional_float, text_value

_RATIO_VISUALS = (
    ("plus1_over_base_height_ratio", "+1 / base height", "+1 是否比 base 穩定"),
    ("plus1_over_plus2_height_ratio", "+1 / +2 height", "+2 是否支持同一 envelope"),
    ("plus1_over_plus4_height_ratio", "+1 / +4 height", "+4 是否偏弱或受 overlap 影響"),
)


def render_ratio_profile_html(
    *,
    primary_rows: Sequence[Mapping[str, object]],
    primary_summary: Sequence[Mapping[str, object]],
    background_summary: Sequence[Mapping[str, object]],
    formula_rows: Sequence[Mapping[str, object]],
    profile_id: str,
) -> str:
    """Render the ratio profile as a chart-first diagnostic review surface."""

    primary_by_ratio = _summary_map(primary_summary)
    background_by_ratio = _summary_map(background_summary)
    tail_rows = [
        row
        for row in primary_rows
        if (optional_float(row.get("ratio_outlier_count")) or 0) > 0
    ]
    background_count = _population_count(background_summary)
    body = "".join(
        [
            '<section class="verdict"><div><h2>先看結論：ratio 還不能當 gate</h2><p>accepted 與 background 在 +1/base、+1/+2 的分布高度重疊；+1/+4 的中位數雖然拉開，但變異也最大。現階段 ratio 適合排序人工 review，不適合決定 detected。</p></div><aside class="signal"><strong>最重要的限制</strong><span>12.5-13.5 min 是密集 overlap 區。先把 RT 群聚與 assembly 影響拆開，才有資格談理論 ratio。</span></aside></section>',
            '<section class="metric-strip">',
            _metric(len(primary_rows), "分析主群 primary population"),
            _metric(background_count, "比較背景 background population"),
            _metric(len(tail_rows), "ratio 極端值 review rows"),
            "</section>",
            '<section class="report-section"><div class="section-head"><h2>Ratio 怎麼看</h2><p>用同一個 log 尺度比較 median 與 IQR。線段越重疊，代表這個 ratio 越難區分 accepted 與 background；不能只看兩個 median 的差。</p></div>',
            _interval_chart(primary_by_ratio, background_by_ratio),
            '<p class="takeaway">+1/base 幾乎沒有分離；+1/+2 只有小幅位移；+1/+4 差距較大但 IQR 仍重疊，因此三者目前都不具 hard-gate 證據。</p></section>',
            '<section class="report-section"><div class="section-head"><h2>RT 是否改變 ratio</h2><p>每個點是一個 primary candidate。橘色帶標出 12.5-13.5 min；Y 軸為 log ratio，超出各自 5-95% 範圍的值貼邊顯示，避免極端值壓扁主要分布。</p></div>',
            _rt_small_multiples(primary_rows, primary_by_ratio),
            '<p class="takeaway">先觀察橘色區是否呈現不同的散布或偏移。它只能指出 interference 假說值得追，不等於證明 isotope envelope 的物理機制。</p></section>',
            '<section class="report-section"><div class="section-head"><h2>Formula 判讀</h2><p>公式方向不是權威問題，而是閱讀角度。現階段以 +1 當 anchor 最容易連回 Pt 同位素工作假說；base 放分子與 FeatureHunter 公式保留作 reciprocal comparator。</p></div>',
            '<div class="equation-grid"><div class="equation"><code>+1 / base</code><span>先看主位置與最低 m/z anchor 的相對強度。</span></div><div class="equation"><code>+1 / +2</code><span>觀察 +2 是否跟著 +1 支持同一 envelope。</span></div><div class="equation"><code>+1 / +4</code><span>最敏感，也最容易被弱訊號、拖尾與 overlap 放大。</span></div></div>',
            _formula_details(formula_rows),
            _tail_details(tail_rows),
            "</section>",
        ]
    )
    return report_shell(
        title="Pt Pattern Ratio Review",
        meta=f"{profile_id} · diagnostic_only · ratio profile only",
        body=body,
    )


def _metric(value: int, label: str) -> str:
    return f'<div class="metric"><strong>{value}</strong><span>{escape(label)}</span></div>'


def _summary_map(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    return {text_value(row.get("ratio_name")): row for row in rows}


def _population_count(rows: Sequence[Mapping[str, object]]) -> int:
    if not rows:
        return 0
    return int(optional_float(rows[0].get("row_count")) or 0)


def _interval_chart(
    primary: Mapping[str, Mapping[str, object]],
    background: Mapping[str, Mapping[str, object]],
) -> str:
    left, width = 178.0, 690.0
    parts = [
        '<div class="chart-shell"><p class="chart-title">Height ratio distributions</p>',
        '<p class="chart-subtitle">median + IQR，log scale；gold circle = accepted，teal open circle = background</p>',
        '<svg data-chart="ratio-intervals" viewBox="0 0 940 330" role="img" aria-label="accepted 與 background ratio median and interquartile range">',
    ]
    for tick in (0.1, 0.3, 1.0, 3.0, 10.0, 30.0):
        x = _log_x(tick, left, width)
        parts.append(
            f'<line class="grid" x1="{x:.1f}" y1="34" x2="{x:.1f}" y2="286"/><text x="{x:.1f}" y="312" text-anchor="middle">{number(tick)}</text>'
        )
    for index, (key, label, _) in enumerate(_RATIO_VISUALS):
        y = 74.0 + index * 82.0
        parts.append(
            f'<text class="label" x="12" y="{y + 10:.1f}">{escape(label)}</text>'
        )
        parts.extend(_interval_marks(primary.get(key), y, left, width, "primary"))
        parts.extend(
            _interval_marks(background.get(key), y + 29, left, width, "background")
        )
    parts.append(
        '</svg><div class="legend"><span><i class="swatch gold"></i>accepted</span><span><i class="swatch"></i>background</span></div></div>'
    )
    return "".join(parts)


def _interval_marks(
    row: Mapping[str, object] | None,
    y: float,
    left: float,
    width: float,
    series: str,
) -> list[str]:
    if row is None:
        return []
    q25 = optional_float(row.get("q25"))
    median = optional_float(row.get("median"))
    q75 = optional_float(row.get("q75"))
    if q25 is None or median is None or q75 is None:
        return []
    x1 = _log_x(q25, left, width)
    xm = _log_x(median, left, width)
    x2 = _log_x(q75, left, width)
    line_class = "primary-line" if series == "primary" else "background-line"
    dot_class = "primary-dot" if series == "primary" else "background-dot"
    radius = 6 if series == "primary" else 5
    return [
        f'<line class="{line_class}" x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}"/>',
        f'<circle class="{dot_class}" cx="{xm:.1f}" cy="{y:.1f}" r="{radius}"/>',
        f'<text class="value" x="{min(xm + 10, 885):.1f}" y="{y + 4:.1f}">{number(median, digits=4)}</text>',
    ]


def _log_x(value: float, left: float, width: float) -> float:
    bounded = min(max(value, 0.1), 30.0)
    return left + (math.log10(bounded) + 1.0) / math.log10(300.0) * width


def _rt_small_multiples(
    rows: Sequence[Mapping[str, object]],
    summary: Mapping[str, Mapping[str, object]],
) -> str:
    rt_values = [
        value
        for row in rows
        if (value := optional_float(row.get("base_rt_apex_min"))) is not None
    ]
    if not rt_values:
        return '<div class="chart-shell">沒有可畫的 RT 資料。</div>'
    rt_min, rt_max = min(rt_values), max(rt_values)
    left, width = 154.0, 730.0
    parts = [
        '<div class="chart-shell"><p class="chart-title">Accepted ratio across RT</p>',
        '<p class="chart-subtitle">橘色區 = 12.5-13.5 min；每列使用自己的 5-95% log ratio 範圍</p>',
        '<svg data-chart="rt-ratio-small-multiples" viewBox="0 0 940 610" role="img" aria-label="accepted ratio across retention time">',
    ]
    hotspot_x1 = left + _linear(12.5, rt_min, rt_max, width)
    hotspot_x2 = left + _linear(13.5, rt_min, rt_max, width)
    for index, (key, label, _) in enumerate(_RATIO_VISUALS):
        top = 34.0 + index * 185.0
        row_summary = summary.get(key, {})
        low = optional_float(row_summary.get("q05")) or 0.1
        high = optional_float(row_summary.get("q95")) or max(low * 10, 1.0)
        low = max(low, 1e-6)
        high = max(high, low * 1.01)
        parts.append(
            f'<text class="label" x="12" y="{top + 16:.1f}">{escape(label)}</text>'
        )
        parts.append(
            f'<rect class="hotspot" x="{hotspot_x1:.1f}" y="{top:.1f}" width="{max(hotspot_x2 - hotspot_x1, 0):.1f}" height="130"/>'
        )
        parts.append(
            f'<line class="axis" x1="{left}" y1="{top + 130:.1f}" x2="{left + width}" y2="{top + 130:.1f}"/>'
        )
        for row in rows:
            rt = optional_float(row.get("base_rt_apex_min"))
            ratio = optional_float(row.get(key))
            if rt is None or ratio is None or ratio <= 0:
                continue
            x = left + _linear(rt, rt_min, rt_max, width)
            y = (
                top
                + 130
                - _linear(
                    math.log10(min(max(ratio, low), high)),
                    math.log10(low),
                    math.log10(high),
                    118,
                )
            )
            point_class = "point-hot" if 12.5 <= rt <= 13.5 else "point"
            parts.append(
                f'<circle class="{point_class}" cx="{x:.1f}" cy="{y:.1f}" r="3"/>'
            )
        parts.append(
            f'<text x="{left}" y="{top + 151:.1f}">{number(rt_min)} min</text><text x="{left + width}" y="{top + 151:.1f}" text-anchor="end">{number(rt_max)} min</text>'
        )
        parts.append(
            f'<text x="12" y="{top + 45:.1f}">{number(high)}</text><text x="12" y="{top + 126:.1f}">{number(low)}</text>'
        )
    parts.append(
        '</svg><div class="legend"><span><i class="swatch orange"></i>12.5-13.5 min</span><span><i class="swatch"></i>其餘 RT</span></div></div>'
    )
    return "".join(parts)


def _linear(value: float, minimum: float, maximum: float, size: float) -> float:
    if math.isclose(minimum, maximum):
        return size / 2
    return min(max((value - minimum) / (maximum - minimum), 0.0), 1.0) * size


def _formula_details(rows: Sequence[Mapping[str, object]]) -> str:
    visible = [
        row for row in rows if text_value(row.get("measurement_basis")) == "height"
    ]
    body = "".join(
        f"<tr><td><code>{escape(text_value(row.get('ratio_name')))}</code></td><td>{escape(text_value(row.get('numerator')))}</td><td>{escape(text_value(row.get('denominator')))}</td><td>{escape(text_value(row.get('formula_family')))}</td></tr>"
        for row in visible
    )
    return f'<details><summary>Debug: 完整 formula manifest</summary><div class="table-wrap"><table><thead><tr><th>ratio</th><th>分子</th><th>分母</th><th>family</th></tr></thead><tbody>{body}</tbody></table></div></details>'


def _tail_details(rows: Sequence[Mapping[str, object]]) -> str:
    body = "".join(
        f"<tr><td><code>{escape(text_value(row.get('candidate_id')))}</code></td><td>{number(optional_float(row.get('base_mz')))}</td><td>{number(optional_float(row.get('base_rt_apex_min')), digits=4)}</td><td>{int(optional_float(row.get('ratio_outlier_count')) or 0)}</td></tr>"
        for row in rows[:50]
    )
    return f'<details><summary>Tail review rows（前 50 筆）</summary><div class="table-wrap"><table><thead><tr><th>candidate</th><th>base m/z</th><th>RT min</th><th>tail fields</th></tr></thead><tbody>{body}</tbody></table></div></details>'
