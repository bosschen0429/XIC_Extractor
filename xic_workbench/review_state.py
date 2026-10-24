"""Atomic local review checkpoints and bounded, immutable curve snapshots."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from xic_extractor.configuration.atomic_write import atomic_write_text


def file_metadata(paths: Iterable[Path]) -> dict[str, list[int]]:
    """Cheap cache freshness hints, not acquisition identity or content proof."""
    result = {}
    for path in paths:
        path = path.resolve()
        stat = path.stat()
        result[str(path)] = [stat.st_size, stat.st_mtime_ns]
    return result


class ReviewStateStore:
    """Keep one last reading under the selected application-state directory."""

    def __init__(self, config_dir: Path) -> None:
        self.folder = config_dir.resolve() / "review"
        self.path = self.folder / "state.json"

    def read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("不支援的檢閱進度格式。")
        if any(
            not isinstance(value.get(key), dict)
            for key in ("reading", "curve_inputs", "source_metadata", "query")
        ) or not isinstance(value["reading"].get("curveCache"), list):
            raise ValueError("檢閱進度格式不正確。")
        return value

    def view_path(self, view_id: str) -> Path:
        if not isinstance(view_id, str) or not re.fullmatch(r"[a-f0-9]{32}", view_id):
            raise ValueError("無效的曲線快取識別。")
        return self.folder / f"{view_id}.view.json"

    def read_view(self, view_id: str) -> Path:
        path = self.view_path(view_id)
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict) or not isinstance(
            snapshot.get("payload_data"), dict
        ):
            raise ValueError("曲線快取無法讀取。")
        return path

    def write(self, state: dict[str, Any], views: dict[str, Path]) -> None:
        # Payloads are copied once. Scrolling only replaces the small checkpoint.
        for view_id, source in views.items():
            target = self.view_path(view_id)
            if not target.exists():
                atomic_write_text(target, source.read_text(encoding="utf-8"))
        atomic_write_text(
            self.path,
            json.dumps({"version": 1, **state}, ensure_ascii=False, allow_nan=False),
        )
        retained = {self.view_path(view_id) for view_id in views}
        for path in self.folder.glob("*.view.json"):
            if (
                re.fullmatch(r"[a-f0-9]{32}\.view\.json", path.name)
                and path not in retained
            ):
                path.unlink(missing_ok=True)
