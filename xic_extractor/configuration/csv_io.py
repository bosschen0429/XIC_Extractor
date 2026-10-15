import csv
import io
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.parsing import _require_columns

TARGET_FIELDS = ("label", "mz", "expected_rt_min", "cid_nl_tag", "role", "istd_pair")
LEGACY_TARGET_FIELDS = (
    "rt_min",
    "rt_max",
    "ppm_tol",
    "neutral_loss_da",
    "nl_ppm_warn",
    "nl_ppm_max",
    "is_istd",
)
OPTIONAL_TARGET_METADATA_FIELDS = ("sample_applicability",)
TARGET_WRITE_FIELDS = (*TARGET_FIELDS, *OPTIONAL_TARGET_METADATA_FIELDS)
_REQUIRED_SETTING_FIELDS = ("key", "value")
_TARGET_DRAFT_IDENTITY_FIELDS = ("label", "mz")


def read_settings(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ConfigError(f"{path}: file is missing")
    return _parse_settings_csv_bytes(path.read_bytes(), path)


def _parse_settings_csv_bytes(payload: bytes, path: Path) -> dict[str, str]:
    """Decode captured settings bytes using the same intake rules as files."""
    with io.StringIO(payload.decode("utf-8-sig"), newline="") as handle:
        rows = csv.DictReader(handle)
        _require_columns(path, rows.fieldnames, _REQUIRED_SETTING_FIELDS)
        settings: dict[str, str] = {}
        for row_number, row in enumerate(rows, start=2):
            key = str(row.get("key") or "").strip()
            value = str(row.get("value") or "").strip()
            if not key:
                continue
            if key in settings and settings[key] != value:
                raise ConfigError(
                    f"{path} row {row_number}: conflicting duplicate setting {key!r}"
                )
            settings[key] = value
        return settings


def _read_target_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    return _read_target_file_rows(path, delimiter=delimiter, required=TARGET_FIELDS)


def read_target_draft_rows(path: Path) -> list[dict[str, str]]:
    """Decode an editable target table without applying runtime validation."""

    suffix = path.suffix.lower()
    if suffix == ".csv":
        delimiter = ","
    elif suffix == ".tsv":
        delimiter = "\t"
    else:
        raise ConfigError(f"{path}: expected a .csv or .tsv target file")

    numbered_rows = _read_target_file_rows(
        path,
        delimiter=delimiter,
        required=_TARGET_DRAFT_IDENTITY_FIELDS,
    )
    if not numbered_rows:
        raise ConfigError(f"{path}: at least one target row is required")
    return [row for _row_number, row in numbered_rows]


def _read_target_file_rows(
    path: Path,
    *,
    delimiter: str,
    required: tuple[str, ...],
) -> list[tuple[int, dict[str, str]]]:
    if not path.exists():
        raise ConfigError(f"{path}: file is missing")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle, delimiter=delimiter)
        fieldnames = rows.fieldnames
        if fieldnames is None:
            raise ConfigError(f"{path}: file is empty")
        if any(field == "" for field in fieldnames):
            raise ConfigError(f"{path}: target header names must not be empty")
        duplicates = sorted(
            field for field in set(fieldnames) if fieldnames.count(field) > 1
        )
        if duplicates:
            raise ConfigError(
                f"{path}: duplicate target header {duplicates[0]!r} is not allowed"
            )
        _require_columns(path, fieldnames, required)

        result: list[tuple[int, dict[str, str]]] = []
        for row_number, row in enumerate(rows, start=2):
            if None in row:
                raise ConfigError(f"{path} row {row_number}: more values than columns")
            missing_fields = [field for field, value in row.items() if value is None]
            if missing_fields:
                raise ConfigError(
                    f"{path} row {row_number}: fewer values than columns; "
                    f"missing {missing_fields[0]!r}"
                )
            result.append((row_number, row))
        return result


def write_config_snapshot(
    directory: Path,
    settings: Mapping[str, str],
    target_rows: Sequence[Mapping[str, str]],
) -> None:
    """Write machine-owned run inputs, not user-editable method documents."""
    fields = tuple(
        dict.fromkeys(
            [*TARGET_WRITE_FIELDS, *(key for row in target_rows for key in row)]
        )
    )
    tables = (
        (
            "settings.csv",
            ("key", "value"),
            [{"key": key, "value": value} for key, value in settings.items()],
        ),
        ("targets.csv", fields, target_rows),
    )
    for name, columns, rows in tables:
        with (directory / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
