import csv
from pathlib import Path

from xic_extractor.configuration.hashing import (
    compute_config_hash,
    compute_target_config_hash,
)
from xic_extractor.configuration.loader import load_config
from xic_extractor.settings_schema import (
    CANONICAL_SETTINGS_DEFAULTS,
    CANONICAL_SETTINGS_DESCRIPTIONS,
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


def test_new_keys_present() -> None:
    for key, default in {
        "injection_order_source": "",
        "rolling_window_size": "5",
        "dirty_matrix_mode": "false",
    }.items():
        assert CANONICAL_SETTINGS_DEFAULTS[key] == default


def test_new_settings_are_in_canonical_defaults_and_descriptions() -> None:
    for key in (
        "injection_order_source",
        "rolling_window_size",
        "dirty_matrix_mode",
    ):
        assert CANONICAL_SETTINGS_DESCRIPTIONS[key]
    assert "fallback" in CANONICAL_SETTINGS_DESCRIPTIONS["injection_order_source"]
    assert "RAW mtime" in CANONICAL_SETTINGS_DESCRIPTIONS["injection_order_source"]


def test_load_config_parses_scoring_settings(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    data_dir.mkdir()
    dll_dir.mkdir()
    injection_order = tmp_path / "sample_info.csv"
    rows = {
        "data_dir": str(data_dir),
        "dll_dir": str(dll_dir),
        "smooth_window": "15",
        "smooth_polyorder": "3",
        "signal_processing_window_points": "21",
        "peak_rel_height": "0.95",
        "peak_min_prominence_ratio": "0.10",
        "ms2_precursor_tol_da": "0.5",
        "nl_min_intensity_ratio": "0.01",
        "count_no_ms2_as_detected": "false",
        "injection_order_source": str(injection_order),
        "rolling_window_size": "7",
        "dirty_matrix_mode": "true",
    }

    config_dir.mkdir()
    settings_path = config_dir / "settings.csv"
    with settings_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=SETTINGS_FIELDS)
        writer.writeheader()
        for key, value in rows.items():
            writer.writerow({"key": key, "value": value, "description": key})

    targets_path = config_dir / "targets.csv"
    with targets_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=TARGET_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
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
        )

    config, _ = load_config(config_dir)

    assert config.injection_order_source == injection_order
    assert config.rolling_window_size == 7
    assert config.dirty_matrix_mode is True
    assert config.signal_processing_window_points == 21
    assert config.config_hash == compute_config_hash(targets_path, settings_path)
    assert config.target_config_hash == compute_target_config_hash(targets_path)


def test_load_config_defaults_scoring_settings_for_legacy_settings_csv(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    data_dir.mkdir()
    dll_dir.mkdir()
    rows = {
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

    config_dir.mkdir()
    with (config_dir / "settings.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=SETTINGS_FIELDS)
        writer.writeheader()
        for key, value in rows.items():
            writer.writerow({"key": key, "value": value, "description": key})

    with (config_dir / "targets.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=TARGET_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
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
        )

    config, _ = load_config(config_dir)

    assert config.injection_order_source is None
    assert config.rolling_window_size == 5
    assert config.dirty_matrix_mode is False


def test_disabled_optional_resources_are_not_reported_as_missing_settings():
    from xic_extractor.configuration.settings import resolve_settings
    from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS

    assert resolve_settings(dict(CANONICAL_SETTINGS_DEFAULTS))[2] == []
