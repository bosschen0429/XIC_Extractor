"""One visible place that says what every delivered column means."""

from __future__ import annotations

from xic_extractor.output.workbook_styles import (
    LINE_HEIGHT,
    _add_table,
    _prepare_sheet,
    _prose_line_count,
    _style_body,
    _write_header,
)

FIELD_GUIDE: tuple[tuple[str, str, str, str], ...] = (
    (
        "面積",
        "（每個目標一欄）",
        "已保存峰面積；analyte 後面接它配對的 ISTD。"
        "灰字＝未計入，數值保留；ND＝未偵測。",
        "counts·s",
    ),
    ("XIC Results", "SampleName", "樣本名稱；與 Target 共同識別一筆結果。", ""),
    ("XIC Results", "Target", "目標名稱；對應 Targets 的 Label。", ""),
    ("XIC Results", "RT", "所選峰的頂點保留時間。", "min"),
    ("XIC Results", "Area", "所選峰的面積。有數值不代表已計入。", "counts·s"),
    ("XIC Results", "IntSmoothed", "平滑後的峰頂強度；不是峰面積。", "counts"),
    (
        "XIC Results",
        "Counted Detection",
        "TRUE＝已計入；FALSE＝未計入。灰字列即 FALSE。",
        "",
    ),
    ("XIC Results", "Review", "Review／Check＝有尚待確認的事項；空白＝沒有。", ""),
    ("XIC Results", "Review Reason", "待確認事項的白話說明。", ""),
    (
        "XIC Results",
        "PeakStart / PeakEnd",
        "積分起點與終點；此欄起為收合欄，按欄標上方＋展開。",
        "min",
    ),
    ("XIC Results", "NL Evidence / Reason", "中性丟失觀察與完整判定說明。", ""),
    ("XIC Results", "Role / Group", "ISTD 或 Analyte；依樣本名稱判斷的樣本組別。", ""),
    ("XIC Results", "NL / Confidence", "中性丟失摘要與信心分級，供追查用。", ""),
    ("XIC Results", "Int / PeakWidth", "原始峰頂強度與峰寬。", "counts / min"),
    ("Targets", "m/z", "目標前驅離子質荷比。", "m/z"),
    ("Targets", "RT min / RT max", "RT 定位提示範圍，不是身分確認的硬界線。", "min"),
    ("Targets", "NL (Da)", "設定的中性丟失質量；空白＝未設定，不是測得零。", "Da"),
    (
        "Targets",
        "Expected product m/z",
        "目標 m/z 減去中性丟失質量的設定參考值，不是實測產物。",
        "m/z",
    ),
    ("Targets", "Sample applicability", "目標適用的樣本範圍；空白＝不限制。", ""),
    ("Run Metadata", "Key / Value", "分析設定與版本，供比對兩次執行是否相同。", ""),
)
_HEADERS = ("工作表", "欄位", "意思", "單位")


def build_field_guide_sheet(ws) -> None:
    _prepare_sheet(ws, landscape=False)
    _write_header(ws, 1, _HEADERS)
    for row, entry in enumerate(FIELD_GUIDE, start=2):
        for column, value in enumerate(entry, start=1):
            _style_body(ws.cell(row, column, value), wrap=column == 3)
        ws.row_dimensions[row].height = (
            LINE_HEIGHT * _prose_line_count(entry[2], 58) + 4
        )
    for letter, width in zip("ABCD", (14, 22, 60, 12), strict=True):
        ws.column_dimensions[letter].width = width
    _add_table(ws, "FieldGuide", f"A1:D{len(FIELD_GUIDE) + 1}")
    ws.freeze_panes = "A2"
