"""Capture and replay the per-batch facts used by cross-batch correspondence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from xic_extractor.alignment.batch_correspondence import (
    GroupedBatch,
    pair_grouped_batches,
)
from xic_extractor.diagnostics.grouping_experiment_io import write_json
from xic_extractor.diagnostics.matrix_replay import save_matrix_join
from xic_extractor.diagnostics.stage_snapshot import load_snapshot, save_snapshot
from xic_extractor.output.alignment.batch_correspondence_table import (
    batch_correspondence_summary,
    write_batch_correspondence_table,
)
from xic_extractor.output.alignment.pipeline_outputs import (
    new_alignment_artifacts,
)


def save_pairing_input(
    path: Path,
    batch: GroupedBatch,
    *,
    latest_acquisition: datetime,
    precursor_tolerance_ppm: float,
) -> None:
    save_snapshot(path, (batch, latest_acquisition, precursor_tolerance_ppm))


def replay_pairing(inputs: Sequence[Path], output_dir: Path) -> Path:
    if len(inputs) < 2:
        raise ValueError("pair-batches requires at least two batch inputs")
    table = output_dir / "batch_correspondence.tsv"
    summary = output_dir / "summary.json"
    join = output_dir / "matrix-join.pickle.gz"
    with new_alignment_artifacts(join, table, summary) as paths:
        captures: list[tuple[GroupedBatch, datetime, float]] = [
            load_snapshot(path) for path in inputs
        ]
        latest = {batch.batch_id: date for batch, date, _ in captures}
        if len(latest) != len(captures):
            raise ValueError("Pairing inputs must have distinct batch IDs")
        if len({ppm for _, _, ppm in captures}) != 1:
            raise ValueError("Pairing inputs must use the same precursor_tolerance_ppm")
        if len({date.utcoffset() is None for date in latest.values()}) > 1:
            raise ValueError(
                "Pairing input acquisition times use incompatible time zones"
            )
        result, features = pair_grouped_batches(
            [batch for batch, _, _ in captures],
            latest_acquisition=latest.__getitem__,
            precursor_tolerance_ppm=captures[0][2],
        )
        save_matrix_join(paths[join], result)
        write_batch_correspondence_table(paths[table], result, features)
        write_json(
            paths[summary],
            {"batch_correspondence": batch_correspondence_summary(result)},
        )
    return table
