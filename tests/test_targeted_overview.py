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


def test_overview_workbook_links_to_results_and_collapses_secondary_quantities(
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
    assert "先看 3 筆" in sheet["A4"].value
    assert sheet["A10"].value == "Analyte"
    assert sheet["E10"].hyperlink.target == "#'XIC Results'!A4"
    assert any(dimension.hidden for dimension in sheet.row_dimensions.values())
    links = [cell.hyperlink.target for row in sheet for cell in row if cell.hyperlink]
    assert "#'Targets'!A1" in links and "#'Run Metadata'!A1" in links
    with_book.close()
