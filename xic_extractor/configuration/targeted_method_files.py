"""Portable Targeted XLSX documents, composed from settings and target owners."""

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
    DELTA_MASS_SHEET,
    delta_mass_rows,
    delta_mass_setting_rows,
    parse_delta_mass_rows,
)
from xic_extractor.configuration.legacy_target_methods import (
    LEGACY_METHOD_SHEET,
    legacy_method_sheet_payload,
    upgrade_target_table,
)
from xic_extractor.configuration.models import ConfigError, Target
from xic_extractor.configuration.settings import (
    resolve_settings,
)
from xic_extractor.configuration.targets import (
    parse_target_rows,
    targets_delta_mass_payload,
)
from xic_extractor.settings_schema import (
    CANONICAL_SETTINGS_DEFAULTS,
    METHOD_KEYS,
    TARGETED_STRATEGY,
)


@dataclass(frozen=True)
class TargetedMethod:
    """Complete method values and target definitions, without batch or machine state."""

    settings: Mapping[str, str]
    targets: tuple[Target, ...]
    target_rows: tuple[Mapping[str, str], ...]
    # Delta Mass details payload; None when no target uses Delta Mass.
    delta_mass: Mapping | None = field(default=None)


def create_targeted_method(
    settings: Mapping[str, str],
    target_rows: Sequence[Mapping[str, str]],
    *,
    delta_mass: Mapping | None = None,
    legacy_methods: Mapping | None = None,
) -> TargetedMethod:
    """Admit an editable method without reading files.

    Rows in the earlier ``method``-column format are converted using
    ``legacy_methods`` (the earlier identification method definitions).
    """
    unknown = sorted(
        set(settings) - set(CANONICAL_SETTINGS_DEFAULTS) - set(METHOD_KEYS)
    )
    if unknown:
        raise ConfigError("Retired or unknown method settings: " + ", ".join(unknown))
    strategy = settings.get("strategy", TARGETED_STRATEGY)
    if strategy != TARGETED_STRATEGY:
        raise ConfigError(f"Unsupported Targeted strategy: {strategy}")
    upgraded, legacy_details = upgrade_target_table(target_rows, legacy_methods)
    targets = parse_target_rows(
        upgraded,
        delta_mass=delta_mass if delta_mass is not None else legacy_details,
    )
    return TargetedMethod(
        MappingProxyType({"strategy": strategy}),
        targets,
        tuple(MappingProxyType(row) for row in upgraded),
        targets_delta_mass_payload(targets),
    )


def apply_targeted_method(
    settings: Mapping[str, str],
    method: TargetedMethod,
    *,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Bind the admitted strategy while retaining batch and machine inputs."""
    if method.settings.get("strategy") != TARGETED_STRATEGY:
        raise ConfigError("Unsupported Targeted strategy")
    return resolve_settings(dict(settings), dict(overrides or {}))[0]


def load_targeted_method(path: Path) -> TargetedMethod:
    """Read the Method + Targets (+ Delta Mass) workbook contract."""
    tables = _read_workbook(
        path,
        {"Method", "Targets"},
        optional_sheets={DELTA_MASS_SHEET, LEGACY_METHOD_SHEET},
    )
    settings = {}
    delta_settings = {}
    for row in tables["Method"]:
        key = row.get("key", "")
        if key in settings or key in delta_settings:
            raise ConfigError(f"{path}: duplicate method setting {key!r}")
        if key in DELTA_MASS_SETTING_KEYS:
            delta_settings[key] = row.get("value", "")
        else:
            settings[key] = row.get("value", "")
    missing = set(METHOD_KEYS) - settings.keys()
    extra = settings.keys() - set(METHOD_KEYS)
    if missing or extra:
        raise ConfigError(
            f"{path}: 方法設定缺少 {sorted(missing)}；額外 {sorted(extra)}"
        )
    legacy = (
        legacy_method_sheet_payload(tables[LEGACY_METHOD_SHEET])
        if LEGACY_METHOD_SHEET in tables
        else None
    )
    details = (
        parse_delta_mass_rows(tables.get(DELTA_MASS_SHEET, []), delta_settings)
        if DELTA_MASS_SHEET in tables or delta_settings
        else None
    )
    try:
        return create_targeted_method(
            settings, tables["Targets"], delta_mass=details, legacy_methods=legacy
        )
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def load_target_table(path: Path) -> list[dict[str, str]]:
    rows = _read_workbook(path, {"Targets"})["Targets"]
    return [dict(row) for row in create_targeted_method({}, rows).target_rows]


def load_workbook_targets(path: Path) -> tuple[Target, ...]:
    """Read shared target intent; a method workbook brings its own definitions."""
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True)
    try:
        has_definitions = bool(
            {DELTA_MASS_SHEET, LEGACY_METHOD_SHEET} & set(book.sheetnames)
        )
    finally:
        book.close()
    if has_definitions:
        return load_targeted_method(path).targets
    rows = _read_workbook(path, {"Targets"}, optional_sheets={"Method"})["Targets"]
    return create_targeted_method({}, rows).targets


def _read_workbook(
    path: Path, sheets: set[str], *, optional_sheets: set[str] | None = None
) -> dict[str, list[dict[str, str]]]:
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
        if not sheets <= actual or actual - sheets - (optional_sheets or set()):
            raise ConfigError(f"{path}: 工作表必須為 {sorted(sheets)}")
        tables = {}
        definitions = (
            actual
            & (optional_sheets or set())
            & {DELTA_MASS_SHEET, LEGACY_METHOD_SHEET}
        )
        for name in sorted(sheets | definitions):
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
            required = {
                "Method": {"key", "value"},
                "Targets": set(REQUIRED_TARGET_FIELDS),
                DELTA_MASS_SHEET: {"Target", "Delta m/z"},
                LEGACY_METHOD_SHEET: {"Method", "Condition", "Kind"},
            }[name]
            if not required <= set(data[0]):
                raise ConfigError(
                    f"{path}/{name}: 缺少欄位 {sorted(required - set(data[0]))}"
                )
            if name == "Method" and set(data[0]) - {"key", "value", "description"}:
                raise ConfigError(f"{path}/Method: 不支援額外方法欄位。")
            tables[name] = [
                dict(zip(data[0], row, strict=True)) for row in data[1:] if any(row)
            ]
        return tables
    finally:
        book.close()


def save_targeted_method(method: TargetedMethod, path: Path) -> TargetedMethod:
    tables: dict[str, tuple[Sequence[str], Sequence[Mapping[str, object]]]] = {
        "Method": (
            ("key", "value", "description"),
            [
                *(
                    {
                        "key": key,
                        "value": method.settings[key],
                        "description": "Shared Targeted strategy",
                    }
                    for key in METHOD_KEYS
                ),
                *(
                    delta_mass_setting_rows(method.delta_mass)
                    if method.delta_mass
                    else ()
                ),
            ],
        ),
        "Targets": (_target_fields(method.target_rows), method.target_rows),
    }
    if method.delta_mass:
        tables[DELTA_MASS_SHEET] = (
            DELTA_MASS_HEADERS,
            delta_mass_rows(method.delta_mass),
        )
    _save_workbook(path, tables, load_targeted_method)
    return method


def save_target_table(rows: Sequence[Mapping[str, str]], path: Path) -> None:
    rows = create_targeted_method({}, rows).target_rows
    _save_workbook(path, {"Targets": (_target_fields(rows), rows)}, load_target_table)


def _target_fields(rows: Sequence[Mapping[str, str]]) -> tuple[str, ...]:
    return tuple(dict.fromkeys([*TARGET_FIELDS, *(key for row in rows for key in row)]))


def _save_workbook(path: Path, tables: Mapping, reader) -> None:
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
        reader(staged)
        os.replace(staged, path)
    finally:
        book.close()
        if staged is not None:
            staged.unlink(missing_ok=True)
