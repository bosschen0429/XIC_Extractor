from __future__ import annotations

from typing import Literal

from xic_extractor.alignment_explanation.storage import EXPLANATION_ARTIFACT_NAME

AlignmentOutputLevel = Literal[
    "production",
    "machine",
    "debug",
    "validation-minimal",
]

_ARTIFACTS: dict[AlignmentOutputLevel, tuple[str, ...]] = {
    "production": ("alignment_matrix.tsv", "alignment_rt_anchors.tsv"),
    "machine": (
        "alignment_matrix.tsv",
        "alignment_rt_anchors.tsv",
        "alignment_review.tsv",
    ),
    "debug": (
        "alignment_matrix.tsv",
        "alignment_rt_anchors.tsv",
        "alignment_review.tsv",
        "alignment_cells.tsv",
        EXPLANATION_ARTIFACT_NAME,
    ),
    "validation-minimal": (
        "alignment_matrix.tsv",
        "alignment_rt_anchors.tsv",
        "alignment_review.tsv",
        "alignment_backfill_cell_evidence.tsv",
        EXPLANATION_ARTIFACT_NAME,
    ),
}


def artifact_names_for_output_level(
    level: AlignmentOutputLevel,
) -> tuple[str, ...]:
    return _ARTIFACTS[level]
