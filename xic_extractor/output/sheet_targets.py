from __future__ import annotations

from openpyxl.comments import Comment
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

from xic_extractor.configuration.models import Target
from xic_extractor.output.workbook_styles import (
    _SAMPLE_HEADER,
    _TARGETS_HEADERS,
    BORDER,
    CENTER,
    GREY,
    WHITE,
    _add_table,
    _apply,
    _excel_text,
    _fill,
    _header_style,
    _prepare_print_view,
)


def _build_targets_sheet(ws, targets: list[Target]) -> None:
    for col_idx, header in enumerate(_TARGETS_HEADERS, start=1):
        _apply(
            ws.cell(row=1, column=col_idx, value=header),
            **_header_style(_SAMPLE_HEADER),
        )
    ws["I1"].comment = Comment(
        "理論產物 m/z＝目標 m/z－設定的中性丟失質量。"
        "這是設定參考值，不是實測產物或 NL 通過判定。"
        "NL 需按每個 MS2 掃描的實測中性丟失與所選峰的對應關係判讀。",
        "XIC Extractor",
    )
    notes = {
        "A1": "目標名稱；與 XIC Results 的 Target 欄對應。",
        "B1": "ISTD 為內標；Analyte 為分析目標。",
        "C1": "分析目標所配對的 ISTD；空白表示未設定配對。",
        "D1": "目標前驅離子質荷比。",
        "E1": "RT 定位提示的下限（min），不是身份確認的硬性界線。",
        "F1": "RT 定位提示的上限（min），不是身份確認的硬性界線。",
        "G1": "MS1 擷取的質量容差（ppm）。",
        "H1": "設定的中性丟失質量（Da）。未設定不代表測得零。",
        "L1": "目標適用的樣本範圍。空白表示未限制此條件。",
    }
    for address, note in notes.items():
        ws[address].comment = Comment(note, "XIC Extractor")
    ws.row_dimensions[1].height = 32

    for row_idx, target in enumerate(targets, start=2):
        fill_hex = GREY if row_idx % 2 == 0 else WHITE
        expected_product = (
            target.mz - target.neutral_loss_da
            if target.neutral_loss_da is not None
            else None
        )
        values: list[object] = [
            _excel_text(target.label),
            "ISTD" if target.is_istd else "Analyte",
            _excel_text(target.istd_pair),
            target.mz,
            target.rt_min,
            target.rt_max,
            target.ppm_tol,
            target.neutral_loss_da if target.neutral_loss_da is not None else "—",
            expected_product if expected_product is not None else "—",
            target.nl_ppm_warn if target.nl_ppm_warn is not None else "—",
            target.nl_ppm_max if target.nl_ppm_max is not None else "—",
            target.sample_applicability,
        ]
        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            _apply(
                cell,
                fill=_fill(fill_hex),
                border=BORDER,
                alignment=Alignment(
                    horizontal="left", vertical="center", wrap_text=True
                )
                if col_idx in {1, 3, 12}
                else CENTER,
            )
            header = _TARGETS_HEADERS[col_idx - 1]
            if isinstance(value, float):
                if header in {"m/z", "Expected product m/z", "NL (Da)"}:
                    cell.number_format = "0.0000"
                elif header in {"RT min", "RT max"}:
                    cell.number_format = "0.00"
        ws.row_dimensions[row_idx].height = 28

    widths = [24, 12, 24, 14, 10, 10, 10, 12, 22, 14, 14, 24]
    for col_idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.auto_filter.ref = f"A1:L{max(1, len(targets) + 1)}"
    ws.freeze_panes = "A2"
    _add_table(ws, "TargetedDefinitions", ws.auto_filter.ref)
    ws.column_dimensions.group("I", "L", hidden=True)
    _prepare_print_view(ws, area=f"A1:H{max(2, len(targets) + 1)}")
