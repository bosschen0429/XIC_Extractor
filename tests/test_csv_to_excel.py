import csv
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from scripts.csv_to_excel import run
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import NLResult
from xic_extractor.extraction.result_models import ExtractionResult
from xic_extractor.output.detection import MissingTargetedProductProjectionError
from xic_extractor.output.messages import build_diagnostic_records
from xic_extractor.output.review_queue_model import _review_queue_rows
from xic_extractor.output.sheet_field_guide import FIELD_GUIDE
from xic_extractor.output.sheet_results import _build_data_sheet
from xic_extractor.output.sheet_targets import _build_targets_sheet
from xic_extractor.output.workbook_inputs import _wide_to_long_rows
from xic_extractor.output.workbook_styles import _apply_sheet_role_styles
from xic_extractor.peak_detection.models import PeakDetectionResult, PeakResult


def test_data_sheet_preserves_measurements_and_compact_review_fields() -> None:
    rows = [
        _long_row("Tumor_1", "Analyte", "9.1234", "12345.6", "OK"),
        _long_row("Tumor_2", "Analyte", "9.2", "22345.6", "WARN_12.3ppm"),
        _long_row("Tumor_3", "Analyte", "ND", "ND", "ND"),
    ]
    wb = Workbook()
    ws = wb.active
    _build_data_sheet(ws, rows, review_rows=_review_queue_rows(rows, []))
    records = _sheet_records(ws)
    assert [(r["SampleName"], r["RT"], r["Area"]) for r in records] == [
        ("Tumor_1", 9.1234, 12345.6),
        ("Tumor_2", 9.2, 22345.6),
        ("Tumor_3", "ND", "ND"),
    ]
    assert records[0]["NL Evidence"] == "Observed"
    assert records[1]["NL"] == "⚠ 12.3ppm"
    assert records[1]["Review Reason"].replace("\n", "") == "中性丟失質量偏差偏大"
    # RT precedes Area; intensities read as scientific notation. Numbers align
    # right and text left so a column scans by magnitude.
    assert ws["C2"].number_format == "0.00"
    assert ws["D2"].number_format == "0.00E+00"
    assert ws["A2"].alignment.horizontal == "left"
    assert {ws[cell].alignment.horizontal for cell in ("C2", "D2")} == {"right"}
    assert records[0]["IntSmoothed"] == 900
    assert records[0]["Int"] == 1000
    assert records[0]["PeakWidth"] == 0.4
    assert ws["E2"].number_format == "0.00E+00"
    assert ws.freeze_panes == "C2"
    assert ws.tables["TargetedResults"].ref == "A1:S4"
    assert ws.auto_filter.ref is None
    wb.close()


def test_data_sheet_keeps_each_filtered_row_self_identifying() -> None:
    rows = [
        _long_row("Tumor_1", "A", "9.1", "100", "OK"),
        _long_row("Tumor_1", "B", "9.2", "200", "OK"),
    ]
    wb = Workbook()
    _build_data_sheet(wb.active, rows, review_rows=[])
    records = _sheet_records(wb.active)
    assert not wb.active.merged_cells.ranges
    assert [(r["SampleName"], r["Target"], r["Group"]) for r in records] == [
        ("Tumor_1", "A", "Tumor"),
        ("Tumor_1", "B", "Tumor"),
    ]
    wb.close()


def test_data_sheet_preserves_reason_without_dumping_projection_codes() -> None:
    row = _long_row(
        "SampleA",
        "Analyte",
        "9.1",
        "123",
        "OK",
        counted_detection="FALSE",
        product_state="ambiguous",
    )
    row.update(
        {
            "Reason": "A measured peak needs review.",
            "Projection Reason": "conflict: targeted_rt_conflict",
            "Projection Conflict Reasons": "targeted_rt_conflict",
        }
    )
    wb = Workbook()
    _build_data_sheet(wb.active, [row], review_rows=_review_queue_rows([row], []))
    record = _sheet_records(wb.active)[0]
    assert record["Reason"] == row["Reason"]
    assert record["Area"] == 123
    assert record["Counted Detection"] == "FALSE"
    assert record["Review"] == "Review"
    assert record["Review Reason"].replace("\n", "") == "證據衝突，身分未定"
    assert not any(header.startswith("Projection ") for header in record)
    assert "Legacy Authority Status" not in record
    wb.close()


def test_data_sheet_forces_sample_names_and_target_labels_to_literal_text() -> None:
    rows = [_long_row("+Sample", "=Bad", "9.1", "10000", "OK")]
    wb = Workbook()
    ws = wb.active

    _build_data_sheet(ws, rows, review_rows=_review_queue_rows(rows, []))

    assert ws["A2"].value == "'+Sample"
    assert ws["A2"].data_type != "f"
    assert ws["B2"].value == "'=Bad"
    assert ws["B2"].data_type != "f"


def test_review_queue_aggregates_diagnostics_by_sample_target() -> None:
    rows = [
        _long_row(
            "Tumor_1",
            "Analyte",
            "9.0",
            "10000",
            "NL_FAIL",
            confidence="LOW",
            reason="concerns: nl_support (major); local_sn (minor)",
        ),
        _long_row(
            "Tumor_1",
            "Analyte",
            "9.1",
            "11000",
            "OK",
            confidence="MEDIUM",
            reason="all checks passed",
        ),
    ]
    diagnostics = [
        {
            "SampleName": "Tumor_1",
            "Target": "Analyte",
            "Issue": "NL_FAIL",
            "Reason": "strict observed neutral loss missing",
        },
        {
            "SampleName": "Tumor_1",
            "Target": "Analyte",
            "Issue": "ANCHOR_MISMATCH",
            "Reason": "selected RT is far from anchor",
        },
    ]

    review_rows = _review_queue_rows(rows, diagnostics)

    assert len(review_rows) == 1
    assert review_rows[0]["Priority"] == "1"
    assert review_rows[0]["Sample"] == "Tumor_1"
    assert review_rows[0]["Target"] == "Analyte"
    assert review_rows[0]["Status"] == "Review"
    assert review_rows[0]["Why"] == "MS2 未見預期的中性丟失"
    assert review_rows[0]["Action"] == "看所選 RT 附近的 MS2 是否有預期的中性丟失"
    assert review_rows[0]["Issue Count"] == "2"
    assert review_rows[0]["Evidence"] == "NL_FAIL; ANCHOR_MISMATCH"


def test_review_queue_does_not_reopen_confirmed_results_from_diagnostic_history():
    row = _long_row(
        "Recovered",
        "ISTD",
        "9.1",
        "123",
        "NL_FAIL",
        role="ISTD",
        confidence="LOW",
        counted_detection="TRUE",
        product_state="detected_clean",
    ) | {
        "Review State": "none",
        "Projection Support Reasons": "cross_sample_backfill_identity",
    }
    diagnostics = [
        {"SampleName": "Recovered", "Target": "ISTD", "Issue": issue, "Reason": issue}
        for issue in (
            "NL_FAIL",
            "MULTI_PEAK",
            "ANCHOR_RT_MISMATCH",
            "BACKFILL_ACCEPTED",
        )
    ]
    assert _review_queue_rows([row], diagnostics) == []


def test_review_queue_product_mode_does_not_use_legacy_confidence_as_issue() -> None:
    rows = [
        _long_row(
            "Tumor_1",
            "Analyte",
            "9.3",
            "40000",
            "OK",
            confidence="VERY_LOW",
            reason="decision: detected_clean; support: ms1_peak_present",
            counted_detection="TRUE",
            product_state="detected_clean",
        )
    ]

    review_rows = _review_queue_rows(rows, [])

    assert review_rows == []


def test_review_queue_does_not_turn_ordinary_nd_into_attention() -> None:
    row = _long_row(
        "Absent",
        "Analyte",
        "ND",
        "ND",
        "NO_MS2",
        counted_detection="FALSE",
        product_state="not_counted",
    ) | {"Projection Not Counted Reasons": "missing_positive_ms1_peak"}
    diagnostics = [{"SampleName": "Absent", "Target": "Analyte", "Issue": "NO_SIGNAL"}]
    assert _review_queue_rows([row], diagnostics) == []
    # A missing spiked internal standard is actionable, unlike an absent analyte.
    istd = row | {"Role": "ISTD"}
    assert _review_queue_rows([istd], diagnostics)[0]["Why"] == "內標未檢出"
    failed = row | {"Projection Exclusion Reasons": "file_error"}
    assert _review_queue_rows([failed], diagnostics)[0]["Why"] == "RAW 處理失敗"
    # Missing signal must not conceal an independent unresolved conflict.
    row["Projection Conflict Reasons"] = "identity_conflict"
    assert _review_queue_rows([row], diagnostics)[0]["Status"] == "Review"


def test_targets_sheet_marks_expected_product_as_nominal_reference() -> None:
    wb = Workbook()
    ws = wb.active

    _build_targets_sheet(ws, [_target("Analyte")])

    assert ws["I1"].value == "Expected product m/z"
    meaning = {field: text for _, field, text, _ in FIELD_GUIDE}
    assert "設定參考值，不是實測產物" in meaning["Expected product m/z"]


def test_wide_to_long_rows_classifies_qc_from_sample_name_token() -> None:
    rows = [
        {
            "SampleName": "Breast Cancer Tissue_pooled_QC_1",
            "Analyte_RT": "9.0",
            "Analyte_Area": "10000",
            "Analyte_NL": "OK",
        },
        {
            "SampleName": "SampleA",
            "Analyte_RT": "9.0",
            "Analyte_Area": "10000",
            "Analyte_NL": "OK",
        },
        {
            "SampleName": "Breast_Cancer_Tissue_pooled_QC1",
            "Analyte_RT": "9.0",
            "Analyte_Area": "10000",
            "Analyte_NL": "OK",
        },
    ]

    long_rows = _wide_to_long_rows(rows, [_target("Analyte")])

    assert long_rows[0]["Group"] == "QC"
    assert long_rows[1]["Group"] == "Other"
    assert long_rows[2]["Group"] == "QC"
    assert long_rows[0]["Confidence"] == ""
    assert long_rows[0]["Reason"] == "legacy_wide_csv_no_product_projection"
    assert long_rows[0]["Legacy Authority Status"] == "legacy_projection_only"


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
        "需檢查",
        "面積",
        "XIC Results",
        "Targets",
        "Run Metadata",
        "欄位說明",
    ]
    assert wb.active.title == "摘要"
    records = _sheet_records(wb["XIC Results"])
    assert [(r["SampleName"], r["Area"], r["Counted Detection"]) for r in records] == [
        ("Sample A", 123, "TRUE"),
        ("Sample B", 456, "FALSE"),
    ]
    assert records[1]["Review Reason"] == "未設定配對 ISTD，無法確認身分"
    assert {row[0]: row[2] for row in wb["Run Metadata"].values}[
        "targeted_workbook_layout_version"
    ] == 2
    wb.close()


@pytest.mark.parametrize(
    ("qualification", "istd_pair", "why", "action"),
    [
        # The MS1 shape failed, so the paired ISTD could not lend identity.
        ("", "", "MS1 峰形未通過：肩峰／重疊", "在 XIC 看峰形為什麼未通過"),
        # A qualified MS1 peak lacks only an identity source.
        (
            "ms1_coherent",
            "",
            "未設定配對 ISTD，無法確認身分",
            "在 XIC 判斷這個峰是否就是目標",
        ),
        (
            "ms1_coherent",
            "d3-8-oxodG",
            "配對 ISTD 在此樣本未計入，無法確認身分",
            "先看配對 ISTD 在此樣本的結果",
        ),
        (
            "cross_sample_backfill_localization",
            "",
            "未設定配對 ISTD，無法確認身分",
            "在 XIC 判斷這個峰是否就是目標",
        ),
    ],
)
def test_review_names_the_decisive_ms1_evidence_not_missing_ms2(
    qualification, istd_pair, why, action
):
    row = _long_row(
        "TumorBC2312_DNA",
        "8-oxodG",
        "16.4936",
        "513298",
        "NO_MS2",
        istd_pair=istd_pair,
        counted_detection="FALSE",
        product_state="not_counted",
    )
    row.update(
        {
            "Projection Support Reasons": (
                "ms1_peak_present;paired_istd_anchor_support;trace_coherent"
            )
            + (f";{qualification}" if qualification else ""),
            "Projection Review Reasons": (
                "missing_ms2_not_observed;physical_shoulder_overlap;"
                "trace_morphology_review"
            ),
            "Projection Not Counted Reasons": "missing_ms2_policy_not_counted",
        }
    )
    queue = _review_queue_rows([row], [])
    wb = Workbook()
    _build_data_sheet(wb.active, [row], review_rows=queue)
    saved = _sheet_records(wb.active)[0]
    assert saved["RT"] == 16.4936
    assert saved["Area"] == 513298
    assert saved["Counted Detection"] == "FALSE"
    assert saved["Review Reason"] == why
    assert queue[0]["Action"] == action
    # Under CID a missing neutral loss is a given fact, not the thing to check.
    assert "MS2" not in why + action
    wb.close()


@pytest.mark.parametrize(
    ("review_reason", "action"),
    [
        ("physical_shoulder_overlap", "在 XIC 看肩峰是否影響積分範圍"),
        ("trace_morphology_review", "在 XIC 看峰形是否完整"),
        ("targeted_rt_review", "在 XIC 比對 RT 與配對 ISTD"),
    ],
)
def test_counted_review_points_at_the_concern_that_could_overturn_it(
    review_reason, action
):
    row = _long_row(
        "Sample A",
        "Analyte",
        "9.1",
        "123",
        "NO_MS2",
        counted_detection="TRUE",
        product_state="detected_flagged",
    )
    row.update(
        {
            "Review State": "flagged",
            "Projection Support Reasons": "ms1_coherent;paired_istd_identity",
            "Projection Review Reasons": review_reason,
        }
    )
    [review] = _review_queue_rows([row], [])
    assert review["Action"] == action
    assert "MS2" not in review["Why"] + review["Action"]


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


def test_workbook_sheet_tabs_separate_results_from_method_context(
    tmp_path: Path,
) -> None:
    wb = Workbook()
    wb.active.title = "XIC Results"
    wb.create_sheet("Targets")
    wb.create_sheet("Run Metadata")
    _apply_sheet_role_styles(wb)
    result_color = wb["XIC Results"].sheet_properties.tabColor.rgb
    assert result_color
    for title in ("Targets", "Run Metadata"):
        assert wb[title].sheet_properties.tabColor.rgb != result_color
    wb.close()


def test_review_queue_evidence_uses_short_tags() -> None:
    rows = [
        _long_row(
            "BenignfatBC1055_DNA",
            "8-oxodG",
            "17.177",
            "1850221.22",
            "NL_FAIL",
            confidence="LOW",
            reason="concerns: rt_prior (major); weak candidate: too_broad",
        )
    ]
    diagnostics = [
        {
            "SampleName": "BenignfatBC1055_DNA",
            "Target": "8-oxodG",
            "Issue": "NL_FAIL",
            "Reason": (
                "selected candidate has 2 candidate-aligned MS2 trigger scans; "
                "strict observed neutral loss 116.047 Da not detected in any "
                "aligned scan; "
                "alignment=region"
            ),
        },
        {
            "SampleName": "BenignfatBC1055_DNA",
            "Target": "8-oxodG",
            "Issue": "ANCHOR_RT_MISMATCH",
            "Reason": (
                "Paired analyte peak RT 17.177 min deviates 0.71 min from "
                "ISTD anchor at 16.470 min (allowed +/-0.50 min)"
            ),
        },
        {
            "SampleName": "BenignfatBC1055_DNA",
            "Target": "8-oxodG",
            "Issue": "MULTI_PEAK",
            "Reason": (
                "4 qualified physical candidates in selection window; "
                "selected RT 17.1770 min"
            ),
        },
    ]

    queue = _review_queue_rows(rows, diagnostics)

    assert queue[0]["Evidence"] == "NL_FAIL; anchor dRT=0.71 min; multi_peak=4"
    assert "selected candidate has" not in queue[0]["Evidence"]
    assert len(queue[0]["Evidence"]) < 80


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
        # Window evidence belongs to the higher neighbor, not the selected peak.
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
    assert record["Review"] is None
    assert record["Review Evidence"] is None
    # An informational event is not a worklist entry; its full text stays on
    # the hidden trace sheet so a past selection can still be explained.
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
    confidence: str = "HIGH",
    reason: str = "all checks passed",
    counted_detection: str | None = None,
    product_state: str = "",
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
        "NL Evidence": {
            "OK": "Observed",
            "WARN": "Observed with tolerance warning",
            "NL_FAIL": "Target-peak CID-NL not supported",
            "NO_MS2": "No target-matched MS2 in peak",
            "ERROR": "Processing error",
        }.get(nl.split("_")[0] if nl.startswith("WARN_") else nl, "Unavailable"),
        "Int": "1000",
        "IntSmoothed": "900",
        "PeakStart": "8.9",
        "PeakEnd": "9.3",
        "PeakWidth": "0.4000",
        "Confidence": confidence,
        "Reason": reason,
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
                "Legacy Authority Status": "evidence_only",
                "Benchmark Eligibility State": "",
            }
        )
    return row


def _wide_row(sample_name: str, targets: list[Target]) -> dict[str, str]:
    row = {"SampleName": sample_name}
    for target in targets:
        row.update(
            {
                f"{target.label}_RT": "9.1",
                f"{target.label}_Int": "1000",
                f"{target.label}_Area": "10000",
                f"{target.label}_PeakStart": "8.9",
                f"{target.label}_PeakEnd": "9.3",
                f"{target.label}_PeakWidth": "0.4000",
                f"{target.label}_NL": "OK",
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
