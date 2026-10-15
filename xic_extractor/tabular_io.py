"""Small tabular and scalar helpers shared across package layers.

Keep this module deliberately narrow. It is not a diagnostic framework; it only
holds repeated CSV/TSV mechanics and scalar parsing that are safe for domain,
diagnostic, and tool adapters to share.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal


def read_delimited_rows(
    path: Path,
    *,
    required_columns: Sequence[str],
    exact_columns: Sequence[str] | None = None,
    allow_missing_trailing_columns: Sequence[str] = (),
    delimiter: str = "\t",
    encoding: str = "utf-8",
) -> list[dict[str, str]]:
    with path.open(newline="", encoding=encoding) as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        validate_delimited_header(
            reader.fieldnames,
            path=path,
            required_columns=required_columns,
            exact_columns=exact_columns,
        )
        allowed_missing = set(allow_missing_trailing_columns)
        rows: list[dict[str, str]] = []
        for row_index, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(
                    f"{path}:{row_index}: row has more cells than declared header"
                )
            truncated = [
                column for column, value in row.items() if value is None
            ]
            disallowed_truncated = [
                column for column in truncated if column not in allowed_missing
            ]
            if disallowed_truncated:
                raise ValueError(
                    f"{path}:{row_index}: row has fewer cells than declared header; "
                    f"missing cells={truncated!r}"
                )
            for column in truncated:
                row[column] = ""
            rows.append(dict(row))
        return rows


def validate_delimited_header(
    fieldnames: Sequence[str] | None,
    *,
    path: Path,
    required_columns: Sequence[str] = (),
    exact_columns: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Validate a CSV/TSV header before ``DictReader`` can overwrite it.

    Header names are case-sensitive public schema values, so this helper keeps
    their original spelling and order. It rejects blank and duplicate names
    before checking required columns. ``exact_columns`` additionally seals a
    versioned artifact to one complete header and column order. Otherwise
    ``csv.DictReader`` silently turns duplicate columns into row-order-dependent
    last-value-wins mappings, while subset-only validation can hide schema drift.
    """

    header = tuple(fieldnames or ())
    blank_positions = [
        str(index)
        for index, column in enumerate(header, start=1)
        if not column.strip()
    ]
    if blank_positions:
        raise ValueError(
            f"{path}: blank columns at positions: {', '.join(blank_positions)}"
        )
    counts = Counter(header)
    duplicates = sorted(column for column, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(f"{path}: duplicate columns: {', '.join(duplicates)}")
    missing = [column for column in required_columns if column not in header]
    if missing:
        raise ValueError(f"{path}: missing required columns: {', '.join(missing)}")
    if exact_columns is not None:
        expected = tuple(exact_columns)
        if header != expected:
            expected_set = set(expected)
            header_set = set(header)
            exact_missing = [column for column in expected if column not in header_set]
            unexpected = [column for column in header if column not in expected_set]
            details = []
            if exact_missing:
                details.append(f"missing={exact_missing!r}")
            if unexpected:
                details.append(f"unexpected={unexpected!r}")
            if not exact_missing and not unexpected:
                details.append("column order differs")
            raise ValueError(
                f"{path}: header does not match exact schema; " + "; ".join(details)
            )
    return header


def read_tsv_required(
    path: Path,
    required_columns: Sequence[str],
    *,
    allow_missing_trailing_columns: Sequence[str] = (),
) -> tuple[dict[str, str], ...]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return tuple(
        read_delimited_rows(
            path,
            required_columns=required_columns,
            allow_missing_trailing_columns=allow_missing_trailing_columns,
            encoding="utf-8-sig",
        )
    )


def read_tsv_with_header(
    path: Path,
    *,
    required_columns: Sequence[str] = (),
    encoding: str = "utf-8",
) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open(encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        header = validate_delimited_header(
            reader.fieldnames,
            path=path,
            required_columns=required_columns,
        )
        return header, list(reader)


def read_tsv_with_header_snapshot(
    path: Path,
    *,
    required_columns: Sequence[str] = (),
    exact_columns: Sequence[str] | None = None,
    encoding: str = "utf-8",
) -> tuple[tuple[str, ...], list[dict[str, str]], str]:
    """Parse and hash one exact TSV byte snapshot without reopening its path."""

    payload = path.read_bytes()
    try:
        text = payload.decode(encoding)
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: invalid {encoding} text") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter="\t")
    header = validate_delimited_header(
        reader.fieldnames,
        path=path,
        required_columns=required_columns,
        exact_columns=exact_columns,
    )
    rows: list[dict[str, str]] = []
    for row_index, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(
                f"{path}:{row_index}: row has more cells than declared header"
            )
        truncated = [column for column, value in row.items() if value is None]
        if truncated:
            raise ValueError(
                f"{path}:{row_index}: row has fewer cells than declared header; "
                f"missing cells={truncated!r}"
            )
        rows.append(dict(row))
    return header, rows, hashlib.sha256(payload).hexdigest().upper()


def file_sha256(path: Path, *, uppercase: bool = True) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    return value.upper() if uppercase else value


def require_fields(
    rows: Sequence[Mapping[str, Any]],
    required_columns: Sequence[str],
    path: Path,
) -> None:
    if not rows:
        raise ValueError(f"{path} has no data rows")
    fieldnames = set(rows[0])
    missing = [column for column in required_columns if column not in fieldnames]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")


def write_tsv(
    path: Path,
    rows: Iterable[Mapping[str, Any]],
    fieldnames: Sequence[str],
    *,
    encoding: str = "utf-8",
    extrasaction: Literal["ignore", "raise"] = "ignore",
    formatter: Callable[[Any], str] | None = None,
    lineterminator: str | None = None,
) -> None:
    write_delimited_rows(
        path,
        rows,
        fieldnames,
        delimiter="\t",
        encoding=encoding,
        extrasaction=extrasaction,
        formatter=formatter,
        lineterminator=lineterminator,
    )


def write_delimited_rows(
    path: Path,
    rows: Iterable[Mapping[str, Any]],
    fieldnames: Sequence[str],
    *,
    delimiter: str = ",",
    encoding: str = "utf-8",
    extrasaction: Literal["ignore", "raise"] = "ignore",
    formatter: Callable[[Any], str] | None = None,
    lineterminator: str | None = None,
) -> None:
    with path.open(
        "w",
        newline="",
        encoding=encoding,
        buffering=1024 * 1024,
    ) as handle:
        writer = csv.DictWriter(
            handle,
            **_delimited_writer_kwargs(
                fieldnames,
                delimiter=delimiter,
                extrasaction=extrasaction,
                lineterminator=lineterminator,
            ),
        )
        _write_formatted_delimited_rows(
            writer,
            rows,
            fieldnames,
            extrasaction=extrasaction,
            formatter=formatter,
        )


def render_delimited_rows(
    rows: Sequence[Mapping[str, Any]],
    fieldnames: Sequence[str],
    *,
    delimiter: str = ",",
    extrasaction: Literal["ignore", "raise"] = "ignore",
    formatter: Callable[[Any], str] | None = None,
    lineterminator: str | None = None,
) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        **_delimited_writer_kwargs(
            fieldnames,
            delimiter=delimiter,
            extrasaction=extrasaction,
            lineterminator=lineterminator,
        ),
    )
    _write_formatted_delimited_rows(
        writer,
        rows,
        fieldnames,
        extrasaction=extrasaction,
        formatter=formatter,
    )
    return output.getvalue()


def _delimited_writer_kwargs(
    fieldnames: Sequence[str],
    *,
    delimiter: str,
    extrasaction: Literal["ignore", "raise"],
    lineterminator: str | None,
) -> dict[str, Any]:
    if extrasaction not in {"ignore", "raise"}:
        raise ValueError("extrasaction must be 'ignore' or 'raise'")
    writer_kwargs: dict[str, Any] = {
        "delimiter": delimiter,
        "fieldnames": fieldnames,
        "extrasaction": extrasaction,
    }
    if lineterminator is not None:
        writer_kwargs["lineterminator"] = lineterminator
    return writer_kwargs


def _write_formatted_delimited_rows(
    writer: Any,
    rows: Iterable[Mapping[str, Any]],
    fieldnames: Sequence[str],
    *,
    extrasaction: Literal["ignore", "raise"],
    formatter: Callable[[Any], str] | None,
) -> None:
    value_formatter = formatter or format_diagnostic_value
    fieldname_set = set(fieldnames)
    writer.writeheader()
    for row in rows:
        if extrasaction == "raise":
            extra_fields = set(row) - fieldname_set
            if extra_fields:
                joined = ", ".join(repr(field) for field in extra_fields)
                raise ValueError(
                    "dict contains fields not in fieldnames: " + joined,
                )
        writer.writerow(
            {key: value_formatter(row.get(key, "")) for key in fieldnames},
        )


def escape_excel_formula(value: str) -> str:
    if value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def format_spreadsheet_value(value: object) -> str:
    """Protect display-only text, retaining typed numbers and their precision.

    Machine handoffs must preserve their original strings instead.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return escape_excel_formula(value)
    return str(value)


def format_machine_float(value: float) -> str:
    """Format a machine-handoff float without losing binary precision.

    Python's repr is the shortest decimal that round-trips to the same float;
    display rounding such as ``.6g`` moves an m/z by up to ppm scale.
    """

    if not math.isfinite(value):
        return ""
    if value.is_integer():
        return str(int(value))
    return repr(value)


def format_diagnostic_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return f"{value:.6g}"
    return str(value)


def optional_float(value: object) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if math.isfinite(value) else None
    text = str(value).strip()
    if text.startswith("'"):
        text = text[1:].strip()
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def optional_int(value: object) -> int | None:
    parsed = optional_float(value)
    if parsed is None:
        return None
    return int(parsed)


def positive_int(value: object) -> int | None:
    try:
        parsed = int(text_value(value))
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def required_float(value: object, field: str, label: str) -> float:
    parsed = optional_float(value)
    if parsed is None:
        raise ValueError(f"{label} has invalid {field}: {value!r}")
    return parsed


def bool_value(value: object) -> bool | None:
    normalized = text_value(value).upper()
    if normalized in {"TRUE", "T", "YES", "Y", "1"}:
        return True
    if normalized in {"FALSE", "F", "NO", "N", "0"}:
        return False
    return None


def numeric_equal(left: object, right: object) -> bool:
    left_value = optional_float(left)
    right_value = optional_float(right)
    if left_value is None or right_value is None:
        return text_value(left) == text_value(right)
    return math.isclose(left_value, right_value, rel_tol=1e-6, abs_tol=1e-9)


def text_value(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def has_semicolon_token(value: object, token: str) -> bool:
    return token in set(split_semicolon_labels(value))


def rows_by_text_field(
    rows: Iterable[Mapping[str, str]],
    field: str,
) -> dict[str, tuple[Mapping[str, str], ...]]:
    grouped: dict[str, list[Mapping[str, str]]] = {}
    for row in rows:
        key = text_value(row.get(field))
        if key:
            grouped.setdefault(key, []).append(row)
    return {key: tuple(items) for key, items in grouped.items()}


def identity_family_keys(identity: Mapping[str, str]) -> tuple[str, ...]:
    keys = [text_value(identity.get("peak_hypothesis_id"))]
    keys.extend(split_semicolon_labels(identity.get("source_feature_family_ids")))
    return tuple(dict.fromkeys(key for key in keys if key))


def split_semicolon_labels(value: object) -> list[str]:
    return [part.strip() for part in text_value(value).split(";") if part.strip()]


def required_indexes(
    header: Sequence[object],
    required_columns: Sequence[str],
    sheet_name: str,
) -> dict[str, int]:
    indexes = {
        str(value).strip(): index for index, value in enumerate(header) if value
    }
    missing = [column for column in required_columns if column not in indexes]
    if missing:
        raise ValueError(
            f"{sheet_name}: missing required columns: {', '.join(missing)}"
        )
    return {column: indexes[column] for column in required_columns}
