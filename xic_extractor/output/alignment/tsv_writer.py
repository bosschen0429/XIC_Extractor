from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Iterable, Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING

from xic_extractor.alignment.backfill.evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
)
from xic_extractor.alignment.owner_group_delivery import delivery_feature_id
from xic_extractor.alignment.primary_matrix_area import (
    primary_matrix_area_from_integration,
)
from xic_extractor.alignment.untargeted_finalized_matrix import (
    PRESENTATION_FORMULA_VERSION,
    AlignmentMatrix,
    FinalizedUntargetMatrixRow,
    backfill_query_bounds,
    cell_origin,
    cell_status,
)
from xic_extractor.output.alignment.matrix_output_contract import (
    MATRIX_REVIEW_COLUMNS,
    bind_matrix_review_row,
    canonical_mz_text,
    serialize_public_matrix_table,
)
from xic_extractor.output.alignment.output_rows import (
    format_value,
    safe_rate,
)
from xic_extractor.output.alignment.product_matrix import (
    build_product_matrix_rows,
    product_matrix_tsv_rows,
)
from xic_extractor.tabular_io import escape_excel_formula

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_decision.owner import ProductionCellDecision

ALIGNMENT_REVIEW_COLUMNS = (
    "feature_id",
    *(
        column
        for column in MATRIX_REVIEW_COLUMNS
        if column not in {"feature_id", "accepted_cell_count"}
    ),
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "family_product_mz",
    "family_observed_neutral_loss_da",
    "physical_peak_keys",
    "detected_count",
    "not_searched_count",
    "unresolved_count",
    "present_rate",
    "identity_reason",
    "accepted_cell_count",
    "accepted_backfill_count",
    "include_in_primary_matrix",
    "row_flags",
    "membership_risk_flags",
    "warning",
    "reason",
)

ALIGNMENT_CELLS_COLUMNS = (
    "feature_id",
    "sample_stem",
    "status",
    "area",
    "primary_matrix_area",
    "primary_matrix_area_source",
    "primary_matrix_area_reason",
    "apex_rt",
    "height",
    "peak_start_rt",
    "peak_end_rt",
    "rt_delta_sec",
    "trace_quality",
    "noise_sigma",
    "signal_to_noise",
    "low_signal_to_noise",
    "scan_support_score",
    "source_candidate_id",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "reason",
)


def write_alignment_tsv_row_batches(
    paths: Mapping[str, Path],
    rows: Iterable[FinalizedUntargetMatrixRow],
    *,
    sample_order: tuple[str, ...],
) -> int:
    """Render finalized rows to their TSV consumers in one bounded pass."""

    matrix_header = serialize_public_matrix_table(
        (), sample_columns=sample_order
    ).header
    headers = {
        "matrix": matrix_header,
        "review": ALIGNMENT_REVIEW_COLUMNS,
        "backfill": ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        "cells": ALIGNMENT_CELLS_COLUMNS,
    }
    source_candidate_owners: dict[str, str] = {}
    product_count = 0
    writer_row_count = 0
    with ExitStack() as stack:
        writers = {}
        for name, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = stack.enter_context(
                path.open("w", newline="", encoding="utf-8", buffering=1024 * 1024)
            )
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow([escape_excel_formula(column) for column in headers[name]])
            writers[name] = writer

        def emit(name: str, rows: Iterable[Mapping[str, object]]) -> None:
            if name in writers:
                for row in rows:
                    writers[name].writerow(
                        [format_value(row.get(column, "")) for column in headers[name]]
                    )

        for row in rows:
            matrix = AlignmentMatrix(clusters=(row,), sample_order=sample_order)
            writer_row_count += 1
            products = build_product_matrix_rows(
                matrix,
                source_candidate_owners=source_candidate_owners,
            )
            projection_by_feature = {}
            for product in products:
                product_count += 1
                table = serialize_public_matrix_table(
                    ({"Mz": product.mz, "RT": product.rt, **product.sample_values},),
                    sample_columns=sample_order,
                )
                emit("matrix", table.rows)
                if "review" in writers:
                    bound = bind_matrix_review_row(
                        source_review=product.review_projection,
                        index=product_count,
                    )
                    projection_by_feature[bound["feature_id"]] = bound
            if "review" in writers:
                emit(
                    "review",
                    _format_alignment_review_rows(
                        _review_rows(matrix, matrix_projection=projection_by_feature)
                    ),
                )
            emit("backfill", _backfill_cell_evidence_rows(matrix))
            emit("cells", _alignment_cell_rows(matrix))
    return writer_row_count


def write_alignment_review_tsv(path: Path, matrix: AlignmentMatrix) -> Path:
    products = build_product_matrix_rows(matrix)
    projection = {
        str(product.review_projection["feature_id"]): bind_matrix_review_row(
            source_review=product.review_projection,
            index=index,
        )
        for index, product in enumerate(products, start=1)
    }
    return _write_tsv(
        path,
        ALIGNMENT_REVIEW_COLUMNS,
        _format_alignment_review_rows(
            _review_rows(matrix, matrix_projection=projection)
        ),
    )


def write_alignment_matrix_tsv(path: Path, matrix: AlignmentMatrix) -> Path:
    table = serialize_public_matrix_table(
        product_matrix_tsv_rows(matrix),
        sample_columns=matrix.sample_order,
    )
    return _write_tsv(path, table.header, table.rows)


def _format_alignment_review_rows(
    rows: Iterable[Mapping[str, object]],
) -> Iterable[Mapping[str, object]]:
    for row in rows:
        formatted = dict(row)
        formatted["family_center_mz"] = canonical_mz_text(
            row.get("family_center_mz", "")
        )
        yield formatted


def write_alignment_cells_tsv(path: Path, matrix: AlignmentMatrix) -> Path:
    return _write_tsv(path, ALIGNMENT_CELLS_COLUMNS, _alignment_cell_rows(matrix))


def _alignment_cell_rows(matrix: AlignmentMatrix) -> Iterable[dict[str, object]]:
    for cluster in matrix.clusters:
        candidate_ids = dict(cluster.source_candidate_ids)
        for sample_stem, cell in zip(matrix.sample_order, cluster.cells, strict=True):
            yield _cell_fields(cluster, sample_stem, cell, candidate_ids)


def _cell_fields(
    cluster: FinalizedUntargetMatrixRow,
    sample_stem: str,
    cell: ProductionCellDecision,
    candidate_ids: Mapping[str, str],
) -> dict[str, object]:
    integration = cell.selected_integration
    quality = integration.signal_quality if integration is not None else None
    missing_area_reason = (
        primary_matrix_area_from_integration(integration).reason
        if cell_origin(cell) is not None
        else ""
    )
    return {
        "feature_id": delivery_feature_id(cluster),
        "sample_stem": sample_stem,
        "status": cell_status(cell),
        "area": format_value(cell.value),
        "primary_matrix_area": format_value(cell.value),
        "primary_matrix_area_source": cell.source or missing_area_reason,
        "primary_matrix_area_reason": missing_area_reason,
        "apex_rt": format_value(
            integration.rt_apex_min
            if cell.value is not None and integration is not None
            else None
        ),
        "height": format_value(
            integration.height_raw if integration is not None else None
        ),
        "peak_start_rt": format_value(
            integration.rt_left_min if integration is not None else None
        ),
        "peak_end_rt": format_value(
            integration.rt_right_min if integration is not None else None
        ),
        "rt_delta_sec": "",
        "trace_quality": "finalized_owner_decision",
        "noise_sigma": format_value(quality.noise_sigma) if quality is not None else "",
        "signal_to_noise": format_value(quality.signal_to_noise)
        if quality is not None
        else "",
        "low_signal_to_noise": quality.low_signal_to_noise
        if quality is not None and quality.low_signal_to_noise is not None
        else "",
        "scan_support_score": "",
        "source_candidate_id": candidate_ids.get(sample_stem, ""),
        "neutral_loss_tag": cluster.neutral_loss_tag,
        "family_center_mz": format_value(cluster.family_center_mz),
        "family_center_rt": format_value(cluster.family_center_rt),
        "reason": cell.source or cell.variant,
    }


def write_alignment_backfill_cell_evidence_tsv(
    path: Path, matrix: AlignmentMatrix
) -> Path:
    """Write the compact cell ledger needed by backfill evidence review surfaces."""

    return _write_tsv(
        path,
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        _backfill_cell_evidence_rows(matrix),
    )


def _backfill_cell_evidence_rows(
    matrix: AlignmentMatrix,
) -> Iterable[dict[str, object]]:
    for cluster in matrix.clusters:
        if not any(cell_origin(cell) == "backfilled" for cell in cluster.cells):
            continue
        candidate_ids = dict(cluster.source_candidate_ids)
        for sample_stem, cell in zip(matrix.sample_order, cluster.cells, strict=True):
            if cell_origin(cell) is None:
                continue
            query_rt_min, query_rt_max = backfill_query_bounds(cell)
            yield {
                **_cell_fields(cluster, sample_stem, cell, candidate_ids),
                "schema_version": ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
                "include_in_primary_matrix": cluster.destination == "matrix",
                "identity_decision": _identity_decision(cluster),
                "row_flags": _row_flags(cluster),
                "backfill_query_rt_min": format_value(query_rt_min),
                "backfill_query_rt_max": format_value(query_rt_max),
            }


def write_alignment_status_matrix_tsv(path: Path, matrix: AlignmentMatrix) -> Path:
    columns = (
        "feature_id",
        "neutral_loss_tag",
        "family_center_mz",
        "family_center_rt",
        *matrix.sample_order,
    )
    rows: list[dict[str, object]] = []
    for cluster in matrix.clusters:
        row: dict[str, object] = {
            "feature_id": delivery_feature_id(cluster),
            "neutral_loss_tag": cluster.neutral_loss_tag,
            "family_center_mz": format_value(cluster.family_center_mz),
            "family_center_rt": format_value(cluster.family_center_rt),
        }
        for sample_stem, cell in zip(matrix.sample_order, cluster.cells, strict=True):
            row[sample_stem] = cell_status(cell)
        rows.append(row)
    return _write_tsv(path, columns, rows)


def _review_rows(
    matrix: AlignmentMatrix,
    *,
    matrix_projection: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    sample_count = len(matrix.sample_order)
    for cluster in matrix.clusters:
        status_counts = Counter(cell_status(cell) for cell in cluster.cells)
        detected_count = status_counts["detected"]
        backfilled_count = status_counts["backfilled"]
        not_searched_count = status_counts["not_searched"]
        unresolved_count = status_counts["unresolved"]
        present_count = detected_count + backfilled_count
        rows.append(
            {
                "feature_id": delivery_feature_id(cluster),
                **matrix_projection.get(delivery_feature_id(cluster), {}),
                "neutral_loss_tag": cluster.neutral_loss_tag,
                "family_center_mz": cluster.family_center_mz,
                "family_center_rt": cluster.family_center_rt,
                "family_product_mz": cluster.family_product_mz,
                "family_observed_neutral_loss_da": (
                    cluster.family_observed_neutral_loss_da
                ),
                "physical_peak_keys": ";".join(cluster.physical_peak_keys),
                "detected_count": detected_count,
                "not_searched_count": not_searched_count,
                "unresolved_count": unresolved_count,
                "present_rate": safe_rate(present_count, sample_count),
                "identity_reason": cluster.route_reason,
                "accepted_cell_count": sum(
                    cell.value is not None for cell in cluster.cells
                ),
                "accepted_backfill_count": backfilled_count,
                "include_in_primary_matrix": cluster.destination == "matrix",
                "row_flags": _row_flags(cluster),
                "membership_risk_flags": ";".join(
                    sorted(
                        {
                            flag
                            for _, risk in cluster.membership_risks
                            for flag in risk.risk_flags
                        }
                    )
                ),
                "warning": _warning(
                    cluster,
                    sample_count=sample_count,
                    detected_count=detected_count,
                    backfilled_count=backfilled_count,
                    not_searched_count=not_searched_count,
                ),
                "reason": _reason(
                    cluster,
                    present_count,
                    sample_count,
                    backfilled_count,
                    unresolved_count,
                ),
            }
        )
    return rows


def _identity_decision(cluster: FinalizedUntargetMatrixRow) -> str:
    return (
        "finalized_matrix" if cluster.destination == "matrix" else "finalized_archive"
    )


def _row_flags(cluster: FinalizedUntargetMatrixRow) -> str:
    return f"finalized_route:{cluster.destination};{PRESENTATION_FORMULA_VERSION}"


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: Iterable[Mapping[str, object]],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    column_map = tuple((column, escape_excel_formula(column)) for column in fieldnames)
    with path.open("w", newline="", encoding="utf-8", buffering=1024 * 1024) as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow([escaped_column for _, escaped_column in column_map])
        for row in rows:
            writer.writerow(
                [format_value(row.get(column, "")) for column, _ in column_map],
            )
    return path


def _warning(
    cluster: FinalizedUntargetMatrixRow,
    *,
    sample_count: int,
    detected_count: int,
    backfilled_count: int,
    not_searched_count: int,
) -> str:
    if not cluster.has_anchor:
        return "no_anchor"
    if sample_count > 0 and not_searched_count / sample_count > 0.5:
        return "high_not_searched"
    if backfilled_count > 0 and detected_count == 0:
        return "high_backfill_dependency"
    return ""


def _reason(
    cluster: FinalizedUntargetMatrixRow,
    present_count: int,
    sample_count: int,
    backfilled_count: int,
    unresolved_count: int,
) -> str:
    prefix = "anchor family" if cluster.has_anchor else "no anchor"
    parts = [
        prefix,
        f"{present_count}/{sample_count} present",
        f"{backfilled_count} MS1 backfilled",
    ]
    if unresolved_count:
        parts.append(f"{unresolved_count} unresolved")
    return "; ".join(parts)
