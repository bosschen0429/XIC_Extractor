from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import cast

from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DISCOVERY_BATCH_RECEIPT_COLUMNS,
    DiscoveryPeakUniverseReceipt,
    discovery_receipt_from_index_fields,
    validate_discovery_peak_artifact,
    validate_discovery_peak_universe,
)
from xic_extractor.discovery.models import (
    DISCOVERY_PEAK_COLUMNS,
    PEAK_STATUSES,
    DiscoveryPeak,
    GroupPrecursorMzBasis,
    NeutralLossErrorBasis,
    PeakStatus,
)
from xic_extractor.processing_identity import IdentityContractError

_BATCH_REQUIRED_COLUMNS = ("sample_stem", "raw_file", "peak_csv")
_BATCH_OWNER_REQUIRED_COLUMNS = (
    "schema_version",
    *_BATCH_REQUIRED_COLUMNS,
    "peak_count",
    *DISCOVERY_BATCH_RECEIPT_COLUMNS,
)
_BATCH_UNESCAPE_FIELDS = {"sample_stem", "raw_file", "peak_csv", "event_csv"}
_PEAK_UNESCAPE_FIELDS = {"sample_stem", "raw_file", "peak_id"}

_ALLOWED_PRECURSOR_MZ_BASIS = {
    "scan_precursor",
    "scan_monoisotopic",
    "product_plus_neutral_loss",
    "mixed",
}
_ALLOWED_NEUTRAL_LOSS_ERROR_BASIS = {
    "measured_scan_precursor_product",
    "recorded_monoisotopic_precursor_product",
    "configured_loss_inferred_precursor",
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
    if "peak_csv" not in fieldnames or "acquisition_local_file_revision" not in (
        fieldnames
    ):
        raise ValueError(
            f"{path}: unsupported Discovery batch index; "
            "regenerate Discovery with the current version"
        )
    _require_columns(path, fieldnames, _BATCH_OWNER_REQUIRED_COLUMNS)

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
    _require_columns(path, fieldnames, DISCOVERY_PEAK_COLUMNS)

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


def _require_columns(
    path: Path,
    fieldnames: tuple[str, ...],
    required_columns: tuple[str, ...],
) -> None:
    missing = [column for column in required_columns if column not in fieldnames]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"{path}: missing required columns: {joined}")


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

    def flag(column: str) -> bool:
        return _parse_bool(path, row_number, row, column)

    def integer(column: str) -> int:
        return _parse_int(path, row_number, row, column)

    return DiscoveryPeak(
        peak_id=_machine_text(peak_id),
        raw_file=_raw_file_path(_machine_field(row, "raw_file")),
        sample_stem=_machine_field(row, "sample_stem"),
        xic_mz=number("xic_mz"),
        best_event_precursor_mz=number("best_event_precursor_mz"),
        best_event_product_mz=number("best_event_product_mz"),
        best_event_neutral_loss_da=number("best_event_neutral_loss_da"),
        neutral_loss_tag=text("neutral_loss_tag"),
        configured_neutral_loss_da=number("configured_neutral_loss_da"),
        neutral_loss_mass_error_ppm=optional("neutral_loss_mass_error_ppm"),
        best_ms2_scan_id=integer("best_ms2_scan_id"),
        best_event_rt=number("best_event_rt"),
        event_scan_ids=_parse_int_tuple(path, row_number, row, "event_scan_ids"),
        attached_event_count=integer("attached_event_count"),
        event_rt_min=number("event_rt_min"),
        event_rt_max=number("event_rt_max"),
        ms2_product_max_intensity=number("ms2_product_max_intensity"),
        ms1_search_rt_min=number("ms1_search_rt_min"),
        ms1_search_rt_max=number("ms1_search_rt_max"),
        ms1_apex_rt=number("ms1_apex_rt"),
        ms1_area=number("ms1_area"),
        ms1_height=number("ms1_height"),
        ms1_peak_rt_start=number("ms1_peak_rt_start"),
        ms1_peak_rt_end=number("ms1_peak_rt_end"),
        ms1_event_delta_min=number("ms1_event_delta_min"),
        ms1_trace_quality=text("ms1_trace_quality"),
        review_priority=text("review_priority"),  # type: ignore[arg-type]
        reason=text("reason"),
        status=_parse_status(path, row_number, row),
        ms1_scan_support_score=optional("ms1_scan_support_score"),
        ms1_apex_mz=optional("ms1_apex_mz"),
        evidence_score=integer("evidence_score"),
        evidence_tier=text("evidence_tier"),
        ms2_support=text("ms2_support"),
        ms1_support=text("ms1_support"),
        rt_alignment=text("rt_alignment"),
        neutral_loss_error_basis=_parse_neutral_loss_error_basis(path, row_number, row),
        precursor_mz_basis=_parse_precursor_mz_basis(path, row_number, row),
        scan_precursor_mz=optional("scan_precursor_mz"),
        scan_precursor_delta_da=optional("scan_precursor_delta_da"),
        max_scan_precursor_abs_delta_da=optional("max_scan_precursor_abs_delta_da"),
        selected_tag_count=integer("selected_tag_count"),
        matched_tag_count=integer("matched_tag_count"),
        matched_tag_names=_parse_text_tuple(row, "matched_tag_names"),
        primary_tag_name=text("primary_tag_name"),
        tag_combine_mode=text("tag_combine_mode"),  # type: ignore[arg-type]
        tag_intersection_status=text("tag_intersection_status"),  # type: ignore[arg-type]
        tag_evidence_json=text("tag_evidence_json"),
        is_provisional=flag("is_provisional"),
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


def _parse_neutral_loss_error_basis(
    path: Path,
    row_number: int,
    row: dict[str, str],
) -> NeutralLossErrorBasis:
    value = _required_text(path, row_number, row, "neutral_loss_error_basis")
    if value not in _ALLOWED_NEUTRAL_LOSS_ERROR_BASIS:
        allowed = ", ".join(sorted(_ALLOWED_NEUTRAL_LOSS_ERROR_BASIS))
        raise ValueError(
            f"{path}: row {row_number}: neutral_loss_error_basis must be one "
            f"of {allowed}: {value!r}"
        )
    return cast(NeutralLossErrorBasis, value)


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


def _parse_int_tuple(
    path: Path,
    row_number: int,
    row: dict[str, str],
    column: str,
) -> tuple[int, ...]:
    value = row.get(column, "")
    if value == "":
        return ()
    try:
        return tuple(int(item) for item in value.split(";") if item)
    except ValueError as exc:
        raise ValueError(
            f"{path}: row {row_number}: {column} must be semicolon-separated integers"
        ) from exc


def _parse_text_tuple(row: dict[str, str], column: str) -> tuple[str, ...]:
    value = row.get(column, "")
    if value == "":
        return ()
    return tuple(item for item in value.split(";") if item)
