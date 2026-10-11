"""A per-result reading guide with the original diagnostic events retained."""

from __future__ import annotations

from collections import Counter
from math import ceil

from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from xic_extractor.output.review_queue_model import _review_queue_rows
from xic_extractor.output.schema import DIAGNOSTIC_HEADERS
from xic_extractor.output.workbook_styles import (
    ACCENT,
    BORDER,
    CAUTION,
    GREY,
    INK,
    MUTED,
    REPORT_FONT,
    WHITE,
    _add_table,
    _apply,
    _fill,
    _header_style,
    _long_cell_value,
    _prepare_print_view,
    _prose_line_count,
)
from xic_extractor.output.workbook_values import _excel_text


def build_diagnostics_sheet(
    ws,
    rows: list[dict[str, str]],
    *,
    result_rows: list[dict[str, str]],
    review_rows: list[dict[str, str]],
) -> None:
    event_counts = Counter(
        (row.get("SampleName", ""), row.get("Target", "")) for row in rows
    )
    result_locations = {
        (row.get("SampleName", ""), row.get("Target", "")): index
        for index, row in enumerate(result_rows, 2)
    }
    # Events without a delivered measurement still need a visible reading route.
    unmatched = dict.fromkeys(
        (row.get("SampleName", ""), row.get("Target", ""))
        for row in rows
        if (row.get("SampleName", ""), row.get("Target", "")) not in result_locations
    )
    orphan_rows = [
        {"SampleName": sample, "Target": target} for sample, target in unmatched
    ]
    worklist = [*review_rows, *_review_queue_rows(orphan_rows, rows)]
    worklist.sort(key=lambda row: (int(row["Priority"]), row["Sample"], row["Target"]))
    widths = (35, 15, 10, 48, 38, 12, 19, 11, 11)
    for index, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(index)].width = width
    for row, text, size in (
        (2, "待處理結果", 20),
        (3, "僅列未確認或仍需處理的結果；一般流程資訊保留於原始紀錄。", 10),
        (
            5,
            f"{len(worklist)} 筆結果待處理；"
            f"原始 {len(rows)} 筆事件保留在下方，可展開查看。",
            10,
        ),
    ):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=9)
        cell = ws.cell(row, 1, text)
        cell.font = Font(name=REPORT_FONT, size=size, bold=size > 12, color=INK)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[row].height = 32 if size > 12 else 22
    ws["A4"] = "返回總覽"
    ws["A4"].hyperlink = "#'Overview'!A1"
    ws["B4"] = "全部結果"
    ws["B4"].hyperlink = "#'XIC Results'!A1"
    for address in ("A4", "B4"):
        ws[address].font = Font(name=REPORT_FONT, color=ACCENT, underline="single")

    headers = (
        "樣本",
        "目標",
        "結果",
        "原因說明",
        "建議檢查",
        "RT (min)",
        "Area (counts·s)",
        "查看量測",
        "原始紀錄",
    )
    for column, header in enumerate(headers, 1):
        _apply(ws.cell(7, column, header), **_header_style(INK))
    ws.row_dimensions[1].height = 8
    ws.row_dimensions[4].height = 22
    ws.row_dimensions[6].height = 8
    ws.row_dimensions[7].height = 30
    raw_heading = 10 + len(worklist)
    for index, record in enumerate(worklist, 8):
        key = (record["Sample"], record["Target"])
        event_count = event_counts[key]
        values = (
            record["Sample"],
            record["Target"],
            record["Outcome"],
            record["Why"],
            record["Action"],
            _long_cell_value("RT", record["RT"]),
            _long_cell_value("Area", record["Area"]),
            "查看結果"
            if (record["Sample"], record["Target"]) in result_locations
            else "無量測列",
            f"{event_count} 筆事件" if event_count else "結果註記",
        )
        for column, value in enumerate(values, 1):
            cell = ws.cell(index, column, _excel_text(value))
            color = GREY if index % 2 == 0 else WHITE
            if column == 3 and record["Outcome"] != "已計入":
                color = CAUTION
            _apply(
                cell,
                fill=_fill(color),
                border=BORDER,
                alignment=Alignment(
                    horizontal="right" if column in {6, 7} else "left",
                    vertical="center",
                    wrap_text=True,
                ),
            )
        ws.cell(index, 6).number_format = "0.0000"
        ws.cell(index, 7).number_format = "#,##0.00"
        if key in result_locations:
            ws.cell(index, 8).hyperlink = f"#'XIC Results'!A{result_locations[key]}"
            ws.cell(index, 8).font = Font(
                name=REPORT_FONT, color=ACCENT, underline="single"
            )
        ws.cell(index, 9).hyperlink = (
            f"#'Diagnostics'!A{raw_heading}"
            if event_count
            else f"#'XIC Results'!A{result_locations[key]}"
        )
        ws.cell(index, 9).font = Font(
            name=REPORT_FONT, color=ACCENT, underline="single"
        )
        lines = max(
            _prose_line_count(record["Why"], 54),
            _prose_line_count(record["Action"], 42),
        )
        ws.row_dimensions[index].height = max(44, lines * 15 + 12)
    if worklist:
        ws.auto_filter.ref = f"A7:I{7 + len(worklist)}"
        _add_table(ws, "TargetedReview", ws.auto_filter.ref)
    else:
        ws["A8"] = "本批沒有待處理結果。"

    ws.merge_cells(
        start_row=raw_heading, start_column=1, end_row=raw_heading, end_column=9
    )
    cell = ws.cell(
        raw_heading, 1, "原始技術紀錄（按左側＋展開；事件文字不代表最終計數）"
    )
    cell.font = Font(name=REPORT_FONT, size=12, bold=True, color=MUTED)
    ws.row_dimensions[raw_heading].height = 30
    if rows:
        header_row = raw_heading + 1
        for column, header in enumerate(DIAGNOSTIC_HEADERS, 1):
            _apply(ws.cell(header_row, column, header), **_header_style(MUTED))
        for index, record in enumerate(rows, header_row + 1):
            for column, header in enumerate(DIAGNOSTIC_HEADERS, 1):
                _apply(
                    ws.cell(index, column, _excel_text(record.get(header, ""))),
                    alignment=Alignment(vertical="top", wrap_text=True),
                    border=BORDER,
                )
            ws.row_dimensions[index].height = min(
                400, max(60, ceil(len(record.get("Reason", "")) / 48) * 16)
            )
        ws.row_dimensions.group(header_row, header_row + len(rows), hidden=True)
        ws.row_dimensions[raw_heading].collapsed = True
    ws.column_dimensions.group("F", "G", hidden=True)
    ws.freeze_panes = "D8"
    _prepare_print_view(ws, area=f"A1:I{max(8, 7 + len(worklist))}", repeat_rows="7:7")
