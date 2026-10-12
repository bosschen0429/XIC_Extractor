import math
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.configuration.models import ConfigError


def _parse_existing_dir(path: Path, column: str, value: str) -> Path:
    directory = Path(value).expanduser()
    if not directory.is_dir():
        raise _config_error(path, None, column, value, "must exist as a directory")
    return directory


def _require_columns(
    path: Path,
    fieldnames: Sequence[str] | None,
    required: tuple[str, ...],
) -> None:
    available = set(fieldnames or [])
    for column in required:
        if column not in available:
            raise _config_error(path, None, column, "", "required column is missing")


def _parse_int(path: Path, row_number: int | None, column: str, value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise _config_error(
            path, row_number, column, value, "must be an integer"
        ) from exc


def _parse_float(path: Path, row_number: int | None, column: str, value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise _config_error(path, row_number, column, value, "must be numeric") from exc
    if not math.isfinite(parsed):
        raise _config_error(path, row_number, column, value, "must be finite")
    return parsed


def _parse_bool(path: Path, row_number: int | None, column: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise _config_error(path, row_number, column, value, "must be true or false")


def _parse_optional_path(value: str) -> Path | None:
    normalized = value.strip()
    if not normalized:
        return None
    return Path(normalized).expanduser()


def _config_error(
    path: Path, row_number: int | None, column: str, value: str, message: str
) -> ConfigError:
    row = f" row {row_number}" if row_number is not None else ""
    return ConfigError(f"{path}{row} column {column} value {value!r}: {message}")
