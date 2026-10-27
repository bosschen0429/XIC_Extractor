from __future__ import annotations

from collections.abc import Mapping

from xic_extractor.output.alignment.matrix_output_contract import (
    bind_matrix_review_row,
    public_matrix_mz_text,
)


def current_matrix_review_row(
    *,
    index: int,
    mz: str,
    rt: str,
    feature_id: str,
    sample_values: Mapping[str, str],
) -> dict[str, str]:
    """Build persisted Review facts bound to supplied Matrix values."""
    matrix = {"Mz": public_matrix_mz_text(mz), "RT": rt, **sample_values}
    return bind_matrix_review_row(
        matrix_header=tuple(matrix),
        matrix_row=matrix,
        index=index,
        source_review={
            "feature_id": feature_id,
            "matrix_mz": mz,
            "matrix_rt": rt,
            "center_mz_basis": "fixture_mz",
            "accepted_cell_count": "0",
        },
    )
