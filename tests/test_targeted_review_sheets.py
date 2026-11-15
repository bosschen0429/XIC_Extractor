from openpyxl import Workbook

from xic_extractor.output.sheet_area_matrix import build_area_matrix_sheet
from xic_extractor.output.sheet_overview import build_overview_sheet
from xic_extractor.output.targeted_overview import (
    focus_worklist,
    summarize_targeted_results,
)


def _saved_rows():
    return [
        {
            "SampleName": sample,
            "Target": target,
            "Role": role,
            "ISTD Pair": "Standard" if target == "Common" else "",
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
        for target, role in [("Standard", "ISTD"), ("Common", "Analyte")]
    ]


def _review(sample, target, why):
    return {
        "Sample": sample,
        "Target": target,
        "Outcome": "未計入",
        "Why": why,
        "RT": "9",
        "Area": "13.5",
    }


def _worklist_values(sheet):
    return [
        tuple(cell.value for cell in row)
        for row in sheet[sheet.tables["TargetedReview"].ref]
    ]


def test_area_matrix_keeps_saved_values_beside_the_paired_istd():
    sheet = Workbook().active
    build_area_matrix_sheet(sheet, _saved_rows())
    # The analyte leads and its ISTD follows, so a ratio is one formula away.
    assert [sheet.cell(4, column).value for column in (1, 2, 3)] == [
        "樣本",
        "Common",
        "Standard",
    ]
    assert [sheet.cell(row, 1).value for row in range(5, 10)] == [
        "S2",
        "S1",
        "S3",
        "S4",
        "S5",
    ]
    assert [sheet.cell(row, 2).value for row in range(5, 10)] == [
        0,
        "ND",
        13.5,
        "",
        500,
    ]
    assert [sheet.cell(row, 3).value for row in range(5, 10)] == [
        0,
        "ND",
        13.5,
        "",
        500,
    ]
    # Grey repeats the saved count decision; the value itself is kept.
    assert sheet["B7"].font.color.rgb != sheet["B5"].font.color.rgb
    assert sheet["C7"].font.color.rgb == sheet["B5"].font.color.rgb
    assert all(cell.data_type != "f" for row in sheet for cell in row)


def test_area_matrix_groups_analytes_that_share_an_istd():
    rows = [
        {"SampleName": "S1", "Target": target, "Role": role, "ISTD Pair": pair}
        for target, role, pair in [
            ("A", "Analyte", "IS"),
            ("Lone", "Analyte", ""),
            ("IS", "ISTD", ""),
            ("B", "Analyte", "IS"),
        ]
    ]
    sheet = Workbook().active
    build_area_matrix_sheet(sheet, rows)
    # Each analyte sharing the ISTD sits beside the others, then the ISTD.
    assert [sheet.cell(4, column).value for column in range(2, 6)] == [
        "A",
        "B",
        "IS",
        "Lone",
    ]


def test_summary_headline_counts_events_without_a_result():
    rows = [
        {"SampleName": "S1", "Target": "Common", "Counted Detection": "TRUE"},
    ]
    failed = {
        "SampleName": "S2",
        "Target": "Common",
        "Issue": "FILE_ERROR",
        "Reason": "RAW file could not be opened",
    }
    overview = summarize_targeted_results(rows, [])
    worklist = focus_worklist(overview, rows, [], [failed])
    sheet = Workbook().active
    build_overview_sheet(sheet, overview, worklist=worklist, provenance="v1")
    # The failed RAW file is on the worklist, so the headline cannot say "none".
    assert sheet["A1"].value == "先看 1 筆結果"
    [(_, sample, outcome, why, _, _)] = _worklist_values(sheet)[1:]
    # No XIC view can explain a RAW file that failed to open; its message stays.
    assert (sample, outcome) == ("S2", "未記錄判定")
    assert why == "RAW 處理失敗：RAW file could not be opened"


def test_worklist_lists_only_results_that_differ_from_the_majority():
    rows = _saved_rows()
    reviews = [
        _review("S3", "Common", "沒有 MS2"),
        # A saved condition on a uniform target stays in XIC Results only.
        _review("S1", "Standard", "峰形需檢查"),
    ]
    overview = summarize_targeted_results(rows, reviews)
    worklist = focus_worklist(overview, rows, reviews, [])
    sheet = Workbook().active
    build_overview_sheet(sheet, overview, worklist=worklist, provenance="v1")
    values = _worklist_values(sheet)
    assert values[0] == ("目標", "樣本", "結果", "原因", "RT (min)", "Area")
    assert [row[:4] for row in values[1:]] == [("Common", "S3", "未計入", "沒有 MS2")]


def test_minority_counted_without_saved_review_names_its_supporting_evidence():
    rows = [
        r
        | {
            "Counted Detection": "TRUE" if r["SampleName"] == "S3" else "FALSE",
            "Projection Support Reasons": "ms1_coherent;paired_istd_identity",
        }
        for r in _saved_rows()
        if r["Target"] == "Common"
    ]
    overview = summarize_targeted_results(rows, [])
    [record] = focus_worklist(overview, rows, [], [])
    assert record["Sample"] == "S3"
    assert record["Outcome"] == "已計入"
    # The reader tests the evidence the saved count rests on.
    assert record["Why"] == "多數樣本未計入；此樣本：MS1 峰形合格，位於配對 ISTD 位置"
    assert record["Area"] == "13.5"


def test_summary_lists_analytes_and_only_istds_missing_somewhere():
    samples = ("S1", "S2", "S3", "S4", "S5")
    counted = {
        ("Rare", "Analyte"): {"S3"},
        ("Steady", "Analyte"): set(samples),
        ("Whole", "ISTD"): set(samples),
        ("Gap", "ISTD"): {"S1", "S2", "S3", "S4"},
    }
    rows = [
        {
            "SampleName": sample,
            "Target": target,
            "Role": role,
            "Counted Detection": "TRUE" if sample in hits else "FALSE",
        }
        for (target, role), hits in counted.items()
        for sample in samples
    ]
    overview = summarize_targeted_results(rows, [])
    worklist = focus_worklist(overview, rows, [], [])
    sheet = Workbook().active
    build_overview_sheet(sheet, overview, worklist=worklist, provenance="v1")
    assert sheet["A1"].value == "先看 2 筆結果"
    assert sheet["A2"].value == "5 個樣本 · 4 個目標 · ISTD 計入 9/10 · v1"
    header = next(
        row
        for row in range(1, sheet.max_row + 1)
        if sheet.cell(row, 3).value == "計入樣本數"
    )
    table = {
        sheet.cell(row, 1).value: (sheet.cell(row, 2).value, sheet.cell(row, 3).value)
        for row in range(header + 1, header + 4)
    }
    # An ISTD counted in every sample is the batch fact on line two.
    assert table == {
        "Rare": ("Analyte", 1),
        "Gap": ("ISTD", 4),
        "Steady": ("Analyte", 5),
    }
    assert not sheet.cell(header + 4, 1).value
    assert not sheet.merged_cells.ranges
    assert not sheet._charts
    assert not any(cell.hyperlink for row in sheet for cell in row)
