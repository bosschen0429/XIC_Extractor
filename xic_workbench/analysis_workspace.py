"""Local configuration and artifact operations for the HTML analysis workspace."""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from xic_workbench.jobs import AnalysisJobs


def json_value(value: Any) -> Any:
    """Project path-bearing application receipts onto the JSON transport."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return json_value(dataclasses.asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value


class AnalysisWorkspace:
    def __init__(self, config_dir: Path, defaults: dict[str, str]) -> None:
        self.config_dir = config_dir
        self.defaults = defaults
        self.jobs = AnalysisJobs(config_dir)
        self._files: dict[str, Path] = {}

    def bootstrap(self) -> dict[str, Any]:
        from xic_extractor.configuration.models import ConfigError
        from xic_extractor.configuration.settings import (
            fill_setting_defaults,
            resolve_settings,
        )
        from xic_workbench.config_io import (
            read_settings,
            read_targets,
            targets_example_path,
        )
        from xic_workbench.discovery_config_io import read_discovery_config
        from xic_workbench.methods import list_methods
        from xic_workbench.targeted import method_summary, setting_fields

        raw = read_settings(config_dir=self.config_dir)
        overrides = {}
        if self.defaults.get("dll_dir"):
            overrides["dll_dir"] = self.defaults["dll_dir"]
        if self.defaults.get("raw_dir") and (
            not raw.get("data_dir") or raw["data_dir"] == "C:/your/data/folder"
        ):
            overrides["data_dir"] = self.defaults["raw_dir"]
        try:
            settings, _parsed, warnings = resolve_settings(raw, overrides)
        except ConfigError as exc:
            # Opening the workspace must stay possible so the value can be fixed.
            settings, warnings = fill_setting_defaults({**raw, **overrides})
            warnings.append(f"settings.csv 有無效值，修正後才能執行 Targeted：{exc}")
        if overrides:
            warnings.append("啟動選項覆寫：" + ", ".join(overrides))
        discovery = read_discovery_config(config_dir=self.config_dir)
        discovery["dll_dir"] = settings.get("dll_dir", "")
        if self.defaults.get("raw_dir") and not discovery.get("raw_dir"):
            discovery["raw_dir"] = self.defaults["raw_dir"]
        source = (
            "settings.csv"
            if (self.config_dir / "settings.csv").exists()
            else "settings.example.csv"
        )
        if source == "settings.example.csv":
            warnings.append("尚未儲存 settings.csv，目前使用範本設定。")
        return {
            "config_dir": str(self.config_dir),
            "targets_example": str(targets_example_path()),
            "review_source": self.defaults.get("source", ""),
            "settings": settings,
            "targets": read_targets(config_dir=self.config_dir),
            "targeted_form": {"settings": setting_fields()},
            "targeted_method": {
                **method_summary(settings),
                "warnings": warnings,
            },
            "settings_source": source,
            "untargeted": discovery,
            "methods": list_methods(),
            "targeted_methods": self.targeted_methods(),
            "preferences": self.preferences(),
            "analysis": self.state(),
        }

    def targeted_methods(self) -> list[dict[str, str]]:
        root = self.config_dir / "methods"
        if not root.exists():
            return []
        return [
            {"name": path.name, "path": str(path.absolute())}
            for path in sorted(root.iterdir())
            if path.is_dir()
            and (path / "settings.csv").is_file()
            and (path / "targets.csv").is_file()
        ]

    def preferences(self) -> dict[str, str]:
        from xic_workbench.config_io import read_preferences

        return read_preferences(config_dir=self.config_dir)

    def state(self) -> dict[str, Any]:
        state = json_value(self.jobs.state())
        for result in state["results"].values():
            paths = result.get("published_paths", [])
            paths += [
                result[key] for key in ("output", "manifest_path") if result.get(key)
            ]
            result["artifacts"] = [
                self._artifact(Path(path)) for path in paths if Path(path).is_file()
            ]
        job = state.get("job")
        if job is not None:
            for item in job.get("available_outputs", []):
                if Path(item["path"]).is_file():
                    item["artifact"] = self._artifact(Path(item["path"]))
        return state

    def _artifact(self, path: Path) -> dict[str, str]:
        path = path.resolve()
        key = next((key for key, value in self._files.items() if value == path), None)
        if key is None:
            key = uuid.uuid4().hex
            self._files[key] = path
        return {"name": path.name, "path": str(path), "url": f"/files/{key}"}

    def artifact_path(self, key: str) -> Path:
        try:
            return self._files[key]
        except KeyError as exc:
            raise ValueError("這個檔案不屬於目前工作區的輸出。") from exc

    def action(self, action: str, values: dict[str, Any]) -> dict[str, Any]:
        if action == "targeted/method/save":
            from xic_extractor.configuration.targeted_method_files import (
                create_targeted_method,
                save_targeted_method,
            )

            name = str(values["name"]).strip()
            if (
                not name
                or name in {".", ".."}
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or name.endswith((".", " "))
                or name.split(".")[0].upper()
                in {
                    "CON",
                    "PRN",
                    "AUX",
                    "NUL",
                    *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10)),
                }
            ):
                raise ValueError("請輸入有效的方法名稱，不可使用路徑或系統保留名稱。")
            path = self.config_dir / "methods" / name
            if path.exists():
                raise ValueError(
                    "方法名稱已存在；請使用新名稱另存版本，原方法不會被覆寫。"
                )
            method = create_targeted_method(values["settings"], values["targets"])
            save_targeted_method(method, path)
            return {
                "name": name,
                "methods": self.targeted_methods(),
                "message": f"已儲存方法：{name}",
            }
        if action == "targeted/method/load":
            from xic_extractor.configuration.targeted_method_files import (
                apply_targeted_method,
                load_targeted_method,
            )
            from xic_workbench.targeted import method_summary

            method = load_targeted_method(Path(values["path"]))
            settings = apply_targeted_method(values["settings"], method)
            return {
                "settings": settings,
                "targets": [dict(row) for row in method.target_rows],
                "summary": method_summary(settings),
            }
        if action == "preferences":
            from xic_workbench.config_io import write_preferences

            return write_preferences(values, config_dir=self.config_dir)
        if action == "targeted/save":
            from xic_workbench.config_io import write_settings, write_targets
            from xic_workbench.targeted import method_summary

            settings = write_settings(values["settings"], config_dir=self.config_dir)
            write_targets(values["targets"], config_dir=self.config_dir)
            return {
                "message": "設定與目標已儲存。",
                "summary": method_summary(settings),
            }
        if action in {"targeted/method", "targeted/method/reset"}:
            from xic_extractor.configuration.settings import (
                reset_method_settings,
                resolve_settings,
            )
            from xic_workbench.config_io import read_settings
            from xic_workbench.targeted import method_summary

            raw = (
                values["settings"]
                if "settings" in values
                else read_settings(config_dir=self.config_dir)
            )
            if action == "targeted/method/reset":
                raw = reset_method_settings(raw)
            settings, _parsed, warnings = resolve_settings(raw)
            summary = {**method_summary(settings), "warnings": warnings}
            return (
                {"settings": settings, "summary": summary}
                if action.endswith("/reset")
                else summary
            )
        if action == "targets/import":
            from xic_workbench.target_import import import_targets

            return {
                "targets": import_targets(
                    Path(values["path"]), sheet=values.get("sheet", "both")
                )
            }
        if action == "untargeted/save":
            from xic_workbench.config_io import write_dll_dir
            from xic_workbench.discovery_config_io import write_discovery_config

            if "dll_dir" in values:
                write_dll_dir(values["dll_dir"], config_dir=self.config_dir)
            write_discovery_config(values, config_dir=self.config_dir)
            return {"message": "批次與方法設定已儲存。"}
        if action == "method":
            from xic_workbench.methods import method_details

            return method_details(values["preset"])
        if action == "method/save":
            from xic_workbench.methods import save_method

            destination = (
                self.config_dir / "current_discovery_method.toml"
                if values.get("apply")
                else Path(values["path"])
            )
            return save_method(values["draft"], destination)
        if action == "samples/export":
            from xic_workbench.samples import export_samples

            return export_samples(values["samples"], Path(values["path"]))
        if action == "istd":
            from xic_extractor.configuration.targets import read_istd_targets

            if not values.get("path"):
                return {"message": "本次不使用內標清單。"}
            selection = read_istd_targets(Path(values["path"]))
            return {"selection": json_value(selection)}
        if action == "istd/copy":
            from xic_workbench.config_io import targets_example_path

            source, destination = Path(values["path"]), Path(values["destination"])
            if destination.resolve() == targets_example_path().resolve():
                raise ValueError("請另存副本，保留隨附內標範本。")
            if source.resolve() == destination.resolve():
                raise ValueError("請選擇新的副本位置。")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            return {"path": str(destination)}
        if action == "open-local":
            path = Path(values["path"]).resolve()
            if not path.exists():
                raise ValueError("這個位置已不存在。")
            if path.is_file() and path.suffix.lower() not in {
                ".csv",
                ".tsv",
                ".xlsx",
                ".xls",
                ".html",
                ".json",
                ".toml",
                ".txt",
            }:
                raise ValueError("工作區只會開啟分析文件或資料夾。")
            if os.name != "nt":
                raise ValueError(f"請在檔案管理員開啟：{path}")
            os.startfile(str(path))  # noqa: S606
            return {"path": str(path)}
        raise ValueError("不支援此操作。")

    def close(self) -> None:
        self.jobs.close()
