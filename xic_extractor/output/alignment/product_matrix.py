from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.untargeted_finalized_matrix import (
    AlignmentMatrix,
    FinalizedUntargetMatrixRow,
)
from xic_extractor.output.alignment.output_rows import format_float


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
    source_candidate_owners: dict[str, str] | None = None,
) -> tuple[ProductMatrixRow, ...]:
    rows: list[ProductMatrixRow] = []
    seen_source_candidates: dict[str, str] = (
        {} if source_candidate_owners is None else source_candidate_owners
    )

    for cluster in matrix.clusters:
        if cluster.destination != "matrix":
            continue
        _claim_source_candidates(cluster, seen_source_candidates)
        projection = _matrix_review_projection(
            cluster,
            matrix_row_index=len(rows) + 1,
        )
        sample_values = {
            sample_stem: "" if cell.value is None else format_float(cell.value)
            for sample_stem, cell in zip(
                matrix.sample_order, cluster.cells, strict=True
            )
        }
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


def product_matrix_tsv_rows(matrix: AlignmentMatrix) -> list[dict[str, object]]:
    return [
        {"Mz": row.mz, "RT": row.rt, **row.sample_values}
        for row in build_product_matrix_rows(matrix)
    ]


def _matrix_review_projection(
    cluster: FinalizedUntargetMatrixRow,
    *,
    matrix_row_index: int,
) -> dict[str, object]:
    mz = float(cluster.family_center_mz)
    rt = float(cluster.family_center_rt)
    return {
        "feature_id": cluster.feature_id,
        "matrix_row_index": matrix_row_index,
        "matrix_mz": mz,
        "matrix_rt": rt,
        "center_mz_basis": cluster.center_mz_basis,
        "accepted_cell_count": sum(cell.value is not None for cell in cluster.cells),
    }


def _claim_source_candidates(
    cluster: FinalizedUntargetMatrixRow,
    seen_source_candidates: dict[str, str],
) -> None:
    for _sample_stem, candidate_id in cluster.source_candidate_ids:
        previous = seen_source_candidates.setdefault(candidate_id, cluster.feature_id)
        if previous != cluster.feature_id:
            raise ValueError(
                "source peak cannot contribute to multiple product "
                f"hypothesis rows: {candidate_id}"
            )


def _float_identity_value(value: object, *, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError(f"{name} must be numeric")
