from __future__ import annotations

import math
from pathlib import Path

from xic_extractor.tabular_io import escape_excel_formula


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
