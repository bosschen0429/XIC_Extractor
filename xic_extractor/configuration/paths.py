"""Configuration locations: writable runtime state and read-only package data."""

import os
import sys
from pathlib import Path

_PACKAGE_PARENT = Path(__file__).resolve().parents[2]


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    if (_PACKAGE_PARENT / "pyproject.toml").is_file() and (
        _PACKAGE_PARENT / "config"
    ).is_dir():
        return _PACKAGE_PARENT
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "XICExtractor"
    return Path.home() / ".local" / "share" / "XICExtractor"


def targets_template_path() -> Path:
    return Path(__file__).with_name("data") / "targets.xlsx"
