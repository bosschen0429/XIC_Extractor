from __future__ import annotations

from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

from xic_extractor.output.schema import WORKBOOK_DETAIL_HEADERS, WORKBOOK_RESULT_HEADERS
from xic_extractor.output.workbook_styles import (
    _MS2_HEADER,
    _SAMPLE_HEADER,
    BORDER,
    CENTER,
    _apply,
    _header_style,
    _long_cell_fill,
    _long_cell_value,
    _long_column_width,
    _long_number_format,
)


def _build_data_sheet(
    ws, rows: list[dict[str, str]], *, review_rows: list[dict[str, str]]
) -> None:
    """Present the finished measurements and review routing once, per sample."""
    review_by_key = {(r["Sample"], r["Target"]): r for r in review_rows}
    for column, header in enumerate(WORKBOOK_RESULT_HEADERS, start=1):
        color = _MS2_HEADER if header in WORKBOOK_DETAIL_HEADERS else _SAMPLE_HEADER
        _apply(ws.cell(row=1, column=column, value=header), **_header_style(color))
    ws.row_dimensions[1].height = 30
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
                alignment=(
                    Alignment(horizontal="left", vertical="center", wrap_text=True)
                    if header
                    in {
                        "SampleName",
                        "Target",
                        "Review Reason",
                        "Reason",
                        "NL Evidence",
                    }
                    else CENTER
                ),
                border=BORDER,
            )
            if isinstance(cell.value, float):
                cell.number_format = _long_number_format(header)
        ws.row_dimensions[row_index].height = 32
    ws.auto_filter.ref = (
        f"A1:{get_column_letter(len(WORKBOOK_RESULT_HEADERS))}{max(1, len(rows) + 1)}"
    )
    ws.freeze_panes = "C2"
    ws.sheet_view.showGridLines = False
    for column, header in enumerate(WORKBOOK_RESULT_HEADERS, start=1):
        dimension = ws.column_dimensions[get_column_letter(column)]
        dimension.width = _long_column_width(header)
        if header in WORKBOOK_DETAIL_HEADERS:
            dimension.hidden = True
            dimension.outlineLevel = 1
