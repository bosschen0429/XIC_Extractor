from __future__ import annotations

from collections.abc import Mapping

from xic_extractor.alignment.matrix_output_contract import (
    ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
    MATRIX_IDENTITY_SCHEMA_VERSION,
    MATRIX_ROW_BINDING_COLUMN,
    canonical_mz_text,
    matrix_row_binding_sha256,
    public_matrix_mz_text,
)


def current_matrix_identity_row(
    *,
    index: int | str,
    mz: str,
    rt: str,
    peak_hypothesis_id: str,
    sample_values: Mapping[str, str],
    source_feature_family_ids: str | None = None,
    row_identity_basis: str = "no_split_peak_hypothesis",
) -> dict[str, str]:
    """Build a current identity row bound to its public Matrix values."""

    source_ids = source_feature_family_ids or peak_hypothesis_id
    source_count = len(tuple(part for part in source_ids.split(";") if part))
    row = {column: "" for column in ALIGNMENT_MATRIX_IDENTITY_COLUMNS}
    row.update(
        {
            "identity_schema_version": MATRIX_IDENTITY_SCHEMA_VERSION,
            "matrix_row_index": str(index),
            "Mz": canonical_mz_text(mz),
            "RT": rt,
            "peak_hypothesis_id": peak_hypothesis_id,
            "row_identity_basis": row_identity_basis,
            "split_evaluation_status": "fixture_complete",
            "projection_status": "not_projection",
            "source_feature_family_ids": source_ids,
            "source_feature_family_count": str(source_count),
            "center_mz_basis": "fixture_mz",
            "center_rt_basis": "fixture_rt",
            "center_weight_basis": "fixture",
            "accepted_cell_count": "0",
            "accepted_sample_count": "0",
            "evidence_status": "fixture_complete",
        }
    )
    matrix_row = {"Mz": public_matrix_mz_text(mz), "RT": rt, **sample_values}
    row[MATRIX_ROW_BINDING_COLUMN] = matrix_row_binding_sha256(
        matrix_header=tuple(matrix_row),
        matrix_row=matrix_row,
        identity_columns=ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
        identity_row=row,
    )
    return row
