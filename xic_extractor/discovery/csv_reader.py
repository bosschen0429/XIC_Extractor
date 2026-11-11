from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import cast

from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_COLUMNS,
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DiscoveryPeakUniverseReceipt,
    discovery_receipt_from_index_fields,
    validate_discovery_peak_artifact,
    validate_discovery_peak_universe,
)
from xic_extractor.discovery.models import (
    DISCOVERY_EVENT_COLUMNS,
    DISCOVERY_PEAK_COLUMNS,
    PEAK_STATUSES,
    DiscoveryEvent,
    DiscoveryPeak,
    EventAttachment,
    GroupPrecursorMzBasis,
    PeakStatus,
    located_event_order,
)
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
from xic_extractor.precursor_evidence import Ms1PrecursorObservation
from xic_extractor.processing_identity import IdentityContractError

_BATCH_UNESCAPE_FIELDS = {"sample_stem", "raw_file", "peak_csv", "event_csv"}
_PEAK_UNESCAPE_FIELDS = {"sample_stem", "peak_id"}
_EVENT_PRECURSOR_BASES = {
    "scan_precursor",
    "scan_monoisotopic",
    "product_plus_neutral_loss",
}

_ALLOWED_PRECURSOR_MZ_BASIS = {
    "scan_precursor",
    "scan_monoisotopic",
    "product_plus_neutral_loss",
    "mixed",
}


@dataclass(frozen=True)
class DiscoveryBatchInput:
    sample_order: tuple[str, ...]
    peak_csvs: dict[str, Path]
    raw_files: dict[str, Path | None]
    event_csvs: dict[str, Path | None]
    peak_receipts: dict[str, DiscoveryPeakUniverseReceipt]


def read_discovery_batch_index(
    path: Path,
) -> DiscoveryBatchInput:
    rows, fieldnames = _read_csv_rows(path)
    require_current_discovery_columns(
        path, fieldnames, DISCOVERY_BATCH_INDEX_COLUMNS, table="batch index"
    )

    sample_order: list[str] = []
    peak_csvs: dict[str, Path] = {}
    raw_files: dict[str, Path | None] = {}
    event_csvs: dict[str, Path | None] = {}
    peak_receipts: dict[str, DiscoveryPeakUniverseReceipt] = {}
    for row_number, row in rows:
        sample_stem = _machine_text(row.get("sample_stem", ""))
        peak_csv = _machine_text(row.get("peak_csv", ""))
        raw_file = _machine_text(row.get("raw_file", ""))
        event_csv = _machine_text(row.get("event_csv", ""))
        if not sample_stem:
            raise ValueError(f"{path}: row {row_number}: sample_stem is required")
        if not peak_csv:
            raise ValueError(f"{path}: row {row_number}: peak_csv is required")
        if sample_stem in peak_csvs:
            raise ValueError(
                f"{path}: row {row_number}: duplicate sample_stem values are not "
                f"supported ({sample_stem!r})"
            )
        schema_version = _machine_text(row.get("schema_version", ""))
        if schema_version != DISCOVERY_BATCH_INDEX_SCHEMA_VERSION:
            raise ValueError(
                f"{path}: row {row_number}: unsupported schema_version "
                f"{schema_version!r}; regenerate Discovery with the current version"
            )
        peak_count = _batch_nonnegative_int(
            path,
            row_number,
            row.get("peak_count", ""),
            field="peak_count",
        )
        try:
            receipt = discovery_receipt_from_index_fields(
                row,
                sample_stem=sample_stem,
                peak_count=peak_count,
            )
        except (IdentityContractError, ValueError) as exc:
            raise ValueError(
                f"{path}: row {row_number}: invalid Discovery receipt: {exc}"
            ) from exc
        sample_order.append(sample_stem)
        peak_csvs[sample_stem] = _resolve_artifact_path(path.parent, peak_csv)
        raw_files[sample_stem] = _raw_file_path(raw_file) if raw_file else None
        event_csvs[sample_stem] = (
            _resolve_artifact_path(path.parent, event_csv) if event_csv else None
        )
        peak_receipts[sample_stem] = receipt
    return DiscoveryBatchInput(
        sample_order=tuple(sample_order),
        peak_csvs=peak_csvs,
        raw_files=raw_files,
        event_csvs=event_csvs,
        peak_receipts=peak_receipts,
    )


def read_discovery_peaks_csv(
    path: Path,
) -> tuple[DiscoveryPeak, ...]:
    rows, fieldnames = _read_csv_rows(path)
    require_current_discovery_columns(
        path, fieldnames, DISCOVERY_PEAK_COLUMNS, table="peak table"
    )

    peaks: list[DiscoveryPeak] = []
    seen_peak_ids: dict[str, int] = {}
    for row_number, row in rows:
        peak = _parse_peak_row(path, row_number, row)
        previous_row = seen_peak_ids.get(peak.peak_id)
        if previous_row is not None:
            raise ValueError(
                f"{path}: row {row_number}: duplicate peak_id "
                f"{peak.peak_id!r}; first seen at row {previous_row}"
            )
        seen_peak_ids[peak.peak_id] = row_number
        peaks.append(peak)
    return tuple(peaks)


def read_owner_bound_discovery_peaks_csv(
    path: Path,
    *,
    receipt: DiscoveryPeakUniverseReceipt,
) -> tuple[DiscoveryPeak, ...]:
    """Read one exact Discovery artifact and close it against its receipt."""

    try:
        validate_discovery_peak_artifact(receipt, path)
        peaks = read_discovery_peaks_csv(path)
        validate_discovery_peak_artifact(receipt, path)
        return validate_discovery_peak_universe(
            receipt,
            acquisition_source=receipt.acquisition_source,
            peaks=peaks,
        )
    except IdentityContractError as exc:
        raise ValueError(f"{path}: invalid owner-bound peak artifact: {exc}") from exc


def located_events_by_peak(
    path: Path,
    *,
    raw_file: Path,
    sample_stem: str,
    neutral_loss_by_tag: Mapping[str, float],
) -> dict[str, tuple[DiscoveryEvent, ...]]:
    """Every event locating each peak of one sample's event table."""
    grouped: dict[str, list[DiscoveryEvent]] = {}
    for item in read_discovery_events_csv(
        path,
        raw_file=raw_file,
        sample_stem=sample_stem,
        neutral_loss_by_tag=neutral_loss_by_tag,
    ):
        if item.peak_id:
            grouped.setdefault(item.peak_id, []).append(item.event)
    return {peak_id: located_event_order(events) for peak_id, events in grouped.items()}


def read_discovery_events_csv(
    path: Path,
    *,
    raw_file: Path,
    sample_stem: str,
    neutral_loss_by_tag: Mapping[str, float],
) -> tuple[EventAttachment, ...]:
    """Read one sample's event table back into its events and located peaks."""
    rows, fieldnames = _read_csv_rows(path)
    require_current_discovery_columns(
        path, fieldnames, DISCOVERY_EVENT_COLUMNS, table="event table"
    )
    return tuple(
        parse_discovery_event_row(
            path,
            row_number,
            row,
            raw_file=raw_file,
            sample_stem=sample_stem,
            neutral_loss_by_tag=neutral_loss_by_tag,
        )
        for row_number, row in rows
    )


def require_current_discovery_columns(
    path: Path,
    fieldnames: tuple[str, ...],
    columns: tuple[str, ...],
    *,
    table: str,
) -> None:
    """Reject a Discovery table another version wrote; it has no reader here."""
    if fieldnames != columns:
        raise ValueError(
            f"{path}: unsupported Discovery {table} (its columns are not the "
            "current ones); regenerate Discovery with the current version"
        )


def parse_discovery_event_row(
    path: Path,
    row_number: int,
    row: Mapping[str, str],
    *,
    raw_file: Path,
    sample_stem: str,
    neutral_loss_by_tag: Mapping[str, float],
) -> EventAttachment:
    values = dict(row)

    def number(column: str) -> float:
        return _parse_float(path, row_number, values, column)

    def optional(column: str) -> float | None:
        return _parse_optional_float(path, row_number, values, column)

    def optional_int(column: str) -> int | None:
        if values.get(column, "") == "":
            return None
        return _parse_int(path, row_number, values, column)

    tag = _required_text(path, row_number, values, "neutral_loss_tag")
    if tag not in neutral_loss_by_tag:
        raise ValueError(
            f"{path}: row {row_number}: neutral_loss_tag {tag!r} is not a "
            "configured tag of this run"
        )
    basis = _required_text(path, row_number, values, "precursor_mz_basis")
    if basis not in _EVENT_PRECURSOR_BASES:
        raise ValueError(
            f"{path}: row {row_number}: precursor_mz_basis must be one of "
            f"{', '.join(sorted(_EVENT_PRECURSOR_BASES))}: {basis!r}"
        )
    parent_scan = optional_int("parent_ms1_scan_number")
    try:
        recorded = Ms2PrecursorMetadata(
            monoisotopic_mz=optional("precursor_monoisotopic_mz"),
            charge=optional_int("precursor_charge"),
            isolation_width_da=optional("isolation_width_da"),
            isolation_offset_da=optional("isolation_offset_da"),
            master_scan_number=optional_int("master_scan_number"),
        )
        # An event without vendor precursor metadata writes five blanks.
        metadata = None if recorded == Ms2PrecursorMetadata() else recorded
        observation = (
            None
            if parent_scan is None
            else Ms1PrecursorObservation(
                scan_number=parent_scan,
                rt_min=number("parent_ms1_rt"),
                centroid_mzs=_parse_float_tuple(
                    path, row_number, values, "parent_ms1_centroid_mzs"
                ),
            )
        )
        event = DiscoveryEvent(
            raw_file=raw_file,
            sample_stem=sample_stem,
            scan_number=_parse_int(path, row_number, values, "ms2_scan_number"),
            rt=number("ms2_rt"),
            precursor_mz=number("precursor_mz"),
            product_mz=number("product_mz"),
            product_intensity=number("product_intensity"),
            neutral_loss_tag=tag,
            configured_neutral_loss_da=float(neutral_loss_by_tag[tag]),
            observed_neutral_loss_da=number("observed_neutral_loss_da"),
            observed_loss_error_ppm=optional("neutral_loss_mass_error_ppm"),
            scan_precursor_mz=optional("scan_precursor_mz"),
            precursor_mz_basis=basis,  # type: ignore[arg-type]
            precursor_metadata=metadata,
            ms1_precursor_observation=observation,
        )
        return EventAttachment(
            event=event,
            xic_mz=number("xic_mz"),
            precursor_verified=_parse_bool(
                path, row_number, values, "precursor_verified"
            ),
            peak_id=_machine_field(values, "peak_id"),
            unattached_reason=values.get("unattached_reason", ""),
        )
    except (TypeError, ValueError) as exc:
        if str(exc).startswith(f"{path}:"):
            raise
        raise ValueError(f"{path}: row {row_number}: {exc}") from exc


def _read_csv_rows(
    path: Path,
) -> tuple[list[tuple[int, dict[str, str]]], tuple[str, ...]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fieldnames = tuple(reader.fieldnames or ())
            return (
                [(index, row) for index, row in enumerate(reader, start=2)],
                fieldnames,
            )
    except OSError as exc:
        raise ValueError(f"{path}: CSV file could not be read: {exc}") from exc


def _parse_peak_row(
    path: Path,
    row_number: int,
    row: dict[str, str],
) -> DiscoveryPeak:
    peak_id = _required_text(path, row_number, row, "peak_id")

    def number(column: str) -> float:
        return _parse_float(path, row_number, row, column)

    def optional(column: str) -> float | None:
        return _parse_optional_float(path, row_number, row, column)

    def text(column: str) -> str:
        return _required_text(path, row_number, row, column)

    def integer(column: str) -> int:
        return _parse_int(path, row_number, row, column)

    return DiscoveryPeak(
        peak_id=_machine_text(peak_id),
        sample_stem=_machine_field(row, "sample_stem"),
        peak_mz=number("peak_mz"),
        neutral_loss_tag=text("neutral_loss_tag"),
        best_ms2_scan_id=integer("best_ms2_scan_id"),
        attached_event_count=integer("attached_event_count"),
        ms1_apex_rt=number("ms1_apex_rt"),
        ms1_search_rt_min=number("ms1_search_rt_min"),
        ms1_search_rt_max=number("ms1_search_rt_max"),
        query_rt_min=number("query_rt_min"),
        status=_parse_status(path, row_number, row),
        ms1_apex_mz=optional("ms1_apex_mz"),
        evidence_score=integer("evidence_score"),
        precursor_mz_basis=_parse_precursor_mz_basis(path, row_number, row),
        matched_tag_names=_parse_text_tuple(row, "matched_tag_names"),
    )


def _raw_file_path(value: str) -> Path:
    """Read Windows-authored RAW locators without treating backslashes as names."""
    windows_path = PureWindowsPath(value)
    return Path(windows_path.as_posix() if windows_path.drive else value)


def _resolve_artifact_path(parent: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else parent / path


def _machine_field(row: dict[str, str], column: str) -> str:
    value = row.get(column, "")
    if column in _PEAK_UNESCAPE_FIELDS or column in _BATCH_UNESCAPE_FIELDS:
        return _machine_text(value)
    return value


def _machine_text(value: str) -> str:
    if len(value) >= 2 and value[0] == "'" and value[1] in ("=", "+", "-", "@"):
        return value[1:]
    return value


def _parse_status(path: Path, row_number: int, row: dict[str, str]) -> PeakStatus:
    value = _required_text(path, row_number, row, "status")
    if value not in PEAK_STATUSES:
        raise ValueError(
            f"{path}: row {row_number}: status must be one of "
            f"{', '.join(PEAK_STATUSES)}: {value!r}"
        )
    return cast(PeakStatus, value)


def _parse_precursor_mz_basis(
    path: Path,
    row_number: int,
    row: dict[str, str],
) -> GroupPrecursorMzBasis:
    value = _required_text(path, row_number, row, "precursor_mz_basis")
    if value not in _ALLOWED_PRECURSOR_MZ_BASIS:
        allowed = ", ".join(sorted(_ALLOWED_PRECURSOR_MZ_BASIS))
        raise ValueError(
            f"{path}: row {row_number}: precursor_mz_basis must be one of "
            f"{allowed}: {value!r}"
        )
    return cast(GroupPrecursorMzBasis, value)


def _required_text(
    path: Path,
    row_number: int,
    row: dict[str, str],
    column: str,
) -> str:
    value = row.get(column, "")
    if value == "":
        raise ValueError(f"{path}: row {row_number}: {column} is required")
    return value


def _parse_int(path: Path, row_number: int, row: dict[str, str], column: str) -> int:
    value = row.get(column, "")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"{path}: row {row_number}: {column} must be an integer: {value!r}"
        ) from exc


def _batch_nonnegative_int(
    path: Path,
    row_number: int,
    value: object,
    *,
    field: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(
            f"{path}: row {row_number}: {field} must be a non-negative integer"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{path}: row {row_number}: {field} must be a non-negative integer"
        ) from exc
    if parsed < 0:
        raise ValueError(
            f"{path}: row {row_number}: {field} must be a non-negative integer"
        )
    return parsed


def _parse_float(
    path: Path,
    row_number: int,
    row: dict[str, str],
    column: str,
) -> float:
    value = row.get(column, "")
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(
            f"{path}: row {row_number}: {column} must be a float: {value!r}"
        ) from exc


def _parse_optional_float(
    path: Path,
    row_number: int,
    row: dict[str, str],
    column: str,
) -> float | None:
    value = row.get(column, "")
    if value == "":
        return None
    return _parse_float(path, row_number, row, column)


def _parse_bool(path: Path, row_number: int, row: dict[str, str], column: str) -> bool:
    value = row.get(column, "")
    if value == "TRUE":
        return True
    if value == "FALSE":
        return False
    raise ValueError(
        f"{path}: row {row_number}: {column} must be TRUE or FALSE: {value!r}"
    )


def _parse_float_tuple(
    path: Path,
    row_number: int,
    row: dict[str, str],
    column: str,
) -> tuple[float, ...]:
    value = row.get(column, "")
    if value == "":
        return ()
    try:
        return tuple(float(item) for item in value.split(";") if item)
    except ValueError as exc:
        raise ValueError(
            f"{path}: row {row_number}: {column} must be semicolon-separated floats"
        ) from exc


def _parse_text_tuple(row: dict[str, str], column: str) -> tuple[str, ...]:
    value = row.get(column, "")
    if value == "":
        return ()
    return tuple(item for item in value.split(";") if item)
