from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from math import isfinite
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui import styles
from gui.method_editor import MethodEditorDialog
from gui.ui import section_card
from xic_extractor.presets import Preset, bind_cid_nl_recipe, load_preset
from xic_extractor.processing_run_spec import CidNlDiscoveryMethodSpec
from xic_extractor.settings_schema import RESOLVER_MODES

ADVANCED_NUMERIC_FIELDS: tuple[tuple[str, str, float, float, int, float], ...] = (
    ("nl_tolerance_ppm", "NL 容差 (ppm)", 0.0, 1000.0, 2, 1.0),
    ("precursor_mz_tolerance_ppm", "Precursor 容差 (ppm)", 0.0, 1000.0, 2, 1.0),
    ("product_mz_tolerance_ppm", "Product 容差 (ppm)", 0.0, 1000.0, 2, 1.0),
    ("product_search_ppm", "Product 搜尋 (ppm)", 0.0, 1000.0, 2, 1.0),
    ("nl_min_intensity_ratio", "NL 最低強度比", 0.0, 1.0, 4, 0.01),
    ("seed_rt_gap_min", "Seed RT 間隔 (min)", 0.0, 60.0, 3, 0.05),
    ("ms1_search_padding_min", "MS1 RT padding (min)", 0.0, 60.0, 3, 0.05),
    ("rt_min", "RT 下界 (min)", 0.0, 999.0, 3, 0.5),
    ("rt_max", "RT 上界 (min)", 0.0, 999.0, 3, 0.5),
)
ADVANCED_FIELDS = tuple(name for name, *_ in ADVANCED_NUMERIC_FIELDS) + (
    "resolver_mode",
)
POSITIVE_DISCOVERY_OVERRIDE_FIELDS = frozenset(
    {
        "nl_tolerance_ppm",
        "precursor_mz_tolerance_ppm",
        "product_mz_tolerance_ppm",
        "product_search_ppm",
        "nl_min_intensity_ratio",
        "seed_rt_gap_min",
        "ms1_search_padding_min",
    }
)


class DiscoveryMethodSection(QWidget):
    changed = pyqtSignal()

    def __init__(self, presets: tuple[str, ...]) -> None:
        super().__init__()
        self._edited: set[str] = set()
        self._pending_quarantine_edits: set[str] = set()
        self._quarantine_widget_values: dict[str, object] = {}
        self._loading = False
        self._unavailable_preset: str | None = None
        self._quarantined_bundle: dict[str, Any] | None = None
        self._quarantine_kind: str | None = None
        self._quarantine_reason: str | None = None
        self._spins: dict[str, QDoubleSpinBox] = {}
        self._active_preset: Preset | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card, body = section_card("② Discovery 方法", "選擇、編輯與重用方法")
        root.addWidget(card)

        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.addWidget(QLabel("Preset"))
        self._preset = QComboBox()
        for name in presets:
            self._preset.addItem(name, name)
        preset_row.addWidget(self._preset, 1)
        body.addLayout(preset_row)

        actions = QHBoxLayout()
        import_button = QPushButton("匯入方法…")
        import_button.setObjectName("import_discovery_method")
        import_button.clicked.connect(self._choose_import)
        edit_button = QPushButton("編輯／另存方法…")
        edit_button.setObjectName("edit_discovery_method")
        edit_button.clicked.connect(self._edit_method)
        new_button = QPushButton("新增方法…")
        new_button.setObjectName("new_discovery_method")
        new_button.clicked.connect(self._new_method)
        actions.addWidget(import_button)
        actions.addWidget(edit_button)
        actions.addWidget(new_button)
        actions.addStretch()
        body.addLayout(actions)
        self._method_message = QLabel()
        self._method_message.setObjectName("method_operation_message")
        self._method_message.setWordWrap(True)
        self._method_message.setTextFormat(Qt.TextFormat.PlainText)
        body.addWidget(self._method_message)

        self._preset_preview = QLabel("")
        self._preset_preview.setObjectName("preset_preview")
        self._preset_preview.setWordWrap(True)
        self._preset_preview.setTextFormat(Qt.TextFormat.RichText)
        body.addWidget(self._preset_preview)

        self._advanced_toggle = QPushButton("顯示進階覆寫")
        self._advanced_toggle.setCheckable(True)
        self._advanced_toggle.toggled.connect(self._on_toggle)
        body.addWidget(self._advanced_toggle)

        self._advanced_panel = QWidget()
        advanced_layout = QGridLayout(self._advanced_panel)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setHorizontalSpacing(12)
        advanced_layout.setVerticalSpacing(8)

        for row, (name, label, minimum, maximum, decimals, step) in enumerate(
            ADVANCED_NUMERIC_FIELDS
        ):
            advanced_layout.addWidget(QLabel(label), row, 0)
            spin = QDoubleSpinBox()
            spin.setDecimals(decimals)
            spin.setRange(_minimum_for_field(name, minimum, decimals), maximum)
            spin.setSingleStep(step)
            spin.valueChanged.connect(
                lambda _value, key=name: self._on_method_value_changed(key)
            )
            spin.editingFinished.connect(
                lambda key=name: self._on_method_edit_committed(key)
            )
            advanced_layout.addWidget(spin, row, 1)
            self._spins[name] = spin

        resolver_row = len(ADVANCED_NUMERIC_FIELDS)
        advanced_layout.addWidget(QLabel("Resolver 模式"), resolver_row, 0)
        self._resolver = QComboBox()
        for mode in RESOLVER_MODES:
            self._resolver.addItem(mode, mode)
        self._resolver.currentIndexChanged.connect(
            lambda _index: self._on_method_value_changed("resolver_mode")
        )
        self._resolver.activated.connect(
            lambda _index: self._on_method_edit_committed("resolver_mode")
        )
        advanced_layout.addWidget(self._resolver, resolver_row, 1)
        self._advanced_panel.setVisible(False)
        body.addWidget(self._advanced_panel)

        self._preset.currentIndexChanged.connect(self._on_preset_changed)
        self._preset.activated.connect(self._on_preset_activated)
        self._load_preset_defaults(clear_edits=True)

    def load(self, values: dict[str, Any]) -> None:
        if self._quarantined_bundle is not None:
            self._update_quarantine_preview()
            return

        persisted_bundle = {
            "preset": deepcopy(values.get("preset", "")),
            "overrides": deepcopy(values.get("overrides", {})),
        }
        preset = str(persisted_bundle["preset"] or "")
        blocker = QSignalBlocker(self._preset)
        try:
            sentinel_index = self._preset.findData("")
            if sentinel_index >= 0:
                self._preset.removeItem(sentinel_index)
            self._unavailable_preset = None
            index = self._preset.findData(preset)
            if index < 0 and preset:
                try:
                    imported = self._read_supported_preset(preset)
                except (OSError, TypeError, ValueError):
                    pass
                else:
                    self._preset.addItem(imported.name, preset)
                    index = self._preset.count() - 1
            if index >= 0:
                self._preset.setCurrentIndex(index)
            elif preset:
                self._unavailable_preset = preset
                self._quarantine(
                    persisted_bundle,
                    kind="unavailable_preset",
                    reason=f"preset {preset!r} 不可用",
                )
                self._preset.addItem(f"已保存但不可用：{preset}", "")
                self._preset.setCurrentIndex(self._preset.count() - 1)
            else:
                self._preset.setCurrentIndex(-1)
        finally:
            del blocker

        self._load_preset_defaults(clear_edits=True)
        self._edited.clear()
        persisted_overrides = persisted_bundle["overrides"]
        if not self._preset_value():
            return
        try:
            overrides = _validated_persisted_overrides(
                persisted_overrides,
                preset=self._preset_value(),
                spins=self._spins,
            )
        except (TypeError, ValueError) as exc:
            self._quarantine(
                persisted_bundle,
                kind="invalid_overrides",
                reason=str(exc),
            )
            self._edited.clear()
            self._update_quarantine_preview()
            return
        for key, value in overrides.items():
            self._set_override_value(key, value)

    def get_values(self) -> dict[str, Any]:
        if self._quarantined_bundle is not None:
            return deepcopy(self._quarantined_bundle)
        overrides: dict[str, Any] = {}
        for key in ADVANCED_FIELDS:
            if key not in self._edited:
                continue
            if key == "resolver_mode":
                overrides[key] = self._resolver_value()
            else:
                overrides[key] = self._spins[key].value()
        return {"preset": self._preset_value(), "overrides": overrides}

    def selected_method(self) -> Preset:
        """Return the method actually displayed, even if its file later changes."""
        if not self.is_valid() or self._active_preset is None:
            raise ValueError(self.validation_reason())
        return deepcopy(self._active_preset)

    @staticmethod
    def _read_supported_preset(source: str) -> Preset:
        preset = load_preset(source)
        recipe = bind_cid_nl_recipe(preset, "discovery_only")
        CidNlDiscoveryMethodSpec(settings=recipe.discovery_settings)
        return preset

    def import_method(self, path: Path) -> bool:
        """Select a validated method; a rejected import leaves current state intact."""
        source = str(path.resolve())
        try:
            preset = self._read_supported_preset(source)
        except (OSError, TypeError, ValueError) as exc:
            self._method_message.setText(f"無法匯入方法：{exc}")
            return False
        self._clear_quarantine()
        self._unavailable_preset = None
        blocker = QSignalBlocker(self._preset)
        try:
            index = self._preset.findData(source)
            if index < 0:
                self._preset.addItem(preset.name, source)
                index = self._preset.count() - 1
            else:
                self._preset.setItemText(index, preset.name)
            self._preset.setCurrentIndex(index)
        finally:
            del blocker
        self._load_preset_defaults(clear_edits=True)
        self._method_message.setText(f"已載入：{preset.name}")
        self.changed.emit()
        return True

    def _choose_import(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "匯入 Discovery 方法", "", "TOML 方法 (*.toml)"
        )
        if chosen:
            self.import_method(Path(chosen))

    def _edit_method(self) -> None:
        if not self.is_valid():
            self._method_message.setText(self.validation_reason())
            return
        editor = MethodEditorDialog(
            self.selected_method(),
            discovery_overrides=self.get_values()["overrides"],
            parent=self,
        )
        editor.exec()
        if editor.saved_path is not None:
            self.import_method(editor.saved_path)

    def _new_method(self) -> None:
        editor = MethodEditorDialog(
            Preset(
                name="新方法",
                description="自訂 CID-NL Discovery 方法",
                tags=(),
                combine_mode="single",
                discovery_overrides={},
                alignment_overrides={},
                source="gui:new",
            ),
            parent=self,
        )
        editor.add_tag()
        editor.exec()
        if editor.saved_path is not None:
            self.import_method(editor.saved_path)

    def set_override(self, key: str, value: Any) -> None:
        self._set_override_value(key, value)

    def numeric_value(self, key: str) -> float:
        if key not in self._spins:
            raise KeyError(key)
        return self._spins[key].value()

    def is_valid(self) -> bool:
        if self._quarantined_bundle is not None:
            return False
        return self._method_values_are_valid()

    def validation_reason(self) -> str:
        if self._quarantine_kind == "invalid_overrides":
            return (
                "已保存的方法覆寫無效："
                f"{self._quarantine_reason}；請重新選擇 preset 或修改參數"
            )
        if self._quarantine_kind == "unavailable_preset":
            return (
                f"已保存的 preset「{self._unavailable_preset}」不支援 CID-NL Discovery"
            )
        if not self._preset_value():
            return "請選擇 CID-NL Discovery preset"
        if any(
            self._spins[key].value() <= 0 for key in POSITIVE_DISCOVERY_OVERRIDE_FIELDS
        ):
            return "方法參數必須大於 0"
        if self._spins["rt_min"].value() > self._spins["rt_max"].value():
            return "RT 下界需 ≤ 上界"
        return ""

    def set_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)

    def refresh_theme(self) -> None:
        if self._quarantined_bundle is not None:
            self._update_quarantine_preview()
            return
        preset_obj = self._active_preset
        if preset_obj is not None:
            recipe = bind_cid_nl_recipe(preset_obj, "discovery_only")
            self._update_preset_preview(preset_obj, recipe.discovery_settings)
        else:
            self._update_missing_preset_preview()

    def _set_override_value(self, key: str, value: Any) -> None:
        if key == "resolver_mode":
            index = self._resolver.findData(str(value))
            if index < 0:
                raise ValueError(f"unknown resolver_mode: {value}")
            blocker = QSignalBlocker(self._resolver)
            try:
                self._resolver.setCurrentIndex(index)
            finally:
                del blocker
            if self._quarantined_bundle is None:
                self._edited.add(key)
            return

        if key not in self._spins:
            raise KeyError(key)
        blocker = QSignalBlocker(self._spins[key])
        try:
            self._spins[key].setValue(float(value))
        finally:
            del blocker
        if self._quarantined_bundle is None:
            self._edited.add(key)

    def _load_preset_defaults(self, *, clear_edits: bool) -> None:
        preset = self._preset_value()
        if not preset:
            self._active_preset = None
            if clear_edits:
                self._edited.clear()
            self._update_missing_preset_preview()
            return

        try:
            preset_obj = self._read_supported_preset(preset)
        except (OSError, TypeError, ValueError) as exc:
            self._active_preset = None
            self._unavailable_preset = preset
            self._quarantine(
                {"preset": preset, "overrides": {}},
                kind="unavailable_preset",
                reason=str(exc),
            )
            self._update_quarantine_preview()
            return
        self._active_preset = preset_obj
        self._preset.setToolTip(preset_obj.source)
        settings = bind_cid_nl_recipe(
            preset_obj,
            "discovery_only",
        ).discovery_settings
        self._update_preset_preview(preset_obj, settings)
        blockers = [QSignalBlocker(spin) for spin in self._spins.values()]
        blockers.append(QSignalBlocker(self._resolver))
        self._loading = True
        try:
            for key, spin in self._spins.items():
                spin.setValue(float(getattr(settings, key)))
                spin.setToolTip(f"方法基準值：{getattr(settings, key)!r}")
            resolver_index = self._resolver.findData(settings.resolver_mode)
            if resolver_index >= 0:
                self._resolver.setCurrentIndex(resolver_index)
            if clear_edits:
                self._edited.clear()
        finally:
            self._loading = False
            del blockers

    def _update_preset_preview(self, preset: Any, settings: Any) -> None:
        if preset is None or settings is None:
            self._preset_preview.setText("")
            self._preset_preview.setVisible(False)
            return
        description = str(getattr(preset, "description", "") or "").strip()
        combination = {
            "single": "單一 tag",
            "union": "OR：符合任一 tag",
            "intersection": "AND：同一個峰符合全部 tag",
        }[preset.combine_mode]
        tag_summary = " · ".join(
            f"{tag.name} ({tag.value!r} Da)" for tag in preset.tags
        )
        chips = " ".join(
            _chip(text)
            for text in (
                f"resolver {settings.resolver_mode}",
                f"NL≥{settings.nl_min_intensity_ratio:g}",
                f"RT {settings.rt_min:g}–{settings.rt_max:g}",
            )
        )
        desc_html = (
            f'<span style="color:{styles.ACTIVE["text_muted"]};">'
            f"{_escape(description)}</span><br>"
            if description
            else ""
        )
        self._preset_preview.setText(
            f"<b>{_escape(combination)}</b> · {_escape(tag_summary)}<br>"
            + desc_html
            + chips
        )
        self._preset_preview.setVisible(True)

    def _on_preset_changed(self, _index: int) -> None:
        if self._quarantined_bundle is not None:
            self._update_quarantine_preview()
            return
        if self._preset_value():
            self._unavailable_preset = None
        self._load_preset_defaults(clear_edits=True)
        self.changed.emit()

    def _on_preset_activated(self, _index: int) -> None:
        if self._quarantined_bundle is None or not self._preset_value():
            return
        self._clear_quarantine()
        self._unavailable_preset = None
        self._load_preset_defaults(clear_edits=True)
        self.changed.emit()

    def _update_missing_preset_preview(self) -> None:
        if self._unavailable_preset is None:
            self._update_preset_preview(None, None)
            return
        self._preset_preview.setText(
            f'<span style="color:{styles.ACTIVE["danger_border"]};">'
            f"{_escape(self.validation_reason())}</span>"
        )
        self._preset_preview.setVisible(True)

    def _update_quarantine_preview(self) -> None:
        self._preset_preview.setText(
            f'<span style="color:{styles.ACTIVE["danger_border"]};">'
            f"{_escape(self.validation_reason())}</span>"
        )
        self._preset_preview.setVisible(True)

    def _on_toggle(self, checked: bool) -> None:
        self._advanced_toggle.setText("隱藏進階覆寫" if checked else "顯示進階覆寫")
        self._advanced_panel.setVisible(checked)

    def _on_method_value_changed(self, key: str) -> None:
        if self._loading or self._quarantined_bundle is not None:
            return
        self._edited.add(key)
        self.changed.emit()

    def _on_method_edit_committed(self, key: str) -> None:
        if self._loading or self._quarantine_kind != "invalid_overrides":
            return
        if self._method_value(key) == self._quarantine_widget_values[key]:
            self._pending_quarantine_edits.discard(key)
            return
        self._pending_quarantine_edits.add(key)
        if not self._method_values_are_valid():
            return
        pending_edits = set(self._pending_quarantine_edits)
        self._clear_quarantine()
        self._edited.update(pending_edits)
        self.refresh_theme()
        self.changed.emit()

    def _method_values_are_valid(self) -> bool:
        if not self._preset_value() or self._active_preset is None:
            return False
        if any(
            self._spins[key].value() <= 0 for key in POSITIVE_DISCOVERY_OVERRIDE_FIELDS
        ):
            return False
        return self._spins["rt_min"].value() <= self._spins["rt_max"].value()

    def _quarantine(
        self,
        bundle: dict[str, Any],
        *,
        kind: str,
        reason: str,
    ) -> None:
        self._quarantined_bundle = deepcopy(bundle)
        self._quarantine_kind = kind
        self._quarantine_reason = reason
        self._pending_quarantine_edits.clear()
        self._quarantine_widget_values = {
            key: self._method_value(key) for key in ADVANCED_FIELDS
        }

    def _clear_quarantine(self) -> None:
        self._quarantined_bundle = None
        self._quarantine_kind = None
        self._quarantine_reason = None
        self._pending_quarantine_edits.clear()
        self._quarantine_widget_values.clear()

    def _method_value(self, key: str) -> object:
        if key == "resolver_mode":
            return self._resolver_value()
        return self._spins[key].value()

    def _preset_value(self) -> str:
        value = self._preset.currentData()
        return str(value if value is not None else "")

    def _resolver_value(self) -> str:
        value = self._resolver.currentData()
        return str(value if value is not None else "")


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _chip(text: str) -> str:
    c = styles.ACTIVE
    return (
        f'<span style="background:{c["primary_soft"]}; color:{c["primary_hover"]}; '
        f'padding:2px 7px; border-radius:8px; font-weight:600;">'
        f"{_escape(text)}</span>"
    )


def _minimum_for_field(name: str, minimum: float, decimals: int) -> float:
    if name not in POSITIVE_DISCOVERY_OVERRIDE_FIELDS:
        return minimum
    return max(minimum, 10**-decimals)


def _validated_persisted_overrides(
    value: object,
    *,
    preset: str,
    spins: Mapping[str, QDoubleSpinBox],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("overrides 必須是 mapping")

    overrides: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or raw_key not in ADVANCED_FIELDS:
            raise ValueError(f"不支援的 override 欄位：{raw_key!r}")
        key = raw_key
        if key == "resolver_mode":
            if not isinstance(raw_value, str) or raw_value not in RESOLVER_MODES:
                raise ValueError(f"resolver_mode 不支援：{raw_value!r}")
            overrides[key] = raw_value
            continue

        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise TypeError(f"{key} 必須是數值")
        numeric = float(raw_value)
        spin = spins[key]
        if not isfinite(numeric):
            raise ValueError(f"{key} 必須是有限數值")
        if not spin.minimum() <= numeric <= spin.maximum():
            raise ValueError(f"{key} 超出可顯示範圍")
        if round(numeric, spin.decimals()) != numeric:
            raise ValueError(f"{key} 無法由目前 widget 精確表示")
        overrides[key] = numeric

    try:
        bind_cid_nl_recipe(
            load_preset(preset),
            "discovery_only",
            recipe_overrides=overrides,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    return overrides
