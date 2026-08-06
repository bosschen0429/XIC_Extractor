from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.output_rows import (
    cells_by_cluster,
    count_status,
    escape_excel_formula,
    row_id,
)
from xic_extractor.alignment.owner_group_delivery import (
    CROSS_SAMPLE_GROUP_CELL_COLUMNS,
    GROUP_REVIEW_PROJECTION_COLUMNS,
    delivery_group_projection,
)
from xic_extractor.alignment.product_matrix import build_product_matrix_rows
from xic_extractor.alignment.production_decisions import (
    ProductionDecisionSet,
    build_production_decisions,
)
from xic_extractor.alignment.quant_matrix_version import (
    CELL_PROVENANCE_COLUMNS,
    EXPECTED_DIFF_SUMMARY_COLUMNS,
    ROW_SUMMARY_COLUMNS,
    QuantMatrixVersionRows,
)


def write_alignment_results_xlsx(
    path: Path,
    matrix: AlignmentMatrix,
    *,
    metadata: dict[str, str],
    alignment_config: AlignmentConfig | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    config = alignment_config or AlignmentConfig()
    decisions = build_production_decisions(
        matrix,
        config,
    )
    workbook = Workbook()
    matrix_sheet = workbook.active
    matrix_sheet.title = "Matrix"
    _write_matrix_sheet(matrix_sheet, matrix, alignment_config=config)
    _write_review_sheet(workbook.create_sheet("Review"), matrix, decisions)
    _write_audit_sheet(workbook.create_sheet("Audit"), matrix, decisions)
    _write_metadata_sheet(workbook.create_sheet("Metadata"), metadata)
    workbook.save(path)
    return path


def write_quant_matrix_version_xlsx(
    path: Path,
    *,
    matrix_header: Sequence[str],
    quant_matrix_version: QuantMatrixVersionRows,
    metadata: Mapping[str, str],
) -> Path:
    """Render an already-authorized QuantMatrixVersion without recomputation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    matrix_sheet = workbook.active
    matrix_sheet.title = "Matrix"
    _write_mapping_rows(
        matrix_sheet,
        matrix_header,
        quant_matrix_version.quant_matrix_rows,
        matrix_values=True,
    )
    _write_mapping_rows(
        workbook.create_sheet("CellProvenance"),
        CELL_PROVENANCE_COLUMNS,
        quant_matrix_version.cell_provenance_rows,
    )
    _write_mapping_rows(
        workbook.create_sheet("RowSummary"),
        ROW_SUMMARY_COLUMNS,
        quant_matrix_version.row_summary_rows,
    )
    _write_mapping_rows(
        workbook.create_sheet("ExpectedDiff"),
        EXPECTED_DIFF_SUMMARY_COLUMNS,
        quant_matrix_version.expected_diff_summary_rows,
    )
    _write_metadata_sheet(
        workbook.create_sheet("Metadata"),
        dict(metadata),
    )
    workbook.save(path)
    return path


def _write_mapping_rows(
    sheet: Any,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, str]],
    *,
    matrix_values: bool = False,
) -> None:
    _append_xlsx_row(sheet, columns)
    for row in rows:
        values: list[object] = [row.get(column, "") for column in columns]
        if matrix_values:
            values = [_xlsx_quant_matrix_value(value) for value in values]
        _append_xlsx_row(sheet, values)


def _write_matrix_sheet(
    sheet: Any,
    matrix: AlignmentMatrix,
    alignment_config: AlignmentConfig,
) -> None:
    headers = ["Mz", "RT", *matrix.sample_order]
    _append_xlsx_row(sheet, headers)
    for product_row in build_product_matrix_rows(
        matrix,
        alignment_config=alignment_config,
    ):
        _append_xlsx_row(
            sheet,
            [
                product_row.mz,
                product_row.rt,
                *[
                    _xlsx_matrix_value(product_row.sample_values[sample])
                    for sample in matrix.sample_order
                ],
            ],
        )


def _write_review_sheet(
    sheet: Any,
    matrix: AlignmentMatrix,
    decisions: ProductionDecisionSet,
) -> None:
    _append_xlsx_row(
        sheet,
        [
            "feature_family_id",
            *GROUP_REVIEW_PROJECTION_COLUMNS,
            "neutral_loss_tag",
            "detected_count",
            "rescued_count",
            "identity_decision",
            "identity_confidence",
            "primary_evidence",
            "identity_reason",
            "quantifiable_detected_count",
            "quantifiable_rescue_count",
            "accepted_cell_count",
            "accepted_rescue_count",
            "review_rescue_count",
            "absent_count",
            "unchecked_count",
            "duplicate_assigned_count",
            "ambiguous_ms1_owner_count",
            "include_in_primary_matrix",
            "row_flags",
            "artificial_adduct_role",
            "artificial_adduct_name",
            "artificial_adduct_related_family_id",
            "artificial_adduct_mz_delta_error_ppm",
            "artificial_adduct_rt_delta_min",
        ],
    )
    grouped_cells = cells_by_cluster(matrix)
    for cluster in matrix.clusters:
        cluster_id = row_id(cluster)
        cells = grouped_cells.get(cluster_id, ())
        row_decision = decisions.row(cluster_id)
        group_projection = dict(delivery_group_projection(cluster))
        _append_xlsx_row(
            sheet,
            [
                cluster_id,
                *[
                    group_projection[column]
                    for column in GROUP_REVIEW_PROJECTION_COLUMNS
                ],
                cluster.neutral_loss_tag,
                count_status(cells, "detected"),
                count_status(cells, "rescued"),
                row_decision.identity_decision,
                row_decision.identity_confidence,
                row_decision.primary_evidence,
                row_decision.identity_reason,
                row_decision.quantifiable_detected_count,
                row_decision.quantifiable_rescue_count,
                row_decision.accepted_cell_count,
                row_decision.accepted_rescue_count,
                row_decision.review_rescue_count,
                count_status(cells, "absent"),
                count_status(cells, "unchecked"),
                count_status(cells, "duplicate_assigned"),
                count_status(cells, "ambiguous_ms1_owner"),
                row_decision.include_in_primary_matrix,
                ";".join(row_decision.row_flags),
                _optional_attr(cluster, "artificial_adduct_role"),
                _optional_attr(cluster, "artificial_adduct_name"),
                _optional_attr(cluster, "artificial_adduct_related_family_id"),
                _optional_attr(cluster, "artificial_adduct_mz_delta_error_ppm"),
                _optional_attr(cluster, "artificial_adduct_rt_delta_min"),
            ],
        )


def _write_audit_sheet(
    sheet: Any,
    matrix: AlignmentMatrix,
    decisions: ProductionDecisionSet,
) -> None:
    _append_xlsx_row(
        sheet,
        [
            "feature_family_id",
            *CROSS_SAMPLE_GROUP_CELL_COLUMNS,
            "sample_stem",
            "neutral_loss_tag",
            "family_center_mz",
            "family_center_rt",
            "raw_status",
            "production_status",
            "rescue_tier",
            "write_matrix_value",
            "blank_reason",
            "area",
            "primary_matrix_area",
            "primary_matrix_area_source",
            "primary_matrix_area_reason",
            "apex_rt",
            "rt_delta_sec",
            "claim_state",
            "row_flags",
            "identity_decision",
            "identity_confidence",
            "primary_evidence",
            "identity_reason",
            "quantifiable_detected_count",
            "quantifiable_rescue_count",
            "accepted_cell_count",
            "accepted_rescue_count",
            "review_rescue_count",
            "duplicate_assigned_count",
            "ambiguous_ms1_owner_count",
            "artificial_adduct_role",
            "artificial_adduct_name",
            "artificial_adduct_related_family_id",
            "artificial_adduct_mz_delta_error_ppm",
            "artificial_adduct_rt_delta_min",
            "region_decision_status",
            "region_decision_class",
            "region_product_action",
            "region_promotion_reason",
            "region_baseline_method",
            "reason",
        ],
    )
    clusters = {row_id(cluster): cluster for cluster in matrix.clusters}
    for cell in matrix.cells:
        cluster = clusters.get(cell.cluster_id)
        if cluster is None:
            raise ValueError(
                f"Alignment cell references unknown cluster: {cell.cluster_id}"
            )
        decision = decisions.cell(cell.cluster_id, cell.sample_stem)
        row_decision = decisions.row(cell.cluster_id)
        _append_xlsx_row(
            sheet,
            [
                cell.cluster_id,
                *[getattr(cell, column) for column in CROSS_SAMPLE_GROUP_CELL_COLUMNS],
                cell.sample_stem,
                cluster.neutral_loss_tag,
                _family_center_mz(cluster),
                _family_center_rt(cluster),
                decision.raw_status,
                decision.production_status,
                decision.rescue_tier,
                decision.write_matrix_value,
                decision.blank_reason,
                cell.area,
                cell.matrix_area,
                cell.matrix_area_source,
                cell.matrix_area_missing_reason,
                cell.apex_rt,
                cell.rt_delta_sec,
                _claim_state(cell),
                ";".join(row_decision.row_flags),
                row_decision.identity_decision,
                row_decision.identity_confidence,
                row_decision.primary_evidence,
                row_decision.identity_reason,
                row_decision.quantifiable_detected_count,
                row_decision.quantifiable_rescue_count,
                row_decision.accepted_cell_count,
                row_decision.accepted_rescue_count,
                row_decision.review_rescue_count,
                row_decision.duplicate_assigned_count,
                row_decision.ambiguous_ms1_owner_count,
                _optional_attr(cluster, "artificial_adduct_role"),
                _optional_attr(cluster, "artificial_adduct_name"),
                _optional_attr(cluster, "artificial_adduct_related_family_id"),
                _optional_attr(cluster, "artificial_adduct_mz_delta_error_ppm"),
                _optional_attr(cluster, "artificial_adduct_rt_delta_min"),
                cell.region_decision_status,
                cell.region_decision_class,
                cell.region_product_action,
                cell.region_promotion_reason,
                cell.region_baseline_method,
                cell.reason,
            ],
        )


def _write_metadata_sheet(sheet: Any, metadata: dict[str, str]) -> None:
    _append_xlsx_row(sheet, ["key", "value"])
    for key in sorted(metadata):
        _append_xlsx_row(sheet, [key, metadata[key]])


def _append_xlsx_row(sheet: Any, values: Sequence[object]) -> None:
    sheet.append([_xlsx_value(value) for value in values])


def _xlsx_value(value: object) -> object:
    if isinstance(value, str):
        return escape_excel_formula(value)
    return value


def _xlsx_matrix_value(text: str) -> float | None:
    return float(text) if text else None


def _xlsx_quant_matrix_value(value: object) -> float | None:
    return float(str(value)) if value != "" else None


def _claim_state(cell: AlignedCell) -> str:
    if cell.status == "duplicate_assigned":
        return "loser"
    if cell.status in {"detected", "rescued"}:
        return "winner_or_unclaimed"
    return ""


def _family_center_mz(row: Any) -> float:
    if hasattr(row, "family_center_mz"):
        return row.family_center_mz
    return row.cluster_center_mz


def _family_center_rt(row: Any) -> float:
    if hasattr(row, "family_center_rt"):
        return row.family_center_rt
    return row.cluster_center_rt


def _optional_attr(row: Any, name: str) -> object:
    return getattr(row, name, "")
