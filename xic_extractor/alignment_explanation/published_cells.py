"""Read published Matrix cells and their retained per-sample evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.output.alignment.matrix_output_contract import (
    MATRIX_NAME,
    RUN_METADATA_NAME,
    locate_matrix_identity,
    read_matrix_identities,
)
from xic_extractor.tabular_io import read_tsv_with_header_snapshot

from .reader import (
    read_explanation_metadata,
    read_row_cell_values,
)
from .storage import EXPLANATION_ARTIFACT_NAME


@dataclass(frozen=True)
class PublishedCell:
    row: int
    row_mz: float
    sample: str
    origin: str
    apex_rt_min: float | None
    feature_id: str = ""
    value: float | None = None


def read_published_cells(alignment_dir: Path) -> tuple[PublishedCell, ...]:
    """Published Matrix cells with their value and the apex each one quantifies.

    Values and selected apices come directly from the explanation's saved cell
    decisions, independently of optional ledgers and observation ordering.
    """
    run_path = alignment_dir / RUN_METADATA_NAME
    run = json.loads(run_path.read_text(encoding="utf-8")) if run_path.is_file() else {}
    header, matrix, digest = read_tsv_with_header_snapshot(
        alignment_dir / MATRIX_NAME, required_columns=("Mz", "RT"), encoding="utf-8-sig"
    )
    identities = read_matrix_identities(
        locate_matrix_identity(alignment_dir, run),
        matrix_header=header,
        matrix_rows=matrix,
        matrix_digest=digest,
    )
    rows = {key: (index, mz) for index, (key, mz) in enumerate(identities, 1)}
    explanation = alignment_dir / EXPLANATION_ARTIFACT_NAME
    summary = read_explanation_metadata(explanation)
    samples = {
        sample["sample_key"]: sample["sample_stem"] for sample in summary["samples"]
    }
    cells = []
    for group, sample_key, value, origin, apex in read_row_cell_values(
        explanation, rows
    ):
        if value is None:
            continue
        row, row_mz = rows[group]
        sample = samples[sample_key]
        cells.append(
            PublishedCell(
                row=row,
                row_mz=row_mz,
                sample=sample,
                origin=origin,
                apex_rt_min=apex,
                feature_id=group,
                value=float(value),
            )
        )
    return tuple(cells)
