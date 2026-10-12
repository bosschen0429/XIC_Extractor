from __future__ import annotations

from openpyxl.comments import Comment
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

from xic_extractor.output.schema import WORKBOOK_DETAIL_HEADERS, WORKBOOK_RESULT_HEADERS
from xic_extractor.output.workbook_styles import (
    _MS2_HEADER,
    _SAMPLE_HEADER,
    BORDER,
    CENTER,
    CENTER_WRAP,
    _add_table,
    _apply,
    _header_style,
    _long_cell_fill,
    _long_cell_value,
    _long_column_width,
    _long_number_format,
    _prepare_print_view,
)


def _build_data_sheet(
    ws, rows: list[dict[str, str]], *, review_rows: list[dict[str, str]]
) -> None:
    """Present the finished measurements and review routing once, per sample."""
    review_by_key = {(r["Sample"], r["Target"]): r for r in review_rows}
    for column, header in enumerate(WORKBOOK_RESULT_HEADERS, start=1):
        color = _MS2_HEADER if header in WORKBOOK_DETAIL_HEADERS else _SAMPLE_HEADER
        _apply(ws.cell(row=1, column=column, value=header), **_header_style(color))
    descriptions = {
        "SampleName": "樣本名稱。此欄與 Target 共同識別每一筆結果。",
        "Target": "目標名稱。用篩選器查看單一目標的所有樣本。",
        "Area": "峰面積（counts·s）。保留正式積分值；有數值不代表已計入偵測。",
        "RT": "所選峰的頂點保留時間（min）。",
        "IntSmoothed": "平滑後的峰頂強度（counts），沿用正式量測值；不是峰面積。",
        "Int": "原始峰頂強度（counts），沿用正式量測值。",
        "PeakWidth": "所選峰的寬度（min）。",
        "Counted Detection": (
            "是否計入偵測：TRUE＝已計入；FALSE＝未計入。與檢閱註記分開判讀。"
        ),
        "Review": ("Review／Check＝尚待處理的結果。空白表示沒有待處理事項。"),
        "Review Reason": (
            "白話檢閱說明。完整原因、下一步與原始事件可在 Diagnostics 查看。"
        ),
    }
    for column, header in enumerate(WORKBOOK_RESULT_HEADERS, start=1):
        if header in descriptions:
            ws.cell(1, column).comment = Comment(descriptions[header], "XIC Extractor")
    ws.row_dimensions[1].height = 32
    for row_index, row in enumerate(rows, start=2):
        review = review_by_key.get(
            (row.get("SampleName", ""), row.get("Target", "")), {}
        )
        values = row | {
            "Review": review.get("Status", ""),
            "Review Reason": review.get("Why", ""),
            "Review Evidence": review.get("Evidence", ""),
        }
        for column, header in enumerate(WORKBOOK_RESULT_HEADERS, start=1):
            raw = values.get(header, "")
            cell = ws.cell(row=row_index, column=column)
            cell.value = _long_cell_value(header, raw)
            _apply(
                cell,
                fill=_long_cell_fill(header, raw, row_index % 2 == 0),
                # Values read best centered; only explanatory sentences wrap left.
                alignment=(
                    Alignment(horizontal="left", vertical="center", wrap_text=True)
                    if header in {"Review Reason", "Reason", "NL Evidence"}
                    else CENTER_WRAP
                    if header in {"SampleName", "Target"}
                    else CENTER
                ),
                border=BORDER,
            )
            if isinstance(cell.value, float):
                cell.number_format = _long_number_format(header)
        ws.row_dimensions[row_index].height = 26
    ws.auto_filter.ref = (
        f"A1:{get_column_letter(len(WORKBOOK_RESULT_HEADERS))}{max(1, len(rows) + 1)}"
    )
    ws.freeze_panes = "C2"
    _add_table(ws, "TargetedResults", ws.auto_filter.ref)
    _prepare_print_view(ws, area=f"A1:F{max(2, len(rows) + 1)}")
    for column, header in enumerate(WORKBOOK_RESULT_HEADERS, start=1):
        dimension = ws.column_dimensions[get_column_letter(column)]
        dimension.width = _long_column_width(header)
        if header in WORKBOOK_DETAIL_HEADERS:
            dimension.hidden = True
            dimension.outlineLevel = 1
