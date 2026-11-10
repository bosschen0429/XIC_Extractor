"""Column contract for the persisted Backfill cell evidence TSV."""

from __future__ import annotations

ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION = (
    "alignment_backfill_cell_query_evidence"
)
ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS = (
    "schema_version",
    "feature_id",
    "sample_stem",
    "status",
    "include_in_primary_matrix",
    "identity_decision",
    "row_flags",
    "area",
    "primary_matrix_area",
    "primary_matrix_area_source",
    "primary_matrix_area_reason",
    "apex_rt",
    "height",
    "peak_start_rt",
    "peak_end_rt",
    "source_candidate_id",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "reason",
    "backfill_query_rt_min",
    "backfill_query_rt_max",
)

__all__ = (
    "ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS",
    "ALIGNMENT_BACKFILL_CELL_EVIDENCE_SCHEMA_VERSION",
)
