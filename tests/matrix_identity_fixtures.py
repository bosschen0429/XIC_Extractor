from __future__ import annotations

from xic_extractor.alignment.matrix_output_contract import (
    LEGACY_ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
    LEGACY_MATRIX_IDENTITY_SCHEMA_VERSION,
)


def legacy_matrix_identity_row(
    *,
    index: int | str,
    mz: str,
    rt: str,
    peak_hypothesis_id: str,
    source_feature_family_ids: str | None = None,
    row_identity_basis: str = "no_split_peak_hypothesis",
) -> dict[str, str]:
    """Build a structurally complete historical v1 identity test row."""

    source_ids = source_feature_family_ids or peak_hypothesis_id
    source_count = len(tuple(part for part in source_ids.split(";") if part))
    row = {column: "" for column in LEGACY_ALIGNMENT_MATRIX_IDENTITY_COLUMNS}
    row.update(
        {
            "identity_schema_version": LEGACY_MATRIX_IDENTITY_SCHEMA_VERSION,
            "matrix_row_index": str(index),
            "Mz": mz,
            "RT": rt,
            "peak_hypothesis_id": peak_hypothesis_id,
            "row_identity_basis": row_identity_basis,
            "split_evaluation_status": "legacy_fixture_complete",
            "projection_status": "not_projection",
            "source_feature_family_ids": source_ids,
            "source_feature_family_count": str(source_count),
            "center_mz_basis": "legacy_fixture_mz",
            "center_rt_basis": "legacy_fixture_rt",
            "center_weight_basis": "legacy_fixture",
            "accepted_cell_count": "0",
            "accepted_sample_count": "0",
            "evidence_status": "legacy_fixture_complete",
        }
    )
    return row
