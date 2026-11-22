"""Local developer checkpoints of the exact inputs at the formal Backfill boundary."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.diagnostics.matrix_replay import CompletedMatrixCells
from xic_extractor.diagnostics.stage_snapshot import load_snapshot, save_snapshot
from xic_extractor.execution.workers import WorkerAdmissionPool
from xic_extractor.output.alignment.pipeline_outputs import new_alignment_artifacts
from xic_extractor.output.alignment.tsv_writer import cell_status
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.execution import FrozenBackfillPartition
from xic_extractor.untargeted_execution.partition_composition import (
    compose_untargeted_partition,
)

_ROSTER = "samples.pickle.gz"


def _partition(directory: Path, index: int) -> Path:
    return directory / f"{index}.pickle.gz"


class BackfillCheckpoint:
    """The roster, written only when the capture completes, marks it usable."""

    def __init__(self, directory: Path):
        self.directory = directory
        self._count = 0
        self._samples: dict[str, dict[str, SampleMetadata]] = {}

    def __enter__(self) -> BackfillCheckpoint:
        self.directory.mkdir(parents=True, exist_ok=False)
        return self

    def record_samples(self, samples: tuple[SampleMetadata, ...]) -> None:
        """A batch can have canonical samples without any Backfill partitions."""
        for sample in samples:
            self._samples.setdefault(sample.batch_id, {})[sample.raw_stem] = sample

    def append(self, partition: FrozenBackfillPartition) -> None:
        save_snapshot(_partition(self.directory, self._count), partition)
        self._count += 1

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            save_snapshot(self.directory / _ROSTER, (self._count, self._samples))


def _partitions(directory: Path, count: int) -> Iterator[FrozenBackfillPartition]:
    for index in range(count):
        yield load_snapshot(_partition(directory, index))


def replay_backfill(
    directory: Path,
    output_dir: Path,
    *,
    execution: ExecutionProfile,
    save_matrix_inputs: bool = False,
) -> Path:
    """Write diagnostic row/sample decisions, never a production Matrix."""
    output = output_dir / "backfill-cells.tsv"
    if not (directory / _ROSTER).is_file():
        raise ValueError(
            f"No completed Backfill capture from the current code in {directory}"
        )
    count, samples = load_snapshot(directory / _ROSTER)
    matrix = CompletedMatrixCells(samples) if save_matrix_inputs else None
    matrix_path = output_dir / "matrix-cells.pickle.gz"
    paths = (output, matrix_path) if matrix is not None else (output,)
    with new_alignment_artifacts(*paths) as staged:
        with staged[output].open("w", newline="", encoding="utf-8") as stream:
            rows = csv.writer(stream, delimiter="\t", lineterminator="\n")
            rows.writerow(
                (
                    "group_id",
                    "group_rt_min",
                    "sample_stem",
                    "area",
                    "decision",
                    "source",
                    "status",
                )
            )
            for index, frozen in enumerate(_partitions(directory, count)):
                with WorkerAdmissionPool(
                    max_workers=execution.alignment_workers,
                    backend=execution.backend,
                ) as pool:
                    result = frozen.execute(f"partition-{index:05d}-backfill", pool)
                if result.failure is not None:
                    raise result.failure
                composition = compose_untargeted_partition(
                    replace(frozen.batch, prepared_grouping=frozen.grouping),
                    result.sample_results,
                    alignment_config=frozen.grouping.alignment_config,
                    known_istd_reference=frozen.grouping.known_istd_reference,
                )
                if matrix is not None:
                    matrix.append(composition, frozen.batch.context.samples)
                for child in composition.children:
                    for group, cells in child.group_cells:
                        for cell in cells:
                            rows.writerow(
                                (
                                    group.group_hypothesis_id,
                                    group.group_center_rt,
                                    cell.subject.canonical_sample.raw_stem,
                                    cell.value,
                                    cell.variant,
                                    cell.source,
                                    cell_status(cell),
                                )
                            )
        if matrix is not None:
            matrix.write(staged[matrix_path])
    return output
