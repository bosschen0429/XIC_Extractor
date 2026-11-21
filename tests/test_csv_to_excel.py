import csv
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from scripts.csv_to_excel import run
from xic_extractor.configuration.execution_profile import in_process_execution
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import NLResult
from xic_extractor.extraction.result_models import ExtractionResult
from xic_extractor.output.detection import MissingTargetedProductProjectionError
from xic_extractor.output.messages import build_diagnostic_records
from xic_extractor.output.sheet_results import _build_data_sheet
from xic_extractor.output.workbook_styles import FOCUS, INK, MUTED
from xic_extractor.peak_detection.models import PeakDetectionResult, PeakResult

_COUNTED = {"counted_detection": "TRUE", "product_state": "detected_clean"}
_MISSED = {"counted_detection": "FALSE", "product_state": "not_counted"}


def test_data_sheet_preserves_measurements_and_marks_labels_to_look_at() -> None:
    rows = [
        _long_row(
            "Tumor_1", "Analyte", "9.1234", "12345.6", "OK", **_COUNTED, basis="NL 確認"
        ),
        _long_row(
            "Tumor_2",
            "Analyte",
            "9.2",
            "22345.6",
            "NO_MS2",
            **_COUNTED,
            basis="ISTD 位置",
            doubts="無 MS2、訊號缺口",
        ),
        _long_row(
            "Tumor_3", "Analyte", "ND", "ND", "", **_MISSED, basis="無可用 MS1 峰"
        ),
        _long_row(
            "Tumor_4", "Analyte", "9.3", "100", "", **_MISSED, basis="肩峰／重疊"
        ),
    ]
    wb = Workbook()
    ws = wb.active
    _build_data_sheet(ws, rows)
    records = _sheet_records(ws)
    assert [(r["SampleName"], r["RT"], r["Area"]) for r in records] == [
        ("Tumor_1", 9.1234, 12345.6),
        ("Tumor_2", 9.2, 22345.6),
        ("Tumor_3", "ND", "ND"),
        ("Tumor_4", 9.3, 100),
    ]
    assert [(r["依據"], r["疑點"] or "") for r in records] == [
        ("NL 確認", ""),
        ("ISTD 位置", "無 MS2、訊號缺口"),
        ("無可用 MS1 峰", ""),
        ("肩峰／重疊", ""),
    ]
    accent = [
        (ws[f"G{row}"].font.color.rgb[-6:], ws[f"H{row}"].font.color.rgb[-6:])
        for row in range(2, 6)
    ]
    assert accent == [(INK, INK), (INK, FOCUS), (MUTED, MUTED), (FOCUS, MUTED)]
    assert ws["C2"].number_format == "0.00"
    assert ws["D2"].number_format == "0.00E+00"
    assert ws["A2"].alignment.horizontal == "left"
    assert {ws[cell].alignment.horizontal for cell in ("C2", "D2")} == {"right"}
    assert records[0]["IntSmoothed"] == 900
    assert records[0]["Int"] == 1000
    assert records[0]["PeakWidth"] == 0.4
    assert ws["E2"].number_format == "0.00E+00"
    assert ws.freeze_panes == "C2"
    assert ws.tables["TargetedResults"].ref == "A1:M5"
    assert ws.auto_filter.ref is None
    wb.close()


def test_data_sheet_keeps_each_filtered_row_self_identifying() -> None:
    rows = [
        _long_row("Tumor_1", "A", "9.1", "100", "OK"),
        _long_row("Tumor_1", "B", "9.2", "200", "OK"),
    ]
    wb = Workbook()
    _build_data_sheet(wb.active, rows)
    records = _sheet_records(wb.active)
    assert not wb.active.merged_cells.ranges
    assert [(r["SampleName"], r["Target"], r["Group"]) for r in records] == [
        ("Tumor_1", "A", "Tumor"),
        ("Tumor_1", "B", "Tumor"),
    ]
    wb.close()


def test_data_sheet_shows_saved_labels_without_dumping_projection_codes() -> None:
    row = _long_row(
        "SampleA",
        "Analyte",
        "9.1",
        "123",
        "OK",
        counted_detection="FALSE",
        product_state="ambiguous",
        basis="證據衝突",
    )
    row.update(
        {
            "Projection Reason": "conflict: ambiguous_evidence",
            "Projection Conflict Reasons": "ambiguous_evidence",
        }
    )
    wb = Workbook()
    _build_data_sheet(wb.active, [row])
    record = _sheet_records(wb.active)[0]
    assert record["依據"] == "證據衝突"
    assert record["Area"] == 123
    assert record["Counted Detection"] == "FALSE"
    assert not any(header.startswith("Projection ") for header in record)
    assert {"Reason", "Review Reason", "NL Evidence"}.isdisjoint(record)
    wb.close()


def test_data_sheet_forces_sample_names_and_target_labels_to_literal_text() -> None:
    rows = [_long_row("+Sample", "=Bad", "9.1", "10000", "OK")]
    wb = Workbook()
    ws = wb.active

    _build_data_sheet(ws, rows)

    assert ws["A2"].value == "'+Sample"
    assert ws["A2"].data_type != "f"
    assert ws["B2"].value == "'=Bad"
    assert ws["B2"].data_type != "f"


def test_run_writes_one_review_table_and_method_context(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.output_csv.parent.mkdir(parents=True)
    rows = [
        _long_row(
            "Sample A",
            "Analyte",
            "9.1",
            "123",
            "OK",
            counted_detection="TRUE",
            product_state="detected_clean",
        ),
        _long_row(
            "Sample B",
            "Analyte",
            "9.2",
            "456",
            "NO_MS2",
            counted_detection="FALSE",
            product_state="not_counted",
            basis="無 MS2",
        ),
    ]
    rows[1].update(
        {
            "Projection Support Reasons": "ms1_peak_present;ms1_coherent",
            "Projection Not Counted Reasons": "missing_ms2_policy_not_counted",
        }
    )
    _write_csv(config.output_csv.with_name("xic_results_long.csv"), rows)
    _write_empty_diagnostics_csv(config.diagnostics_csv)
    wb = load_workbook(run(config, [_target("Analyte")]))
    assert wb.sheetnames == [
        "摘要",
        "面積",
        "XIC Results",
        "Targets",
        "Run Metadata",
    ]
    assert wb.active.title == "摘要"
    assert [ws.title for ws in wb.worksheets if ws.sheet_state == "visible"] == [
        "摘要",
        "面積",
        "XIC Results",
        "Targets",
    ]
    assert wb["Run Metadata"].sheet_state == "hidden"
    records = _sheet_records(wb["XIC Results"])
    assert [(r["SampleName"], r["Area"], r["Counted Detection"]) for r in records] == [
        ("Sample A", 123, "TRUE"),
        ("Sample B", 456, "FALSE"),
    ]
    assert records[1]["依據"] == "無 MS2"
    assert "targeted_workbook_layout_version" not in {
        row[0] for row in wb["Run Metadata"].values
    }
    wb.close()


def test_run_blocks_product_long_results_without_projection_fields(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    targets = [_target("Analyte")]
    config.output_csv.parent.mkdir(parents=True)
    _write_csv(
        config.output_csv.with_name("xic_results_long.csv"),
        [_long_row("Tumor_1", "Analyte", "9.1", "10000", "OK")],
    )
    _write_empty_diagnostics_csv(config.diagnostics_csv)

    with pytest.raises(MissingTargetedProductProjectionError):
        run(config, targets)


def test_run_blocks_inconsistent_product_projection_fields(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    targets = [_target("Analyte")]
    config.output_csv.parent.mkdir(parents=True)
    _write_csv(
        config.output_csv.with_name("xic_results_long.csv"),
        [
            _long_row(
                "Tumor_1",
                "Analyte",
                "9.1",
                "10000",
                "OK",
                counted_detection="TRUE",
                product_state="not_counted",
            )
        ],
    )
    _write_empty_diagnostics_csv(config.diagnostics_csv)

    with pytest.raises(MissingTargetedProductProjectionError):
        run(config, targets)


def test_run_blocks_legacy_wide_csv_without_product_projection(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    targets = [_target("Analyte")]
    config.output_csv.parent.mkdir(parents=True)
    _write_csv(
        config.output_csv,
        [
            {
                "SampleName": "Tumor_1",
                "Analyte_RT": "9.1",
                "Analyte_Int": "1000",
                "Analyte_Area": "10000",
                "Analyte_PeakStart": "8.9",
                "Analyte_PeakEnd": "9.3",
                "Analyte_PeakWidth": "0.4000",
                "Analyte_NL": "OK",
            }
        ],
    )
    _write_empty_diagnostics_csv(config.diagnostics_csv)

    with pytest.raises(MissingTargetedProductProjectionError):
        run(config, targets)


def test_csv_to_excel_retains_multi_peak_selection_and_candidate_count(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    target = _target("Analyte")
    result = ExtractionResult(
        peak_result=PeakDetectionResult(
            status="OK",
            peak=PeakResult(9.0, 200.0, 190.0, 123.0, 8.9, 9.1),
            n_points=401,
            max_smoothed=1000.0,
            n_prominent_peaks=2,
        ),
        nl=NLResult("OK", 1.0, 8.5, 2, 0, 2),
        reason="concerns: rt_prior (minor)",
    )
    records = build_diagnostic_records("S1", target, result, config)
    assert len(records) == 1
    assert "tallest" not in records[0].reason
    assert "NL-confirmed" not in records[0].reason
    assert "8.500" not in records[0].reason
    config.output_csv.parent.mkdir(parents=True)
    _write_csv(
        config.output_csv.with_name("xic_results_long.csv"),
        [
            _long_row(
                "S1",
                target.label,
                "9.0",
                "123.0",
                "OK",
                counted_detection="TRUE",
                product_state="detected_clean",
            )
        ],
    )
    _write_csv(
        config.diagnostics_csv,
        [
            {
                "SampleName": record.sample_name,
                "Target": record.target_label,
                "Issue": record.issue,
                "Reason": record.reason,
            }
            for record in records
        ],
    )

    workbook = load_workbook(run(config, [target]))

    assert (
        "2 qualified physical candidates in retained trace context" in records[0].reason
    )
    assert "selected RT 9.0000 min" in records[0].reason
    record = _sheet_records(workbook["XIC Results"])[0]
    assert record["疑點"] is None
    assert "需檢查" not in workbook.sheetnames
    assert workbook["診斷事件"].sheet_state == "hidden"
    assert any(
        row[2:4] == ("MULTI_PEAK", records[0].reason)
        for row in workbook["診斷事件"].values
    )
    assert record["RT"] == 9.0
    assert record["Area"] == 123.0
    workbook.close()


def _sheet_records(ws):
    rows = iter(ws.values)
    headers = next(rows)
    return [dict(zip(headers, row, strict=True)) for row in rows]


def _long_row(
    sample_name: str,
    target: str,
    rt: str,
    area: str,
    nl: str,
    *,
    role: str = "Analyte",
    istd_pair: str = "",
    counted_detection: str | None = None,
    product_state: str = "",
    basis: str = "",
    doubts: str = "",
) -> dict[str, str]:
    row = {
        "SampleName": sample_name,
        "Group": "Tumor" if sample_name.startswith("Tumor") else "QC",
        "Target": target,
        "Role": role,
        "ISTD Pair": istd_pair,
        "RT": rt,
        "Area": area,
        "NL": nl,
        "Int": "1000",
        "IntSmoothed": "900",
        "PeakStart": "8.9",
        "PeakEnd": "9.3",
        "PeakWidth": "0.4000",
        "依據": basis,
        "疑點": doubts,
    }
    if counted_detection is not None:
        row.update(
            {
                "Product State": product_state,
                "Counted Detection": counted_detection,
                "Review State": "none",
                "Projection Reason": product_state or "legacy_test_projection",
                "Projection Support Reasons": "",
                "Projection Review Reasons": "",
                "Projection Conflict Reasons": "",
                "Projection Not Counted Reasons": "",
                "Projection Exclusion Reasons": "",
                "Benchmark Eligibility State": "",
            }
        )
    return row


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_empty_diagnostics_csv(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["SampleName", "Target", "Issue", "Reason"]
        )
        writer.writeheader()


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
        count_no_ms2_as_detected=True,
        execution=in_process_execution(),
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
