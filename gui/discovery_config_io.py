import copy
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from gui.config_io import targets_example_path

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).parent  # user-writable: config/, output/
else:
    ROOT = Path(__file__).resolve().parent.parent

CONFIG_DIR = ROOT / "config"

_CONFIG_NAME = "discovery_gui.json"
_DEFAULT_CONFIG: dict[str, Any] = {
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


def _config_path() -> Path:
    return CONFIG_DIR / _CONFIG_NAME


def _fresh_defaults() -> dict[str, Any]:
    defaults = copy.deepcopy(_DEFAULT_CONFIG)
    defaults["targets_csv"] = str(targets_example_path())
    return defaults


def read_discovery_config() -> dict[str, Any]:
    path = _config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _with_runtime_defaults(_fresh_defaults())

    if not isinstance(payload, dict):
        return _with_runtime_defaults(_fresh_defaults())

    return _with_runtime_defaults(_merge_known_keys(payload))


def _default_output_dir() -> str:
    """Repo-local default output location; the user can override it in the GUI."""
    return str(ROOT / "output")


def _with_runtime_defaults(config: dict[str, Any]) -> dict[str, Any]:
    """Fill a blank output_dir with the repo default so the field is never empty."""
    if not str(config.get("output_dir") or "").strip():
        config["output_dir"] = _default_output_dir()
    return config


def write_discovery_config(config: Mapping[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = _merge_known_keys(config)
    _config_path().write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _merge_known_keys(config: Mapping[str, Any]) -> dict[str, Any]:
    merged = _fresh_defaults()
    for key in _DEFAULT_CONFIG:
        if key in config:
            merged[key] = copy.deepcopy(config[key])
    return merged
