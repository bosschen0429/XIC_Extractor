import csv
from pathlib import Path

from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.parsing import _require_columns

TARGET_FIELDS = (
    "label",
    "mz",
    "rt_min",
    "rt_max",
    "ppm_tol",
    "neutral_loss_da",
    "nl_ppm_warn",
    "nl_ppm_max",
    "is_istd",
    "istd_pair",
)
OPTIONAL_TARGET_METADATA_FIELDS = (
    "isotope_label_type",
    "paired_rt_relation",
    "sample_applicability",
)
TARGET_WRITE_FIELDS = (*TARGET_FIELDS, *OPTIONAL_TARGET_METADATA_FIELDS)
_REQUIRED_SETTING_FIELDS = ("key", "value")
_TARGET_DRAFT_IDENTITY_FIELDS = ("label", "mz")


def _config_input_path(config_dir: Path, name: str) -> Path:
    runtime_path = config_dir / f"{name}.csv"
    if runtime_path.exists():
        return runtime_path
    example_path = config_dir / f"{name}.example.csv"
    if example_path.exists():
        return example_path
    return runtime_path


def _read_settings(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ConfigError(f"{path}: file is missing")
    with path.open(newline="", encoding="utf-8-sig") as handle:
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
    return _read_target_file_rows(path, delimiter=",", required=TARGET_FIELDS)


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
