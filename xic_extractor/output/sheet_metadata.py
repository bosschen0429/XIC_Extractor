from __future__ import annotations

from collections.abc import Sequence

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.output import metadata
from xic_extractor.output.workbook_styles import (
    _add_table,
    _prepare_sheet,
    _style_body,
    _write_header,
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
    "targeted_strategy": "Targeted 選峰與計入規則的完整設定（JSON），供追查使用。",
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
    """Value comes last so a long saved setting extends into empty space."""
    _prepare_sheet(ws, landscape=False)
    _write_header(ws, 1, ("Key", "說明", "Value"))
    rows = [*metadata.build_metadata_rows(config), *extra_metadata]
    for row, (key, value) in enumerate(rows, start=2):
        for column, cell_value in enumerate(
            (key, _DESCRIPTIONS.get(key, ""), value), start=1
        ):
            _style_body(ws.cell(row, column, cell_value))
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 64
    ws.column_dimensions["C"].width = 30
    ws.freeze_panes = "A2"
    reference = f"A1:C{len(rows) + 1}"
    _add_table(ws, "TargetedRunMetadata", reference)
    ws.print_area = reference
