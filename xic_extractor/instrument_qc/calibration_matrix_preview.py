from __future__ import annotations

from pathlib import Path

from xic_extractor.injection_rolling import read_injection_order
from xic_extractor.instrument_qc.calibration_product_evidence import counts
from xic_extractor.instrument_qc.calibration_product_loaders import (
    parse_optional_float,
    read_tsv_rows,
)
from xic_extractor.instrument_qc.calibration_product_models import (
    ARTIFACT_SCHEMA_VERSION,
    CorrectionStatus,
    MatrixResponsePreviewRow,
    MatrixRTPreviewRow,
    ResponseTransferStatus,
)
from xic_extractor.instrument_qc.calibration_rt_model import RtModelBundle

ALIGNMENT_CELLS_REQUIRED_COLUMNS = {
    "feature_family_id",
    "sample_stem",
    "status",
    "area",
    "apex_rt",
    "source_raw_file",
    "family_center_mz",
    "family_center_rt",
}
_MEASURED_CELL_STATUSES = frozenset({"detected", "backfilled"})


def build_rt_preview_rows(
    *,
    bundle_id: str,
    matrix_input: Path,
    matrix_hash: str,
    rt_model: RtModelBundle,
    injection_order: dict[str, int],
) -> tuple[MatrixRTPreviewRow, ...]:
    rows: list[MatrixRTPreviewRow] = []
    for row_number, row in enumerate(
        read_tsv_rows(matrix_input, required_columns=ALIGNMENT_CELLS_REQUIRED_COLUMNS),
        start=1,
    ):
        tsv_row_number = row_number + 1
        raw_rt = parse_optional_float(
            row,
            "apex_rt",
            path=matrix_input,
            row_number=tsv_row_number,
        )
        family_center_rt = parse_optional_float(
            row,
            "family_center_rt",
            path=matrix_input,
            row_number=tsv_row_number,
        )
        feature_id = (row.get("feature_family_id") or "").strip()
        sample_stem = (row.get("sample_stem") or "").strip()
        raw_file_stem = raw_file_stem_from_row(row.get("source_raw_file"), sample_stem)
        sample_order = injection_order.get(raw_file_stem) or injection_order.get(
            sample_stem
        )
        status = (row.get("status") or "").strip()
        prediction = rt_model.predict(
            feature_rt_min=raw_rt or family_center_rt,
            injection_order=sample_order,
        )
        correction_status = CorrectionStatus.SHADOW_ONLY
        corrected_rt = None
        block_reason = ""
        review_reason = prediction.review_reason
        if prediction.predicted_rt_delta_min is None:
            correction_status = CorrectionStatus.BLOCKED_NOT_COVERED
            block_reason = prediction.rt_alignment_support_status
        elif raw_rt is None:
            correction_status = CorrectionStatus.BLOCKED_MISSING_VALUE
            block_reason = "raw feature RT is missing"
            review_reason = "No imputation in RT preview."
        elif status not in _MEASURED_CELL_STATUSES:
            correction_status = CorrectionStatus.NOT_APPLICABLE
            block_reason = f"cell status is {status or 'blank'}"
            review_reason = "RT preview is only informative for measured cells."
        else:
            corrected_rt = raw_rt - prediction.predicted_rt_delta_min
        rows.append(
            MatrixRTPreviewRow(
                schema_version=ARTIFACT_SCHEMA_VERSION,
                bundle_id=bundle_id,
                matrix_source=matrix_input.name,
                matrix_source_hash=matrix_hash,
                matrix_schema_version="alignment_cells.tsv",
                source_row_id=str(tsv_row_number),
                source_cell_key=f"{feature_id}|{sample_stem}",
                feature_id=feature_id,
                matrix_column_name=sample_stem,
                sample_name=sample_stem,
                sample_stem=sample_stem,
                raw_file_stem=raw_file_stem,
                feature_mz=parse_optional_float(
                    row,
                    "family_center_mz",
                    path=matrix_input,
                    row_number=tsv_row_number,
                ),
                raw_feature_rt_min=raw_rt or family_center_rt,
                injection_order=sample_order,
                model_id=prediction.model_id,
                predicted_rt_delta_min=prediction.predicted_rt_delta_min,
                rt_uncertainty_min=prediction.rt_uncertainty_min,
                rt_if_standard_corrected_min=corrected_rt,
                coverage_status=prediction.coverage_status,
                rt_alignment_support_status=prediction.rt_alignment_support_status,
                local_anchor_count=prediction.local_anchor_count,
                local_clean_anchor_count=prediction.local_clean_anchor_count,
                local_biological_istd_anchor_count=(
                    prediction.local_biological_istd_anchor_count
                ),
                local_residual_p95_min=prediction.local_residual_p95_min,
                irt_anchor_scope=prediction.irt_anchor_scope,
                irt_position=prediction.irt_position,
                correction_status=correction_status,
                correction_block_reason=block_reason,
                review_reason=review_reason,
            )
        )
    return tuple(rows)


def build_response_preview_rows(
    *,
    bundle_id: str,
    matrix_input: Path,
    matrix_hash: str,
) -> tuple[MatrixResponsePreviewRow, ...]:
    rows: list[MatrixResponsePreviewRow] = []
    for row_number, row in enumerate(
        read_tsv_rows(matrix_input, required_columns=ALIGNMENT_CELLS_REQUIRED_COLUMNS),
        start=1,
    ):
        tsv_row_number = row_number + 1
        feature_id = (row.get("feature_family_id") or "").strip()
        sample_stem = (row.get("sample_stem") or "").strip()
        raw_area = parse_optional_float(
            row,
            "area",
            path=matrix_input,
            row_number=tsv_row_number,
        )
        raw_height = parse_optional_float(
            row,
            "height",
            path=matrix_input,
            row_number=tsv_row_number,
        )
        rows.append(
            MatrixResponsePreviewRow(
                schema_version=ARTIFACT_SCHEMA_VERSION,
                bundle_id=bundle_id,
                matrix_source=matrix_input.name,
                matrix_source_hash=matrix_hash,
                matrix_schema_version="alignment_cells.tsv",
                source_row_id=str(tsv_row_number),
                source_cell_key=f"{feature_id}|{sample_stem}",
                feature_id=feature_id,
                matrix_column_name=sample_stem,
                sample_name=sample_stem,
                sample_stem=sample_stem,
                raw_file_stem=raw_file_stem_from_row(
                    row.get("source_raw_file"),
                    sample_stem,
                ),
                feature_mz=parse_optional_float(
                    row,
                    "family_center_mz",
                    path=matrix_input,
                    row_number=tsv_row_number,
                ),
                raw_feature_rt_min=parse_optional_float(
                    row,
                    "apex_rt",
                    path=matrix_input,
                    row_number=tsv_row_number,
                ),
                injection_order=None,
                raw_area=raw_area,
                raw_area_status="missing" if raw_area is None else "observed",
                raw_height=raw_height,
                raw_height_status="missing" if raw_height is None else "observed",
                model_id="response-transfer-gate-unavailable",
                predicted_log2_response_delta=None,
                area_if_response_corrected=None,
                height_if_response_corrected=None,
                preview_area_status="blocked",
                preview_height_status="blocked",
                response_uncertainty_log2=None,
                transfer_status=ResponseTransferStatus.CLEAN_ONLY,
                correction_status=CorrectionStatus.BLOCKED_NOT_COVERED,
                correction_block_reason=(
                    "biological response transfer gate is not implemented"
                ),
                review_reason=(
                    "Response preview is shadow-only; no area or height correction "
                    "is applied."
                ),
            )
        )
    return tuple(rows)


def preview_summary(
    *,
    bundle_id: str,
    matrix_source: str,
    matrix_source_hash: str,
    total_rows: int,
    correction_status_counts: dict[str, int],
) -> dict[str, object]:
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "matrix_source": matrix_source,
        "matrix_source_hash": matrix_source_hash,
        "total_rows": total_rows,
        "counts_by_correction_status": correction_status_counts,
    }


def raw_file_stem_from_row(raw_file: str | None, sample_stem: str) -> str:
    value = (raw_file or "").strip()
    if not value:
        return sample_stem
    return Path(value).stem or sample_stem


def read_optional_injection_order(instrument_qc_dir: Path) -> dict[str, int]:
    path = instrument_qc_dir / "instrument_qc_injection_order.csv"
    if not path.exists():
        return {}
    return read_injection_order(path)


def correction_status_counts(rows) -> dict[str, int]:
    return counts(str(row.correction_status) for row in rows)
