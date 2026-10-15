"""Keep the run's diagnostic events with the only artifact a run delivers.

The sheet is hidden: it serves tracing a past decision, not reading the batch.
"""

from __future__ import annotations

from xic_extractor.output.schema import DIAGNOSTIC_HEADERS
from xic_extractor.output.workbook_styles import (
    _add_table,
    _prepare_sheet,
    _style_body,
    _write_header,
)
from xic_extractor.output.workbook_values import _excel_text


def build_diagnostic_events_sheet(ws, diagnostics: list[dict[str, str]]) -> None:
    _prepare_sheet(ws, repeat_rows="1:1")
    _write_header(ws, 1, DIAGNOSTIC_HEADERS)
    for row, event in enumerate(diagnostics, start=2):
        for column, header in enumerate(DIAGNOSTIC_HEADERS, start=1):
            _style_body(ws.cell(row, column, _excel_text(event.get(header, ""))))
    for letter, width in zip("ABCD", (32, 20, 28, 100), strict=True):
        ws.column_dimensions[letter].width = width
    _add_table(ws, "DiagnosticEvents", f"A1:D{len(diagnostics) + 1}")
    ws.freeze_panes = "A2"
    ws.sheet_state = "hidden"
