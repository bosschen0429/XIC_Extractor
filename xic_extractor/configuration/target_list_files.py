"""Targeted target-list XLSX: a Targets sheet, plus Delta Mass details when used.

Targets that declare Delta Mass ions may add a ``Delta Mass`` sheet (one row per
ion) and a ``Delta Mass Settings`` sheet (settings shared by the whole list).
Batch and machine settings never live in the list.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from zipfile import BadZipFile

from xic_extractor.configuration.csv_io import (
    REQUIRED_TARGET_FIELDS,
    TARGET_FIELDS,
)
from xic_extractor.configuration.delta_mass_table import (
    DELTA_MASS_HEADERS,
    DELTA_MASS_SETTING_KEYS,
    DELTA_MASS_SETTINGS_HEADERS,
    DELTA_MASS_SETTINGS_SHEET,
    DELTA_MASS_SHEET,
    delta_mass_rows,
    delta_mass_setting_rows,
    parse_delta_mass_rows,
)
from xic_extractor.configuration.legacy_target_methods import (
    LEGACY_METHOD_SHEET,
    upgrade_target_table,
)
from xic_extractor.configuration.models import ConfigError, Target
from xic_extractor.configuration.targets import (
    parse_target_rows,
    targets_delta_mass_payload,
)

TARGETS_SHEET = "Targets"
_REQUIRED_COLUMNS = {
    TARGETS_SHEET: set(REQUIRED_TARGET_FIELDS),
    DELTA_MASS_SHEET: {"Target", "Delta m/z"},
    DELTA_MASS_SETTINGS_SHEET: {"key", "value"},
}
# Sheets of the earlier method workbook, which carried a fixed strategy row.
_RETIRED_SHEETS = ("Method", LEGACY_METHOD_SHEET)


@dataclass(frozen=True)
class TargetList:
    """Admitted targets with their table rows and Delta Mass details."""

    targets: tuple[Target, ...]
    target_rows: tuple[Mapping[str, str], ...]
    # Delta Mass details payload; None when no target uses Delta Mass.
    delta_mass: Mapping | None = field(default=None)


def create_target_list(
    target_rows: Sequence[Mapping[str, str]], *, delta_mass: Mapping | None = None
) -> TargetList:
    """Admit an edited list without reading files.

    Rows in the earlier ``method``-column format are converted to parameter
    columns.
    """
    upgraded, legacy_details = upgrade_target_table(target_rows)
    targets = parse_target_rows(
        upgraded,
        delta_mass=delta_mass if delta_mass is not None else legacy_details,
    )
    return TargetList(
        targets,
        tuple(MappingProxyType(row) for row in upgraded),
        targets_delta_mass_payload(targets),
    )


def load_target_list(path: Path) -> TargetList:
    tables = _read_workbook(path)
    settings = {}
    for row in tables.get(DELTA_MASS_SETTINGS_SHEET, []):
        key = row.get("key", "")
        if key not in DELTA_MASS_SETTING_KEYS:
            raise ConfigError(
                f"{path}/{DELTA_MASS_SETTINGS_SHEET}: 不支援的設定 {key!r}；"
                f"可用設定：{', '.join(DELTA_MASS_SETTING_KEYS)}"
            )
        if key in settings:
            raise ConfigError(f"{path}/{DELTA_MASS_SETTINGS_SHEET}: 設定 {key!r} 重複")
        settings[key] = row.get("value", "")
    details = (
        parse_delta_mass_rows(tables.get(DELTA_MASS_SHEET, []), settings)
        if DELTA_MASS_SHEET in tables or settings
        else None
    )
    try:
        return create_target_list(tables[TARGETS_SHEET], delta_mass=details)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def save_target_list(target_list: TargetList, path: Path) -> None:
    rows = target_list.target_rows
    tables: dict[str, tuple[Sequence[str], Sequence[Mapping[str, object]]]] = {
        TARGETS_SHEET: (_target_fields(rows), rows)
    }
    if target_list.delta_mass:
        tables[DELTA_MASS_SHEET] = (
            DELTA_MASS_HEADERS,
            delta_mass_rows(target_list.delta_mass),
        )
        settings = delta_mass_setting_rows(target_list.delta_mass)
        if settings:
            tables[DELTA_MASS_SETTINGS_SHEET] = (DELTA_MASS_SETTINGS_HEADERS, settings)
    _save_workbook(path, tables)


def _read_workbook(path: Path) -> dict[str, list[dict[str, str]]]:
    import openpyxl

    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        raise ConfigError(f"{path}: 請選擇 .xlsx 文件。")
    try:
        book = openpyxl.load_workbook(path, read_only=True, data_only=False)
    except (OSError, ValueError, KeyError, BadZipFile) as exc:
        raise ConfigError(f"{path}: 無法讀取 XLSX：{exc}") from exc
    try:
        actual = set(book.sheetnames)
        retired = [name for name in _RETIRED_SHEETS if name in actual]
        if retired:
            raise ConfigError(
                f"{path}: 這是舊格式的目標清單（含 {'、'.join(retired)} 工作表）。"
                f"請刪除這些工作表後再使用；Delta Mass 共用設定改放在 "
                f"{DELTA_MASS_SETTINGS_SHEET} 工作表（key、value 兩欄）。"
            )
        unknown = actual - set(_REQUIRED_COLUMNS)
        if TARGETS_SHEET not in actual or unknown:
            raise ConfigError(
                f"{path}: 需要 {TARGETS_SHEET} 工作表；另外只接受 "
                f"{DELTA_MASS_SHEET} 與 {DELTA_MASS_SETTINGS_SHEET}。"
            )
        tables = {}
        for name in sorted(actual):
            data = []
            for cells in book[name].iter_rows():
                if any(cell.data_type in {"f", "e"} for cell in cells):
                    raise ConfigError(
                        f"{path}/{name}: 不接受公式或 Excel 錯誤；請貼上實際值。"
                    )
                data.append(
                    [
                        ""
                        if cell.value is None
                        else str(cell.value).lower()
                        if isinstance(cell.value, bool)
                        else str(cell.value)
                        for cell in cells
                    ]
                )
            if (
                not data
                or len(set(data[0])) != len(data[0])
                or any(not h for h in data[0])
            ):
                raise ConfigError(f"{path}/{name}: 欄位名稱不可空白或重複。")
            required = _REQUIRED_COLUMNS[name]
            if not required <= set(data[0]):
                raise ConfigError(
                    f"{path}/{name}: 缺少欄位 {sorted(required - set(data[0]))}"
                )
            tables[name] = [
                dict(zip(data[0], row, strict=True)) for row in data[1:] if any(row)
            ]
        return tables
    finally:
        book.close()


def _target_fields(rows: Sequence[Mapping[str, str]]) -> tuple[str, ...]:
    return tuple(dict.fromkeys([*TARGET_FIELDS, *(key for row in rows for key in row)]))


def _save_workbook(path: Path, tables: Mapping) -> None:
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    path = Path(path).absolute()
    if path.suffix.lower() != ".xlsx" or path.is_symlink() or path.is_dir():
        raise ConfigError(f"{path}: 僅可寫入 .xlsx 文件。")
    path.parent.mkdir(parents=True, exist_ok=True)
    book = openpyxl.Workbook()
    book.remove(book.active)
    staged = None
    try:
        for name, (fields, rows) in tables.items():
            sheet = book.create_sheet(name)
            sheet.append(list(fields))
            for row in rows:
                sheet.append(
                    [
                        "" if row.get(key) is None else str(row.get(key))
                        for key in fields
                    ]
                )
            # Text storage preserves exact decimal strings and labels (including '=').
            for cells in sheet:
                for cell in cells:
                    cell.data_type = "s"
                    cell.number_format = "@"
            for cell in sheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="24445C")
                sheet.column_dimensions[cell.column_letter].width = (
                    28 if cell.value != "description" else 80
                )
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
        with tempfile.NamedTemporaryFile(
            dir=path.parent, suffix=".xlsx", delete=False
        ) as handle:
            staged = Path(handle.name)
        book.save(staged)
        load_target_list(staged)
        os.replace(staged, path)
    finally:
        book.close()
        if staged is not None:
            staged.unlink(missing_ok=True)
