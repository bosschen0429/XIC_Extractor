"""One quiet visual system for the delivered Targeted workbook.

Text reads in two inks; numbers align right and text left. The single accent
marks what the reader should look at first. Grey text marks a value the saved
decision did not count; the decision itself remains a column of its own.
"""

from __future__ import annotations

import re
from unicodedata import east_asian_width

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, NamedStyle, Side
from openpyxl.worksheet.table import Table, TableStyleInfo

from xic_extractor.output.workbook_values import (
    ND_ERROR,
    _excel_text,
    _nl_to_display,
    _safe_float,
)

REPORT_FONT = "Microsoft JhengHei"
INK = "1F2A37"
SOFT = "5B6B7C"
MUTED = "9AA5B1"
FOCUS = "B45309"
_RULE = Side(style="thin", color="E5E9EE")
_HEAD_RULE = Side(style="thin", color="8C99A6")
_READING_TAB = "1F2A37"
_REFERENCE_TAB = "A6B3C2"
READING_SHEETS = ("摘要", "需檢查", "面積", "XIC Results")
LINE_HEIGHT = 16
TEXT_HEADERS = frozenset(
    {
        "SampleName",
        "Target",
        "Review",
        "Review Reason",
        "NL Evidence",
        "Reason",
        "Review Evidence",
        "Role",
        "Group",
        "NL",
        "Confidence",
        "Counted Detection",
    }
)
_NUMBER_FORMATS = {
    "RT": "0.00",
    "PeakStart": "0.00",
    "PeakEnd": "0.00",
    "PeakWidth": "0.000",
    "Area": "0.00E+00",
    "Int": "0.00E+00",
    "IntSmoothed": "0.00E+00",
}


def _font(
    color: str = INK, *, bold: bool = False, size: int = 10, underline=None
) -> Font:
    return Font(
        name=REPORT_FONT, size=size, bold=bold, color=color, underline=underline
    )


def _prepare_sheet(
    ws,
    *,
    landscape: bool = True,
    fit_width: bool = True,
    repeat_rows: str | None = None,
    repeat_columns: str | None = None,
) -> None:
    ws.sheet_view.showGridLines = False
    ws.sheet_format.defaultRowHeight = 18
    ws.sheet_format.customHeight = True
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    # Wide sheets continue across pages instead of shrinking to unreadable text.
    ws.sheet_properties.pageSetUpPr.fitToPage = fit_width
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    if repeat_rows:
        ws.print_title_rows = repeat_rows
    if repeat_columns:
        ws.print_title_cols = repeat_columns
    ws.oddFooter.right.text = "&A · &P / &N"
    ws.oddFooter.right.size = 8
    ws.oddFooter.right.font = REPORT_FONT
    ws.oddFooter.right.color = SOFT


def _write_header(ws, row: int, headers, numeric=frozenset()) -> None:
    for column, title in enumerate(headers, 1):
        cell = ws.cell(row, column, title)
        cell.font = _font(SOFT, bold=True)
        cell.alignment = Alignment(
            horizontal="right" if title in numeric else "left", vertical="center"
        )
        cell.border = Border(bottom=_HEAD_RULE)
    ws.row_dimensions[row].height = 22


def _style_body(
    cell, *, color: str = INK, numeric: bool = False, wrap: bool = False
) -> None:
    """Style a new body cell before its column format or link is applied."""
    workbook = cell.parent.parent
    name = f"xic_body_{color}_{int(numeric)}_{int(wrap)}"
    if name not in workbook.style_names:
        workbook.add_named_style(
            NamedStyle(
                name=name,
                font=_font(color),
                border=Border(bottom=_RULE),
                alignment=Alignment(
                    horizontal="right" if numeric else "left",
                    vertical="top" if wrap else "center",
                    wrap_text=wrap,
                ),
            )
        )
    cell.style = name


def _link(cell, location: str, *, color: str = INK, bold: bool = False) -> None:
    cell.hyperlink = f"#{location}"
    cell.font = _font(color, bold=bold, underline="single")


def _measurement_value(header: str, raw: object) -> object:
    """Saved measurements become numbers; ND and other text stay literal."""
    if header == "NL":
        return _nl_to_display(str(raw)) if raw else ""
    if header in _NUMBER_FORMATS and isinstance(raw, str) and raw not in ND_ERROR:
        parsed = _safe_float(raw)
        if parsed is not None:
            return parsed
    return _excel_text(raw)


def _number_format(header: str) -> str | None:
    return _NUMBER_FORMATS.get(header)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _text_width(value: object) -> int:
    return sum(2 if east_asian_width(char) in {"W", "F"} else 1 for char in str(value))


def _prose_line_count(text: object, width: int) -> int:
    """Reserve row height without inserting line breaks into saved prose."""
    lines = 1
    used = 0
    for token in re.findall(r"[A-Za-z0-9_./-]+|.", str(text or ""), re.DOTALL):
        if token == "\n":
            lines += 1
            used = 0
            continue
        size = _text_width(token)
        if used and used + size > width and token not in "，。；：、？！）】」》":
            lines += 1
            used = 0
        used += size
    return lines


def _fit_columns(ws, *, minimum: int = 8, maximum: int = 40) -> None:
    widths: dict[str, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            size = 10 if _is_number(cell.value) else _text_width(cell.value)
            widths[cell.column_letter] = max(widths.get(cell.column_letter, 0), size)
    for letter, size in widths.items():
        ws.column_dimensions[letter].width = max(minimum, min(maximum, size + 3))


def _add_table(ws, name: str, reference: str) -> None:
    table = Table(displayName=name, ref=reference)
    table.tableStyleInfo = TableStyleInfo(showRowStripes=False, showColumnStripes=False)
    ws.add_table(table)
    # A Table owns its filter. A second worksheet filter on the same range
    # makes desktop Excel repair the workbook and remove the Table.
    ws.auto_filter.ref = None


def _apply_sheet_role_styles(wb: Workbook) -> None:
    for sheet in wb.worksheets:
        sheet.sheet_properties.tabColor = (
            _READING_TAB if sheet.title in READING_SHEETS else _REFERENCE_TAB
        )
