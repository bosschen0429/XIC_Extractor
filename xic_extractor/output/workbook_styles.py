from __future__ import annotations

import re
from unicodedata import east_asian_width

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.table import Table, TableStyleInfo

from xic_extractor.output.schema import WORKBOOK_DETAIL_HEADERS as _ADVANCED_HEADERS
from xic_extractor.output.workbook_values import (
    ND_ERROR,
    _excel_text,
    _nl_to_display,
    _safe_float,
)

REPORT_FONT = "Microsoft JhengHei"
INK = "24364B"
MUTED = "63758A"
ACCENT = "356D95"
SUCCESS = "E8F3ED"
CAUTION = "FFF2DC"
_MS2_HEADER = "65778B"
_MS2_OK = SUCCESS
_MS2_WARN = CAUTION
_MS2_FAIL = "F9E9E7"
_MS2_NO_MS2 = "EDF0F4"
_SAMPLE_HEADER = INK
_RESULT_TAB = ACCENT
_TECHNICAL_TAB = "A6B3C2"
WHITE = "FFFFFF"
GREY = "F3F6FA"
_THIN = Side(style="hair", color="DCE3EC")
BORDER = Border(bottom=_THIN)
CENTER = Alignment(horizontal="center", vertical="center")
CENTER_WRAP = Alignment(horizontal="center", vertical="center", wrap_text=True)
_TARGETS_HEADERS = [
    "Label",
    "Role",
    "ISTD Pair",
    "m/z",
    "RT min",
    "RT max",
    "ppm tol",
    "NL (Da)",
    "Expected product m/z",
    "NL ppm warn",
    "NL ppm max",
    "Sample applicability",
]


def _fill(hex6: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex6)


def _apply(cell, **kw: object) -> None:
    if "font" not in kw:
        cell.font = Font(name=REPORT_FONT, size=11, color=INK)
    for key, value in kw.items():
        setattr(cell, key, value)


def _header_style(hex6: str) -> dict[str, object]:
    return {
        "font": Font(name=REPORT_FONT, bold=True, color="FFFFFF", size=11),
        "fill": _fill(hex6),
        "alignment": CENTER_WRAP,
        "border": BORDER,
    }


def _nl_cell_fill(value: str) -> PatternFill:
    if value == "OK":
        return _fill(_MS2_OK)
    if value.startswith("WARN_"):
        return _fill(_MS2_WARN)
    if value == "NO_MS2":
        return _fill(_MS2_NO_MS2)
    return _fill(_MS2_FAIL)


def _long_cell_value(header: str, raw_val: str) -> object:
    if header in {
        "SampleName",
        "Target",
        "ISTD Pair",
        "Confidence",
        "Reason",
        "Review",
        "Review Reason",
        "Supporting Evidence",
        "Conflicts / Concerns",
    }:
        return _excel_text(raw_val)
    if header == "NL":
        return _nl_to_display(raw_val) if raw_val else ""
    if header in {
        "RT",
        "Area",
        "Int",
        "IntSmoothed",
        "PeakStart",
        "PeakEnd",
        "PeakWidth",
    }:
        if raw_val in ND_ERROR:
            return raw_val
        parsed = _safe_float(raw_val)
        return parsed if parsed is not None else raw_val
    if header in _ADVANCED_HEADERS:
        return _excel_text(raw_val) if raw_val else ""
    return raw_val


def _long_cell_fill(header: str, raw_val: str, alt: bool) -> PatternFill:
    if raw_val in ND_ERROR:
        return _fill("FFE0B2" if raw_val == "ND" else "FF7043")
    if header == "NL" and raw_val:
        return _nl_cell_fill(raw_val)
    if header == "Counted Detection":
        if raw_val.upper() == "TRUE":
            return _fill(SUCCESS)
        if raw_val.upper() == "FALSE":
            return _fill(CAUTION)
    if header == "Review" and raw_val in {"Review", "Check"}:
        return _fill(CAUTION)
    if header in _ADVANCED_HEADERS:
        return _fill("ECEFF1")
    return _fill(GREY if alt else WHITE)


def _long_number_format(header: str) -> str:
    if header in {"RT", "PeakStart", "PeakEnd"}:
        return "0.0000"
    if header == "Area":
        return "#,##0.00"
    if header in {"Int", "IntSmoothed"}:
        return "0.00E+00"
    if header == "PeakWidth":
        return "0.0000"
    return "#,##0"


def _long_column_width(header: str) -> int:
    widths = {
        "SampleName": 38,
        "Target": 18,
        "Area": 18,
        "RT": 11,
        "Counted Detection": 16,
        "Review": 11,
        "Review Reason": 50,
        "Review Evidence": 42,
        "PeakStart": 14,
        "PeakEnd": 14,
        "NL Evidence": 34,
        "Reason": 48,
        "Role": 12,
        "Group": 14,
        "NL": 14,
        "Confidence": 14,
    }
    return widths.get(header, 24)


def _wrap_prose(text: str, width: int) -> str:
    """Give free-text explanations stable line breaks in Excel and previews."""
    parts: list[str] = []
    used = 0
    for token in re.findall(r"[A-Za-z0-9_./-]+|.", text, re.DOTALL):
        if token == "\n":
            parts.append(token)
            used = 0
            continue
        size = sum(2 if east_asian_width(char) in {"W", "F"} else 1 for char in token)
        if used and used + size > width and token not in "，。；：、？！）】」》":
            parts.append("\n")
            used = 0
        parts.append(token)
        used += size
    return "".join(parts)


def _add_table(ws, name: str, reference: str) -> None:
    table = Table(displayName=name, ref=reference)
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleLight9", showRowStripes=True, showColumnStripes=False
    )
    ws.add_table(table)


def _prepare_print_view(ws, *, area: str, repeat_rows: str = "1:1") -> None:
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 90
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_area = area
    ws.print_title_rows = repeat_rows
    ws.sheet_properties.outlinePr.summaryRight = False
    ws.sheet_properties.outlinePr.summaryBelow = False
    ws.oddFooter.left.text = "XIC Extractor"
    ws.oddFooter.right.text = "&A  |  &P / &N"


def _apply_sheet_role_styles(wb: Workbook) -> None:
    role_colors = {
        "Overview": _RESULT_TAB,
        "XIC Results": _RESULT_TAB,
        "Targets": _TECHNICAL_TAB,
        "Run Metadata": _TECHNICAL_TAB,
        "Diagnostics": "B68434",
        "Identity Support": _TECHNICAL_TAB,
        "Sample Context": _TECHNICAL_TAB,
        "Selection Approvals": _TECHNICAL_TAB,
    }
    for name, color in role_colors.items():
        if name in wb.sheetnames:
            wb[name].sheet_properties.tabColor = color
