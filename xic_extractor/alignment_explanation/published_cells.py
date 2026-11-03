"""Read published Matrix cells and their retained per-sample evidence."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .reader import (
    read_explanation_summary,
    read_observation_apices,
    read_row_cell_values,
)
from .storage import EXPLANATION_ARTIFACT_NAME

_DIRECT_ORIGINS = frozenset({"discovery_support", "reacquired_support"})


@dataclass(frozen=True)
class PublishedCell:
    row: int
    row_mz: float
    sample: str
    direct: bool
    # None when no peak apex is recorded for the cell, as for a Backfill
    # without its ledger.
    apex_rt_min: float | None
    feature_id: str = ""
    value: float | None = None


def read_published_cells(alignment_dir: Path) -> tuple[PublishedCell, ...]:
    """Published Matrix cells with their value and the apex each one quantifies.

    Direct detections take their apex from the explanation's observations.
    Backfilled cells take it from the backfill ledger or ``alignment_cells.tsv``
    when the output level wrote one; otherwise their apex is unavailable.
    """
    identity = _read_tsv(alignment_dir / "alignment_review.tsv")
    rows = {
        record["feature_id"]: (
            int(record["matrix_row_index"]),
            float(record["matrix_mz"]),
        )
        for record in identity
        if record["matrix_row_index"]
    }
    backfill_apex: dict[tuple[str, str], float] = {}
    for name in ("alignment_backfill_cell_evidence.tsv", "alignment_cells.tsv"):
        cell_ledger = alignment_dir / name
        if cell_ledger.is_file():
            backfill_apex = {
                (record["feature_id"], record["sample_stem"]): float(record["apex_rt"])
                for record in _read_tsv(cell_ledger)
                if record["apex_rt"] and record["feature_id"] in rows
            }
            break
    explanation = alignment_dir / EXPLANATION_ARTIFACT_NAME
    summary = read_explanation_summary(explanation)
    samples = {
        sample["sample_key"]: sample["sample_stem"] for sample in summary["samples"]
    }
    observation_apex = read_observation_apices(explanation)
    cells = []
    for group, sample_key, value, origin, observations in read_row_cell_values(
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
                direct=origin in _DIRECT_ORIGINS,
                apex_rt_min=_cell_apex(
                    observations, observation_apex, backfill_apex, group, sample
                ),
                feature_id=group,
                value=float(value),
            )
        )
    return tuple(cells)


def _cell_apex(
    observations: Iterable[str],
    observation_apex: Mapping[str, float],
    backfill_apex: Mapping[tuple[str, str], float],
    group: str,
    sample: str,
) -> float | None:
    for observation in observations:
        if observation in observation_apex:
            return float(observation_apex[observation])
    return backfill_apex.get((group, sample))


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))
