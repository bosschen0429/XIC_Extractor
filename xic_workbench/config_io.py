"""Qt-free persistence for the local Targeted workbench."""

from __future__ import annotations

import functools
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xic_extractor.configuration.atomic_write import atomic_write_text
from xic_extractor.configuration.paths import runtime_root
from xic_extractor.configuration.settings import resolve_settings
from xic_extractor.configuration.workspace_state import (
    read_workspace,
    save_workspace,
    workspace_settings,
)

if TYPE_CHECKING:
    from xic_extractor.configuration.loader import PreparedTargetedConfig

ROOT = runtime_root()
CONFIG_DIR = ROOT / "config"

_state_lock = threading.RLock()


def _serialized[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(function)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        with _state_lock:
            return function(*args, **kwargs)

    return run


def _runtime_dir(config_dir: Path | None) -> Path:
    return Path(config_dir) if config_dir is not None else CONFIG_DIR


@_serialized
def read_settings(*, config_dir: Path | None = None) -> dict[str, str]:
    return workspace_settings(read_workspace(_runtime_dir(config_dir)))


@_serialized
def write_settings(
    settings: dict[str, str], *, config_dir: Path | None = None
) -> dict[str, str]:
    values, _parsed, _warnings = resolve_settings(
        read_settings(config_dir=config_dir), settings
    )
    save_workspace(_runtime_dir(config_dir), settings=values)
    return values


@_serialized
def write_work(
    settings: dict[str, str],
    targets: list[dict[str, str]],
    *,
    config_dir: Path | None = None,
    delta_mass: dict | None = None,
) -> dict[str, str]:
    values, _parsed, _warnings = resolve_settings(
        read_settings(config_dir=config_dir), settings
    )
    from xic_extractor.configuration.target_list_files import create_target_list

    target_list = create_target_list(targets, delta_mass=delta_mass)
    save_workspace(
        _runtime_dir(config_dir),
        settings=values,
        targets=[dict(row) for row in target_list.target_rows],
        delta_mass=dict(target_list.delta_mass) if target_list.delta_mass else None,
    )
    return values


@_serialized
def write_application_settings(
    settings: dict[str, str], *, config_dir: Path | None = None
) -> dict[str, str]:
    """Invalid scientific drafts must not block saving machine settings."""
    values, _parsed, _warnings = resolve_settings(settings)
    selected = {key: values[key] for key in settings}
    save_workspace(_runtime_dir(config_dir), settings=selected)
    return selected


@_serialized
def write_dll_dir(dll_dir: str, *, config_dir: Path | None = None) -> None:
    write_application_settings({"dll_dir": dll_dir}, config_dir=config_dir)


def _read_json_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_object(path: Path, payload: dict) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


@_serialized
def read_preferences(*, config_dir: Path | None = None) -> dict:
    return _read_json_object(_runtime_dir(config_dir) / "workbench.json")


@_serialized
def write_preferences(values: dict, *, config_dir: Path | None = None) -> dict:
    payload = read_preferences(config_dir=config_dir) | values
    _write_json_object(_runtime_dir(config_dir) / "workbench.json", payload)
    return payload


@_serialized
def read_targets(*, config_dir: Path | None = None) -> list[dict[str, str]]:
    return read_workspace(_runtime_dir(config_dir))["targets"]


@_serialized
def write_targets(
    targets: list[dict[str, str]], *, config_dir: Path | None = None
) -> None:
    from xic_extractor.configuration.targets import parse_target_rows

    parse_target_rows(targets)
    save_workspace(_runtime_dir(config_dir), targets=targets)


@_serialized
def read_delta_mass(*, config_dir: Path | None = None) -> dict | None:
    return read_workspace(_runtime_dir(config_dir)).get("delta_mass")


@_serialized
def prepare_targeted_config(config_dir: Path, **options: Any) -> PreparedTargetedConfig:
    """A Targeted run freezes its inputs from the same state files."""
    from xic_extractor.configuration import loader

    return loader.prepare_targeted_config(config_dir, **options)
