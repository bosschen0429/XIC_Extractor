from gui.sections.settings_section import SettingsSection
from xic_extractor.configuration.settings import migrate_settings_dict


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
        "targeted_ms1_shape_identity_support_tsv": "",
        "targeted_ms1_shape_identity_activation_policy": "limited_5hmdc_5medc",
        "emit_score_breakdown": "false",
        "emit_review_report": "false",
        "baseline_audit_method": "",
        "keep_intermediate_csv": "false",
        "nl_rt_anchor_search_margin_min": "2.0",
        "nl_rt_anchor_half_window_min": "1.0",
        "nl_fallback_half_window_min": "2.0",
        "parallel_mode": "serial",
        "parallel_workers": "1",
    }


def test_settings_section_saves_canonical_keys_after_canonical_load(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    migrated = section.load(_canonical_settings())

    values = section.get_values()

    assert migrated is False
    assert values["signal_processing_method"] == "gaussian"
    assert values["signal_processing_window_points"] == "15"
    assert "ms1_morphology_smoothing_window_points" not in values
    assert values["ms2_precursor_tol_da"] == "0.4"
    assert values["nl_min_intensity_ratio"] == "0.02"
    assert values["count_no_ms2_as_detected"] == "true"
    assert values["parallel_mode"] == "serial"
    assert values["parallel_workers"] == "1"
    assert values["emit_review_report"] == "false"
    assert "smooth_points" not in values
    assert "smooth_sigma" not in values


def test_settings_section_drops_obsolete_peak_keys_on_save(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    migrated = section.load(
        {
            **_canonical_settings(),
            "smooth_points": "19",
            "smooth_sigma": "3.0",
            "smooth_window": "21",
            "smooth_polyorder": "5",
            "peak_rel_height": "0.8",
            "peak_min_prominence_ratio": "0.2",
            "resolver_min_scans": "9",
            "baseline_integration_method": "linear_edge",
        }
    )

    values = section.get_values()

    assert migrated is False
    assert values["signal_processing_method"] == "gaussian"
    assert values["signal_processing_window_points"] == "15"
    assert "ms1_morphology_smoothing_window_points" not in values
    assert "smooth_points" not in values
    assert "smooth_sigma" not in values
    assert "smooth_window" not in values
    assert "smooth_polyorder" not in values
    assert "peak_rel_height" not in values
    assert "peak_min_prominence_ratio" not in values
    assert "resolver_min_scans" not in values
    assert "baseline_integration_method" not in values


def test_settings_section_exposes_new_processing_controls(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.load(_canonical_settings())

    section._ms2_precursor_tol_da_spin.setValue(0.7)
    section._nl_min_intensity_ratio_spin.setValue(0.03)
    section._ms1_morphology_smoothing_window_spin.setValue(21)
    section._count_no_ms2_checkbox.setChecked(False)

    values = section.get_values()

    assert values["ms2_precursor_tol_da"] == "0.7"
    assert values["nl_min_intensity_ratio"] == "0.03"
    assert values["signal_processing_method"] == "gaussian"
    assert values["signal_processing_window_points"] == "21"
    assert "ms1_morphology_smoothing_window_points" not in values
    assert values["count_no_ms2_as_detected"] == "false"


def test_settings_section_round_trips_nondefault_canonical_signal_window(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.load(_canonical_settings())
    section._ms1_morphology_smoothing_window_spin.setValue(21)

    saved = section.get_values()
    migrated, warnings = migrate_settings_dict(saved)
    reloaded = SettingsSection()
    qtbot.addWidget(reloaded)
    reload_migrated = reloaded.load(migrated)

    assert not any("deprecated_alias" in warning for warning in warnings)
    assert migrated["signal_processing_window_points"] == "21"
    assert "ms1_morphology_smoothing_window_points" not in migrated
    assert reload_migrated is False
    assert reloaded.get_values()["signal_processing_window_points"] == "21"


def test_settings_section_round_trips_default_canonical_signal_window(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.load(_canonical_settings())

    saved = section.get_values()
    migrated, warnings = migrate_settings_dict(saved)
    reloaded = SettingsSection()
    qtbot.addWidget(reloaded)
    reload_migrated = reloaded.load(migrated)

    assert not any("deprecated_alias" in warning for warning in warnings)
    assert migrated["signal_processing_window_points"] == "15"
    assert "ms1_morphology_smoothing_window_points" not in migrated
    assert reload_migrated is False
    assert reloaded.get_values()["signal_processing_window_points"] == "15"


def test_settings_section_migrates_legacy_signal_window_one_way(qtbot) -> None:
    legacy_settings = _canonical_settings()
    legacy_settings.pop("signal_processing_method")
    legacy_settings.pop("signal_processing_window_points")
    legacy_settings["ms1_morphology_smoothing_window_points"] = "21"
    section = SettingsSection()
    qtbot.addWidget(section)

    migrated = section.load(legacy_settings)
    values = section.get_values()

    assert migrated is True
    assert values["signal_processing_method"] == "gaussian"
    assert values["signal_processing_window_points"] == "21"
    assert "ms1_morphology_smoothing_window_points" not in values


def test_settings_section_numeric_rules_do_not_require_existing_paths(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.load(
        {
            **_canonical_settings(),
            "data_dir": "C:\\missing-data",
            "dll_dir": "C:\\missing-dll",
        }
    )

    assert section.is_valid()

    section._ms1_morphology_smoothing_window_spin.setValue(4)
    assert not section.is_valid()


def test_settings_section_rejects_invalid_loaded_parallel_settings(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)

    section.load(
        {
            **_canonical_settings(),
            "parallel_mode": "proces",
            "parallel_workers": "0",
        }
    )

    assert not section.is_valid()
    values = section.get_values()
    assert values["parallel_mode"] == "proces"
    assert values["parallel_workers"] == "0"


def test_settings_section_load_keeps_save_button_hidden(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()

    section.load(_canonical_settings())

    assert not section._save_button.isVisible()


def test_settings_section_editing_main_fields_marks_dirty(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()
    section.load(_canonical_settings())

    section._data_dir_edit.setText("C:\\new-data")

    assert section._save_button.isVisible()

    section.load(_canonical_settings())
    section._ms1_morphology_smoothing_window_spin.setValue(21)

    assert section._save_button.isVisible()


def test_settings_section_compacts_configured_dll_without_changing_values(
    qtbot,
) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.load(_canonical_settings())
    before = section.get_values()

    assert not section.environment_section.is_expanded()
    section.environment_section.set_expanded(True)
    section.environment_section.set_expanded(False)

    assert section.get_values() == before

    section.load({**before, "dll_dir": ""})
    assert section.environment_section.is_expanded()


def test_settings_section_changed_tracks_edits_but_not_load(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    changes: list[bool] = []
    section.changed.connect(lambda: changes.append(True))

    section.load(_canonical_settings())
    assert changes == []

    section._data_dir_edit.setText("C:\\new-data")
    assert changes == [True]


def test_settings_section_prepares_next_batch_once_and_preserves_method_context(
    qtbot,
) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.load(_canonical_settings())
    before = section.get_values()
    requests: list[bool] = []
    section.new_batch_requested.connect(lambda: requests.append(True))

    section._new_batch_button.click()

    assert requests == [True]
    assert section.get_values() == before

    changes: list[bool] = []
    section.changed.connect(lambda: changes.append(True))
    section.new_batch_requested.connect(section.prepare_next_batch)
    section._new_batch_button.click()
    after = section.get_values()

    assert requests == [True, True]
    assert changes == [True]
    assert after["data_dir"] == ""
    assert {key: value for key, value in after.items() if key != "data_dir"} == {
        key: value for key, value in before.items() if key != "data_dir"
    }


def test_settings_section_save_emits_signal_and_clears_dirty(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()
    section.load(_canonical_settings())
    section._data_dir_edit.setText("C:\\new-data")
    emitted: list[bool] = []
    section.settings_saved.connect(lambda: emitted.append(True))

    section._save_button.click()

    assert emitted == [True]
    assert not section._save_button.isVisible()


def test_settings_section_advanced_flag_change_marks_dirty(qtbot) -> None:
    section = SettingsSection()
    qtbot.addWidget(section)
    section.show()
    section.load(_canonical_settings())

    section._keep_intermediate_csv_checkbox.setChecked(True)

    assert section._save_button.isVisible()
