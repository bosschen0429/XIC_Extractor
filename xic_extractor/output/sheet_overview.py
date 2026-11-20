"""The workbook opens on its conclusion: the results to look at, then targets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from openpyxl.utils import get_column_letter

from xic_extractor.output.targeted_overview import TargetedOverview
from xic_extractor.output.workbook_styles import (
    INK,
    LINE_HEIGHT,
    MUTED,
    SOFT,
    _add_table,
    _font,
    _is_number,
    _measurement_value,
    _number_format,
    _prepare_sheet,
    _prose_line_count,
    _style_body,
    _write_header,
)
from xic_extractor.output.workbook_values import _excel_text

_WORKLIST = (
    ("目標", "Target"),
    ("樣本", "Sample"),
    ("結果", "Outcome"),
    ("依據", "依據"),
    ("疑點", "疑點"),
    ("RT (min)", "RT"),
    ("Area", "Area"),
)
_ANALYTES = ("分析物", "計入樣本數")
_WIDTHS = (22, 32, 10, 16, 28, 9, 11)
_FIRST_TABLE_ROW = 4


def build_overview_sheet(
    ws,
    overview: TargetedOverview,
    *,
    worklist: Sequence[Mapping[str, object]],
    provenance: str,
) -> None:
    """Write the conclusion, the results to look at first, then analyte counts.

    ISTD status is one batch fact on line two, naming only an ISTD that was not
    counted in every sample.
    """
    _prepare_sheet(ws, landscape=False)
    ws["A1"] = (
        "尚無已保存結果"
        if not overview.result_count
        else f"先看 {len(worklist)} 筆結果"
        if worklist
        else "沒有需要先看的結果"
    )
    ws["A1"].font = _font(INK, bold=True, size=16)
    ws.row_dimensions[1].height = 30
    facts = [f"{overview.sample_count} 個樣本", f"{len(overview.targets)} 個目標"]
    gaps = [
        f"{target.label} {target.counted}/{target.total}"
        for target in overview.targets
        if target.role == "ISTD" and target.counted < target.total
    ]
    if gaps:
        facts.append(
            f"ISTD 計入 {overview.istd_counted}/{overview.istd_total}"
            f"（{'、'.join(gaps)}）"
        )
    elif overview.istd_total:
        facts.append("ISTD 全部計入")
    if overview.count_unknown:
        facts.append(f"{overview.count_unknown} 筆未記錄計數判定")
    facts.append(provenance)
    ws["A2"] = " · ".join(facts)
    ws["A2"].font = _font(SOFT)

    row = _FIRST_TABLE_ROW
    if worklist:
        row = _write_worklist(ws, row, worklist) + 2
    analytes = [target for target in overview.targets if target.role != "ISTD"]
    if analytes:
        _write_header(ws, row, _ANALYTES, {"計入樣本數"})
        for offset, target in enumerate(analytes, 1):
            _style_body(ws.cell(row + offset, 1, _excel_text(target.label)))
            _style_body(ws.cell(row + offset, 2, target.counted), numeric=True)
        row += len(analytes)
    ws.cell(row + 2, 1, "逐峰檢查：在工作台開啟此檔看 XIC。").font = _font(SOFT, size=9)
    for column, width in enumerate(_WIDTHS, 1):
        ws.column_dimensions[get_column_letter(column)].width = width
    ws.print_area = f"A1:{get_column_letter(len(_WIDTHS))}{row + 2}"


def _write_worklist(ws, header_row: int, worklist) -> int:
    """Write the worklist table; return its last row."""
    _write_header(
        ws, header_row, [title for title, _ in _WORKLIST], {"RT (min)", "Area"}
    )
    row = header_row
    for row, record in enumerate(worklist, header_row + 1):
        for column, (_, key) in enumerate(_WORKLIST, 1):
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
                numeric=measured,
                wrap=True,
            )
            if measured and _is_number(value):
                cell.number_format = _number_format(key)
        lines = max(
            _prose_line_count(record.get("疑點", ""), 26),
            _prose_line_count(record.get("Sample", ""), 30),
        )
        ws.row_dimensions[row].height = LINE_HEIGHT * lines + 4
    last = get_column_letter(len(_WORKLIST))
    _add_table(ws, "TargetedReview", f"A{header_row}:{last}{row}")
    return row
