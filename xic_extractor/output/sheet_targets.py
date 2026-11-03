from __future__ import annotations

from openpyxl.utils import get_column_letter

from xic_extractor.configuration.models import Target
from xic_extractor.output.workbook_styles import (
    _add_table,
    _fit_columns,
    _prepare_sheet,
    _style_body,
    _write_header,
)
from xic_extractor.output.workbook_values import _excel_text

TARGETS_HEADERS = (
    "Label",
    "Role",
    "ISTD Pair",
    "m/z",
    "RT min",
    "RT max",
    "ppm tol",
    "NL (Da)",
    "Expected product m/z",
    "NL ppm warn",
    "NL ppm max",
    "Sample applicability",
)
_TEXT = frozenset({"Label", "Role", "ISTD Pair", "Sample applicability"})
_FORMATS = {
    "m/z": "0.0000",
    "Expected product m/z": "0.0000",
    "NL (Da)": "0.0000",
    "RT min": "0.00",
    "RT max": "0.00",
}


def _build_targets_sheet(ws, targets: list[Target]) -> None:
    delta_headers = (
        ("Delta Mass Method", "Quant Position")
        if any(t.delta_mass_settings for t in targets)
        else ()
    )
    headers = (*TARGETS_HEADERS, *delta_headers)
    numeric = frozenset(TARGETS_HEADERS) - _TEXT
    _prepare_sheet(ws, repeat_rows="1:1")
    _write_header(ws, 1, headers, numeric)
    for row, target in enumerate(targets, start=2):
        expected_product = (
            target.mz - target.neutral_loss_da
            if target.neutral_loss_da is not None
            else None
        )
        values = (
            _excel_text(target.label),
            "ISTD" if target.is_istd else "Analyte",
            _excel_text(target.istd_pair),
            target.mz,
            target.rt_min,
            target.rt_max,
            target.ppm_tol,
            target.neutral_loss_da,
            expected_product,
            target.nl_ppm_warn,
            target.nl_ppm_max,
            target.sample_applicability,
        ) + (
            tuple(
                _excel_text(value)
                for value in (target.delta_mass_method, target.quant_position)
            )
            if delta_headers
            else ()
        )
        for column, (header, value) in enumerate(
            zip(headers, values, strict=True), start=1
        ):
            cell = ws.cell(row, column, value)
            _style_body(cell, numeric=header in numeric)
            if header in _FORMATS and isinstance(value, float):
                cell.number_format = _FORMATS[header]
    reference = f"A1:{get_column_letter(len(headers))}{max(2, len(targets) + 1)}"
    _add_table(ws, "TargetedDefinitions", reference)
    _fit_columns(ws, maximum=28)
    ws.freeze_panes = "A2"
