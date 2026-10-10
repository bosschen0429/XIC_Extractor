"""Qt-free persistence for the local Untargeted workbench."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xic_workbench.config_io import (
    ROOT,
    _read_json_object,
    _write_json_object,
    targets_example_path,
)

CONFIG_DIR = ROOT / "config"
CONFIG_NAME = "discovery_gui.json"
DEFAULT_CONFIG: dict[str, Any] = {
    "mode": "full",
    "preset": "dna_dr",
    "raw_dir": "",
    "raw_file": "",
    "dll_dir": "",
    "output_dir": "",
    "discovery_batch_index": "",
    "targets_csv": "",
    "sequence_source": "",
    "custom_roles": {},
    "overrides": {},
}


def _runtime_dir(config_dir: Path | None) -> Path:
    return Path(config_dir) if config_dir is not None else CONFIG_DIR


def _fresh_defaults() -> dict[str, Any]:
    defaults = copy.deepcopy(DEFAULT_CONFIG)
    defaults["targets_csv"] = str(targets_example_path())
    return defaults


def _merge_known_keys(config: Mapping[str, Any]) -> dict[str, Any]:
    merged = _fresh_defaults()
    for key in DEFAULT_CONFIG:
        if key in config:
            merged[key] = copy.deepcopy(config[key])
    return merged


def _with_runtime_defaults(config: dict[str, Any]) -> dict[str, Any]:
    if not str(config.get("output_dir") or "").strip():
        config["output_dir"] = str(ROOT / "output")
    return config


def read_discovery_config(*, config_dir: Path | None = None) -> dict[str, Any]:
    directory = _runtime_dir(config_dir)
    payload = _read_json_object(directory / CONFIG_NAME)
    config = _with_runtime_defaults(_merge_known_keys(payload))
    return config


def write_discovery_config(
    config: Mapping[str, Any], *, config_dir: Path | None = None
) -> None:
    destination = _runtime_dir(config_dir)
    path = destination / CONFIG_NAME
    existing = _read_json_object(path)
    payload = {**existing, **_merge_known_keys({**existing, **config})}
    payload.pop("dll_dir", None)
    _write_json_object(path, payload)
