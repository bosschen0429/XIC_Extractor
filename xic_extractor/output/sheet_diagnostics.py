"""Secondary diagnostic records retain the supplied text, not another verdict."""

from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from xic_extractor.output.schema import DIAGNOSTIC_HEADERS
from xic_extractor.output.workbook_values import _excel_text


def build_diagnostics_sheet(ws, rows: list[dict[str, str]]) -> None:
    ws.append(list(DIAGNOSTIC_HEADERS))
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([_excel_text(row.get(header, "")) for header in DIAGNOSTIC_HEADERS])
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[row[0].row].height = 45
    for column, width in enumerate((28, 24, 24, 90), 1):
        ws.column_dimensions[get_column_letter(column)].width = width
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False
