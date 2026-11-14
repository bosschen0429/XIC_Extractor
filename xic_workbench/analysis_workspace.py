"""Local configuration and artifact operations for the HTML analysis workspace."""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Protocol

from xic_workbench.json_transport import json_value


class AnalysisJobControl(Protocol):
    @property
    def busy(self) -> bool: ...

    @property
    def has_unseen_completion(self) -> bool: ...

    def acknowledge_completion(self, job_id: str) -> None: ...

    def state(self) -> dict[str, Any]: ...

    def start(self, kind: str, values: dict[str, Any]) -> dict[str, Any]: ...

    def cancel(self, job_id: str) -> dict[str, Any]: ...

    def remember_samples(
        self, raw_dir: str, rows: list[dict[str, Any]], overview: dict[str, Any]
    ) -> None: ...

    def close(self) -> None: ...


class CurationOperations(Protocol):
    def describe_source(
        self, config_dir: Path, values: dict[str, Any]
    ) -> dict[str, Any]: ...

    def preview_curation(
        self, config_dir: Path, values: dict[str, Any]
    ) -> dict[str, Any]: ...


def targeted_specificity_choices() -> dict[str, Any]:
    """Editor defaults: common CID-NL losses and list-shared Delta Mass settings."""
    from dataclasses import asdict

    from xic_extractor.configuration.targeted_identification import (
        DEFAULT_DELTA_MASS_SETTINGS,
        LEGACY_CID_NL_LOSSES,
    )

    return {
        "cid_nl_losses": [
            {"name": name, "loss_da": loss}
            for name, loss in LEGACY_CID_NL_LOSSES.items()
        ],
        "delta_mass_settings": asdict(DEFAULT_DELTA_MASS_SETTINGS),
    }


def _validate_path(value: str, kind: Any) -> dict[str, Any]:
    """Explain an unusable path in the user's terms, not the OS exception text."""
    folder = kind in {"folder", "output"}
    noun = "資料夾" if folder else "檔案"
    if not value.strip():
        return {"usable": False, "message": "尚未選擇路徑"}
    path = Path(value).expanduser()
    if kind == "output" and not path.exists():
        # Each run creates its folder here, so only the nearest parent matters.
        parent = next((p for p in path.parents if p.exists()), None)
        if parent is not None and parent.is_dir():
            return {"usable": True, "message": "開始分析時建立"}
        return {"usable": False, "message": "無法在這裡建立資料夾"}
    if folder and path.is_file():
        return {"usable": False, "message": "這是檔案，請選擇資料夾"}
    if not folder and path.is_dir():
        return {"usable": False, "message": "這是資料夾，請選擇檔案"}
    try:
        if folder:
            with os.scandir(path) as entries:
                next(entries, None)
        else:
            with path.open("rb") as stream:
                stream.read(1)
    except FileNotFoundError:
        message = f"找不到這個{noun}"
    except PermissionError:
        message = f"沒有讀取這個{noun}的權限"
    except OSError:
        message = f"無法讀取這個{noun}"
    else:
        return {"usable": True, "message": "可讀取"}
    return {"usable": False, "message": message}


class AnalysisWorkspace:
    def __init__(
        self,
        config_dir: Path,
        defaults: dict[str, str],
        *,
        jobs: AnalysisJobControl,
        curation: CurationOperations,
    ) -> None:
        self.config_dir = config_dir
        self.defaults = defaults
        self.jobs = jobs
        self.curation = curation
        self._files: dict[str, Path] = {}

    def bootstrap(self) -> dict[str, Any]:
        from xic_extractor.configuration.loader import default_output_dir
        from xic_extractor.configuration.models import ConfigError
        from xic_extractor.configuration.paths import targets_template_path
        from xic_extractor.configuration.settings import (
            resolve_settings,
        )
        from xic_extractor.configuration.workspace_state import read_workspace
        from xic_workbench.config_io import (
            read_settings,
            read_targets,
        )
        from xic_workbench.methods import list_methods
        from xic_workbench.targeted_config import setting_fields
        from xic_workbench.untargeted_config import read_untargeted_config

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
            settings, warnings = {**raw, **overrides}, []
            warnings.append(f"workspace.json 有無效值，修正後才能執行 Targeted：{exc}")
        if overrides:
            warnings.append("啟動選項覆寫：" + ", ".join(overrides))
        reader = self.thermo_reader_status(settings.get("dll_dir", ""))
        configured = (
            settings.get("dll_dir", "").replace("\\", "/").rstrip("/").casefold()
        )
        if (
            not self.defaults.get("dll_dir")
            and configured in {"", "c:/xcalibur/system/programs"}
            and reader["dll_errors"]
            and reader["installed_dir"]
        ):
            settings["dll_dir"] = reader["installed_dir"]
            reader = {**reader, "dll_dir": reader["installed_dir"], "dll_errors": []}
            warnings.append("RawFileReader：使用本機已安裝版本。")
        discovery = read_untargeted_config(config_dir=self.config_dir)
        discovery["dll_dir"] = settings.get("dll_dir", "")
        if self.defaults.get("raw_dir") and not discovery.get("raw_dir"):
            discovery["raw_dir"] = self.defaults["raw_dir"]
        from xic_extractor.analysis_set import ANALYZED_ROLES

        return {
            "config_dir": str(self.config_dir),
            # The setup separates samples each flow analyzes from the rest.
            "analyzed_roles": {
                kind: sorted(roles) for kind, roles in ANALYZED_ROLES.items()
            },
            "targets_example": str(targets_template_path()),
            "review_source": self.defaults.get("source", ""),
            "settings": settings,
            "targets": read_targets(config_dir=self.config_dir),
            "delta_mass": read_workspace(self.config_dir).get("delta_mass"),
            "targeted_specificity": targeted_specificity_choices(),
            "targeted_form": {"settings": setting_fields()},
            "targeted_warnings": warnings,
            "targeted_output_dir": self.preferences().get("targeted_output_dir")
            or str(default_output_dir(self.config_dir)),
            "untargeted": discovery,
            "methods": list_methods(),
            "preferences": self.preferences(),
            "analysis": self.state(),
            "thermo_reader": reader,
        }

    def thermo_reader_status(self, dll_dir: str) -> dict[str, Any]:
        """What the RAW reader still needs on this computer, for first-run setup."""
        from xic_extractor.raw_reader import (
            DOTNET_INSTALL_COMMAND,
            dotnet_runtime_errors,
            thermo_dll_errors,
        )
        from xic_extractor.thermo_reader_install import (
            THERMO_LICENSE_URL,
            THERMO_READER_VERSION,
            default_thermo_reader_dir,
        )

        installed = default_thermo_reader_dir()
        dll_errors = (
            thermo_dll_errors(Path(dll_dir))
            if dll_dir
            else ["RawFileReader DLL directory is not set."]
        )
        installed_errors = (
            dll_errors if Path(dll_dir) == installed else thermo_dll_errors(installed)
        )
        return {
            "dll_dir": dll_dir,
            "installed_dir": "" if installed_errors else str(installed),
            "dotnet_errors": dotnet_runtime_errors(),
            "dll_errors": dll_errors,
            "dotnet_install_command": DOTNET_INSTALL_COMMAND,
            "license_url": THERMO_LICENSE_URL,
            "version": THERMO_READER_VERSION,
        }

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
                self.register_artifact(Path(path))
                for path in paths
                if Path(path).is_file()
            ]
        job = state.get("job")
        if job is not None:
            for item in job.get("available_outputs", []):
                if Path(item["path"]).is_file():
                    item["artifact"] = self.register_artifact(Path(item["path"]))
        return state

    def register_artifact(self, path: Path) -> dict[str, str]:
        """Expose a published or opened result through the local file allowlist."""
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
        if action == "paths/validate":
            return _validate_path(str(values.get("path", "")), values.get("kind"))
        if action == "preferences":
            from xic_workbench.config_io import write_preferences

            return write_preferences(values, config_dir=self.config_dir)
        if action == "targeted/save":
            from xic_workbench.config_io import (
                write_preferences,
                write_work,
            )

            settings = write_work(
                values["settings"],
                values["targets"],
                config_dir=self.config_dir,
                delta_mass=values.get("delta_mass"),
            )
            if values.get("output_dir"):
                write_preferences(
                    {"targeted_output_dir": values["output_dir"]},
                    config_dir=self.config_dir,
                )
            return {"message": "批次設定已保存。", "settings": settings}
        if action == "targeted/settings":
            from xic_extractor.configuration.settings import resolve_settings

            _settings, _parsed, warnings = resolve_settings(values["settings"])
            return {"warnings": warnings}
        if action == "targets/export":
            from xic_extractor.configuration.target_list_files import (
                create_target_list,
                save_target_list,
            )

            path = Path(values["path"])
            save_target_list(
                create_target_list(
                    values["targets"], delta_mass=values.get("delta_mass")
                ),
                path,
            )
            return {"message": f"已匯出目標清單：{path}", "path": str(path)}
        if action == "targets/upload":
            from xic_workbench.target_import import preview_target_upload

            return preview_target_upload(values.get("name"), values.get("content"))
        if action == "targets/import":
            from xic_workbench.target_import import import_targets

            return {"targets": import_targets(Path(values["path"]))}
        if action == "thermo-reader/status":
            dll_dir = values.get("dll_dir", "")
            if not isinstance(dll_dir, str):
                raise ValueError("請提供 DLL 資料夾。")
            return self.thermo_reader_status(dll_dir)
        if action in {"thermo-reader/install", "thermo-reader/use-installed"}:
            from xic_extractor.thermo_reader_install import (
                default_thermo_reader_dir,
                install_thermo_reader,
            )
            from xic_workbench.config_io import write_dll_dir

            if action == "thermo-reader/install":
                target = install_thermo_reader(
                    accept_license=values.get("accept_license") is True
                )
                message = f"RawFileReader 已安裝，DLL 資料夾已設為 {target}"
            else:
                from xic_extractor.raw_reader import thermo_dll_errors

                target = default_thermo_reader_dir()
                errors = thermo_dll_errors(target)
                if errors:
                    raise ValueError(
                        "已安裝的 RawFileReader 不完整，請重新下載。 "
                        + " ".join(errors)
                    )
                message = f"已使用本機 RawFileReader，DLL 資料夾已設為 {target}"
            write_dll_dir(str(target), config_dir=self.config_dir)
            if self.defaults.pop("dll_dir", None):
                message += (
                    "；下次啟動前請更新或移除 --dll-dir／"
                    "THERMO_RAWFILE_READER_DLL_DIR 舊設定。"
                )
            return {
                "message": message,
                "dll_dir": str(target),
                "thermo_reader": self.thermo_reader_status(str(target)),
            }
        if action == "untargeted/save":
            from xic_workbench.config_io import write_dll_dir
            from xic_workbench.untargeted_config import write_untargeted_config

            if "dll_dir" in values:
                write_dll_dir(values["dll_dir"], config_dir=self.config_dir)
            write_untargeted_config(values, config_dir=self.config_dir)
            return {"message": "批次與方法設定已儲存。"}
        if action == "method":
            from xic_workbench.methods import method_details, preset_from_payload

            if "draft" in values:
                return {
                    **method_details(preset_from_payload(values["draft"])),
                    "path": str(
                        (self.config_dir / "current_discovery_method.toml").resolve()
                    ),
                }
            return method_details(values["preset"])
        if action == "method/save":
            from xic_workbench.methods import save_method

            return save_method(values["draft"], Path(values["path"]))
        if action == "samples/save":
            from xic_workbench.samples import save_batch_samples

            saved = save_batch_samples(
                self.config_dir, Path(values["raw_dir"]), values["samples"]
            )
            self.jobs.remember_samples(
                values["raw_dir"], values["samples"], saved["overview"]
            )
            return saved
        if action == "samples/exclude":
            from xic_workbench.samples import exclude_sample

            raw_dir = Path(values["raw_dir"])
            result = exclude_sample(
                self.config_dir,
                raw_dir,
                str(values["raw_stem"]),
                str(values.get("reason") or ""),
                dll_dir=Path(values["dll_dir"]) if values.get("dll_dir") else None,
                sequence_source=Path(values["sequence_source"])
                if values.get("sequence_source")
                else None,
            )
            self.jobs.remember_samples(
                values["raw_dir"], result["samples"], result["overview"]
            )
            return result
        if action == "samples/export":
            from xic_workbench.samples import export_samples

            return export_samples(values["samples"], Path(values["path"]))
        if action == "matrix-curation/source":
            return self.curation.describe_source(self.config_dir, values)
        if action in {"istd", "istd/upload"}:
            from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
            from xic_extractor.configuration.targets import read_istd_targets
            from xic_workbench.methods import preset_from_payload
            from xic_workbench.target_import import import_istd_upload

            profiles = None
            if "preset_snapshot" in values:
                # A Delta Mass method's applicable standards follow its CID-NL tags.
                profiles = tuple(
                    NeutralLossProfile(tag.name, tag.value)
                    for tag in preset_from_payload(values["preset_snapshot"]).tags
                    if tag.strategy == "neutral_loss" and tag.value is not None
                )
            if action == "istd/upload":
                return json_value(
                    import_istd_upload(
                        values.get("name"),
                        values.get("content"),
                        self.config_dir,
                        profiles=profiles,
                    )
                )
            if not values.get("path"):
                return {"message": "本次不使用內標清單。"}
            selection = read_istd_targets(Path(values["path"]))
            if profiles is not None:
                selection = selection.for_method(profiles)
            return {"selection": json_value(selection)}
        if action == "istd/copy":
            from xic_extractor.configuration.paths import targets_template_path

            source, destination = Path(values["path"]), Path(values["destination"])
            if destination.resolve() == targets_template_path().resolve():
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
            if sys.platform != "win32":
                raise ValueError(f"請在檔案管理員開啟：{path}")
            os.startfile(str(path))  # noqa: S606
            return {"path": str(path)}
        raise ValueError("不支援此操作。")

    def close(self) -> None:
        self.jobs.close()
