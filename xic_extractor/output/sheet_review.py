"""The results to look at first, one row each, linked to their measurements."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from openpyxl.utils import get_column_letter

from xic_extractor.output.workbook_styles import (
    INK,
    LINE_HEIGHT,
    MUTED,
    _add_table,
    _font,
    _is_number,
    _link,
    _measurement_value,
    _number_format,
    _prepare_sheet,
    _prose_line_count,
    _style_body,
    _write_header,
)
from xic_extractor.output.workbook_values import _excel_text

_COLUMNS = (
    ("目標", "Target", 18),
    ("樣本", "Sample", 34),
    ("結果", "Outcome", 8),
    ("原因", "Why", 44),
    ("建議檢查", "Action", 40),
    ("RT (min)", "RT", 9),
    ("Area", "Area", 11),
)
_NUMERIC = frozenset({"RT (min)", "Area"})


def build_review_sheet(
    ws,
    worklist: Sequence[Mapping[str, object]],
    *,
    result_rows: Mapping[tuple[str, str], int],
) -> dict[str, int]:
    """Write the worklist; return the first row of each target for the summary."""
    _prepare_sheet(ws, repeat_rows="1:1")
    _write_header(ws, 1, [title for title, _, _ in _COLUMNS], _NUMERIC)
    first_rows: dict[str, int] = {}
    for row, record in enumerate(worklist, 2):
        first_rows.setdefault(str(record["Target"]), row)
        for column, (title, key, _) in enumerate(_COLUMNS, 1):
            measured = key in {"RT", "Area"}
            value = (
                _measurement_value(key, record.get(key, ""))
                if measured
                else _excel_text(record.get(key, ""))
            )
            cell = ws.cell(row, column, value)
            _style_body(
                cell,
                color=MUTED if measured and not _is_number(value) else INK,
                numeric=title in _NUMERIC,
                # Every cell starts at the top so wrapped rows read as one line.
                wrap=True,
            )
            if measured and _is_number(value):
                cell.number_format = _number_format(key)
        location = result_rows.get((str(record["Sample"]), str(record["Target"])))
        if location:
            _link(ws.cell(row, 2), f"'XIC Results'!A{location}")
        lines = max(
            _prose_line_count(record.get("Why", ""), 42),
            _prose_line_count(record.get("Action", ""), 38),
            _prose_line_count(record.get("Sample", ""), 32),
        )
        ws.row_dimensions[row].height = LINE_HEIGHT * lines + 4
    if worklist:
        _add_table(ws, "TargetedReview", f"A1:G{len(worklist) + 1}")
    else:
        ws.cell(2, 1, "沒有需要先看的結果。").font = _font(MUTED)
    for column, (_, _, width) in enumerate(_COLUMNS, 1):
        ws.column_dimensions[get_column_letter(column)].width = width
    ws.freeze_panes = "C2"
    return first_rows
