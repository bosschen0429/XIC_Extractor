from openpyxl import Workbook

from xic_extractor.output.sheet_area_matrix import build_area_matrix_sheet
from xic_extractor.output.sheet_diagnostics import build_diagnostics_sheet
from xic_extractor.output.sheet_overview import build_overview_sheet


def _saved_rows():
    return [
        {
            "SampleName": sample,
            "Target": target,
            "Role": role,
            "Counted Detection": "FALSE"
            if target == "Common" and sample == "S3"
            else "TRUE",
            "Area": area,
            "RT": "9",
        }
        for sample, area in [
            ("S2", "0"),
            ("S1", "ND"),
            ("S3", "13.5"),
            ("S4", ""),
            ("S5", "500"),
        ]
        for target, role in [("Common", "Analyte"), ("Standard", "ISTD")]
    ]


def test_area_matrix_keeps_saved_values_and_injection_order():
    sheet = Workbook().active
    build_area_matrix_sheet(sheet, _saved_rows())
    assert sheet["A4"].value == "Analyte"
    assert sheet["B5"].value == "Common"
    assert [sheet.cell(row, 1).value for row in range(6, 11)] == [
        "S2",
        "S1",
        "S3",
        "S4",
        "S5",
    ]
    assert [sheet.cell(row, 2).value for row in range(6, 11)] == [
        0,
        "ND",
        13.5,
        "",
        500,
    ]
    assert sheet["A13"].value == "ISTD"
    assert sheet["B14"].value == "Standard"
    assert [sheet.cell(row, 2).value for row in range(15, 20)] == [
        0,
        "ND",
        13.5,
        "",
        500,
    ]
    assert all(cell.data_type != "f" for row in sheet for cell in row)


def test_diagnostics_starts_with_minority_summary_and_keeps_other_conditions():
    rows = _saved_rows()
    reviews = [
        {
            "Sample": "S3",
            "Target": "Common",
            "Outcome": "未計入",
            "Why": "缺少 MS2",
            "Action": "檢查",
            "RT": "9",
            "Area": "13.5",
        },
        {
            "Sample": "S1",
            "Target": "Standard",
            "Outcome": "已計入",
            "Why": "品質條件",
            "Action": "檢查",
            "RT": "9",
            "Area": "ND",
        },
    ]
    book = Workbook()
    sheet = book.active
    build_diagnostics_sheet(sheet, [], result_rows=rows, review_rows=reviews)
    assert [sheet.cell(7, col).value for col in (1, 2, 6, 8)] == [
        "目標",
        "原因說明",
        "需先看（筆）",
        "查看樣本",
    ]
    assert [sheet.cell(8, col).value for col in (1, 2, 6)] == [
        "Common",
        "缺少 MS2",
        1,
    ]
    sample_row = next(cell.row for cell in sheet["A"] if cell.value == "S3")
    assert not sheet.row_dimensions[sample_row].hidden
    assert sheet.cell(sample_row, 10).hyperlink.target == "#'Diagnostics'!A8"
    saved_row = next(cell.row for cell in sheet["A"] if cell.value == "S1")
    assert sheet.row_dimensions[saved_row].hidden
    overview = book.create_sheet("Overview")
    build_overview_sheet(overview, rows, reviews)
    assert overview["A7"].value == 1
    assert overview["E7"].value == "4 / 5"
    assert overview["I7"].value == "5 / 5"
    assert overview["A2"].value == "先看 1 筆與多數不同的結果"
    assert rows[4]["Area"] == "13.5"


def test_fully_counted_target_keeps_secondary_heading_and_saved_conditions():
    rows = [r for r in _saved_rows() if r["Target"] == "Standard"]
    reviews = [
        {
            "Sample": "S1",
            "Target": "Standard",
            "Outcome": "已計入",
            "Why": "品質條件",
            "Action": "檢查",
            "RT": "9",
            "Area": "ND",
        }
    ]
    sheet = Workbook().active
    build_diagnostics_sheet(sheet, [], result_rows=rows, review_rows=reviews)
    heading = next(
        cell for cell in sheet["A"] if str(cell.value).startswith("其餘已保存檢閱條件")
    )
    assert sheet.row_dimensions[heading.row].collapsed
    sample = next(cell for cell in sheet["A"] if cell.value == "S1")
    assert sheet.row_dimensions[sample.row].hidden
    assert sheet.cell(sample.row, 4).value == "品質條件"


def test_minority_counted_without_saved_review_still_has_summary_and_sample():
    rows = [
        r | {"Counted Detection": "TRUE" if r["SampleName"] == "S3" else "FALSE"}
        for r in _saved_rows()
        if r["Target"] == "Common"
    ]
    sheet = Workbook().active
    build_diagnostics_sheet(sheet, [], result_rows=rows, review_rows=[])
    assert sheet["A8"].value == "Common"
    assert sheet["F8"].value == 1
    sample = next(cell for cell in sheet["A"] if cell.value == "S3")
    assert sheet.cell(sample.row, 3).value == "已計入"
    assert sheet.cell(sample.row, 7).value == 13.5
    assert sheet.cell(sample.row, 10).hyperlink.target == "#'Diagnostics'!A8"
    assert not sheet.row_dimensions[sample.row].hidden
