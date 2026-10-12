"""A per-result reading guide with the original diagnostic events retained."""

from __future__ import annotations

from collections import Counter
from math import ceil

from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from xic_extractor.output.review_queue_model import _review_queue_rows
from xic_extractor.output.schema import DIAGNOSTIC_HEADERS
from xic_extractor.output.targeted_overview import _counted, summarize_targeted_results
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
    overview = summarize_targeted_results(result_rows, review_rows)
    focus_keys = {
        (sample, target.label)
        for target in overview.targets
        for sample in target.focus_samples
    }
    work_by_key = {(r["Sample"], r["Target"]): r for r in review_rows}
    # A minority counted outcome may have no saved review condition. Present its
    # saved decision, not a synthetic scientific flag or a new review verdict.
    for record in result_rows:
        key = (record["SampleName"], record["Target"])
        if key in focus_keys and key not in work_by_key:
            outcome = _counted(record)
            work_by_key[key] = {
                "Sample": key[0],
                "Target": key[1],
                "Outcome": "已計入" if outcome == "TRUE" else "未計入",
                "Why": record.get("Reason") or "已保存判定與此目標的多數不同。",
                "Action": "確認是否抓錯峰。" if outcome == "TRUE" else "確認是否漏抓。",
                "RT": record.get("RT", ""),
                "Area": record.get("Area", ""),
            }
    for record in _review_queue_rows(orphan_rows, rows):
        work_by_key[(record["Sample"], record["Target"])] = record
    target_order = {target.label: i for i, target in enumerate(overview.targets)}
    sample_order = {
        row["SampleName"]: i for i, row in reversed(list(enumerate(result_rows)))
    }
    worklist = sorted(
        work_by_key.values(),
        key=lambda r: (
            (r["Sample"], r["Target"]) not in focus_keys,
            target_order.get(r["Target"], len(target_order)),
            sample_order.get(r["Sample"], len(sample_order)),
        ),
    )
    primary = [r for r in worklist if (r["Sample"], r["Target"]) in focus_keys]
    reason_counts = Counter((r["Target"], r["Why"]) for r in primary)
    summary_header = 7
    list_heading = summary_header + len(reason_counts) + 3
    list_header = list_heading + 1
    secondary_heading = list_header + max(1, len(primary)) + 1
    secondary_header = secondary_heading + 1
    raw_heading = secondary_header + len(worklist) - len(primary) + 3
    widths = (35, 15, 10, 48, 38, 12, 19, 11, 11)
    for index, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(index)].width = width
    for row, text, size in (
        (2, "先看與多數不同的結果", 20),
        (3, "先讀目標 × 原因摘要，再讀樣本；已保存檢閱條件與原始紀錄保留於下方。", 10),
        (
            5,
            f"{overview.focus_count} 筆結果需先看；"
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

    summary_columns = ((1, 1), (2, 4), (6, 2), (8, 3))
    for (column, span), title in zip(
        summary_columns, ("目標", "原因說明", "需先看（筆）", "查看樣本"), strict=True
    ):
        ws.merge_cells(
            start_row=summary_header,
            start_column=column,
            end_row=summary_header,
            end_column=column + span - 1,
        )
        _apply(ws.cell(summary_header, column, title), **_header_style(INK))
    summary_locations = {}
    first_sample: dict[tuple[str, str], int] = {}
    for index, record in enumerate(primary, list_header + 1):
        first_sample.setdefault((record["Target"], record["Why"]), index)
    for index, ((target, reason), count) in enumerate(
        reason_counts.items(), summary_header + 1
    ):
        summary_locations[(target, reason)] = index
        for (column, span), value in zip(
            summary_columns, (target, reason, count, "查看樣本"), strict=True
        ):
            ws.merge_cells(
                start_row=index,
                start_column=column,
                end_row=index,
                end_column=column + span - 1,
            )
            _apply(
                ws.cell(index, column, _excel_text(value)),
                border=BORDER,
                alignment=Alignment(vertical="center", wrap_text=True),
            )
        ws.cell(
            index, 8
        ).hyperlink = f"#'Diagnostics'!A{first_sample[(target, reason)]}"
        ws.cell(index, 8).font = Font(
            name=REPORT_FONT, color=ACCENT, underline="single"
        )
        ws.row_dimensions[index].height = max(
            44, _prose_line_count(reason, 110) * 15 + 12
        )
    if not reason_counts:
        ws.cell(8, 1, "沒有需先看的少數結果；已保存檢閱條件可展開查看。")
    for row, title in (
        (list_heading, "需先看的樣本"),
        (secondary_heading, "其餘已保存檢閱條件（按左側＋展開；不改變判定）"),
    ):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=10)
        ws.cell(row, 1, title).font = Font(
            name=REPORT_FONT, size=12, bold=True, color=MUTED
        )
        ws.row_dimensions[row].height = 30

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
        "返回摘要",
    )
    for header_row in (list_header, secondary_header):
        for column, header in enumerate(headers, 1):
            _apply(ws.cell(header_row, column, header), **_header_style(INK))
    ws.row_dimensions[1].height = 8
    ws.row_dimensions[4].height = 22
    ws.row_dimensions[6].height = 8
    ws.row_dimensions[7].height = 30
    for offset, record in enumerate(worklist):
        index = (
            list_header + 1 + offset
            if offset < len(primary)
            else secondary_header + 1 + offset - len(primary)
        )
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
            "返回摘要" if key in focus_keys else "其他檢閱條件",
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
                    horizontal="left" if column in {4, 5} else "center",
                    vertical="center",
                    wrap_text=True,
                ),
            )
        ws.cell(index, 6).number_format = "0.0000"
        ws.cell(index, 7).number_format = "0.00E+00"
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
        if key in focus_keys:
            summary_row = summary_locations[(record["Target"], record["Why"])]
            ws.cell(index, 10).hyperlink = f"#'Diagnostics'!A{summary_row}"
            ws.cell(index, 10).font = Font(
                name=REPORT_FONT, color=ACCENT, underline="single"
            )
        ws.cell(index, 9).font = Font(
            name=REPORT_FONT, color=ACCENT, underline="single"
        )
        lines = max(
            _prose_line_count(record["Why"], 54),
            _prose_line_count(record["Action"], 42),
        )
        ws.row_dimensions[index].height = max(44, lines * 15 + 12)
    if primary:
        _add_table(
            ws, "TargetedReview", f"A{list_header}:J{list_header + len(primary)}"
        )
    else:
        ws.cell(list_header + 1, 1, "本批沒有需先看的少數結果。")
    secondary_count = len(worklist) - len(primary)
    if secondary_count:
        _add_table(
            ws,
            "SavedReviewConditions",
            f"A{secondary_header}:J{secondary_header + secondary_count}",
        )
        ws.row_dimensions.group(
            secondary_header, secondary_header + secondary_count, hidden=True
        )
        ws.row_dimensions[secondary_heading].collapsed = True
    else:
        ws.cell(secondary_header, 1, "沒有其他已保存檢閱條件。")

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
    ws.column_dimensions["J"].width = 14
    ws.freeze_panes = "B8"
    _prepare_print_view(ws, area=f"A1:J{secondary_heading}", repeat_rows="7:7")
