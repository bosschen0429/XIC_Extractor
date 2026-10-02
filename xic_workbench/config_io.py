"""Qt-free persistence for the local Targeted workbench."""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

from xic_extractor.configuration.csv_io import TARGET_WRITE_FIELDS
from xic_extractor.configuration.settings import migrate_settings_dict
from xic_extractor.settings_schema import (
    CANONICAL_SETTINGS_DEFAULTS,
    CANONICAL_SETTINGS_DESCRIPTIONS,
)

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
    Path(getattr(sys, "_MEIPASS"))
    if getattr(sys, "frozen", False)
    else _PACKAGE_PARENT
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
    with _read_path("settings", config_dir).open(
        newline="", encoding="utf-8-sig"
    ) as handle:
        raw = {
            "parallel_workers": CANONICAL_SETTINGS_DEFAULTS["parallel_workers"],
            **{row["key"]: row["value"] for row in csv.DictReader(handle)},
        }
    migrated, _warnings = migrate_settings_dict(raw)
    return migrated


def write_settings(settings: dict[str, str], *, config_dir: Path | None = None) -> None:
    destination_dir = _runtime_dir(config_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    path = destination_dir / "settings.csv"
    source_path = path if path.exists() else _read_path("settings", config_dir)
    existing: dict[str, dict[str, str]] = {}
    if source_path.exists():
        with source_path.open(newline="", encoding="utf-8-sig") as handle:
            existing = {row["key"]: row for row in csv.DictReader(handle)}

    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=_SETTINGS_FIELDS)
        writer.writeheader()
        for key, value in settings.items():
            writer.writerow(
                {
                    "key": key,
                    "value": value,
                    "description": existing.get(key, {}).get("description", "")
                    or CANONICAL_SETTINGS_DESCRIPTIONS.get(key, ""),
                }
            )


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
    seen = set(fieldnames)
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
