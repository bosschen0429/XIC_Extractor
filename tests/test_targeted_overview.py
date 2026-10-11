from openpyxl import Workbook, load_workbook

from xic_extractor.output.sheet_overview import build_overview_sheet
from xic_extractor.output.targeted_overview import summarize_targeted_results


def _rows():
    return [
        {
            "SampleName": "A",
            "Target": "ISTD",
            "Counted Detection": "TRUE",
            "Area": "100",
            "RT": "9",
        },
        {
            "SampleName": "B",
            "Target": "ISTD",
            "Counted Detection": True,
            "Area": 0,
            "RT": 9.2,
        },
        {
            "SampleName": "A",
            "Target": "Analyte",
            "Counted Detection": "FALSE",
            "Area": "9000",
            "RT": "9.1",
        },
        {
            "SampleName": "B",
            "Target": "Analyte",
            "Counted Detection": "FALSE",
            "Area": "ND",
            "RT": "ND",
        },
    ]


def _reviews():
    return [
        {"Sample": "A", "Target": "ISTD", "Why": "Check evidence"},
        {"Sample": "A", "Target": "Analyte", "Why": "MS2 trigger missing"},
        {"Sample": "B", "Target": "Analyte", "Why": "Peak not found"},
    ]


def test_overview_summarizes_decisions_not_numeric_availability():
    overview = summarize_targeted_results(_rows(), _reviews())
    assert (overview.sample_count, overview.result_count) == (2, 4)
    assert (
        overview.counted_count,
        overview.review_count,
        overview.review_sample_count,
    ) == (2, 3, 2)
    assert [target.label for target in overview.targets] == ["Analyte", "ISTD"]
    analyte, istd = overview.targets
    assert (analyte.counted, analyte.review, analyte.median_area, analyte.rt_min) == (
        0,
        2,
        None,
        None,
    )
    assert (istd.counted, istd.review, istd.median_area, istd.rt_min, istd.rt_max) == (
        2,
        1,
        50,
        9,
        9.2,
    )


def test_overview_does_not_infer_missing_legacy_decisions():
    row = _rows()[0] | {"Counted Detection": None}
    overview = summarize_targeted_results([row], [])
    assert (overview.counted_count, overview.count_unknown) == (0, 1)
    assert overview.targets[0].median_area is None
    empty = summarize_targeted_results([], [])
    assert empty.result_count == empty.counted_count == empty.review_count == 0
    assert empty.targets == ()


def test_overview_workbook_charts_saved_counts_and_links_to_result_rows(
    tmp_path,
):
    book = Workbook()
    sheet = book.active
    sheet.title = "Overview"
    build_overview_sheet(sheet, _rows(), _reviews())
    path = tmp_path / "overview.xlsx"
    book.save(path)
    book.close()
    with_book = load_workbook(path)
    sheet = with_book["Overview"]
    assert sheet["A7"].value == "2 / 4"
    assert sheet["E7"].value == 2
    assert sheet["I7"].value == 3
    assert sheet["A29"].value == "Analyte"
    assert sheet["K29"].hyperlink.target == "#'XIC Results'!A4"
    assert len(sheet._charts) == 2
    left_chart, right_chart = sheet._charts
    assert left_chart.anchor.to.col == right_chart.anchor._from.col == 6
    assert left_chart.anchor.to.colOff < 0
    assert right_chart.anchor.to.col == 12
    assert all(
        not axis.delete and axis.tickLblPos == "nextTo"
        for chart in sheet._charts
        for axis in (chart.x_axis, chart.y_axis)
    )
    assert sheet.page_setup.fitToHeight == 1
    assert [sheet.cell(row, 15).value for row in (2, 3)] == [0, 2]
    assert [sheet.cell(row, 16).value for row in (2, 3)] == [2, 0]
    assert [sheet.cell(row, 17).value for row in (2, 3)] == [0, 0]
    assert any(dimension.hidden for dimension in sheet.column_dimensions.values())
    links = [cell.hyperlink.target for row in sheet for cell in row if cell.hyperlink]
    assert "#'Targets'!A1" in links and "#'Run Metadata'!A1" in links
    with_book.close()
