from __future__ import annotations

import csv
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.configuration.mass_recalibration import (
    VENDOR_MASS_RECALIBRATION_RESULT,
    MassRecalibrationResult,
)
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_COLUMNS,
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DiscoveryPeakUniverseReceipt,
    discovery_receipt_index_fields,
    mint_discovery_peak_universe_receipt,
)
from xic_extractor.discovery.csv_writer import (
    format_discovery_csv_value,
    write_discovery_events_csv,
    write_discovery_peaks_csv,
)
from xic_extractor.discovery.models import (
    DISCOVERY_EVENT_COLUMNS,
    DISCOVERY_PEAK_COLUMNS,
    DiscoveryPeak,
    DiscoveryRunOutputs,
    EventAttachment,
)
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.tabular_io import write_delimited_rows


@dataclass(frozen=True)
class PublishedDiscoverySample:
    """Committed sample artifacts and their bound receipt and batch-index facts."""

    outputs: DiscoveryRunOutputs
    receipt: DiscoveryPeakUniverseReceipt
    index_row: dict[str, str]


def publish_discovery_batch_sample(
    raw_path: Path,
    peaks: tuple[DiscoveryPeak, ...],
    events: tuple[EventAttachment, ...],
    *,
    output_dir: Path,
    acquisition_source: AcquisitionSourceIdentity,
    timing_recorder: TimingRecorder,
    mass_recalibration_result: MassRecalibrationResult = (
        VENDOR_MASS_RECALIBRATION_RESULT
    ),
    sample_output_dir: Path | None = None,
) -> PublishedDiscoverySample:
    """Publish one sample and bind all batch handoff facts to its persisted rows.

    Both serial and process execution consume this complete handoff. The receipt
    contract owns minting after the writer commits the sample's bytes; no batch
    index row is returned when publication or receipt construction fails.
    """

    outputs = _write_sample_outputs(
        sample_output_dir
        if sample_output_dir is not None
        else output_dir / raw_path.stem,
        peaks,
        events,
        timing_recorder=timing_recorder,
        sample_stem=raw_path.stem,
    )
    receipt = mint_discovery_peak_universe_receipt(
        sample_stem=raw_path.stem,
        acquisition_source=acquisition_source,
        peak_csv=outputs.peaks_csv,
        peaks=peaks,
        mass_recalibration_result=mass_recalibration_result,
    )
    return PublishedDiscoverySample(
        outputs=outputs,
        receipt=receipt,
        index_row=_batch_index_row(
            raw_path=raw_path,
            outputs=outputs,
            peaks=peaks,
            events=events,
            receipt=receipt,
            index_parent=output_dir,
        ),
    )


def _write_sample_outputs(
    output_dir: Path,
    peaks: tuple[DiscoveryPeak, ...],
    events: tuple[EventAttachment, ...],
    *,
    timing_recorder: TimingRecorder,
    sample_stem: str,
) -> DiscoveryRunOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    peaks_path = output_dir / "discovery_peaks.csv"
    events_path = output_dir / "discovery_events.csv"

    final_paths = [peaks_path, events_path]
    temp_paths = [_discovery_output_temp_path(path) for path in final_paths]
    backup_paths = [_discovery_output_backup_path(path) for path in final_paths]
    existed_before = {path: path.exists() for path in final_paths}
    backups: list[tuple[Path, Path]] = []
    try:
        for path in (*temp_paths, *backup_paths):
            path.unlink(missing_ok=True)
        with timing_recorder.stage(
            "discover.write_peaks_csv",
            sample_stem=sample_stem,
            metrics={"row_count": len(peaks)},
        ):
            write_discovery_peaks_csv(temp_paths[0], peaks)
            _verify_staged_discovery_csv(
                temp_paths[0],
                columns=DISCOVERY_PEAK_COLUMNS,
                row_count=len(peaks),
            )
        with timing_recorder.stage(
            "discover.write_events_csv",
            sample_stem=sample_stem,
            metrics={"row_count": len(events)},
        ):
            write_discovery_events_csv(temp_paths[1], events)
            _verify_staged_discovery_csv(
                temp_paths[1],
                columns=DISCOVERY_EVENT_COLUMNS,
                row_count=len(events),
            )
    except Exception:
        for path in (*temp_paths, *backup_paths):
            path.unlink(missing_ok=True)
            path.with_name(f"{path.name}.tmp").unlink(missing_ok=True)
        raise

    with timing_recorder.stage(
        "discover.publish_outputs",
        sample_stem=sample_stem,
        metrics={
            "artifact_count": len(final_paths),
            "replaced_existing_count": sum(
                int(existed_before[path]) for path in final_paths
            ),
        },
    ):
        try:
            for final_path, backup_path in zip(
                final_paths,
                backup_paths,
                strict=True,
            ):
                if final_path.exists():
                    final_path.replace(backup_path)
                    backups.append((final_path, backup_path))
            for temp_path, final_path in zip(
                temp_paths,
                final_paths,
                strict=True,
            ):
                temp_path.replace(final_path)
            for _final_path, backup_path in backups:
                with suppress(OSError):
                    backup_path.unlink(missing_ok=True)
        except Exception:
            for final_path, backup_path in reversed(
                tuple(zip(final_paths, backup_paths, strict=True))
            ):
                if backup_path.exists():
                    final_path.unlink(missing_ok=True)
                    backup_path.replace(final_path)
                elif not existed_before[final_path]:
                    final_path.unlink(missing_ok=True)
            for path in (*temp_paths, *backup_paths):
                path.unlink(missing_ok=True)
                path.with_name(f"{path.name}.tmp").unlink(missing_ok=True)
            raise

    return DiscoveryRunOutputs(peaks_csv=peaks_path, events_csv=events_path)


def _verify_staged_discovery_csv(
    path: Path,
    *,
    columns: tuple[str, ...],
    row_count: int,
) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or tuple(rows[0]) != columns:
        raise ValueError(f"{path}: staged Discovery CSV header verification failed")
    if len(rows) - 1 != row_count:
        raise ValueError(f"{path}: staged Discovery CSV row-count verification failed")


def _discovery_output_temp_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.tmp")


def _discovery_output_backup_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.bak")


def _batch_index_row(
    *,
    raw_path: Path,
    outputs: DiscoveryRunOutputs,
    peaks: tuple[DiscoveryPeak, ...],
    events: tuple[EventAttachment, ...],
    receipt: DiscoveryPeakUniverseReceipt,
    index_parent: Path,
) -> dict[str, str]:
    return {
        "schema_version": DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
        "sample_stem": format_discovery_csv_value("sample_stem", raw_path.stem),
        "raw_file": format_discovery_csv_value("raw_file", raw_path),
        "peak_csv": format_discovery_csv_value(
            "peak_csv",
            _relative_batch_artifact(outputs.peaks_csv, parent=index_parent),
        ),
        "event_csv": format_discovery_csv_value(
            "event_csv",
            _relative_batch_artifact(outputs.events_csv, parent=index_parent),
        ),
        "peak_count": str(len(peaks)),
        "admitted_peak_count": str(sum(peak.admitted for peak in peaks)),
        "event_count": str(len(events)),
        **discovery_receipt_index_fields(receipt),
    }


def _relative_batch_artifact(path: Path, *, parent: Path) -> str:
    try:
        relative = path.resolve(strict=True).relative_to(parent.resolve(strict=True))
    except ValueError as exc:
        raise ValueError("Discovery batch artifact must stay under its index") from exc
    return relative.as_posix()


def write_batch_index(
    output_path: Path,
    rows: list[dict[str, str]],
    *,
    timing_recorder: TimingRecorder,
) -> Path:
    with timing_recorder.stage(
        "discover.write_batch_index",
        metrics={"raw_count": len(rows), "row_count": len(rows)},
    ):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_delimited_rows(
            output_path,
            rows,
            DISCOVERY_BATCH_INDEX_COLUMNS,
            delimiter=",",
            encoding="utf-8",
            extrasaction="raise",
        )
    return output_path
