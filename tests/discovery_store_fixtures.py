"""Build published Discovery files for tests through the production writer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xic_extractor.configuration.mass_recalibration import (
    VENDOR_MASS_RECALIBRATION_RESULT,
    MassRecalibrationResult,
)
from xic_extractor.discovery.models import (
    DiscoveryEvent,
    DiscoveryPeak,
    EventAttachment,
)
from xic_extractor.discovery.store import (
    DISCOVERY_ARTIFACT_NAME,
    DiscoverySample,
    merge_sample_parts,
    open_discovery,
    part_path,
    read_discovery_batch,
    read_events,
    read_neutral_loss_by_tag,
    read_peaks,
    read_sample_peaks,
    write_sample_part,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    build_local_acquisition_source_identity,
)

DNA_DR_LOSS_DA = 116.0474
NEUTRAL_LOSS_BY_TAG = {"DNA_dR": DNA_DR_LOSS_DA}


def acquisition_source(directory: Path, sample_stem: str) -> AcquisitionSourceIdentity:
    """A stable synthetic acquisition identity for one sample."""
    source = directory / f"{sample_stem}.acquisition"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(f"acquisition:{sample_stem}".encode())
    return build_local_acquisition_source_identity(
        source,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )


def event(
    sample_stem: str,
    *,
    scan_number: int = 1,
    rt: float = 8.5,
    precursor_mz: float = 500.0,
    neutral_loss_tag: str = "DNA_dR",
    neutral_loss_da: float = DNA_DR_LOSS_DA,
    raw_file: Path | None = None,
    **overrides: Any,
) -> DiscoveryEvent:
    product_mz = precursor_mz - neutral_loss_da
    values: dict[str, Any] = {
        "raw_file": raw_file or Path(f"{sample_stem}.raw"),
        "sample_stem": sample_stem,
        "scan_number": scan_number,
        "rt": rt,
        "precursor_mz": precursor_mz,
        "product_mz": product_mz,
        "product_intensity": 1000.0,
        "neutral_loss_tag": neutral_loss_tag,
        "configured_neutral_loss_da": neutral_loss_da,
        "observed_neutral_loss_da": precursor_mz - product_mz,
        "observed_loss_error_ppm": 0.0,
        "scan_precursor_mz": precursor_mz,
    }
    values.update(overrides)
    return DiscoveryEvent(**values)


def peak(
    sample_stem: str,
    *,
    peak_mz: float = 500.0,
    ms1_apex_rt: float = 8.5,
    neutral_loss_tag: str = "DNA_dR",
    best_ms2_scan_id: int = 1,
    status: str = "admitted",
    evidence_score: int = 80,
    peak_id: str | None = None,
    **overrides: Any,
) -> DiscoveryPeak:
    values: dict[str, Any] = {
        "peak_id": peak_id or f"{sample_stem}@mz{peak_mz:g}_rt{ms1_apex_rt:.4f}",
        "sample_stem": sample_stem,
        "peak_mz": peak_mz,
        "neutral_loss_tag": neutral_loss_tag,
        "best_ms2_scan_id": best_ms2_scan_id,
        "attached_event_count": 0,
        "ms1_apex_rt": ms1_apex_rt,
        "status": status,
        "evidence_score": evidence_score,
    }
    values.update(overrides)
    return DiscoveryPeak(**values)


@dataclass(frozen=True)
class SampleRecords:
    """One sample's peaks and events as Discovery would publish them."""

    sample_stem: str
    raw_file: Path
    peaks: Sequence[DiscoveryPeak] = ()
    attachments: Sequence[EventAttachment] = ()
    acquisition_source: AcquisitionSourceIdentity | None = None
    mass_recalibration_result: MassRecalibrationResult = field(
        default=VENDOR_MASS_RECALIBRATION_RESULT
    )


def attached(peak_: DiscoveryPeak, *events: DiscoveryEvent) -> list[EventAttachment]:
    return [EventAttachment(item, peak_id=peak_.peak_id) for item in events]


def single_peak_sample(
    directory: Path,
    sample_stem: str,
    *,
    raw_file: Path | None = None,
    **peak_overrides: Any,
) -> SampleRecords:
    """One admitted peak located by two attached events at its apex."""
    the_peak = peak(sample_stem, **peak_overrides)
    events = [
        event(
            sample_stem,
            scan_number=the_peak.best_ms2_scan_id + offset,
            rt=the_peak.ms1_apex_rt,
            precursor_mz=the_peak.peak_mz,
            neutral_loss_tag=the_peak.neutral_loss_tag,
        )
        for offset in (0, 1)
    ]
    return SampleRecords(
        sample_stem,
        raw_file or Path(f"C:/stale/{sample_stem}.raw"),
        peaks=[the_peak],
        attachments=attached(the_peak, *events),
        acquisition_source=acquisition_source(directory / "sources", sample_stem),
    )


def write_discovery(
    output_dir: Path,
    samples: Sequence[SampleRecords],
    *,
    neutral_loss_by_tag: Mapping[str, float] = NEUTRAL_LOSS_BY_TAG,
) -> Path:
    """Publish ``samples`` in order as one Discovery file under ``output_dir``."""
    parts = [
        write_sample_part(
            part_path(output_dir, records.sample_stem),
            sample_index=index,
            sample=DiscoverySample(
                records.sample_stem,
                records.raw_file,
                records.acquisition_source,
                records.mass_recalibration_result,
            ),
            neutral_loss_by_tag=neutral_loss_by_tag,
            peaks=records.peaks,
            attachments=records.attachments,
        )
        for index, records in enumerate(samples, start=1)
    ]
    return merge_sample_parts(output_dir / DISCOVERY_ARTIFACT_NAME, parts)


def stored_peaks(store: Path) -> list[DiscoveryPeak]:
    """Every published peak with its located events, in sample order."""
    batch = read_discovery_batch(store)
    return [
        item for stem in batch.sample_order for item in read_sample_peaks(store, stem)
    ]


def stored_events(store: Path) -> list[EventAttachment]:
    """Every published event, with the peak it locates and why it does not attach."""
    with open_discovery(store) as db:
        losses = read_neutral_loss_by_tag(db)
        peak_ids = {row_id: item.peak_id for row_id, item in read_peaks(db)}
        return [
            EventAttachment(
                stored,
                peak_id="" if row_id is None else peak_ids[row_id],
                unattached_reason=reason,
            )
            for row_id, reason, stored in read_events(db, losses)
        ]
