"""Represent machine-bound and user-facing Alignment Matrix numbers."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from xic_extractor.output.alignment.output_rows import format_value
from xic_extractor.tabular_io import escape_excel_formula

MATRIX_IDENTITY_SCHEMA_VERSION = "untargeted_peak_hypothesis_matrix_identity"
MATRIX_ROW_BINDING_COLUMN = "matrix_row_binding_sha256"
_MATRIX_ROW_BINDING_SCHEMA_VERSION = "alignment_matrix_row_binding"
ALIGNMENT_MATRIX_IDENTITY_COLUMNS = (
    "identity_schema_version",
    "matrix_row_index",
    "Mz",
    "RT",
    "peak_hypothesis_id",
    "row_identity_basis",
    "split_evaluation_status",
    "projection_status",
    "source_feature_family_ids",
    "source_feature_family_count",
    "center_mz_basis",
    "center_rt_basis",
    "center_weight_basis",
    "accepted_cell_count",
    "accepted_sample_count",
    "evidence_status",
    MATRIX_ROW_BINDING_COLUMN,
)


@dataclass(frozen=True, slots=True)
class SerializedPublicMatrixTable:
    """Exact header and row tokens that a public Matrix writer must persist."""

    header: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


def canonical_mz_text(value: object) -> str:
    """Return the shortest text that round-trips a finite machine m/z value."""

    numeric = _finite_float(value, field_name="Mz")
    return "" if numeric is None else repr(numeric)


def public_matrix_mz_text(value: object) -> str:
    """Render m/z for the human-facing Matrix without becoming identity."""

    canonical = canonical_mz_text(value)
    return "" if canonical == "" else f"{float(canonical):.4f}"


def public_matrix_area_text(value: object) -> str:
    """Render a finite Matrix area with six significant scientific digits."""

    numeric = _finite_float(value, field_name="Matrix area")
    return "" if numeric is None else f"{numeric:.5e}"


def project_public_matrix_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    sample_columns: Sequence[str],
) -> Iterable[dict[str, object]]:
    """Project machine rows onto the public ``Mz,RT,*samples`` surface."""

    for row in rows:
        yield {
            "Mz": public_matrix_mz_text(row.get("Mz", "")),
            "RT": row.get("RT", ""),
            **{
                sample: public_matrix_area_text(row.get(sample, ""))
                for sample in sample_columns
            },
        }


def serialize_public_matrix_table(
    rows: Iterable[Mapping[str, object]],
    *,
    sample_columns: Sequence[str],
) -> SerializedPublicMatrixTable:
    """Canonicalize the exact durable tokens used by every Matrix publisher."""

    source_header = ("Mz", "RT", *sample_columns)
    persisted_header = tuple(escape_excel_formula(column) for column in source_header)
    if len(set(persisted_header)) != len(persisted_header):
        raise ValueError("serialized alignment matrix header must be unique")
    serialized_rows = tuple(
        {
            persisted_column: format_value(projected.get(source_column, ""))
            for source_column, persisted_column in zip(
                source_header,
                persisted_header,
                strict=True,
            )
        }
        for projected in project_public_matrix_rows(
            rows,
            sample_columns=sample_columns,
        )
    )
    return SerializedPublicMatrixTable(
        header=persisted_header,
        rows=serialized_rows,
    )


def matrix_mz_binding_matches(display_mz: object, canonical_mz: object) -> bool:
    """Report whether text is the current public projection.

    Persisted consumers must use :func:`validate_matrix_identity_rows`, which
    validates the current identity schema and complete row binding.
    """

    display_text = str(display_mz).strip()
    canonical_input = str(canonical_mz).strip()
    try:
        canonical_text = canonical_mz_text(canonical_input)
    except ValueError:
        return False
    if not display_text or not canonical_text:
        return False
    return display_text == public_matrix_mz_text(canonical_text)


def bind_matrix_identity_rows(
    *,
    matrix_header: Sequence[str],
    matrix_rows: Sequence[Mapping[str, object]],
    identity_columns: Sequence[str],
    identity_rows: Sequence[Mapping[str, object]],
) -> tuple[dict[str, str], ...]:
    """Mint identity rows bound to the complete ordered public Matrix rows."""

    _validate_binding_headers(matrix_header, identity_columns)
    if len(matrix_rows) != len(identity_rows):
        raise ValueError("alignment matrix identity row census mismatch")
    bound_rows: list[dict[str, str]] = []
    for index, (matrix_row, source_identity) in enumerate(
        zip(matrix_rows, identity_rows, strict=True),
        start=1,
    ):
        bound_rows.append(
            bind_matrix_identity_row(
                matrix_header=matrix_header,
                matrix_row=matrix_row,
                identity_columns=identity_columns,
                source_identity=source_identity,
                index=index,
            )
        )
    validate_matrix_identity_rows(
        matrix_header=matrix_header,
        matrix_rows=matrix_rows,
        identity_columns=identity_columns,
        identity_rows=bound_rows,
    )
    return tuple(bound_rows)


def bind_matrix_identity_row(
    *,
    matrix_header: Sequence[str],
    matrix_row: Mapping[str, object],
    identity_columns: Sequence[str],
    source_identity: Mapping[str, object],
    index: int,
) -> dict[str, str]:
    """Bind one row at its global position in a streamed public Matrix."""

    identity = {
        column: (
            canonical_mz_text(source_identity.get(column, ""))
            if column == "Mz"
            else format_value(source_identity.get(column, ""))
        )
        for column in identity_columns
    }
    identity["identity_schema_version"] = MATRIX_IDENTITY_SCHEMA_VERSION
    identity["matrix_row_index"] = str(index)
    identity["Mz"] = canonical_mz_text(identity.get("Mz", ""))
    identity[MATRIX_ROW_BINDING_COLUMN] = ""
    identity[MATRIX_ROW_BINDING_COLUMN] = matrix_row_binding_sha256(
        matrix_header=matrix_header,
        matrix_row=matrix_row,
        identity_columns=identity_columns,
        identity_row=identity,
    )
    return identity


def validate_matrix_identity_rows(
    *,
    matrix_header: Sequence[str],
    matrix_rows: Sequence[Mapping[str, object]],
    identity_columns: Sequence[str],
    identity_rows: Sequence[Mapping[str, object]],
) -> None:
    """Validate an exact ordered Matrix/identity census and every row binding."""

    header_schema = _validate_binding_headers(
        matrix_header,
        identity_columns,
    )
    if len(matrix_rows) != len(identity_rows):
        raise ValueError("alignment matrix identity row census mismatch")
    peak_hypothesis_ids: set[str] = set()
    declared_schema = ""
    for index, (matrix_row, identity_row) in enumerate(
        zip(matrix_rows, identity_rows, strict=True),
        start=1,
    ):
        schema = _declared_identity_schema(identity_row)
        if schema != header_schema:
            raise ValueError(
                "alignment matrix identity schema does not match its columns"
            )
        if not declared_schema:
            declared_schema = schema
        elif schema != declared_schema:
            raise ValueError("alignment matrix identity schema must be uniform")
        peak_hypothesis_id = str(identity_row.get("peak_hypothesis_id", "")).strip()
        if not peak_hypothesis_id or peak_hypothesis_id in peak_hypothesis_ids:
            raise ValueError(
                "alignment matrix identity peak_hypothesis_id must be unique"
            )
        peak_hypothesis_ids.add(peak_hypothesis_id)
        validate_matrix_identity_row(
            matrix_header=matrix_header,
            matrix_row=matrix_row,
            identity_columns=identity_columns,
            identity_row=identity_row,
            expected_index=index,
        )


def validate_matrix_identity_row(
    *,
    matrix_header: Sequence[str],
    matrix_row: Mapping[str, object],
    identity_columns: Sequence[str],
    identity_row: Mapping[str, object],
    expected_index: int,
) -> None:
    """Validate one row when a caller already owns the complete census."""

    header_schema = _validate_binding_headers(
        matrix_header,
        identity_columns,
    )
    _validate_serialized_row_shape(
        matrix_row,
        matrix_header,
        label="Matrix",
    )
    _validate_serialized_row_shape(
        identity_row,
        identity_columns,
        label="identity",
    )
    schema = _declared_identity_schema(identity_row)
    if schema != header_schema:
        raise ValueError("alignment matrix identity schema does not match its columns")
    if str(identity_row.get("matrix_row_index", "")).strip() != str(expected_index):
        raise ValueError(
            "alignment matrix identity rows must be an exact ordered census"
        )
    _validate_matrix_identity_row(
        matrix_header=matrix_header,
        matrix_row=matrix_row,
        identity_columns=identity_columns,
        identity_row=identity_row,
    )


def matrix_row_binding_sha256(
    *,
    matrix_header: Sequence[str],
    matrix_row: Mapping[str, object],
    identity_columns: Sequence[str],
    identity_row: Mapping[str, object],
) -> str:
    """Hash one complete public row together with its complete identity row."""

    _validate_binding_headers(matrix_header, identity_columns)
    _validate_serialized_row_shape(
        matrix_row,
        matrix_header,
        label="Matrix",
    )
    _validate_serialized_row_shape(
        identity_row,
        identity_columns,
        label="identity",
    )
    identity_binding_columns = tuple(
        column for column in identity_columns if column != MATRIX_ROW_BINDING_COLUMN
    )
    basis = {
        "schema_version": _MATRIX_ROW_BINDING_SCHEMA_VERSION,
        "matrix_header": list(matrix_header),
        "matrix_row": [str(matrix_row.get(column, "")) for column in matrix_header],
        "identity_columns": list(identity_binding_columns),
        "identity_row": [
            str(identity_row.get(column, "")) for column in identity_binding_columns
        ],
    }
    payload = json.dumps(
        basis,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_binding_headers(
    matrix_header: Sequence[str],
    identity_columns: Sequence[str],
) -> str:
    if tuple(matrix_header[:2]) != ("Mz", "RT"):
        raise ValueError("alignment matrix identity requires an Mz/RT Matrix")
    if len(set(matrix_header)) != len(matrix_header):
        raise ValueError("alignment matrix header must be unique")
    if len(set(identity_columns)) != len(identity_columns):
        raise ValueError("alignment matrix identity columns must be unique")
    identity_column_set = set(identity_columns)
    if identity_column_set == set(ALIGNMENT_MATRIX_IDENTITY_COLUMNS):
        return MATRIX_IDENTITY_SCHEMA_VERSION
    expected = set(ALIGNMENT_MATRIX_IDENTITY_COLUMNS)
    missing = sorted(expected - identity_column_set)
    unexpected = sorted(identity_column_set - expected)
    details = []
    if missing:
        details.append("missing=" + ",".join(missing))
    if unexpected:
        details.append("unexpected=" + ",".join(unexpected))
    if not details:
        details.append("schema columns are incomplete")
    raise ValueError(
        "alignment matrix identity columns do not match current schema: "
        + "; ".join(details)
    )


def _validate_serialized_row_shape(
    row: Mapping[str, object],
    columns: Sequence[str],
    *,
    label: str,
) -> None:
    if set(row) != set(columns):
        raise ValueError(
            f"alignment matrix {label} row fields must exactly match declared columns"
        )
    if any(row[column] is None for column in columns):
        raise ValueError(f"alignment matrix {label} row contains missing cells")


def _validate_matrix_identity_row(
    *,
    matrix_header: Sequence[str],
    matrix_row: Mapping[str, object],
    identity_columns: Sequence[str],
    identity_row: Mapping[str, object],
) -> None:
    display_mz = str(matrix_row.get("Mz", "")).strip()
    canonical_mz = str(identity_row.get("Mz", "")).strip()
    if display_mz != public_matrix_mz_text(canonical_mz):
        raise ValueError("identity Mz binding mismatch")
    binding = str(identity_row.get(MATRIX_ROW_BINDING_COLUMN, "")).strip()
    expected = matrix_row_binding_sha256(
        matrix_header=matrix_header,
        matrix_row=matrix_row,
        identity_columns=identity_columns,
        identity_row=identity_row,
    )
    if binding != expected:
        raise ValueError("identity row binding mismatch")
    if not _exact_finite_numeric_equal(
        matrix_row.get("RT", ""),
        identity_row.get("RT", ""),
    ):
        raise ValueError("alignment matrix identity RT binding mismatch")


def _declared_identity_schema(identity_row: Mapping[str, object]) -> str:
    schema = str(identity_row.get("identity_schema_version", "")).strip()
    if schema != MATRIX_IDENTITY_SCHEMA_VERSION:
        raise ValueError("alignment matrix identity schema is missing or unknown")
    return schema


def _exact_finite_numeric_equal(left: object, right: object) -> bool:
    try:
        left_value = float(str(left).strip())
        right_value = float(str(right).strip())
    except ValueError:
        return False
    return (
        math.isfinite(left_value)
        and math.isfinite(right_value)
        and left_value == right_value
    )


def _finite_float(value: object, *, field_name: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        numeric = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    return numeric if math.isfinite(numeric) else None
