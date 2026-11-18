"""Split one Discovery batch into its declared batches.

Every batch is aligned alone, as if it were the only input; the batch
correspondence is the one step that relates them. Without sample metadata
the RAW directory is one batch.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from xic_extractor.acquisition.raw_paths import existing_raw_paths
from xic_extractor.discovery.csv_reader import DiscoveryBatchInput
from xic_extractor.raw_reader import read_raw_injection_header
from xic_extractor.sample_metadata import SampleMetadata


def split_discovery_batch(
    batch: DiscoveryBatchInput,
    sample_metadata: tuple[SampleMetadata, ...] | None,
) -> tuple[tuple[str, DiscoveryBatchInput], ...]:
    """Each declared batch ID with its own samples; one entry for one batch."""
    batch_by_stem = {row.raw_stem: row.batch_id for row in sample_metadata or ()}
    stems_by_batch: dict[str, list[str]] = {}
    for stem in batch.sample_order:
        stems_by_batch.setdefault(batch_by_stem.get(stem, ""), []).append(stem)
    if len(stems_by_batch) <= 1:
        return ((next(iter(stems_by_batch), ""), batch),)
    return tuple(
        (
            batch_id,
            replace(
                batch,
                sample_order=tuple(stems),
                peak_csvs={stem: batch.peak_csvs[stem] for stem in stems},
                raw_files={stem: batch.raw_files[stem] for stem in stems},
                event_csvs={stem: batch.event_csvs[stem] for stem in stems},
                peak_receipts={stem: batch.peak_receipts[stem] for stem in stems},
            ),
        )
        for batch_id, stems in sorted(stems_by_batch.items())
    )


def latest_acquisition_reader(
    batches: Mapping[str, DiscoveryBatchInput], *, raw_dir: Path, dll_dir: Path
) -> Callable[[str], datetime]:
    """Read a batch's latest RAW acquisition time when its size ties another's."""

    def latest(batch_id: str) -> datetime:
        batch = batches[batch_id]
        paths = existing_raw_paths(
            sample_order=batch.sample_order,
            raw_files=batch.raw_files,
            raw_dir=raw_dir,
        )
        return max(
            read_raw_injection_header(path, dll_dir).creation_date
            for path in paths.values()
        )

    return latest
