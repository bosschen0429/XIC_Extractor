from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
)


@dataclass(frozen=True)
class AdvancedControls:
    keep_intermediate_csv_checkbox: QCheckBox
    emit_score_breakdown_checkbox: QCheckBox
    emit_review_report_checkbox: QCheckBox
    emit_peak_candidates_checkbox: QCheckBox
    dirty_matrix_mode_checkbox: QCheckBox
    count_no_ms2_checkbox: QCheckBox
    rolling_window_size_spin: QSpinBox
    rt_prior_library_path_edit: QLineEdit
    target_pair_rt_calibration_path_edit: QLineEdit
    injection_order_source_edit: QLineEdit
    nl_rt_anchor_search_margin_min_spin: QDoubleSpinBox
    nl_rt_anchor_half_window_min_spin: QDoubleSpinBox
    nl_fallback_half_window_min_spin: QDoubleSpinBox
    parallel_mode_combo: QComboBox
    parallel_workers_spin: QSpinBox
