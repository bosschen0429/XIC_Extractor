"""Local developer checkpoints of the exact inputs at the formal Backfill boundary."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from xic_extractor.configuration.atomic_write import atomic_write_text
from xic_extractor.execution._serialization import (
    owner_pickle_dumps,
    owner_pickle_loads,
)
from xic_extractor.execution.workers import WorkerAdmissionPool
from xic_extractor.output.alignment.tsv_writer import cell_status
from xic_extractor.untargeted_execution.execution import FrozenBackfillPartition
from xic_extractor.untargeted_execution.partition_composition import (
    compose_untargeted_partition,
)


def _receipt(directory: Path) -> Path:
    # The decoded owner graph can execute code. Never accept a digest from the
    # input itself: only this user's successful local capture grants trust.
    identity = hashlib.sha256(
        os.fsencode(os.path.normcase(directory.resolve()))
    ).hexdigest()
    return Path.home() / ".xic-extractor" / "backfill-v1" / identity


class BackfillCheckpoint:
    def __init__(self, directory: Path):
        self.directory = directory
        self._digests: list[str] = []

    def __enter__(self) -> BackfillCheckpoint:
        self.directory.mkdir(parents=True, exist_ok=False)
        _receipt(self.directory).unlink(missing_ok=True)
        return self

    def append(self, partition: FrozenBackfillPartition) -> None:
        data = owner_pickle_dumps(partition, ())
        (self.directory / f"{len(self._digests)}.bin").write_bytes(data)
        self._digests.append(hashlib.sha256(data).hexdigest())

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            path = _receipt(self.directory)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write_text(path, json.dumps(self._digests))


def _partitions(directory: Path) -> Iterator[FrozenBackfillPartition]:
    receipt = _receipt(directory)
    if not receipt.is_file():
        raise ValueError(f"No completed local Backfill capture for {directory}")
    for index, digest in enumerate(json.loads(receipt.read_text(encoding="utf-8"))):
        data = (directory / f"{index}.bin").read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(
                f"Backfill checkpoint partition {index} differs from its local capture"
            )
        yield cast(FrozenBackfillPartition, owner_pickle_loads(data, ()))


def replay_backfill(directory: Path, output_dir: Path) -> Path:
    """Write diagnostic row/sample decisions, never a production Matrix."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "backfill-cells.tsv"
    if output.exists():
        raise FileExistsError(output)
    stream = NamedTemporaryFile(
        mode="w", newline="", encoding="utf-8", dir=output_dir, delete=False
    )
    temporary = Path(stream.name)
    try:
        with stream:
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
            for index, frozen in enumerate(_partitions(directory)):
                with WorkerAdmissionPool() as pool:
                    result = frozen.execute(f"partition-{index:05d}-backfill", pool)
                if result.failure is not None:
                    raise result.failure
                composition = compose_untargeted_partition(
                    replace(frozen.batch, prepared_grouping=frozen.grouping),
                    result.sample_results,
                    alignment_config=frozen.grouping.alignment_config,
                    known_istd_reference=frozen.grouping.known_istd_reference,
                )
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
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
