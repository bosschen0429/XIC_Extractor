from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from gui.sections.settings_section import SettingsSection
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS


def _canonical_settings() -> dict[str, str]:
    return {
        "data_dir": "C:\\data",
        "dll_dir": "C:\\dll",
        "signal_processing_method": "gaussian",
        "signal_processing_window_points": "15",
        "ms2_precursor_tol_da": "0.4",
        "nl_min_intensity_ratio": "0.02",
        "count_no_ms2_as_detected": "true",
        "injection_order_source": "",
        "rolling_window_size": "5",
        "dirty_matrix_mode": "false",
        "rt_prior_library_path": "",
        "target_pair_rt_calibration_path": "",
        "model_selection_expected_diff_approval_registry": "",
        "emit_score_breakdown": "false",
        "emit_review_report": "false",
        "emit_peak_candidates": "false",
        "keep_intermediate_csv": "false",
        "nl_rt_anchor_search_margin_min": "2.0",
        "nl_rt_anchor_half_window_min": "1.0",
        "nl_fallback_half_window_min": "2.0",
        "parallel_mode": "serial",
        "parallel_workers": "1",
    }


def test_advanced_section_collapsed_by_default(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()

    assert section.advanced_section.isVisible()
    assert not section.advanced_section._content.isVisible()


def test_advanced_section_expands_on_click(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()

    qtbot.mouseClick(section.advanced_section._toggle, Qt.MouseButton.LeftButton)

    assert section.advanced_section._content.isVisible()


def test_advanced_section_contains_required_flags(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()

    advanced_keys = set(section.advanced_section_field_keys())

    assert {
        "keep_intermediate_csv",
        "emit_score_breakdown",
        "emit_review_report",
        "emit_peak_candidates",
        "dirty_matrix_mode",
        "count_no_ms2_as_detected",
        "rolling_window_size",
        "rt_prior_library_path",
        "target_pair_rt_calibration_path",
        "injection_order_source",
        "nl_rt_anchor_search_margin_min",
        "nl_rt_anchor_half_window_min",
        "nl_fallback_half_window_min",
        "parallel_mode",
        "parallel_workers",
    } <= advanced_keys


def test_reset_button_restores_param_defaults_keeping_paths(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()
    section.load(
        {**_canonical_settings(), "data_dir": "C:\\keep", "dll_dir": "C:\\dll"}
    )
    section._ms1_morphology_smoothing_window_spin.setValue(21)

    section._reset_button.click()

    values = section.get_values()
    assert values["signal_processing_window_points"] == (
        CANONICAL_SETTINGS_DEFAULTS["signal_processing_window_points"]
    )
    assert values["data_dir"] == "C:\\keep"
    assert values["dll_dir"] == "C:\\dll"
    assert section._save_button.isVisible()  # reset marks the form dirty


def test_rt_prior_library_gui_label_marks_developer_debug(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.load(_canonical_settings())

    labels = section.findChildren(QLabel)
    text = "\n".join(label.text() for label in labels)

    assert "RT prior library" in text
    assert "developer/debug" in text


def test_target_pair_rt_calibration_gui_label_marks_shadow(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.load(_canonical_settings())

    labels = section.findChildren(QLabel)
    text = "\n".join(label.text() for label in labels)

    assert "Target pair RT calibration" in text
    assert "shadow" in text


def test_advanced_section_uses_compact_rows_for_related_controls(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()

    keep_layout, keep_index = _containing_layout(
        section._keep_intermediate_csv_checkbox
    )
    score_layout, score_index = _containing_layout(
        section._emit_score_breakdown_checkbox
    )
    report_layout, report_index = _containing_layout(
        section._emit_review_report_checkbox
    )
    candidates_layout, candidates_index = _containing_layout(
        section._emit_peak_candidates_checkbox
    )
    dirty_layout, dirty_index = _containing_layout(section._dirty_matrix_mode_checkbox)
    no_ms2_layout, no_ms2_index = _containing_layout(section._count_no_ms2_checkbox)
    mode_layout, mode_index = _containing_layout(section._parallel_mode_combo)
    workers_layout, workers_index = _containing_layout(section._parallel_workers_spin)

    assert _direct_grid_position(section._dirty_matrix_mode_checkbox) is None
    assert _direct_grid_position(section._count_no_ms2_checkbox) is None
    assert (
        keep_layout
        is score_layout
        is report_layout
        is candidates_layout
        is dirty_layout
        is no_ms2_layout
    )
    assert [
        keep_index,
        score_index,
        report_index,
        candidates_index,
        dirty_index,
        no_ms2_index,
    ] == [
        0,
        1,
        2,
        3,
        4,
        5,
    ]
    assert mode_layout is workers_layout
    assert mode_index < workers_index


def test_obsolete_peak_controls_are_not_exposed(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()
    section.load(_canonical_settings())

    assert not hasattr(section, "_smooth_window_spin")
    assert not hasattr(section, "_peak_min_prominence_ratio_spin")
    assert not hasattr(section, "_resolver_chrom_threshold_spin")


def test_obsolete_peak_settings_are_not_serialized(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()
    section.load(
        {
            **_canonical_settings(),
            "resolver_mode": "region_first_safe_merge",
            "smooth_window": "21",
            "peak_rel_height": "0.8",
            "resolver_min_absolute_height": "80",
            "baseline_integration_method": "linear_edge",
        }
    )

    assert section._ms1_morphology_smoothing_window_spin.isVisible()
    section._ms1_morphology_smoothing_window_spin.setValue(21)
    values = section.get_values()
    assert values["signal_processing_window_points"] == "21"
    assert "resolver_mode" not in values
    assert "smooth_window" not in values
    assert "peak_rel_height" not in values
    assert "resolver_min_absolute_height" not in values
    assert "baseline_integration_method" not in values


def test_advanced_section_edits_round_trip_through_get_values(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()
    section.load(_canonical_settings())

    section._keep_intermediate_csv_checkbox.setChecked(True)
    section._emit_score_breakdown_checkbox.setChecked(True)
    section._emit_review_report_checkbox.setChecked(True)
    section._emit_peak_candidates_checkbox.setChecked(True)
    section._dirty_matrix_mode_checkbox.setChecked(True)
    section._count_no_ms2_checkbox.setChecked(False)
    section._rolling_window_size_spin.setValue(9)
    section._rt_prior_library_path_edit.setText("C:\\data\\rt_prior.csv")
    section._target_pair_rt_calibration_path_edit.setText(
        "C:\\data\\target_pair_rt_calibration.tsv"
    )
    section._injection_order_source_edit.setText("C:\\data\\sample_order.csv")
    section._nl_rt_anchor_half_window_min_spin.setValue(0.75)
    section._parallel_mode_combo.setCurrentText("process")
    section._parallel_workers_spin.setValue(4)

    values = section.get_values()

    assert values["keep_intermediate_csv"] == "true"
    assert values["emit_score_breakdown"] == "true"
    assert values["emit_review_report"] == "true"
    assert values["emit_peak_candidates"] == "true"
    assert values["dirty_matrix_mode"] == "true"
    assert values["count_no_ms2_as_detected"] == "false"
    assert values["rolling_window_size"] == "9"
    assert values["rt_prior_library_path"] == "C:\\data\\rt_prior.csv"
    assert values["target_pair_rt_calibration_path"] == (
        "C:\\data\\target_pair_rt_calibration.tsv"
    )
    assert values["injection_order_source"] == "C:\\data\\sample_order.csv"
    assert values["nl_rt_anchor_half_window_min"] == "0.75"
    assert values["parallel_mode"] == "process"
    assert values["parallel_workers"] == "4"


def test_advanced_section_preserves_loaded_strings_without_edits(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()
    section.load(_canonical_settings())

    values = section.get_values()

    assert values["nl_rt_anchor_search_margin_min"] == "2.0"
    assert values["parallel_mode"] == "serial"
    assert values["parallel_workers"] == "1"


def test_parallel_defaults_match_canonical_settings_defaults(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()
    section.load(
        {
            key: value
            for key, value in _canonical_settings().items()
            if key not in {"parallel_mode", "parallel_workers"}
        }
    )

    values = section.get_values()

    assert values["parallel_mode"] == CANONICAL_SETTINGS_DEFAULTS["parallel_mode"]
    assert values["parallel_workers"] == CANONICAL_SETTINGS_DEFAULTS["parallel_workers"]


def test_parallel_loaded_values_round_trip_through_get_values(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()
    section.load(
        {
            **_canonical_settings(),
            "parallel_mode": "process",
            "parallel_workers": "4",
        }
    )

    values = section.get_values()

    assert values["parallel_mode"] == "process"
    assert values["parallel_workers"] == "4"


def _grid_position(widget) -> tuple[int, int, int, int]:
    layout = widget.parentWidget().layout()
    index = layout.indexOf(widget)
    assert index >= 0
    return layout.getItemPosition(index)


def _direct_grid_position(widget) -> tuple[int, int, int, int] | None:
    layout = widget.parentWidget().layout()
    index = layout.indexOf(widget)
    if index < 0:
        return None
    return layout.getItemPosition(index)


def _containing_layout(widget):
    layout = widget.parentWidget().layout()
    found = _find_layout_item(layout, widget)
    assert found is not None
    return found


def _find_layout_item(layout, widget):
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is widget:
            return layout, index
        nested = item.layout()
        if nested is not None:
            found = _find_layout_item(nested, widget)
            if found is not None:
                return found
    return None
