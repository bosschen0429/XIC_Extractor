from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from xic_extractor.alignment.output_rows import escape_excel_formula
from xic_extractor.alignment.quant_matrix_version import (
    CELL_PROVENANCE_COLUMNS,
    EXPECTED_DIFF_SUMMARY_COLUMNS,
    ROW_SUMMARY_COLUMNS,
    QuantMatrixVersionRows,
)


def write_quant_matrix_version_xlsx(
    path: Path,
    *,
    matrix_header: Sequence[str],
    quant_matrix_version: QuantMatrixVersionRows,
    metadata: Mapping[str, str],
) -> Path:
    """Render an already-authorized QuantMatrixVersion without recomputation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    matrix_sheet = workbook.active
    matrix_sheet.title = "Matrix"
    _write_mapping_rows(
        matrix_sheet,
        matrix_header,
        quant_matrix_version.quant_matrix_rows,
        matrix_values=True,
    )
    _write_mapping_rows(
        workbook.create_sheet("CellProvenance"),
        CELL_PROVENANCE_COLUMNS,
        quant_matrix_version.cell_provenance_rows,
    )
    _write_mapping_rows(
        workbook.create_sheet("RowSummary"),
        ROW_SUMMARY_COLUMNS,
        quant_matrix_version.row_summary_rows,
    )
    _write_mapping_rows(
        workbook.create_sheet("ExpectedDiff"),
        EXPECTED_DIFF_SUMMARY_COLUMNS,
        quant_matrix_version.expected_diff_summary_rows,
    )
    _write_metadata_sheet(
        workbook.create_sheet("Metadata"),
        dict(metadata),
    )
    workbook.save(path)
    return path


def _write_mapping_rows(
    sheet: Any,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, str]],
    *,
    matrix_values: bool = False,
) -> None:
    _append_xlsx_row(sheet, columns)
    for row in rows:
        values: list[object] = [row.get(column, "") for column in columns]
        if matrix_values:
            values = [_xlsx_quant_matrix_value(value) for value in values]
        _append_xlsx_row(sheet, values)


def _write_metadata_sheet(sheet: Any, metadata: dict[str, str]) -> None:
    _append_xlsx_row(sheet, ["key", "value"])
    for key in sorted(metadata):
        _append_xlsx_row(sheet, [key, metadata[key]])


def _append_xlsx_row(sheet: Any, values: Sequence[object]) -> None:
    sheet.append([_xlsx_value(value) for value in values])


def _xlsx_value(value: object) -> object:
    if isinstance(value, str):
        return escape_excel_formula(value)
    return value


def _xlsx_quant_matrix_value(value: object) -> float | None:
    return float(str(value)) if value != "" else None
