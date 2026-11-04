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
    TARGET_FIELDS,
    TARGET_WRITE_FIELDS,
)
from xic_extractor.configuration.json_cells import decode_json_cells, encode_json_cells
from xic_extractor.configuration.models import ConfigError, Target
from xic_extractor.configuration.settings import (
    resolve_settings,
)
from xic_extractor.configuration.targeted_identification import (
    BUILTIN_TARGET_METHODS,
    IdentificationMethod,
    identification_methods_payload,
    parse_identification_methods,
)
from xic_extractor.configuration.targets import parse_target_rows
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
    identification_methods: Mapping[str, IdentificationMethod] = field(
        default_factory=dict
    )
    default_method: str = ""


def create_targeted_method(
    settings: Mapping[str, str],
    target_rows: Sequence[Mapping[str, str]],
    *,
    identification_methods: Mapping | None = None,
    default_method: str = "",
) -> TargetedMethod:
    """Admit an editable method without reading files."""
    unknown = sorted(
        set(settings) - set(CANONICAL_SETTINGS_DEFAULTS) - set(METHOD_KEYS)
    )
    if unknown:
        raise ConfigError("Retired or unknown method settings: " + ", ".join(unknown))
    strategy = settings.get("strategy", TARGETED_STRATEGY)
    if strategy != TARGETED_STRATEGY:
        raise ConfigError(f"Unsupported Targeted strategy: {strategy}")
    rows = tuple(MappingProxyType(dict(row)) for row in target_rows)
    try:
        methods = parse_identification_methods(
            {} if identification_methods is None else identification_methods
        )
        if not isinstance(default_method, str) or (
            default_method
            and default_method not in BUILTIN_TARGET_METHODS
            and default_method not in methods
        ):
            raise ValueError(
                "default_method must reference a defined or built-in method"
            )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    targets = parse_target_rows(
        rows, identification_methods=methods, default_method=default_method
    )
    return TargetedMethod(
        MappingProxyType({"strategy": strategy}),
        targets,
        rows,
        MappingProxyType(methods),
        default_method,
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
    """Read the explicit Method + Targets workbook contract; no format detection."""
    tables = _read_workbook(path, {"Method", "Targets"})
    settings = {}
    for row in tables["Method"]:
        key = row.get("key", "")
        if key in settings:
            raise ConfigError(f"{path}: duplicate method setting {key!r}")
        settings[key] = row.get("value", "")
    missing = set(METHOD_KEYS) - settings.keys()
    definition_keys = [
        key
        for key in settings
        if key == "identification_methods" or key.startswith("identification_methods.")
    ]
    extra = (
        settings.keys() - set(METHOD_KEYS) - {"default_method"} - set(definition_keys)
    )
    if missing or extra:
        raise ConfigError(
            f"{path}: 方法設定缺少 {sorted(missing)}；額外 {sorted(extra)}"
        )
    try:
        methods = (
            decode_json_cells(
                (
                    1
                    if key == "identification_methods"
                    else key.removeprefix("identification_methods."),
                    settings.pop(key),
                )
                for key in definition_keys
            )
            if definition_keys
            else {}
        )
    except ValueError as exc:
        raise ConfigError(
            f"{path}: invalid identification_methods JSON: {exc}"
        ) from exc
    default_method = settings.pop("default_method", "")
    return create_targeted_method(
        settings,
        tables["Targets"],
        identification_methods=methods,
        default_method=default_method,
    )


def load_target_table(path: Path) -> list[dict[str, str]]:
    rows = _read_workbook(path, {"Targets"})["Targets"]
    parse_target_rows(rows, Path(path))
    return rows


def load_workbook_targets(path: Path) -> tuple[Target, ...]:
    """Read shared target intent, ignoring optional Targeted method settings."""
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        has_method_intent = "Method" in book.sheetnames and any(
            row
            and isinstance(row[0], str)
            and (
                row[0] in {"identification_methods", "default_method"}
                or row[0].startswith("identification_methods.")
            )
            for row in book["Method"].iter_rows(values_only=True)
        )
    finally:
        book.close()
    if has_method_intent:
        return load_targeted_method(path).targets
    rows = _read_workbook(path, {"Targets"}, optional_sheets={"Method"})["Targets"]
    return parse_target_rows(rows, Path(path))


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
        for name in sheets:
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
            required = {"key", "value"} if name == "Method" else set(TARGET_FIELDS)
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
    _save_workbook(
        path,
        {
            "Method": (
                ("key", "value", "description"),
                [
                    {
                        "key": key,
                        "value": method.settings[key],
                        "description": "Shared Targeted strategy",
                    }
                    for key in METHOD_KEYS
                ]
                + (
                    [
                        {
                            "key": "identification_methods"
                            if part == 1
                            else f"identification_methods.{part}",
                            "value": fragment,
                            "description": (
                                "Reusable identification conditions; "
                                "edit in the workbench"
                            ),
                        }
                        for part, fragment in enumerate(
                            encode_json_cells(
                                identification_methods_payload(
                                    method.identification_methods
                                )
                            ),
                            start=1,
                        )
                    ]
                    if method.identification_methods
                    else []
                )
                + (
                    [
                        {
                            "key": "default_method",
                            "value": method.default_method,
                            "description": "Default target identification method",
                        }
                    ]
                    if method.default_method
                    else []
                ),
            ),
            "Targets": (_target_fields(method.target_rows), method.target_rows),
        },
        load_targeted_method,
    )
    return method


def save_target_table(rows: Sequence[Mapping[str, str]], path: Path) -> None:
    parse_target_rows(rows, Path(path))
    _save_workbook(path, {"Targets": (_target_fields(rows), rows)}, load_target_table)


def _target_fields(rows: Sequence[Mapping[str, str]]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys([*TARGET_WRITE_FIELDS, *(key for row in rows for key in row)])
    )


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
                sheet.append([str(row.get(key, "")) for key in fields])
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
