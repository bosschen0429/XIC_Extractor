"""The workbook opens on one conclusion and one table of targets."""

from __future__ import annotations

from collections.abc import Mapping

from xic_extractor.output.targeted_overview import TargetedOverview, TargetOverview
from xic_extractor.output.workbook_styles import (
    FOCUS,
    INK,
    SOFT,
    _font,
    _link,
    _prepare_sheet,
    _style_body,
    _write_header,
)
from xic_extractor.output.workbook_values import _excel_text

_HEADERS = ("目標", "類型", "配對 ISTD", "計入", "全部", "需先看", "說明")
_NUMERIC = frozenset({"計入", "全部", "需先看"})
_WIDTHS = (22, 9, 22, 7, 7, 8, 30)
_TABLE_ROW = 4


def build_overview_sheet(
    ws,
    overview: TargetedOverview,
    *,
    pairs: Mapping[str, str],
    review_rows_by_target: Mapping[str, int],
) -> None:
    """Write the summary; `review_rows_by_target` locates each target in 需檢查."""
    _prepare_sheet(ws, landscape=False)
    ws["A1"] = (
        "尚無已保存結果"
        if not overview.result_count
        else f"先看 {overview.focus_count} 筆與多數不同的結果"
        if overview.focus_count
        else "各目標結果一致，沒有需要先看的結果"
    )
    ws["A1"].font = _font(INK, bold=True, size=16)
    ws.row_dimensions[1].height = 30
    facts = [
        f"{overview.sample_count} 個樣本",
        f"{len(overview.targets)} 個目標",
        f"Analyte 計入 {overview.analyte_counted}/{overview.analyte_total}",
        f"ISTD 計入 {overview.istd_counted}/{overview.istd_total}",
    ]
    if overview.count_unknown:
        facts.append(f"{overview.count_unknown} 筆未記錄計數判定")
    ws["A2"] = " · ".join(facts)
    ws["A2"].font = _font(SOFT)

    _write_header(ws, _TABLE_ROW, _HEADERS, _NUMERIC)
    for row, target in enumerate(overview.targets, _TABLE_ROW + 1):
        focus = len(target.focus_samples)
        values = (
            _excel_text(target.label),
            target.role,
            _excel_text(pairs.get(target.label, "")),
            target.counted,
            target.total,
            focus or None,
            _focus_note(target),
        )
        for column, (header, value) in enumerate(zip(_HEADERS, values, strict=True), 1):
            cell = ws.cell(row, column, value)
            color = SOFT if header in {"類型", "配對 ISTD"} else INK
            if focus and header in {"需先看", "說明"}:
                color = FOCUS
            _style_body(cell, color=color, numeric=header in _NUMERIC)
        if focus and target.label in review_rows_by_target:
            _link(
                ws.cell(row, 6),
                f"'需檢查'!A{review_rows_by_target[target.label]}",
                color=FOCUS,
                bold=True,
            )
    note = _TABLE_ROW + len(overview.targets) + 2
    for row, text in (
        (
            note,
            "需先看＝該目標中占 20% 以下的判定；沒有明顯多數時改看檢閱註記。"
            "只決定閱讀順序，不改變已保存判定。",
        ),
        (note + 1, "數值與判定皆為已保存結果；逐峰檢查請在工作台開啟此檔看 XIC。"),
    ):
        ws.cell(row, 1, text).font = _font(SOFT, size=9)
    for column, width in enumerate(_WIDTHS, 1):
        ws.column_dimensions[chr(64 + column)].width = width
    ws.freeze_panes = f"A{_TABLE_ROW + 1}"
    ws.print_area = f"A1:G{note + 1}"


def _focus_note(target: TargetOverview) -> str:
    return (
        {
            "minority_counted": "少數計入：確認是否抓錯峰",
            "minority_not_counted": "少數未計入：確認是否漏抓",
            "mixed": "沒有明顯多數：看檢閱註記",
        }.get(target.focus_kind, "")
        if target.focus_samples
        else ""
    )
