import csv
from pathlib import Path

import pytest

from xic_extractor.config import (
    ConfigError,
    ExtractionConfig,
    compute_target_config_hash,
    load_config,
    migrate_settings_dict,
)
from xic_extractor.settings_schema import (
    CANONICAL_SETTINGS_DEFAULTS,
    default_parallel_workers,
)
from xic_extractor.target_pair_rt_calibration import (
    TARGET_PAIR_RT_CALIBRATION_SCHEMA_VERSION,
    TargetPairRTCalibrationRow,
    write_target_pair_rt_calibration_tsv,
)

SETTINGS_FIELDS = ["key", "value", "description"]
TARGET_FIELDS = [
    "label",
    "mz",
    "rt_min",
    "rt_max",
    "ppm_tol",
    "neutral_loss_da",
    "nl_ppm_warn",
    "nl_ppm_max",
    "is_istd",
    "istd_pair",
]


def _settings_rows(config_dir: Path) -> dict[str, str]:
    data_dir = config_dir.parent / "raw"
    dll_dir = config_dir.parent / "dll"
    data_dir.mkdir(parents=True)
    dll_dir.mkdir(parents=True)
    return {
        "data_dir": str(data_dir),
        "dll_dir": str(dll_dir),
        "smooth_window": "15",
        "smooth_polyorder": "3",
        "peak_rel_height": "0.95",
        "peak_min_prominence_ratio": "0.10",
        "ms2_precursor_tol_da": "0.5",
        "nl_min_intensity_ratio": "0.01",
        "count_no_ms2_as_detected": "false",
    }


def _write_settings(config_dir: Path, overrides: dict[str, str] | None = None) -> None:
    rows = _settings_rows(config_dir)
    if overrides:
        rows.update(overrides)

    config_dir.mkdir(parents=True, exist_ok=True)
    with (config_dir / "settings.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=SETTINGS_FIELDS)
        writer.writeheader()
        for key, value in rows.items():
            writer.writerow({"key": key, "value": value, "description": key})


def _target_row(**overrides: str) -> dict[str, str]:
    row = {
        "label": "Analyte",
        "mz": "258.1085",
        "rt_min": "8.0",
        "rt_max": "10.0",
        "ppm_tol": "20",
        "neutral_loss_da": "116.0474",
        "nl_ppm_warn": "20",
        "nl_ppm_max": "50",
        "is_istd": "false",
        "istd_pair": "",
    }
    row.update(overrides)
    return row


def _write_targets(config_dir: Path, rows: list[dict[str, str]] | None = None) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    with (config_dir / "targets.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=TARGET_FIELDS)
        writer.writeheader()
        for row in rows or [_target_row()]:
            writer.writerow({field: row.get(field, "") for field in TARGET_FIELDS})


def _write_valid_config(config_dir: Path) -> None:
    _write_settings(config_dir)
    _write_targets(config_dir)


def _assert_error(exc_info: pytest.ExceptionInfo[ConfigError], *parts: str) -> None:
    message = str(exc_info.value)
    for part in parts:
        assert part in message


def test_load_config_derives_output_paths_and_creates_output_dir(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_valid_config(config_dir)

    config, targets = load_config(config_dir)

    assert config.output_csv == tmp_path / "output" / "xic_results.csv"
    assert config.diagnostics_csv == tmp_path / "output" / "xic_diagnostics.csv"
    assert config.output_csv.parent.exists()
    assert config.smooth_window == 15
    assert config.smooth_polyorder == 3
    assert config.signal_processing_method == "gaussian"
    assert config.signal_processing_window_points == 15
    assert config.ms1_morphology_smoothing_window_points == 15
    assert config.count_no_ms2_as_detected is False
    assert config.resolver_mode == "region_first_safe_merge"
    assert config.resolver_chrom_threshold == pytest.approx(0.05)
    assert config.resolver_min_search_range_min == pytest.approx(0.08)
    assert config.resolver_min_relative_height == pytest.approx(0.02)
    assert config.resolver_min_absolute_height == pytest.approx(25.0)
    assert config.resolver_min_ratio_top_edge == pytest.approx(1.7)
    assert config.resolver_peak_duration_min == pytest.approx(0.0)
    assert config.resolver_peak_duration_max == pytest.approx(2.0)
    assert config.resolver_min_scans == 5
    assert config.parallel_mode == "process"
    assert config.parallel_workers == default_parallel_workers()
    assert config.baseline_audit_method == ""
    assert config.emit_peak_candidates is False
    assert config.target_pair_rt_calibration_path is None
    assert config.target_config_hash == compute_target_config_hash(
        config_dir / "targets.csv"
    )
    assert targets[0].label == "Analyte"
    assert targets[0].neutral_loss_da == pytest.approx(116.0474)
    assert targets[0].isotope_label_type == "unknown"
    assert targets[0].paired_rt_relation == "none"


def test_load_config_hash_reflects_settings_overrides(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_valid_config(config_dir)
    validation_dir = tmp_path / "validation"
    validation_dir.mkdir()

    base_config, _ = load_config(config_dir)
    override_config, _ = load_config(
        config_dir,
        settings_overrides={"data_dir": str(validation_dir)},
    )

    assert override_config.data_dir == validation_dir
    assert override_config.config_hash != base_config.config_hash


def test_load_config_uses_examples_when_runtime_files_are_missing(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_valid_config(config_dir)
    (config_dir / "settings.csv").rename(config_dir / "settings.example.csv")
    (config_dir / "targets.csv").rename(config_dir / "targets.example.csv")

    config, targets = load_config(config_dir)

    assert config.data_dir == tmp_path / "raw"
    assert targets[0].label == "Analyte"
    assert config.config_hash
    assert not (config_dir / "settings.csv").exists()
    assert not (config_dir / "targets.csv").exists()


def test_load_config_missing_settings_file_raises_config_error(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_targets(config_dir)

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "settings.csv")


def test_load_config_missing_targets_file_raises_config_error(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "targets.csv")


def test_load_config_rejects_settings_missing_required_columns(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "settings.csv").write_text(
        "name,value\ndata_dir,C:\\data\n",
        encoding="utf-8-sig",
    )
    _write_targets(config_dir)

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "settings.csv", "column key")


def test_load_config_rejects_targets_missing_required_columns(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)
    (config_dir / "targets.csv").write_text(
        "label,mz,rt_min,rt_max\nAnalyte,258.1085,8.0,10.0\n",
        encoding="utf-8-sig",
    )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "targets.csv", "column ppm_tol")


def test_load_config_rejects_empty_targets_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)
    (config_dir / "targets.csv").write_text(
        ",".join(TARGET_FIELDS) + "\n",
        encoding="utf-8-sig",
    )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "targets.csv", "label")


def test_migrate_settings_dict_renames_legacy_key_and_backfills_defaults() -> None:
    migrated, warnings = migrate_settings_dict(
        {
            "data_dir": "C:/data",
            "dll_dir": "C:/dll",
            "smooth_points": "17",
        }
    )

    assert migrated["smooth_window"] == "17"
    assert "smooth_points" not in migrated
    assert migrated["smooth_polyorder"] == "3"
    assert migrated["peak_rel_height"] == "0.95"
    assert migrated["parallel_mode"] == "process"
    assert migrated["parallel_workers"] == str(default_parallel_workers())
    assert any(
        "smooth_points" in warning and "smooth_window" in warning
        for warning in warnings
    )
    assert any("smooth_polyorder" in warning for warning in warnings)


def test_migrate_settings_dict_drops_smooth_sigma_with_warning() -> None:
    migrated, warnings = migrate_settings_dict({"smooth_sigma": "3.0"})

    assert "smooth_sigma" not in migrated
    assert any("smooth_sigma" in warning for warning in warnings)


def test_load_config_accepts_local_minimum_resolver_settings(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(
        config_dir,
        {
            "resolver_mode": "local_minimum",
            "resolver_chrom_threshold": "0.08",
            "resolver_min_search_range_min": "0.06",
            "resolver_min_relative_height": "0.12",
            "resolver_min_absolute_height": "90",
            "resolver_min_ratio_top_edge": "1.8",
            "resolver_peak_duration_min": "0.04",
            "resolver_peak_duration_max": "0.80",
            "resolver_min_scans": "9",
        },
    )
    _write_targets(config_dir)

    config, _ = load_config(config_dir)

    assert config.resolver_mode == "local_minimum"
    assert config.resolver_chrom_threshold == pytest.approx(0.08)
    assert config.resolver_min_search_range_min == pytest.approx(0.06)
    assert config.resolver_min_relative_height == pytest.approx(0.12)
    assert config.resolver_min_absolute_height == pytest.approx(90.0)
    assert config.resolver_min_ratio_top_edge == pytest.approx(1.8)
    assert config.resolver_peak_duration_min == pytest.approx(0.04)
    assert config.resolver_peak_duration_max == pytest.approx(0.80)
    assert config.resolver_min_scans == 9


def test_load_config_rejects_retired_arbitrated_resolver_mode(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir, {"resolver_mode": "arbitrated"})
    _write_targets(config_dir)

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "settings.csv", "resolver_mode", "arbitrated")
    assert "retired" in str(exc_info.value)
    assert "region_first_safe_merge" in str(exc_info.value)


def test_load_config_accepts_region_first_safe_merge_resolver_mode(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir, {"resolver_mode": "region_first_safe_merge"})
    _write_targets(config_dir)

    config, _ = load_config(config_dir)

    assert config.resolver_mode == "region_first_safe_merge"


def test_load_config_migrates_ms1_morphology_smoothing_window_points(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir, {"ms1_morphology_smoothing_window_points": "21"})
    _write_targets(config_dir)

    config, _ = load_config(config_dir)

    assert config.signal_processing_method == "gaussian"
    assert config.signal_processing_window_points == 21
    assert config.ms1_morphology_smoothing_window_points == 21


def test_load_config_accepts_canonical_signal_processing_settings(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(
        config_dir,
        {
            "signal_processing_method": "gaussian",
            "signal_processing_window_points": "21",
        },
    )
    _write_targets(config_dir)

    config, _ = load_config(config_dir)

    assert config.signal_processing_method == "gaussian"
    assert config.signal_processing_window_points == 21


def test_load_config_accepts_equal_canonical_and_legacy_signal_window(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(
        config_dir,
        {
            "signal_processing_window_points": "21",
            "ms1_morphology_smoothing_window_points": "21",
        },
    )
    _write_targets(config_dir)

    config, _ = load_config(config_dir)

    assert config.signal_processing_window_points == 21
    assert "settings_migration" in caplog.text
    assert "status=deprecated_alias_equal" in caplog.text
    assert "legacy_key=ms1_morphology_smoothing_window_points" in caplog.text
    assert "canonical_key=signal_processing_window_points" in caplog.text
    assert "value=21" in caplog.text


def test_load_config_rejects_conflicting_canonical_and_legacy_signal_window(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(
        config_dir,
        {
            "signal_processing_window_points": "21",
            "ms1_morphology_smoothing_window_points": "15",
        },
    )
    _write_targets(config_dir)

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(
        exc_info,
        "signal_processing_window_points",
        "ms1_morphology_smoothing_window_points",
        "21",
        "15",
    )


def test_resolver_and_savgol_settings_do_not_alias_signal_profile(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(
        config_dir,
        {
            "smooth_window": "21",
            "smooth_polyorder": "5",
            "resolver_mode": "legacy_savgol",
        },
    )
    _write_targets(config_dir)

    config, _ = load_config(config_dir)

    assert config.smooth_window == 21
    assert config.smooth_polyorder == 5
    assert config.resolver_mode == "legacy_savgol"
    assert config.signal_processing_method == "gaussian"
    assert config.signal_processing_window_points == 15


def test_load_config_accepts_legacy_savgol_resolver_mode(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir, {"resolver_mode": "legacy_savgol"})
    _write_targets(config_dir)

    config, _ = load_config(config_dir)

    assert config.resolver_mode == "legacy_savgol"


def test_load_config_accepts_zero_local_minimum_floor_values(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(
        config_dir,
        {
            "resolver_mode": "local_minimum",
            "resolver_min_relative_height": "0.0",
            "resolver_peak_duration_min": "0.0",
            "resolver_peak_duration_max": "10.0",
        },
    )
    _write_targets(config_dir)

    config, _ = load_config(config_dir)

    assert config.resolver_min_relative_height == pytest.approx(0.0)
    assert config.resolver_peak_duration_min == pytest.approx(0.0)
    assert config.resolver_peak_duration_max == pytest.approx(10.0)


def test_load_config_accepts_process_parallel_settings(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(
        config_dir,
        {
            "parallel_mode": "process",
            "parallel_workers": "4",
        },
    )
    _write_targets(config_dir)

    config, _ = load_config(config_dir)

    assert config.parallel_mode == "process"
    assert config.parallel_workers == 4


def test_load_config_accepts_emit_peak_candidates_setting(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir, {"emit_peak_candidates": "true"})
    _write_targets(config_dir)

    config, _ = load_config(config_dir)

    assert config.emit_peak_candidates is True


def test_load_config_accepts_target_pair_rt_calibration_path(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    calibration = tmp_path / "target_pair_rt_calibration.tsv"
    _write_settings(
        config_dir,
        {"target_pair_rt_calibration_path": str(calibration)},
    )
    _write_targets(config_dir)
    _write_calibration(calibration, config_dir / "targets.csv")

    config, _ = load_config(config_dir)

    assert config.target_pair_rt_calibration_path == calibration


def test_load_config_validates_pair_rt_calibration_when_shadow_output_off(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    calibration = tmp_path / "target_pair_rt_calibration.tsv"
    _write_settings(
        config_dir,
        {
            "target_pair_rt_calibration_path": str(calibration),
            "emit_peak_candidates": "false",
        },
    )
    _write_targets(config_dir)
    calibration.write_text(
        "schema_version\ttarget_label\nv\tAnalyte\n",
        encoding="utf-8-sig",
    )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "missing required column target_config_hash")


def test_load_config_rejects_missing_target_pair_rt_calibration_file(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    calibration = tmp_path / "missing_target_pair_rt_calibration.tsv"
    _write_settings(
        config_dir,
        {"target_pair_rt_calibration_path": str(calibration)},
    )
    _write_targets(config_dir)

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "file is missing")


def test_load_config_rejects_duplicate_target_pair_rt_calibration_rows(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    calibration = tmp_path / "target_pair_rt_calibration.tsv"
    _write_settings(
        config_dir,
        {"target_pair_rt_calibration_path": str(calibration)},
    )
    _write_targets(config_dir)
    _write_calibration(calibration, config_dir / "targets.csv", duplicate=True)

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "duplicate (target_label, paired_istd_label)")


def test_load_config_parses_optional_target_pair_rt_metadata(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)
    _write_targets_with_optional_metadata(
        config_dir,
        [
            _target_row(
                label="Analyte",
                istd_pair="ISTD",
                paired_rt_relation="istd_not_later_than_pair",
            ),
            _target_row(
                label="ISTD",
                is_istd="true",
                isotope_label_type="deuterated",
            ),
        ],
    )

    _, targets = load_config(config_dir)
    by_label = {target.label: target for target in targets}

    assert by_label["Analyte"].isotope_label_type == "unknown"
    assert by_label["Analyte"].paired_rt_relation == "istd_not_later_than_pair"
    assert by_label["ISTD"].isotope_label_type == "deuterated"
    assert by_label["ISTD"].paired_rt_relation == "none"


def test_load_config_parses_target_sample_applicability(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)
    _write_targets_with_optional_metadata(
        config_dir,
        [
            _target_row(
                label="8-oxo-Guo",
                istd_pair="ISTD",
                sample_applicability="rna_containing",
            ),
            _target_row(label="ISTD", is_istd="true"),
        ],
    )

    _, targets = load_config(config_dir)
    by_label = {target.label: target for target in targets}

    assert by_label["8-oxo-Guo"].sample_applicability == "rna_containing"
    assert by_label["ISTD"].sample_applicability == "all"


def test_load_config_rejects_invalid_target_pair_rt_metadata_enum(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)
    _write_targets_with_optional_metadata(
        config_dir,
        [_target_row(isotope_label_type="regex_guess")],
    )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "targets.csv", "isotope_label_type", "regex_guess")


def test_load_config_rejects_invalid_target_sample_applicability(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)
    _write_targets_with_optional_metadata(
        config_dir,
        [_target_row(sample_applicability="dna_and_rna_only")],
    )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "targets.csv", "sample_applicability", "dna_and_rna_only")


def test_load_config_rejects_istd_relation_on_non_deuterated_pair(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)
    _write_targets_with_optional_metadata(
        config_dir,
        [
            _target_row(
                label="Analyte",
                istd_pair="ISTD",
                paired_rt_relation="istd_not_later_than_pair",
            ),
            _target_row(
                label="ISTD",
                is_istd="true",
                isotope_label_type="heavy_non_deuterium",
            ),
        ],
    )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(
        exc_info,
        "targets.csv",
        "paired_rt_relation",
        "requires paired ISTD isotope_label_type=deuterated",
    )


def test_load_config_rejects_istd_owned_paired_rt_relation(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)
    _write_targets_with_optional_metadata(
        config_dir,
        [
            _target_row(
                label="ISTD",
                is_istd="true",
                paired_rt_relation="learned_delta_only",
            )
        ],
    )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(
        exc_info,
        "targets.csv",
        "paired_rt_relation",
        "is_istd=true targets must leave paired_rt_relation blank",
    )


def test_load_config_accepts_asls_baseline_audit_method(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir, {"baseline_audit_method": "asls"})
    _write_targets(config_dir)

    config, _ = load_config(config_dir)

    assert config.baseline_audit_method == "asls"


def test_load_config_defaults_baseline_integration_method_to_asls(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir, {})
    _write_targets(config_dir)

    config, _ = load_config(config_dir)

    assert config.baseline_integration_method == "asls"


def test_load_config_rejects_retired_linear_edge_baseline_integration_method(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir, {"baseline_integration_method": "linear_edge"})
    _write_targets(config_dir)

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(
        exc_info,
        "settings.csv",
        "baseline_integration_method",
        "linear_edge",
    )
    assert "retired" in str(exc_info.value)


def test_load_config_rejects_unknown_baseline_integration_method(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir, {"baseline_integration_method": "airpls"})
    _write_targets(config_dir)

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "settings.csv", "baseline_integration_method", "airpls")


def test_load_config_rejects_unknown_baseline_audit_method(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir, {"baseline_audit_method": "airpls"})
    _write_targets(config_dir)

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "settings.csv", "baseline_audit_method", "airpls")


def test_canonical_settings_defaults_include_parallel_settings() -> None:
    assert CANONICAL_SETTINGS_DEFAULTS["parallel_mode"] == "process"
    assert CANONICAL_SETTINGS_DEFAULTS["parallel_workers"] == str(
        default_parallel_workers()
    )
    assert CANONICAL_SETTINGS_DEFAULTS["baseline_audit_method"] == ""
    assert CANONICAL_SETTINGS_DEFAULTS["baseline_integration_method"] == "asls"
    assert CANONICAL_SETTINGS_DEFAULTS["signal_processing_method"] == "gaussian"
    assert CANONICAL_SETTINGS_DEFAULTS["signal_processing_window_points"] == "15"
    assert CANONICAL_SETTINGS_DEFAULTS["ms1_morphology_smoothing_window_points"] == "15"


def test_canonical_settings_defaults_include_local_minimum_preset() -> None:
    assert CANONICAL_SETTINGS_DEFAULTS["resolver_mode"] == "region_first_safe_merge"
    assert CANONICAL_SETTINGS_DEFAULTS["resolver_chrom_threshold"] == "0.05"
    assert CANONICAL_SETTINGS_DEFAULTS["resolver_min_search_range_min"] == "0.08"
    assert CANONICAL_SETTINGS_DEFAULTS["resolver_min_relative_height"] == "0.02"
    assert CANONICAL_SETTINGS_DEFAULTS["resolver_min_absolute_height"] == "25.0"
    assert CANONICAL_SETTINGS_DEFAULTS["resolver_min_ratio_top_edge"] == "1.7"
    assert CANONICAL_SETTINGS_DEFAULTS["resolver_peak_duration_min"] == "0.0"
    assert CANONICAL_SETTINGS_DEFAULTS["resolver_peak_duration_max"] == "2.0"
    assert CANONICAL_SETTINGS_DEFAULTS["resolver_min_scans"] == "5"


def test_extraction_config_default_resolver_mode_matches_canonical_settings() -> None:
    config = ExtractionConfig(
        data_dir=Path("raw"),
        dll_dir=Path("dll"),
        output_csv=Path("output/xic_results.csv"),
        diagnostics_csv=Path("output/xic_diagnostics.csv"),
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )

    assert config.resolver_mode == CANONICAL_SETTINGS_DEFAULTS["resolver_mode"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("signal_processing_method", "asls"),
        ("signal_processing_window_points", True),
        ("signal_processing_window_points", 2),
        ("signal_processing_window_points", 1000),
    ],
)
def test_extraction_config_rejects_invalid_signal_processing_profile_fields(
    field: str,
    value: object,
) -> None:
    kwargs = {
        "data_dir": Path("raw"),
        "dll_dir": Path("dll"),
        "output_csv": Path("output/xic_results.csv"),
        "diagnostics_csv": Path("output/xic_diagnostics.csv"),
        "smooth_window": 15,
        "smooth_polyorder": 3,
        "peak_rel_height": 0.95,
        "peak_min_prominence_ratio": 0.10,
        "ms2_precursor_tol_da": 0.5,
        "nl_min_intensity_ratio": 0.01,
        field: value,
    }

    with pytest.raises((TypeError, ValueError)):
        ExtractionConfig(**kwargs)


def test_settings_example_omits_machine_specific_worker_count() -> None:
    example_path = Path("config/settings.example.csv")

    with example_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = {row["key"]: row["value"] for row in csv.DictReader(handle)}

    assert rows["parallel_mode"] == "process"
    assert "parallel_workers" not in rows
    assert rows["baseline_audit_method"] == ""
    assert rows["baseline_integration_method"] == "asls"
    assert rows["ms1_morphology_smoothing_window_points"] == "15"


def test_settings_example_includes_review_report_setting() -> None:
    example_path = Path("config/settings.example.csv")

    with example_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = {row["key"]: row["value"] for row in csv.DictReader(handle)}

    assert rows["emit_review_report"] == "false"


def test_settings_example_includes_peak_candidates_setting() -> None:
    example_path = Path("config/settings.example.csv")

    with example_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = {row["key"]: row["value"] for row in csv.DictReader(handle)}

    assert rows["emit_peak_candidates"] == "false"


def test_settings_example_includes_target_pair_rt_calibration_setting() -> None:
    example_path = Path("config/settings.example.csv")

    with example_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = {row["key"]: row["value"] for row in csv.DictReader(handle)}

    assert rows["target_pair_rt_calibration_path"] == ""


def test_targets_example_includes_optional_target_pair_rt_metadata() -> None:
    example_path = Path("config/targets.example.csv")

    with example_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = {row["label"]: row for row in csv.DictReader(handle)}

    assert rows["d3-5-medC"]["isotope_label_type"] == "deuterated"
    assert rows["5-medC"]["paired_rt_relation"] == "istd_not_later_than_pair"
    assert rows["15N5-8-oxodG"]["isotope_label_type"] == "heavy_non_deuterium"
    assert rows["8-oxodG"]["paired_rt_relation"] == "learned_delta_only"
    assert rows["8-oxo-Guo"]["sample_applicability"] == "rna_containing"


def test_settings_example_includes_local_minimum_preset() -> None:
    example_path = Path("config/settings.example.csv")

    with example_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = {row["key"]: row["value"] for row in csv.DictReader(handle)}

    assert rows["resolver_chrom_threshold"] == "0.05"
    assert rows["resolver_min_search_range_min"] == "0.08"
    assert rows["resolver_min_relative_height"] == "0.02"
    assert rows["resolver_min_absolute_height"] == "25.0"
    assert rows["resolver_min_ratio_top_edge"] == "1.7"
    assert rows["resolver_peak_duration_min"] == "0.0"
    assert rows["resolver_peak_duration_max"] == "2.0"
    assert rows["resolver_min_scans"] == "5"


def test_settings_example_documents_region_first_safe_merge_mode() -> None:
    example_path = Path("config/settings.example.csv")

    with example_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = {row["key"]: row for row in csv.DictReader(handle)}

    resolver_row = rows["resolver_mode"]
    assert resolver_row["value"] == "region_first_safe_merge"
    assert "region_first_safe_merge" in resolver_row["description"]


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("smooth_window", "14"),
        ("smooth_polyorder", "15"),
        ("signal_processing_method", "asls"),
        ("signal_processing_window_points", "14"),
        ("signal_processing_window_points", "1"),
        ("signal_processing_window_points", "1001"),
        ("signal_processing_window_points", "true"),
        ("ms1_morphology_smoothing_window_points", "14"),
        ("ms1_morphology_smoothing_window_points", "1"),
        ("peak_rel_height", "0.49"),
        ("peak_rel_height", "1.00"),
        ("peak_min_prominence_ratio", "0.00"),
        ("peak_min_prominence_ratio", "0.51"),
        ("ms2_precursor_tol_da", "0"),
        ("nl_min_intensity_ratio", "0"),
        ("nl_min_intensity_ratio", "1.1"),
        ("nl_rt_anchor_search_margin_min", "0"),
        ("nl_rt_anchor_search_margin_min", "-0.1"),
        ("nl_rt_anchor_half_window_min", "0"),
        ("nl_rt_anchor_half_window_min", "-0.1"),
        ("nl_fallback_half_window_min", "0"),
        ("nl_fallback_half_window_min", "-0.1"),
        ("rolling_window_size", "0"),
        ("rolling_window_size", "-1"),
        ("resolver_chrom_threshold", "-0.01"),
        ("resolver_chrom_threshold", "1.01"),
        ("resolver_min_search_range_min", "0"),
        ("resolver_min_relative_height", "-0.01"),
        ("resolver_min_relative_height", "1.1"),
        ("resolver_min_absolute_height", "-1"),
        ("resolver_min_ratio_top_edge", "1.0"),
        ("resolver_peak_duration_min", "-0.01"),
        ("resolver_peak_duration_max", "0"),
        ("resolver_min_scans", "0"),
        ("parallel_workers", "0"),
        ("parallel_workers", "-1"),
    ],
)
def test_load_config_rejects_invalid_settings(
    tmp_path: Path, column: str, value: str
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir, {column: value})
    _write_targets(config_dir)

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "settings.csv", column, value)


def test_load_config_rejects_unknown_resolver_mode(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir, {"resolver_mode": "wavelet"})
    _write_targets(config_dir)

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "settings.csv", "resolver_mode", "wavelet")


def test_load_config_rejects_unknown_parallel_mode(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir, {"parallel_mode": "thread"})
    _write_targets(config_dir)

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "settings.csv", "parallel_mode", "thread")


def _write_targets_with_optional_metadata(
    config_dir: Path,
    rows: list[dict[str, str]],
) -> None:
    fieldnames = [
        *TARGET_FIELDS,
        "isotope_label_type",
        "paired_rt_relation",
        "sample_applicability",
    ]
    config_dir.mkdir(parents=True, exist_ok=True)
    with (config_dir / "targets.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _write_calibration(
    path: Path,
    targets_path: Path,
    *,
    duplicate: bool = False,
) -> None:
    row = TargetPairRTCalibrationRow(
        schema_version=TARGET_PAIR_RT_CALIBRATION_SCHEMA_VERSION,
        target_config_hash=compute_target_config_hash(targets_path),
        source_artifact="mixstds.tsv",
        source_hash="sourcehash",
        source_hash_status="present",
        target_label="Analyte",
        paired_istd_label="ISTD",
        pair_rt_delta_min=0.25,
        delta_source="mixstds_clean_standard",
        point_count=6,
        rt_delta_median_min=0.25,
        rt_delta_mad_min=0.02,
        rt_delta_direction="target_later",
        isotope_label_type="deuterated",
        paired_rt_relation="istd_not_later_than_pair",
        calibration_status="usable",
        calibration_level="clean_standard_only",
        product_transfer_status="not_assessed",
    )
    rows = [row, row] if duplicate else [row]
    write_target_pair_rt_calibration_tsv(path, rows)


def test_load_config_rejects_peak_duration_min_above_max(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(
        config_dir,
        {
            "resolver_peak_duration_min": "0.40",
            "resolver_peak_duration_max": "0.20",
        },
    )
    _write_targets(config_dir)

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "settings.csv", "resolver_peak_duration_min", "0.40")


def test_load_config_rejects_duplicate_target_labels(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)
    _write_targets(config_dir, [_target_row(), _target_row()])

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "targets.csv", "row 3", "label", "Analyte")


def test_load_config_rejects_istd_pair_that_is_not_an_istd(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)
    _write_targets(
        config_dir,
        [
            _target_row(label="Analyte", istd_pair="Candidate"),
            _target_row(label="Candidate", is_istd="false"),
        ],
    )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "targets.csv", "row 2", "istd_pair", "Candidate")


def test_load_config_rejects_istd_with_istd_pair(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)
    _write_targets(
        config_dir,
        [
            _target_row(label="ISTD-A", is_istd="true", istd_pair="ISTD-B"),
            _target_row(label="ISTD-B", is_istd="true"),
        ],
    )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "targets.csv", "row 2", "istd_pair", "ISTD-B")


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("neutral_loss_da", "0"),
        ("neutral_loss_da", "300"),
        ("nl_ppm_warn", ""),
        ("nl_ppm_warn", "0"),
        ("nl_ppm_max", ""),
        ("nl_ppm_max", "0"),
    ],
)
def test_load_config_rejects_invalid_neutral_loss_fields(
    tmp_path: Path, column: str, value: str
) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)
    _write_targets(config_dir, [_target_row(**{column: value})])

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "targets.csv", "row 2", column, value)


def test_load_config_rejects_warn_threshold_above_max(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)
    _write_targets(config_dir, [_target_row(nl_ppm_warn="60", nl_ppm_max="50")])

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_dir)

    _assert_error(exc_info, "targets.csv", "row 2", "nl_ppm_warn", "60")


def test_load_config_allows_no_nl_target_with_empty_thresholds(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_settings(config_dir)
    _write_targets(
        config_dir,
        [
            _target_row(
                neutral_loss_da="",
                nl_ppm_warn="",
                nl_ppm_max="",
            )
        ],
    )

    _, targets = load_config(config_dir)

    assert targets[0].neutral_loss_da is None
    assert targets[0].nl_ppm_warn is None
    assert targets[0].nl_ppm_max is None


def test_untargeted_list_selects_only_istds_from_shared_format():
    from xic_extractor.configuration.targets import read_istd_targets, read_targets

    source = Path(__file__).parents[1] / "config" / "targets.example.csv"
    all_targets = read_targets(source)
    selection = read_istd_targets(source)
    assert selection.targets == tuple(t for t in all_targets if t.is_istd)
    assert selection.targets
    assert selection.skipped_count == len(all_targets) - len(selection.targets)
    assert selection.skipped_count > 0


def test_untargeted_list_with_no_istd_is_actionable(tmp_path):
    from xic_extractor.configuration.targets import read_istd_targets

    source = tmp_path / "targets.csv"
    source.write_text(
        ",".join(TARGET_FIELDS) + "\nAnalyte,100,1,2,5,,,,false,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="is_istd=TRUE"):
        read_istd_targets(source)
