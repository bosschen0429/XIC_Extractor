"""Canonical primitives shared by the CID-NL oracle contracts.

This module owns only the format-level error, canonical bytes/hashes, and
strict scalar field readers used by the oracle validators. It does not own
manifest schemas, review policy, verdict reduction, or product authority.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class OracleContractError(ValueError):
    """Raised when a CID-NL oracle contract is malformed or unbound."""


def _required_text(row: Mapping[str, Any], field: str) -> str:
    value = str(row.get(field, "") or "").strip()
    if not value:
        raise OracleContractError(f"{field} is required")
    return value


def _require_schema_version(
    row: Mapping[str, Any],
    expected: str,
) -> None:
    if _required_text(row, "schema_version") != expected:
        raise OracleContractError(
            f"schema_version must be {expected}"
        )


def _require_fields(
    row: Mapping[str, Any],
    fields: Iterable[str],
) -> None:
    for field in fields:
        _required_text(row, field)


def canonical_row_sha256(
    row: Mapping[str, Any],
    *,
    hash_field: str = "source_row_sha256",
) -> str:
    """Hash one canonical row while excluding its self-referential digest."""

    payload = {
        str(key): value
        for key, value in row.items()
        if str(key) != hash_field
    }
    return sha256_canonical_json(payload)


def validate_canonical_row_hash(
    row: Mapping[str, Any],
    *,
    hash_field: str = "source_row_sha256",
) -> None:
    expected = _required_text(row, hash_field)
    if not _SHA256_RE.fullmatch(expected):
        raise OracleContractError(f"{hash_field} must be SHA256")
    if expected.lower() != canonical_row_sha256(row, hash_field=hash_field):
        raise OracleContractError(f"{hash_field} does not bind canonical row")


def sha256_canonical_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(payload)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_utf8_text_normalized_to_lf(value: bytes) -> str:
    """Hash UTF-8 text after normalizing platform line endings to LF."""

    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OracleContractError(
            "normalized text hash input must be UTF-8"
        ) from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return sha256_bytes(normalized.encode("utf-8"))


def canonical_tsv_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    if not rows:
        return b""
    columns = sorted({str(key) for row in rows for key in row})
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=columns,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    return output.getvalue().encode("utf-8")


def _parse_tsv_bytes(value: bytes) -> list[dict[str, str]]:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OracleContractError("TSV bytes must be UTF-8") from exc
    return list(csv.DictReader(io.StringIO(text), delimiter="\t"))


def _boolean(row: Mapping[str, Any], field: str) -> bool:
    value = row.get(field)
    if value in {True, "TRUE", "true", "1", 1}:
        return True
    if value in {False, "FALSE", "false", "0", 0}:
        return False
    raise OracleContractError(f"{field} must be boolean")


def _threshold_values_equal(left: str, right: str) -> bool:
    try:
        return math.isclose(
            float(left),
            float(right),
            rel_tol=1e-12,
            abs_tol=1e-15,
        )
    except ValueError:
        return left == right


def _finite_number(row: Mapping[str, Any], field: str) -> float:
    value = _optional_number(row, field)
    if value is None:
        raise OracleContractError(f"{field} must be finite")
    return value


def _optional_number(row: Mapping[str, Any], field: str) -> float | None:
    value = row.get(field)
    if value is None:
        return None
    if value in {"", "not_applicable"}:
        return None
    if isinstance(value, bool):
        raise OracleContractError(f"{field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise OracleContractError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed):
        raise OracleContractError(f"{field} must be finite")
    return parsed


def _positive_integer(row: Mapping[str, Any], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool):
        raise OracleContractError(f"{field} must be a positive integer")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise OracleContractError(
            f"{field} must be a positive integer"
        ) from exc
    if parsed <= 0 or str(parsed) != str(value).strip():
        raise OracleContractError(f"{field} must be a positive integer")
    return parsed


def _nonnegative_integer(row: Mapping[str, Any], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool):
        raise OracleContractError(f"{field} must be a non-negative integer")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise OracleContractError(
            f"{field} must be a non-negative integer"
        ) from exc
    if parsed < 0 or str(parsed) != str(value).strip():
        raise OracleContractError(f"{field} must be a non-negative integer")
    return parsed


def _tokens(row: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = row.get(field, "")
    if isinstance(value, str):
        return tuple(token.strip() for token in value.split("|") if token.strip())
    if isinstance(value, Iterable):
        return tuple(str(token).strip() for token in value if str(token).strip())
    raise OracleContractError(f"{field} must be a token list")
