from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.tabular_io import escape_excel_formula

if TYPE_CHECKING:
    from xic_extractor.alignment.production_decisions import ProductionCellDecision


def safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def matrix_area(cell: AlignedCell | None) -> str:
    if cell is None or cell.status not in {"detected", "backfilled"}:
        return ""
    area = cell.matrix_area
    if area is None or not math.isfinite(area) or area <= 0:
        return ""
    return format_float(area)


def production_matrix_area(decision: ProductionCellDecision | None) -> str:
    if decision is None or not decision.write_matrix_value:
        return ""
    assert decision.matrix_value is not None
    return format_float(decision.matrix_value)


def format_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return format_float(value)
    if isinstance(value, Path):
        return escape_excel_formula(str(value))
    return escape_excel_formula(str(value))


def format_float(value: float) -> str:
    return f"{value:.6g}"
