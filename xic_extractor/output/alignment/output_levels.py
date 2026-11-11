from __future__ import annotations

from typing import Literal

from xic_extractor.alignment_explanation.storage import EXPLANATION_ARTIFACT_NAME

# The explanation report is a per-cell projection of results already written
# elsewhere. The debug level produces it; validation-minimal
# keeps the compact form so a run can be scored without a rerun.
AlignmentOutputLevel = Literal[
    "production",
    "machine",
    "debug",
    "validation-minimal",
]

_ARTIFACTS: dict[AlignmentOutputLevel, tuple[str, ...]] = {
    "production": ("alignment_matrix.tsv",),
    "machine": (
        "alignment_matrix.tsv",
        "alignment_review.tsv",
    ),
    "debug": (
        "alignment_matrix.tsv",
        "alignment_review.tsv",
        "alignment_cells.tsv",
        EXPLANATION_ARTIFACT_NAME,
    ),
    "validation-minimal": (
        "alignment_matrix.tsv",
        "alignment_review.tsv",
        "alignment_backfill_cell_evidence.tsv",
        EXPLANATION_ARTIFACT_NAME,
    ),
}


def artifact_names_for_output_level(
    level: AlignmentOutputLevel,
) -> tuple[str, ...]:
    return _ARTIFACTS[level]
