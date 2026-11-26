"""Read published Matrix cells and their retained per-sample evidence."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .reader import (
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
    identity = _read_tsv(alignment_dir / "alignment_review.tsv")
    rows = {
        record["feature_id"]: (
            int(record["matrix_row_index"]),
            float(record["group_center_mz"]),
        )
        for record in identity
        if record["matrix_row_index"]
    }
    explanation = alignment_dir / EXPLANATION_ARTIFACT_NAME
    cells = []
    for group, raw_stem, value, origin, apex in read_row_cell_values(explanation, rows):
        if value is None:
            continue
        row, row_mz = rows[group]
        cells.append(
            PublishedCell(
                row=row,
                row_mz=row_mz,
                sample=raw_stem,
                origin=origin,
                apex_rt_min=apex,
                feature_id=group,
                value=float(value),
            )
        )
    return tuple(cells)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))
