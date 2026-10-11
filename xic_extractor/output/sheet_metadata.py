from __future__ import annotations

from collections.abc import Sequence

from openpyxl.styles import Alignment

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.output import metadata
from xic_extractor.output.workbook_styles import (
    _SAMPLE_HEADER,
    BORDER,
    CENTER,
    GREY,
    WHITE,
    _add_table,
    _apply,
    _fill,
    _header_style,
    _prepare_print_view,
)

_DESCRIPTIONS = {
    "config_hash": "分析設定的識別碼，供比對兩次執行是否採用相同設定。",
    "app_version": "產生這份結果的 XIC Extractor 版本。",
    "generated_at": "結果產生時間（UTC）。",
    "target_config_hash": "目標清單的識別碼，供追查所使用的目標設定。",
    "targeted_output_schema_version": (
        "已保存結果的資料格式，供 Inspector 與其他讀取工具使用。"
    ),
    "targeted_workbook_layout_version": "活頁簿欄位契約版本。版面樣式不改變量測數值。",
    "parallel_mode": "樣本處理方式；serial 表示循序處理。",
    "parallel_workers": "平行處理的 worker 設定；循序模式不會使用此數量。",
    "signal_processing_method": "XIC 平滑方法；gaussian 表示 Gaussian 平滑。",
    "signal_processing_window_points": "平滑視窗涵蓋的資料點數。",
    "ms2_precursor_tol_da": "MS2 前驅離子擷取容差（Da）。",
    "nl_min_intensity_ratio": "NL 候選產物的最低相對強度設定。",
    "count_no_ms2_as_detected": "保留的相容設定，不取代每一筆正式結果的計數判定。",
    "nl_rt_anchor_search_margin_min": "尋找 NL 定位點時使用的 RT 範圍設定（min）。",
    "nl_rt_anchor_half_window_min": "NL 定位點周圍的搜尋半寬（min）。",
    "nl_fallback_half_window_min": "無可用 NL 定位點時的備用搜尋半寬（min）。",
    "rolling_window_size": "滾動 RT 參考使用的視窗大小。",
    "dirty_matrix_mode": "保存的複雜基質模式設定。",
}


def _build_metadata_sheet(
    ws, config: ExtractionConfig, *, extra_metadata: Sequence[tuple[str, object]] = ()
) -> None:
    for col_idx, header in enumerate(("Key", "Value", "說明"), start=1):
        _apply(
            ws.cell(row=1, column=col_idx, value=header),
            **_header_style(_SAMPLE_HEADER),
        )

    for row_idx, (key, value) in enumerate(
        [*metadata.build_metadata_rows(config), *extra_metadata], start=2
    ):
        fill_hex = GREY if row_idx % 2 == 0 else WHITE
        _apply(
            ws.cell(row=row_idx, column=1, value=key),
            fill=_fill(fill_hex),
            alignment=Alignment(horizontal="left", vertical="center"),
            border=BORDER,
        )
        _apply(
            ws.cell(row=row_idx, column=2, value=value),
            fill=_fill(fill_hex),
            alignment=CENTER,
            border=BORDER,
        )
        _apply(
            ws.cell(row_idx, 3, _DESCRIPTIONS.get(key, "")),
            fill=_fill(fill_hex),
            border=BORDER,
            alignment=Alignment(horizontal="left", vertical="center", wrap_text=True),
        )
        ws.row_dimensions[row_idx].height = 38

    ws.column_dimensions["A"].width = 39
    ws.column_dimensions["B"].width = 29
    ws.column_dimensions["C"].width = 67
    ws.row_dimensions[1].height = 34
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:C{ws.max_row}"
    _add_table(ws, "TargetedRunMetadata", ws.auto_filter.ref)
    _prepare_print_view(ws, area=ws.auto_filter.ref)
