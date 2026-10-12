"""Reusable RT-check workbook conversion for Targeted target lists."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import openpyxl

from xic_extractor.configuration.csv_io import TARGET_FIELDS as _FIELDNAMES
from xic_extractor.tabular_io import write_delimited_rows

_ISTD_PREFIXES = (
    "d2-",
    "d3-",
    "d4-",
    "d5-",
    "d6-",
    "d7-",
    "d8-",
    "d9-",
    "15N",
    "13C",
    "[13C",
    "[15N",
)

TargetRow = dict[str, str | float | int | bool]


def _is_istd(label: str) -> bool:
    return any(label.startswith(prefix) for prefix in _ISTD_PREFIXES)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float))


def _find_header_row(rows: list[tuple], required_cols: list[str]) -> int | None:
    for index, row in enumerate(rows):
        row_values = {str(value) for value in row if value is not None}
        if all(column in row_values for column in required_cols):
            return index
    return None


def _col_index(header: tuple, name: str) -> int | None:
    for index, value in enumerate(header):
        if value == name:
            return index
    return None


def _all_col_indices(header: tuple, name: str) -> list[int]:
    return [index for index, value in enumerate(header) if value == name]


def _strip_istd_prefix(label: str) -> str:
    if label.startswith("["):
        index = label.find("]-")
        if index >= 0:
            return label[index + 2 :]
    match = re.match(r"^(?:d\d+-|\d+[CN]\d*-)+", label)
    if match:
        return label[match.end() :]
    return label


def assign_istd_pairs(targets: list[TargetRow]) -> None:
    """Prefill only unique exact isotope-name counterparts; never infer identity.

    Existing roles and pair choices win. Mass and RT remain method inputs, not
    evidence for guessing a label relationship during table editing.
    """
    for target in targets:
        if not str(target.get("role", "")).strip():
            target["role"] = "istd" if _is_istd(str(target["label"])) else "analyte"
    istds: dict[str, list[str]] = {}
    for target in targets:
        if target.get("role") == "istd":
            label = str(target["label"])
            core = _strip_istd_prefix(label).casefold()
            if core != label.casefold():
                istds.setdefault(core, []).append(label)
    analytes: dict[str, list[TargetRow]] = {}
    for target in targets:
        if target.get("role") != "istd":
            analytes.setdefault(str(target["label"]).casefold(), []).append(target)
    for name, rows in analytes.items():
        matches = istds.get(name, [])
        if len(rows) == len(matches) == 1 and not rows[0].get("istd_pair"):
            rows[0]["istd_pair"] = matches[0]


def parse_dna_sheet(
    worksheet: openpyxl.worksheet.worksheet.Worksheet,
) -> list[TargetRow]:
    """Parse DNA workbook blocks into editable Targeted rows."""

    rows = list(worksheet.iter_rows(values_only=True))
    header_index = _find_header_row(rows, ["m/z", "Anticipated adducts"])
    if header_index is None:
        print(
            "Warning: DNA sheet – could not find header row with 'm/z' "
            "and 'Anticipated adducts'",
            file=sys.stderr,
        )
        return []

    header = rows[header_index]
    adducts_columns = _all_col_indices(header, "Anticipated adducts")
    mz_columns = _all_col_indices(header, "m/z")
    rt_columns = _all_col_indices(header, "RT")
    targets: list[TargetRow] = []

    for adducts_column in adducts_columns:
        mz_column = max(
            (column for column in mz_columns if column < adducts_column),
            default=None,
        )
        rt_column = min(
            (column for column in rt_columns if column > adducts_column),
            default=None,
        )
        if mz_column is None or rt_column is None:
            continue

        max_column = max(adducts_column, mz_column, rt_column)
        for row in rows[header_index + 1 :]:
            if len(row) <= max_column:
                continue
            label_raw = row[adducts_column]
            mz_raw = row[mz_column]
            rt_raw = row[rt_column]
            if not label_raw or not _is_number(mz_raw) or not _is_number(rt_raw):
                continue

            label = str(label_raw).strip()
            retention_time = float(rt_raw)
            targets.append(
                {
                    "label": label,
                    "mz": round(float(mz_raw), 6),
                    "expected_rt_min": retention_time,
                    "cid_nl_tag": "dR",
                    "role": "istd" if _is_istd(label) else "analyte",
                    "istd_pair": "",
                }
            )
    return targets


def parse_rna_sheet(
    worksheet: openpyxl.worksheet.worksheet.Worksheet,
) -> list[TargetRow]:
    """Parse an RNA workbook sheet into editable Targeted rows."""

    rows = list(worksheet.iter_rows(values_only=True))
    header_index = _find_header_row(rows, ["Abbre.", "[M+H]+"])
    if header_index is None:
        print(
            "Warning: RNA sheet – could not find header row with 'Abbre.' and '[M+H]+'",
            file=sys.stderr,
        )
        return []

    header = rows[header_index]
    abbreviation_column = _col_index(header, "Abbre.")
    mh_column = _col_index(header, "[M+H]+")
    mhr_column = _col_index(header, "[M+H-R]+")
    methyl_column = _col_index(header, "[M+H-R-CH2]+")
    rt_columns = _all_col_indices(header, "RT")
    rt_column = min(rt_columns) if rt_columns else None
    if None in (abbreviation_column, mh_column, rt_column):
        print("Warning: RNA sheet – missing required columns", file=sys.stderr)
        return []

    assert abbreviation_column is not None
    assert mh_column is not None
    assert rt_column is not None
    max_column = max(
        column
        for column in (
            abbreviation_column,
            mh_column,
            mhr_column,
            methyl_column,
            rt_column,
        )
        if column is not None
    )
    targets: list[TargetRow] = []
    for row in rows[header_index + 1 :]:
        if len(row) <= max_column:
            continue
        label_raw = row[abbreviation_column]
        mz_raw = row[mh_column]
        rt_raw = row[rt_column]
        if not label_raw or not _is_number(mz_raw) or not _is_number(rt_raw):
            continue

        cid_nl_tag: str | None = None
        if mhr_column is not None and _is_number(row[mhr_column]):
            cid_nl_tag = "R"
        elif methyl_column is not None and _is_number(row[methyl_column]):
            cid_nl_tag = "MeR"
        if cid_nl_tag is None:
            continue

        label = str(label_raw).strip()
        retention_time = float(rt_raw)
        targets.append(
            {
                "label": label,
                "mz": round(float(mz_raw), 6),
                "expected_rt_min": retention_time,
                "cid_nl_tag": cid_nl_tag,
                "role": "istd" if _is_istd(label) else "analyte",
                "istd_pair": "",
            }
        )
    return targets


def write_targets_csv(targets: list[TargetRow], output_path: Path) -> None:
    """Write the public XIC Extractor targets CSV contract."""

    write_delimited_rows(
        output_path,
        targets,
        _FIELDNAMES,
        delimiter=",",
        encoding="utf-8",
        formatter=_format_targets_csv_value,
    )
    print(f"Written {len(targets)} targets to {output_path}")


def _format_targets_csv_value(value: object) -> str:
    return "" if value is None else str(value)
