"""Human-facing report for Pt same-scan evidence replay."""

# HTML literals stay readable as complete tags.
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Mapping, Sequence

from xic_extractor.discovery.pt_pattern_report_style import escape, report_shell
from xic_extractor.tabular_io import optional_float, text_value


def render_same_scan_replay_html(
    *,
    summary: Mapping[str, object],
    evidence_rows: Sequence[Mapping[str, object]],
    conflict_rows: Sequence[Mapping[str, object]],
) -> str:
    """Render a compact decision-first same-scan report."""

    current = _integer(summary.get("current_hypothesis_count"))
    strict = _integer(summary.get("current_strict_contract_supported_count"))
    cp_total = _integer(summary.get("known_cp_expected_positive_count"))
    cp_strict = _integer(summary.get("known_cp_strict_contract_supported_count"))
    conflicts = _integer(summary.get("conflict_component_count"))
    resolved = _integer(summary.get("uniquely_resolved_conflict_component_count"))
    d3_core = bool(summary.get("d3_core_contract_supported"))
    d3_strict = bool(summary.get("d3_strict_contract_supported"))
    ppm = text_value(summary.get("mass_tolerance_ppm"))
    noise = text_value(summary.get("noise_level"))
    minimum_joint_scans = text_value(summary.get("minimum_joint_scans"))
    if d3_core:
        verdict = "0/+1/+2 same-scan core 不夠 specific：d3 control 也會通過。"
        detail = "Contract C 只能當召回與排序 evidence，不能單獨成為 Pt detector。"
    elif cp_strict == 0:
        verdict = "四點 strict same-scan 沒保留已知 CP，不能升級成 detector gate。"
        detail = "先檢查 +4 censoring 與 expected m/z，不直接放寬 ppm 或 RT。"
    else:
        verdict = "same-scan 證據有區分力，但目前只足以重排 hypotheses。"
        detail = "只有完整四點 strict support 在 component 內唯一時，才暫時可區分候選。"

    body = "".join(
        [
            '<section class="verdict"><div>',
            f"<h2>{escape(verdict)}</h2><p>{escape(detail)}</p></div>",
            '<div class="signal"><strong>三個 frozen contracts</strong>',
            "<span>A＝feature-first；B＝四點連續 same-scan；"
            "C＝0/+1/+2 same-scan core，+4 僅記 conditional/censored。</span>",
            "</div></section>",
            '<section class="metric-strip">',
            _metric(f"{strict}/{current}", "現有 hypotheses 通過 B"),
            _metric(f"{cp_strict}/{cp_total}", "已知 CP 通過 B"),
            _metric(f"{resolved}/{conflicts}", "conflict components 可唯一區分"),
            "</section>",
            '<section class="report-section"><div class="section-head">',
            "<h2>CP 與 d3 control</h2>",
            f"<p>d3 control：strict={'通過' if d3_strict else '未通過'}；"
            f"core={'通過' if d3_core else '未通過'}。A 是既有 pipeline 狀態，"
            "B/C 才是共同 MS1 scans 的直接證據。</p></div>",
            _known_table(evidence_rows),
            "</section>",
            '<section class="report-section"><div class="section-head">',
            "<h2>共用 feature 的 hypotheses</h2>",
            "<p>多個候選都支持或全部不支持時仍維持 ambiguous/dependent；"
            "不強迫選 winner。</p></div>",
            _conflict_bars(conflict_rows),
            "</section>",
            '<section class="next-step"><h2>繼續條件</h2><p>',
            "保留 195CP-d(GpG)、救回至少一個原本卡在 feature/assembly 的 CP、"
            "不讓 d3 得到相近 Pt-specific 支持，並區分至少一個 conflict component。"
            "否則回頭檢查 isotope/noise/mass model，不再調 ratio。",
            "</p></section>",
        ]
    )
    return report_shell(
        title="Pt pattern same-scan 證據檢查",
        meta=(
            f"report_id={text_value(summary.get('report_id'))} · diagnostic_only · "
            f"Fullscan MS1 index · same-scan · ppm={ppm} · noise={noise} · "
            f"minimum_joint_scans={minimum_joint_scans}"
        ),
        body=body,
    )


def _metric(value: str, label: str) -> str:
    return f'<div class="metric"><strong>{escape(value)}</strong><span>{escape(label)}</span></div>'


def _known_table(rows: Sequence[Mapping[str, object]]) -> str:
    body = []
    for row in rows:
        if text_value(row.get("source_set")) != "known_named":
            continue
        body.append(
            "<tr>"
            f"<td><strong>{escape(text_value(row.get('compound')))}</strong></td>"
            f"<td>{escape(text_value(row.get('expected_class')))}</td>"
            f"<td>{escape(_call(row.get('contract_a_current_feature_accept')))}</td>"
            f"<td>{escape(_call(row.get('contract_b_strict_full_same_scan')))}</td>"
            f"<td>{escape(_call(row.get('contract_c_core_plus4_conditional')))}</td>"
            f"<td>{escape(text_value(row.get('plus4_support_status')))}</td>"
            "</tr>"
        )
    return "".join(
        [
            '<div class="table-wrap"><table><thead><tr>',
            "<th>名稱</th><th>預期</th><th>A</th><th>B</th><th>C</th><th>+4 狀態</th>",
            "</tr></thead><tbody>",
            *body,
            "</tbody></table></div>",
        ]
    )


def _conflict_bars(rows: Sequence[Mapping[str, object]]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        status = text_value(row.get("resolution_status"))
        counts[status] = counts.get(status, 0) + 1
    labels = (
        ("unique_strict_support", "唯一 strict"),
        ("unique_core_support", "唯一 core（diagnostic，不可選）"),
        ("ambiguous_multiple_strict_support", "多個 strict"),
        ("ambiguous_multiple_core_support", "多個 core"),
        ("no_same_scan_support", "皆無支持"),
    )
    maximum = max(counts.values(), default=1)
    bars = []
    for key, label in labels:
        value = counts.get(key, 0)
        width = value / maximum * 100
        bars.append(
            f'<div class="stage-row"><span>{escape(label)}</span>'
            f'<div class="stage-track"><div class="stage-fill" style="width:{width:.1f}%"></div></div>'
            f"<strong>{value}</strong></div>"
        )
    return (
        f'<div class="stage-grid"><div class="stage-panel">{"".join(bars)}</div></div>'
    )


def _integer(value: object) -> int:
    parsed = optional_float(value)
    return int(parsed) if parsed is not None else 0


def _call(value: object) -> str:
    return "通過" if value is True or text_value(value).lower() == "true" else "未通過"
