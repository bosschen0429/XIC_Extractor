"""Render retained per-peak method answers, never reinterpret RAW evidence."""

from openpyxl.utils import get_column_letter

from xic_extractor.configuration.delta_mass_table import (
    DELTA_MASS_HEADERS,
    DELTA_MASS_SHEET,
    delta_mass_rows,
)
from xic_extractor.configuration.targeted_identification import (
    BASE_POSITION,
    target_specificity,
)
from xic_extractor.configuration.targets import targets_delta_mass_payload
from xic_extractor.output.workbook_styles import (
    _add_table,
    _fit_columns,
    _prepare_sheet,
    _style_body,
    _write_header,
)
from xic_extractor.output.workbook_values import _excel_text

METHOD_HEADERS = (
    "SampleName",
    "Target",
    "Specificity",
    "Candidate",
    "Candidate RT",
    "RT Start",
    "RT End",
    "Selected",
    "Method Accepted",
    "Condition",
    "Kind",
    "Condition Label",
    "Status",
    "Reason",
)
# Definitions repeat the list-shared Delta Mass settings on each ion row.
_SETTING_HEADERS = (
    ("m/z Tolerance ppm", "mz_tolerance_ppm"),
    ("Apex Floor sec", "apex_floor_sec"),
    ("Scan Interval Multiplier", "scan_interval_multiplier"),
    ("Shape Window sec", "shape_window_sec"),
    ("Min Shape r", "min_shape_r"),
)
DEFINITION_HEADERS = (*DELTA_MASS_HEADERS, *(header for header, _ in _SETTING_HEADERS))
_DEFINITION_NUMERIC = frozenset(
    {
        "m/z Tolerance ppm",
        "Delta m/z",
        "Ratio Min",
        "Ratio Max",
        "Apex Floor sec",
        "Scan Interval Multiplier",
        "Shape Window sec",
        "Min Shape r",
    }
)
DELTA_MASS_EVIDENCE_HEADERS = (
    "SampleName",
    "Target",
    "Specificity",
    "Condition",
    "Candidate",
    "Candidate RT",
    "Selected",
    "Pattern Status",
    "Reason",
    "Reference Position",
    "Position",
    "Expected m/z",
    "Apex RT",
    "Apex Delta to Quant sec",
    "Height Ratio to Quant",
    "Ratio Status",
    "Shape r",
    "Shape Threshold",
)
_NUMERIC = frozenset(
    {
        "Candidate",
        "Candidate RT",
        "RT Start",
        "RT End",
        "Condition",
        "Expected m/z",
        "Apex RT",
        "Apex Delta to Quant sec",
        "Height Ratio to Quant",
        "Shape r",
        "Shape Threshold",
    }
)


def identification_evidence_rows(run_output, targets):
    conditions, positions = [], []
    for file in run_output.file_results:
        for target in targets:
            method = target.identification
            if method is None:
                continue
            result = file.results.get(target.label)
            common = {
                "SampleName": file.sample_name,
                "Target": target.label,
                "Specificity": target_specificity(target),
            }
            if result is None or not result.method_candidate_evidence:
                conditions.append(
                    {
                        **common,
                        "Status": "not_evaluated",
                        "Method Accepted": "FALSE",
                        "Reason": file.error or "未找到可評估的定量峰",
                    }
                )
                continue
            for candidate in result.method_candidate_evidence:
                peak = {
                    **common,
                    "Candidate": candidate.candidate_index + 1,
                    "Candidate RT": candidate.rt_apex_min,
                    "Selected": "TRUE"
                    if candidate.candidate_index
                    == result.selected_method_candidate_index
                    else "FALSE",
                }
                answer = {
                    **peak,
                    "RT Start": candidate.rt_start_min,
                    "RT End": candidate.rt_end_min,
                    "Method Accepted": "TRUE" if candidate.accepted else "FALSE",
                }
                for condition in candidate.conditions:
                    conditions.append(
                        {
                            **answer,
                            "Condition": condition.condition_index + 1,
                            "Kind": condition.kind,
                            "Condition Label": condition.label,
                            "Status": condition.status,
                        }
                    )
                    for match in condition.delta_mass_candidates:
                        quant_apex = next(
                            (
                                p.apex_rt_min
                                for p in match.positions
                                if p.position_label == BASE_POSITION
                            ),
                            None,
                        )
                        for position in match.positions:
                            positions.append(
                                {
                                    **peak,
                                    "Condition": condition.condition_index + 1,
                                    "Pattern Status": "pass"
                                    if match.status == "candidate"
                                    else "fail",
                                    "Reason": match.reject_reason,
                                    "Reference Position": BASE_POSITION,
                                    "Position": position.position_label,
                                    "Expected m/z": position.expected_mz,
                                    "Apex RT": position.apex_rt_min,
                                    "Apex Delta to Quant sec": None
                                    if quant_apex is None
                                    or position.apex_rt_min is None
                                    else (position.apex_rt_min - quant_apex) * 60,
                                    "Height Ratio to Quant": position.height_ratio,
                                    "Ratio Status": position.ratio_status,
                                    "Shape r": position.shape_r,
                                    "Shape Threshold": position.shape_r_threshold,
                                }
                            )
    return {"conditions": conditions, "positions": positions}


def build_identification_sheets(workbook, targets, evidence_rows):
    details = targets_delta_mass_payload(targets)
    if details is None:
        return
    shared = {header: details["settings"][key] for header, key in _SETTING_HEADERS}
    _evidence_sheet(
        workbook,
        DELTA_MASS_SHEET,
        "DeltaMassDefinitions",
        DEFINITION_HEADERS,
        [
            {
                **row,
                "Compare Shape": "TRUE" if row["Compare Shape"] else "FALSE",
                **shared,
            }
            for row in delta_mass_rows(details)
        ],
        _DEFINITION_NUMERIC,
    )
    evidence_rows = evidence_rows or {"conditions": [], "positions": []}
    _evidence_sheet(
        workbook,
        "Method Evidence",
        "MethodEvidence",
        METHOD_HEADERS,
        evidence_rows["conditions"],
    )
    _evidence_sheet(
        workbook,
        "Delta Mass Evidence",
        "DeltaMassEvidence",
        DELTA_MASS_EVIDENCE_HEADERS,
        evidence_rows["positions"],
    )


def _evidence_sheet(workbook, name, table_name, headers, rows, numeric=_NUMERIC):
    sheet = workbook.create_sheet(name)
    _prepare_sheet(sheet, repeat_rows="1:1")
    _write_header(sheet, 1, headers, numeric)
    for row_number, row in enumerate(rows, start=2):
        for column, key in enumerate(headers, start=1):
            cell = sheet.cell(row_number, column, _excel_text(row.get(key)))
            _style_body(cell, numeric=key in numeric)
            if key in {"Candidate", "Condition"}:
                cell.number_format = "0"
            elif key in numeric and numeric is _NUMERIC:
                cell.number_format = "0.0000"
    _fit_columns(sheet)
    sheet.freeze_panes = "A2"
    _add_table(
        sheet,
        table_name,
        f"A1:{get_column_letter(len(headers))}{max(1, len(rows) + 1)}",
    )
