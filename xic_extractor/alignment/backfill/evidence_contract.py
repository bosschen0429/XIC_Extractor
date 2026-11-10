"""Column contract for the persisted Backfill cell evidence TSV."""

from __future__ import annotations

ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS = (
    "feature_id",
    "sample_stem",
    "status",
    "area",
    "primary_matrix_area_source",
    "apex_rt",
    "height",
    "peak_start_rt",
    "peak_end_rt",
    "source_candidate_id",
    "backfill_query_rt_min",
    "backfill_query_rt_max",
)

__all__ = ("ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS",)
