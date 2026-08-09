from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from xic_extractor.alignment.matrix_output_contract import (
    validate_matrix_identity_rows,
)
from xic_extractor.tabular_io import (
    identity_family_keys,
    positive_int,
    read_tsv_with_header,
    text_value,
)

MatrixIdentityKeyMode = Literal["peak_hypothesis", "family_aliases"]
DuplicatePolicy = Literal["error", "last"]

_MATRIX_META_COLUMNS = frozenset({"Mz", "RT", "feature_family_id", "neutral_loss_tag"})


def matrix_values_from_tsv(
    *,
    alignment_matrix_tsv: Path,
    alignment_matrix_identity_tsv: Path,
    key_mode: MatrixIdentityKeyMode = "peak_hypothesis",
    include_blank: bool = True,
    requested_keys: set[tuple[str, str]] | None = None,
    duplicate_policy: DuplicatePolicy = "error",
) -> dict[tuple[str, str], str]:
    matrix_header, matrix_rows = read_tsv_with_header(alignment_matrix_tsv)
    identity_header, identity_rows = read_tsv_with_header(
        alignment_matrix_identity_tsv
    )
    return matrix_values_by_identity(
        matrix_rows=matrix_rows,
        matrix_identity_rows=identity_rows,
        matrix_header=matrix_header,
        matrix_identity_header=identity_header,
        key_mode=key_mode,
        include_blank=include_blank,
        requested_keys=requested_keys,
        duplicate_policy=duplicate_policy,
    )


def matrix_values_by_identity(
    *,
    matrix_rows: Sequence[Mapping[str, object]],
    matrix_identity_rows: Sequence[Mapping[str, object]],
    matrix_header: Sequence[str] | None = None,
    matrix_identity_header: Sequence[str] | None = None,
    key_mode: MatrixIdentityKeyMode = "peak_hypothesis",
    include_blank: bool = True,
    requested_keys: set[tuple[str, str]] | None = None,
    duplicate_policy: DuplicatePolicy = "error",
) -> dict[tuple[str, str], str]:
    resolved_matrix_header = tuple(
        matrix_header or (tuple(matrix_rows[0]) if matrix_rows else ())
    )
    matrix_has_family_id = "feature_family_id" in resolved_matrix_header
    is_public_matrix = resolved_matrix_header[:2] == ("Mz", "RT")
    if is_public_matrix and not matrix_has_family_id:
        if matrix_rows and not matrix_identity_rows:
            raise ValueError("matrix identity rows are required for Mz/RT matrices")
        resolved_identity_header = tuple(
            matrix_identity_header
            or (tuple(matrix_identity_rows[0]) if matrix_identity_rows else ())
        )
        validate_matrix_identity_rows(
            matrix_header=resolved_matrix_header,
            matrix_rows=matrix_rows,
            identity_columns=resolved_identity_header,
            identity_rows=matrix_identity_rows,
        )
    identity_by_index = _identity_by_matrix_index(matrix_identity_rows)

    sample_columns = _sample_columns(matrix_rows, matrix_header)
    values: dict[tuple[str, str], str] = {}
    for index, matrix_row in enumerate(matrix_rows, start=1):
        identity = identity_by_index.get(index, {})
        row_keys = _row_keys(
            matrix_row=matrix_row,
            identity_row=identity,
            key_mode=key_mode,
        )
        if not row_keys:
            raise ValueError(f"matrix row {index} has no peak_hypothesis_id")
        for row_key in row_keys:
            for sample in sample_columns:
                key = (row_key, sample)
                if requested_keys is not None and key not in requested_keys:
                    continue
                value = text_value(matrix_row.get(sample))
                if not include_blank and not value:
                    continue
                if key in values and duplicate_policy == "error":
                    raise ValueError(f"duplicate matrix value key: {key}")
                values[key] = value
    return values


def matrix_value_diffs(
    before: Mapping[tuple[str, str], str],
    after: Mapping[tuple[str, str], str],
) -> tuple[tuple[tuple[str, str], str, str], ...]:
    keys = sorted(set(before) | set(after))
    return tuple(
        (key, before.get(key, ""), after.get(key, ""))
        for key in keys
        if not _exact_decimal_equal(before.get(key, ""), after.get(key, ""))
    )


def _identity_by_matrix_index(
    rows: Sequence[Mapping[str, object]],
) -> dict[int, Mapping[str, object]]:
    result: dict[int, Mapping[str, object]] = {}
    for row in rows:
        row_index = positive_int(row.get("matrix_row_index"))
        if row_index is None:
            raise ValueError("matrix identity row index must be a positive integer")
        if row_index in result:
            raise ValueError("matrix identity row index must be unique")
        result[row_index] = row
    return result


def _exact_decimal_equal(left: object, right: object) -> bool:
    left_text = text_value(left)
    right_text = text_value(right)
    if not left_text or not right_text:
        return left_text == right_text
    try:
        left_value = Decimal(left_text)
        right_value = Decimal(right_text)
    except InvalidOperation:
        return False
    return (
        left_value.is_finite()
        and right_value.is_finite()
        and left_value == right_value
    )


def _row_keys(
    *,
    matrix_row: Mapping[str, object],
    identity_row: Mapping[str, object],
    key_mode: MatrixIdentityKeyMode,
) -> tuple[str, ...]:
    if key_mode == "family_aliases":
        return _identity_family_keys(identity_row, matrix_row)
    peak_hypothesis_id = text_value(
        identity_row.get("peak_hypothesis_id")
    ) or text_value(matrix_row.get("feature_family_id"))
    return (peak_hypothesis_id,) if peak_hypothesis_id else ()


def _identity_family_keys(
    identity_row: Mapping[str, object],
    matrix_row: Mapping[str, object],
) -> tuple[str, ...]:
    keys = identity_family_keys(
        {str(key): text_value(value) for key, value in identity_row.items()}
    )
    if keys:
        return keys
    family_id = text_value(matrix_row.get("feature_family_id"))
    return (family_id,) if family_id else ()


def _sample_columns(
    matrix_rows: Sequence[Mapping[str, object]],
    matrix_header: Sequence[str] | None,
) -> tuple[str, ...]:
    columns: Sequence[str]
    if matrix_header is not None:
        columns = tuple(matrix_header)
    elif matrix_rows:
        columns = tuple(str(column) for column in matrix_rows[0])
    else:
        columns = ()
    return tuple(column for column in columns if _is_sample_column(column))


def _is_sample_column(column: str) -> bool:
    return (
        column not in _MATRIX_META_COLUMNS
        and not column.startswith("_")
        and not column.startswith("family_center_")
    )
