"""Reusable RT-check workbook conversion for Targeted target lists."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import openpyxl

from xic_extractor.tabular_io import write_delimited_rows

NL_DR = 116.0474
NL_R = 132.0423
NL_MER = 146.0579

DEFAULT_PPM_TOL = 20
DEFAULT_NL_PPM_WARN = 20
DEFAULT_NL_PPM_MAX = 50
DEFAULT_RT_WINDOW = 1.5

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
_MAX_RT_DIFF_MIN = 3.0

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
    match = re.match(r"^(?:d\d+|(?:\d+[CN]\d*-))+", label)
    if match:
        return label[match.end() :]
    return label


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z]", "", name).lower()


@dataclass(frozen=True)
class _StandardMatchProfile:
    row: TargetRow
    normalized_label: str
    rt_center: float


@dataclass(frozen=True)
class _IstdMatchCandidate:
    standard: TargetRow
    score: float


@dataclass(frozen=True)
class _IstdMatchPlan:
    istd: TargetRow
    rt_center: float
    sort_score: float
    candidates: tuple[_IstdMatchCandidate, ...]


def _assign_istd_pairs(targets: list[TargetRow]) -> None:
    """Assign each matching ISTD to at most one non-ISTD target in place."""

    standards = [target for target in targets if target["is_istd"] == "false"]
    istds = [target for target in targets if target["is_istd"] == "true"]
    standard_profiles = tuple(
        _StandardMatchProfile(
            row=standard,
            normalized_label=_normalize_name(str(standard["label"])),
            rt_center=_rt_center(standard),
        )
        for standard in standards
    )

    plans = tuple(_istd_match_plan(istd, standard_profiles) for istd in istds)
    for plan in sorted(plans, key=lambda item: item.sort_score, reverse=True):
        best_standard: TargetRow | None = None
        best_score = 0.0
        for candidate in plan.candidates:
            standard = candidate.standard
            if standard["istd_pair"]:
                continue
            if candidate.score > best_score:
                best_score = candidate.score
                best_standard = standard

        if best_standard is not None:
            best_standard["istd_pair"] = str(plan.istd["label"])
        else:
            print(
                f"  Warning: no standard matched for ISTD '{plan.istd['label']}' "
                f"(RT {plan.rt_center:.2f} min) – fill istd_pair manually",
                file=sys.stderr,
            )


def _istd_match_plan(
    istd: TargetRow,
    standard_profiles: tuple[_StandardMatchProfile, ...],
) -> _IstdMatchPlan:
    normalized_core = _normalize_name(_strip_istd_prefix(str(istd["label"])))
    istd_rt = _rt_center(istd)
    sort_score = 0.0
    candidates: list[_IstdMatchCandidate] = []
    for standard in standard_profiles:
        name_similarity = SequenceMatcher(
            None, normalized_core, standard.normalized_label
        ).ratio()
        sort_score = max(sort_score, name_similarity)
        if name_similarity < 0.70:
            continue
        if abs(istd_rt - standard.rt_center) > _MAX_RT_DIFF_MIN:
            continue
        candidates.append(
            _IstdMatchCandidate(standard=standard.row, score=name_similarity)
        )
    return _IstdMatchPlan(
        istd=istd,
        rt_center=istd_rt,
        sort_score=sort_score,
        candidates=tuple(candidates),
    )


def _rt_center(target: TargetRow) -> float:
    return (float(target["rt_min"]) + float(target["rt_max"])) / 2


def parse_dna_sheet(
    worksheet: openpyxl.worksheet.worksheet.Worksheet,
    rt_window: float,
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
                    "rt_min": round(retention_time - rt_window, 2),
                    "rt_max": round(retention_time + rt_window, 2),
                    "ppm_tol": DEFAULT_PPM_TOL,
                    "neutral_loss_da": NL_DR,
                    "nl_ppm_warn": DEFAULT_NL_PPM_WARN,
                    "nl_ppm_max": DEFAULT_NL_PPM_MAX,
                    "is_istd": "true" if _is_istd(label) else "false",
                    "istd_pair": "",
                }
            )
    return targets


def parse_rna_sheet(
    worksheet: openpyxl.worksheet.worksheet.Worksheet,
    rt_window: float,
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

        neutral_loss: float | None = None
        if mhr_column is not None and _is_number(row[mhr_column]):
            neutral_loss = NL_R
        elif methyl_column is not None and _is_number(row[methyl_column]):
            neutral_loss = NL_MER
        if neutral_loss is None:
            continue

        label = str(label_raw).strip()
        retention_time = float(rt_raw)
        targets.append(
            {
                "label": label,
                "mz": round(float(mz_raw), 6),
                "rt_min": round(retention_time - rt_window, 2),
                "rt_max": round(retention_time + rt_window, 2),
                "ppm_tol": DEFAULT_PPM_TOL,
                "neutral_loss_da": neutral_loss,
                "nl_ppm_warn": DEFAULT_NL_PPM_WARN,
                "nl_ppm_max": DEFAULT_NL_PPM_MAX,
                "is_istd": "true" if _is_istd(label) else "false",
                "istd_pair": "",
            }
        )
    return targets


_FIELDNAMES = (
    "label",
    "mz",
    "rt_min",
    "rt_max",
    "ppm_tol",
    "neutral_loss_da",
    "nl_ppm_warn",
    "nl_ppm_max",
    "is_istd",
    "istd_pair",
)


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
