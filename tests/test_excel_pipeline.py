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
        "sample_name,sample_role,injection_order\nSampleA,study_sample,2\nSampleB,qc,1\n",
        encoding="utf-8",
    )
    config = replace(
        _config(tmp_path),
        injection_order_source=context,
    )
    path = config.output_csv.with_suffix(".xlsx")
    write_excel_from_run_output(
        config,
        [replace(_target("WithNL"), sample_applicability="rna_containing")],
        _run_output(with_diagnostics=False),
        output_path=path,
    )
    context.unlink()
    assert list(path.parent.iterdir()) == [path]
    wb = load_workbook(path)
    assert list(wb["Sample Context"].values) == [
        ("sample_name", "sample_role", "injection_order"),
        ("SampleA", "study_sample", "2"),
        ("SampleB", "qc", "1"),
    ]
    assert wb["Sample Context"].sheet_state == "hidden"
    assert wb["Targets"]["L1"].value == "Sample applicability"
    assert wb["Targets"]["L2"].value == "rna_containing"
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
        "欄位說明",
    ]
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
        "Review",
        "Review Reason",
        "PeakStart",
        "PeakEnd",
        "NL Evidence",
        "Reason",
        "Review Evidence",
        "Role",
        "Group",
        "NL",
        "Confidence",
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
    assert record["NL Evidence"] == "Observed with tolerance warning"
    assert record["NL"] == "⚠ 12.3ppm"
    assert record["Confidence"] == "LOW"
    assert record["Review"] == "Check"
    assert record["Review Reason"].replace("\n", "") == "證據需檢查"
    assert not ws.merged_cells.ranges
    assert ws.freeze_panes == "C2"
    result_columns = {cell.value: cell.column_letter for cell in ws[1]}
    # Measurements, counted status and review reasons show; evidence collapses.
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
        "config_hash",
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
    assert saved["review_queue"][0]["Why"].replace("\n", "") == "證據需檢查"


def test_workbook_keeps_an_event_without_a_result_on_the_worklist(tmp_path):
    from xic_extractor.output.excel_pipeline import write_excel_from_run_output

    config = _config(tmp_path)
    original = _run_output(with_diagnostics=False)
    # A RAW file that failed to open has no result row; its message must stay
    # readable because no XIC view can explain it.
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
    rows = list(book["需檢查"].iter_rows(values_only=False))
    headers = [cell.value for cell in rows[0]]
    record = {h: cell for h, cell in zip(headers, rows[1], strict=True)}
    assert record["樣本"].value == "Another sample"
    assert record["結果"].value == "未記錄判定"
    assert record["原因"].value == "RAW 處理失敗"
    assert record["建議檢查"].value.endswith(diagnostic.reason)
    assert record["建議檢查"].data_type != "f"
    book.close()


def test_workbook_explains_backfill_without_changing_saved_counts(tmp_path):
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
            "NL Evidence": "Target-peak CID-NL not supported",
            "Confidence": "LOW",
            "Counted Detection": counted,
            "Product State": state,
            "Projection Support Reasons": support,
        }
        for sample, rt, area, counted, state, support in (
            (
                "Recovered",
                "9.1",
                "123.45",
                "TRUE",
                "detected_flagged",
                "cross_sample_backfill_identity",
            ),
            ("Held", "9.2", "678.90", "FALSE", "not_counted", ""),
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
    values = list(book["需檢查"].values)
    records = {row[1]: dict(zip(values[0], row, strict=True)) for row in values[1:]}
    assert "Recovered" not in records
    assert records["Held"]["結果"] == "未計入"
    assert "找不到" in records["Held"]["原因"]
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
    assert source.records[0]["Review Reason"] is None


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
        config_hash="abc12345",
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
        confidence="LOW",
        reason="concerns: local_sn (minor)",
        severities=((1, "local_sn"),),
        score_breakdown=(
            ("Base Score", "50"),
            ("Positive Points", "40"),
            ("Negative Points", "0"),
            ("Raw Score", "90"),
            ("Caps", ""),
            ("Final Confidence", "HIGH"),
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
        confidence="LOW",
        reason="concerns: local_sn (minor)",
        severities=((1, "local_sn"),),
        quality_penalty=1,
        quality_flags=("too_broad",),
        score_breakdown=peak_result.score_breakdown,
        targeted_product_projection=TargetedProductProjection(
            product_state="detected_flagged",
            counted_detection=True,
            review_state="flagged",
            projection_reason="decision: detected_flagged; review: local_sn_minor",
            support_reasons=("ms1_peak_present",),
            review_reasons=("local_sn_minor",),
            legacy_evidence={"confidence": "LOW"},
            legacy_authority_status="evidence_only",
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
        ("trace_morphology_review", "峰形需檢查"),
        ("physical_shoulder_overlap", "肩峰重疊需檢查"),
        ("sample_review", "證據需檢查"),
    ],
)
def test_counted_backfill_review_names_quality_not_identity(
    review_reason: str,
    expected: str,
    tmp_path: Path,
) -> None:
    from xic_extractor.output.review_queue_model import _review_why

    row = {
        "Product State": "detected_flagged",
        "Counted Detection": "TRUE",
        "Projection Support Reasons": "cross_sample_backfill_identity",
        "Projection Review Reasons": review_reason,
    }
    assert _review_why("PROJECTION_FLAGGED", row, "BACKFILL_ACCEPTED") == expected

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
    assert saved["Review"] == "Check"
    assert saved["Review Reason"] == expected
    assert saved["Counted Detection"] == "TRUE"
    assert saved["RT"] == result.reported_rt
    assert saved["Area"] == result.reported_peak_area
