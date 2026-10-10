"""Settings policy oracles: method scope, local preservation and fail-closed I/O."""

from __future__ import annotations

import copy
import csv
import json

import pytest

from xic_extractor.configuration.csv_io import _read_settings
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.targeted_settings import (
    METHOD_KEYS,
    RESOURCE_KEYS,
    apply_targeted_method_snapshot,
    export_targeted_method_snapshot,
    load_targeted_method_snapshot,
    reset_targeted_method,
    resolve_targeted_settings,
    save_targeted_method_snapshot,
    targeted_method_summary,
    targeted_setting_fields,
)
from xic_workbench.config_io import read_resolved_settings, write_settings

EXPECTED_METHOD_KEYS = {
    "signal_processing_method",
    "signal_processing_window_points",
    "ms2_precursor_tol_da",
    "nl_min_intensity_ratio",
    "count_no_ms2_as_detected",
    "nl_rt_anchor_search_margin_min",
    "nl_rt_anchor_half_window_min",
    "nl_fallback_half_window_min",
    "rolling_window_size",
    "dirty_matrix_mode",
    "targeted_ms1_shape_identity_activation_policy",
}


def test_fields_and_snapshot_have_the_same_complete_method_scope():
    fields = targeted_setting_fields()
    by_key = {field["key"]: field for field in fields}
    assert len(by_key) == len(fields)
    assert {
        field["key"] for field in fields if field["scope"] == "method"
    } == EXPECTED_METHOD_KEYS
    assert set(METHOD_KEYS) == EXPECTED_METHOD_KEYS
    assert set(RESOURCE_KEYS) == {
        "rt_prior_library_path",
        "target_pair_rt_calibration_path",
        "model_selection_expected_diff_approval_registry",
        "targeted_ms1_shape_identity_support_tsv",
    }
    assert by_key["data_dir"]["scope"] == "batch"
    assert by_key["injection_order_source"]["scope"] == "batch"
    assert by_key["dll_dir"]["scope"] == "environment"
    assert by_key["parallel_mode"]["scope"] == "runtime"
    assert by_key["emit_review_report"]["scope"] == "output"
    assert by_key["count_no_ms2_as_detected"]["compatibility_only"]
    assert by_key["count_no_ms2_as_detected"]["kind"] == "readonly"
    assert "ms1_morphology_smoothing_window_points" not in by_key


def test_summary_counts_method_parameters_not_local_controls_or_resources():
    resolved = resolve_targeted_settings(
        {
            "data_dir": "missing-input",
            "dll_dir": "missing-dll",
            "injection_order_source": "order.csv",
            "parallel_mode": "serial",
            "parallel_workers": "1",
            "emit_review_report": "true",
            "rt_prior_library_path": "prior.csv",
            "signal_processing_window_points": "015",
        }
    )
    summary = targeted_method_summary(resolved)
    assert summary["label"] == "標準參數"
    assert summary["custom_keys"] == []
    assert summary["resource_keys"] == ["rt_prior_library_path"]
    summary = targeted_method_summary(
        {**resolved.values, "dirty_matrix_mode": "true", "ms2_precursor_tol_da": "1.2"}
    )
    assert summary["custom_keys"] == ["ms2_precursor_tol_da", "dirty_matrix_mode"]
    assert summary["differences"] == [
        {"key": "ms2_precursor_tol_da", "default": "0.8", "value": "1.2"},
        {"key": "dirty_matrix_mode", "default": "false", "value": "true"},
    ]


def test_resolution_explains_alias_defaults_and_explicit_override_without_input_io():
    resolved = resolve_targeted_settings(
        {
            "data_dir": "does-not-exist",
            "ms1_morphology_smoothing_window_points": "21",
            "retired_thing": "keep me",
        },
        {"ms2_precursor_tol_da": "1.1"},
    )
    assert resolved.values["signal_processing_window_points"] == "21"
    assert resolved.values["ms2_precursor_tol_da"] == "1.1"
    assert resolved.values["retired_thing"] == "keep me"
    assert resolved.provenance["signal_processing_window_points"] == "legacy_alias"
    assert resolved.provenance["ms2_precursor_tol_da"] == "override"
    assert resolved.provenance["nl_min_intensity_ratio"] == "default"
    assert resolved.provenance["retired_thing"] == "preserved"
    assert any("retired_thing" in warning for warning in resolved.warnings)
    assert "ms1_morphology_smoothing_window_points" not in resolved.values
    assert resolved.parsed.signal_processing_window_points == 21


def test_launch_overlay_retains_persisted_provenance_and_warning_receipts():
    original = resolve_targeted_settings(
        {"ms1_morphology_smoothing_window_points": "21"}, source="settings.example.csv"
    )
    overlay = resolve_targeted_settings(
        original, {"dll_dir": "launch-dll"}, override_source="launch"
    )
    assert overlay.provenance["signal_processing_window_points"] == "legacy_alias"
    assert overlay.provenance["nl_min_intensity_ratio"] == "default"
    assert overlay.provenance["dll_dir"] == "launch"
    assert overlay.source == "settings.example.csv"
    assert set(original.warnings) <= set(overlay.warnings)
    assert any("override_source=launch" in receipt for receipt in overlay.receipts)


def test_snapshot_overrides_legacy_window_the_same_way_in_runtime_and_workbench():
    raw = {"ms1_morphology_smoothing_window_points": "21"}
    snapshot = export_targeted_method_snapshot(
        {"signal_processing_window_points": "17"}
    )
    runtime = resolve_targeted_settings(raw, snapshot["settings"])
    workbench = apply_targeted_method_snapshot(raw, snapshot)
    assert runtime.values == workbench
    assert runtime.parsed.signal_processing_window_points == 17
    assert runtime.provenance["signal_processing_window_points"] == "override"
    assert (
        resolve_targeted_settings(
            {"signal_processing_window_points": "17"},
            {"ms1_morphology_smoothing_window_points": "21"},
        ).parsed.signal_processing_window_points
        == 21
    )
    with pytest.raises(ConfigError, match="conflicting settings"):
        resolve_targeted_settings(
            {"signal_processing_window_points": "15", **raw}, snapshot["settings"]
        )


def test_compatibility_summary_does_not_change_configured_scoring_setting():
    resolved = resolve_targeted_settings({"count_no_ms2_as_detected": "true"})
    summary = targeted_method_summary(resolved)
    compatibility = summary["compatibility"]["count_no_ms2_as_detected"]
    assert compatibility["configured"] == "true"
    assert "NO_MS2" in compatibility["projection_effect"]
    assert "effective" not in compatibility
    assert resolved.parsed.count_no_ms2_as_detected is True
    assert resolved.values["count_no_ms2_as_detected"] == "true"


def test_reset_and_snapshot_import_preserve_resources_and_local_controls():
    values = resolve_targeted_settings(
        {
            "dirty_matrix_mode": "true",
            "signal_processing_window_points": "21",
            "data_dir": "local-data",
            "dll_dir": "local-dll",
            "parallel_workers": "1",
            "emit_review_report": "true",
            "retired_setting": "old-value",
            **{key: f"local-{key}" for key in RESOURCE_KEYS},
        }
    ).values
    reset = reset_targeted_method(values)
    assert reset["dirty_matrix_mode"] == "false"
    assert reset["signal_processing_window_points"] == "15"
    assert {key: value for key, value in reset.items() if key not in METHOD_KEYS} == {
        key: value for key, value in values.items() if key not in METHOD_KEYS
    }
    snapshot = export_targeted_method_snapshot(
        values, name="Method A", description="custom scalars"
    )
    assert set(snapshot) == {"version", "name", "description", "settings"}
    assert set(snapshot["settings"]) == EXPECTED_METHOD_KEYS
    imported = apply_targeted_method_snapshot(reset, snapshot)
    assert imported == values
    assert values["dirty_matrix_mode"] == "true"


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.update(version=2),
        lambda s: s.update(version=True),
        lambda s: s.update(name=[]),
        lambda s: s["settings"].pop("dirty_matrix_mode"),
        lambda s: s["settings"].update(data_dir="foreign-directory"),
        lambda s: s["settings"].update(ms2_precursor_tol_da=float("nan")),
        lambda s: s["settings"].update(ms2_precursor_tol_da=0),
        lambda s: s["settings"].update(nl_min_intensity_ratio=1.1),
        lambda s: s["settings"].update(signal_processing_window_points=16),
        lambda s: s["settings"].update(rolling_window_size=[]),
        lambda s: s["settings"].update(dirty_matrix_mode=""),
        lambda s: s["settings"].update(
            targeted_ms1_shape_identity_activation_policy="unknown"
        ),
    ],
)
def test_invalid_snapshot_does_not_mutate_current_settings(change):
    current = resolve_targeted_settings(
        {"data_dir": "local", "dirty_matrix_mode": "true"}
    ).values
    original = dict(current)
    snapshot = export_targeted_method_snapshot({})
    change(snapshot)
    before = copy.deepcopy(snapshot)
    with pytest.raises(ConfigError):
        apply_targeted_method_snapshot(current, snapshot)
    assert current == original
    assert snapshot == before


def test_snapshot_file_round_trip_and_normalized_native_scalars(tmp_path):
    path = tmp_path / "method.json"
    save_targeted_method_snapshot({"dirty_matrix_mode": "true"}, path, name="Method A")
    snapshot = load_targeted_method_snapshot(path)
    assert snapshot["name"] == "Method A"
    assert snapshot["settings"]["dirty_matrix_mode"] == "true"
    native = snapshot
    native["settings"]["dirty_matrix_mode"] = True
    native["settings"]["rolling_window_size"] = 7
    path.write_text(json.dumps(native), encoding="utf-8")
    loaded = load_targeted_method_snapshot(path)
    assert loaded["settings"]["dirty_matrix_mode"] == "true"
    assert loaded["settings"]["rolling_window_size"] == "7"


def test_snapshot_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / "method.json"
    path.write_text('{"version": 1, "version": 1}', encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate key"):
        load_targeted_method_snapshot(path)


def test_method_export_cannot_overwrite_active_csv(tmp_path):
    path = tmp_path / "settings.csv"
    path.write_text("key,value\ndirty_matrix_mode,false\n", encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(ConfigError, match=".json"):
        save_targeted_method_snapshot({}, path)
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "body",
    [
        "signal_processing_window_points,15\nsignal_processing_window_points,21\n",
        "signal_processing_window_points,15\nms1_morphology_smoothing_window_points,21\n",
    ],
)
def test_csv_conflicts_are_rejected_in_ui_and_runtime_reader(tmp_path, body):
    path = tmp_path / "settings.csv"
    path.write_text("key,value\n" + body, encoding="utf-8")
    with pytest.raises(ConfigError, match="conflict"):
        read_resolved_settings(config_dir=tmp_path)
    if "ms1_morphology" not in body:
        with pytest.raises(ConfigError, match="conflict"):
            _read_settings(path)


def test_equal_csv_aliases_and_duplicates_have_one_canonical_written_row(tmp_path):
    path = tmp_path / "settings.csv"
    path.write_text(
        "key,value,description\n"
        "signal_processing_window_points,15,canonical\n"
        "signal_processing_window_points,15,duplicate\n"
        "ms1_morphology_smoothing_window_points,015,legacy\n"
        "retired_setting,keep,old description\n",
        encoding="utf-8",
    )
    original = path.read_bytes()
    write_settings({"dirty_matrix_mode": "true"}, config_dir=tmp_path)
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    assert sum(row["key"] == "signal_processing_window_points" for row in rows) == 1
    assert all(row["key"] != "ms1_morphology_smoothing_window_points" for row in rows)
    assert next(row for row in rows if row["key"] == "retired_setting") == {
        "key": "retired_setting",
        "value": "keep",
        "description": "old description",
    }
    assert path.with_suffix(".csv.bak").read_bytes() == original


def test_save_merges_partial_values_and_preserves_existing_runtime_and_resources(
    tmp_path,
):
    write_settings(
        {
            "data_dir": "local",
            "parallel_workers": "1",
            "rt_prior_library_path": "prior.csv",
        },
        config_dir=tmp_path,
    )
    write_settings({"dirty_matrix_mode": "true"}, config_dir=tmp_path)
    values = read_resolved_settings(config_dir=tmp_path).values
    assert values["data_dir"] == "local"
    assert values["parallel_workers"] == "1"
    assert values["rt_prior_library_path"] == "prior.csv"
    assert values["dirty_matrix_mode"] == "true"


def test_failed_atomic_replace_preserves_active_settings_and_cleans_temporary(
    tmp_path, monkeypatch
):
    write_settings({"dirty_matrix_mode": "false"}, config_dir=tmp_path)
    path = tmp_path / "settings.csv"
    before = path.read_bytes()

    def failed_replace(*_args):
        raise OSError("simulated interrupted save")

    monkeypatch.setattr("xic_workbench.config_io.os.replace", failed_replace)
    with pytest.raises(OSError, match="interrupted"):
        write_settings({"dirty_matrix_mode": "true"}, config_dir=tmp_path)
    assert path.read_bytes() == before
    assert list(tmp_path.glob(".settings-*.tmp")) == []


def test_invalid_save_preserves_active_file_and_invalid_import_source(tmp_path):
    write_settings({}, config_dir=tmp_path)
    path = tmp_path / "settings.csv"
    before = path.read_bytes()
    with pytest.raises(ConfigError):
        write_settings({"signal_processing_window_points": "16"}, config_dir=tmp_path)
    assert path.read_bytes() == before


def test_example_fallback_is_explicit_and_does_not_create_active_settings(tmp_path):
    example = tmp_path / "settings.example.csv"
    example.write_text(
        "key,value\nsignal_processing_window_points,15\n", encoding="utf-8"
    )
    resolved = read_resolved_settings(config_dir=tmp_path)
    assert resolved.source == str(example)
    assert any("not active until saved" in warning for warning in resolved.warnings)
    assert not (tmp_path / "settings.csv").exists()


def test_retired_resolver_setting_round_trips_without_changing_its_value(tmp_path):
    write_settings({"resolver_mode": "legacy_savgol"}, config_dir=tmp_path)
    write_settings({"dirty_matrix_mode": "true"}, config_dir=tmp_path)
    resolved = read_resolved_settings(config_dir=tmp_path)
    assert resolved.values["resolver_mode"] == "legacy_savgol"
    assert any(
        "resolver_mode" in warning and "not consumed" in warning
        for warning in resolved.warnings
    )


def test_disabled_optional_resources_are_not_reported_as_missing_settings():
    from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS

    values = {
        key: value
        for key, value in CANONICAL_SETTINGS_DEFAULTS.items()
        if key != "ms1_morphology_smoothing_window_points"
    }
    assert resolve_targeted_settings(values).warnings == ()
