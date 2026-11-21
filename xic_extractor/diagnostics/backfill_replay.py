"""Local developer checkpoints of the exact inputs at the formal Backfill boundary."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

from xic_extractor.configuration.atomic_write import atomic_write_text
from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.diagnostics.matrix_replay import CompletedMatrixCells
from xic_extractor.execution._serialization import (
    owner_pickle_dumps,
    owner_pickle_loads,
)
from xic_extractor.execution.workers import WorkerAdmissionPool
from xic_extractor.output.alignment.pipeline_outputs import new_alignment_artifacts
from xic_extractor.output.alignment.tsv_writer import cell_status
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.execution import FrozenBackfillPartition
from xic_extractor.untargeted_execution.partition_composition import (
    compose_untargeted_partition,
)


def _receipt(directory: Path) -> Path:
    identity = hashlib.sha256(
        os.fsencode(os.path.normcase(directory.resolve()))
    ).hexdigest()
    return Path.home() / ".xic-extractor" / "backfill-v1" / identity


class BackfillCheckpoint:
    def __init__(self, directory: Path):
        self.directory = directory
        self._digests: list[str] = []
        self._samples: dict[str, dict[str, int | None]] = {}

    def __enter__(self) -> BackfillCheckpoint:
        self.directory.mkdir(parents=True, exist_ok=False)
        _receipt(self.directory).unlink(missing_ok=True)
        return self

    def record_samples(self, samples: tuple[SampleMetadata, ...]) -> None:
        """A batch can have canonical samples without any Backfill partitions."""
        for sample in samples:
            self._samples.setdefault(sample.batch_id, {})[sample.raw_stem] = (
                sample.injection_order
            )

    def append(self, partition: FrozenBackfillPartition) -> None:
        data = owner_pickle_dumps(partition, ())
        (self.directory / f"{len(self._digests)}.bin").write_bytes(data)
        self._digests.append(hashlib.sha256(data).hexdigest())

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            path = _receipt(self.directory)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write_text(
                path, json.dumps({"digests": self._digests, "samples": self._samples})
            )


def _load_receipt(
    directory: Path,
) -> tuple[list[str], dict[str, dict[str, int | None]] | None]:
    receipt = _receipt(directory)
    if not receipt.is_file():
        raise ValueError(f"No completed local Backfill capture for {directory}")
    capture = json.loads(receipt.read_text(encoding="utf-8"))
    if isinstance(capture, list):
        return capture, None
    return capture["digests"], capture["samples"]


def _partitions(
    directory: Path, digests: list[str]
) -> Iterator[FrozenBackfillPartition]:
    for index, digest in enumerate(digests):
        data = (directory / f"{index}.bin").read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(
                f"Backfill checkpoint partition {index} differs from its local capture"
            )
        yield cast(FrozenBackfillPartition, owner_pickle_loads(data, ()))


def replay_backfill(
    directory: Path,
    output_dir: Path,
    *,
    execution: ExecutionProfile,
    save_matrix_inputs: bool = False,
) -> Path:
    """Write diagnostic row/sample decisions, never a production Matrix."""
    output = output_dir / "backfill-cells.tsv"
    digests, samples = _load_receipt(directory)
    matrix = None
    if save_matrix_inputs:
        if samples is None:
            raise ValueError(
                "Recapture grouping with a sample roster to export Matrix inputs"
            )
        matrix = CompletedMatrixCells(samples)
    matrix_path = output_dir / "matrix-cells.json"
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
            for index, frozen in enumerate(_partitions(directory, digests)):
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
