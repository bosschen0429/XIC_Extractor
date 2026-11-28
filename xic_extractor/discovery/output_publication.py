"""Publish Discovery samples as part files and the batch as one SQLite file."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.configuration.mass_recalibration import (
    VENDOR_MASS_RECALIBRATION_RESULT,
    MassRecalibrationResult,
)
from xic_extractor.discovery.models import (
    DiscoveryBatchOutputs,
    DiscoveryPeak,
    EventAttachment,
)
from xic_extractor.discovery.store import (
    DISCOVERY_ARTIFACT_NAME,
    DiscoverySample,
    merge_sample_parts,
    part_path,
    write_sample_part,
)
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.processing_identity import AcquisitionSourceIdentity


@dataclass(frozen=True)
class PublishedDiscoverySample:
    """One sample's committed part file, waiting for the batch to merge it."""

    sample_index: int
    sample_stem: str
    part: Path


def publish_discovery_batch_sample(
    raw_path: Path,
    peaks: tuple[DiscoveryPeak, ...],
    events: tuple[EventAttachment, ...],
    *,
    output_dir: Path,
    sample_index: int,
    neutral_loss_by_tag: Mapping[str, float],
    acquisition_source: AcquisitionSourceIdentity,
    timing_recorder: TimingRecorder,
    mass_recalibration_result: MassRecalibrationResult = (
        VENDOR_MASS_RECALIBRATION_RESULT
    ),
) -> PublishedDiscoverySample:
    """Write one sample's part file; serial and process execution share it."""
    with timing_recorder.stage(
        "discover.write_sample",
        sample_stem=raw_path.stem,
        metrics={"peak_count": len(peaks), "event_count": len(events)},
    ):
        part = write_sample_part(
            part_path(output_dir, raw_path.stem),
            sample_index=sample_index,
            sample=DiscoverySample(
                raw_path.stem, raw_path, acquisition_source, mass_recalibration_result
            ),
            neutral_loss_by_tag=neutral_loss_by_tag,
            peaks=peaks,
            attachments=events,
        )
    return PublishedDiscoverySample(sample_index, raw_path.stem, part)


def publish_discovery_batch(
    output_dir: Path,
    samples: Sequence[PublishedDiscoverySample],
    *,
    timing_recorder: TimingRecorder,
) -> DiscoveryBatchOutputs:
    """Merge the samples' parts, in run order, into the published batch file."""
    ordered = sorted(samples, key=lambda sample: sample.sample_index)
    with timing_recorder.stage(
        "discover.publish_batch", metrics={"sample_count": len(ordered)}
    ):
        store = merge_sample_parts(
            output_dir / DISCOVERY_ARTIFACT_NAME, [sample.part for sample in ordered]
        )
    return DiscoveryBatchOutputs(
        store=store, sample_stems=tuple(sample.sample_stem for sample in ordered)
    )
