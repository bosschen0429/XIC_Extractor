"""Saved Targeted areas in the publication's resolved injection order."""

from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

from xic_extractor.output.workbook_styles import (
    INK,
    MUTED,
    SOFT,
    _font,
    _measurement_value,
    _prepare_sheet,
    _style_body,
    _text_width,
    _write_header,
)
from xic_extractor.output.workbook_values import _excel_text

_HEADER_ROW = 4


def build_area_matrix_sheet(ws, rows) -> None:
    """Project saved values only; no filtering by counted outcome or ratios.

    Analytes sharing an ISTD sit together, followed by that ISTD, so each
    ratio is one formula away. Grey text repeats what `Counted Detection`
    already records.
    """
    samples = list(dict.fromkeys(row["SampleName"] for row in rows))
    records = {(row["SampleName"], row["Target"]): row for row in rows}
    labels = _column_order(rows)
    istds = {row["Target"] for row in rows if row.get("Role") == "ISTD"}
    _prepare_sheet(
        ws,
        fit_width=False,
        repeat_rows=f"{_HEADER_ROW}:{_HEADER_ROW}",
        repeat_columns="A:A",
    )
    ws["A1"] = "峰面積（counts·s）"
    ws["A1"].font = _font(INK, bold=True, size=14)
    ws["A2"] = (
        "灰字＝未計入（保留已保存數值）· ND＝未偵測 · "
        "空白＝未提供定量值（原因見 XIC Results）· 灰色欄名＝ISTD"
    )
    ws["A2"].font = _font(SOFT, size=9)
    _write_header(
        ws,
        _HEADER_ROW,
        ("樣本", *(_excel_text(label) for label in labels)),
        set(labels),
    )
    for column, label in enumerate(labels, 2):
        header = ws.cell(_HEADER_ROW, column)
        header.font = _font(MUTED if label in istds else SOFT, bold=True)
        header.alignment = Alignment(horizontal="right", vertical="bottom")
        ws.column_dimensions[get_column_letter(column)].width = max(
            11, min(24, _text_width(label) + 3)
        )
    for row, sample in enumerate(samples, _HEADER_ROW + 1):
        _style_body(ws.cell(row, 1, _excel_text(sample)))
        for column, label in enumerate(labels, 2):
            record = records.get((sample, label), {})
            counted = str(record.get("Counted Detection", "")).upper() == "TRUE"
            cell = ws.cell(
                row, column, _measurement_value("Area", record.get("Area", ""))
            )
            _style_body(cell, color=INK if counted else MUTED, numeric=True)
            cell.number_format = "0.00E+00"
    ws.column_dimensions["A"].width = max(
        12, max((_text_width(sample) for sample in samples), default=0) + 3
    )
    ws.freeze_panes = f"B{_HEADER_ROW + 1}"
    ws.print_area = (
        f"A1:{get_column_letter(len(labels) + 1)}{_HEADER_ROW + len(samples)}"
    )


def _column_order(rows) -> list[str]:
    targets = list(dict.fromkeys(row["Target"] for row in rows))
    roles = {row["Target"]: row.get("Role", "") for row in rows}
    pairs = {row["Target"]: row.get("ISTD Pair", "") for row in rows}
    order: list[str] = []
    for label in targets:
        if roles[label] == "ISTD" or label in order:
            continue
        pair = pairs[label]
        if pair not in roles:
            order.append(label)
            continue
        order.extend(
            other
            for other in targets
            if roles[other] != "ISTD" and pairs[other] == pair
        )
        order.append(pair)
    return order + [label for label in targets if label not in order]
