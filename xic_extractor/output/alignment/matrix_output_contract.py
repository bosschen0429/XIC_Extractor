"""Represent machine-bound and user-facing Alignment Matrix numbers."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.output.alignment.output_rows import format_value
from xic_extractor.tabular_io import escape_excel_formula, read_tsv_with_header

MATRIX_NAME = "alignment_matrix.tsv"
RUN_METADATA_NAME = "alignment_run_metadata.json"
_LEGACY_REVIEW_NAME = "alignment_review.tsv"
MatrixRowIdentity = tuple[str, float]

MATRIX_REVIEW_COLUMNS = (
    "feature_id",
    "matrix_row_index",
    "group_center_mz",
    "group_center_rt",
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


def locate_matrix_identity(directory: Path, run: Mapping[str, object]) -> Path:
    """New outputs bind the Matrix in run metadata; admit existing exports too."""
    if "matrix_identity" in run:
        return directory / RUN_METADATA_NAME
    legacy = directory / _LEGACY_REVIEW_NAME
    if legacy.is_file():
        return legacy
    raise ValueError("找不到矩陣列身分；請使用包含發布資訊的 Alignment 結果。")


def read_matrix_identities(
    path: Path,
    *,
    matrix_header: Sequence[str],
    matrix_rows: Sequence[Mapping[str, object]],
    matrix_digest: str,
) -> tuple[MatrixRowIdentity, ...]:
    """Read full-precision mass and writer-assigned IDs in Matrix row order."""
    if path.name == _LEGACY_REVIEW_NAME:
        columns, review_rows = read_tsv_with_header(path, encoding="utf-8-sig")
        return tuple(
            (str(row["feature_id"]), float(str(row["group_center_mz"])))
            for row in matrix_review_rows(
                matrix_header=matrix_header,
                matrix_rows=matrix_rows,
                review_columns=columns,
                review_rows=review_rows,
            )
        )
    run = json.loads(path.read_text(encoding="utf-8"))
    identity = run.get("matrix_identity") if isinstance(run, dict) else None
    if not isinstance(identity, dict) or identity.get("matrix_sha256") != matrix_digest:
        raise ValueError("矩陣列身分的 SHA-256 與目前的 Matrix 不符。")
    rows = identity.get("rows")
    if not isinstance(rows, list) or len(rows) != len(matrix_rows):
        raise ValueError("矩陣列身分與 Matrix 的列數不符。")
    features: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError("無效的矩陣列身分。")
        feature, mz = row
        if not isinstance(feature, str) or not feature.strip() or feature in features:
            raise ValueError("矩陣 feature ID 必須非空且不可重複。")
        exact_mz = _finite_float(mz, field_name="matrix_identity.mz")
        if exact_mz is None:
            raise ValueError("矩陣列身分缺少全精度 m/z。")
        features[feature] = exact_mz
    return tuple(features.items())


def matrix_review_rows(
    *,
    matrix_header: Sequence[str],
    matrix_rows: Sequence[Mapping[str, object]],
    review_columns: Sequence[str],
    review_rows: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    """Validate Review and return its Matrix subset in explicit Matrix order.

    Archive rows remain in Review with an empty ``matrix_row_index``. Its
    physical row order is not a Matrix key.
    """
    if len(set(review_columns)) != len(review_columns):
        raise ValueError("alignment Review columns must be unique")
    missing = set(MATRIX_REVIEW_COLUMNS) - set(review_columns)
    if missing:
        raise ValueError(
            "alignment Review requires current fields: " + ",".join(sorted(missing))
        )
    features: set[str] = set()
    projected: dict[int, Mapping[str, object]] = {}
    for row in review_rows:
        _validate_serialized_row_shape(row, review_columns, label="Review")
        feature = str(row["feature_id"]).strip()
        if not feature or feature in features:
            raise ValueError("alignment Review feature_id must be nonempty and unique")
        features.add(feature)
        position = str(row["matrix_row_index"]).strip()
        if not position:
            continue
        try:
            index = int(position)
        except ValueError as exc:
            raise ValueError(
                "Review matrix_row_index must be a positive integer"
            ) from exc
        if position != str(index) or index < 1 or index in projected:
            raise ValueError("Review Matrix positions must be positive and unique")
        projected[index] = row
    if set(projected) != set(range(1, len(matrix_rows) + 1)):
        raise ValueError("alignment Matrix/Review row census mismatch")
    ordered = tuple(projected[index] for index in range(1, len(matrix_rows) + 1))
    for matrix, review in zip(matrix_rows, ordered, strict=True):
        _validate_serialized_row_shape(matrix, matrix_header, label="Matrix")
        if str(matrix["Mz"]).strip() != public_matrix_mz_text(
            review["group_center_mz"]
        ):
            raise ValueError("Review group_center_mz binding mismatch")
        if not _exact_finite_numeric_equal(matrix["RT"], review["group_center_rt"]):
            raise ValueError("Review group_center_rt binding mismatch")
    return ordered


def _validate_serialized_row_shape(
    row: Mapping[str, object], columns: Sequence[str], *, label: str
) -> None:
    if set(row) != set(columns):
        raise ValueError(
            f"alignment {label} row fields must exactly match declared columns"
        )
    if any(row[column] is None for column in columns):
        raise ValueError(f"alignment {label} row contains missing cells")


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
