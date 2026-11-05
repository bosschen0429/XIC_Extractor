"""Persist the local Workbench Untargeted workflow controls."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xic_extractor.configuration.paths import targets_template_path
from xic_workbench.config_io import (
    ROOT,
    _read_json_object,
    _write_json_object,
)

CONFIG_DIR = ROOT / "config"
CONFIG_NAME = "discovery_gui.json"
DEFAULT_CONFIG: dict[str, Any] = {
    "mode": "full",
    "alignment_enabled": True,
    "backfill_enabled": True,
    "preset": "dna_dr",
    "raw_dir": "",
    "raw_file": "",
    "output_dir": "",
    "discovery_batch_index": "",
    "targets_csv": "",
    "sequence_source": "",
    "overrides": {},
}


def _runtime_dir(config_dir: Path | None) -> Path:
    return Path(config_dir) if config_dir is not None else CONFIG_DIR


def _fresh_defaults() -> dict[str, Any]:
    defaults = copy.deepcopy(DEFAULT_CONFIG)
    defaults["targets_csv"] = str(targets_template_path())
    return defaults


def _merge_known_keys(config: Mapping[str, Any]) -> dict[str, Any]:
    merged = _fresh_defaults()
    for key in DEFAULT_CONFIG:
        if key in config:
            merged[key] = copy.deepcopy(config[key])
    return merged


def _with_runtime_defaults(config: dict[str, Any]) -> dict[str, Any]:
    preset = config.get("preset")
    if preset is None or (isinstance(preset, str) and not preset.strip()):
        config["preset"] = DEFAULT_CONFIG["preset"]
    if not str(config.get("output_dir") or "").strip():
        config["output_dir"] = str(ROOT / "output")
    return config


def read_untargeted_config(*, config_dir: Path | None = None) -> dict[str, Any]:
    directory = _runtime_dir(config_dir)
    payload = _read_json_object(directory / CONFIG_NAME)
    config = _with_runtime_defaults(_merge_known_keys(payload))
    return config


def write_untargeted_config(
    config: Mapping[str, Any], *, config_dir: Path | None = None
) -> None:
    destination = _runtime_dir(config_dir)
    path = destination / CONFIG_NAME
    existing = _read_json_object(path)
    merged = _merge_known_keys({**existing, **config})
    _write_json_object(path, merged)
