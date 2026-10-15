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
    ("摘要", "目標 / 類型 / 配對 ISTD", "目標名稱、ISTD 或 Analyte、配對的 ISTD。", ""),
    ("摘要", "計入 / 全部", "已計入的結果數與已保存的結果數。", ""),
    (
        "摘要",
        "需先看 / 說明",
        "該目標要先看的結果數與要確認的事；點數字跳到需檢查。",
        "",
    ),
    ("需檢查", "目標 / 樣本", "要先看的結果；點樣本跳到 XIC Results 的那一列。", ""),
    ("需檢查", "結果", "已保存的判定：已計入或未計入。", ""),
    ("需檢查", "原因 / 建議檢查", "判定依據的證據，以及能推翻它的那一項檢查。", ""),
    ("需檢查", "RT (min) / Area", "所選峰的頂點保留時間與面積。", "min / counts·s"),
    (
        "面積",
        "樣本 / （每個目標一欄）",
        "已保存峰面積；共用同一 ISTD 的 analyte 排在一起，後面接該 ISTD。"
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
    ("XIC Results", "Review Evidence", "檢閱註記的來源事件代碼，供追查用。", ""),
    ("XIC Results", "Role / Group", "ISTD 或 Analyte；依樣本名稱判斷的樣本組別。", ""),
    ("XIC Results", "NL / Confidence", "中性丟失摘要與信心分級，供追查用。", ""),
    ("XIC Results", "Int / PeakWidth", "原始峰頂強度與峰寬。", "counts / min"),
    ("Targets", "Label / Role", "目標名稱；ISTD 或 Analyte。", ""),
    ("Targets", "ISTD Pair", "analyte 配對的 ISTD；空白＝未設定。", ""),
    ("Targets", "m/z", "目標前驅離子質荷比。", "m/z"),
    ("Targets", "ppm tol", "擷取 XIC 的質量容許誤差。", "ppm"),
    ("Targets", "RT min / RT max", "RT 定位提示範圍，不是身分確認的硬界線。", "min"),
    ("Targets", "NL (Da)", "設定的中性丟失質量；空白＝未設定，不是測得零。", "Da"),
    (
        "Targets",
        "Expected product m/z",
        "目標 m/z 減去中性丟失質量的設定參考值，不是實測產物。",
        "m/z",
    ),
    (
        "Targets",
        "NL ppm warn / NL ppm max",
        "中性丟失質量偏差的警示門檻與上限。",
        "ppm",
    ),
    ("Targets", "Sample applicability", "目標適用的樣本範圍；空白＝不限制。", ""),
    (
        "Run Metadata",
        "Key / 說明 / Value",
        "分析設定與版本，供比對兩次執行是否相同。",
        "",
    ),
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
