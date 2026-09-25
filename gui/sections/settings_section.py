from pathlib import Path

from PyQt6.QtCore import QSignalBlocker, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui.sections.settings_advanced_panel import (
    build_advanced_section,
    configure_advanced_controls,
    load_advanced_values,
)
from gui.sections.settings_constants import _ADVANCED_SETTING_KEYS
from gui.sections.settings_controls import AdvancedControls
from gui.sections.settings_value_helpers import (
    _float_setting_text,
    _float_value,
    _int_setting_text,
    _int_value,
    _invalid_parallel_mode,
    _invalid_parallel_workers,
    _settings_values_are_valid,
)
from gui.sections.settings_widgets import CollapsibleSection, _LabeledSpin
from gui.ui import SPACE_LG, SPACE_MD, SPACE_SM, titled_card
from xic_extractor.config import migrate_settings_dict
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS


class SettingsSection(QWidget):
    changed = pyqtSignal()
    new_batch_requested = pyqtSignal()
    settings_saved = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._dirty = False
        self._settings_values = dict(CANONICAL_SETTINGS_DEFAULTS)
        self._invalid_parallel_mode: str | None = None
        self._invalid_parallel_workers: str | None = None

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        card, header_layout, card_layout = titled_card("設定", "")
        header_layout.addStretch()
        self._new_batch_button = QPushButton("開始新批次")
        self._new_batch_button.setToolTip("保留 DLL 與目前方法參數，清空本批資料目錄")
        self._new_batch_button.clicked.connect(lambda: self.new_batch_requested.emit())
        header_layout.addWidget(self._new_batch_button)
        self._reset_button = QPushButton("↺ 回到預設")
        self._reset_button.setToolTip("將方法參數重設為預設值（保留資料 / DLL 路徑）")
        self._reset_button.setFlat(True)
        self._reset_button.clicked.connect(self._reset_defaults)
        header_layout.addWidget(self._reset_button)
        root_layout.addWidget(card)

        body = QWidget()
        body_layout = QGridLayout(body)
        body_layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        body_layout.setHorizontalSpacing(SPACE_MD)
        body_layout.setVerticalSpacing(SPACE_SM)
        card_layout.addWidget(body)

        self._data_dir_edit = QLineEdit()
        self._dll_dir_edit = QLineEdit()
        self._ms1_morphology_smoothing_window_spin = QSpinBox()
        self._ms2_precursor_tol_da_spin = QDoubleSpinBox()
        self._nl_min_intensity_ratio_spin = QDoubleSpinBox()
        self._count_no_ms2_checkbox = QCheckBox("將 NO_MS2 視為偵測到")
        self._keep_intermediate_csv_checkbox = QCheckBox("保留中間 CSV 檔")
        self._emit_score_breakdown_checkbox = QCheckBox("輸出 Score Breakdown sheet")
        self._emit_review_report_checkbox = QCheckBox("輸出 Review Report HTML")
        self._emit_peak_candidates_checkbox = QCheckBox("輸出 Peak Candidate TSV")
        self._dirty_matrix_mode_checkbox = QCheckBox("啟用 dirty matrix mode")
        self._rolling_window_size_spin = QSpinBox()
        self._rt_prior_library_path_edit = QLineEdit()
        self._target_pair_rt_calibration_path_edit = QLineEdit()
        self._injection_order_source_edit = QLineEdit()
        self._nl_rt_anchor_search_margin_min_spin = QDoubleSpinBox()
        self._nl_rt_anchor_half_window_min_spin = QDoubleSpinBox()
        self._nl_fallback_half_window_min_spin = QDoubleSpinBox()
        self._parallel_mode_combo = QComboBox()
        self._parallel_workers_spin = QSpinBox()
        self._save_button = QPushButton("儲存設定")
        self._save_button.setObjectName("btn_save")
        self._save_button.setVisible(False)

        self._advanced_controls = AdvancedControls(
            keep_intermediate_csv_checkbox=self._keep_intermediate_csv_checkbox,
            emit_score_breakdown_checkbox=self._emit_score_breakdown_checkbox,
            emit_review_report_checkbox=self._emit_review_report_checkbox,
            emit_peak_candidates_checkbox=self._emit_peak_candidates_checkbox,
            dirty_matrix_mode_checkbox=self._dirty_matrix_mode_checkbox,
            count_no_ms2_checkbox=self._count_no_ms2_checkbox,
            rolling_window_size_spin=self._rolling_window_size_spin,
            rt_prior_library_path_edit=self._rt_prior_library_path_edit,
            target_pair_rt_calibration_path_edit=(
                self._target_pair_rt_calibration_path_edit
            ),
            injection_order_source_edit=self._injection_order_source_edit,
            nl_rt_anchor_search_margin_min_spin=(
                self._nl_rt_anchor_search_margin_min_spin
            ),
            nl_rt_anchor_half_window_min_spin=self._nl_rt_anchor_half_window_min_spin,
            nl_fallback_half_window_min_spin=self._nl_fallback_half_window_min_spin,
            parallel_mode_combo=self._parallel_mode_combo,
            parallel_workers_spin=self._parallel_workers_spin,
        )

        self._ms1_morphology_smoothing_window_spin.setRange(3, 999)
        self._ms1_morphology_smoothing_window_spin.setSingleStep(2)
        self._ms2_precursor_tol_da_spin.setRange(0.01, 10.0)
        self._ms2_precursor_tol_da_spin.setSingleStep(0.1)
        self._ms2_precursor_tol_da_spin.setDecimals(2)
        self._nl_min_intensity_ratio_spin.setRange(0.01, 1.0)
        self._nl_min_intensity_ratio_spin.setSingleStep(0.01)
        self._nl_min_intensity_ratio_spin.setDecimals(2)
        configure_advanced_controls(self._advanced_controls)

        # ── Data directory row ───────────────────────────────────────────────
        body_layout.addWidget(QLabel("資料目錄"), 0, 0)
        body_layout.addWidget(self._data_dir_edit, 0, 1)
        body_layout.addWidget(self._make_browse_button(self._data_dir_edit), 0, 2)

        self._data_dir_error = QLabel("路徑不存在")
        self._data_dir_error.setObjectName("field_error")
        self._data_dir_error.setVisible(False)
        body_layout.addWidget(self._data_dir_error, 1, 1)

        # ── Environment row ──────────────────────────────────────────────────
        self.environment_section = CollapsibleSection("Thermo DLL：需要設定（展開）")
        environment_panel = QWidget()
        environment_layout = QGridLayout(environment_panel)
        environment_layout.setContentsMargins(0, 0, 0, 0)
        environment_layout.setColumnStretch(1, 1)
        environment_layout.addWidget(QLabel("DLL 目錄"), 0, 0)
        environment_layout.addWidget(self._dll_dir_edit, 0, 1)
        environment_layout.addWidget(self._make_browse_button(self._dll_dir_edit), 0, 2)
        self.environment_section.add_row(environment_panel)
        body_layout.addWidget(self.environment_section, 2, 0, 1, 3)

        body_layout.addWidget(QLabel("共用物理峰"), 3, 0)
        body_layout.addWidget(
            _LabeledSpin(
                "Gaussian 視窗點數",
                self._ms1_morphology_smoothing_window_spin,
            ),
            3, 1, 1, 2,
        )

        self.method_advanced_section = CollapsibleSection("峰偵測進階參數")
        ms2_row = QWidget()
        ms2_layout = QHBoxLayout(ms2_row)
        ms2_layout.setContentsMargins(0, 0, 0, 0)
        ms2_layout.setSpacing(16)
        ms2_layout.addWidget(QLabel("MS2 / NL"))
        ms2_layout.addWidget(
            _LabeledSpin("Precursor tol", self._ms2_precursor_tol_da_spin)
        )
        ms2_layout.addWidget(
            _LabeledSpin("Min intensity", self._nl_min_intensity_ratio_spin)
        )
        ms2_layout.addStretch()
        self.method_advanced_section.add_row(ms2_row)
        body_layout.addWidget(self.method_advanced_section, 4, 0, 1, 3)

        self.advanced_section = CollapsibleSection("除錯與方法開發設定")
        build_advanced_section(
            self.advanced_section,
            self._advanced_controls,
            self._make_file_browse_button,
        )
        body_layout.addWidget(self.advanced_section, 5, 0, 1, 3)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(16, 0, 16, 16)
        button_row.addStretch()
        button_row.addWidget(self._save_button)
        card_layout.addLayout(button_row)

        self._wire_signals()
        self._update_dll_presentation(initial=True)

    def load(self, settings: dict[str, str]) -> bool:
        blockers = [
            QSignalBlocker(self._data_dir_edit),
            QSignalBlocker(self._dll_dir_edit),
            QSignalBlocker(self._ms1_morphology_smoothing_window_spin),
            QSignalBlocker(self._ms2_precursor_tol_da_spin),
            QSignalBlocker(self._nl_min_intensity_ratio_spin),
            QSignalBlocker(self._count_no_ms2_checkbox),
            QSignalBlocker(self._keep_intermediate_csv_checkbox),
            QSignalBlocker(self._emit_score_breakdown_checkbox),
            QSignalBlocker(self._emit_review_report_checkbox),
            QSignalBlocker(self._emit_peak_candidates_checkbox),
            QSignalBlocker(self._dirty_matrix_mode_checkbox),
            QSignalBlocker(self._rolling_window_size_spin),
            QSignalBlocker(self._rt_prior_library_path_edit),
            QSignalBlocker(self._target_pair_rt_calibration_path_edit),
            QSignalBlocker(self._injection_order_source_edit),
            QSignalBlocker(self._nl_rt_anchor_search_margin_min_spin),
            QSignalBlocker(self._nl_rt_anchor_half_window_min_spin),
            QSignalBlocker(self._nl_fallback_half_window_min_spin),
            QSignalBlocker(self._parallel_mode_combo),
            QSignalBlocker(self._parallel_workers_spin),
        ]
        try:
            migrated, _ = migrate_settings_dict(settings)
            self._settings_values = dict(CANONICAL_SETTINGS_DEFAULTS)
            self._settings_values.update(migrated)
            self._invalid_parallel_mode = _invalid_parallel_mode(
                self._settings_values.get("parallel_mode", "")
            )
            self._invalid_parallel_workers = _invalid_parallel_workers(
                self._settings_values.get("parallel_workers", "")
            )
            self._data_dir_edit.setText(self._settings_values.get("data_dir", ""))
            self._dll_dir_edit.setText(self._settings_values.get("dll_dir", ""))
            self._ms1_morphology_smoothing_window_spin.setValue(
                _int_value(
                    self._settings_values,
                    "signal_processing_window_points",
                )
            )
            self._ms2_precursor_tol_da_spin.setValue(
                _float_value(self._settings_values, "ms2_precursor_tol_da")
            )
            self._nl_min_intensity_ratio_spin.setValue(
                _float_value(self._settings_values, "nl_min_intensity_ratio")
            )
            self._count_no_ms2_checkbox.setChecked(
                self._settings_values.get("count_no_ms2_as_detected", "false").lower()
                == "true"
            )
            load_advanced_values(
                self._settings_values,
                self._advanced_controls,
            )
        finally:
            del blockers
        self._set_dirty(False)
        self._update_dll_presentation(initial=True)
        return migrated != settings

    def get_values(self) -> dict[str, str]:
        values = {
            key: self._settings_values[key]
            for key in CANONICAL_SETTINGS_DEFAULTS
            if key != "ms1_morphology_smoothing_window_points"
        }
        values.update(
            {
                "data_dir": self._data_dir_edit.text().strip(),
                "dll_dir": self._dll_dir_edit.text().strip(),
                "signal_processing_method": CANONICAL_SETTINGS_DEFAULTS[
                    "signal_processing_method"
                ],
                "signal_processing_window_points": str(
                    self._ms1_morphology_smoothing_window_spin.value()
                ),
                "ms2_precursor_tol_da": f"{self._ms2_precursor_tol_da_spin.value():g}",
                "nl_min_intensity_ratio": (
                    f"{self._nl_min_intensity_ratio_spin.value():.2f}"
                ),
                "count_no_ms2_as_detected": (
                    "true" if self._count_no_ms2_checkbox.isChecked() else "false"
                ),
                "keep_intermediate_csv": (
                    "true"
                    if self._keep_intermediate_csv_checkbox.isChecked()
                    else "false"
                ),
                "emit_score_breakdown": (
                    "true"
                    if self._emit_score_breakdown_checkbox.isChecked()
                    else "false"
                ),
                "emit_review_report": (
                    "true" if self._emit_review_report_checkbox.isChecked() else "false"
                ),
                "emit_peak_candidates": (
                    "true"
                    if self._emit_peak_candidates_checkbox.isChecked()
                    else "false"
                ),
                "dirty_matrix_mode": (
                    "true" if self._dirty_matrix_mode_checkbox.isChecked() else "false"
                ),
                "rolling_window_size": _int_setting_text(
                    self._settings_values,
                    "rolling_window_size",
                    self._rolling_window_size_spin,
                ),
                "rt_prior_library_path": (
                    self._rt_prior_library_path_edit.text().strip()
                ),
                "target_pair_rt_calibration_path": (
                    self._target_pair_rt_calibration_path_edit.text().strip()
                ),
                "injection_order_source": (
                    self._injection_order_source_edit.text().strip()
                ),
                "nl_rt_anchor_search_margin_min": _float_setting_text(
                    self._settings_values,
                    "nl_rt_anchor_search_margin_min",
                    self._nl_rt_anchor_search_margin_min_spin,
                ),
                "nl_rt_anchor_half_window_min": _float_setting_text(
                    self._settings_values,
                    "nl_rt_anchor_half_window_min",
                    self._nl_rt_anchor_half_window_min_spin,
                ),
                "nl_fallback_half_window_min": _float_setting_text(
                    self._settings_values,
                    "nl_fallback_half_window_min",
                    self._nl_fallback_half_window_min_spin,
                ),
                "parallel_mode": (
                    self._invalid_parallel_mode
                    if self._invalid_parallel_mode is not None
                    else self._parallel_mode_combo.currentText()
                ),
                "parallel_workers": (
                    self._invalid_parallel_workers
                    if self._invalid_parallel_workers is not None
                    else _int_setting_text(
                        self._settings_values,
                        "parallel_workers",
                        self._parallel_workers_spin,
                    )
                ),
            }
        )
        return values

    def is_valid(self) -> bool:
        return _settings_values_are_valid(self.get_values())

    def set_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)

    def prepare_next_batch(self) -> None:
        blocker = QSignalBlocker(self._data_dir_edit)
        self._data_dir_edit.clear()
        del blocker
        self._validate_data_dir("")
        self._set_dirty(True)

    def reveal_invalid(self) -> None:
        values = self.get_values()
        if not values["dll_dir"]:
            self.environment_section.set_expanded(True)
        values.update(data_dir="present", dll_dir="present")
        if not _settings_values_are_valid(values):
            self.method_advanced_section.set_expanded(True)
            self.advanced_section.set_expanded(True)

    def advanced_section_field_keys(self) -> tuple[str, ...]:
        return _ADVANCED_SETTING_KEYS

    def _wire_signals(self) -> None:
        self._data_dir_edit.textChanged.connect(self._validate_data_dir)
        self._data_dir_edit.textChanged.connect(lambda _: self._set_dirty(True))
        self._dll_dir_edit.textChanged.connect(self._update_dll_presentation)
        self._dll_dir_edit.textChanged.connect(lambda _: self._set_dirty(True))
        self._ms1_morphology_smoothing_window_spin.valueChanged.connect(
            lambda _: self._set_dirty(True)
        )
        self._ms2_precursor_tol_da_spin.valueChanged.connect(
            lambda _: self._set_dirty(True)
        )
        self._nl_min_intensity_ratio_spin.valueChanged.connect(
            lambda _: self._set_dirty(True)
        )
        self._count_no_ms2_checkbox.stateChanged.connect(
            lambda _: self._set_dirty(True)
        )
        self._wire_advanced_signals()
        self._save_button.clicked.connect(self._save)

    def _wire_advanced_signals(self) -> None:
        for checkbox in (
            self._keep_intermediate_csv_checkbox,
            self._emit_score_breakdown_checkbox,
            self._emit_review_report_checkbox,
            self._emit_peak_candidates_checkbox,
            self._dirty_matrix_mode_checkbox,
        ):
            checkbox.stateChanged.connect(lambda _: self._set_dirty(True))
        for line_edit in (
            self._rt_prior_library_path_edit,
            self._target_pair_rt_calibration_path_edit,
            self._injection_order_source_edit,
        ):
            line_edit.textChanged.connect(lambda _: self._set_dirty(True))
        self._parallel_mode_combo.currentTextChanged.connect(
            self._on_parallel_mode_changed
        )
        self._parallel_workers_spin.valueChanged.connect(
            self._on_parallel_workers_changed
        )
        for spin in (
            self._rolling_window_size_spin,
            self._nl_rt_anchor_search_margin_min_spin,
            self._nl_rt_anchor_half_window_min_spin,
            self._nl_fallback_half_window_min_spin,
        ):
            spin.valueChanged.connect(lambda _: self._set_dirty(True))

    def _on_parallel_mode_changed(self, _text: str) -> None:
        self._invalid_parallel_mode = None
        self._set_dirty(True)

    def _on_parallel_workers_changed(self, _value: int) -> None:
        self._invalid_parallel_workers = None
        self._set_dirty(True)

    def _validate_data_dir(self, text: str) -> None:
        stripped = text.strip()
        invalid = bool(stripped) and not Path(stripped).exists()
        self._data_dir_error.setVisible(invalid)
        # Reuse the themed QLineEdit[invalid="true"] rule from styles.py instead
        # of an inline hard-coded colour (which froze the error tint to the light
        # palette and never followed dark mode). Repolish so the property-based
        # selector re-evaluates immediately.
        self._data_dir_edit.setProperty("invalid", invalid)
        edit_style = self._data_dir_edit.style()
        if edit_style is not None:
            edit_style.unpolish(self._data_dir_edit)
            edit_style.polish(self._data_dir_edit)

    def _make_browse_button(self, target: QLineEdit) -> QPushButton:
        button = QPushButton("瀏覽…")
        button.clicked.connect(lambda: self._browse_for_directory(target))
        return button

    def _make_file_browse_button(self, target: QLineEdit) -> QPushButton:
        button = QPushButton("瀏覽…")
        button.clicked.connect(lambda: self._browse_for_file(target))
        return button

    def _browse_for_directory(self, target: QLineEdit) -> None:
        directory = QFileDialog.getExistingDirectory(self, "選擇資料夾", target.text())
        if directory:
            target.setText(directory)

    def _browse_for_file(self, target: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "選擇檔案", target.text())
        if path:
            target.setText(path)

    def _reset_defaults(self) -> None:
        """Reset method parameters to canonical defaults, keeping the data/DLL
        paths (those are environment-specific locations, not parameters). Marks
        the form dirty so the user can save — or discard — the reset."""
        defaults = dict(CANONICAL_SETTINGS_DEFAULTS)
        defaults["data_dir"] = self._data_dir_edit.text()
        defaults["dll_dir"] = self._dll_dir_edit.text()
        self.load(defaults)
        self._set_dirty(True)

    def _save(self) -> None:
        self._set_dirty(False)
        self.settings_saved.emit()

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self._save_button.setVisible(dirty)
        if dirty:
            self.changed.emit()

    def _update_dll_presentation(
        self, _text: str = "", *, initial: bool = False
    ) -> None:
        configured = bool(self._dll_dir_edit.text().strip())
        self.environment_section.set_title(
            "Thermo DLL：已設定（展開編輯）"
            if configured
            else "Thermo DLL：需要設定（展開）"
        )
        if initial:
            self.environment_section.set_expanded(not configured)
