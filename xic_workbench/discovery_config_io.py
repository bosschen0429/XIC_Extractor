"""Qt-free persistence for the local Untargeted workbench."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xic_workbench.config_io import ROOT, targets_example_path

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
    path = _runtime_dir(config_dir) / CONFIG_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _with_runtime_defaults(_fresh_defaults())
    if not isinstance(payload, dict):
        return _with_runtime_defaults(_fresh_defaults())
    return _with_runtime_defaults(_merge_known_keys(payload))


def write_discovery_config(
    config: Mapping[str, Any], *, config_dir: Path | None = None
) -> None:
    destination = _runtime_dir(config_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / CONFIG_NAME).write_text(
        json.dumps(_merge_known_keys(config), indent=2, sort_keys=True),
        encoding="utf-8",
    )
