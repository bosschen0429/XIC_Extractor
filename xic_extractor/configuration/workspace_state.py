"""Program-managed local work state, distinct from reusable user documents."""

from __future__ import annotations

import json
from pathlib import Path

from xic_extractor.configuration.atomic_write import atomic_write_text
from xic_extractor.configuration.legacy_target_methods import upgrade_target_table
from xic_extractor.configuration.models import ConfigError
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS

STATE_NAME = "workspace.json"
_SECTION = {
    "dll_dir": "application",
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
            "delta_mass",
            "identification_methods",
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
        legacy = state.pop("identification_methods", None)
        if legacy is not None and not isinstance(legacy, dict):
            raise ValueError("invalid identification method map")
        rows, legacy_details = upgrade_target_table(state["targets"], legacy)
        state["targets"] = rows
        if legacy_details is not None:
            state["delta_mass"] = legacy_details
        if not isinstance(state.get("delta_mass", {}), dict):
            raise ValueError("invalid Delta Mass details")
        return state
    except (OSError, ValueError, TypeError, ConfigError) as exc:
        raise ConfigError(
            f"{path}: 工作區設定檔無法讀取，原檔已保留。刪除或改名這個檔案後重新啟動，"
            "工作區會用預設值重新建立；目前儲存的批次設定與目標清單會清空。"
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
    delta_mass: dict | None = None,
) -> None:
    """Targets and their Delta Mass details are always written together."""
    state = read_workspace(config_dir)
    if targets is not None:
        state.pop("delta_mass", None)
        if delta_mass:
            state["delta_mass"] = delta_mass
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
