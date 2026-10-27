from __future__ import annotations

from typing import Literal

# The explanation report is a per-cell projection of results already written
# elsewhere. Diagnostic and validation levels produce it; validation-minimal
# keeps the compact form so a run can be scored without a rerun.
EXPLANATION_ARTIFACT_NAME = "cid_nl_explanation.sqlite"

AlignmentOutputLevel = Literal[
    "production",
    "machine",
    "debug",
    "validation",
    "validation-minimal",
]

_ARTIFACTS: dict[AlignmentOutputLevel, tuple[str, ...]] = {
    "production": ("alignment_matrix.tsv",),
    "machine": (
        "alignment_matrix.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
    ),
    "debug": (
        "alignment_matrix.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
        "alignment_cells.tsv",
        EXPLANATION_ARTIFACT_NAME,
    ),
    "validation": (
        "alignment_matrix.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
        "alignment_cells.tsv",
        EXPLANATION_ARTIFACT_NAME,
    ),
    "validation-minimal": (
        "alignment_matrix.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
        "alignment_backfill_cell_evidence.tsv",
        EXPLANATION_ARTIFACT_NAME,
    ),
}


def artifact_names_for_output_level(
    level: AlignmentOutputLevel,
) -> tuple[str, ...]:
    return _ARTIFACTS[level]
