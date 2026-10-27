from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from typing import Any

from xic_extractor.alignment.backfill.evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import (
    AlignedCell,
    AlignmentMatrix,
    cells_by_cluster,
    row_id,
)
from xic_extractor.alignment.owner_group_delivery import (
    CROSS_SAMPLE_GROUP_CELL_COLUMNS,
    GROUP_BACKFILL_SEED_AUDIT_COLUMNS,
    GROUP_REVIEW_PROJECTION_COLUMNS,
    delivery_feature_id,
    delivery_group_projection,
)
from xic_extractor.alignment.production_decisions import (
    ProductionDecisionSet,
)
from xic_extractor.alignment.untargeted_finalized_matrix import (
    CompletedUntargetBatchWriterInputs,
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

ALIGNMENT_REVIEW_COLUMNS = (
    "feature_id",
    *GROUP_REVIEW_PROJECTION_COLUMNS,
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
    "has_anchor",
    "event_cluster_count",
    "event_cluster_ids",
    "event_member_count",
    "detected_count",
    "absent_count",
    "unchecked_count",
    "duplicate_assigned_count",
    "ambiguous_ms1_owner_count",
    "present_rate",
    "identity_decision",
    "identity_confidence",
    "primary_evidence",
    "identity_reason",
    "quantifiable_detected_count",
    "quantifiable_backfill_count",
    "accepted_cell_count",
    "accepted_backfill_count",
    "review_backfill_count",
    "include_in_primary_matrix",
    "row_flags",
    "membership_risk_status",
    "membership_risk_flags",
    "membership_risks_json",
    "representative_samples",
    "family_evidence",
    "warning",
    "reason",
)

ALIGNMENT_CELLS_COLUMNS = (
    "feature_id",
    *CROSS_SAMPLE_GROUP_CELL_COLUMNS,
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
    "signal_quality_status",
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

BACKFILL_EVIDENCE_ROW_FLAGS = frozenset(
    {
        "backfill_cell_evidence_required",
        "backfill_rescue_review_only",
        "high_backfill_dependency",
        "missing_independent_backfill_identity_evidence",
        "rescue_heavy",
    },
)


ALIGNMENT_BACKFILL_SEED_AUDIT_COLUMNS = (
    "feature_id",
    *GROUP_BACKFILL_SEED_AUDIT_COLUMNS,
    "sample_stem",
    "status",
    "area",
    "apex_rt",
    "peak_start_rt",
    "peak_end_rt",
    "rt_delta_sec",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "backfill_seed_mz",
    "backfill_seed_rt",
    "backfill_request_rt_min",
    "backfill_request_rt_max",
    "backfill_request_ppm",
    "backfill_apex_delta_sec",
    "reason",
)


def _require_production_decisions(value: object) -> ProductionDecisionSet:
    if not isinstance(value, ProductionDecisionSet):
        raise TypeError("production_decisions must be owner-minted")
    return value


def write_alignment_tsv_row_batches(
    paths: Mapping[str, Path],
    batches: Iterable[CompletedUntargetBatchWriterInputs],
    *,
    sample_order: tuple[str, ...],
    alignment_config: AlignmentConfig,
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

        for batch in batches:
            matrix = batch.matrix
            decisions = batch.production_decisions
            writer_row_count += len(matrix.clusters)
            products = build_product_matrix_rows(
                matrix,
                alignment_config=alignment_config,
                production_decisions=decisions,
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
                        matrix_header=matrix_header,
                        matrix_row=table.rows[0],
                        source_review=product.review_projection,
                        index=product_count,
                    )
                    projection_by_feature[bound["feature_id"]] = bound
            if "review" in writers:
                emit(
                    "review",
                    _format_alignment_review_rows(
                        _review_rows(
                            matrix,
                            alignment_config=alignment_config,
                            production_decisions=decisions,
                            matrix_projection=projection_by_feature,
                        )
                    ),
                )
            if "backfill" in writers:
                for values in _backfill_cell_evidence_rows(matrix, decisions):
                    writers["backfill"].writerow(
                        [format_value(value) for value in values]
                    )
            if "cells" in writers:
                emit("cells", _alignment_cell_rows(matrix))
    return writer_row_count


def write_alignment_review_tsv(
    path: Path,
    matrix: AlignmentMatrix,
    *,
    production_decisions: ProductionDecisionSet,
    alignment_config: AlignmentConfig | None = None,
) -> Path:
    decisions = _require_production_decisions(production_decisions)
    config = alignment_config or AlignmentConfig()
    products = build_product_matrix_rows(
        matrix, alignment_config=config, production_decisions=decisions
    )
    table = serialize_public_matrix_table(
        (
            {"Mz": product.mz, "RT": product.rt, **product.sample_values}
            for product in products
        ),
        sample_columns=matrix.sample_order,
    )
    projection = {
        str(product.review_projection["feature_id"]): bind_matrix_review_row(
            matrix_header=table.header,
            matrix_row=values,
            source_review=product.review_projection,
            index=index,
        )
        for index, (product, values) in enumerate(
            zip(products, table.rows, strict=True), start=1
        )
    }
    return _write_tsv(
        path,
        ALIGNMENT_REVIEW_COLUMNS,
        _format_alignment_review_rows(
            _review_rows(
                matrix,
                alignment_config=config,
                production_decisions=decisions,
                matrix_projection=projection,
            )
        ),
    )


def write_alignment_matrix_tsv(
    path: Path,
    matrix: AlignmentMatrix,
    *,
    production_decisions: ProductionDecisionSet,
    alignment_config: AlignmentConfig | None = None,
) -> Path:
    decisions = _require_production_decisions(production_decisions)
    rows = product_matrix_tsv_rows(
        matrix,
        alignment_config=alignment_config,
        production_decisions=decisions,
    )
    table = serialize_public_matrix_table(
        rows,
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
    clusters_by_id = {row_id(cluster): cluster for cluster in matrix.clusters}
    for cell in matrix.cells:
        cluster = clusters_by_id[cell.cluster_id]
        quality = (
            cell.selected_integration.signal_quality
            if cell.selected_integration is not None
            else None
        )
        yield {
            "feature_id": delivery_feature_id(cluster),
            "group_construction_role": cell.group_construction_role,
            "group_delivery_role": cell.group_delivery_role,
            "group_membership_source": cell.group_membership_source,
            "gap_fill_state": cell.gap_fill_state,
            "gap_fill_reason": cell.gap_fill_reason,
            "missing_observation_state": cell.missing_observation_state,
            "sample_stem": cell.sample_stem,
            "status": cell.status,
            "area": format_value(cell.area),
            "primary_matrix_area": format_value(cell.matrix_area),
            "primary_matrix_area_source": cell.matrix_area_source,
            "primary_matrix_area_reason": cell.matrix_area_missing_reason,
            "apex_rt": format_value(cell.apex_rt),
            "height": format_value(cell.height),
            "peak_start_rt": format_value(cell.peak_start_rt),
            "peak_end_rt": format_value(cell.peak_end_rt),
            "rt_delta_sec": format_value(cell.rt_delta_sec),
            "trace_quality": cell.trace_quality,
            "signal_quality_status": quality.status
            if quality is not None
            else "not_recorded",
            "noise_sigma": format_value(quality.noise_sigma)
            if quality is not None
            else "",
            "signal_to_noise": format_value(quality.signal_to_noise)
            if quality is not None
            else "",
            "low_signal_to_noise": quality.low_signal_to_noise
            if quality is not None and quality.low_signal_to_noise is not None
            else "",
            "scan_support_score": format_value(cell.scan_support_score),
            "source_candidate_id": cell.source_candidate_id or "",
            "neutral_loss_tag": cluster.neutral_loss_tag,
            "family_center_mz": format_value(_family_center_mz(cluster)),
            "family_center_rt": format_value(_family_center_rt(cluster)),
            "reason": cell.reason,
        }


def write_alignment_backfill_cell_evidence_tsv(
    path: Path,
    matrix: AlignmentMatrix,
    *,
    production_decisions: ProductionDecisionSet,
) -> Path:
    """Write the compact cell ledger needed by backfill evidence review surfaces."""

    return _write_tsv_sequences(
        path,
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        _backfill_cell_evidence_rows(matrix, production_decisions),
    )


def _backfill_cell_evidence_rows(
    matrix: AlignmentMatrix,
    production_decisions: ProductionDecisionSet,
) -> Iterable[tuple[object, ...]]:
    decisions = _require_production_decisions(production_decisions)
    clusters_by_id = {row_id(cluster): cluster for cluster in matrix.clusters}
    backfill_family_ids = _backfill_evidence_family_ids(matrix, decisions)

    def rows() -> Iterable[tuple[object, ...]]:
        for cell in matrix.cells:
            if cell.cluster_id not in backfill_family_ids:
                continue
            cluster = clusters_by_id[cell.cluster_id]
            row_decision = decisions.row(cell.cluster_id)
            cell_decision = decisions.cell(cell.cluster_id, cell.sample_stem)
            if not _include_backfill_evidence_cell(cell, cell_decision):
                continue
            yield (
                ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION,
                delivery_feature_id(cluster),
                cell.sample_stem,
                cell.status,
                cell_decision.production_status,
                cell_decision.backfill_tier,
                cell_decision.write_matrix_value,
                row_decision.include_in_primary_matrix,
                row_decision.identity_decision,
                ";".join(row_decision.row_flags),
                format_value(cell.area),
                format_value(cell.matrix_area),
                cell.matrix_area_source,
                cell.matrix_area_missing_reason,
                format_value(cell.apex_rt),
                format_value(cell.height),
                format_value(cell.peak_start_rt),
                format_value(cell.peak_end_rt),
                format_value(cell.rt_delta_sec),
                cell.trace_quality,
                format_value(cell.scan_support_score),
                cell.gap_fill_state,
                cell.gap_fill_reason,
                cell.source_candidate_id or "",
                cluster.neutral_loss_tag,
                format_value(_family_center_mz(cluster)),
                format_value(_family_center_rt(cluster)),
                cell.reason,
                format_value(cell.backfill_request_rt_min),
                format_value(cell.backfill_request_rt_max),
            )

    return rows()


def write_alignment_backfill_seed_audit_tsv(
    path: Path,
    matrix: AlignmentMatrix,
) -> Path:
    clusters_by_id = {row_id(cluster): cluster for cluster in matrix.clusters}
    rows: list[dict[str, object]] = []
    for cell in matrix.cells:
        if cell.status != "backfilled" or cell.backfill_seed_mz is None:
            continue
        cluster = clusters_by_id[cell.cluster_id]
        rows.append(
            {
                "feature_id": delivery_feature_id(cluster),
                "group_construction_role": cell.group_construction_role,
                "group_delivery_role": cell.group_delivery_role,
                "group_membership_source": cell.group_membership_source,
                "gap_fill_state": cell.gap_fill_state,
                "gap_fill_reason": cell.gap_fill_reason,
                "missing_observation_state": cell.missing_observation_state,
                "sample_stem": cell.sample_stem,
                "status": cell.status,
                "area": cell.area,
                "apex_rt": cell.apex_rt,
                "peak_start_rt": cell.peak_start_rt,
                "peak_end_rt": cell.peak_end_rt,
                "rt_delta_sec": cell.rt_delta_sec,
                "neutral_loss_tag": cluster.neutral_loss_tag,
                "family_center_mz": _family_center_mz(cluster),
                "family_center_rt": _family_center_rt(cluster),
                "backfill_seed_mz": cell.backfill_seed_mz,
                "backfill_seed_rt": cell.backfill_seed_rt,
                "backfill_request_rt_min": cell.backfill_request_rt_min,
                "backfill_request_rt_max": cell.backfill_request_rt_max,
                "backfill_request_ppm": cell.backfill_request_ppm,
                "backfill_apex_delta_sec": _rt_delta_sec(
                    cell.apex_rt,
                    cell.backfill_seed_rt,
                ),
                "reason": cell.reason,
            }
        )
    return _write_tsv(path, ALIGNMENT_BACKFILL_SEED_AUDIT_COLUMNS, rows)


def write_alignment_status_matrix_tsv(path: Path, matrix: AlignmentMatrix) -> Path:
    columns = (
        "feature_id",
        "neutral_loss_tag",
        "family_center_mz",
        "family_center_rt",
        *matrix.sample_order,
    )
    rows: list[dict[str, object]] = []
    grouped_cells = cells_by_cluster(matrix)
    for cluster in matrix.clusters:
        cluster_id = row_id(cluster)
        cells = grouped_cells.get(cluster_id, ())
        cells_by_sample = {cell.sample_stem: cell for cell in cells}
        row: dict[str, object] = {
            "feature_id": delivery_feature_id(cluster),
            "neutral_loss_tag": cluster.neutral_loss_tag,
            "family_center_mz": format_value(_family_center_mz(cluster)),
            "family_center_rt": format_value(_family_center_rt(cluster)),
        }
        for sample_stem in matrix.sample_order:
            cell = cells_by_sample.get(sample_stem)
            row[sample_stem] = "" if cell is None else cell.status
        rows.append(row)
    return _write_tsv(path, columns, rows)


def _review_rows(
    matrix: AlignmentMatrix,
    *,
    alignment_config: AlignmentConfig,
    production_decisions: ProductionDecisionSet,
    matrix_projection: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    grouped_cells = cells_by_cluster(matrix)
    sample_count = len(matrix.sample_order)
    decisions = production_decisions
    for cluster in matrix.clusters:
        cluster_id = row_id(cluster)
        cells = grouped_cells.get(cluster_id, ())
        row_decision = decisions.row(cluster_id)
        raw_status_counts = Counter(
            decisions.cell(cluster_id, cell.sample_stem).raw_status for cell in cells
        )
        detected_count = raw_status_counts["detected"]
        backfilled_count = raw_status_counts["backfilled"]
        absent_count = raw_status_counts["absent"]
        unchecked_count = raw_status_counts["unchecked"]
        duplicate_assigned_count = raw_status_counts["duplicate_assigned"]
        ambiguous_owner_count = raw_status_counts["ambiguous_ms1_owner"]
        present_count = detected_count + backfilled_count
        rows.append(
            {
                "feature_id": delivery_feature_id(cluster),
                **delivery_group_projection(cluster),
                **matrix_projection.get(delivery_feature_id(cluster), {}),
                "neutral_loss_tag": cluster.neutral_loss_tag,
                "family_center_mz": _family_center_mz(cluster),
                "family_center_rt": _family_center_rt(cluster),
                "family_product_mz": _family_product_mz(cluster),
                "family_observed_neutral_loss_da": (
                    _family_observed_neutral_loss_da(cluster)
                ),
                "has_anchor": cluster.has_anchor,
                "event_cluster_count": len(_event_cluster_ids(cluster)),
                "event_cluster_ids": ";".join(_event_cluster_ids(cluster)),
                "event_member_count": _event_member_count(cluster),
                "detected_count": detected_count,
                "absent_count": absent_count,
                "unchecked_count": unchecked_count,
                "duplicate_assigned_count": duplicate_assigned_count,
                "ambiguous_ms1_owner_count": ambiguous_owner_count,
                "present_rate": safe_rate(present_count, sample_count),
                "identity_decision": row_decision.identity_decision,
                "identity_confidence": row_decision.identity_confidence,
                "primary_evidence": row_decision.primary_evidence,
                "identity_reason": row_decision.identity_reason,
                "quantifiable_detected_count": (
                    row_decision.quantifiable_detected_count
                ),
                "quantifiable_backfill_count": (
                    row_decision.quantifiable_backfill_count
                ),
                "accepted_cell_count": row_decision.accepted_cell_count,
                "accepted_backfill_count": row_decision.accepted_backfill_count,
                "review_backfill_count": row_decision.review_backfill_count,
                "include_in_primary_matrix": row_decision.include_in_primary_matrix,
                "row_flags": ";".join(row_decision.row_flags),
                "membership_risk_status": "recorded"
                if getattr(cluster, "membership_risks", ())
                else "not_recorded",
                "membership_risk_flags": ";".join(
                    sorted(
                        {
                            flag
                            for _, risk in getattr(cluster, "membership_risks", ())
                            for flag in risk.risk_flags
                        }
                    )
                ),
                "membership_risks_json": json.dumps(
                    [
                        {"sample_key": sample, **asdict(risk)}
                        for sample, risk in getattr(cluster, "membership_risks", ())
                    ],
                    ensure_ascii=False,
                    allow_nan=False,
                ),
                "representative_samples": _representative_samples(cells, decisions),
                "family_evidence": _family_evidence(cluster),
                "warning": _warning(
                    cluster,
                    sample_count=sample_count,
                    detected_count=detected_count,
                    backfilled_count=backfilled_count,
                    unchecked_count=unchecked_count,
                ),
                "reason": _reason(
                    cluster,
                    present_count,
                    sample_count,
                    backfilled_count,
                    duplicate_assigned_count,
                    ambiguous_owner_count,
                ),
            }
        )
    return rows


def _backfill_evidence_family_ids(
    matrix: AlignmentMatrix,
    decisions: Any,
) -> set[str]:
    grouped_cells = cells_by_cluster(matrix)
    family_ids: set[str] = set()
    for cluster in matrix.clusters:
        cluster_id = row_id(cluster)
        cells = grouped_cells.get(cluster_id, ())
        row_decision = decisions.row(cluster_id)
        if any(cell.status == "backfilled" for cell in cells):
            family_ids.add(cluster_id)
            continue
        if any(
            count > 0
            for count in (
                row_decision.quantifiable_backfill_count,
                row_decision.accepted_backfill_count,
                row_decision.review_backfill_count,
            )
        ):
            family_ids.add(cluster_id)
            continue
        if set(row_decision.row_flags) & BACKFILL_EVIDENCE_ROW_FLAGS:
            family_ids.add(cluster_id)
            continue
        if any(_cell_mentions_backfill(cell) for cell in cells):
            family_ids.add(cluster_id)
    return family_ids


def _cell_mentions_backfill(cell: AlignedCell) -> bool:
    values = (
        cell.gap_fill_state,
        cell.gap_fill_reason,
        cell.trace_quality,
        cell.matrix_area_source,
        cell.matrix_area_missing_reason,
        cell.reason,
    )
    return any("backfill" in str(value).lower() for value in values)


def _include_backfill_evidence_cell(cell: AlignedCell, decision: Any) -> bool:
    if cell.status in {"detected", "backfilled"}:
        return True
    if getattr(decision, "write_matrix_value", False):
        return True
    production_status = str(getattr(decision, "production_status", ""))
    return production_status in {
        "detected",
        "accepted_backfill",
        "review_backfill",
        "rejected_backfill",
    }


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


def write_safe_alignment_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: Iterable[Mapping[str, object]],
) -> Path:
    """Write an Alignment-owned TSV with spreadsheet-safe headers and values."""

    return _write_tsv(path, fieldnames, rows)


def _write_tsv_sequences(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: Iterable[Sequence[object]],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    escaped_fieldnames = [escape_excel_formula(column) for column in fieldnames]
    with path.open("w", newline="", encoding="utf-8", buffering=1024 * 1024) as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(escaped_fieldnames)
        for row in rows:
            writer.writerow([format_value(value) for value in row])
    return path


def _representative_samples(
    cells: tuple[AlignedCell, ...], decisions: ProductionDecisionSet
) -> str:
    samples = [
        cell.sample_stem
        for cell in cells
        if decisions.cell(cell.cluster_id, cell.sample_stem).raw_status
        in {"detected", "backfilled"}
    ]
    return _cap_semicolon(samples)


def _cap_semicolon(values: list[str]) -> str:
    capped = values[:5]
    if len(values) > 5:
        capped.append("...")
    return ";".join(capped)


def _warning(
    cluster: Any,
    *,
    sample_count: int,
    detected_count: int,
    backfilled_count: int,
    unchecked_count: int,
) -> str:
    if not cluster.has_anchor:
        return "no_anchor"
    if sample_count > 0 and unchecked_count / sample_count > 0.5:
        return "high_unchecked"
    if backfilled_count > detected_count and (
        detected_count == 0 or not _has_detected_owner_evidence(cluster)
    ):
        return "high_backfill_dependency"
    return ""


def _reason(
    cluster: Any,
    present_count: int,
    sample_count: int,
    backfilled_count: int,
    duplicate_assigned_count: int,
    ambiguous_owner_count: int,
) -> str:
    prefix = "anchor family" if cluster.has_anchor else "no anchor"
    parts = [
        prefix,
        f"{present_count}/{sample_count} present",
        f"{backfilled_count} MS1 backfilled",
    ]
    event_cluster_count = len(_event_cluster_ids(cluster))
    if event_cluster_count > 1:
        parts.append(f"merged {event_cluster_count} event clusters")
    if duplicate_assigned_count:
        parts.append(f"{duplicate_assigned_count} duplicate-assigned")
    if ambiguous_owner_count:
        parts.append(f"{ambiguous_owner_count} ambiguous MS1 owner")
    return "; ".join(parts)


def _family_center_mz(row: Any) -> float:
    if hasattr(row, "family_center_mz"):
        return row.family_center_mz
    return row.cluster_center_mz


def _family_center_rt(row: Any) -> float:
    if hasattr(row, "family_center_rt"):
        return row.family_center_rt
    return row.cluster_center_rt


def _rt_delta_sec(left_rt: float | None, right_rt: float | None) -> float | None:
    if left_rt is None or right_rt is None:
        return None
    return (left_rt - right_rt) * 60.0


def _family_product_mz(row: Any) -> float:
    if hasattr(row, "family_product_mz"):
        return row.family_product_mz
    return row.cluster_product_mz


def _family_observed_neutral_loss_da(row: Any) -> float:
    if hasattr(row, "family_observed_neutral_loss_da"):
        return row.family_observed_neutral_loss_da
    return row.cluster_observed_neutral_loss_da


def _event_cluster_ids(row: Any) -> tuple[str, ...]:
    if hasattr(row, "event_cluster_ids"):
        return tuple(row.event_cluster_ids)
    return (str(row.cluster_id), *tuple(row.folded_cluster_ids))


def _event_member_count(row: Any) -> int:
    if hasattr(row, "event_member_count"):
        return int(row.event_member_count)
    return len(row.members) + int(row.folded_member_count)


def _family_evidence(row: Any) -> str:
    if hasattr(row, "evidence"):
        return str(row.evidence)
    return str(row.fold_evidence)


def _has_detected_owner_evidence(row: Any) -> bool:
    evidence = _family_evidence(row)
    return evidence == "single_sample_local_owner" or evidence.startswith(
        "owner_complete_link;"
    )
