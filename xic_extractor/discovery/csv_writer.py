"""CSV writers for the Discovery peak and event tables."""

import csv
from collections.abc import Iterable
from pathlib import Path, PureWindowsPath

from xic_extractor.discovery.models import (
    DISCOVERY_EVENT_COLUMNS,
    DISCOVERY_PEAK_COLUMNS,
    DiscoveryPeak,
    EventAttachment,
    discovery_event_output_sort_key,
)
from xic_extractor.tabular_io import (
    escape_excel_formula,
    format_machine_float,
    write_delimited_rows,
)


def merge_discovery_peak_tables(paths: Iterable[Path], target: Path) -> Path:
    """Stream already-published sample tables into a downloadable peak export."""
    header: list[str] | None = None
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = None
        for path in paths:
            with path.open(encoding="utf-8", newline="") as source:
                reader = csv.DictReader(source)
                if writer is None:
                    header = list(reader.fieldnames or ())
                    writer = csv.DictWriter(handle, fieldnames=header)
                    writer.writeheader()
                elif list(reader.fieldnames or ()) != header:
                    raise ValueError(f"{path}: peak table columns differ")
                writer.writerows(reader)
    return target


def write_discovery_peaks_csv(path: Path, peaks: Iterable[DiscoveryPeak]) -> Path:
    """One row per measured peak, in the given order, judgment fields first."""
    return _write_csv(
        path,
        [_row(peak, DISCOVERY_PEAK_COLUMNS) for peak in peaks],
        DISCOVERY_PEAK_COLUMNS,
    )


def write_discovery_events_csv(path: Path, events: Iterable[EventAttachment]) -> Path:
    """One row per strict NL event: its located peak and why it does not attach."""
    return _write_csv(
        path,
        [
            _row(event, DISCOVERY_EVENT_COLUMNS)
            for event in sorted(events, key=discovery_event_output_sort_key)
        ],
        DISCOVERY_EVENT_COLUMNS,
    )


def _write_csv(
    path: Path,
    rows: list[dict[str, str]],
    columns: tuple[str, ...],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_delimited_rows(path, rows, columns)
    return path


def _row(value: object, columns: tuple[str, ...]) -> dict[str, str]:
    return {
        column: format_discovery_csv_value(column, getattr(value, column))
        for column in columns
    }


def format_discovery_csv_value(column: str, value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if column == "matched_tag_names":
        if isinstance(value, str):
            return value
        if isinstance(value, tuple):
            return ";".join(str(item) for item in value)
        return str(value)
    if column == "parent_ms1_centroid_mzs" and isinstance(value, tuple):
        return ";".join(format_machine_float(float(item)) for item in value)
    if isinstance(value, Path):
        return escape_excel_formula(_stable_path_text(value))
    if isinstance(value, float):
        return format_machine_float(value)
    return escape_excel_formula(str(value))


def _stable_path_text(value: Path) -> str:
    text = str(value)
    windows_path = PureWindowsPath(text)
    if windows_path.drive:
        return str(windows_path)
    return text
