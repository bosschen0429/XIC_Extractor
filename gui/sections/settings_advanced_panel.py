from collections.abc import Callable

from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from gui.sections.settings_controls import AdvancedControls
from gui.sections.settings_value_helpers import _bool_value, _float_value, _int_value
from gui.sections.settings_widgets import (
    CollapsibleSection,
    _LabeledSpin,
    _set_float_range,
)


def configure_advanced_controls(controls: AdvancedControls) -> None:
    controls.parallel_mode_combo.addItems(["serial", "process"])
    controls.rolling_window_size_spin.setRange(1, 999)
    controls.parallel_workers_spin.setRange(1, 999)
    _set_float_range(controls.nl_rt_anchor_search_margin_min_spin, 0.0, 100.0, 3)
    _set_float_range(controls.nl_rt_anchor_half_window_min_spin, 0.0, 100.0, 3)
    _set_float_range(controls.nl_fallback_half_window_min_spin, 0.0, 100.0, 3)


def build_advanced_section(
    section: CollapsibleSection,
    controls: AdvancedControls,
    file_browse_button_factory: Callable[[QLineEdit], QPushButton],
) -> None:
    help_label = QLabel("下列選項僅在除錯或方法開發時需要。日常使用請保持預設值。")
    help_label.setObjectName("hint")
    section.add_row(help_label)

    body = QWidget()
    layout = QGridLayout(body)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setHorizontalSpacing(12)
    layout.setVerticalSpacing(8)

    debug_flags_layout = QHBoxLayout()
    debug_flags_layout.setContentsMargins(0, 0, 0, 0)
    debug_flags_layout.setSpacing(16)
    debug_flags_layout.addWidget(controls.keep_intermediate_csv_checkbox)
    debug_flags_layout.addWidget(controls.emit_score_breakdown_checkbox)
    debug_flags_layout.addWidget(controls.emit_review_report_checkbox)
    debug_flags_layout.addWidget(controls.emit_peak_candidates_checkbox)
    debug_flags_layout.addWidget(controls.dirty_matrix_mode_checkbox)
    debug_flags_layout.addWidget(controls.count_no_ms2_checkbox)
    debug_flags_layout.addStretch()
    layout.addLayout(debug_flags_layout, 0, 0, 1, 3)

    layout.addWidget(QLabel("Rolling window size"), 1, 0)
    layout.addWidget(controls.rolling_window_size_spin, 1, 1)

    parallel_layout = QHBoxLayout()
    parallel_layout.setContentsMargins(0, 0, 0, 0)
    parallel_layout.setSpacing(8)
    parallel_layout.addWidget(QLabel("Mode"))
    parallel_layout.addWidget(controls.parallel_mode_combo)
    parallel_layout.addWidget(QLabel("Workers"))
    parallel_layout.addWidget(controls.parallel_workers_spin)
    parallel_layout.addStretch()
    layout.addWidget(QLabel("Parallel execution"), 2, 0)
    layout.addLayout(parallel_layout, 2, 1, 1, 2)

    layout.addWidget(QLabel("RT prior library (developer/debug)"), 3, 0)
    layout.addWidget(controls.rt_prior_library_path_edit, 3, 1)
    layout.addWidget(
        file_browse_button_factory(controls.rt_prior_library_path_edit), 3, 2
    )

    layout.addWidget(QLabel("Target pair RT calibration (shadow)"), 4, 0)
    layout.addWidget(controls.target_pair_rt_calibration_path_edit, 4, 1)
    layout.addWidget(
        file_browse_button_factory(controls.target_pair_rt_calibration_path_edit),
        4,
        2,
    )

    layout.addWidget(QLabel("Injection order source"), 5, 0)
    layout.addWidget(controls.injection_order_source_edit, 5, 1)
    layout.addWidget(
        file_browse_button_factory(controls.injection_order_source_edit), 5, 2
    )

    nl_layout = QHBoxLayout()
    nl_layout.setContentsMargins(0, 0, 0, 0)
    nl_layout.setSpacing(16)
    nl_layout.addWidget(
        _LabeledSpin("NL search", controls.nl_rt_anchor_search_margin_min_spin)
    )
    nl_layout.addWidget(
        _LabeledSpin("NL anchor window", controls.nl_rt_anchor_half_window_min_spin)
    )
    nl_layout.addWidget(
        _LabeledSpin("NL fallback", controls.nl_fallback_half_window_min_spin)
    )
    nl_layout.addStretch()
    layout.addWidget(QLabel("NL RT windows"), 6, 0)
    layout.addLayout(nl_layout, 6, 1, 1, 2)

    layout.setColumnStretch(1, 1)
    section.add_row(body)


def load_advanced_values(
    settings_values: dict[str, str],
    advanced_controls: AdvancedControls,
) -> None:
    advanced_controls.keep_intermediate_csv_checkbox.setChecked(
        _bool_value(settings_values, "keep_intermediate_csv")
    )
    advanced_controls.emit_score_breakdown_checkbox.setChecked(
        _bool_value(settings_values, "emit_score_breakdown")
    )
    advanced_controls.emit_review_report_checkbox.setChecked(
        _bool_value(settings_values, "emit_review_report")
    )
    advanced_controls.emit_peak_candidates_checkbox.setChecked(
        _bool_value(settings_values, "emit_peak_candidates")
    )
    advanced_controls.dirty_matrix_mode_checkbox.setChecked(
        _bool_value(settings_values, "dirty_matrix_mode")
    )
    advanced_controls.rolling_window_size_spin.setValue(
        _int_value(settings_values, "rolling_window_size")
    )
    advanced_controls.rt_prior_library_path_edit.setText(
        settings_values.get("rt_prior_library_path", "")
    )
    advanced_controls.target_pair_rt_calibration_path_edit.setText(
        settings_values.get("target_pair_rt_calibration_path", "")
    )
    advanced_controls.injection_order_source_edit.setText(
        settings_values.get("injection_order_source", "")
    )
    advanced_controls.nl_rt_anchor_search_margin_min_spin.setValue(
        _float_value(settings_values, "nl_rt_anchor_search_margin_min")
    )
    advanced_controls.nl_rt_anchor_half_window_min_spin.setValue(
        _float_value(settings_values, "nl_rt_anchor_half_window_min")
    )
    advanced_controls.nl_fallback_half_window_min_spin.setValue(
        _float_value(settings_values, "nl_fallback_half_window_min")
    )
    parallel_mode = settings_values.get("parallel_mode", "serial")
    if parallel_mode not in {"serial", "process"}:
        parallel_mode = "serial"
    advanced_controls.parallel_mode_combo.setCurrentText(parallel_mode)
    advanced_controls.parallel_workers_spin.setValue(
        _int_value(settings_values, "parallel_workers")
    )
