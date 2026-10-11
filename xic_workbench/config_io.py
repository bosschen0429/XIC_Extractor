"""Qt-free persistence for the local Targeted workbench."""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from xic_extractor.configuration.csv_io import (
    RETIRED_TARGET_FIELDS,
    TARGET_WRITE_FIELDS,
    _read_settings,
)
from xic_extractor.configuration.settings import resolve_settings
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DESCRIPTIONS

_PACKAGE_PARENT = Path(__file__).resolve().parent.parent


def _default_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    if (_PACKAGE_PARENT / "pyproject.toml").is_file() and (
        _PACKAGE_PARENT / "config"
    ).is_dir():
        return _PACKAGE_PARENT
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "XICExtractor"
    return Path.home() / ".local" / "share" / "XICExtractor"


ROOT = _default_root()
_BUNDLE = (
    Path(getattr(sys, "_MEIPASS")) if getattr(sys, "frozen", False) else _PACKAGE_PARENT
)

CONFIG_DIR = ROOT / "config"
_BUNDLE_CONFIG = _BUNDLE / "config"
_PREFIX_CONFIG = Path(sys.prefix) / "config"
_SETTINGS_FIELDS = ("key", "value", "description")
_TARGETS_FIELDS = tuple(TARGET_WRITE_FIELDS)


def _runtime_dir(config_dir: Path | None) -> Path:
    return Path(config_dir) if config_dir is not None else CONFIG_DIR


def _example_candidates(name: str, config_dir: Path | None) -> tuple[Path, ...]:
    candidates: list[Path] = []
    if config_dir is not None:
        candidates.append(Path(config_dir) / f"{name}.example.csv")
    candidates.extend(
        (
            _BUNDLE_CONFIG / f"{name}.example.csv",
            _PREFIX_CONFIG / f"{name}.example.csv",
        )
    )
    return tuple(dict.fromkeys(candidates))


def _read_path(name: str, config_dir: Path | None = None) -> Path:
    runtime_path = _runtime_dir(config_dir) / f"{name}.csv"
    if runtime_path.exists():
        return runtime_path
    for candidate in _example_candidates(name, config_dir):
        if candidate.exists():
            return candidate
    return runtime_path


def read_settings(*, config_dir: Path | None = None) -> dict[str, str]:
    """Read CSV inputs only; normalization and admission belong to their consumers."""
    return _read_settings(_read_path("settings", config_dir))


def write_settings(
    settings: dict[str, str], *, config_dir: Path | None = None
) -> dict[str, str]:
    path = _runtime_dir(config_dir) / "settings.csv"
    source = path if path.exists() else _read_path("settings", config_dir)
    persisted = _read_settings(source) if source.exists() else {}
    descriptions = {}
    if source.exists():
        with source.open(newline="", encoding="utf-8-sig") as handle:
            descriptions = {
                row["key"]: row.get("description", "") for row in csv.DictReader(handle)
            }
    values, _parsed, _warnings = resolve_settings(
        persisted, settings, settings_path=source
    )
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=_SETTINGS_FIELDS)
    writer.writeheader()
    for key, value in values.items():
        writer.writerow(
            dict(
                key=key,
                value=value,
                description=(
                    descriptions.get(key)
                    or CANONICAL_SETTINGS_DESCRIPTIONS.get(key, "")
                ),
            )
        )
    _atomic_write(path, "\ufeff" + buffer.getvalue())
    return values


def _atomic_write(path: Path, text: str) -> None:
    """Replace a local configuration file with a recoverable backup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.stem}-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _read_json_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_object(path: Path, payload: dict) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def migrate_legacy_dll_settings(*, config_dir: Path | None = None) -> tuple[str, ...]:
    """Move the former effective DLL to settings.csv once, then retire old keys.

    Legacy preferences formerly overrode Targeted settings. Discovery's DLL is
    only a fallback when the canonical active setting is absent or a placeholder.
    Launch overrides are intentionally not persisted by this boundary.
    """
    directory = _runtime_dir(config_dir)
    preference_path = directory / "workbench.json"
    discovery_path = directory / "discovery_gui.json"
    preferences = _read_json_object(preference_path)
    discovery = _read_json_object(discovery_path)
    if "dll_dir" not in preferences and "dll_dir" not in discovery:
        return ()
    canonical_path = directory / "settings.csv"
    canonical = _read_settings(canonical_path) if canonical_path.exists() else {}
    dll = str(canonical.get("dll_dir") or "").strip()
    preferred = str(preferences.get("dll_dir") or "").strip()
    discovered = str(discovery.get("dll_dir") or "").strip()
    missing = not dll or dll.startswith("<") or "/your/" in dll.replace("\\", "/")
    receipts: list[str] = []
    if preferred or (missing and discovered):
        origin = "workbench.json" if preferred else "discovery_gui.json"
        write_settings({"dll_dir": preferred or discovered}, config_dir=directory)
        receipts.append(f"dll_migration={origin} -> settings.csv")
    for path, payload in ((preference_path, preferences), (discovery_path, discovery)):
        if "dll_dir" in payload:
            del payload["dll_dir"]
            _write_json_object(path, payload)
            receipts.append(f"retired_dll_source={path.name} backup={path.name}.bak")
    return tuple(receipts)


def read_preferences(*, config_dir: Path | None = None) -> dict:
    return _read_json_object(_runtime_dir(config_dir) / "workbench.json")


def write_preferences(values: dict, *, config_dir: Path | None = None) -> dict:
    if set(values) - {"theme", "view"}:
        raise ValueError("不支援此偏好設定；DLL 目錄由 settings.csv 管理。")
    payload = read_preferences(config_dir=config_dir) | values
    _write_json_object(_runtime_dir(config_dir) / "workbench.json", payload)
    return payload


def read_targets(*, config_dir: Path | None = None) -> list[dict[str, str]]:
    with _read_path("targets", config_dir).open(
        newline="", encoding="utf-8-sig"
    ) as handle:
        return list(csv.DictReader(handle))


def write_targets(
    targets: list[dict[str, str]], *, config_dir: Path | None = None
) -> None:
    destination_dir = _runtime_dir(config_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = _target_write_fieldnames(targets)
    with (destination_dir / "targets.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for target in targets:
            writer.writerow({field: target.get(field, "") for field in fieldnames})


def _target_write_fieldnames(targets: list[dict[str, str]]) -> list[str]:
    fieldnames = list(_TARGETS_FIELDS)
    seen = set(fieldnames) | RETIRED_TARGET_FIELDS
    for target in targets:
        for field in target:
            if field not in seen:
                fieldnames.append(field)
                seen.add(field)
    return fieldnames


def targets_example_path() -> Path:
    """Return the read-only shared Targeted template."""

    for candidate in _example_candidates("targets", None):
        if candidate.exists():
            return candidate
    return _BUNDLE_CONFIG / "targets.example.csv"
