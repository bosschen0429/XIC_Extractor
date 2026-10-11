from dataclasses import replace
from pathlib import Path

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


def test_workbook_retains_nonempty_decision_inputs_without_sidecars(tmp_path):
    from xic_extractor.output.excel_pipeline import write_excel_from_run_output

    context = tmp_path / "sample.csv"
    context.write_text(
        "sample_name,sample_role,injection_order\nSampleA,study_sample,2\nSampleB,qc,1\n",
        encoding="utf-8",
    )
    support = tmp_path / "support.tsv"
    support.write_text("sample\ttarget\nSampleA\t=unsafe\n", encoding="utf-8")
    config = replace(
        _config(tmp_path),
        injection_order_source=context,
        targeted_ms1_shape_identity_support_tsv=support,
    )
    path = config.output_csv.with_suffix(".xlsx")
    write_excel_from_run_output(
        config,
        [replace(_target("WithNL"), sample_applicability="rna_containing")],
        _run_output(with_diagnostics=False),
        output_path=path,
    )
    context.unlink()
    support.unlink()
    assert list(path.parent.iterdir()) == [path]
    wb = load_workbook(path)
    assert list(wb["Sample Context"].values) == [
        ("sample_name", "sample_role", "injection_order"),
        ("SampleA", "study_sample", "2"),
        ("SampleB", "qc", "1"),
    ]
    assert wb["Sample Context"].sheet_state == "hidden"
    assert wb["Identity Support"]["B2"].data_type != "f"
    assert wb["Identity Support"]["B2"].value.endswith("=unsafe")
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
    assert wb.sheetnames == ["XIC Results", "Targets", "Run Metadata"]
    assert wb.active.title == "XIC Results"
    ws = wb["XIC Results"]
    headers = [cell.value for cell in ws[1]]
    assert headers == [
        "SampleName",
        "Target",
        "Area",
        "RT",
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
    assert record["NL Evidence"] == "Observed with tolerance warning"
    assert record["NL"] == "⚠ 12.3ppm"
    assert record["Confidence"] == "LOW"
    assert record["Review"] == "Review"
    assert record["Review Reason"] == "NL support failed"
    assert not ws.merged_cells.ranges
    assert ws.freeze_panes == "C2"
    result_columns = {cell.value: cell.column_letter for cell in ws[1]}
    for header in headers[:7]:
        assert ws.column_dimensions[result_columns[header]].hidden is not True
    for header in headers[7:]:
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
        "signal_processing_method",
        "signal_processing_window_points",
    } <= metadata_keys
    wb.close()
    from xic_inspector.api import SelectionRequest, prepare_report

    report = prepare_report(output_path, SelectionRequest(strategy="attention"))
    saved = report.payload["items"][0]["details"]
    assert saved["records"][0]["Area"] == 123.45
    assert saved["review_queue"][0]["Why"] == "NL support failed"


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
