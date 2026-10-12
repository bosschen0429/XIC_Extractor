"""Saved Targeted areas in the publication's resolved injection order."""

from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from xic_extractor.output.workbook_styles import (
    BORDER,
    INK,
    REPORT_FONT,
    _apply,
    _header_style,
    _long_cell_value,
    _prepare_print_view,
)
from xic_extractor.output.workbook_values import _excel_text


def build_area_matrix_sheet(ws, rows) -> None:
    """Project saved values only; no filtering by counted outcome or ratios."""
    samples = list(dict.fromkeys(row["SampleName"] for row in rows))
    targets = dict.fromkeys(row["Target"] for row in rows)
    roles = {row["Target"]: row.get("Role", "") for row in rows}
    values = {(row["SampleName"], row["Target"]): row.get("Area", "") for row in rows}
    ws["A1"] = "Area Matrix · 已保存峰面積（counts·s）"
    ws["A1"].font = Font(name=REPORT_FONT, size=18, bold=True, color=INK)
    ws["A2"] = "依正式進樣順序；保留零、ND 與缺值；有面積不代表計入。不計算比例。"
    ws.column_dimensions["A"].width = 40
    first_row = 4
    max_columns = 1
    for role in ("Analyte", "ISTD"):
        labels = [
            label for label in targets if (roles[label] == "ISTD") == (role == "ISTD")
        ]
        if not labels:
            continue
        ws.cell(first_row, 1, role).font = Font(
            name=REPORT_FONT, size=13, bold=True, color=INK
        )
        header_row = first_row + 1
        for column, label in enumerate(("SampleName", *labels), 1):
            _apply(
                ws.cell(header_row, column, _excel_text(label)), **_header_style(INK)
            )
        for index, sample in enumerate(samples, header_row + 1):
            _apply(ws.cell(index, 1, _excel_text(sample)), border=BORDER)
            for column, label in enumerate(labels, 2):
                cell = ws.cell(
                    index,
                    column,
                    _long_cell_value("Area", values.get((sample, label), "")),
                )
                _apply(
                    cell,
                    border=BORDER,
                    alignment=Alignment(horizontal="center", vertical="center"),
                )
                cell.number_format = "0.00E+00"
                ws.column_dimensions[get_column_letter(column)].width = 20
        first_row = header_row + len(samples) + 3
        max_columns = max(max_columns, 1 + len(labels))
    ws.freeze_panes = "B6"
    _prepare_print_view(
        ws, area=f"A1:{get_column_letter(max_columns)}{max(2, first_row - 2)}"
    )
