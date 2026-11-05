from __future__ import annotations

import math
from dataclasses import dataclass

from xic_extractor.alignment.matrix import (
    AlignedCell,
    AlignmentMatrix,
    cells_by_cluster,
)
from xic_extractor.alignment.production_decisions import (
    ProductionCellDecision,
    ProductionDecisionSet,
)
from xic_extractor.alignment.untargeted_finalized_matrix import (
    FinalizedUntargetMatrixRow,
)
from xic_extractor.output.alignment.matrix_output_contract import (
    MATRIX_ROW_BINDING_COLUMN,
)
from xic_extractor.output.alignment.output_rows import (
    production_matrix_area,
)

ProductCellAssignment = tuple[AlignedCell, ProductionCellDecision]


@dataclass(frozen=True)
class ProductMatrixRow:
    matrix_row_index: int
    mz: float
    rt: float
    sample_values: dict[str, str]
    review_projection: dict[str, object]


def build_product_matrix_rows(
    matrix: AlignmentMatrix,
    *,
    production_decisions: ProductionDecisionSet,
    source_candidate_owners: dict[str, str] | None = None,
) -> tuple[ProductMatrixRow, ...]:
    decisions = production_decisions
    grouped_cells = cells_by_cluster(matrix)
    rows: list[ProductMatrixRow] = []
    seen_source_candidates: dict[str, str] = (
        {} if source_candidate_owners is None else source_candidate_owners
    )

    for cluster in matrix.clusters:
        cluster_id = cluster.feature_id
        row_decision = decisions.row(cluster_id)
        cluster_cells = grouped_cells.get(cluster_id, ())
        cluster_sample_stems = {cell.sample_stem for cell in cluster_cells}
        if not row_decision.include_in_primary_matrix:
            continue
        product_cells = _product_cells(cluster_id, cluster_cells, decisions)
        _claim_source_candidates(
            product_cells,
            seen_source_candidates,
            row_owner_id=cluster_id,
        )
        projection = _matrix_review_projection(
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
                mz=_float_identity_value(projection["matrix_mz"], name="Mz"),
                rt=_float_identity_value(projection["matrix_rt"], name="RT"),
                sample_values=sample_values,
                review_projection=projection,
            )
        )
    return tuple(rows)


def product_matrix_tsv_rows(
    matrix: AlignmentMatrix,
    *,
    production_decisions: ProductionDecisionSet,
) -> list[dict[str, object]]:
    return [
        {"Mz": row.mz, "RT": row.rt, **row.sample_values}
        for row in build_product_matrix_rows(
            matrix,
            production_decisions=production_decisions,
        )
    ]


def _matrix_review_projection(
    cluster: FinalizedUntargetMatrixRow,
    *,
    matrix_row_index: int,
    product_cells: tuple[ProductCellAssignment, ...],
) -> dict[str, object]:
    mz = float(cluster.family_center_mz)
    rt, _ = _center_rt(cluster, product_cells)
    return {
        "feature_id": cluster.feature_id,
        "matrix_row_index": matrix_row_index,
        "matrix_mz": mz,
        "matrix_rt": rt,
        "center_mz_basis": cluster.center_mz_basis,
        "accepted_cell_count": len(product_cells),
        MATRIX_ROW_BINDING_COLUMN: "",
    }


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


def _float_identity_value(value: object, *, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError(f"{name} must be numeric")


def _center_rt(
    cluster: FinalizedUntargetMatrixRow,
    product_cells: tuple[ProductCellAssignment, ...],
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
        float(cluster.family_center_rt),
        "source_family_center_rt",
    )
