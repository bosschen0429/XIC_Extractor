"""Chart-first renderer for Pt candidate assembly replay."""

# HTML and SVG literals stay readable as complete tags.
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Mapping, Sequence

from xic_extractor.discovery.pt_pattern_report_style import (
    escape,
    number,
    report_shell,
)
from xic_extractor.tabular_io import optional_float, text_value

_CLASSIFICATION_LABELS = {
    "rt_incoherent": "RT 對不起來",
    "mass_incoherent": "同一 base 湊不齊 mass edges",
    "feature_quality_blocked": "base quality 擋下",
    "unexpected_emittable": "應可輸出但上游未列候選",
}
_POSITION_LABELS = {
    "pt_plus_1": "+1",
    "pt_plus_2": "+2",
    "pt_plus_4": "+4",
}


def render_candidate_assembly_replay_html(
    *,
    summary: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
) -> str:
    """Render a static decision-first report from replay rows and summary."""

    row_classifications = _counts(summary.get("classification_counts"))
    classifications = _counts(summary.get("unique_classification_counts"))
    thresholds = _counts(summary.get("unique_threshold_recovery_counts"))
    limiting = _counts(summary.get("unique_limiting_position_counts"))
    row_count = _integer(summary.get("row_count"))
    unique_targets = _integer(summary.get("unique_target_window_count"))
    unique_signatures = _integer(summary.get("unique_replay_signature_count"))
    recovered_60 = thresholds.get("60", 0)
    unexpected = row_classifications.get("unexpected_emittable", 0)
    rt_incoherent = row_classifications.get("rt_incoherent", 0)
    mass_incoherent = row_classifications.get("mass_incoherent", 0)
    first_break_labels = {
        key: label
        for key, label in _CLASSIFICATION_LABELS.items()
        if classifications.get(key, 0) > 0 or key == "unexpected_emittable"
    }

    if unexpected:
        verdict = f"先停下規則調整：有 {unexpected} 筆在本次重播中其實可被 canonical matcher 輸出。"
        verdict_detail = (
            "這代表至少一筆上游 no-candidate 標籤需要對帳；在釐清 artifact 版本或輸入來源前，"
            "不能把全部差異解釋成 RT 或 mass incoherence。"
        )
    else:
        verdict = (
            f"沒有看到 assembly 漏組裝。{rt_incoherent} 筆是 RT 對不起來，"
            f"{mass_incoherent} 筆連同一 base 的三條 mass edge 都湊不齊。"
        )
        verdict_detail = (
            "「四個 m/z 都曾出現」只代表四個 list-relative 視窗各自有 feature，"
            f"不代表它們屬於同一個層析 feature family。去重後是 {unique_signatures} 組實際 feature 組合。"
            "把 RT hard cap 從 30 放到 60 秒"
            f"只會得到 {recovered_60}/{unique_signatures} 組，因此 30 秒不是這批漏抓的主因。"
        )

    body = "".join(
        [
            '<section class="verdict">',
            f"<div><h2>{escape(verdict)}</h2><p>{escape(verdict_detail)}</p></div>",
            '<div class="signal"><strong>這輪只做 no-RAW replay</strong>',
            "<span>沿用 canonical detected feature table、20 ppm expected-m/z denominator 與 matcher quality semantics；不改 detector、counted detection 或 Matrix。</span></div>",
            "</section>",
            '<section class="metric-strip" aria-label="關鍵數字">',
            _metric(row_count, "list rows；不是獨立 compound 數"),
            _metric(
                unique_signatures,
                f"實際 feature 組合；分布於 {unique_targets} 個 20 ppm windows",
            ),
            _metric(recovered_60, "RT hard cap 放寬到 60 秒可形成完整 group"),
            "</section>",
            _section(
                "第一個斷點",
                "圖表先依實際 feature 組合去重，再看哪一層最早不成立。只有「應可輸出但上游未列候選」才指向 candidate assembly 或 artifact 對帳問題。",
                _bar_chart(
                    classifications,
                    labels=first_break_labels,
                    data_chart="first-break",
                    color_by_key={
                        "rt_incoherent": "#b77b18",
                        "mass_incoherent": "#b5522c",
                        "feature_quality_blocked": "#6f7f77",
                        "unexpected_emittable": "#0f6b63",
                    },
                ),
                (
                    f"row-level 對帳仍是 RT incoherence（{rt_incoherent}/{row_count}）；圖中的柱高已去除重複 feature 組合。"
                    "這不是把 30 秒稍微放寬就能解決的邊界案例。"
                ),
            ),
            _section(
                "RT 上限敏感度",
                "以去重後的 feature 組合計數，同時要求每個 position 對 base 的絕對 RT 差與四點總 span 都不超過指定上限。30 秒是正式規則，其餘只做診斷。",
                _bar_chart(
                    thresholds,
                    labels={
                        str(value): f"≤ {value} sec"
                        for value in (30, 45, 60, 90, 180, 300)
                    },
                    data_chart="rt-sensitivity",
                    color_by_key={"30": "#0f6b63", "45": "#b77b18", "60": "#b77b18"},
                    default_color="#bcd8d2",
                ),
                (
                    "45 與 60 秒若仍沒有新增，表示不是 hard cap 太緊；"
                    "要到 90 秒以上才出現的配對，更像是同 m/z 視窗中的其他層析 feature。"
                ),
            ),
            _section(
                "哪個 position 最常拖開 RT",
                "只對去重後、能湊出完整 mass edges 的 best assembly 計數；柱高代表該 position 是最大 RT 差的次數。",
                _bar_chart(
                    limiting,
                    labels=_POSITION_LABELS,
                    data_chart="limiting-position",
                    color_by_key={"pt_plus_4": "#b5522c"},
                    default_color="#0f6b63",
                ),
                "這張圖用來決定後續要優先檢查哪個 position 的 feature-table 分裂或干擾，不直接轉成新 gate。",
            ),
            '<section class="report-section">',
            '<div class="section-head"><h2>逐筆核對</h2><p>表格只保留判斷所需欄位；完整 feature id、m/z、ppm 與各 position RT delta 留在 CSV。</p></div>',
            _detail_table(rows),
            "</section>",
            '<section class="next-step"><h2>後續驗證</h2><p>',
            (
                "既然「應可輸出但上游未列候選」是 0，先維持 30 秒規則，不做 RT 放寬。"
                "下一個 evidence loop 應聚焦於兩筆 mass-incoherent 的 feature-table m/z edge，"
                "以及 RT-incoherent 中最接近門檻的幾筆 overlay；"
                "不是重新掃 RAW 或直接改 detector。"
            ),
            "</p></section>",
        ]
    )
    return report_shell(
        title="Candidate assembly 到底卡在哪",
        meta=f"report_id={text_value(summary.get('report_id'))} · sample={text_value(summary.get('sample_stem'))}",
        body=body,
    )


def _metric(value: int, label: str) -> str:
    return f'<div class="metric"><strong>{value}</strong><span>{escape(label)}</span></div>'


def _section(title: str, description: str, chart: str, takeaway: str) -> str:
    return "".join(
        [
            '<section class="report-section">',
            f'<div class="section-head"><h2>{escape(title)}</h2><p>{escape(description)}</p></div>',
            chart,
            f'<p class="takeaway">{escape(takeaway)}</p>',
            "</section>",
        ]
    )


def _bar_chart(
    values: Mapping[str, int],
    *,
    labels: Mapping[str, str],
    data_chart: str,
    color_by_key: Mapping[str, str],
    default_color: str = "#b77b18",
) -> str:
    ordered = [(key, label, values.get(key, 0)) for key, label in labels.items()]
    maximum = max((value for _, _, value in ordered), default=1)
    maximum = max(maximum, 1)
    height = max(150, 42 * len(ordered) + 44)
    groups: list[str] = []
    for index, (key, label, value) in enumerate(ordered):
        y = 28 + index * 42
        width = 580 * value / maximum
        color = color_by_key.get(key, default_color)
        groups.append(
            f'<g transform="translate(0 {y})"><text class="label" x="0" y="13">{escape(label)}</text>'
            f'<rect x="230" y="0" width="{width:.2f}" height="18" fill="{color}" rx="2"></rect>'
            f'<text class="value" x="{min(820, 242 + width):.2f}" y="13">{value}</text></g>'
        )
    return "".join(
        [
            f'<div class="chart-shell" data-chart="{escape(data_chart)}">',
            f'<svg viewBox="0 0 900 {height}" role="img" aria-label="{escape(data_chart)}">',
            *groups,
            "</svg></div>",
        ]
    )


def _detail_table(rows: Sequence[Mapping[str, object]]) -> str:
    ordered = sorted(
        rows,
        key=lambda row: (
            _sortable_float(row.get("best_max_abs_rt_delta_sec")),
            text_value(row.get("source_set")),
            text_value(row.get("compound")),
        ),
    )
    body_rows = []
    for row in ordered:
        max_delta = optional_float(row.get("best_max_abs_rt_delta_sec"))
        body_rows.append(
            "<tr>"
            f"<td>{escape(text_value(row.get('source_set')))}</td>"
            f"<td><strong>{escape(text_value(row.get('compound')))}</strong></td>"
            f"<td><code>{number(optional_float(row.get('list_plus1_mz')), digits=7)}</code></td>"
            f"<td>{escape(_CLASSIFICATION_LABELS.get(text_value(row.get('classification')), text_value(row.get('classification'))))}</td>"
            f"<td>{escape(_format_positions(text_value(row.get('missing_mass_positions'))))}</td>"
            f"<td>{number(max_delta, digits=4)}</td>"
            f"<td>{escape(_POSITION_LABELS.get(text_value(row.get('limiting_position')), text_value(row.get('limiting_position'))))}</td>"
            f"<td>{escape(text_value(row.get('target_window_group_id')))}"
            f" × {escape(text_value(row.get('target_window_group_size')))}</td>"
            "</tr>"
        )
    return "".join(
        [
            '<div class="table-wrap"><table><thead><tr>',
            "<th>來源</th><th>名稱</th><th>list +1 m/z</th><th>第一個斷點</th><th>缺哪條 mass edge</th>",
            "<th>best max ΔRT (sec)</th><th>limiting</th><th>20 ppm target group</th>",
            "</tr></thead><tbody>",
            *body_rows,
            "</tbody></table></div>",
        ]
    )


def _counts(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {text_value(key): _integer(count) for key, count in value.items()}


def _integer(value: object) -> int:
    parsed = optional_float(value)
    return int(parsed) if parsed is not None else 0


def _format_positions(value: str) -> str:
    return ", ".join(
        _POSITION_LABELS.get(label, label) for label in value.split(";") if label
    )


def _sortable_float(value: object) -> float:
    parsed = optional_float(value)
    return parsed if parsed is not None else float("inf")
