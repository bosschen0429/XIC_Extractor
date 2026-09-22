from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.matrix_output_contract import (
    ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
    MATRIX_IDENTITY_SCHEMA_VERSION,
    MATRIX_ROW_BINDING_COLUMN,
    bind_matrix_identity_rows,
    canonical_mz_text,
    serialize_public_matrix_table,
)
from xic_extractor.alignment.output_rows import (
    cells_by_cluster,
    format_value,
    production_matrix_area,
    row_id,
)
from xic_extractor.alignment.production_decisions import (
    ProductionCellDecision,
    ProductionDecisionSet,
    build_production_decisions,
)
from xic_extractor.alignment.untargeted_finalized_matrix import (
    FinalizedUntargetMatrixRow,
)

_ALLOWED_ROW_IDENTITY_BASIS = {
    "no_split_peak_hypothesis",
    "split_peak_hypothesis",
}
_FORBIDDEN_ROW_IDENTITY_BASIS = {
    "family_projection",
    "family_projection_no_split_evidence",
}
_ALLOWED_SPLIT_EVALUATION_STATUS = {
    "complete_no_product_ready_split",
    "complete_product_ready_split",
}
ProductCellAssignment = tuple[AlignedCell, ProductionCellDecision]
SplitHypothesisAssignment = tuple[str, Any, tuple[ProductCellAssignment, ...]]


@dataclass(frozen=True)
class ProductMatrixRow:
    matrix_row_index: int
    peak_hypothesis_id: str
    mz: float
    rt: float
    sample_values: dict[str, str]
    identity: dict[str, object]


def build_product_matrix_rows(
    matrix: AlignmentMatrix,
    *,
    alignment_config: AlignmentConfig | None = None,
    production_decisions: ProductionDecisionSet | None = None,
    source_candidate_owners: dict[str, str] | None = None,
) -> tuple[ProductMatrixRow, ...]:
    config = alignment_config or AlignmentConfig()
    decisions = production_decisions or build_production_decisions(matrix, config)
    grouped_cells = cells_by_cluster(matrix)
    rows: list[ProductMatrixRow] = []
    seen_source_candidates: dict[str, str] = (
        {} if source_candidate_owners is None else source_candidate_owners
    )
    seen_peak_hypothesis_ids: dict[str, str] = {}

    for cluster in matrix.clusters:
        cluster_id = row_id(cluster)
        row_decision = decisions.row(cluster_id)
        cluster_cells = grouped_cells.get(cluster_id, ())
        cluster_sample_stems = {cell.sample_stem for cell in cluster_cells}
        explicit_hypotheses = _explicit_peak_hypotheses(cluster)
        if explicit_hypotheses:
            parent_product_cells = _product_cells(cluster_id, cluster_cells, decisions)
            if not parent_product_cells:
                continue
            child_assignments = _split_hypothesis_assignments(
                cluster_id,
                parent_product_cells,
                decisions,
                explicit_hypotheses,
            )
            for peak_hypothesis_id, hypothesis, product_cells in child_assignments:
                _claim_peak_hypothesis_id(
                    peak_hypothesis_id,
                    seen_peak_hypothesis_ids,
                    source_family_id=cluster_id,
                )
                _claim_source_candidates(
                    product_cells,
                    seen_source_candidates,
                    row_owner_id=peak_hypothesis_id,
                )
                identity = _identity_row(
                    hypothesis,
                    matrix_row_index=len(rows) + 1,
                    product_cells=product_cells,
                    fallback_cluster=cluster,
                )
                rows.append(
                    ProductMatrixRow(
                        matrix_row_index=len(rows) + 1,
                        peak_hypothesis_id=str(identity["peak_hypothesis_id"]),
                        mz=_float_identity_value(identity["Mz"], name="Mz"),
                        rt=_float_identity_value(identity["RT"], name="RT"),
                        sample_values=_sample_values_for_product_cells(
                            product_cells,
                            matrix.sample_order,
                        ),
                        identity=identity,
                    )
                )
            continue
        if not row_decision.include_in_primary_matrix:
            continue
        product_cells = _product_cells(cluster_id, cluster_cells, decisions)
        peak_hypothesis_id = _product_identity_id(cluster)
        _claim_peak_hypothesis_id(
            peak_hypothesis_id,
            seen_peak_hypothesis_ids,
            source_family_id=cluster_id,
        )
        _claim_source_candidates(
            product_cells,
            seen_source_candidates,
            row_owner_id=peak_hypothesis_id,
        )
        identity = _identity_row(
            cluster,
            matrix_row_index=len(rows) + 1,
            product_cells=product_cells,
        )
        sample_values = {}
        for sample_stem in matrix.sample_order:
            decision = (
                decisions.cell(cluster_id, sample_stem)
                if sample_stem in cluster_sample_stems
                else None
            )
            sample_values[sample_stem] = production_matrix_area(decision)
        rows.append(
            ProductMatrixRow(
                matrix_row_index=len(rows) + 1,
                peak_hypothesis_id=str(identity["peak_hypothesis_id"]),
                mz=_float_identity_value(identity["Mz"], name="Mz"),
                rt=_float_identity_value(identity["RT"], name="RT"),
                sample_values=sample_values,
                identity=identity,
            )
        )
    return tuple(rows)


def product_matrix_tsv_rows(
    matrix: AlignmentMatrix,
    *,
    alignment_config: AlignmentConfig | None = None,
    production_decisions: ProductionDecisionSet | None = None,
) -> list[dict[str, object]]:
    return [
        {"Mz": row.mz, "RT": row.rt, **row.sample_values}
        for row in build_product_matrix_rows(
            matrix,
            alignment_config=alignment_config,
            production_decisions=production_decisions,
        )
    ]


def product_matrix_identity_rows(
    matrix: AlignmentMatrix,
    *,
    alignment_config: AlignmentConfig | None = None,
    production_decisions: ProductionDecisionSet | None = None,
) -> list[dict[str, object]]:
    return [
        row.identity
        for row in build_product_matrix_rows(
            matrix,
            alignment_config=alignment_config,
            production_decisions=production_decisions,
        )
    ]


def _identity_row(
    cluster: Any,
    *,
    matrix_row_index: int,
    product_cells: tuple[ProductCellAssignment, ...],
    fallback_cluster: Any | None = None,
) -> dict[str, object]:
    cluster_id = _product_identity_id(cluster, fallback_cluster=fallback_cluster)
    default_row_identity_basis = (
        "split_peak_hypothesis"
        if fallback_cluster is not None
        else "no_split_peak_hypothesis"
    )
    row_identity_basis = str(
        getattr(cluster, "row_identity_basis", default_row_identity_basis)
    )
    split_evaluation_status = str(
        getattr(
            cluster,
            "split_evaluation_status",
            (
                "complete_product_ready_split"
                if row_identity_basis == "split_peak_hypothesis"
                else "complete_no_product_ready_split"
            ),
        )
    )
    projection_status = str(getattr(cluster, "projection_status", "not_projection"))
    _validate_identity_tokens(
        cluster_id,
        row_identity_basis=row_identity_basis,
        split_evaluation_status=split_evaluation_status,
        projection_status=projection_status,
    )
    source_ids = _source_feature_family_ids(
        cluster,
        fallback=_source_family_fallback(cluster, fallback_cluster),
        require_explicit=fallback_cluster is not None,
    )
    _validate_single_source_feature_family(cluster_id, source_ids)
    mz = _family_center_mz(cluster, fallback=fallback_cluster)
    rt, rt_basis = _center_rt(cluster, product_cells, fallback_cluster=fallback_cluster)
    child_peak_hypothesis_ids = _child_peak_hypothesis_ids(cluster)
    if child_peak_hypothesis_ids:
        raise ValueError(
            f"{cluster_id}: parent aggregate product row cannot write beside "
            f"child hypotheses {child_peak_hypothesis_ids}"
        )
    accepted_samples = {
        cell.sample_stem
        for cell, decision in product_cells
        if decision.write_matrix_value
    }
    return {
        "identity_schema_version": MATRIX_IDENTITY_SCHEMA_VERSION,
        "matrix_row_index": matrix_row_index,
        "Mz": mz,
        "RT": rt,
        "peak_hypothesis_id": cluster_id,
        "row_identity_basis": row_identity_basis,
        "split_evaluation_status": split_evaluation_status,
        "projection_status": projection_status,
        "source_feature_family_ids": ";".join(source_ids),
        "source_feature_family_count": len(source_ids),
        "center_mz_basis": str(
            getattr(cluster, "center_mz_basis", "source_family_center_mz")
        ),
        "center_rt_basis": str(getattr(cluster, "center_rt_basis", rt_basis)),
        "center_weight_basis": str(
            getattr(cluster, "center_weight_basis", "primary_matrix_area")
        ),
        "accepted_cell_count": len(product_cells),
        "accepted_sample_count": len(accepted_samples),
        "evidence_status": str(getattr(cluster, "evidence_status", ""))
        or (
            "product_matrix_split_identity_complete"
            if row_identity_basis == "split_peak_hypothesis"
            else "product_matrix_identity_complete"
        ),
        "parent_peak_hypothesis_id": str(
            getattr(cluster, "parent_peak_hypothesis_id", "")
        ),
        "child_peak_hypothesis_ids": child_peak_hypothesis_ids,
        MATRIX_ROW_BINDING_COLUMN: "",
    }


def _validate_identity_tokens(
    cluster_id: str,
    *,
    row_identity_basis: str,
    split_evaluation_status: str,
    projection_status: str,
) -> None:
    if row_identity_basis in _FORBIDDEN_ROW_IDENTITY_BASIS:
        raise ValueError(
            f"{cluster_id}: product matrix row cannot use {row_identity_basis}"
        )
    if row_identity_basis not in _ALLOWED_ROW_IDENTITY_BASIS:
        raise ValueError(
            f"{cluster_id}: unsupported product row_identity_basis {row_identity_basis}"
        )
    if split_evaluation_status not in _ALLOWED_SPLIT_EVALUATION_STATUS:
        raise ValueError(
            f"{cluster_id}: unsupported product split_evaluation_status "
            f"{split_evaluation_status}"
        )
    if projection_status != "not_projection":
        raise ValueError(
            f"{cluster_id}: product projection_status must be not_projection, "
            f"got {projection_status}"
        )
    if (
        row_identity_basis == "no_split_peak_hypothesis"
        and split_evaluation_status != "complete_no_product_ready_split"
    ):
        raise ValueError(
            f"{cluster_id}: no-split product row requires "
            "complete_no_product_ready_split"
        )
    if (
        row_identity_basis == "split_peak_hypothesis"
        and split_evaluation_status != "complete_product_ready_split"
    ):
        raise ValueError(
            f"{cluster_id}: split product row requires complete_product_ready_split"
        )


def _product_cells(
    cluster_id: str,
    cells: tuple[AlignedCell, ...],
    decisions: ProductionDecisionSet,
) -> tuple[ProductCellAssignment, ...]:
    product_cells = []
    for cell in cells:
        decision = decisions.cell(cluster_id, cell.sample_stem)
        if decision.write_matrix_value:
            product_cells.append((cell, decision))
    return tuple(product_cells)


def _hypothesis_product_cells(
    cluster_id: str,
    cells: tuple[ProductCellAssignment, ...],
    decisions: ProductionDecisionSet,
    hypothesis: Any,
) -> tuple[ProductCellAssignment, ...]:
    sample_stems = _hypothesis_sample_stems(hypothesis)
    product_cells = []
    for cell, _parent_decision in cells:
        if cell.sample_stem not in sample_stems:
            continue
        decision = decisions.cell(cluster_id, cell.sample_stem)
        if decision.write_matrix_value:
            product_cells.append((cell, decision))
    return tuple(product_cells)


def _split_hypothesis_assignments(
    cluster_id: str,
    parent_product_cells: tuple[ProductCellAssignment, ...],
    decisions: ProductionDecisionSet,
    hypotheses: tuple[Any, ...],
) -> tuple[SplitHypothesisAssignment, ...]:
    assignments: list[SplitHypothesisAssignment] = []
    cell_claims: dict[tuple[str, str], str] = {}
    local_hypothesis_ids: set[str] = set()

    for hypothesis in hypotheses:
        peak_hypothesis_id = _split_peak_hypothesis_id(
            hypothesis,
            source_family_id=cluster_id,
        )
        if peak_hypothesis_id in local_hypothesis_ids:
            raise ValueError(
                f"{cluster_id}: duplicate product peak_hypothesis_id "
                f"{peak_hypothesis_id}"
            )
        local_hypothesis_ids.add(peak_hypothesis_id)
        product_cells = _hypothesis_product_cells(
            cluster_id,
            parent_product_cells,
            decisions,
            hypothesis,
        )
        if not product_cells:
            raise ValueError(
                f"{peak_hypothesis_id}: split product hypothesis has no accepted "
                "product cells"
            )
        for cell, _decision in product_cells:
            claim_key = _product_cell_claim_key(cell)
            previous = cell_claims.setdefault(claim_key, peak_hypothesis_id)
            if previous != peak_hypothesis_id:
                raise ValueError(
                    f"{cluster_id}: product cell {cell.sample_stem} claimed by "
                    "multiple peak_hypothesis_id rows: "
                    f"{previous};{peak_hypothesis_id}"
                )
        assignments.append((peak_hypothesis_id, hypothesis, product_cells))

    parent_claims = {_product_cell_claim_key(cell) for cell, _ in parent_product_cells}
    missing_claims = tuple(sorted(parent_claims - set(cell_claims)))
    if missing_claims:
        missing_samples = ";".join(sample for _cluster_id, sample in missing_claims)
        raise ValueError(
            f"{cluster_id}: accepted product cells not assigned to split "
            f"hypothesis rows: {missing_samples}"
        )
    return tuple(assignments)


def _sample_values_for_product_cells(
    product_cells: tuple[ProductCellAssignment, ...],
    sample_order: tuple[str, ...],
) -> dict[str, str]:
    by_sample = {cell.sample_stem: decision for cell, decision in product_cells}
    return {
        sample_stem: production_matrix_area(by_sample.get(sample_stem))
        for sample_stem in sample_order
    }


def _claim_peak_hypothesis_id(
    peak_hypothesis_id: str,
    seen_peak_hypothesis_ids: dict[str, str],
    *,
    source_family_id: str,
) -> None:
    previous = seen_peak_hypothesis_ids.setdefault(
        peak_hypothesis_id,
        source_family_id,
    )
    if previous != source_family_id:
        raise ValueError(
            f"{peak_hypothesis_id}: product peak_hypothesis_id cannot collapse "
            f"multiple source families: {previous};{source_family_id}"
        )


def _claim_source_candidates(
    product_cells: tuple[ProductCellAssignment, ...],
    seen_source_candidates: dict[str, str],
    *,
    row_owner_id: str,
) -> None:
    for cell, _decision in product_cells:
        if cell.source_candidate_id is None:
            continue
        previous = seen_source_candidates.setdefault(
            cell.source_candidate_id,
            row_owner_id,
        )
        if previous != row_owner_id:
            raise ValueError(
                "source peak cannot contribute to multiple product "
                f"hypothesis rows: {cell.source_candidate_id}"
            )


def _product_cell_claim_key(cell: AlignedCell) -> tuple[str, str]:
    return (cell.cluster_id, cell.sample_stem)


def _explicit_peak_hypotheses(cluster: Any) -> tuple[Any, ...]:
    raw_value = getattr(cluster, "peak_hypotheses", ())
    if raw_value is None:
        return ()
    if isinstance(raw_value, str):
        raise ValueError(
            f"{row_id(cluster)}: peak_hypotheses must be explicit row objects"
        )
    return tuple(raw_value)


def _hypothesis_sample_stems(hypothesis: Any) -> frozenset[str]:
    raw_value = (
        getattr(hypothesis, "sample_stems", None)
        or getattr(hypothesis, "accepted_sample_stems", None)
        or getattr(hypothesis, "sample_ids", None)
    )
    values = frozenset(_string_values(raw_value))
    if not values:
        peak_hypothesis_id = _peak_hypothesis_id(hypothesis, fallback="<unknown>")
        raise ValueError(
            f"{peak_hypothesis_id}: split product hypothesis requires sample_stems"
        )
    return values


def _source_feature_family_ids(
    cluster: Any,
    *,
    fallback: str,
    require_explicit: bool = False,
) -> tuple[str, ...]:
    raw_value = getattr(cluster, "source_feature_family_ids", None)
    if raw_value is None:
        if require_explicit:
            peak_hypothesis_id = _peak_hypothesis_id(cluster, fallback="<unknown>")
            raise ValueError(
                f"{peak_hypothesis_id}: split product hypothesis requires "
                "source_feature_family_ids"
            )
        return (fallback,)
    values = tuple(_string_values(raw_value))
    if require_explicit and not values:
        peak_hypothesis_id = _peak_hypothesis_id(cluster, fallback="<unknown>")
        raise ValueError(
            f"{peak_hypothesis_id}: split product hypothesis requires "
            "source_feature_family_ids"
        )
    return tuple(dict.fromkeys(values)) or (fallback,)


def _validate_single_source_feature_family(
    peak_hypothesis_id: str,
    source_ids: tuple[str, ...],
) -> None:
    if len(source_ids) == 1:
        return
    raise ValueError(
        f"{peak_hypothesis_id}: product matrix row requires exactly one "
        f"source_feature_family_id, got {';'.join(source_ids)}"
    )


def _source_family_fallback(cluster: Any, fallback_cluster: Any | None) -> str:
    if fallback_cluster is not None:
        return row_id(fallback_cluster)
    return row_id(cluster)


def _float_identity_value(value: object, *, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError(f"{name} must be numeric")


def _child_peak_hypothesis_ids(cluster: Any) -> str:
    raw_value = getattr(cluster, "child_peak_hypothesis_ids", "")
    if isinstance(raw_value, str):
        return raw_value
    return ";".join(str(part) for part in raw_value)


def _family_center_mz(row: Any, *, fallback: Any | None = None) -> float:
    if hasattr(row, "family_center_mz"):
        return float(row.family_center_mz)
    if hasattr(row, "mz"):
        return float(row.mz)
    if fallback is not None:
        return _family_center_mz(fallback)
    return float(row.cluster_center_mz)


def _family_center_rt(row: Any, *, fallback: Any | None = None) -> float:
    if hasattr(row, "family_center_rt"):
        return float(row.family_center_rt)
    if hasattr(row, "rt"):
        return float(row.rt)
    if fallback is not None:
        return _family_center_rt(fallback)
    return float(row.cluster_center_rt)


def _center_rt(
    cluster: Any,
    product_cells: tuple[ProductCellAssignment, ...],
    *,
    fallback_cluster: Any | None = None,
) -> tuple[float, str]:
    if type(cluster) is FinalizedUntargetMatrixRow:
        return cluster.family_center_rt, cluster.center_rt_basis
    weighted_sum = 0.0
    weight_sum = 0.0
    for cell, decision in product_cells:
        if cell.apex_rt is None or decision.matrix_value is None:
            continue
        if not math.isfinite(cell.apex_rt):
            continue
        weight = decision.matrix_value
        if not math.isfinite(weight) or weight <= 0:
            continue
        weighted_sum += cell.apex_rt * weight
        weight_sum += weight
    if weight_sum > 0:
        return weighted_sum / weight_sum, "accepted_cell_area_weighted_apex_rt"
    return (
        _family_center_rt(cluster, fallback=fallback_cluster),
        "source_family_center_rt",
    )


def _product_identity_id(row: Any, *, fallback_cluster: Any | None = None) -> str:
    if fallback_cluster is not None:
        direct_peak_hypothesis_id = _direct_peak_hypothesis_id(row)
        if direct_peak_hypothesis_id:
            return direct_peak_hypothesis_id
        raise ValueError(
            f"{row_id(fallback_cluster)}: split product hypothesis requires "
            "peak_hypothesis_id"
        )
    peak_hypothesis_id = _product_peak_hypothesis_id(row)
    if peak_hypothesis_id:
        return peak_hypothesis_id
    return row_id(row)


def _split_peak_hypothesis_id(row: Any, *, source_family_id: str) -> str:
    peak_hypothesis_id = _direct_peak_hypothesis_id(row)
    if peak_hypothesis_id:
        return peak_hypothesis_id
    raise ValueError(
        f"{source_family_id}: split product hypothesis requires peak_hypothesis_id"
    )


def _product_peak_hypothesis_id(row: Any) -> str:
    return _direct_peak_hypothesis_id(row) or _group_hypothesis_id(row)


def _direct_peak_hypothesis_id(row: Any) -> str:
    value = getattr(row, "peak_hypothesis_id", "")
    if value:
        return str(value)
    return ""


def _group_hypothesis_id(row: Any) -> str:
    value = getattr(row, "group_hypothesis_id", "")
    if value:
        return str(value)
    return ""


def _peak_hypothesis_id(row: Any, *, fallback: str) -> str:
    return _product_peak_hypothesis_id(row) or fallback


def _string_values(raw_value: object) -> tuple[str, ...]:
    if raw_value is None:
        return ()
    if isinstance(raw_value, str):
        return tuple(part for part in raw_value.split(";") if part)
    if isinstance(raw_value, Iterable):
        return tuple(str(part) for part in raw_value if str(part))
    return (str(raw_value),)


def formatted_identity_rows(
    matrix: AlignmentMatrix,
    *,
    alignment_config: AlignmentConfig | None = None,
    production_decisions: ProductionDecisionSet | None = None,
) -> list[dict[str, str]]:
    product_rows = build_product_matrix_rows(
        matrix,
        alignment_config=alignment_config,
        production_decisions=production_decisions,
    )
    formatted_rows = [
        {
            column: (
                canonical_mz_text(row.get(column, ""))
                if column == "Mz"
                else format_value(row.get(column, ""))
            )
            for column in ALIGNMENT_MATRIX_IDENTITY_COLUMNS
        }
        for row in (product_row.identity for product_row in product_rows)
    ]
    public_table = serialize_public_matrix_table(
        (
            {
                "Mz": row.mz,
                "RT": row.rt,
                **row.sample_values,
            }
            for row in product_rows
        ),
        sample_columns=matrix.sample_order,
    )
    return list(
        bind_matrix_identity_rows(
            matrix_header=public_table.header,
            matrix_rows=public_table.rows,
            identity_columns=ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
            identity_rows=formatted_rows,
        )
    )
