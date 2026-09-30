"""No-RAW verification of the desktop artifact through its GUI consumers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import QApplication, QMainWindow

from gui.config_io import read_settings, read_targets
from gui.method_editor import MethodEditorDialog
from xic_extractor.alignment.quantitative_projection_authority import (
    load_quantitative_projection_authority_for_diagnostic,
)
from xic_extractor.discovery.blank_qualification import get_default_background_library
from xic_extractor.presets import bind_cid_nl_recipe, load_preset


def run_desktop_smoke(app: QApplication, window: QMainWindow, report_path: Path) -> int:
    """Write a bounded startup/resource/method-roundtrip result; never read RAW."""
    report: dict[str, Any] = {
        "status": "failed",
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "stage": "qt_startup",
    }
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        app.processEvents()
        if not window.isVisible() or window.windowIcon().isNull():
            raise RuntimeError(
                "Desktop window or bundled application icon is unavailable"
            )
        report["qt_started"] = True

        report["stage"] = "bundled_resources"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        settings = read_settings()
        targets = read_targets()
        if not settings or not targets:
            raise RuntimeError("Bundled settings or targets example is empty")
        authority = load_quantitative_projection_authority_for_diagnostic()
        background = get_default_background_library()
        if background is None or not background.ions:
            raise RuntimeError("Bundled background library is unavailable or empty")
        report["resources"] = {
            "settings_keys": len(settings),
            "target_rows": len(targets),
            "authority_id": authority.authority_id,
            "background_ions": len(background.ions),
        }

        report["stage"] = "method_roundtrip"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        preset = load_preset("dna_dr")
        editor = MethodEditorDialog(preset, parent=window)
        try:
            editor.show()
            app.processEvents()
            destination = report_path.parent / "smoke-method.toml"
            if not editor.save_to(destination):
                raise RuntimeError(editor.validation_message())
            restored = load_preset(destination)
            expected = bind_cid_nl_recipe(
                preset, "full", recipe_overrides={"rt_max": 60.0}
            )
            actual = bind_cid_nl_recipe(restored, "full")
            if (
                restored.name != preset.name
                or restored.tags != preset.tags
                or actual.discovery_settings != expected.discovery_settings
            ):
                raise RuntimeError("Saved GUI method changed its bound settings")
            report["preset_roundtrip"] = True
            report["preset_path"] = str(destination)
        finally:
            editor.close()
        report["stage"] = "complete"
        report["status"] = "passed"
    except Exception as exc:
        report["reason"] = str(exc)
    try:
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError:
        return 1
    return 0 if report["status"] == "passed" else 1
