from __future__ import annotations

from xic_extractor.output.alignment.matrix_output_contract import canonical_mz_text


def current_matrix_review_row(
    *,
    index: int,
    mz: str,
    rt: str,
    feature_id: str,
) -> dict[str, str]:
    """Build persisted Review facts that locate one Matrix row."""
    return {
        "feature_id": feature_id,
        "matrix_row_index": str(index),
        "family_center_mz": canonical_mz_text(mz),
        "family_center_rt": rt,
    }
