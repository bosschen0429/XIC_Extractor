"""Render retained Targeted Delta Mass answers; never interpret RAW evidence."""

import json
from dataclasses import asdict

from xic_extractor.output.workbook_styles import (
    _add_table,
    _fit_columns,
    _prepare_sheet,
    _style_body,
    _write_header,
)
from xic_extractor.output.workbook_values import _excel_text

DELTA_MASS_HEADERS = (
    "SampleName",
    "Target",
    "Method",
    "Quant Position",
    "Candidate RT",
    "Selected",
    "Pattern Status",
    "Reason",
    "Position",
    "Expected m/z",
    "Apex RT",
    "Apex Delta sec",
    "Height Ratio",
    "Ratio Status",
    "Shape r",
    "Shape Threshold",
)
_NUMERIC = frozenset(
    {
        "Candidate RT",
        "Expected m/z",
        "Apex RT",
        "Apex Delta sec",
        "Height Ratio",
        "Shape r",
        "Shape Threshold",
    }
)
_REASONS = {
    "partner_not_confirmed_in_raw": "配對位置的 RAW 訊號未通過 RT 或峰形比對",
    "apex_out_of_range": "配對峰頂超出共洗脫 RT 容差",
    "discriminating_shape_below_threshold": "指定位置的峰形相關性低於設定值",
    "local_background_unavailable": "背景視窗沒有足夠的量測點",
    "peak_not_above_background": "指定位置的訊號未高於設定背景門檻",
    "lowest_position_not_lowest": "指定的最低強度位置不符合量測結果",
    "base_support_reference_unavailable": "參考位置的背景扣除強度無法使用",
    "base_support_below_threshold": "基準位置相對參考訊號的支持不足",
}


def delta_mass_evidence_rows(run_output, targets):
    """Project owner-recorded pattern answers, including empty searches."""
    rows = []
    for file in run_output.file_results:
        for target in targets:
            if target.delta_mass_settings is None:
                continue
            result = file.results.get(target.label)
            common = {
                "SampleName": file.sample_name,
                "Target": target.label,
                "Method": target.delta_mass_method,
                "Quant Position": target.quant_position,
            }
            if result is None or not result.delta_mass_candidates:
                rows.append(
                    {
                        **common,
                        "Pattern Status": "error" if file.error else "no_match",
                        "Reason": file.error or "未找到可量測且共洗脫的訊號組合",
                    }
                )
                continue
            for match in result.delta_mass_candidates:
                selected = (
                    match.anchor_feature_id == result.selected_delta_mass_feature_id
                )
                for position in match.positions:
                    rows.append(
                        {
                            **common,
                            "Candidate RT": next(
                                p.apex_rt_min
                                for p in match.positions
                                if p.position_label == target.quant_position
                            ),
                            "Selected": "TRUE" if selected else "FALSE",
                            "Pattern Status": "pass"
                            if match.status == "candidate"
                            else "fail",
                            "Reason": _REASONS.get(
                                match.reject_reason, match.reject_reason
                            ),
                            "Position": position.position_label,
                            "Expected m/z": position.expected_mz,
                            "Apex RT": position.apex_rt_min,
                            "Apex Delta sec": position.apex_delta_sec,
                            "Height Ratio": position.height_ratio_to_base,
                            "Ratio Status": position.ratio_status,
                            "Shape r": position.shape_r,
                            "Shape Threshold": position.shape_r_threshold,
                        }
                    )
    return rows


def build_delta_mass_sheets(workbook, targets, evidence_rows):
    methods = {
        t.delta_mass_method: t.delta_mass_settings
        for t in targets
        if t.delta_mass_settings is not None
    }
    if not methods:
        return
    definitions = workbook.create_sheet("Delta Mass Methods")
    _prepare_sheet(definitions, repeat_rows="1:1")
    _write_header(definitions, 1, ("Method", "Definition"), frozenset())
    for name, method in methods.items():
        definitions.append(
            [
                _excel_text(name),
                _excel_text(json.dumps(asdict(method), ensure_ascii=False)),
            ]
        )
    for cells in definitions.iter_rows(min_row=2):
        for cell in cells:
            _style_body(cell)
    _add_table(definitions, "DeltaMassDefinitions", f"A1:B{len(methods) + 1}")
    _fit_columns(definitions)
    sheet = workbook.create_sheet("Delta Mass Evidence")
    _prepare_sheet(sheet, repeat_rows="1:1")
    _write_header(sheet, 1, DELTA_MASS_HEADERS, _NUMERIC)
    for row_number, row in enumerate(evidence_rows, start=2):
        for column, key in enumerate(DELTA_MASS_HEADERS, start=1):
            cell = sheet.cell(row_number, column, _excel_text(row.get(key)))
            _style_body(cell, numeric=key in _NUMERIC)
            if key in _NUMERIC:
                cell.number_format = "0.0000" if key == "Expected m/z" else "0.000"
    _fit_columns(sheet)
    sheet.freeze_panes = "A2"
    _add_table(sheet, "DeltaMassEvidence", f"A1:P{max(1, len(evidence_rows) + 1)}")
