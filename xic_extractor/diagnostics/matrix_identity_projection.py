from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from xic_extractor.tabular_io import (
    identity_family_keys,
    numeric_equal,
    positive_int,
    read_tsv_required,
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
    identity_rows = read_tsv_required(
        alignment_matrix_identity_tsv,
        ("matrix_row_index", "peak_hypothesis_id"),
    )
    return matrix_values_by_identity(
        matrix_rows=matrix_rows,
        matrix_identity_rows=identity_rows,
        matrix_header=matrix_header,
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
    key_mode: MatrixIdentityKeyMode = "peak_hypothesis",
    include_blank: bool = True,
    requested_keys: set[tuple[str, str]] | None = None,
    duplicate_policy: DuplicatePolicy = "error",
) -> dict[tuple[str, str], str]:
    identity_by_index = _identity_by_matrix_index(matrix_identity_rows)
    matrix_has_family_id = matrix_rows and "feature_family_id" in matrix_rows[0]
    if matrix_rows and not identity_by_index and not matrix_has_family_id:
        raise ValueError("matrix identity rows are required for Mz/RT matrices")

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
        if not numeric_equal(before.get(key, ""), after.get(key, ""))
    )


def _identity_by_matrix_index(
    rows: Sequence[Mapping[str, object]],
) -> dict[int, Mapping[str, object]]:
    return {
        row_index: row
        for row in rows
        if (row_index := positive_int(row.get("matrix_row_index"))) is not None
    }


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
