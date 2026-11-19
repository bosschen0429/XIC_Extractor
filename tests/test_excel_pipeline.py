import json
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import NLResult
from xic_extractor.extraction.result_models import (
    ExtractionResult,
    FileResult,
    RunOutput,
)
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.output.targeted_product_model import (
    TargetedProductProjection,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)


def test_workbook_filters_belong_to_tables_not_overlapping_worksheet_ranges(tmp_path):
    from xic_extractor.output.excel_pipeline import write_excel_from_run_output

    config = _config(tmp_path)
    path = write_excel_from_run_output(
        config,
        [_target("WithNL")],
        _run_output(with_diagnostics=True),
        output_path=config.output_csv.with_suffix(".xlsx"),
    )
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(path) as archive:
        assert (
            len([name for name in archive.namelist() if name.startswith("xl/tables/")])
            == 4
        )
        for name in archive.namelist():
            if name.startswith("xl/worksheets/sheet"):
                sheet = ElementTree.fromstring(archive.read(name))
                if sheet.find("s:tableParts", ns) is not None:
                    # Excel repairs/removes tables when a worksheet filter overlaps.
                    assert sheet.find("s:autoFilter", ns) is None, name
            elif name.startswith("xl/tables/"):
                table = ElementTree.fromstring(archive.read(name))
                table_filter = table.find("s:autoFilter", ns)
                assert table_filter is not None
                assert table_filter.attrib["ref"] == table.attrib["ref"]
    book = load_workbook(path)
    assert str(book["Run Metadata"].print_area) == (
        f"'Run Metadata'!$A$1:$C${book['Run Metadata'].max_row}"
    )
    book.close()


def test_workbook_retains_nonempty_decision_inputs_without_sidecars(tmp_path):
    from xic_extractor.output.excel_pipeline import write_excel_from_run_output

    context = tmp_path / "sample.csv"
    context.write_text(
        "sample_name,sample_role,injection_order\nSampleA,sample,2\nSampleB,qc,1\n",
        encoding="utf-8",
    )
    config = replace(
        _config(tmp_path),
        injection_order_source=context,
    )
    path = config.output_csv.with_suffix(".xlsx")
    write_excel_from_run_output(
        config,
        [_target("WithNL")],
        _run_output(with_diagnostics=False),
        output_path=path,
    )
    context.unlink()
    assert list(path.parent.iterdir()) == [path]
    wb = load_workbook(path)
    assert list(wb["Sample Context"].values) == [
        ("sample_name", "sample_role", "injection_order"),
        ("SampleA", "sample", "2"),
        ("SampleB", "qc", "1"),
    ]
    assert wb["Sample Context"].sheet_state == "hidden"
    assert wb["Targets"]["A2"].value == "WithNL"
    wb.close()


def test_write_excel_from_run_output_uses_in_memory_rows_and_metadata(
    tmp_path: Path,
) -> None:
    from xic_extractor.output.excel_pipeline import write_excel_from_run_output

    config = _config(tmp_path)
    target = _target("WithNL")
    output = _run_output(with_diagnostics=True)
    output_path = tmp_path / "output" / "xic_results.xlsx"

    result_path = write_excel_from_run_output(
        config,
        [target],
        output,
        output_path=output_path,
    )

    assert result_path == output_path
    assert output_path.exists()
    assert not config.output_csv.exists()
    assert not config.output_csv.with_name("xic_results_long.csv").exists()
    assert not config.diagnostics_csv.exists()

    wb = load_workbook(output_path)
    # One sample shares the batch outcome, so there is nothing to look at first.
    assert wb.sheetnames == [
        "摘要",
        "面積",
        "XIC Results",
        "Targets",
        "Run Metadata",
        "診斷事件",
    ]
    assert wb["診斷事件"].sheet_state == "hidden"
    assert wb.active.title == "摘要"
    ws = wb["XIC Results"]
    headers = [cell.value for cell in ws[1]]
    assert headers == [
        "SampleName",
        "Target",
        "RT",
        "Area",
        "IntSmoothed",
        "Counted Detection",
        "依據",
        "疑點",
        "PeakStart",
        "PeakEnd",
        "Group",
        "Int",
        "PeakWidth",
    ]
    record = dict(
        zip(
            headers,
            next(ws.iter_rows(min_row=2, max_row=2, values_only=True)),
            strict=True,
        )
    )
    assert record["SampleName"] == "SampleA"
    assert record["Target"] == "WithNL"
    assert record["RT"] == 9.0
    assert record["Area"] == 123.45
    assert record["IntSmoothed"] == 450.0
    assert record["Int"] == 500.0
    assert record["PeakWidth"] == 0.3
    assert (record["依據"], record["疑點"]) == ("NL 偏差大", "訊號缺口")
    assert not ws.merged_cells.ranges
    assert ws.freeze_panes == "C2"
    result_columns = {cell.value: cell.column_letter for cell in ws[1]}
    # Measurements, counted status and the labels show; evidence collapses.
    for header in headers[:8]:
        assert ws.column_dimensions[result_columns[header]].hidden is not True
    for header in headers[8:]:
        assert ws.column_dimensions[result_columns[header]].hidden is True

    ws_metadata = wb["Run Metadata"]
    metadata_keys = {
        ws_metadata.cell(row=row, column=1).value
        for row in range(2, ws_metadata.max_row + 1)
    }
    assert {
        "app_version",
        "generated_at",
        "targeted_strategy",
        "signal_processing_method",
        "signal_processing_window_points",
    } <= metadata_keys
    metadata_values = {
        row[0]: row[2]
        for row in ws_metadata.iter_rows(min_row=2, max_col=3, values_only=True)
    }
    strategy = json.loads(metadata_values["targeted_strategy"])
    assert (
        strategy["uncalibrated_paired_istd_position"]
        == "qualified_identity_and_coelution_rejection"
    )
    wb.close()
    from xic_inspector.api import SelectionRequest, prepare_report

    report = prepare_report(
        output_path, SelectionRequest(strategy="query", target_ids=("WithNL",))
    )
    saved = report.payload["items"][0]["details"]
    assert saved["records"][0]["Area"] == 123.45
    assert saved["records"][0]["疑點"] == "訊號缺口"


def test_workbook_keeps_an_event_without_a_result_on_the_worklist(tmp_path):
    from xic_extractor.output.excel_pipeline import write_excel_from_run_output

    config = _config(tmp_path)
    original = _run_output(with_diagnostics=False)
    # A RAW file that failed to open has no result row; it stays on the
    # worklist, and its message on the trace sheet, because no XIC view can
    # explain it.
    diagnostic = DiagnosticRecord(
        sample_name="Another sample",
        target_label="",
        issue="FILE_ERROR",
        reason="=Failed to open .raw: OSError: missing file",
    )
    output = RunOutput(file_results=original.file_results, diagnostics=[diagnostic])
    path = write_excel_from_run_output(
        config, [_target("WithNL")], output, output_path=tmp_path / "results.xlsx"
    )
    book = load_workbook(path)
    assert book.active.title == "摘要"
    summary = book["摘要"]
    rows = list(summary[summary.tables["TargetedReview"].ref])
    headers = [cell.value for cell in rows[0]]
    record = {h: cell for h, cell in zip(headers, rows[1], strict=True)}
    assert record["樣本"].value == "Another sample"
    assert record["結果"].value is None
    assert record["依據"].value == "RAW 失敗"
    # The full event also stays on the hidden trace sheet, as literal text.
    events = book["診斷事件"]
    assert events.sheet_state == "hidden"
    assert [cell.value for cell in events[1]] == [
        "SampleName",
        "Target",
        "Issue",
        "Reason",
    ]
    assert events["C2"].value == "FILE_ERROR"
    assert events["D2"].value == "'" + diagnostic.reason
    assert events["D2"].data_type != "f"
    book.close()


def test_workbook_shows_backfill_labels_without_changing_saved_counts(tmp_path):
    from xic_extractor.output.workbook_builder import write_workbook_from_rows
    from xic_inspector.sources.targeted_workbook import TargetedWorkbookSource

    rows = [
        {
            "SampleName": sample,
            "Target": "ISTD",
            "Role": "ISTD",
            "RT": rt,
            "Area": area,
            "PeakStart": "8.5",
            "PeakEnd": "9.5",
            "NL": "NL_FAIL",
            "Counted Detection": counted,
            "Product State": state,
            "依據": basis,
            "疑點": doubts,
        }
        for sample, rt, area, counted, state, basis, doubts in (
            (
                "Recovered",
                "9.1",
                "123.45",
                "TRUE",
                "detected_flagged",
                "回填",
                "未見 NL",
            ),
            ("Held", "9.2", "678.90", "FALSE", "not_counted", "未見 NL", ""),
        )
    ]
    diagnostics = [
        {"SampleName": sample, "Target": "ISTD", "Issue": issue, "Reason": reason}
        for sample, issue, reason in (
            ("Recovered", "NL_FAIL", "Original failed NL observation"),
            ("Recovered", "BACKFILL_ACCEPTED", "ordered_one_to_one_recovery_evidence"),
            ("Held", "NL_FAIL", "Original failed NL observation"),
            ("Held", "BACKFILL_HOLD", "no_unique_qualified_ms1_peak"),
        )
    ]
    path = write_workbook_from_rows(
        _config(tmp_path),
        [replace(_target("ISTD"), is_istd=True)],
        rows,
        diagnostics=diagnostics,
        output_path=tmp_path / "readable.xlsx",
    )
    book = load_workbook(path)
    summary = book["摘要"]
    values = [
        [cell.value for cell in row]
        for row in summary[summary.tables["TargetedReview"].ref]
    ]
    records = {row[1]: dict(zip(values[0], row, strict=True)) for row in values[1:]}
    # Backfill events stay on the trace sheet; the labels say what was seen.
    assert (records["Recovered"]["結果"], records["Recovered"]["疑點"]) == (
        "已計入",
        "未見 NL",
    )
    assert (records["Held"]["結果"], records["Held"]["依據"]) == ("未計入", "未見 NL")
    assert records["Held"]["Area"] == 678.90
    book.close()
    source = TargetedWorkbookSource(path)
    assert [
        (row["SampleName"], row["Area"], row["RT"], row["Counted Detection"])
        for row in source.records
    ] == [
        ("Recovered", 123.45, 9.1, "TRUE"),
        ("Held", 678.90, 9.2, "FALSE"),
    ]
    assert source.records[0]["依據"] == "回填"


def test_saved_workbook_overview_takes_roles_from_targets(tmp_path):
    from xic_extractor.output.workbook_builder import write_workbook_from_rows
    from xic_inspector.sources.targeted_workbook import TargetedWorkbookSource

    rows = [
        {
            "SampleName": sample,
            "Target": target,
            "Role": role,
            "RT": "9.0",
            "Area": "100",
            "Counted Detection": counted,
            "Product State": "detected_clean" if counted == "TRUE" else "not_counted",
        }
        for sample in ("S1", "S2")
        for target, role, counted in (
            ("A", "Analyte", "TRUE" if sample == "S1" else "FALSE"),
            ("IS", "ISTD", "TRUE"),
        )
    ]
    path = write_workbook_from_rows(
        _config(tmp_path),
        [replace(_target("A"), istd_pair="IS"), replace(_target("IS"), is_istd=True)],
        rows,
        diagnostics=[],
        output_path=tmp_path / "roles.xlsx",
    )
    overview = TargetedWorkbookSource(path).summary["overview"]
    # XIC Results carries no Role; the saved Targets sheet is its only source.
    assert {t["label"]: t["role"] for t in overview["targets"]} == {
        "A": "Analyte",
        "IS": "ISTD",
    }
    assert (overview["analyte_counted"], overview["analyte_total"]) == (1, 2)
    assert (overview["istd_counted"], overview["istd_total"]) == (2, 2)


def _config(
    tmp_path: Path,
) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_csv=tmp_path / "output" / "xic_results.csv",
        diagnostics_csv=tmp_path / "output" / "xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _target(label: str) -> Target:
    return Target(
        label=label,
        mz=258.1085,
        rt_min=8.0,
        rt_max=10.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=False,
        istd_pair="",
    )


def _run_output(*, with_diagnostics: bool) -> RunOutput:
    peak = PeakResult(
        rt=9.03,
        intensity=500.0,
        intensity_smoothed=450.0,
        area=123.45,
        peak_start=8.9,
        peak_end=9.2,
    )
    candidate = PeakCandidate(
        peak=peak,
        selection_apex_rt=9.0,
        selection_apex_intensity=450.0,
        selection_apex_index=10,
        raw_apex_rt=9.03,
        raw_apex_intensity=500.0,
        raw_apex_index=11,
        prominence=100.0,
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=peak,
        n_points=12,
        max_smoothed=450.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
        reason="concerns: local_sn (minor)",
        severities=((1, "local_sn"),),
        score_breakdown=(
            ("Base Score", "50"),
            ("Positive Points", "40"),
            ("Negative Points", "0"),
            ("Raw Score", "90"),
            ("Caps", ""),
            ("Support", "strict_nl_ok; local_sn_strong"),
            ("Concerns", ""),
        ),
    )
    result = ExtractionResult(
        peak_result=peak_result,
        nl=NLResult("WARN", 12.34, None, 3, 0, 2),
        target_label="WithNL",
        role="Analyte",
        istd_pair="",
        reason="concerns: local_sn (minor)",
        severities=((1, "local_sn"),),
        quality_penalty=1,
        quality_flags=("too_broad",),
        score_breakdown=peak_result.score_breakdown,
        targeted_product_projection=TargetedProductProjection(
            product_state="detected_flagged",
            counted_detection=True,
            review_state="flagged",
            projection_reason=(
                "decision: detected_flagged; review: targeted_acquired_signal_gaps"
            ),
            support_reasons=("ms1_peak_present",),
            review_reasons=("targeted_acquired_signal_gaps",),
        ),
    )
    diagnostics = []
    if with_diagnostics:
        diagnostics.append(
            DiagnosticRecord(
                sample_name="SampleA",
                target_label="WithNL",
                issue="NL_FAIL",
                reason="best match 80 ppm",
            )
        )
    return RunOutput(
        file_results=[FileResult(sample_name="SampleA", results={"WithNL": result})],
        diagnostics=diagnostics,
    )


@pytest.mark.parametrize(
    "review_reason, expected",
    [
        ("trace_morphology_review", "峰形不完整"),
        ("physical_shoulder_overlap", "肩峰／重疊"),
        ("targeted_competing_regions", "多個候選峰"),
    ],
)
def test_counted_backfill_doubt_names_quality_not_identity(
    review_reason: str,
    expected: str,
    tmp_path: Path,
) -> None:
    from xic_extractor.output.excel_pipeline import write_excel_from_run_output
    from xic_inspector.sources.targeted_workbook import TargetedWorkbookSource

    output = _run_output(with_diagnostics=False)
    file = output.file_results[0]
    result = file.results["WithNL"]
    file.results["WithNL"] = replace(
        result,
        targeted_product_projection=replace(
            result.targeted_product_projection,
            support_reasons=("cross_sample_backfill_identity",),
            review_reasons=(review_reason,),
        ),
    )
    config = _config(tmp_path)
    path = write_excel_from_run_output(
        config,
        [_target("WithNL")],
        output,
        output_path=tmp_path / "final-peak-review.xlsx",
    )
    saved = TargetedWorkbookSource(path).records[0]
    # The fixture's neutral loss was seen with a large mass error.
    assert (saved["依據"], saved["疑點"]) == ("回填", f"NL 偏差大、{expected}")
    assert saved["Counted Detection"] == "TRUE"
    assert saved["RT"] == result.reported_rt
    assert saved["Area"] == result.reported_peak_area
