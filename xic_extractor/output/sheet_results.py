from __future__ import annotations

from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

from xic_extractor.output.result_labels import basis_needs_look
from xic_extractor.output.schema import WORKBOOK_DETAIL_HEADERS, WORKBOOK_RESULT_HEADERS
from xic_extractor.output.workbook_styles import (
    FOCUS,
    INK,
    MUTED,
    TEXT_HEADERS,
    _add_table,
    _fit_columns,
    _is_number,
    _measurement_value,
    _number_format,
    _prepare_sheet,
    _style_body,
    _write_header,
)

_CENTERED = frozenset({"Counted Detection"})


def _build_data_sheet(ws, rows: list[dict[str, str]]) -> None:
    """Present the finished measurements and their labels once, per sample.

    Not-counted rows read in grey; `Counted Detection` stays the recorded fact.
    The accent marks the label cell that needs a look.
    """
    headers = WORKBOOK_RESULT_HEADERS
    numeric = frozenset(headers) - TEXT_HEADERS
    _prepare_sheet(ws, repeat_rows="1:1")
    _write_header(ws, 1, headers, numeric)
    for row_index, row in enumerate(rows, start=2):
        counted = row.get("Counted Detection", "").upper() == "TRUE"
        focus = {
            "依據": basis_needs_look(
                row.get("依據"), is_istd=row.get("Role", "").upper() == "ISTD"
            ),
            "疑點": bool(row.get("疑點")),
        }
        for column, header in enumerate(headers, start=1):
            value = _measurement_value(header, row.get(header, ""))
            cell = ws.cell(row=row_index, column=column, value=value)
            color = INK if counted else MUTED
            if focus.get(header):
                color = FOCUS
            elif header in numeric and not _is_number(value):
                color = MUTED
            _style_body(cell, color=color, numeric=header in numeric)
            if header in _CENTERED:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            if _is_number(value) and _number_format(header):
                cell.number_format = _number_format(header)
    for column, header in enumerate(headers, start=1):
        if header in _CENTERED:
            cell = ws.cell(1, column)
            cell.alignment = Alignment(horizontal="center", vertical="center")
    last_column = get_column_letter(len(headers))
    _add_table(ws, "TargetedResults", f"A1:{last_column}{max(1, len(rows) + 1)}")
    _fit_columns(ws, maximum=34)
    for header in WORKBOOK_DETAIL_HEADERS:
        dimension = ws.column_dimensions[_letter(header)]
        dimension.hidden = True
        dimension.outlineLevel = 1
    ws.freeze_panes = "C2"
    ws.print_area = f"A1:{_letter('疑點')}{max(2, len(rows) + 1)}"


def _letter(header: str) -> str:
    return get_column_letter(WORKBOOK_RESULT_HEADERS.index(header) + 1)
