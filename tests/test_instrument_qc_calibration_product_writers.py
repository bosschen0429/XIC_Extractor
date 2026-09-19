import csv
import json
from pathlib import Path
from typing import Any, Callable

from xic_extractor.instrument_qc.calibration_product_models import (
    ARTIFACT_SCHEMA_VERSION,
    ArtifactInventoryItem,
    CalibrationBundleManifest,
    CalibrationEvidenceRow,
    CalibrationEvidenceSummary,
    CorrectionStatus,
    CoverageStatus,
    MatrixResponsePreviewRow,
    MatrixRTPreviewRow,
    ProductSupportStatus,
    ResponseTransferStatus,
    RtDriftModelRow,
    RtLeaveOneAnchorOutRow,
)
from xic_extractor.instrument_qc.calibration_product_writers import (
    CALIBRATION_EVIDENCE_COLUMNS,
    MATRIX_RESPONSE_PREVIEW_COLUMNS,
    MATRIX_RT_PREVIEW_COLUMNS,
    RT_DRIFT_MODEL_COLUMNS,
    RT_LEAVE_ONE_ANCHOR_OUT_COLUMNS,
    write_calibration_evidence_summary_json,
    write_calibration_evidence_tsv,
    write_calibration_manifest_json,
    write_matrix_response_preview_tsv,
    write_matrix_rt_preview_tsv,
    write_rt_drift_model_tsv,
    write_rt_leave_one_anchor_out_tsv,
)

Writer = Callable[[Path, Any], None]


def _evidence_row() -> CalibrationEvidenceRow:
    return CalibrationEvidenceRow(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        bundle_id="bundle-001",
        evidence_row_id="evidence-001",
        source_artifact_id="instrument_qc_sdolek_trend.tsv",
        source_artifact_hash="hash",
        source_type="sdolek",
        matrix_context="clean",
        sample_name="SDOLEK_1",
        raw_stem="SDOLEK_1",
        source_raw_file="SDOLEK_1.raw",
        raw_path_kind="basename",
        injection_order=1,
        compound="SDO",
        compound_group="SDO",
        precursor_mz=311.0814,
        observed_mz=None,
        mz_ppm_error=None,
        reference_rt_min=6.26,
        observed_rt_min=6.27,
        rt_delta_min=0.01,
        rt_region="rt_06_07",
        area=1000.0,
        height=None,
        log2_area_delta=None,
        log2_height_delta=None,
        peak_width_min=0.83,
        activation_method="wHCD",
        product_support_status=ProductSupportStatus.SUPPORTED,
        neutral_loss_support_status=ProductSupportStatus.NOT_APPLICABLE,
        evidence_confidence="high",
        calibration_eligible=True,
        coverage_status=CoverageStatus.COVERED,
        exclusion_reason="",
    )


def test_write_calibration_manifest_json_round_trips_paths(tmp_path: Path) -> None:
    path = tmp_path / "instrument_qc_calibration_manifest.json"
    manifest = CalibrationBundleManifest(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        bundle_id="bundle-001",
        run_id="run-001",
        product_maturity_level="level_0",
        overall_verdict="diagnostic_only",
        artifact_inventory=(
            ArtifactInventoryItem(
                artifact_id="evidence",
                path=Path("instrument_qc_calibration_evidence.tsv"),
                role="row_contract",
                required=True,
                schema_version=ARTIFACT_SCHEMA_VERSION,
                status="present",
            ),
        ),
        source_artifacts={"instrument_qc_sdolek_trend.tsv": "abc"},
        source_contracts={"instrument_qc": "sdolek_trend"},
        generation_command="cmd",
        created_at_utc="2026-05-21T00:00:00Z",
        created_by="tool",
        status_counts={"evidence_rows": {"total": 1}},
        first_human_file="",
        first_machine_file="instrument_qc_calibration_evidence.tsv",
    )

    write_calibration_manifest_json(path, manifest)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["artifact_inventory"][0]["path"] == (
        "instrument_qc_calibration_evidence.tsv"
    )
    assert payload["overall_verdict"] == "diagnostic_only"


def test_write_calibration_evidence_tsv_uses_stable_columns(tmp_path: Path) -> None:
    path = tmp_path / "instrument_qc_calibration_evidence.tsv"
    write_calibration_evidence_tsv(path, [_evidence_row()])

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)

    assert reader.fieldnames == CALIBRATION_EVIDENCE_COLUMNS
    assert rows[0]["product_support_status"] == "supported"
    assert rows[0]["calibration_eligible"] == "true"


def test_calibration_product_tsv_writers_preserve_fixed_schemas_and_values(
    tmp_path: Path,
) -> None:
    cases: list[tuple[str, list[str], Writer, Any, dict[str, str]]] = [
        (
            "matrix_rt_preview.tsv",
            MATRIX_RT_PREVIEW_COLUMNS,
            write_matrix_rt_preview_tsv,
            [_matrix_rt_preview_row()],
            {
                "correction_status": "shadow_only",
                "predicted_rt_delta_min": "0.05",
                "rt_if_standard_corrected_min": "11.95",
            },
        ),
        (
            "rt_drift_model.tsv",
            RT_DRIFT_MODEL_COLUMNS,
            write_rt_drift_model_tsv,
            [_rt_drift_model_row()],
            {
                "anchor_ids": "anchor-1;anchor-2",
                "coverage_status": "covered",
                "predicted_rt_delta_min": "0.05",
            },
        ),
        (
            "rt_leave_one_anchor_out.tsv",
            RT_LEAVE_ONE_ANCHOR_OUT_COLUMNS,
            write_rt_leave_one_anchor_out_tsv,
            [_rt_leave_one_anchor_out_row()],
            {
                "prediction_error_min": "-0.01",
                "abs_prediction_error_min": "0.01",
                "status": "PASS",
            },
        ),
        (
            "matrix_response_preview.tsv",
            MATRIX_RESPONSE_PREVIEW_COLUMNS,
            write_matrix_response_preview_tsv,
            [_matrix_response_preview_row()],
            {
                "transfer_status": "clean_only",
                "raw_area": "",
                "correction_status": "blocked_missing_value",
            },
        ),
    ]

    for filename, columns, writer, rows, expected in cases:
        path = tmp_path / "nested" / filename

        writer(path, rows)

        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            assert reader.fieldnames == columns
            written_rows = list(reader)
        assert len(written_rows) == 1
        for column, value in expected.items():
            assert written_rows[0][column] == value


def test_calibration_product_tsv_writers_write_header_without_rows(
    tmp_path: Path,
) -> None:
    cases: list[tuple[str, list[str], Writer]] = [
        (
            "calibration_evidence.tsv",
            CALIBRATION_EVIDENCE_COLUMNS,
            write_calibration_evidence_tsv,
        ),
        (
            "matrix_rt_preview.tsv",
            MATRIX_RT_PREVIEW_COLUMNS,
            write_matrix_rt_preview_tsv,
        ),
        ("rt_drift_model.tsv", RT_DRIFT_MODEL_COLUMNS, write_rt_drift_model_tsv),
        (
            "rt_leave_one_anchor_out.tsv",
            RT_LEAVE_ONE_ANCHOR_OUT_COLUMNS,
            write_rt_leave_one_anchor_out_tsv,
        ),
        (
            "matrix_response_preview.tsv",
            MATRIX_RESPONSE_PREVIEW_COLUMNS,
            write_matrix_response_preview_tsv,
        ),
    ]

    for filename, columns, writer in cases:
        path = tmp_path / "empty" / filename

        writer(path, [])

        assert path.read_text(encoding="utf-8").splitlines() == [
            "\t".join(columns)
        ]


def test_write_calibration_evidence_summary_json(tmp_path: Path) -> None:
    path = tmp_path / "instrument_qc_calibration_evidence_summary.json"
    summary = CalibrationEvidenceSummary(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        bundle_id="bundle-001",
        total_rows=1,
        counts_by_source_type={"sdolek": 1},
        counts_by_matrix_context={"clean": 1},
        counts_by_coverage_status={"covered": 1},
        counts_by_product_support_status={"supported": 1},
        counts_by_calibration_eligible={"true": 1},
        missing_artifacts=(),
    )

    write_calibration_evidence_summary_json(path, summary)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["total_rows"] == 1
    assert payload["counts_by_source_type"] == {"sdolek": 1}


def _matrix_rt_preview_row() -> MatrixRTPreviewRow:
    return MatrixRTPreviewRow(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        bundle_id="bundle-001",
        matrix_source="alignment_cells.tsv",
        matrix_source_hash="hash",
        matrix_schema_version="unknown",
        source_row_id="1",
        source_cell_key="FAM001|SampleA",
        feature_id="FAM001",
        matrix_column_name="SampleA",
        sample_name="SampleA",
        sample_stem="SampleA",
        raw_file_stem="SampleA",
        feature_mz=300.0,
        raw_feature_rt_min=12.0,
        injection_order=5,
        model_id="rt-clean-summary",
        predicted_rt_delta_min=0.05,
        rt_uncertainty_min=0.02,
        rt_if_standard_corrected_min=11.95,
        coverage_status=CoverageStatus.COVERED,
        rt_alignment_support_status="local_rt_supported",
        local_anchor_count=3,
        local_clean_anchor_count=3,
        local_biological_istd_anchor_count=0,
        local_residual_p95_min=0.02,
        irt_anchor_scope="inside_anchor_range",
        irt_position=50.0,
        correction_status=CorrectionStatus.SHADOW_ONLY,
        correction_block_reason="",
        review_reason="Preview only.",
    )


def _rt_drift_model_row() -> RtDriftModelRow:
    return RtDriftModelRow(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        bundle_id="bundle-001",
        model_id="model-001",
        model_scope="clean_standard",
        compound="SDO",
        compound_group="SDO",
        source_type="sdolek",
        matrix_context="clean",
        injection_order=5,
        rt_region="rt_06_07",
        source_mix="MixA",
        anchor_ids="anchor-1;anchor-2",
        anchor_count=3,
        clean_anchor_count=3,
        biological_istd_anchor_count=0,
        predicted_rt_delta_min=0.05,
        rt_uncertainty_min=0.02,
        coverage_status=CoverageStatus.COVERED,
        conflict_status="none",
        model_status="fit",
        review_reason="Model row.",
    )


def _rt_leave_one_anchor_out_row() -> RtLeaveOneAnchorOutRow:
    return RtLeaveOneAnchorOutRow(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        bundle_id="bundle-001",
        evidence_row_id="evidence-001",
        compound="SDO",
        source_type="sdolek",
        matrix_context="clean",
        injection_order=5,
        reference_rt_min=6.26,
        observed_rt_delta_min=0.04,
        predicted_rt_delta_min=0.05,
        prediction_error_min=-0.01,
        abs_prediction_error_min=0.01,
        local_anchor_count=3,
        coverage_status=CoverageStatus.COVERED,
        status="PASS",
        review_reason="LOAO row.",
    )


def _matrix_response_preview_row() -> MatrixResponsePreviewRow:
    return MatrixResponsePreviewRow(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        bundle_id="bundle-001",
        matrix_source="alignment_cells.tsv",
        matrix_source_hash="hash",
        matrix_schema_version="unknown",
        source_row_id="row-1",
        source_cell_key="FAM001|SampleA",
        feature_id="FAM001",
        matrix_column_name="SampleA",
        sample_name="SampleA",
        sample_stem="SampleA",
        raw_file_stem="SampleA",
        feature_mz=300.0,
        raw_feature_rt_min=12.0,
        injection_order=5,
        raw_area=None,
        raw_area_status="missing",
        raw_height=None,
        raw_height_status="missing",
        model_id="response-clean-summary",
        predicted_log2_response_delta=None,
        area_if_response_corrected=None,
        height_if_response_corrected=None,
        preview_area_status="blocked",
        preview_height_status="blocked",
        response_uncertainty_log2=None,
        transfer_status=ResponseTransferStatus.CLEAN_ONLY,
        correction_status=CorrectionStatus.BLOCKED_MISSING_VALUE,
        correction_block_reason="raw area is missing",
        review_reason="No imputation in response preview.",
    )
