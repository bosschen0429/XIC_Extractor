"""Portable Targeted method intent using the existing settings and targets CSVs."""

from __future__ import annotations

import csv
import os
import shutil
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from xic_extractor.configuration.csv_io import (
    TARGET_WRITE_FIELDS,
    _read_settings,
    _read_target_rows,
)
from xic_extractor.configuration.models import ConfigError, Target
from xic_extractor.configuration.settings import (
    fill_setting_defaults,
    resolve_settings,
)
from xic_extractor.configuration.targets import parse_target_rows
from xic_extractor.settings_schema import (
    CANONICAL_SETTINGS_DEFAULTS,
    METHOD_KEYS,
    RESOURCE_KEYS,
)

_FILES = frozenset({"settings.csv", "targets.csv"})


@dataclass(frozen=True)
class TargetedMethod:
    """Complete method values and target definitions, without batch or machine state."""

    settings: Mapping[str, str]
    targets: tuple[Target, ...]
    target_rows: tuple[Mapping[str, str], ...]


def create_targeted_method(
    settings: Mapping[str, str], target_rows: Sequence[Mapping[str, str]]
) -> TargetedMethod:
    """Admit an editable method without files or discarded resource knowledge."""
    normalized, _warnings = fill_setting_defaults(dict(settings))
    _reject_portable_resources(normalized)
    unknown = sorted(set(normalized) - CANONICAL_SETTINGS_DEFAULTS.keys())
    if unknown:
        raise ConfigError(f"方法包含尚未支援的設定：{', '.join(unknown)}；原設定保留。")
    # Batch and machine settings are not method values and must not affect admission.
    values, _parsed, _warnings = resolve_settings(
        {key: normalized[key] for key in METHOD_KEYS}
    )
    rows = tuple(MappingProxyType(dict(row)) for row in target_rows)
    targets = parse_target_rows(rows)
    return TargetedMethod(
        MappingProxyType({key: values[key] for key in METHOD_KEYS}), targets, rows
    )


def apply_targeted_method(
    settings: Mapping[str, str],
    method: TargetedMethod,
    *,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Replace all method intent while retaining this batch and local configuration.

    Explicit overrides are applied after method selection. Resource-bound cases
    are not portable yet: neither inherited nor explicitly rebound resources may
    silently turn a selected method into a differently supported calculation.
    """
    _reject_portable_resources(settings)
    values, _warnings = fill_setting_defaults(dict(settings))
    values.update(method.settings)
    values.update(overrides or {})
    _reject_portable_resources(values)
    return values


def _reject_portable_resources(settings: Mapping[str, str]) -> None:
    resources = [key for key in RESOURCE_KEYS if settings.get(key, "").strip()]
    if resources:
        raise ConfigError(
            "此設定使用科學參考資料，尚不支援可跨批次方法的保存或套用："
            + ", ".join(resources)
            + "。原設定保留；請使用現有完整設定執行，不可省略參考資料。"
        )


def load_targeted_method(directory: Path) -> TargetedMethod:
    """Load a complete method, never patch a currently selected method."""
    directory = Path(directory)
    settings = _read_settings(directory / "settings.csv")
    missing = sorted(set(METHOD_KEYS) - settings.keys())
    extra = sorted(settings.keys() - set(METHOD_KEYS))
    if missing or extra:
        raise ConfigError(
            f"{directory}: 方法 settings.csv 必須完整且僅包含方法設定；"
            f"缺少：{', '.join(missing) or '無'}；額外：{', '.join(extra) or '無'}。"
            "原方法保留。"
        )
    rows = [row for _number, row in _read_target_rows(directory / "targets.csv")]
    return create_targeted_method(settings, rows)


def save_targeted_method(method: TargetedMethod, directory: Path) -> TargetedMethod:
    """Stage both CSVs and replace the method directory, restoring it on failure.

    Existing directories must contain only method files. This protects workspaces
    and unrelated files. Directory renames provide whole-file generations, not a
    concurrent-reader transaction; callers serialize method save/load operations.
    """
    destination = Path(directory).absolute()
    if destination.is_symlink():
        raise ConfigError(f"{destination}: 不可覆寫連結方法目錄。")
    if destination.exists():
        if (
            not destination.is_dir()
            or set(p.name for p in destination.iterdir()) != _FILES
        ):
            raise ConfigError(
                f"{destination}: 僅可覆寫含 settings.csv 與 targets.csv 的方法目錄。"
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    backup = destination.with_name(f".{destination.name}.backup-{uuid.uuid4().hex}")
    moved_old = False
    try:
        _write_csv(
            staged / "settings.csv",
            ("key", "value"),
            [{"key": key, "value": method.settings[key]} for key in METHOD_KEYS],
        )
        _write_method_targets(method, staged / "targets.csv")
        admitted = load_targeted_method(staged)
        if destination.exists():
            os.replace(destination, backup)
            moved_old = True
        try:
            os.replace(staged, destination)
        except BaseException:
            if moved_old:
                os.replace(backup, destination)
            raise
        if moved_old:
            shutil.rmtree(backup)
        return admitted
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def _write_csv(
    path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, str]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())


def _write_method_targets(method: TargetedMethod, path: Path) -> None:
    extra_fields = tuple(
        dict.fromkeys(
            key
            for row in method.target_rows
            for key in row
            if key not in TARGET_WRITE_FIELDS
        )
    )
    _write_csv(path, (*TARGET_WRITE_FIELDS, *extra_fields), method.target_rows)
