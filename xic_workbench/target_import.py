"""Target-list import adapters for the local HTML workbench."""

from __future__ import annotations

from pathlib import Path

from xic_extractor.configuration.csv_io import read_target_draft_rows
from xic_extractor.configuration.target_workbook import (
    DEFAULT_RT_WINDOW,
    _assign_istd_pairs,
    parse_dna_sheet,
    parse_rna_sheet,
)


def import_targets(path: Path, *, sheet: str = "both") -> list[dict[str, str]]:
    """Import an editable target draft without applying runtime validation."""

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        return read_target_draft_rows(source)
    if suffix not in {".xlsx", ".xls"}:
        raise ValueError(f"{source}: expected CSV, TSV, XLSX, or XLS target input")

    normalized_sheet = sheet.strip().lower()
    if normalized_sheet not in {"dna", "rna", "both"}:
        raise ValueError("sheet must be DNA, RNA, or both")

    import openpyxl

    workbook = openpyxl.load_workbook(source, data_only=True, read_only=False)
    try:
        targets = []
        if normalized_sheet in {"dna", "both"} and "DNA" in workbook.sheetnames:
            targets.extend(
                parse_dna_sheet(workbook["DNA"], rt_window=DEFAULT_RT_WINDOW)
            )
        if normalized_sheet in {"rna", "both"} and "RNA" in workbook.sheetnames:
            targets.extend(
                parse_rna_sheet(workbook["RNA"], rt_window=DEFAULT_RT_WINDOW)
            )
        _assign_istd_pairs(targets)
        return [
            {key: str(value) for key, value in target.items()} for target in targets
        ]
    finally:
        workbook.close()
