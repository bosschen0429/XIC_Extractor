from openpyxl import Workbook

from xic_extractor.output.sheet_area_matrix import build_area_matrix_sheet
from xic_extractor.output.sheet_overview import build_overview_sheet
from xic_extractor.output.targeted_overview import (
    focus_worklist,
    rows_needing_look,
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


def _worklist_values(sheet):
    return [
        tuple(cell.value for cell in row)
        for row in sheet[sheet.tables["TargetedReview"].ref]
    ]


def test_area_matrix_keeps_saved_values_beside_the_paired_istd():
    sheet = Workbook().active
    build_area_matrix_sheet(sheet, _saved_rows())
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
    overview = summarize_targeted_results(rows, [], istd_targets=set())
    worklist = focus_worklist(overview, rows, [failed])
    sheet = Workbook().active
    build_overview_sheet(sheet, overview, worklist=worklist, provenance="v1")
    assert sheet["A1"].value == "先看 1 筆結果"
    [(_, sample, outcome, basis, doubts, _, _)] = _worklist_values(sheet)[1:]
    assert (sample, outcome, basis, doubts) == ("S2", "", "RAW 失敗", "")


def test_worklist_lists_only_results_that_differ_from_the_majority():
    labels = {
        ("S3", "Common"): ("無 MS2", ""),
        ("S1", "Standard"): ("NL 確認", "峰形不完整"),
    }
    rows = [
        row
        | dict(
            zip(
                ("依據", "疑點"),
                labels.get((row["SampleName"], row["Target"]), ("NL 確認", "")),
                strict=True,
            )
        )
        for row in _saved_rows()
    ]
    look = rows_needing_look(rows, {"Standard"})
    overview = summarize_targeted_results(rows, look, istd_targets={"Standard"})
    assert overview.needs_look_count == 2
    worklist = focus_worklist(overview, rows, [])
    sheet = Workbook().active
    build_overview_sheet(sheet, overview, worklist=worklist, provenance="v1")
    values = _worklist_values(sheet)
    assert values[0] == ("目標", "樣本", "結果", "依據", "疑點", "RT (min)", "Area")
    assert [row[:5] for row in values[1:]] == [("Common", "S3", "未計入", "無 MS2", "")]


def test_minority_counted_without_a_doubt_shows_its_identity_basis():
    rows = [
        r
        | {
            "Counted Detection": "TRUE" if r["SampleName"] == "S3" else "FALSE",
            "依據": "ISTD 位置" if r["SampleName"] == "S3" else "未見 NL",
            "疑點": "",
        }
        for r in _saved_rows()
        if r["Target"] == "Common"
    ]
    overview = summarize_targeted_results(rows, [], istd_targets=set())
    [record] = focus_worklist(overview, rows, [])
    assert (record["Sample"], record["Outcome"], record["依據"], record["疑點"]) == (
        "S3",
        "已計入",
        "ISTD 位置",
        "",
    )
    assert record["Area"] == "13.5"


def test_summary_counts_analytes_and_names_only_istds_missing_somewhere():
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
            "Counted Detection": "TRUE" if sample in hits else "FALSE",
        }
        for (target, _), hits in counted.items()
        for sample in samples
    ]
    istds = {target for target, role in counted if role == "ISTD"}
    overview = summarize_targeted_results(rows, [], istd_targets=istds)
    worklist = focus_worklist(overview, rows, [])
    sheet = Workbook().active
    build_overview_sheet(sheet, overview, worklist=worklist, provenance="v1")
    assert sheet["A1"].value == "先看 2 筆結果"
    assert sheet["A2"].value == "5 個樣本 · 4 個目標 · ISTD 計入 9/10（Gap 4/5） · v1"
    header = next(
        row
        for row in range(1, sheet.max_row + 1)
        if sheet.cell(row, 1).value == "分析物"
    )
    assert sheet.cell(header, 2).value == "計入樣本數"
    table = {
        sheet.cell(row, 1).value: sheet.cell(row, 2).value
        for row in range(header + 1, header + 3)
    }
    assert table == {"Rare": 1, "Steady": 5}
    assert not sheet.cell(header + 3, 1).value
    assert not sheet.merged_cells.ranges
    assert not sheet._charts
    assert not any(cell.hyperlink for row in sheet for cell in row)
