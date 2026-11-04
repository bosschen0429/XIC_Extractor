"""Program-managed local work state, distinct from reusable user documents."""

from __future__ import annotations

import json
from pathlib import Path

from xic_extractor.configuration.atomic_write import atomic_write_text
from xic_extractor.configuration.models import ConfigError
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS

STATE_NAME = "workspace.json"
_SECTION = {
    "dll_dir": "application",
    "parallel_workers": "application",
    "data_dir": "batch",
    "injection_order_source": "batch",
}


def read_workspace(config_dir: Path) -> dict:
    path = Path(config_dir) / STATE_NAME
    if not path.exists():
        return {
            "application": {},
            "batch": {},
            "targets": [],
        }
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or set(state) - {
            "identification_methods",
            "default_method",
        } != {
            "application",
            "batch",
            "targets",
        }:
            raise ValueError("unsupported workspace schema")
        for section in ("application", "batch"):
            values = state[section]
            if not isinstance(values, dict) or any(
                key not in _SECTION
                or _SECTION[key] != section
                or not isinstance(value, str)
                for key, value in values.items()
            ):
                raise ValueError(f"invalid {section} settings")
        if not isinstance(state["targets"], list) or any(
            not isinstance(row, dict)
            or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in row.items()
            )
            for row in state["targets"]
        ):
            raise ValueError("invalid target rows")
        if not isinstance(state.get("identification_methods", {}), dict):
            raise ValueError("invalid identification method map")
        if not isinstance(state.get("default_method", ""), str):
            raise ValueError("invalid default method")
        return state
    except (OSError, ValueError, TypeError) as exc:
        raise ConfigError(
            f"{path}: 工作狀態無法讀取；原檔保留。"
            f"請手動修正欄位後重跑；舊設定檔請刪除 version 那一行。{exc}"
        ) from exc


def workspace_settings(state: dict) -> dict[str, str]:
    return {
        **CANONICAL_SETTINGS_DEFAULTS,
        **state["application"],
        **state["batch"],
    }


def save_workspace(
    config_dir: Path,
    *,
    settings: dict[str, str] | None = None,
    targets: list[dict[str, str]] | None = None,
    identification_methods: dict | None = None,
    default_method: str | None = None,
) -> None:
    state = read_workspace(config_dir)
    if identification_methods is not None:
        state["identification_methods"] = identification_methods
    if default_method is not None:
        state["default_method"] = default_method
    for key, value in (settings or {}).items():
        if key not in _SECTION:
            raise ConfigError(f"不支援工作設定：{key}")
        state[_SECTION[key]][key] = value
    if targets is not None:
        state["targets"] = [
            {
                key: str(value).lower()
                if isinstance(value, bool)
                else ""
                if value is None
                else str(value)
                for key, value in row.items()
            }
            for row in targets
        ]
    atomic_write_text(
        Path(config_dir) / STATE_NAME,
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
    )
