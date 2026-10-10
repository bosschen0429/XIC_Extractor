"""Local configuration and artifact operations for the HTML analysis workspace."""

from __future__ import annotations

import dataclasses
import json
import os
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
        from xic_workbench.config_io import (
            read_settings,
            read_targets,
            targets_example_path,
        )
        from xic_workbench.discovery_config_io import read_discovery_config
        from xic_workbench.methods import list_methods
        from xic_workbench.targeted import method_summary, targeted_form

        settings = read_settings(config_dir=self.config_dir)
        discovery = read_discovery_config(config_dir=self.config_dir)
        preferences = self.preferences()
        dll = self.defaults.get("dll_dir") or preferences.get("dll_dir")
        if dll:
            settings["dll_dir"] = discovery["dll_dir"] = dll
        if self.defaults.get("raw_dir"):
            if (
                not settings.get("data_dir")
                or settings["data_dir"] == "C:/your/data/folder"
            ):
                settings["data_dir"] = self.defaults["raw_dir"]
            if not discovery.get("raw_dir"):
                discovery["raw_dir"] = self.defaults["raw_dir"]
        return {
            "config_dir": str(self.config_dir),
            "targets_example": str(targets_example_path()),
            "review_source": self.defaults.get("source", ""),
            "settings": settings,
            "targets": read_targets(config_dir=self.config_dir),
            "targeted_form": targeted_form(),
            "targeted_method": method_summary(settings),
            "untargeted": discovery,
            "methods": list_methods(),
            "preferences": preferences,
            "analysis": self.state(),
        }

    def preferences(self) -> dict[str, str]:
        try:
            data = json.loads(
                (self.config_dir / "workbench.json").read_text(encoding="utf-8")
            )
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

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
        if action == "preferences":
            if set(values) - {"theme", "view", "dll_dir"}:
                raise ValueError("不支援此偏好設定。")
            data = self.preferences() | values
            self.config_dir.mkdir(parents=True, exist_ok=True)
            (self.config_dir / "workbench.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            return data
        if action == "targeted/save":
            from xic_workbench.config_io import write_settings, write_targets

            write_settings(values["settings"], config_dir=self.config_dir)
            write_targets(values["targets"], config_dir=self.config_dir)
            return {"message": "設定與目標已儲存。"}
        if action == "targeted/method":
            from xic_workbench.targeted import method_summary

            return method_summary(values["settings"])
        if action == "targets/import":
            from xic_workbench.target_import import import_targets

            return {
                "targets": import_targets(
                    Path(values["path"]), sheet=values.get("sheet", "both")
                )
            }
        if action == "untargeted/save":
            from xic_workbench.discovery_config_io import write_discovery_config

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
