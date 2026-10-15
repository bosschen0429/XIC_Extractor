from openpyxl import Workbook

from xic_extractor.output.sheet_area_matrix import build_area_matrix_sheet
from xic_extractor.output.sheet_overview import build_overview_sheet
from xic_extractor.output.sheet_review import build_review_sheet
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
        "Action": "檢查",
        "RT": "9",
        "Area": "13.5",
    }


def _result_rows(rows):
    return {(r["SampleName"], r["Target"]): i for i, r in enumerate(rows, 2)}


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


def test_review_sheet_lists_only_results_that_differ_from_the_majority():
    rows = _saved_rows()
    reviews = [
        _review("S3", "Common", "沒有 MS2"),
        # A saved condition on a uniform target stays in XIC Results only.
        _review("S1", "Standard", "峰形需檢查"),
    ]
    overview = summarize_targeted_results(rows, reviews)
    worklist = focus_worklist(overview, rows, reviews, [])
    sheet = Workbook().active
    first_rows = build_review_sheet(sheet, worklist, result_rows=_result_rows(rows))
    values = list(sheet.values)
    assert values[0] == ("目標", "樣本", "結果", "原因", "建議檢查", "RT (min)", "Area")
    assert [row[:4] for row in values[1:]] == [("Common", "S3", "未計入", "沒有 MS2")]
    assert sheet["B2"].hyperlink.target == "#'XIC Results'!A7"
    assert first_rows == {"Common": 2}


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
    assert record["Action"] == "在 XIC 確認峰形成立，不是雜訊或鄰近峰"
    assert record["Area"] == "13.5"


def test_summary_names_each_focus_kind_and_links_to_the_worklist():
    samples = ("S1", "S2", "S3", "S4", "S5")
    counted = {"Rare": {"S3"}, "Split": {"S4", "S5"}, "Steady": set(samples)}
    rows = [
        {
            "SampleName": sample,
            "Target": target,
            "Role": "Analyte",
            "Counted Detection": "TRUE" if sample in hits else "FALSE",
        }
        for target, hits in counted.items()
        for sample in samples
    ]
    reviews = [_review("S1", "Split", "峰形需檢查")]
    overview = summarize_targeted_results(rows, reviews)
    sheet = Workbook().active
    build_overview_sheet(
        sheet,
        overview,
        pairs={},
        review_rows_by_target={"Rare": 2, "Split": 3},
    )
    assert sheet["A1"].value == "先看 2 筆與多數不同的結果"
    table = {
        sheet.cell(row, 1).value: (sheet.cell(row, 6).value, sheet.cell(row, 7).value)
        for row in range(5, 8)
    }
    # Without a clear majority the saved review notes are the reading order;
    # they are counted in 先看 and must not read as secondary.
    assert table == {
        "Rare": (1, "少數計入：確認是否抓錯峰"),
        "Split": (1, "沒有明顯多數：看檢閱註記"),
        "Steady": (None, ""),
    }
    assert sheet["F5"].hyperlink.target == "#'需檢查'!A2"
    assert not sheet.merged_cells.ranges
    assert not sheet._charts
