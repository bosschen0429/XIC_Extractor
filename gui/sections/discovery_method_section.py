from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui import styles
from gui.ui import section_card
from xic_extractor.presets import bind_cid_nl_recipe, load_preset
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
        self._loading = False
        self._unavailable_preset: str | None = None
        self._spins: dict[str, QDoubleSpinBox] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card, body = section_card("② 方法", "preset 與方法參數")
        root.addWidget(card)

        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.addWidget(QLabel("Preset"))
        self._preset = QComboBox()
        for name in presets:
            self._preset.addItem(name, name)
        preset_row.addWidget(self._preset, 1)
        body.addLayout(preset_row)

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
            spin.valueChanged.connect(lambda _value, key=name: self._mark_edited(key))
            advanced_layout.addWidget(spin, row, 1)
            self._spins[name] = spin

        resolver_row = len(ADVANCED_NUMERIC_FIELDS)
        advanced_layout.addWidget(QLabel("Resolver 模式"), resolver_row, 0)
        self._resolver = QComboBox()
        for mode in RESOLVER_MODES:
            self._resolver.addItem(mode, mode)
        self._resolver.currentIndexChanged.connect(
            lambda _index: self._mark_edited("resolver_mode")
        )
        advanced_layout.addWidget(self._resolver, resolver_row, 1)
        self._advanced_panel.setVisible(False)
        body.addWidget(self._advanced_panel)

        self._preset.currentIndexChanged.connect(self._on_preset_changed)
        self._load_preset_defaults(clear_edits=True)

    def load(self, values: dict[str, Any]) -> None:
        preset = str(values.get("preset", "") or "")
        blocker = QSignalBlocker(self._preset)
        try:
            sentinel_index = self._preset.findData("")
            if sentinel_index >= 0:
                self._preset.removeItem(sentinel_index)
            self._unavailable_preset = None
            index = self._preset.findData(preset)
            if index >= 0:
                self._preset.setCurrentIndex(index)
            elif preset:
                self._unavailable_preset = preset
                self._preset.addItem(f"已保存但不可用：{preset}", "")
                self._preset.setCurrentIndex(self._preset.count() - 1)
            else:
                self._preset.setCurrentIndex(-1)
        finally:
            del blocker

        self._load_preset_defaults(clear_edits=True)
        self._edited.clear()
        for key, value in _sanitized_persisted_overrides(
            values.get("overrides")
        ).items():
            self._set_override_value(key, value)

    def get_values(self) -> dict[str, Any]:
        overrides: dict[str, Any] = {}
        for key in ADVANCED_FIELDS:
            if key not in self._edited:
                continue
            if key == "resolver_mode":
                overrides[key] = self._resolver_value()
            else:
                overrides[key] = self._spins[key].value()
        return {"preset": self._preset_value(), "overrides": overrides}

    def set_override(self, key: str, value: Any) -> None:
        self._set_override_value(key, value)

    def numeric_value(self, key: str) -> float:
        if key not in self._spins:
            raise KeyError(key)
        return self._spins[key].value()

    def is_valid(self) -> bool:
        if not self._preset_value():
            return False
        for key in POSITIVE_DISCOVERY_OVERRIDE_FIELDS:
            if self._spins[key].value() <= 0:
                return False
        return self._spins["rt_min"].value() <= self._spins["rt_max"].value()

    def validation_reason(self) -> str:
        if self._unavailable_preset is not None and not self._preset_value():
            return (
                f"已保存的 preset「{self._unavailable_preset}」"
                "不支援 CID-NL Discovery"
            )
        if not self._preset_value():
            return "請選擇 CID-NL Discovery preset"
        if any(
            self._spins[key].value() <= 0
            for key in POSITIVE_DISCOVERY_OVERRIDE_FIELDS
        ):
            return "方法參數必須大於 0"
        if self._spins["rt_min"].value() > self._spins["rt_max"].value():
            return "RT 下界需 ≤ 上界"
        return ""

    def set_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)

    def refresh_theme(self) -> None:
        preset = self._preset_value()
        if preset:
            preset_obj = load_preset(preset)
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
            self._edited.add(key)
            return

        if key not in self._spins:
            raise KeyError(key)
        blocker = QSignalBlocker(self._spins[key])
        try:
            self._spins[key].setValue(float(value))
        finally:
            del blocker
        self._edited.add(key)

    def _load_preset_defaults(self, *, clear_edits: bool) -> None:
        preset = self._preset_value()
        if not preset:
            if clear_edits:
                self._edited.clear()
            self._update_missing_preset_preview()
            return

        preset_obj = load_preset(preset)
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
        self._preset_preview.setText(desc_html + chips)
        self._preset_preview.setVisible(True)

    def _on_preset_changed(self, _index: int) -> None:
        if self._preset_value():
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

    def _on_toggle(self, checked: bool) -> None:
        self._advanced_toggle.setText(
            "隱藏進階覆寫" if checked else "顯示進階覆寫"
        )
        self._advanced_panel.setVisible(checked)

    def _mark_edited(self, key: str) -> None:
        if not self._loading:
            self._edited.add(key)
            self.changed.emit()

    def _preset_value(self) -> str:
        value = self._preset.currentData()
        return str(value if value is not None else "")

    def _resolver_value(self) -> str:
        value = self._resolver.currentData()
        return str(value if value is not None else "")


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


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


def _sanitized_persisted_overrides(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}

    overrides: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if key == "resolver_mode":
            mode = str(raw_value)
            if mode in RESOLVER_MODES:
                overrides[key] = mode
            continue

        if key not in ADVANCED_FIELDS:
            continue
        try:
            numeric = float(raw_value)
        except (TypeError, ValueError):
            continue
        if isfinite(numeric):
            overrides[key] = numeric
    return overrides
