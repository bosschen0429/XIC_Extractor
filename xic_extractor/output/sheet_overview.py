"""The workbook starts with the outcome, then links to evidence and references."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from xic_extractor.output.targeted_overview import summarize_targeted_results
from xic_extractor.output.workbook_values import _excel_text


def build_overview_sheet(
    ws,
    rows: Sequence[Mapping[str, object]],
    review_rows: Sequence[Mapping[str, object]],
    *,
    has_diagnostics: bool = False,
) -> None:
    overview = summarize_targeted_results(rows, review_rows)
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.outlinePr.summaryBelow = False
    widths = (30, 23, 16, 52, 18)
    for index, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(index)].width = width

    def line(index: int, text: str, *, size: int = 11, bold: bool = False) -> None:
        ws.merge_cells(start_row=index, start_column=1, end_row=index, end_column=5)
        cell = ws.cell(index, 1, _excel_text(text))
        cell.font = Font(name="Calibri", size=size, bold=bold, color="183C42")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[index].height = 32 if size > 12 else 28

    def link(row: int, column: int, text: str, location: str) -> None:
        cell = ws.cell(row, column, _excel_text(text))
        cell.hyperlink = f"#{location}"
        cell.font = Font(color="006B80", underline="single")

    line(1, "Targeted 結果總覽", size=22, bold=True)
    line(
        2,
        f"{overview.sample_count} 個樣本 · {len(overview.targets)} 個目標 · "
        f"{overview.result_count} 筆樣本／目標結果",
    )
    conclusion = (
        f"先看 {overview.review_count} 筆需檢閱結果，"
        f"涉及 {overview.review_sample_count} 個樣本。"
        if overview.review_count
        else "沒有記錄到需檢閱條件；可繼續查看各目標的偵測情況。"
    )
    if not overview.result_count:
        conclusion = "沒有已保存的樣本／目標結果。"
    line(4, conclusion, size=15, bold=True)
    unknown = (
        f"；另有 {overview.count_unknown} 筆未記錄偵測判定"
        if overview.count_unknown
        else ""
    )
    line(
        5,
        f"計入偵測 {overview.counted_count} / {overview.result_count} 筆{unknown}。"
        "計入偵測與需檢閱可重疊。",
    )
    line(6, "這是已保存結果的摘要，不代表整批通過科學或品質驗收。")
    line(8, "各目標結果｜需檢閱項目優先", size=13, bold=True)
    headers = ("目標", "計入偵測 / 結果數", "需檢閱", "主要檢閱原因", "下一步")
    for column, title in enumerate(headers, 1):
        cell = ws.cell(9, column, title)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="246875")
        cell.alignment = Alignment(vertical="center")
    first_row: dict[str, int] = {}
    for index, record in enumerate(rows, 2):
        first_row.setdefault(str(record["Target"]), index)
    for index, target in enumerate(overview.targets, 10):
        counted = f"{target.counted} / {target.total}"
        if target.count_unknown:
            counted += f"（{target.count_unknown} 未記錄）"
        reasons = "; ".join(target.reasons[:2]) or "未記錄需檢閱條件"
        if len(target.reasons) > 2:
            reasons += f"；另 {len(target.reasons) - 2} 種，見結果"
        for column, value in enumerate(
            (target.label, counted, target.review, reasons), 1
        ):
            cell = ws.cell(
                index, column, _excel_text(value) if isinstance(value, str) else value
            )
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.fill = PatternFill(
                "solid", fgColor="FFF4DE" if target.review else "F3F7F8"
            )
        link(index, 5, "查看樣本結果 →", f"'XIC Results'!A{first_row[target.label]}")
        ws.row_dimensions[index].height = 42
    next_row = 11 + len(overview.targets)
    line(
        next_row,
        "下一步：在 XIC Results 篩選 Target 或 Review，點開對應樣本的峰界與原因。",
    )
    link(next_row + 1, 1, "所有樣本結果 →", "'XIC Results'!A1")
    line(next_row + 3, "量值摘要（展開下方列；僅包含計入偵測的結果）", bold=True)
    detail_start = next_row + 4
    for column, title in enumerate(
        ("目標", "Area 中位數", "RT 最小值 (min)", "RT 最大值 (min)"), 1
    ):
        ws.cell(detail_start, column, title).font = Font(bold=True)
    for index, target in enumerate(overview.targets, detail_start + 1):
        ws.cell(index, 1, _excel_text(target.label))
        for column, value in enumerate(
            (target.median_area, target.rt_min, target.rt_max), 2
        ):
            cell = ws.cell(index, column, value if value is not None else "—")
            cell.number_format = "0.0000" if column > 2 else "#,##0.00"
    detail_end = detail_start + len(overview.targets)
    ws.row_dimensions.group(detail_start, detail_end, hidden=True)
    ws.row_dimensions[next_row + 3].collapsed = True
    line(detail_end + 2, "需要追查設定與來源時", bold=True)
    link(detail_end + 3, 1, "目標設定 →", "'Targets'!A1")
    link(detail_end + 3, 2, "方法與執行資訊 →", "'Run Metadata'!A1")
    if has_diagnostics:
        link(detail_end + 3, 4, "診斷原文 →", "'Diagnostics'!A1")
    ws.freeze_panes = "B10"
    ws.print_options.horizontalCentered = True
    ws.print_area = f"A1:E{detail_end + 3}"
