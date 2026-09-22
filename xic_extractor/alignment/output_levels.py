from __future__ import annotations

from typing import Literal, cast

# The explanation report is a per-cell projection of results already written
# elsewhere. It is diagnostic, so only the diagnostic levels produce it.
EXPLANATION_ARTIFACT_NAME = "cid_nl_explanation.sqlite"

AlignmentOutputLevel = Literal[
    "production",
    "machine",
    "debug",
    "validation",
    "validation-minimal",
]

_ARTIFACTS: dict[AlignmentOutputLevel, tuple[str, ...]] = {
    "production": (
        "alignment_matrix.tsv",
    ),
    "machine": (
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
    ),
    "debug": (
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
        "alignment_cells.tsv",
        "alignment_matrix_status.tsv",
        "event_to_ms1_owner.tsv",
        "ambiguous_ms1_owners.tsv",
        "owner_edge_evidence.tsv",
        EXPLANATION_ARTIFACT_NAME,
    ),
    "validation": (
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
        "alignment_cells.tsv",
        "alignment_matrix_status.tsv",
        "event_to_ms1_owner.tsv",
        "ambiguous_ms1_owners.tsv",
        "owner_edge_evidence.tsv",
        EXPLANATION_ARTIFACT_NAME,
    ),
    "validation-minimal": (
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
        "alignment_backfill_cell_evidence.tsv",
    ),
}


def parse_alignment_output_level(value: str) -> AlignmentOutputLevel:
    if value not in _ARTIFACTS:
        raise ValueError(
            "output_level must be one of production, machine, debug, "
            "validation, validation-minimal",
        )
    return cast(AlignmentOutputLevel, value)


def artifact_names_for_output_level(
    level: AlignmentOutputLevel,
) -> tuple[str, ...]:
    return _ARTIFACTS[level]
