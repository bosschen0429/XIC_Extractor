from __future__ import annotations

from xic_extractor.output.alignment.matrix_output_contract import (
    bind_matrix_review_row,
)


def current_matrix_review_row(
    *,
    index: int,
    mz: str,
    rt: str,
    feature_id: str,
) -> dict[str, str]:
    """Build persisted Review facts that locate one Matrix row."""
    return bind_matrix_review_row(
        index=index,
        source_review={
            "feature_id": feature_id,
            "matrix_mz": mz,
            "matrix_rt": rt,
            "center_mz_basis": "fixture_mz",
            "accepted_cell_count": "0",
        },
    )
